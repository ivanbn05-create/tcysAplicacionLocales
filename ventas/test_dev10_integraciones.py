import copy
import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.hashers import is_password_usable, make_password
from django.test import TestCase, override_settings
from django.utils import timezone

from catalogo.models import (
    Categoria,
    IdentidadCategoriaCentral,
    IdentidadProductoCentral,
    Precio,
    Producto,
    PublicacionCatalogoCentral,
)
from personas.models import Sucursal, UsuarioPOS
from ventas.admin_services import asegurar_actor_administrador
from ventas.catalogo_central import (
    ErrorCatalogoCentral,
    aplicar_publicacion_catalogo,
)
from ventas.clientes import guardar_cliente
from ventas.integracion_sucursales import (
    ORIGEN_API_V2,
    _sincronizar_pedidos_api_v2,
)
from ventas.models import (
    ConfiguracionSucursal,
    EstadoSincronizacionPedidos,
    EventoOutbox,
    Mesa,
    Partida,
    PedidoSucursalImportado,
    SucursalPedido,
    Ticket,
)
from ventas.pedidos_api_v2 import ErrorBrechaRetencion
from ventas.services import (
    actualizar_partida,
    agregar_partida,
    ajustar_grupo_partidas,
    registrar_evento,
)
from ventas.sincronizacion_central import hash_payload


class ActorAdministradorProtegidoTests(TestCase):
    def setUp(self):
        self.sucursal = Sucursal.objects.create(
            clave="DEV10-ACTOR",
            nombre="Pruebas actor dev.10",
        )
        ConfiguracionSucursal.objects.create(
            sucursal=self.sucursal,
            clave_administrador=make_password("3141"),
        )

    def test_creacion_reparacion_e_idempotencia(self):
        actor_inicial = asegurar_actor_administrador(self.sucursal)
        actor_repetido = asegurar_actor_administrador(self.sucursal)

        self.assertEqual(actor_repetido.pk, actor_inicial.pk)
        self.assertTrue(actor_inicial.es_sistema)
        self.assertTrue(actor_inicial.activo)
        self.assertEqual(actor_inicial.nombre, "Administrador")
        self.assertFalse(is_password_usable(actor_inicial.clave))
        self.assertEqual(
            UsuarioPOS.objects.filter(sucursal=self.sucursal, es_sistema=True).count(),
            1,
        )
        configuracion = ConfiguracionSucursal.objects.get(sucursal=self.sucursal)
        self.assertEqual(configuracion.actor_administrador_id, actor_inicial.pk)

        UsuarioPOS.objects.filter(pk=actor_inicial.pk).update(
            activo=False,
            nombre="Nombre alterado",
        )
        actor_reparado = asegurar_actor_administrador(self.sucursal)
        actor_reparado.refresh_from_db()

        self.assertEqual(actor_reparado.pk, actor_inicial.pk)
        self.assertTrue(actor_reparado.activo)
        self.assertEqual(actor_reparado.nombre, "Administrador")
        self.assertEqual(
            UsuarioPOS.objects.filter(sucursal=self.sucursal, es_sistema=True).count(),
            1,
        )


