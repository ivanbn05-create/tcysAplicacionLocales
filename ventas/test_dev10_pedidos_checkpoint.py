import hashlib
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from personas.models import Sucursal
from ventas.integracion_sucursales import (
    ORIGEN_API_V2,
    ORIGEN_SQLITE,
    ORIGEN_SUPABASE,
    PedidoRemotoInvalido,
    PedidoRemotoRequiereConciliacion,
    _adaptar_pedido_api_v2,
    _importar_pedido,
    _sincronizar_pedidos_api_v2,
)
from ventas.pedidos_api_v2 import ErrorContratoPedidos, _decimal
from ventas.models import (
    EstadoSincronizacionPedidos,
    Mesa,
    PedidoSucursalImportado,
    SucursalPedido,
    Ticket,
)


@override_settings(
    PEDIDOS_API_BASE_URL="https://pedidos.example.test",
    PEDIDOS_API_ENDPOINT="/api/v2/pos/pedidos/",
    PEDIDOS_API_TOKEN="token-solo-pruebas",
    PEDIDOS_API_SUCURSAL_IDS=(77,),
    PEDIDOS_API_CA_BUNDLE="",
    PEDIDOS_API_CONNECT_TIMEOUT_SECONDS=1,
    PEDIDOS_API_READ_TIMEOUT_SECONDS=1,
    PEDIDOS_API_MAX_RESPONSE_BYTES=65536,
    PEDIDOS_API_MAX_RETRIES=0,
    PEDIDOS_API_PAGE_SIZE=50,
)
class CheckpointVentanasPedidosV2Tests(TestCase):
    def setUp(self):
        self.sucursal = Sucursal.objects.create(
            clave="DEV10-CHK",
            nombre="Checkpoint Pedidos dev.10",
        )
        self.origen = SucursalPedido.objects.create(
            sucursal=self.sucursal,
            origen_id=77,
            nombre="Identidad explicita",
            tipo=SucursalPedido.Tipo.SUCURSAL,
            identidad_confirmada_en=timezone.now(),
        )
        self.mesa = Mesa.objects.create(
            sucursal=self.sucursal,
            canal=Mesa.Canal.SUCURSALES,
            clave="SUC-77-CHK",
            nombre="Posicion checkpoint",
            cliente_sucursal=self.origen,
        )

    def _reloj(self):
        return timezone.make_aware(
            datetime(2026, 9, 24, 10, 30),
            timezone.get_current_timezone(),
        )

    def _cliente_vacio(self, cliente_cls, llamadas):
        def sincronizar_ventana(*, desde, hasta, aplicar_pagina, limite, cursor_inicial):
            llamadas.append(
                SimpleNamespace(
                    desde=desde,
                    hasta=hasta,
                    cursor=cursor_inicial,
                    limite=limite,
                )
            )
            aplicar_pagina(
                SimpleNamespace(pedidos=(), request_id=f"req-{len(llamadas)}"),
                SimpleNamespace(
                    cursor_entrada=cursor_inicial,
                    cursor_siguiente=None,
                    ventana_completa=True,
                ),
            )
            return SimpleNamespace(paginas=1, pedidos=0, ventana_completa=True)

        cliente_cls.return_value.sincronizar_ventana.side_effect = sincronizar_ventana

    @patch("ventas.integracion_sucursales.ClientePedidosV2")
    def test_varios_dias_offline_avanzan_ventanas_consecutivas(self, cliente_cls):
        horizonte = self._reloj()
        agua_inicial = horizonte - timedelta(days=3, hours=6)
        estado = EstadoSincronizacionPedidos.objects.create(
            sucursal=self.sucursal,
            sucursales_origen=[77],
            agua_alta_hasta=agua_inicial,
        )
        llamadas = []
        self._cliente_vacio(cliente_cls, llamadas)

        with patch("ventas.integracion_sucursales.timezone.now", return_value=horizonte):
            resultado = _sincronizar_pedidos_api_v2(self.sucursal)

        estado.refresh_from_db()
        self.assertTrue(resultado["activa"])
        self.assertFalse(resultado["pendiente"])
        self.assertEqual(resultado["ventanas"], 4)
        self.assertEqual(len(llamadas), 4)
        self.assertEqual(llamadas[0].desde, agua_inicial)
        self.assertEqual(llamadas[-1].hasta, horizonte)
        for anterior, siguiente in zip(llamadas, llamadas[1:]):
            self.assertEqual(anterior.hasta, siguiente.desde)
            self.assertIsNone(siguiente.cursor)
        self.assertEqual(estado.agua_alta_hasta, horizonte)
        self.assertEqual(estado.cursor, "")

    @patch("ventas.integracion_sucursales.ClientePedidosV2")
    def test_reanuda_cursor_de_pagina_y_luego_continua_siguiente_ventana(self, cliente_cls):
        horizonte = self._reloj()
        agua_inicial = horizonte - timedelta(days=2)
        EstadoSincronizacionPedidos.objects.create(
            sucursal=self.sucursal,
            sucursales_origen=[77],
            agua_alta_hasta=agua_inicial,
            ventana_desde=agua_inicial,
            ventana_hasta=agua_inicial + timedelta(days=1),
            cursor="cursor-pagina-2",
            ultimo_cursor_confirmado="cursor-pagina-2",
        )
        llamadas = []
        self._cliente_vacio(cliente_cls, llamadas)

        with patch("ventas.integracion_sucursales.timezone.now", return_value=horizonte):
            resultado = _sincronizar_pedidos_api_v2(self.sucursal)

        estado = EstadoSincronizacionPedidos.objects.get(sucursal=self.sucursal)
        self.assertEqual(resultado["ventanas"], 2)
        self.assertEqual(llamadas[0].cursor, "cursor-pagina-2")
        self.assertEqual(llamadas[0].desde, agua_inicial)
        self.assertIsNone(llamadas[1].cursor)
        self.assertEqual(llamadas[1].desde, llamadas[0].hasta)
        self.assertEqual(estado.agua_alta_hasta, horizonte)
        self.assertEqual(estado.cursor, "")

    @patch("ventas.integracion_sucursales.ClientePedidosV2")
    def test_discontinuidad_temporal_entra_en_conciliacion_sin_llamar_api(self, cliente_cls):
        horizonte = self._reloj()
        agua_inicial = horizonte - timedelta(days=2)
        estado = EstadoSincronizacionPedidos.objects.create(
            sucursal=self.sucursal,
            sucursales_origen=[77],
            agua_alta_hasta=agua_inicial,
            ventana_desde=agua_inicial + timedelta(hours=1),
            ventana_hasta=agua_inicial + timedelta(days=1),
            cursor="cursor-discontinuo",
        )

        with patch("ventas.integracion_sucursales.timezone.now", return_value=horizonte):
            resultado = _sincronizar_pedidos_api_v2(self.sucursal)

        estado.refresh_from_db()
        self.assertFalse(resultado["activa"])
        self.assertTrue(resultado["requiere_conciliacion"])
        self.assertEqual(
            estado.estado,
            EstadoSincronizacionPedidos.Estado.RECONCILIACION,
        )
        self.assertEqual(estado.agua_alta_hasta, agua_inicial)
        self.assertEqual(estado.cursor, "cursor-discontinuo")
        cliente_cls.return_value.sincronizar_ventana.assert_not_called()

    @patch("ventas.integracion_sucursales.ClientePedidosV2")
    def test_id_reutilizado_en_pagina_entra_en_conciliacion_sin_avanzar(self, cliente_cls):
        horizonte = self._reloj()
        agua_inicial = horizonte - timedelta(hours=2)
        codigo_existente = str(uuid.uuid4())
        self._crear_importado(501, codigo_existente)
        estado = EstadoSincronizacionPedidos.objects.create(
            sucursal=self.sucursal,
            sucursales_origen=[77],
            agua_alta_hasta=agua_inicial,
        )
        pedido_adaptado = {
            "id": 501,
            "codigo_publico": str(uuid.uuid4()),
            "estado": "confirmado",
        }

        def sincronizar_ventana(*, aplicar_pagina, cursor_inicial, **kwargs):
            remoto = SimpleNamespace(
                sucursal=SimpleNamespace(id=77),
                order_canonical_json="{}",
                order_sha256=hashlib.sha256(b"{}").hexdigest(),
            )
            aplicar_pagina(
                SimpleNamespace(pedidos=(remoto,), request_id="req-conflicto"),
                SimpleNamespace(
                    cursor_entrada=cursor_inicial,
                    cursor_siguiente=None,
                    ventana_completa=True,
                ),
            )
            return SimpleNamespace(paginas=1)

        cliente_cls.return_value.sincronizar_ventana.side_effect = sincronizar_ventana
        with (
            patch("ventas.integracion_sucursales.timezone.now", return_value=horizonte),
            patch(
                "ventas.integracion_sucursales._adaptar_pedido_api_v2",
                return_value=(pedido_adaptado, []),
            ),
        ):
            resultado = _sincronizar_pedidos_api_v2(self.sucursal)

        estado.refresh_from_db()
        self.assertFalse(resultado["activa"])
        self.assertTrue(resultado["requiere_conciliacion"])
        self.assertEqual(
            estado.estado,
            EstadoSincronizacionPedidos.Estado.RECONCILIACION,
        )
        self.assertEqual(estado.agua_alta_hasta, agua_inicial)
        self.assertEqual(estado.cursor, "")

    def _crear_ticket(self, folio):
        return Ticket.objects.create(
            sucursal=self.sucursal,
            mesa=self.mesa,
            folio=folio,
            canal=Mesa.Canal.SUCURSALES,
            estado=Ticket.Estado.PAGADO,
        )

    def _crear_importado(self, origen_id, codigo_publico, *, origen=ORIGEN_API_V2):
        return PedidoSucursalImportado.objects.create(
            sucursal=self.sucursal,
            ticket=self._crear_ticket(
                Ticket.objects.filter(sucursal=self.sucursal).count() + 1
            ),
            origen=origen,
            origen_id=origen_id,
            codigo_publico=codigo_publico,
            estado_origen="confirmado",
        )

    def test_uuid_repetido_con_id_nuevo_es_idempotente(self):
        codigo = str(uuid.uuid4())
        existente = self._crear_importado(601, codigo)

        resultado = _importar_pedido(
            self.sucursal,
            {"id": 999, "codigo_publico": codigo, "estado": "confirmado"},
            [],
            ORIGEN_API_V2,
            identidad_estricta=True,
        )

        self.assertIsNone(resultado)
        self.assertEqual(
            PedidoSucursalImportado.objects.filter(
                sucursal=self.sucursal,
                codigo_publico=codigo,
            ).count(),
            1,
        )
        self.assertTrue(PedidoSucursalImportado.objects.filter(pk=existente.pk).exists())

    def test_id_reutilizado_con_uuid_distinto_exige_conciliacion(self):
        self._crear_importado(701, str(uuid.uuid4()))

        with self.assertRaises(PedidoRemotoRequiereConciliacion):
            _importar_pedido(
                self.sucursal,
                {
                    "id": 701,
                    "codigo_publico": str(uuid.uuid4()),
                    "estado": "confirmado",
                },
                [],
                ORIGEN_API_V2,
                identidad_estricta=True,
            )

    def test_constraint_uuid_es_parcial_y_compatible_con_legacy(self):
        codigo = str(uuid.uuid4())
        self._crear_importado(801, codigo)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._crear_importado(802, codigo)

        codigo_legacy = "codigo-publico-legacy"
        self._crear_importado(901, codigo_legacy, origen=ORIGEN_SUPABASE)
        self._crear_importado(902, codigo_legacy, origen=ORIGEN_SQLITE)
        self.assertEqual(
            PedidoSucursalImportado.objects.filter(codigo_publico=codigo_legacy).count(),
            2,
        )
    def test_identidad_v2_compuesta_permite_mismo_codigo_en_dos_remitentes(self):
        codigo = str(uuid.uuid4())
        legacy = self._crear_importado(811, codigo)
        self.assertIsNone(legacy.sender_id)
        PedidoSucursalImportado.objects.create(
            sucursal=self.sucursal,
            ticket=self._crear_ticket(2),
            origen=ORIGEN_API_V2,
            origen_id=812,
            codigo_publico=codigo,
            sender_id=77,
            estado_origen="confirmado",
        )
        PedidoSucursalImportado.objects.create(
            sucursal=self.sucursal,
            ticket=self._crear_ticket(3),
            origen=ORIGEN_API_V2,
            origen_id=813,
            codigo_publico=codigo,
            sender_id=78,
            estado_origen="confirmado",
        )
        self.assertEqual(
            PedidoSucursalImportado.objects.filter(codigo_publico=codigo).count(), 3
        )

    def test_identidad_v2_misma_pareja_con_hash_distinto_exige_conciliacion(self):
        codigo = str(uuid.uuid4())
        existente = self._crear_importado(821, codigo)
        existente.sender_id = 77
        existente.order_canonical_json = "{}"
        existente.order_sha256 = hashlib.sha256(b"{}").hexdigest()
        existente.save(update_fields=["sender_id", "order_canonical_json", "order_sha256"])
        cuerpo = '{"id":821}'
        with self.assertRaises(PedidoRemotoRequiereConciliacion):
            _importar_pedido(
                self.sucursal,
                {"id": 821, "codigo_publico": codigo, "estado": "confirmado"},
                [],
                ORIGEN_API_V2,
                identidad_estricta=True,
                sender_id=77,
                order_canonical_json=cuerpo,
                order_sha256=hashlib.sha256(cuerpo.encode("utf-8")).hexdigest(),
            )

    def test_parser_rechaza_decimal_enorme_y_escala_excesiva(self):
        with self.assertRaises(ErrorContratoPedidos):
            _decimal(
                "9" * 64,
                "data[0].total",
                max_enteros=18,
                max_decimales=2,
            )
        with self.assertRaises(ErrorContratoPedidos):
            _decimal(
                "1.2345",
                "data[0].items[0].cantidad",
                max_enteros=3,
                max_decimales=3,
                positivo=True,
            )

    @patch("ventas.integracion_sucursales.ClientePedidosV2")
    def test_decimal_enorme_de_api_termina_en_estado_contractual(self, cliente_cls):
        try:
            _decimal(
                "9" * 64,
                "data[0].total",
                max_enteros=18,
                max_decimales=2,
            )
        except ErrorContratoPedidos as error_decimal:
            cliente_cls.return_value.sincronizar_ventana.side_effect = error_decimal
        else:  # pragma: no cover - protege la intencion del fixture
            self.fail("El decimal enorme debio ser rechazado por el parser.")

        resultado = _sincronizar_pedidos_api_v2(self.sucursal)

        estado = EstadoSincronizacionPedidos.objects.get(sucursal=self.sucursal)
        self.assertFalse(resultado["activa"])
        self.assertFalse(resultado["requiere_conciliacion"])
        self.assertEqual(
            estado.estado,
            EstadoSincronizacionPedidos.Estado.CONTRATO_RECHAZADO,
        )
        self.assertIsNone(estado.agua_alta_hasta)
        self.assertEqual(estado.cursor, "")
    def test_adaptador_convierte_invalid_operation_en_rechazo_controlado(self):
        producto = SimpleNamespace(
            id=1,
            nombre="Producto",
            nombre_ticket="P",
            unidad_abreviatura="pz",
            cantidad_por_precio=Decimal("1"),
        )
        item = SimpleNamespace(
            id=1,
            pedido_id=1,
            producto=producto,
            cantidad=Decimal("9" * 64),
            precio_unitario=Decimal("9" * 64),
            subtotal=Decimal("0.00"),
        )
        pedido = SimpleNamespace(
            id=1,
            codigo_publico=uuid.uuid4(),
            fecha_confirmacion=timezone.now(),
            total=Decimal("0.00"),
            sucursal=SimpleNamespace(id=77, nombre="Sucursal"),
            items=(item,),
        )

        with self.assertRaises(PedidoRemotoInvalido):
            _adaptar_pedido_api_v2(pedido)
