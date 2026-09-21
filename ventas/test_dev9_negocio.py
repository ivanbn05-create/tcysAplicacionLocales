import importlib
import json
import tempfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth.hashers import make_password
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone

from impresion.models import TrabajoImpresion
from impresion.services import encolar_impresiones, encolar_reporte, reclamar_siguiente
from personas.models import Rol, Sucursal, UsuarioPOS

from .admin_services import (
    activar_programados,
    actualizar_movimiento,
    agregar_movimiento,
    control_efectivo_dia,
    crear_corte_caja,
    crear_previa_corte_caja,
    desprogramar_ticket,
    eliminar_movimiento,
)
from .models import (
    ConfiguracionSucursal,
    ControlEfectivoDia,
    CorteCaja,
    CorteSucursal,
    EventoOutbox,
    Mesa,
    MovimientoCaja,
    Partida,
    PrecioProductoSucursal,
    ProductoSucursal,
    ReporteAdministrativo,
    SucursalPedido,
    Ticket,
)
from .services import (
    ErrorVenta,
    abrir_ticket,
    agregar_partida_sucursal,
    completar_ticket_sucursal,
    crear_ticket_repetido,
    procesar_ticket,
    reactivar_ticket_sucursal,
)


@override_settings(
    SUCURSAL_CLAVE="ARBOLEDAS",
    POS_REQUIRE_AUTH=False,
    PRINT_SYNC=False,
    PRINT_BACKEND="archivo",
    PEDIDOS_SUCURSALES_AUTO_SYNC=False,
)
class NegocioDev9Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command(
            "aprovisionar_sucursal",
            clave="ARBOLEDAS",
            nombre="Arboledas",
            verbosity=0,
        )
        call_command("cargar_datos_iniciales", verbosity=0)
        cls.sucursal = Sucursal.objects.get(clave="ARBOLEDAS")
        configuracion = ConfiguracionSucursal.objects.get(sucursal=cls.sucursal)
        configuracion.clave_administrador = make_password("1212")
        configuracion.save(update_fields=["clave_administrador", "actualizado_en"])

        rol_elevado = Rol.objects.create(
            sucursal=cls.sucursal,
            nombre="Elevado dev9",
            tipo=Rol.Tipo.ELEVADO,
            puede_cobrar=True,
            puede_reimprimir=True,
            puede_cancelar=True,
            puede_sincronizar=True,
        )
        cls.elevado = UsuarioPOS(
            sucursal=cls.sucursal,
            rol=rol_elevado,
            nombre="Supervisión dev9",
        )
        cls.elevado.set_clave("3434")
        cls.elevado.save()

    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory()
        self.ajustes_media = override_settings(MEDIA_ROOT=self.temporal.name)
        self.ajustes_media.enable()

    def tearDown(self):
        self.ajustes_media.disable()
        self.temporal.cleanup()

    def _pedido_sucursal(self, *, producto_origen=7, cantidad="1.000", orden=None):
        cliente = SucursalPedido.objects.get(
            sucursal=self.sucursal,
            origen_id=3,
        )
        mesas = Mesa.objects.filter(
            sucursal=self.sucursal,
            canal=Mesa.Canal.SUCURSALES,
            cliente_sucursal=cliente,
        ).order_by("orden")
        if orden is not None:
            mesas = mesas.filter(orden=orden)
        mesa = mesas.first()
        ticket, creado = abrir_ticket(mesa)
        self.assertTrue(creado)
        producto = ProductoSucursal.objects.get(
            sucursal=self.sucursal,
            origen_id=producto_origen,
        )
        partida = agregar_partida_sucursal(ticket, producto, Decimal(cantidad))
        return ticket, partida

    def _autorizar_admin(self, clave="1212"):
        return self.client.post(
            "/api/administrador/acceso/",
            data=json.dumps({"clave_administrador": clave}),
            content_type="application/json",
        )

    def test_reactivar_sucursal_respeta_clave_version_bloqueo_y_auditoria(self):
        ticket, partida = self._pedido_sucursal()
        procesar_ticket(ticket)
        ticket.refresh_from_db()
        version_procesada = ticket.version_entidad
        partida.refresh_from_db()
        self.assertTrue(partida.procesada)

        desactualizada = self.client.post(
            f"/api/tickets/{ticket.id}/reactivar-sucursal/",
            data=json.dumps(
                {
                    "clave_administrador": "1212",
                    "version_entidad": version_procesada - 1,
                }
            ),
            content_type="application/json",
            HTTP_X_POS_DEVICE_ID="caja-dev9",
        )
        self.assertEqual(desactualizada.status_code, 409)
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, Ticket.Estado.PROCESADO)
        self.assertEqual(ticket.bloqueo_device_id, "")

        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/reactivar-sucursal/",
            data=json.dumps(
                {
                    "clave_administrador": "1212",
                    "version_entidad": version_procesada,
                }
            ),
            content_type="application/json",
            HTTP_X_POS_DEVICE_ID="caja-dev9",
        )
        self.assertEqual(respuesta.status_code, 200)
        ticket.refresh_from_db()
        partida.refresh_from_db()
        self.assertEqual(ticket.estado, Ticket.Estado.ABIERTO)
        self.assertTrue(ticket.comanda_en_edicion)
        self.assertIsNone(ticket.procesado_en)
        self.assertEqual(ticket.version_entidad, version_procesada + 1)
        self.assertEqual(ticket.bloqueo_device_id, "caja-dev9")
        self.assertFalse(partida.procesada)
        evento = EventoOutbox.objects.get(
            agregado_id=ticket.id,
            tipo="ticket.sucursal_reactivado",
        )
        self.assertEqual(evento.datos["nivel_acceso"], "administrador")

    def test_reactivar_acepta_elevado_y_rechaza_pedido_con_corte(self):
        ticket, _ = self._pedido_sucursal(orden=2)
        procesar_ticket(ticket)
        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/reactivar-sucursal/",
            data=json.dumps({"clave_administrador": "3434"}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        evento = EventoOutbox.objects.get(
            agregado_id=ticket.id,
            tipo="ticket.sucursal_reactivado",
        )
        self.assertEqual(evento.datos["nivel_acceso"], "elevado")
        self.assertEqual(evento.datos["reactivado_por_id"], str(self.elevado.id))

        ticket.refresh_from_db()
        procesar_ticket(ticket)
        reporte = ReporteAdministrativo.objects.create(
            sucursal=self.sucursal,
            tipo=ReporteAdministrativo.Tipo.CORTE_SUCURSAL,
            datos={"titulo": "CORTE DE SUCURSAL"},
        )
        corte = CorteSucursal.objects.create(
            sucursal=self.sucursal,
            cliente_sucursal=ticket.mesa.cliente_sucursal,
            reporte=reporte,
            partidas=[],
            total=ticket.total,
        )
        corte.tickets.add(ticket)
        with self.assertRaisesRegex(ErrorVenta, "pertenece a un corte"):
            reactivar_ticket_sucursal(ticket)

    def test_reactivar_rechaza_clave_incorrecta_y_canal_no_sucursal(self):
        ticket_sucursal, _ = self._pedido_sucursal()
        procesar_ticket(ticket_sucursal)
        respuesta = self.client.post(
            f"/api/tickets/{ticket_sucursal.id}/reactivar-sucursal/",
            data=json.dumps({"clave_administrador": "9999"}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 400)
        ticket_sucursal.refresh_from_db()
        self.assertEqual(ticket_sucursal.estado, Ticket.Estado.PROCESADO)
        self.assertFalse(
            EventoOutbox.objects.filter(
                agregado_id=ticket_sucursal.id,
                tipo="ticket.sucursal_reactivado",
            ).exists()
        )

        mesa = Mesa.objects.get(sucursal=self.sucursal, clave="MESA-1")
        ticket_comedor, creado = abrir_ticket(mesa)
        self.assertTrue(creado)
        ticket_comedor.estado = Ticket.Estado.PROCESADO
        ticket_comedor.comanda_en_edicion = False
        ticket_comedor.procesado_en = timezone.now()
        ticket_comedor.save(
            update_fields=["estado", "comanda_en_edicion", "procesado_en"]
        )
        respuesta = self.client.post(
            f"/api/tickets/{ticket_comedor.id}/reactivar-sucursal/",
            data=json.dumps({"clave_administrador": "1212"}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("no pertenece", respuesta.json()["error"])
        ticket_comedor.refresh_from_db()
        self.assertEqual(ticket_comedor.estado, Ticket.Estado.PROCESADO)

    def test_reactivar_descarta_impresiones_no_impresas_y_permite_reprocesar(self):
        ticket, _ = self._pedido_sucursal()
        procesar_ticket(ticket)
        impreso = TrabajoImpresion.objects.create(
            sucursal=self.sucursal,
            ticket=ticket,
            formato=TrabajoImpresion.Formato.SUCURSAL,
            destino=TrabajoImpresion.Destino.CAJA,
            estado=TrabajoImpresion.Estado.IMPRESO,
        )
        pendiente = TrabajoImpresion.objects.create(
            sucursal=self.sucursal,
            ticket=ticket,
            formato=TrabajoImpresion.Formato.SUCURSAL,
            destino=TrabajoImpresion.Destino.CAJA,
            estado=TrabajoImpresion.Estado.PENDIENTE,
        )
        error = TrabajoImpresion.objects.create(
            sucursal=self.sucursal,
            ticket=ticket,
            formato=TrabajoImpresion.Formato.SUCURSAL,
            destino=TrabajoImpresion.Destino.CAJA,
            estado=TrabajoImpresion.Estado.ERROR,
        )
        otro_formato = TrabajoImpresion.objects.create(
            sucursal=self.sucursal,
            ticket=ticket,
            formato=TrabajoImpresion.Formato.COMANDA,
            destino=TrabajoImpresion.Destino.COCINA,
            comanda_numero=ticket.comanda_actual,
            estado=TrabajoImpresion.Estado.PENDIENTE,
        )

        ticket = reactivar_ticket_sucursal(ticket, nivel_acceso="administrador")
        self.assertTrue(TrabajoImpresion.objects.filter(pk=impreso.pk).exists())
        self.assertFalse(TrabajoImpresion.objects.filter(pk=pendiente.pk).exists())
        self.assertFalse(TrabajoImpresion.objects.filter(pk=error.pk).exists())
        self.assertTrue(TrabajoImpresion.objects.filter(pk=otro_formato.pk).exists())
        evento = EventoOutbox.objects.get(
            agregado_id=ticket.id,
            tipo="ticket.sucursal_reactivado",
        )
        self.assertEqual(evento.datos["impresiones_descartadas"], 2)

        ticket = procesar_ticket(ticket)
        nuevos = encolar_impresiones(ticket, TrabajoImpresion.Formato.SUCURSAL)
        self.assertEqual(len(nuevos), 1)
        self.assertEqual(
            TrabajoImpresion.objects.filter(
                ticket=ticket,
                formato=TrabajoImpresion.Formato.SUCURSAL,
            ).count(),
            2,
        )
        self.assertTrue(TrabajoImpresion.objects.filter(pk=impreso.pk).exists())

    def test_reactivar_espera_si_la_impresion_sigue_en_curso(self):
        ticket, _ = self._pedido_sucursal()
        procesar_ticket(ticket)
        procesando = TrabajoImpresion.objects.create(
            sucursal=self.sucursal,
            ticket=ticket,
            formato=TrabajoImpresion.Formato.SUCURSAL,
            destino=TrabajoImpresion.Destino.CAJA,
            estado=TrabajoImpresion.Estado.PROCESANDO,
            procesado_en=timezone.now(),
        )

        with self.assertRaisesRegex(ErrorVenta, "impresión.*curso"):
            reactivar_ticket_sucursal(ticket, nivel_acceso="administrador")

        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, Ticket.Estado.PROCESADO)
        self.assertTrue(TrabajoImpresion.objects.filter(pk=procesando.pk).exists())

    @override_settings(PRINT_PROCESSING_TIMEOUT_SECONDS=60)
    def test_reactivar_descarta_una_impresion_procesando_abandonada(self):
        ticket, _ = self._pedido_sucursal()
        procesar_ticket(ticket)
        abandonado = TrabajoImpresion.objects.create(
            sucursal=self.sucursal,
            ticket=ticket,
            formato=TrabajoImpresion.Formato.SUCURSAL,
            destino=TrabajoImpresion.Destino.CAJA,
            estado=TrabajoImpresion.Estado.PROCESANDO,
            procesado_en=timezone.now() - timedelta(seconds=61),
        )

        ticket = reactivar_ticket_sucursal(
            ticket,
            nivel_acceso="administrador",
        )

        self.assertEqual(ticket.estado, Ticket.Estado.ABIERTO)
        self.assertFalse(
            TrabajoImpresion.objects.filter(pk=abandonado.pk).exists()
        )
        evento = EventoOutbox.objects.get(
            agregado_id=ticket.id,
            tipo="ticket.sucursal_reactivado",
        )
        self.assertEqual(evento.datos["impresiones_abandonadas"], 1)

    @override_settings(PRINT_PROCESSING_TIMEOUT_SECONDS=60)
    def test_worker_recupera_solo_el_trabajo_procesando_vencido(self):
        ticket, _ = self._pedido_sucursal()
        ahora = timezone.now()
        vigente = TrabajoImpresion.objects.create(
            sucursal=self.sucursal,
            ticket=ticket,
            formato=TrabajoImpresion.Formato.SUCURSAL,
            destino=TrabajoImpresion.Destino.CAJA,
            estado=TrabajoImpresion.Estado.PROCESANDO,
            procesado_en=ahora - timedelta(seconds=30),
            intentos=1,
        )
        abandonado = TrabajoImpresion.objects.create(
            sucursal=self.sucursal,
            ticket=ticket,
            formato=TrabajoImpresion.Formato.SUCURSAL,
            destino=TrabajoImpresion.Destino.CAJA,
            estado=TrabajoImpresion.Estado.PROCESANDO,
            procesado_en=ahora - timedelta(seconds=61),
            intentos=1,
        )

        with patch("impresion.services.timezone.now", return_value=ahora):
            reclamado = reclamar_siguiente()

        self.assertEqual(reclamado.pk, abandonado.pk)
        self.assertEqual(reclamado.estado, TrabajoImpresion.Estado.PROCESANDO)
        self.assertEqual(reclamado.intentos, 2)
        self.assertEqual(reclamado.procesado_en, ahora)
        vigente.refresh_from_db()
        self.assertEqual(vigente.estado, TrabajoImpresion.Estado.PROCESANDO)
        self.assertEqual(vigente.intentos, 1)

    def test_catalogo_sucursal_cobra_chile_por_kilo_y_barbacoa_por_litro(self):
        chile = ProductoSucursal.objects.get(
            sucursal=self.sucursal,
            origen_id=7,
        )
        barbacoa = ProductoSucursal.objects.get(
            sucursal=self.sucursal,
            origen_id=1,
        )
        self.assertEqual((chile.unidad, chile.cantidad_por_precio), ("KG", Decimal("1.000")))
        self.assertEqual((barbacoa.unidad, barbacoa.cantidad_por_precio), ("LT", Decimal("1.000")))
        self.assertFalse(
            PrecioProductoSucursal.objects.filter(
                producto=chile,
            ).exclude(importe=Decimal("64.00")).exists()
        )

        ticket_chile, partida_chile = self._pedido_sucursal(
            producto_origen=7,
            cantidad="1.000",
            orden=3,
        )
        self.assertEqual(partida_chile.unidad, "KG")
        self.assertEqual(partida_chile.importe, Decimal("64.00"))
        ticket_chile.delete()

        _, partida_barbacoa = self._pedido_sucursal(
            producto_origen=1,
            cantidad="1.000",
            orden=3,
        )
        self.assertEqual(partida_barbacoa.unidad, "LT")
        self.assertEqual(partida_barbacoa.importe, Decimal("193.00"))

    def test_migracion_corrige_catalogo_de_una_instalacion_existente(self):
        barbacoa = ProductoSucursal.objects.get(
            sucursal=self.sucursal,
            origen_id=1,
        )
        chile = ProductoSucursal.objects.get(
            sucursal=self.sucursal,
            origen_id=7,
        )
        barbacoa.unidad = "KG"
        barbacoa.save(update_fields=["unidad"])
        chile.unidad = "PZA"
        chile.cantidad_por_precio = Decimal("30.000")
        chile.save(update_fields=["unidad", "cantidad_por_precio"])
        PrecioProductoSucursal.objects.filter(producto=chile).update(
            importe=Decimal("1.00")
        )
        precio_base = PrecioProductoSucursal.objects.filter(producto=chile).first()
        historico = PrecioProductoSucursal.objects.create(
            sucursal=self.sucursal,
            cliente_sucursal=precio_base.cliente_sucursal,
            producto=chile,
            importe=Decimal("10.00"),
            nombre_ticket=precio_base.nombre_ticket,
            vigente_desde=date(2026, 1, 1),
        )
        futuro = PrecioProductoSucursal.objects.create(
            sucursal=self.sucursal,
            cliente_sucursal=precio_base.cliente_sucursal,
            producto=chile,
            importe=Decimal("80.00"),
            nombre_ticket=precio_base.nombre_ticket,
            vigente_desde=date(2026, 12, 1),
        )

        migracion = importlib.import_module(
            "ventas.migrations.0019_catalogo_sucursales_unidades_dev9"
        )
        migracion.corregir_unidades_y_precio(apps, None)

        barbacoa.refresh_from_db()
        chile.refresh_from_db()
        self.assertEqual((barbacoa.unidad, barbacoa.cantidad_por_precio), ("LT", Decimal("1.000")))
        self.assertEqual((chile.unidad, chile.cantidad_por_precio), ("KG", Decimal("1.000")))
        self.assertFalse(
            PrecioProductoSucursal.objects.filter(
                producto=chile,
                vigente_desde=date(2026, 8, 15),
            ).exclude(
                importe=Decimal("64.00")
            ).exists()
        )
        historico.refresh_from_db()
        futuro.refresh_from_db()
        self.assertEqual(historico.importe, Decimal("10.00"))
        self.assertEqual(futuro.importe, Decimal("80.00"))

    def test_sucursales_no_crean_movimientos_ni_entran_al_total_de_caja(self):
        ticket, partida = self._pedido_sucursal()
        self.assertEqual(partida.importe, Decimal("64.00"))
        procesar_ticket(ticket)
        completar_ticket_sucursal(ticket)
        self.assertFalse(MovimientoCaja.objects.filter(sucursal=self.sucursal).exists())

        corte, reporte = crear_corte_caja(self.sucursal)
        self.assertEqual(corte.total_ventas, Decimal("0.00"))
        self.assertEqual(corte.total_caja, Decimal("0.00"))
        self.assertEqual(corte.totales_sucursales, {"Estancia": "64.00"})
        self.assertEqual(reporte.datos["canales"][Mesa.Canal.SUCURSALES], "64.00")
        self.assertFalse(MovimientoCaja.objects.filter(sucursal=self.sucursal).exists())

    def test_fondo_siguiente_abre_turno_mismo_dia_y_cruce_de_fecha(self):
        zona = timezone.get_current_timezone()
        primer_corte = timezone.make_aware(datetime(2026, 9, 21, 20, 0), zona)
        control = control_efectivo_dia(self.sucursal, primer_corte.date())
        control.fondo_anterior = {"100": 2}
        control.fondo_siguiente = {"50": 1}
        control.ventas_apps = {"rappi": "10.00"}
        control.save()

        with patch("ventas.admin_services.timezone.now", return_value=primer_corte):
            corte_1, reporte_1 = crear_corte_caja(self.sucursal)
        control.refresh_from_db()
        siguiente = ControlEfectivoDia.objects.get(
            sucursal=self.sucursal,
            fecha=primer_corte.date() + timedelta(days=1),
        )
        self.assertEqual(corte_1.total_fondo_siguiente, Decimal("50.00"))
        self.assertEqual(reporte_1.datos["fondo_siguiente_desglose"]["50"], 1)
        self.assertEqual(control.fondo_anterior["50"], 1)
        self.assertEqual(control.fondo_siguiente["50"], 0)
        self.assertEqual(control.ventas_apps["rappi"], "0.00")
        self.assertEqual(siguiente.fondo_anterior["50"], 1)

        control.fondo_siguiente = {"20": 3}
        control.save(update_fields=["fondo_siguiente", "actualizado_en"])
        segundo_corte = primer_corte + timedelta(hours=1)
        with patch("ventas.admin_services.timezone.now", return_value=segundo_corte):
            corte_2, reporte_2 = crear_corte_caja(self.sucursal)
        control.refresh_from_db()
        siguiente.refresh_from_db()
        self.assertEqual(corte_2.total_fondo_anterior, Decimal("50.00"))
        self.assertEqual(corte_2.total_fondo_siguiente, Decimal("60.00"))
        self.assertEqual(reporte_2.datos["fondo_anterior_desglose"]["50"], 1)
        self.assertEqual(control.fondo_anterior["20"], 3)
        self.assertEqual(siguiente.fondo_anterior["20"], 3)

        siguiente.fondo_siguiente = {"100": 1}
        siguiente.save(update_fields=["fondo_siguiente", "actualizado_en"])
        tercer_corte = primer_corte + timedelta(days=1, hours=1)
        with patch("ventas.admin_services.timezone.now", return_value=tercer_corte):
            corte_3, _ = crear_corte_caja(self.sucursal)
        siguiente.refresh_from_db()
        subsiguiente = ControlEfectivoDia.objects.get(
            sucursal=self.sucursal,
            fecha=tercer_corte.date() + timedelta(days=1),
        )
        self.assertEqual(corte_3.total_fondo_anterior, Decimal("60.00"))
        self.assertEqual(corte_3.total_fondo_siguiente, Decimal("100.00"))
        self.assertEqual(siguiente.fondo_anterior["100"], 1)
        self.assertEqual(subsiguiente.fondo_anterior["100"], 1)

    def test_corte_duplicado_vacio_no_sobrescribe_el_fondo_propagado(self):
        instante = timezone.now()
        control = control_efectivo_dia(
            self.sucursal,
            timezone.localdate(instante),
        )
        control.fondo_siguiente = {"50": 2}
        control.save(update_fields=["fondo_siguiente", "actualizado_en"])

        with patch("ventas.admin_services.timezone.now", return_value=instante):
            primer_corte, _ = crear_corte_caja(self.sucursal)
        siguiente = ControlEfectivoDia.objects.get(
            sucursal=self.sucursal,
            fecha=timezone.localdate(instante) + timedelta(days=1),
        )

        with patch(
            "ventas.admin_services.timezone.now",
            return_value=instante + timedelta(seconds=1),
        ), self.assertRaisesRegex(ErrorVenta, "corte duplicado vacío"):
            crear_corte_caja(self.sucursal)

        self.assertEqual(
            CorteCaja.objects.filter(sucursal=self.sucursal).count(),
            1,
        )
        self.assertTrue(CorteCaja.objects.filter(pk=primer_corte.pk).exists())
        control.refresh_from_db()
        siguiente.refresh_from_db()
        self.assertEqual(control.fondo_anterior["50"], 2)
        self.assertEqual(siguiente.fondo_anterior["50"], 2)

    def test_previa_exige_liquidacion_no_cierra_y_se_purga_en_corte_real(self):
        ticket, _ = self._pedido_sucursal()
        procesar_ticket(ticket)
        with self.assertRaisesRegex(ErrorVenta, "por completar"):
            crear_previa_corte_caja(self.sucursal)
        self.assertFalse(CorteCaja.objects.filter(sucursal=self.sucursal).exists())

        completar_ticket_sucursal(ticket)
        movimiento_previo = agregar_movimiento(
            self.sucursal,
            MovimientoCaja.Tipo.INGRESO,
            "Cambio inicial",
            "25.00",
        )
        control = control_efectivo_dia(self.sucursal)
        control.fondo_siguiente = {"10": 1}
        control.save(update_fields=["fondo_siguiente", "actualizado_en"])
        inicio_antes = ticket.creado_en

        self.assertEqual(self._autorizar_admin().status_code, 200)
        profundidad_base = len(connection.atomic_blocks)
        profundidades_encolado = []

        def encolar_dentro_de_transaccion(reporte):
            profundidades_encolado.append(len(connection.atomic_blocks))
            return encolar_reporte(reporte)

        with patch(
            "ventas.views.encolar_reporte",
            side_effect=encolar_dentro_de_transaccion,
        ):
            respuesta = self.client.post(
                "/api/administrador/corte-caja/previa/",
                data="{}",
                content_type="application/json",
            )
        self.assertEqual(respuesta.status_code, 201)
        self.assertEqual(len(profundidades_encolado), 1)
        self.assertGreater(profundidades_encolado[0], profundidad_base)
        reporte_previo = ReporteAdministrativo.objects.get(
            pk=respuesta.json()["reporte_id"]
        )
        self.assertTrue(reporte_previo.datos["es_previa"])
        self.assertEqual(reporte_previo.datos["titulo"], "PREVIA DE CORTE DE CAJA")
        self.assertEqual(reporte_previo.datos["ingresos"], "25.00")
        self.assertEqual(CorteCaja.objects.filter(sucursal=self.sucursal).count(), 0)
        self.assertTrue(Ticket.objects.filter(pk=ticket.pk).exists())
        self.assertTrue(MovimientoCaja.objects.filter(pk=movimiento_previo.pk).exists())
        control.refresh_from_db()
        self.assertEqual(control.fondo_siguiente["10"], 1)
        trabajo_previo = TrabajoImpresion.objects.get(reporte=reporte_previo)
        self.assertEqual(trabajo_previo.formato, TrabajoImpresion.Formato.CORTE_CAJA)
        self.assertEqual(reporte_previo.datos["inicio"], inicio_antes.isoformat())

        agregar_movimiento(
            self.sucursal,
            MovimientoCaja.Tipo.GASTO,
            "Ajuste descubierto después de la previa",
            "5.00",
        )
        corte, reporte_final = crear_corte_caja(self.sucursal)
        self.assertEqual(corte.total_entradas, Decimal("25.00"))
        self.assertEqual(corte.total_salidas, Decimal("5.00"))
        self.assertEqual(len(reporte_final.datos["movimientos"]), 2)
        self.assertFalse(ReporteAdministrativo.objects.filter(pk=reporte_previo.pk).exists())
        self.assertFalse(TrabajoImpresion.objects.filter(pk=trabajo_previo.pk).exists())

    def test_previa_es_snapshot_hasta_fin_y_no_muta_datos(self):
        incluido = agregar_movimiento(
            self.sucursal,
            MovimientoCaja.Tipo.INGRESO,
            "Incluido en previa",
            "7.00",
        )
        posterior = agregar_movimiento(
            self.sucursal,
            MovimientoCaja.Tipo.INGRESO,
            "Posterior a previa",
            "11.00",
        )
        mesa = Mesa.objects.get(sucursal=self.sucursal, clave="MESA-3")
        ticket_posterior, creado = abrir_ticket(mesa)
        self.assertTrue(creado)
        fin = timezone.now()
        MovimientoCaja.objects.filter(pk=incluido.pk).update(
            creado_en=fin - timedelta(seconds=1)
        )
        MovimientoCaja.objects.filter(pk=posterior.pk).update(
            creado_en=fin + timedelta(seconds=1)
        )
        Ticket.objects.filter(pk=ticket_posterior.pk).update(
            creado_en=fin + timedelta(seconds=1)
        )

        with patch("ventas.admin_services.timezone.now", return_value=fin):
            reporte = crear_previa_corte_caja(self.sucursal)

        self.assertEqual(reporte.datos["fin"], fin.isoformat())
        self.assertEqual(reporte.datos["ingresos"], "7.00")
        self.assertEqual(
            [item["concepto"] for item in reporte.datos["movimientos"]],
            ["Incluido en previa"],
        )
        self.assertFalse(CorteCaja.objects.filter(sucursal=self.sucursal).exists())
        self.assertTrue(MovimientoCaja.objects.filter(pk=incluido.pk).exists())
        self.assertTrue(MovimientoCaja.objects.filter(pk=posterior.pk).exists())
        self.assertTrue(Ticket.objects.filter(pk=ticket_posterior.pk).exists())

    def test_movimientos_usan_barrera_sucursal_y_revalidan_corte(self):
        manager_sucursal = Sucursal.objects
        with patch.object(
            manager_sucursal,
            "select_for_update",
            wraps=manager_sucursal.select_for_update,
        ) as bloquear_sucursal:
            movimiento = agregar_movimiento(
                self.sucursal,
                MovimientoCaja.Tipo.INGRESO,
                "Movimiento serializado",
                "15.00",
            )
            actualizar_movimiento(
                self.sucursal,
                movimiento,
                MovimientoCaja.Tipo.GASTO,
                "Movimiento actualizado",
                "9.00",
            )
            eliminar_movimiento(self.sucursal, movimiento)
        self.assertEqual(bloquear_sucursal.call_count, 3)

        movimiento = agregar_movimiento(
            self.sucursal,
            MovimientoCaja.Tipo.INGRESO,
            "Movimiento ya cortado",
            "12.00",
        )
        reporte = ReporteAdministrativo.objects.create(
            sucursal=self.sucursal,
            tipo=ReporteAdministrativo.Tipo.CORTE_CAJA,
            datos={"titulo": "CORTE DE PRUEBA"},
        )
        corte = CorteCaja.objects.create(
            sucursal=self.sucursal,
            fin=timezone.now(),
            reporte=reporte,
            totales_canales={},
            total_ventas=Decimal("0.00"),
            total_entradas=Decimal("12.00"),
            total_salidas=Decimal("0.00"),
            total_caja=Decimal("12.00"),
        )
        corte.movimientos.add(movimiento)
        with self.assertRaisesRegex(ErrorVenta, "pertenece a un corte"):
            actualizar_movimiento(
                self.sucursal,
                movimiento,
                MovimientoCaja.Tipo.GASTO,
                "No debe guardarse",
                "1.00",
            )
        with self.assertRaisesRegex(ErrorVenta, "pertenece a un corte"):
            eliminar_movimiento(self.sucursal, movimiento)
        movimiento.refresh_from_db()
        self.assertEqual(movimiento.tipo, MovimientoCaja.Tipo.INGRESO)
        self.assertEqual(movimiento.importe, Decimal("12.00"))

    def test_activar_y_desprogramar_usan_barrera_sucursal(self):
        mesa = Mesa.objects.filter(
            sucursal=self.sucursal,
            canal=Mesa.Canal.RECOGER,
            activa=True,
        ).order_by("orden").first()
        ticket, creado = abrir_ticket(mesa)
        self.assertTrue(creado)
        instante = timezone.now()
        local = timezone.localtime(instante)
        ticket.estado = Ticket.Estado.PROGRAMADO
        ticket.tipo_entrega = Ticket.TipoEntrega.PROGRAMADA
        ticket.fecha_programada = local.date()
        ticket.hora_programada = (
            local - timedelta(minutes=1)
        ).time().replace(microsecond=0)
        ticket.save(
            update_fields=[
                "estado",
                "tipo_entrega",
                "fecha_programada",
                "hora_programada",
            ]
        )

        manager_sucursal = Sucursal.objects
        with patch.object(
            manager_sucursal,
            "select_for_update",
            wraps=manager_sucursal.select_for_update,
        ) as bloquear_sucursal:
            self.assertEqual(
                activar_programados(self.sucursal, ahora=instante),
                1,
            )
        self.assertEqual(bloquear_sucursal.call_count, 1)
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, Ticket.Estado.PROCESADO)

        ticket.estado = Ticket.Estado.PROGRAMADO
        ticket.tipo_entrega = Ticket.TipoEntrega.PROGRAMADA
        ticket.fecha_programada = local.date() + timedelta(days=1)
        ticket.hora_programada = local.time().replace(microsecond=0)
        ticket.activado_programado_en = None
        ticket.save(
            update_fields=[
                "estado",
                "tipo_entrega",
                "fecha_programada",
                "hora_programada",
                "activado_programado_en",
            ]
        )
        with patch.object(
            manager_sucursal,
            "select_for_update",
            wraps=manager_sucursal.select_for_update,
        ) as bloquear_sucursal:
            desprogramar_ticket(ticket)
        self.assertEqual(bloquear_sucursal.call_count, 1)
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, Ticket.Estado.PROCESADO)
        self.assertIsNotNone(ticket.activado_programado_en)

    def test_repetir_ticket_y_su_caller_bloquean_sucursal_antes_del_ticket(self):
        mesa = Mesa.objects.filter(
            sucursal=self.sucursal,
            canal=Mesa.Canal.RECOGER,
            activa=True,
        ).order_by("orden").first()
        ticket, creado = abrir_ticket(mesa)
        self.assertTrue(creado)
        ticket.estado = Ticket.Estado.PROCESADO
        ticket.comanda_en_edicion = False
        ticket.cliente_nombre = "Pedido repetido"
        ticket.cliente_telefono = "3312345678"
        ticket.save(
            update_fields=[
                "estado",
                "comanda_en_edicion",
                "cliente_nombre",
                "cliente_telefono",
            ]
        )

        eventos = []
        manager_sucursal = Sucursal.objects
        manager_ticket = Ticket.objects
        manager_mesa = Mesa.objects

        def bloquear(manager, etiqueta):
            def aplicar(*args, **kwargs):
                eventos.append(etiqueta)
                return manager.get_queryset().select_for_update(*args, **kwargs)

            return aplicar

        with patch.object(
            manager_sucursal,
            "select_for_update",
            side_effect=bloquear(manager_sucursal, "sucursal"),
        ), patch.object(
            manager_ticket,
            "select_for_update",
            side_effect=bloquear(manager_ticket, "ticket"),
        ), patch.object(
            manager_mesa,
            "select_for_update",
            side_effect=bloquear(manager_mesa, "mesa"),
        ):
            nuevo, creado = crear_ticket_repetido(
                ticket,
                "repeticion-dev9-servicio",
                atendio=self.elevado,
            )
        self.assertTrue(creado)
        self.assertEqual(eventos[:3], ["sucursal", "ticket", "mesa"])
        nuevo.estado = Ticket.Estado.PAGADO
        nuevo.save(update_fields=["estado"])

        eventos.clear()
        with patch.object(
            manager_sucursal,
            "select_for_update",
            side_effect=bloquear(manager_sucursal, "sucursal"),
        ), patch.object(
            manager_ticket,
            "select_for_update",
            side_effect=bloquear(manager_ticket, "ticket"),
        ), patch.object(
            manager_mesa,
            "select_for_update",
            side_effect=bloquear(manager_mesa, "mesa"),
        ):
            respuesta = self.client.post(
                f"/api/tickets/{ticket.id}/comandas/",
                data=json.dumps({"idempotency_key": "repeticion-dev9-vista"}),
                content_type="application/json",
            )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(eventos[0], "sucursal")
        self.assertLess(eventos.index("sucursal"), eventos.index("ticket"))

    def test_repetir_ticket_revalida_si_el_corte_lo_borra_antes_de_la_barrera(self):
        mesa = Mesa.objects.filter(
            sucursal=self.sucursal,
            canal=Mesa.Canal.RECOGER,
            activa=True,
        ).order_by("orden").first()
        ticket, creado = abrir_ticket(mesa)
        self.assertTrue(creado)
        ticket.estado = Ticket.Estado.PROCESADO
        ticket.comanda_en_edicion = False
        ticket.save(update_fields=["estado", "comanda_en_edicion"])
        manager_sucursal = Sucursal.objects

        def bloquear_despues_de_borrado(*args, **kwargs):
            Ticket.objects.filter(pk=ticket.pk).delete()
            return manager_sucursal.get_queryset().select_for_update(
                *args,
                **kwargs,
            )

        with patch.object(
            manager_sucursal,
            "select_for_update",
            side_effect=bloquear_despues_de_borrado,
        ):
            respuesta = self.client.post(
                f"/api/tickets/{ticket.id}/comandas/",
                data=json.dumps({"idempotency_key": "repeticion-dev9-carrera"}),
                content_type="application/json",
            )

        self.assertEqual(respuesta.status_code, 404)
        self.assertTrue(Ticket.objects.filter(pk=ticket.pk).exists())

    def test_corte_no_bloquea_ni_purga_un_ticket_posterior_a_su_fin(self):
        mesa = Mesa.objects.get(sucursal=self.sucursal, clave="MESA-2")
        ticket, creado = abrir_ticket(mesa)
        self.assertTrue(creado)
        fin = timezone.now()
        creado_despues = fin + timedelta(seconds=1)
        Ticket.objects.filter(pk=ticket.pk).update(creado_en=creado_despues)

        with patch("ventas.admin_services.timezone.now", return_value=fin):
            corte, _ = crear_corte_caja(self.sucursal)

        self.assertEqual(corte.fin, fin)
        self.assertTrue(Ticket.objects.filter(pk=ticket.pk).exists())
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, Ticket.Estado.ABIERTO)