class OutboxCentralDurableTests(TestCase):
    def setUp(self):
        self.sucursal = Sucursal.objects.create(
            clave="DEV10-OUTBOX",
            nombre="Pruebas outbox dev.10",
        )

    def test_cliente_genera_snapshots_completos_versionados_e_inmutables(self):
        cliente = guardar_cliente(
            self.sucursal,
            {
                "nombre": "Cliente inicial",
                "notas": "Entregar por el portón azul",
                "comentarios_multiples": True,
                "telefonos": [
                    {"numero": "777 100 2000", "etiqueta": "Principal"},
                    {"numero": "777 100 3000", "etiqueta": "Alterno"},
                ],
                "domicilios": [
                    {
                        "etiqueta": "Casa",
                        "calle": "Bugambilia",
                        "numero_exterior": "10",
                        "colonia": "Centro",
                        "municipio": "Cuernavaca",
                        "referencia": "Portón azul",
                    },
                    {
                        "etiqueta": "Trabajo",
                        "calle": "Jacarandas",
                        "numero_exterior": "20",
                        "colonia": "Norte",
                        "municipio": "Cuernavaca",
                        "referencia": "Recepción",
                    },
                ],
            },
        )
        primer_evento = EventoOutbox.objects.get(
            agregado_id=cliente.id,
            destino=EventoOutbox.Destino.CENTRAL_CLIENTES,
            version_origen=1,
        )
        telefonos = list(cliente.telefonos.order_by("creado_en", "id"))
        domicilios = list(cliente.domicilios.order_by("creado_en", "id"))
        ids_telefonos = {str(item.id) for item in telefonos}
        ids_domicilios = {str(item.id) for item in domicilios}

        cliente = guardar_cliente(
            self.sucursal,
            {
                "nombre": "Cliente actualizado",
                "notas": "Sin timbre",
                "comentarios_multiples": False,
                "telefonos": [
                    {
                        "id": str(telefonos[0].id),
                        "numero": telefonos[0].numero,
                        "etiqueta": "Único activo",
                    }
                ],
                "domicilios": [
                    {
                        "id": str(domicilios[0].id),
                        "etiqueta": "Casa",
                        "calle": "Bugambilia",
                        "numero_exterior": "10",
                        "colonia": "Centro",
                        "municipio": "Cuernavaca",
                        "referencia": "Sin timbre",
                    }
                ],
            },
            cliente=cliente,
        )

        eventos = list(
            EventoOutbox.objects.filter(
                agregado_id=cliente.id,
                destino=EventoOutbox.Destino.CENTRAL_CLIENTES,
            ).order_by("version_origen")
        )
        self.assertEqual([evento.version_origen for evento in eventos], [1, 2])
        segundo_evento = eventos[1]
        self.assertEqual(
            segundo_evento.estado_entrega,
            EventoOutbox.EstadoEntrega.PENDIENTE,
        )
        self.assertEqual(segundo_evento.datos["version_origen"], 2)
        self.assertEqual(
            segundo_evento.datos["cliente"]["nombre"],
            "Cliente actualizado",
        )
        self.assertEqual(
            {item["id"] for item in segundo_evento.datos["cliente"]["telefonos"]},
            ids_telefonos,
        )
        self.assertEqual(
            {item["id"] for item in segundo_evento.datos["cliente"]["domicilios"]},
            ids_domicilios,
        )
        self.assertEqual(
            sorted(
                item["activo"]
                for item in segundo_evento.datos["cliente"]["telefonos"]
            ),
            [False, True],
        )
        self.assertEqual(
            sorted(
                item["activo"]
                for item in segundo_evento.datos["cliente"]["domicilios"]
            ),
            [False, True],
        )
        self.assertEqual(
            segundo_evento.payload_hash,
            hash_payload(segundo_evento.datos),
        )

        primer_evento.refresh_from_db()
        self.assertEqual(
            primer_evento.datos["cliente"]["nombre"],
            "Cliente inicial",
        )
        self.assertTrue(
            all(
                item["activo"]
                for item in primer_evento.datos["cliente"]["telefonos"]
            )
        )
        self.assertTrue(
            all(
                item["activo"]
                for item in primer_evento.datos["cliente"]["domicilios"]
            )
        )

    def test_venta_cerrada_conserva_precio_y_nombre_historicos(self):
        categoria = Categoria.objects.create(
            sucursal=self.sucursal,
            nombre="Tacos",
            orden=1,
        )
        producto = Producto.objects.create(
            sucursal=self.sucursal,
            categoria=categoria,
            codigo="HIST",
            nombre="Taco histórico",
            nombre_corto="TH",
            destino_impresion=Producto.Destino.COCINA,
        )
        precio_catalogo = Precio.objects.create(
            sucursal=self.sucursal,
            producto=producto,
            importe=Decimal("42.00"),
        )
        mesa = Mesa.objects.create(
            sucursal=self.sucursal,
            canal=Mesa.Canal.LLEVAR,
            clave="LLEVAR-HIST",
            nombre="Llevar histórico",
        )
        ticket = Ticket.objects.create(
            sucursal=self.sucursal,
            mesa=mesa,
            folio=1,
            canal=Mesa.Canal.LLEVAR,
            estado=Ticket.Estado.PAGADO,
            forma_pago=Ticket.FormaPago.EFECTIVO,
            pagado_en=timezone.now(),
        )
        Partida.objects.create(
            sucursal=self.sucursal,
            ticket=ticket,
            producto=producto,
            comensal=1,
            cantidad=Decimal("2.000"),
            precio_unitario=Decimal("42.00"),
            cantidad_por_precio=Decimal("1.000"),
            unidad="PZA",
            nombre_producto="Taco histórico cobrado",
            nombre_corto="TH",
            procesada=True,
        )

        registrar_evento(ticket, "ticket.pagado", {"origen": "prueba"})
        evento = EventoOutbox.objects.get(
            agregado_id=ticket.id,
            tipo="ticket.pagado",
        )
        payload_congelado = copy.deepcopy(evento.datos)

        precio_catalogo.importe = Decimal("99.00")
        precio_catalogo.save(update_fields=["importe"])
        producto.nombre = "Producto renombrado"
        producto.save(update_fields=["nombre", "actualizado_en"])

        ticket.refresh_from_db()
        evento.refresh_from_db()
        partida_payload = evento.datos["eventos"][0]["venta"]["partidas"][0]
        self.assertEqual(evento.destino, EventoOutbox.Destino.CENTRAL_VENTAS)
        self.assertEqual(
            evento.estado_entrega,
            EventoOutbox.EstadoEntrega.PENDIENTE,
        )
        self.assertEqual(
            partida_payload["nombre_cobrado"],
            "Taco histórico cobrado",
        )
        self.assertEqual(partida_payload["precio_unitario"], "42.00")
        self.assertEqual(partida_payload["importe"], "84.00")
        self.assertEqual(evento.datos, payload_congelado)
        self.assertEqual(evento.payload_hash, hash_payload(evento.datos))
        self.assertEqual(ticket.subtotal, Decimal("84.00"))
        self.assertEqual(ticket.total, Decimal("84.00"))


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
class EstadoPedidosV2Tests(TestCase):
    def setUp(self):
        self.sucursal = Sucursal.objects.create(
            clave="DEV10-PEDIDOS",
            nombre="Pruebas Pedidos dev.10",
        )
        self.origen = SucursalPedido.objects.create(
            sucursal=self.sucursal,
            origen_id=77,
            nombre="Identidad explícita",
            tipo=SucursalPedido.Tipo.SUCURSAL,
            identidad_confirmada_en=timezone.now(),
        )
        self.mesa = Mesa.objects.create(
            sucursal=self.sucursal,
            canal=Mesa.Canal.SUCURSALES,
            clave="SUC-77",
            nombre="Posición sucursal",
            cliente_sucursal=self.origen,
        )

    @patch("ventas.integracion_sucursales.ClientePedidosV2")
    def test_retention_gap_conserva_checkpoint_y_pedidos_importados(
        self,
        cliente_cls,
    ):
        desde = timezone.now() - timedelta(days=1)
        hasta = timezone.now() + timedelta(days=1)
        estado = EstadoSincronizacionPedidos.objects.create(
            sucursal=self.sucursal,
            estado=EstadoSincronizacionPedidos.Estado.LISTO,
            ventana_desde=desde,
            ventana_hasta=hasta,
            sucursales_origen=[77],
            cursor="cursor-opaco-confirmado",
            ultimo_cursor_confirmado="cursor-opaco-confirmado",
            ultimo_request_id="req-previo",
        )
        ticket = Ticket.objects.create(
            sucursal=self.sucursal,
            mesa=self.mesa,
            folio=1,
            canal=Mesa.Canal.SUCURSALES,
            estado=Ticket.Estado.PROCESADO,
        )
        importado = PedidoSucursalImportado.objects.create(
            sucursal=self.sucursal,
            ticket=ticket,
            origen=ORIGEN_API_V2,
            origen_id=4001,
            codigo_publico=str(uuid.uuid4()),
            estado_origen="confirmado",
        )
        cliente_cls.return_value.sincronizar_ventana.side_effect = (
            ErrorBrechaRetencion(
                "gap",
                status=410,
                code="retention_gap",
                request_id="req-gap-410",
            )
        )

        resultado = _sincronizar_pedidos_api_v2(self.sucursal)

        estado.refresh_from_db()
        self.assertFalse(resultado["activa"])
        self.assertTrue(resultado["requiere_conciliacion"])
        self.assertEqual(
            estado.estado,
            EstadoSincronizacionPedidos.Estado.RECONCILIACION,
        )
        self.assertEqual(estado.cursor, "cursor-opaco-confirmado")
        self.assertEqual(
            estado.ultimo_cursor_confirmado,
            "cursor-opaco-confirmado",
        )
        self.assertEqual(estado.ultimo_codigo_http, 410)
        self.assertEqual(estado.ultimo_request_id, "req-gap-410")
        self.assertIsNotNone(estado.conciliacion_requerida_en)
        self.assertTrue(
            PedidoSucursalImportado.objects.filter(pk=importado.pk).exists()
        )
        self.assertTrue(Ticket.objects.filter(pk=ticket.pk).exists())
        cliente_cls.return_value.sincronizar_ventana.assert_called_once()
        self.assertEqual(
            cliente_cls.return_value.sincronizar_ventana.call_args.kwargs[
                "cursor_inicial"
            ],
            "cursor-opaco-confirmado",
        )

    @patch("ventas.integracion_sucursales.ClientePedidosV2")
    def test_nombre_no_sustituye_confirmacion_explicita_de_identidad(
        self,
        cliente_cls,
    ):
        self.origen.identidad_confirmada_en = None
        self.origen.nombre = "Mismo nombre remoto"
        self.origen.save(
            update_fields=["identidad_confirmada_en", "nombre"]
        )

        resultado = _sincronizar_pedidos_api_v2(self.sucursal)

        estado = EstadoSincronizacionPedidos.objects.get(sucursal=self.sucursal)
        self.assertFalse(resultado["activa"])
        self.assertTrue(resultado["requiere_identidad"])
        self.assertEqual(
            estado.estado,
            EstadoSincronizacionPedidos.Estado.PAUSADO,
        )
        cliente_cls.assert_not_called()


class CatalogoCentralAtomicoTests(TestCase):
    def setUp(self):
        self.sucursal = Sucursal.objects.create(
            clave="DEV10-CATALOGO",
            nombre="Pruebas catálogo dev.10",
        )
        ConfiguracionSucursal.objects.create(
            sucursal=self.sucursal,
            clave_administrador=make_password("3141"),
        )
        self.categoria_central_id = uuid.uuid4()
        self.producto_central_id = uuid.uuid4()

    def snapshot(
        self,
        *,
        version=1,
        release_id=None,
        publicacion_id=None,
        publicacion_anterior_id=None,
        nombre_categoria="Comida",
        codigo="CAT1",
        importe="25.00",
        vigente_desde="2026-09-24",
        aplicar_desde="2026-09-24",
    ):
        contenido = {
            "categorias": [
                {
                    "categoria_central_id": str(self.categoria_central_id),
                    "codigo": "CAT-COMIDA",
                    "nombre": nombre_categoria,
                    "orden": 1,
                    "activa": True,
                }
            ],
            "productos": [
                {
                    "producto_central_id": str(self.producto_central_id),
                    "codigo": codigo,
                    "nombre": "Producto de catálogo",
                    "nombre_corto": "PC",
                    "categoria_central_id": str(
                        self.categoria_central_id
                    ),
                    "orden": 1,
                    "activo": True,
                    "permite_termino": False,
                    "termino_predeterminado": "",
                    "abreviaturas_termino": {},
                    "destino_impresion": "cocina",
                    "imagen": {
                        "politica": "conservar_local_o_placeholder",
                        "asset": None,
                    },
                    "precio": {
                        "importe": importe,
                        "origen": "global",
                        "vigente_desde": vigente_desde,
                        "vigente_hasta": None,
                    },
                }
            ],
        }
        return {
            "version_contrato": 2,
            "tipo": "snapshot_completo",
            "release_id": str(release_id or uuid.uuid4()),
            "publicacion_id": str(publicacion_id or uuid.uuid4()),
            "publicacion_anterior_id": (
                str(publicacion_anterior_id)
                if publicacion_anterior_id
                else None
            ),
            "version_sucursal": version,
            "sucursal": {
                "id": str(self.sucursal.id),
                "clave": self.sucursal.clave,
            },
            "publicada_en": "2026-09-24T12:00:00-06:00",
            "aplicar_desde": aplicar_desde,
            "moneda": "MXN",
            "conteos": {"categorias": 1, "productos": 1},
            "contenido": contenido,
            "contenido_sha256": hash_payload(contenido),
        }

    def test_aplicacion_idempotente_crea_un_solo_ack_durable(self):
        datos = self.snapshot()

        publicacion, creada = aplicar_publicacion_catalogo(
            self.sucursal,
            datos,
        )
        repetida, creada_repetida = aplicar_publicacion_catalogo(
            self.sucursal,
            datos,
        )

        self.assertTrue(creada)
        self.assertFalse(creada_repetida)
        self.assertEqual(repetida.pk, publicacion.pk)
        self.assertEqual(PublicacionCatalogoCentral.objects.count(), 1)
        self.assertEqual(IdentidadCategoriaCentral.objects.count(), 1)
        self.assertEqual(IdentidadProductoCentral.objects.count(), 1)
        self.assertEqual(Precio.objects.get().importe, Decimal("25.00"))
        acks = EventoOutbox.objects.filter(
            agregado_id=publicacion.publicacion_id,
            destino=EventoOutbox.Destino.CENTRAL_CATALOGO_ACK,
        )
        self.assertEqual(acks.count(), 1)
        ack = acks.get()
        self.assertEqual(
            ack.estado_entrega,
            EventoOutbox.EstadoEntrega.PENDIENTE,
        )
        self.assertEqual(ack.datos["estado"], "aplicado")
        self.assertEqual(
            ack.datos["contenido_sha256"],
            datos["contenido_sha256"],
        )
        self.assertEqual(ack.payload_hash, hash_payload(ack.datos))

    def test_checksum_invalido_no_altera_ultima_version_valida(self):
        datos = self.snapshot()
        publicacion, _ = aplicar_publicacion_catalogo(
            self.sucursal,
            datos,
        )
        categoria = IdentidadCategoriaCentral.objects.get().categoria
        producto = IdentidadProductoCentral.objects.get().producto
        precio = Precio.objects.get(producto=producto)
        siguiente = self.snapshot(
            version=2,
            publicacion_anterior_id=publicacion.publicacion_id,
            nombre_categoria="Categoría que no debe aplicarse",
            importe="88.00",
        )
        siguiente["contenido_sha256"] = "0" * 64

        with self.assertRaises(ErrorCatalogoCentral) as captura:
            aplicar_publicacion_catalogo(self.sucursal, siguiente)

        self.assertEqual(captura.exception.codigo, "checksum_invalido")
        categoria.refresh_from_db()
        producto.refresh_from_db()
        precio.refresh_from_db()
        self.assertEqual(categoria.nombre, "Comida")
        self.assertEqual(precio.importe, Decimal("25.00"))
        self.assertEqual(PublicacionCatalogoCentral.objects.count(), 1)
        self.assertTrue(
            PublicacionCatalogoCentral.objects.filter(
                pk=publicacion.pk
            ).exists()
        )

    def test_conflicto_tardio_revierte_toda_la_publicacion(self):
        datos = self.snapshot()
        publicacion, _ = aplicar_publicacion_catalogo(
            self.sucursal,
            datos,
        )
        categoria = IdentidadCategoriaCentral.objects.get().categoria
        producto = IdentidadProductoCentral.objects.get().producto
        categoria_ajena = Categoria.objects.create(
            sucursal=self.sucursal,
            nombre="Local no gestionada",
            orden=99,
        )
        Producto.objects.create(
            sucursal=self.sucursal,
            categoria=categoria_ajena,
            codigo="BLOQ",
            nombre="Producto local",
            nombre_corto="PL",
            orden=99,
        )
        siguiente = self.snapshot(
            version=2,
            publicacion_anterior_id=publicacion.publicacion_id,
            nombre_categoria="Cambio previo al conflicto",
            codigo="BLOQ",
            importe="77.00",
        )

        with self.assertRaises(ErrorCatalogoCentral) as captura:
            aplicar_publicacion_catalogo(self.sucursal, siguiente)

        self.assertEqual(captura.exception.codigo, "conflicto_local")
        categoria.refresh_from_db()
        producto.refresh_from_db()
        self.assertEqual(categoria.nombre, "Comida")
        self.assertEqual(producto.codigo, "CAT1")
        self.assertEqual(
            Precio.objects.get(producto=producto).importe,
            Decimal("25.00"),
        )
        self.assertEqual(PublicacionCatalogoCentral.objects.count(), 1)
        self.assertTrue(
            PublicacionCatalogoCentral.objects.filter(
                pk=publicacion.pk
            ).exists()
        )
        self.assertEqual(
            EventoOutbox.objects.filter(
                destino=EventoOutbox.Destino.CENTRAL_CATALOGO_ACK
            ).count(),
            1,
        )
    def test_catalogo_nuevo_no_recalcula_linea_abierta_y_nueva_orden_usa_precio_vigente(self):
        categoria = Categoria.objects.create(
            sucursal=self.sucursal,
            nombre="Comida local mapeada",
            orden=1,
        )
        producto = Producto.objects.create(
            sucursal=self.sucursal,
            categoria=categoria,
            codigo="CAT1",
            nombre="Producto previo",
            nombre_corto="PP",
            orden=1,
            destino_impresion=Producto.Destino.COCINA,
        )
        Precio.objects.create(
            sucursal=self.sucursal,
            producto=producto,
            importe=Decimal("25.00"),
            vigente_desde=timezone.localdate(),
        )
        IdentidadCategoriaCentral.objects.create(
            sucursal=self.sucursal,
            central_id=self.categoria_central_id,
            categoria=categoria,
        )
        IdentidadProductoCentral.objects.create(
            sucursal=self.sucursal,
            central_id=self.producto_central_id,
            producto=producto,
        )
        inicial = self.snapshot(importe="25.00")
        publicacion, _ = aplicar_publicacion_catalogo(self.sucursal, inicial)

        mesa_abierta = Mesa.objects.create(
            sucursal=self.sucursal,
            canal=Mesa.Canal.COMEDOR,
            clave="PRECIO-ABIERTO",
            nombre="Precio abierto",
        )
        ticket_abierto = Ticket.objects.create(
            sucursal=self.sucursal,
            mesa=mesa_abierta,
            folio=1,
            canal=Mesa.Canal.COMEDOR,
            estado=Ticket.Estado.ABIERTO,
        )
        partida_abierta = agregar_partida(ticket_abierto, producto)
        self.assertEqual(partida_abierta.precio_unitario, Decimal("25.00"))
        self.assertEqual(
            partida_abierta.precio_lista_capturado,
            Decimal("25.00"),
        )

        siguiente = self.snapshot(
            version=2,
            publicacion_anterior_id=publicacion.publicacion_id,
            importe="40.00",
        )
        aplicar_publicacion_catalogo(self.sucursal, siguiente)
        partida_abierta = actualizar_partida(partida_abierta, Decimal("2"))
        partida_abierta.refresh_from_db()
        self.assertEqual(partida_abierta.precio_unitario, Decimal("25.00"))
        self.assertEqual(
            partida_abierta.precio_lista_capturado,
            Decimal("25.00"),
        )
        self.assertEqual(partida_abierta.importe, Decimal("50.00"))

        mesa_nueva = Mesa.objects.create(
            sucursal=self.sucursal,
            canal=Mesa.Canal.COMEDOR,
            clave="PRECIO-NUEVO",
            nombre="Precio nuevo",
        )
        ticket_nuevo = Ticket.objects.create(
            sucursal=self.sucursal,
            mesa=mesa_nueva,
            folio=2,
            canal=Mesa.Canal.COMEDOR,
            estado=Ticket.Estado.ABIERTO,
        )
        partida_nueva = agregar_partida(ticket_nuevo, producto)
        self.assertEqual(partida_nueva.precio_unitario, Decimal("40.00"))
        self.assertEqual(
            partida_nueva.precio_lista_capturado,
            Decimal("40.00"),
        )

    def test_codigo_fijo_no_puede_renombrarse_y_version_superior_exige_predecesora(self):
        categoria = Categoria.objects.create(
            sucursal=self.sucursal,
            nombre="Promociones mapeadas",
            orden=1,
        )
        promocion = Producto.objects.create(
            sucursal=self.sucursal,
            categoria=categoria,
            codigo="PB",
            nombre="Promoción existente",
            nombre_corto="PB",
            orden=1,
            destino_impresion=Producto.Destino.COCINA,
        )
        Precio.objects.create(
            sucursal=self.sucursal,
            producto=promocion,
            importe=Decimal("95.00"),
            vigente_desde=date(2026, 9, 1),
        )
        IdentidadCategoriaCentral.objects.create(
            sucursal=self.sucursal,
            central_id=self.categoria_central_id,
            categoria=categoria,
        )
        IdentidadProductoCentral.objects.create(
            sucursal=self.sucursal,
            central_id=self.producto_central_id,
            producto=promocion,
        )
        inicial = self.snapshot(codigo="PB", importe="95.00")
        publicacion, _ = aplicar_publicacion_catalogo(self.sucursal, inicial)

        sin_predecesora = self.snapshot(
            version=2,
            publicacion_anterior_id=None,
            codigo="PB",
            importe="96.00",
        )
        with self.assertRaises(ErrorCatalogoCentral) as version_error:
            aplicar_publicacion_catalogo(self.sucursal, sin_predecesora)
        self.assertEqual(version_error.exception.codigo, "version_fuera_de_orden")

        codigo_alterado = self.snapshot(
            version=2,
            publicacion_anterior_id=publicacion.publicacion_id,
            codigo="PB-NUEVO",
            importe="96.00",
        )
        with self.assertRaises(ErrorCatalogoCentral) as codigo_error:
            aplicar_publicacion_catalogo(self.sucursal, codigo_alterado)
        self.assertEqual(codigo_error.exception.codigo, "conflicto_local")
        promocion.refresh_from_db()
        self.assertEqual(promocion.codigo, "PB")
        self.assertEqual(PublicacionCatalogoCentral.objects.count(), 1)

    def test_rechaza_salto_y_predecesora_ajena_sin_alterar_menu_ni_ack(self):
        inicial = self.snapshot(importe="25.00")
        publicacion, _ = aplicar_publicacion_catalogo(self.sucursal, inicial)
        producto = IdentidadProductoCentral.objects.get().producto
        acks_antes = EventoOutbox.objects.filter(
            destino=EventoOutbox.Destino.CENTRAL_CATALOGO_ACK,
        ).count()
        casos = (
            (
                "version_faltante",
                self.snapshot(
                    version=3,
                    publicacion_anterior_id=publicacion.publicacion_id,
                    nombre_categoria="No aplicar salto",
                    importe="77.00",
                ),
            ),
            (
                "predecesora_incorrecta",
                self.snapshot(
                    version=2,
                    publicacion_anterior_id=uuid.uuid4(),
                    nombre_categoria="No aplicar predecesora",
                    importe="88.00",
                ),
            ),
        )

        for nombre, candidata in casos:
            with self.subTest(caso=nombre):
                with self.assertRaises(ErrorCatalogoCentral) as captura:
                    aplicar_publicacion_catalogo(self.sucursal, candidata)
                self.assertEqual(
                    captura.exception.codigo,
                    "version_fuera_de_orden",
                )
                producto.refresh_from_db()
                self.assertEqual(producto.categoria.nombre, "Comida")
                self.assertEqual(
                    producto.precio_actual().importe,
                    Decimal("25.00"),
                )
                self.assertEqual(PublicacionCatalogoCentral.objects.count(), 1)
                self.assertEqual(
                    EventoOutbox.objects.filter(
                        destino=EventoOutbox.Destino.CENTRAL_CATALOGO_ACK,
                    ).count(),
                    acks_antes,
                )

    def test_rechaza_replay_obsoleto_y_conflicto_sin_ack_aplicado_nuevo(self):
        inicial = self.snapshot(importe="25.00")
        publicacion_1, _ = aplicar_publicacion_catalogo(self.sucursal, inicial)
        segunda = self.snapshot(
            version=2,
            publicacion_anterior_id=publicacion_1.publicacion_id,
            importe="40.00",
        )
        publicacion_2, _ = aplicar_publicacion_catalogo(self.sucursal, segunda)
        acks_antes = EventoOutbox.objects.filter(
            destino=EventoOutbox.Destino.CENTRAL_CATALOGO_ACK,
        ).count()

        with self.assertRaises(ErrorCatalogoCentral) as replay_error:
            aplicar_publicacion_catalogo(self.sucursal, inicial)
        self.assertEqual(replay_error.exception.codigo, "version_fuera_de_orden")

        conflicto = copy.deepcopy(segunda)
        conflicto["publicada_en"] = "2026-09-24T13:00:00-06:00"
        with self.assertRaises(ErrorCatalogoCentral) as conflicto_error:
            aplicar_publicacion_catalogo(self.sucursal, conflicto)
        self.assertEqual(conflicto_error.exception.codigo, "conflicto_local")

        producto = IdentidadProductoCentral.objects.get().producto
        self.assertEqual(producto.precio_actual().importe, Decimal("40.00"))
        self.assertEqual(PublicacionCatalogoCentral.objects.count(), 2)
        self.assertEqual(
            PublicacionCatalogoCentral.objects.order_by("-version").first().pk,
            publicacion_2.pk,
        )
        self.assertEqual(
            EventoOutbox.objects.filter(
                destino=EventoOutbox.Destino.CENTRAL_CATALOGO_ACK,
            ).count(),
            acks_antes,
        )

    def test_snapshot_nuevo_desactiva_precio_central_futuro_obsoleto(self):
        inicial = self.snapshot(
            importe="99.00",
            vigente_desde="2026-10-15",
            aplicar_desde="2026-10-15",
        )
        publicacion_1, _ = aplicar_publicacion_catalogo(self.sucursal, inicial)
        producto = IdentidadProductoCentral.objects.get().producto
        precio_futuro = Precio.objects.get(
            producto=producto,
            vigente_desde=date(2026, 10, 15),
        )
        self.assertTrue(precio_futuro.activo)

        segunda = self.snapshot(
            version=2,
            publicacion_anterior_id=publicacion_1.publicacion_id,
            importe="30.00",
            vigente_desde="2026-09-24",
            aplicar_desde="2026-10-20",
        )
        aplicar_publicacion_catalogo(self.sucursal, segunda)

        precio_futuro.refresh_from_db()
        self.assertFalse(precio_futuro.activo)
        with patch(
            "catalogo.models.timezone.localdate",
            return_value=date(2026, 10, 20),
        ):
            vigente = producto.precio_actual()
        self.assertIsNotNone(vigente)
        self.assertEqual(vigente.importe, Decimal("30.00"))
        self.assertEqual(
            vigente.publicacion_central_id,
            uuid.UUID(segunda["publicacion_id"]),
        )
    def test_editar_grupo_promocional_conserva_precio_capturado_si_catalogo_cambia(self):
        categoria = Categoria.objects.create(
            sucursal=self.sucursal,
            nombre="Promoción con precio congelado",
            orden=1,
        )
        promocion = Producto.objects.create(
            sucursal=self.sucursal,
            categoria=categoria,
            codigo="PB",
            nombre="Taco y bebida",
            nombre_corto="PB",
            orden=1,
            destino_impresion=Producto.Destino.COCINA,
        )
        componente = Producto.objects.create(
            sucursal=self.sucursal,
            categoria=categoria,
            codigo="TB",
            nombre="Taco de barbacoa",
            nombre_corto="TB",
            orden=2,
            destino_impresion=Producto.Destino.COCINA,
        )
        Precio.objects.create(
            sucursal=self.sucursal,
            producto=promocion,
            importe=Decimal("95.00"),
            vigente_desde=date(2026, 9, 1),
        )
        precio_componente = Precio.objects.create(
            sucursal=self.sucursal,
            producto=componente,
            importe=Decimal("25.00"),
            vigente_desde=date(2026, 9, 1),
        )
        mesa = Mesa.objects.create(
            sucursal=self.sucursal,
            canal=Mesa.Canal.COMEDOR,
            clave="PROMO-CONGELADA",
            nombre="Promo congelada",
        )
        ticket = Ticket.objects.create(
            sucursal=self.sucursal,
            mesa=mesa,
            folio=3,
            canal=Mesa.Canal.COMEDOR,
            estado=Ticket.Estado.ABIERTO,
        )
        with patch("ventas.services.timezone.localdate", return_value=date(2026, 9, 21)):
            raiz = agregar_partida(ticket, promocion)
            agregar_partida(ticket, componente, cantidad=Decimal("3"))

        linea = ticket.partidas.get(producto=componente)
        self.assertEqual(linea.precio_unitario, Decimal("0.00"))
        self.assertEqual(linea.precio_lista_capturado, Decimal("25.00"))

        precio_componente.importe = Decimal("40.00")
        precio_componente.save(update_fields=["importe"])

        with patch.object(
            Producto,
            "precio_actual",
            side_effect=AssertionError(
                "Una partida capturada no debe consultar el precio vigente."
            ),
        ):
            ajustar_grupo_partidas(
                ticket,
                [linea.id],
                Decimal("3"),
            )

        linea.refresh_from_db()
        self.assertEqual(linea.promocion_aplicada_id, raiz.id)
        self.assertEqual(linea.precio_unitario, Decimal("0.00"))
        self.assertEqual(linea.precio_lista_capturado, Decimal("25.00"))

        actualizar_partida(raiz, Decimal("0"))

        linea = ticket.partidas.get(producto=componente)
        self.assertIsNone(linea.promocion_aplicada_id)
        self.assertEqual(linea.precio_unitario, Decimal("25.00"))
        self.assertEqual(linea.precio_lista_capturado, Decimal("25.00"))
