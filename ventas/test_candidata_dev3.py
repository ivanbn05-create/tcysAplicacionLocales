"""Pruebas de aceptación backend para la candidata 0.4.0-dev.3.

Este módulo mantiene juntos los contratos nuevos de la candidata para que una
regresión no se oculte entre las pruebas históricas de ``ventas.tests``.
"""

import json
import tempfile
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from impresion.models import TrabajoImpresion
from impresion.render import render_cuenta, render_reporte_administrativo, render_sucursal
from personas.models import Rol, Sucursal, UsuarioPOS

from .admin_services import (
    activar_programados,
    agregar_movimiento,
    control_efectivo_dia,
    crear_corte_caja,
    desprogramar_ticket,
    eliminar_ticket_programado,
    guardar_control_efectivo,
    programar_ticket,
    resumen_administrador,
)
from .consolidacion import (
    construir_totales,
    consolidar_periodo,
    estado_cierre_mensual,
    periodos_pendientes,
)
from .purgas_fisicas import (
    REFERENCIA_CONSOLIDACION,
    REFERENCIA_CORTE,
    crear_solicitud_archivos,
    procesar_solicitud_archivos,
)
from .models import (
    ConsecutivoFolio,
    ConsolidacionMensual,
    ControlEfectivoDia,
    CorteCaja,
    Mesa,
    MovimientoCaja,
    ReporteAdministrativo,
    Ticket,
)
from .services import (
    ErrorVenta,
    abrir_ticket,
    agregar_partida_personalizada,
    cancelar_ticket,
    cobrar_ticket,
    completar_ticket_sucursal,
    procesar_ticket,
)
from .views import _ticket_payload


class CandidataDev3BackendTests(TestCase):
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
        rol, _ = Rol.objects.get_or_create(
            sucursal=cls.sucursal,
            tipo=Rol.Tipo.ELEVADO,
            defaults={
                "nombre": "Elevado",
                "puede_cobrar": True,
                "puede_reimprimir": True,
                "puede_cancelar": True,
                "puede_sincronizar": True,
            },
        )
        cls.cancelador = UsuarioPOS(
            sucursal=cls.sucursal,
            rol=rol,
            nombre="Supervisión de prueba",
        )
        cls.cancelador.set_clave("8042")
        cls.cancelador.save()

    def _mesa(self, canal, orden=1):
        consulta = Mesa.objects.filter(
            sucursal=self.sucursal,
            canal=canal,
            activa=True,
            orden=orden,
        )
        if canal == Mesa.Canal.SUCURSALES:
            consulta = consulta.filter(cliente_sucursal__isnull=False)
        else:
            consulta = consulta.filter(cliente_sucursal__isnull=True)
        return consulta.order_by("nombre").first()

    def _ticket_personalizado(self, canal, precio, *, orden=1, nombre="Producto libre"):
        ticket, creado = abrir_ticket(self._mesa(canal, orden))
        self.assertTrue(creado)
        if canal == Mesa.Canal.RECOGER:
            ticket.cliente_nombre = "Cliente programado"
            ticket.cliente_telefono = "3312345678"
            ticket.save(update_fields=["cliente_nombre", "cliente_telefono", "actualizado_en"])
        partida = agregar_partida_personalizada(
            ticket,
            nombre,
            precio,
            cantidad=1,
            comensal=2,
        )
        ticket.refresh_from_db()
        return ticket, partida

    def test_semilla_crea_cuarenta_posiciones_para_llevar_y_recoger(self):
        for canal in (Mesa.Canal.LLEVAR, Mesa.Canal.RECOGER):
            posiciones = Mesa.objects.filter(
                sucursal=self.sucursal,
                canal=canal,
                activa=True,
                cliente_sucursal__isnull=True,
            )
            self.assertEqual(posiciones.count(), 40)
            self.assertEqual(
                set(posiciones.values_list("orden", flat=True)),
                set(range(1, 41)),
            )

    def test_producto_personalizado_funciona_en_todos_los_canales_con_payload_y_render(self):
        creados = {}
        casos = (
            (Mesa.Canal.COMEDOR, "37.50", "Especial sin catálogo"),
            (Mesa.Canal.LLEVAR, "42.00", "Especial para llevar"),
            (Mesa.Canal.DOMICILIO, "47.00", "Especial a domicilio"),
            (Mesa.Canal.RECOGER, "52.00", "Especial para recoger"),
            (Mesa.Canal.SUCURSALES, "85.00", "Paquete especial sucursal"),
        )
        for canal, precio, nombre in casos:
            with self.subTest(canal=canal):
                ticket, partida = self._ticket_personalizado(
                    canal,
                    precio,
                    nombre=nombre,
                )
                creados[canal] = (ticket, partida)
                self.assertTrue(partida.personalizada)
                self.assertIsNone(partida.producto_id)
                self.assertIsNone(partida.producto_sucursal_id)
                self.assertEqual(
                    partida.comensal,
                    1 if canal == Mesa.Canal.SUCURSALES else 2,
                )
                payload = _ticket_payload(ticket)
                fila = next(
                    item
                    for item in payload["partidas"]
                    if item["id"] == str(partida.id)
                )
                self.assertTrue(fila["personalizado"])
                self.assertEqual(fila["producto_id"], "")
                self.assertEqual(fila["producto_sucursal_id"], "")
                self.assertEqual(fila["nombre"], partida.nombre_producto)
                self.assertEqual(fila["precio"], str(partida.precio_unitario))
                self.assertEqual(fila["categoria"], "Personalizado")
                self.assertEqual(
                    fila["destino"],
                    "caja" if canal == Mesa.Canal.SUCURSALES else "cocina",
                )

        cuenta = render_cuenta(creados[Mesa.Canal.COMEDOR][0])
        total_sucursal = render_sucursal(creados[Mesa.Canal.SUCURSALES][0])
        for imagen in (cuenta, total_sucursal):
            self.assertEqual(imagen.mode, "1")
            self.assertEqual(imagen.width, 576)
            self.assertGreater(imagen.height, 100)

    def test_recoger_se_puede_programar_reprogramar_desprogramar_eliminar_y_activar(self):
        ahora = timezone.now()
        primera_fecha = timezone.localdate(ahora) + timedelta(days=2)
        segunda_fecha = primera_fecha + timedelta(days=1)
        hora_entrega = time(18, 30)

        ticket, _ = self._ticket_personalizado(
            Mesa.Canal.RECOGER,
            "45.00",
            nombre="Recoger programado",
        )
        procesar_ticket(ticket)
        programar_ticket(ticket, primera_fecha, hora_entrega)
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, Ticket.Estado.PROGRAMADO)
        self.assertEqual(ticket.canal, Mesa.Canal.RECOGER)

        programar_ticket(ticket, segunda_fecha, time(19, 15))
        ticket.refresh_from_db()
        self.assertEqual(ticket.fecha_programada, segunda_fecha)
        self.assertEqual(ticket.hora_programada, time(19, 15))

        desprogramar_ticket(ticket)
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, Ticket.Estado.PROCESADO)
        self.assertEqual(ticket.canal, Mesa.Canal.RECOGER)
        self.assertEqual(ticket.mesa.canal, Mesa.Canal.RECOGER)
        self.assertIsNone(ticket.fecha_programada)
        self.assertIsNone(ticket.hora_programada)

        programar_ticket(ticket, segunda_fecha, time(20, 0))
        eliminar_ticket_programado(ticket)
        self.assertFalse(Ticket.objects.filter(pk=ticket.pk).exists())

        activable, _ = self._ticket_personalizado(
            Mesa.Canal.RECOGER,
            "55.00",
            orden=2,
            nombre="Recoger por activar",
        )
        procesar_ticket(activable)
        programar_ticket(activable, primera_fecha, hora_entrega)
        instante = timezone.now()
        local = timezone.localtime(instante)
        Ticket.objects.filter(pk=activable.pk).update(
            fecha_programada=local.date(),
            hora_programada=(local - timedelta(minutes=1)).time().replace(microsecond=0),
        )

        self.assertEqual(activar_programados(self.sucursal, ahora=instante), 1)
        activable.refresh_from_db()
        self.assertEqual(activable.estado, Ticket.Estado.PROCESADO)
        self.assertEqual(activable.canal, Mesa.Canal.RECOGER)
        self.assertEqual(activable.mesa.canal, Mesa.Canal.RECOGER)
        self.assertIsNotNone(activable.activado_programado_en)

    def test_programado_se_edita_con_admin_lease_y_version_sin_perder_agenda(self):
        fecha_programada = timezone.localdate() + timedelta(days=3)
        hora_programada = time(18, 45)
        ticket, partida_original = self._ticket_personalizado(
            Mesa.Canal.RECOGER,
            "45.00",
            nombre="Producto original",
        )
        procesar_ticket(ticket)
        programar_ticket(ticket, fecha_programada, hora_programada)
        ticket.refresh_from_db()

        with self.assertRaisesMessage(ErrorVenta, "sigue programado"):
            procesar_ticket(ticket)

        dispositivo = "prueba-programado-a"
        ruta_ticket = f"/api/tickets/{ticket.id}/"
        cliente_sin_admin = self.client_class()
        sin_admin = cliente_sin_admin.patch(
            ruta_ticket,
            data=json.dumps(
                {
                    "edicion_programada": True,
                    "version_entidad": ticket.version_entidad,
                    "comentario_general": "No autorizado",
                }
            ),
            content_type="application/json",
            HTTP_X_POS_DEVICE_ID=dispositivo,
        )
        self.assertEqual(sin_admin.status_code, 403)

        acceso = self.client.post(
            "/api/administrador/acceso/",
            data=json.dumps({"clave_administrador": "0000"}),
            content_type="application/json",
        )
        self.assertEqual(acceso.status_code, 200)
        entrada = self.client.post(
            f"/api/administrador/tickets/{ticket.id}/editar-programado/",
            data="{}",
            content_type="application/json",
            HTTP_X_POS_DEVICE_ID=dispositivo,
        )
        self.assertEqual(entrada.status_code, 200)
        payload = entrada.json()["ticket"]
        self.assertTrue(entrada.json()["edicion_programada"])
        self.assertEqual(payload["estado"], Ticket.Estado.PROGRAMADO)
        self.assertTrue(payload["bloqueo"]["es_mio"])

        conflicto = self.client.post(
            f"/api/administrador/tickets/{ticket.id}/editar-programado/",
            data="{}",
            content_type="application/json",
            HTTP_X_POS_DEVICE_ID="prueba-programado-b",
        )
        self.assertEqual(conflicto.status_code, 423)

        sin_modo = self.client.patch(
            ruta_ticket,
            data=json.dumps(
                {
                    "version_entidad": payload["version_entidad"],
                    "comentario_general": "Sin contrato de edición",
                }
            ),
            content_type="application/json",
            HTTP_X_POS_DEVICE_ID=dispositivo,
        )
        self.assertEqual(sin_modo.status_code, 400)
        self.assertIn("desde Administración", sin_modo.json()["error"])

        procesado_indebido = self.client.post(
            f"/api/tickets/{ticket.id}/procesar/",
            data=json.dumps(
                {
                    "edicion_programada": True,
                    "version_entidad": payload["version_entidad"],
                }
            ),
            content_type="application/json",
            HTTP_X_POS_DEVICE_ID=dispositivo,
        )
        self.assertEqual(procesado_indebido.status_code, 400)

        datos_editados = self.client.patch(
            ruta_ticket,
            data=json.dumps(
                {
                    "edicion_programada": True,
                    "version_entidad": payload["version_entidad"],
                    "comentario_general": "Entregar sin cebolla",
                    "cliente_nombre": "Cliente corregido",
                    "cliente_telefono": "3312340000",
                }
            ),
            content_type="application/json",
            HTTP_X_POS_DEVICE_ID=dispositivo,
        )
        self.assertEqual(datos_editados.status_code, 200)
        payload = datos_editados.json()["ticket"]

        agregado = self.client.post(
            f"/api/tickets/{ticket.id}/partidas/",
            data=json.dumps(
                {
                    "edicion_programada": True,
                    "version_entidad": payload["version_entidad"],
                    "personalizado": True,
                    "nombre_producto": "Producto corregido",
                    "precio_unitario": "63.50",
                    "cantidad": 2,
                    "comensal": 1,
                }
            ),
            content_type="application/json",
            HTTP_X_POS_DEVICE_ID=dispositivo,
        )
        self.assertEqual(agregado.status_code, 200)
        payload = agregado.json()["ticket"]

        eliminado = self.client.delete(
            f"/api/partidas/{partida_original.id}/",
            data=json.dumps(
                {
                    "edicion_programada": True,
                    "version_entidad": payload["version_entidad"],
                }
            ),
            content_type="application/json",
            HTTP_X_POS_DEVICE_ID=dispositivo,
        )
        self.assertEqual(eliminado.status_code, 200)

        liberado = self.client.delete(
            f"/api/tickets/{ticket.id}/bloqueo/",
            data=json.dumps({"device_id": dispositivo}),
            content_type="application/json",
            HTTP_X_POS_DEVICE_ID=dispositivo,
        )
        self.assertEqual(liberado.status_code, 200)
        self.assertFalse(liberado.json()["bloqueo"]["activo"])

        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, Ticket.Estado.PROGRAMADO)
        self.assertEqual(ticket.fecha_programada, fecha_programada)
        self.assertEqual(ticket.hora_programada, hora_programada)
        self.assertEqual(ticket.cliente_nombre, "Cliente corregido")
        self.assertEqual(ticket.cliente_telefono, "3312340000")
        self.assertEqual(ticket.comentario_general, "Entregar sin cebolla")
        self.assertEqual(
            list(ticket.partidas.values_list("nombre_producto", flat=True)),
            ["Producto corregido"],
        )

    def test_frontend_cubre_subtabs_refresh_lan_y_edicion_programada(self):
        respuesta = self.client.get("/administrador/")
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(
            respuesta,
            'id="tabs-sucursales" class="tabs-sucursales" role="tablist"',
        )
        self.assertContains(
            respuesta,
            'id="pedidos-sucursal-activa" class="tickets-pendientes panel-sucursal" role="tabpanel"',
        )

        raiz = Path(__file__).resolve().parent
        admin_js = (raiz / "static" / "ventas" / "admin.js").read_text(
            encoding="utf-8"
        )
        app_js = (raiz / "static" / "ventas" / "app.js").read_text(
            encoding="utf-8"
        )
        for contrato in (
            'role="tab" tabindex="',
            'aria-selected="',
            'panelSucursal.setAttribute("aria-labelledby", tabActiva.id)',
            '["ArrowLeft", "ArrowRight", "Home", "End"]',
            'data-editar-programado href="/?editar_programado=',
            'aria-label="Editar contenido del pedido ',
        ):
            with self.subTest(contrato=contrato):
                self.assertIn(contrato, admin_js)

        inicio_refresco = app_js.index("function puedeActualizarEstadoLan()")
        fin_refresco = app_js.index("function ticketResumenEstado", inicio_refresco)
        contrato_refresco = app_js[inicio_refresco:fin_refresco]
        self.assertIn("const INTERVALO_ESTADO_LAN_MS = 5000", app_js)
        self.assertIn("!estado.ticket", contrato_refresco)
        self.assertIn(
            "setInterval(actualizarEstadoLan, INTERVALO_ESTADO_LAN_MS)",
            contrato_refresco,
        )
        self.assertNotIn("location.reload", contrato_refresco)
        self.assertIn("editandoProgramado ||", app_js)
        self.assertIn('$("#switch-tipo-pedido").disabled = !editable || editandoProgramado', app_js)
        self.assertIn('window.location.assign("/administrador/#programados")', app_js)

    def test_tabletas_declara_fullscreen_y_usa_keypad_en_pagina_con_objetivos_tactiles(self):
        respuesta = self.client.get("/tabletas/")
        self.assertEqual(respuesta.status_code, 200)
        manifest = self.client.get("/manifest.webmanifest?modo=tableta").json()
        self.assertEqual(manifest["display_override"], ["fullscreen", "standalone"])
        self.assertEqual(manifest["start_url"], "/tabletas/")

        raiz = Path(__file__).resolve().parent
        app_js = (raiz / "static" / "ventas" / "app.js").read_text(
            encoding="utf-8"
        )
        app_css = (raiz / "static" / "ventas" / "app.css").read_text(
            encoding="utf-8"
        )
        inicio_pin = app_js.index("function pedirClavePos")
        fin_pin = app_js.index("function pedirFormaPago", inicio_pin)
        contrato_pin = app_js[inicio_pin:fin_pin]
        self.assertIn("claveEnZonaComedor = modoTableta", contrato_pin)
        self.assertLess(
            contrato_pin.index("return new Promise"),
            contrato_pin.index('const dialogo = $("#dialogo-clave-pos")'),
        )
        self.assertIn('data-tecla-pin-tableta="${numero}"', app_js)
        self.assertIn('data-tecla-pin-tableta="0"', app_js)
        self.assertIn('data-tecla-pin-tableta="confirmar"', app_js)
        self.assertIn('aria-label="Teclado numérico para código de acceso"', app_js)
        self.assertIn(".teclado-pin-tableta button { min-width: 0; min-height: 58px", app_css)
        self.assertIn("iniciarPantallaCompletaPredeterminada();", app_js)
        self.assertIn(
            'document.addEventListener("pointerdown", activarPantallaCompletaConPrimerToque, true)',
            app_js,
        )

    def test_mes_anterior_confirmado_bloquea_nuevas_ventas_hasta_purgada(self):
        ahora = timezone.now()
        cobrable, _ = self._ticket_personalizado(
            Mesa.Canal.COMEDOR,
            "62.00",
            nombre="Venta en espera del cierre",
        )
        procesar_ticket(cobrable)

        programado, _ = self._ticket_personalizado(
            Mesa.Canal.RECOGER,
            "48.00",
            nombre="Programado en espera del cierre",
        )
        procesar_ticket(programado)
        programar_ticket(
            programado,
            timezone.localdate(ahora) + timedelta(days=1),
            time(18, 0),
        )
        local = timezone.localtime(ahora)
        Ticket.objects.filter(pk=programado.pk).update(
            fecha_programada=local.date(),
            hora_programada=(local - timedelta(minutes=1)).time().replace(microsecond=0),
        )

        ultimo_dia_anterior = timezone.localdate(ahora).replace(day=1) - timedelta(days=1)
        consolidacion = ConsolidacionMensual.objects.create(
            sucursal=self.sucursal,
            periodo=ultimo_dia_anterior.replace(day=1),
            estado=ConsolidacionMensual.Estado.CONFIRMADA,
            totales={},
            acuse_vps="acuse-laboratorio",
            confirmado_en=ahora,
            purgado_en=ahora,
        )

        with self.assertRaisesRegex(ErrorVenta, "Debes consolidar"):
            abrir_ticket(self._mesa(Mesa.Canal.COMEDOR, orden=2))
        with self.assertRaisesRegex(ErrorVenta, "Debes consolidar"):
            cobrar_ticket(cobrable, Ticket.FormaPago.EFECTIVO, "62.00")

        self.assertEqual(activar_programados(self.sucursal, ahora=ahora), 0)
        programado.refresh_from_db()
        self.assertEqual(programado.estado, Ticket.Estado.PROGRAMADO)
        # La consulta administrativa llama activar_programados y debe seguir
        # disponible mientras SYSTEM termina la purga física.
        self.assertIsInstance(resumen_administrador(self.sucursal), dict)
        programado.refresh_from_db()
        self.assertEqual(programado.estado, Ticket.Estado.PROGRAMADO)

        consolidacion.estado = ConsolidacionMensual.Estado.PURGADA
        consolidacion.save(update_fields=["estado"])

        _, creado = abrir_ticket(self._mesa(Mesa.Canal.COMEDOR, orden=2))
        self.assertTrue(creado)
        cobrar_ticket(cobrable, Ticket.FormaPago.EFECTIVO, "62.00")
        cobrable.refresh_from_db()
        self.assertEqual(cobrable.estado, Ticket.Estado.PAGADO)
        self.assertEqual(activar_programados(self.sucursal, ahora=ahora), 1)
        programado.refresh_from_db()
        self.assertEqual(programado.estado, Ticket.Estado.PROCESADO)

    def test_movimientos_control_formula_corte_purga_detalle_y_conserva_reporte(self):
        venta, _ = self._ticket_personalizado(
            Mesa.Canal.COMEDOR,
            "100.00",
            nombre="Venta de caja",
        )
        procesar_ticket(venta)
        cobrar_ticket(venta, Ticket.FormaPago.EFECTIVO, "100.00")

        pedido_sucursal, _ = self._ticket_personalizado(
            Mesa.Canal.SUCURSALES,
            "70.00",
            orden=1,
            nombre="Venta externa",
        )
        procesar_ticket(pedido_sucursal)
        completar_ticket_sucursal(pedido_sucursal)

        cancelado, _ = self._ticket_personalizado(
            Mesa.Canal.COMEDOR,
            "15.00",
            orden=2,
            nombre="Pedido cancelado",
        )
        procesar_ticket(cancelado)
        cancelar_ticket(
            cancelado,
            permitir_procesado=True,
            cancelado_por=self.cancelador,
        )
        cancelado.refresh_from_db()
        self.assertEqual(cancelado.cancelado_por_id, self.cancelador.id)
        cancelaciones_antes = resumen_administrador(self.sucursal)["cancelaciones"]
        self.assertTrue(
            any(
                item["id"] == str(cancelado.id)
                and item["cancelado_por_nombre"] == self.cancelador.nombre
                for item in cancelaciones_antes
            )
        )

        agregar_movimiento(self.sucursal, MovimientoCaja.Tipo.INGRESO, "Cambio extra", "50")
        agregar_movimiento(self.sucursal, MovimientoCaja.Tipo.GASTO, "Insumos", "20")
        agregar_movimiento(self.sucursal, MovimientoCaja.Tipo.TERMINAL, "Cobro terminal", "30")
        control = guardar_control_efectivo(
            self.sucursal,
            {
                "fondo_anterior": {"100": 2},
                "fondo_siguiente": {"50": 1},
                "ventas_apps": {
                    "rappi": "10",
                    "didi": "20",
                    "uber_eats": "30",
                },
            },
        )

        corte, reporte = crear_corte_caja(self.sucursal)
        corte.refresh_from_db()
        reporte.refresh_from_db()

        # 200 + 50 + 100 - 20 - 30 - 50 = 250. La venta de sucursal
        # y las Apps se informan por separado y no alteran la caja local.
        self.assertEqual(corte.total_fondo_anterior, Decimal("200.00"))
        self.assertEqual(corte.total_entradas, Decimal("50.00"))
        self.assertEqual(corte.total_salidas, Decimal("20.00"))
        self.assertEqual(corte.total_terminales, Decimal("30.00"))
        self.assertEqual(corte.total_fondo_siguiente, Decimal("50.00"))
        self.assertEqual(corte.total_caja, Decimal("250.00"))
        self.assertEqual(
            sum((Decimal(str(valor)) for valor in corte.totales_sucursales.values()), Decimal("0.00")),
            Decimal("70.00"),
        )
        self.assertEqual(
            corte.ventas_apps,
            {"rappi": "10.00", "didi": "20.00", "uber_eats": "30.00"},
        )

        ids_cerrados = [venta.id, pedido_sucursal.id, cancelado.id]
        self.assertFalse(Ticket.objects.filter(pk__in=ids_cerrados).exists())
        self.assertIsNotNone(corte.detalle_eliminado_en)
        self.assertTrue(ReporteAdministrativo.objects.filter(pk=reporte.pk).exists())
        self.assertFalse(resumen_administrador(self.sucursal)["cancelaciones"])
        imagen = render_reporte_administrativo(reporte)
        self.assertEqual(imagen.mode, "1")
        self.assertEqual(imagen.width, 576)

        siguiente = control_efectivo_dia(
            self.sucursal,
            fecha=control.fecha + timedelta(days=1),
        )
        self.assertEqual(siguiente.fondo_anterior["50"], 1)

    def test_periodos_pendientes_detectan_actividad_sin_corte(self):
        hoy = timezone.localdate()
        mes_actual = date(hoy.year, hoy.month, 1)
        ultimo_anterior = mes_actual - timedelta(days=1)
        periodo = date(ultimo_anterior.year, ultimo_anterior.month, 1)
        instante = timezone.make_aware(
            datetime.combine(ultimo_anterior, time(12, 0)),
            timezone.get_current_timezone(),
        )

        ticket, _ = self._ticket_personalizado(
            Mesa.Canal.COMEDOR,
            "12.00",
            orden=4,
            nombre="Actividad anterior",
        )
        Ticket.objects.filter(pk=ticket.pk).update(
            estado=Ticket.Estado.PROGRAMADO,
            creado_en=instante,
            fecha_programada=hoy + timedelta(days=1),
            hora_programada=time(13, 0),
        )
        self.assertEqual(periodos_pendientes(self.sucursal, hoy=mes_actual), [])
        Ticket.objects.filter(pk=ticket.pk).update(estado=Ticket.Estado.CANCELADO)
        self.assertEqual(periodos_pendientes(self.sucursal, hoy=mes_actual), [periodo])
        Ticket.objects.filter(pk=ticket.pk).delete()

        movimiento = MovimientoCaja.objects.create(
            sucursal=self.sucursal,
            tipo=MovimientoCaja.Tipo.GASTO,
            concepto="Actividad sin corte",
            importe=Decimal("1.00"),
        )
        MovimientoCaja.objects.filter(pk=movimiento.pk).update(creado_en=instante)
        self.assertEqual(periodos_pendientes(self.sucursal, hoy=mes_actual), [periodo])
        MovimientoCaja.objects.filter(pk=movimiento.pk).delete()

        ControlEfectivoDia.objects.create(
            sucursal=self.sucursal,
            fecha=ultimo_anterior,
        )
        self.assertEqual(periodos_pendientes(self.sucursal, hoy=mes_actual), [])

        control = ControlEfectivoDia.objects.get(
            sucursal=self.sucursal,
            fecha=ultimo_anterior,
        )
        for campo, valor in (
            ("fondo_anterior", {"100": 1}),
            ("fondo_siguiente", {"50": 2}),
            ("ventas_apps", {"rappi": "0.01"}),
        ):
            setattr(control, campo, valor)
            control.save(update_fields=[campo, "actualizado_en"])
            self.assertEqual(periodos_pendientes(self.sucursal, hoy=mes_actual), [periodo])
            setattr(control, campo, {})
            control.save(update_fields=[campo, "actualizado_en"])
            self.assertEqual(periodos_pendientes(self.sucursal, hoy=mes_actual), [])

    def test_fallo_de_archivo_conserva_solicitud_y_reintenta(self):
        reporte = ReporteAdministrativo.objects.create(
            sucursal=self.sucursal,
            tipo=ReporteAdministrativo.Tipo.CORTE_CAJA,
            datos={"titulo": "PURGA REINTENTABLE"},
        )
        corte = CorteCaja.objects.create(
            sucursal=self.sucursal,
            inicio=timezone.now(),
            fin=timezone.now(),
            reporte=reporte,
            totales_canales={},
            total_ventas=Decimal("0.00"),
            total_entradas=Decimal("0.00"),
            total_salidas=Decimal("0.00"),
            total_caja=Decimal("0.00"),
            detalle_eliminado_en=timezone.now(),
        )
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            media = raiz / "media"
            solicitudes = raiz / "runtime" / "purgas-pendientes"
            archivo = media / "tickets" / "bloqueado.png"
            archivo.parent.mkdir(parents=True)
            archivo.write_bytes(b"pendiente")
            with override_settings(
                MEDIA_ROOT=media,
                PURGAS_PENDIENTES_ROOT=solicitudes,
            ):
                solicitud = crear_solicitud_archivos(
                    ["tickets/bloqueado.png"],
                    referencia_tipo=REFERENCIA_CORTE,
                    referencia_id=corte.pk,
                )
                bloqueado = MagicMock()
                bloqueado.exists.return_value = True
                bloqueado.unlink.side_effect = OSError("archivo en uso")
                with patch(
                    "ventas.purgas_fisicas._ruta_media_segura",
                    return_value=bloqueado,
                ):
                    self.assertFalse(procesar_solicitud_archivos(solicitud))

                self.assertTrue(solicitud.exists())
                pendiente = json.loads(solicitud.read_text(encoding="utf-8"))
                self.assertEqual(pendiente["intentos"], 1)
                self.assertIn("archivo en uso", pendiente["ultimo_error"])
                self.assertTrue(archivo.exists())

                self.assertTrue(
                    procesar_solicitud_archivos(solicitud, propagar=True)
                )
                self.assertFalse(solicitud.exists())
                self.assertFalse(archivo.exists())

                archivo_logico = media / "reportes" / "sin-purga-logica.png"
                archivo_logico.parent.mkdir(parents=True)
                archivo_logico.write_bytes(b"pendiente")
                periodo = date(2025, 1, 1)
                consolidacion = ConsolidacionMensual.objects.create(
                    sucursal=self.sucursal,
                    periodo=periodo,
                    estado=ConsolidacionMensual.Estado.CONFIRMADA,
                    acuse_vps="acuse-sin-purga",
                    confirmado_en=timezone.now(),
                )
                solicitud_logica = crear_solicitud_archivos(
                    ["reportes/sin-purga-logica.png"],
                    referencia_tipo=REFERENCIA_CONSOLIDACION,
                    referencia_id=consolidacion.pk,
                )
                self.assertFalse(
                    procesar_solicitud_archivos(solicitud_logica)
                )
                self.assertTrue(archivo_logico.exists())
                consolidacion.purgado_en = timezone.now()
                consolidacion.save(update_fields=["purgado_en"])
                self.assertTrue(
                    procesar_solicitud_archivos(
                        solicitud_logica,
                        propagar=True,
                    )
                )
                self.assertFalse(archivo_logico.exists())

    def test_corte_cruzado_pertenece_al_inicio_y_purga_archivos_seguros(self):
        hoy = timezone.localdate()
        mes_actual = date(hoy.year, hoy.month, 1)
        ultimo_anterior = mes_actual - timedelta(days=1)
        periodo = date(ultimo_anterior.year, ultimo_anterior.month, 1)
        inicio = timezone.make_aware(
            datetime.combine(ultimo_anterior, time(23, 30)),
            timezone.get_current_timezone(),
        )
        fin = timezone.make_aware(
            datetime.combine(mes_actual, time(0, 30)),
            timezone.get_current_timezone(),
        )
        residual, _ = self._ticket_personalizado(
            Mesa.Canal.COMEDOR,
            "9.00",
            orden=5,
            nombre="Cancelado residual",
        )
        Ticket.objects.filter(pk=residual.pk).update(
            estado=Ticket.Estado.CANCELADO,
            creado_en=inicio,
        )
        reporte = ReporteAdministrativo.objects.create(
            sucursal=self.sucursal,
            tipo=ReporteAdministrativo.Tipo.CORTE_CAJA,
            datos={"titulo": "CORTE CRUZADO"},
        )
        corte = CorteCaja.objects.create(
            sucursal=self.sucursal,
            inicio=inicio,
            fin=fin,
            reporte=reporte,
            totales_canales={Mesa.Canal.COMEDOR: "50.00"},
            total_ventas=Decimal("50.00"),
            total_entradas=Decimal("0.00"),
            total_salidas=Decimal("0.00"),
            total_caja=Decimal("50.00"),
        )


        self.assertEqual(construir_totales(self.sucursal, periodo)["ventas"], "50.00")
        with self.assertRaisesMessage(ErrorVenta, "No hay cortes"):
            construir_totales(self.sucursal, mes_actual)
        self.assertEqual(periodos_pendientes(self.sucursal, hoy=mes_actual), [periodo])

        respuesta = MagicMock()
        respuesta.status = 200
        respuesta.getcode.return_value = 200
        respuesta.read.return_value = b'{"recibido":true,"acuse":"cruce-001"}'
        respuesta.__enter__.return_value = respuesta
        respuesta.__exit__.return_value = False
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            media = raiz / "media"
            archivo_ticket = media / "tickets" / "residual.png"
            archivo_reporte = media / "reportes" / "corte.png"
            archivo_externo = raiz / "fuera.png"
            archivo_ticket.parent.mkdir(parents=True)
            archivo_reporte.parent.mkdir(parents=True)
            archivo_ticket.write_bytes(b"ticket")
            archivo_reporte.write_bytes(b"reporte")
            archivo_externo.write_bytes(b"fuera")
            TrabajoImpresion.objects.create(
                sucursal=self.sucursal,
                ticket=residual,
                formato=TrabajoImpresion.Formato.CUENTA,
                destino=TrabajoImpresion.Destino.CAJA,
                estado=TrabajoImpresion.Estado.GENERADO,
                archivo="tickets/residual.png",
            )
            TrabajoImpresion.objects.create(
                sucursal=self.sucursal,
                reporte=reporte,
                formato=TrabajoImpresion.Formato.CORTE_CAJA,
                destino=TrabajoImpresion.Destino.CAJA,
                estado=TrabajoImpresion.Estado.GENERADO,
                archivo="reportes/corte.png",
            )
            TrabajoImpresion.objects.create(
                sucursal=self.sucursal,
                reporte=reporte,
                formato=TrabajoImpresion.Formato.CORTE_CAJA,
                destino=TrabajoImpresion.Destino.CAJA,
                estado=TrabajoImpresion.Estado.GENERADO,
                archivo="../fuera.png",
            )
            with (
                override_settings(
                    MEDIA_ROOT=media,
                    VPS_CONSOLIDACION_URL="https://vps.invalid/api/consolidaciones",
                    VPS_CONSOLIDACION_TOKEN="token-prueba",
                    VPS_CONSOLIDACION_TIMEOUT=1,
                    PURGAS_PENDIENTES_ROOT=raiz / "purgas-pendientes",
                ),
                patch("ventas.consolidacion.urlopen", return_value=respuesta),
                self.captureOnCommitCallbacks(execute=True),
            ):
                solicitud_diaria = crear_solicitud_archivos(
                    ["tickets/residual.png"],
                    referencia_tipo=REFERENCIA_CORTE,
                    referencia_id=corte.pk,
                )
                consolidar_periodo(self.sucursal, periodo)

            self.assertFalse(solicitud_diaria.exists())
            solicitudes_restantes = [
                json.loads(path.read_text(encoding="utf-8"))["tipo"]
                for path in (raiz / "purgas-pendientes").glob("purga-*.json")
            ]
            self.assertEqual(solicitudes_restantes, ["respaldos_periodo"])
            self.assertFalse(archivo_ticket.exists())
            self.assertFalse(archivo_reporte.exists())
            self.assertTrue(archivo_externo.exists())

        self.assertFalse(CorteCaja.objects.filter(pk=corte.pk).exists())
        self.assertFalse(Ticket.objects.filter(pk=residual.pk).exists())
        self.assertFalse(ReporteAdministrativo.objects.filter(pk=reporte.pk).exists())

    def test_estado_incluye_programados_de_domicilio_y_recoger_con_canal(self):
        fecha = timezone.localdate() + timedelta(days=2)
        creados = []
        for canal, orden in ((Mesa.Canal.DOMICILIO, 6), (Mesa.Canal.RECOGER, 6)):
            ticket, _ = self._ticket_personalizado(
                canal,
                "18.00",
                orden=orden,
                nombre=f"Programado {canal}",
            )
            Ticket.objects.filter(pk=ticket.pk).update(
                estado=Ticket.Estado.PROGRAMADO,
                fecha_programada=fecha,
                hora_programada=time(14, 0),
            )
            creados.append(ticket.id)

        respuesta = self.client.get("/api/estado/")
        self.assertEqual(respuesta.status_code, 200)
        programados = {
            item["ticket_id"]: item
            for item in respuesta.json()["programados"]
            if item["ticket_id"] in {str(ticket_id) for ticket_id in creados}
        }
        self.assertEqual(set(programados), {str(ticket_id) for ticket_id in creados})
        self.assertEqual(
            {item["canal"] for item in programados.values()},
            {Mesa.Canal.DOMICILIO, Mesa.Canal.RECOGER},
        )
    def test_programado_activado_en_otro_mes_usa_el_periodo_operativo(self):
        mes_actual = date(timezone.localdate().year, timezone.localdate().month, 1)
        ultimo_dia_activacion = mes_actual - timedelta(days=1)
        periodo_activacion = date(
            ultimo_dia_activacion.year,
            ultimo_dia_activacion.month,
            1,
        )
        ultimo_dia_creacion = periodo_activacion - timedelta(days=1)
        periodo_creacion = date(
            ultimo_dia_creacion.year,
            ultimo_dia_creacion.month,
            1,
        )
        zona = timezone.get_current_timezone()
        creado_en = timezone.make_aware(
            datetime.combine(ultimo_dia_creacion, time(12, 0)),
            zona,
        )
        activado_en = timezone.make_aware(
            datetime.combine(ultimo_dia_activacion, time(12, 0)),
            zona,
        )
        ticket, _ = self._ticket_personalizado(
            Mesa.Canal.RECOGER,
            "42.00",
            nombre="Programado entre meses",
        )
        Ticket.objects.filter(pk=ticket.pk).update(
            creado_en=creado_en,
            activado_programado_en=activado_en,
            estado=Ticket.Estado.ABIERTO,
        )

        self.assertEqual(
            periodos_pendientes(self.sucursal, hoy=mes_actual),
            [periodo_activacion],
        )

        def crear_corte(periodo, total):
            instante = timezone.make_aware(
                datetime.combine(periodo + timedelta(days=1), time(20, 0)),
                zona,
            )
            reporte = ReporteAdministrativo.objects.create(
                sucursal=self.sucursal,
                tipo=ReporteAdministrativo.Tipo.CORTE_CAJA,
                datos={"titulo": f"CORTE {periodo.isoformat()}"},
            )
            return CorteCaja.objects.create(
                sucursal=self.sucursal,
                inicio=instante - timedelta(hours=8),
                fin=instante,
                reporte=reporte,
                totales_canales={Mesa.Canal.RECOGER: str(total)},
                total_ventas=total,
                total_entradas=Decimal("0.00"),
                total_salidas=Decimal("0.00"),
                total_caja=total,
            )

        corte_creacion = crear_corte(periodo_creacion, Decimal("10.00"))
        corte_activacion = crear_corte(periodo_activacion, Decimal("42.00"))
        self.assertEqual(
            construir_totales(self.sucursal, periodo_creacion)["ventas"],
            "10.00",
        )
        with self.assertRaisesMessage(ErrorVenta, "Aún hay detalle"):
            construir_totales(self.sucursal, periodo_activacion)

        Ticket.objects.filter(pk=ticket.pk).update(estado=Ticket.Estado.CANCELADO)
        ConsolidacionMensual.objects.create(
            sucursal=self.sucursal,
            periodo=periodo_activacion,
            estado=ConsolidacionMensual.Estado.CONFIRMADA,
            totales={"ventas": "42.00"},
            acuse_vps="acuse-cruce-mes",
            confirmado_en=timezone.now(),
        )
        with tempfile.TemporaryDirectory() as temporal:
            with (
                override_settings(
                    PURGAS_PENDIENTES_ROOT=Path(temporal) / "purgas-pendientes",
                ),
                patch("ventas.consolidacion._enviar_vps") as enviar,
            ):
                consolidar_periodo(self.sucursal, periodo_activacion)

        enviar.assert_not_called()
        self.assertFalse(Ticket.objects.filter(pk=ticket.pk).exists())
        self.assertTrue(CorteCaja.objects.filter(pk=corte_creacion.pk).exists())
        self.assertFalse(CorteCaja.objects.filter(pk=corte_activacion.pk).exists())

    def test_reentrada_confirmada_y_purgada_no_reenvia_ni_reinicia_dos_veces(self):
        mes_actual = date(timezone.localdate().year, timezone.localdate().month, 1)
        ultimo_anterior = mes_actual - timedelta(days=1)
        periodo = date(ultimo_anterior.year, ultimo_anterior.month, 1)
        fin = timezone.make_aware(
            datetime.combine(ultimo_anterior, time(21, 0)),
            timezone.get_current_timezone(),
        )
        reporte = ReporteAdministrativo.objects.create(
            sucursal=self.sucursal,
            tipo=ReporteAdministrativo.Tipo.CORTE_CAJA,
            datos={"titulo": "CORTE CONFIRMADO"},
        )
        CorteCaja.objects.create(
            sucursal=self.sucursal,
            inicio=fin - timedelta(hours=8),
            fin=fin,
            reporte=reporte,
            totales_canales={Mesa.Canal.COMEDOR: "25.00"},
            total_ventas=Decimal("25.00"),
            total_entradas=Decimal("0.00"),
            total_salidas=Decimal("0.00"),
            total_caja=Decimal("25.00"),
        )
        consolidacion = ConsolidacionMensual.objects.create(
            sucursal=self.sucursal,
            periodo=periodo,
            estado=ConsolidacionMensual.Estado.CONFIRMADA,
            totales={"ventas": "25.00"},
            acuse_vps="acuse-previo",
            confirmado_en=timezone.now(),
        )

        with tempfile.TemporaryDirectory() as temporal:
            with (
                override_settings(
                    PURGAS_PENDIENTES_ROOT=Path(temporal) / "purgas-pendientes",
                ),
                patch("ventas.consolidacion._enviar_vps") as enviar,
                patch("ventas.services.reiniciar_folios") as reiniciar,
            ):
                primera = consolidar_periodo(self.sucursal, periodo)
                segunda = consolidar_periodo(self.sucursal, periodo)
                self.assertEqual(
                    len(
                        list(
                            (Path(temporal) / "purgas-pendientes").glob(
                                "purga-*.json"
                            )
                        )
                    ),
                    1,
                )

        enviar.assert_not_called()
        reiniciar.assert_called_once_with(self.sucursal)
        self.assertEqual(primera.pk, consolidacion.pk)
        self.assertEqual(segunda.pk, consolidacion.pk)
        self.assertEqual(primera.estado, ConsolidacionMensual.Estado.CONFIRMADA)
        self.assertEqual(segunda.estado, ConsolidacionMensual.Estado.CONFIRMADA)
        self.assertIsNotNone(primera.purgado_en)

    def test_consolidacion_no_purga_sin_url_ni_acuse_y_purga_con_acuse(self):
        hoy = timezone.localdate()
        primer_dia_mes = date(hoy.year, hoy.month, 1)
        ultimo_dia_anterior = primer_dia_mes - timedelta(days=1)
        periodo = date(ultimo_dia_anterior.year, ultimo_dia_anterior.month, 1)
        fin = timezone.make_aware(
            datetime.combine(ultimo_dia_anterior, time(22, 0)),
            timezone.get_current_timezone(),
        )
        reporte = ReporteAdministrativo.objects.create(
            sucursal=self.sucursal,
            tipo=ReporteAdministrativo.Tipo.CORTE_CAJA,
            datos={"titulo": "CORTE DE CAJA", "total_caja": "300.00"},
        )
        corte = CorteCaja.objects.create(
            sucursal=self.sucursal,
            inicio=None,
            fin=fin,
            reporte=reporte,
            totales_canales={Mesa.Canal.COMEDOR: "321.00"},
            total_ventas=Decimal("321.00"),
            total_entradas=Decimal("15.00"),
            total_salidas=Decimal("6.00"),
            total_terminales=Decimal("30.00"),
            ventas_apps={"rappi": "12.00"},
            totales_sucursales={"Estancia": "90.00"},
            total_caja=Decimal("300.00"),
        )
        consecutivo, _ = ConsecutivoFolio.objects.update_or_create(
            sucursal=self.sucursal,
            defaults={"serie": 4, "ultimo": 27},
        )

        with override_settings(
            VPS_CONSOLIDACION_URL="",
            VPS_CONSOLIDACION_TOKEN="",
            VPS_CONSOLIDACION_TIMEOUT=1,
        ):
            with self.assertRaises(ErrorVenta):
                consolidar_periodo(self.sucursal, periodo)
        self.assertTrue(CorteCaja.objects.filter(pk=corte.pk).exists())
        self.assertTrue(ReporteAdministrativo.objects.filter(pk=reporte.pk).exists())
        consecutivo.refresh_from_db()
        self.assertEqual((consecutivo.serie, consecutivo.ultimo), (4, 27))

        sin_acuse = MagicMock()
        sin_acuse.status = 200
        sin_acuse.getcode.return_value = 200
        sin_acuse.read.return_value = b'{"recibido":true}'
        sin_acuse.__enter__.return_value = sin_acuse
        sin_acuse.__exit__.return_value = False
        with override_settings(
            VPS_CONSOLIDACION_URL="https://vps.invalid/api/consolidaciones",
            VPS_CONSOLIDACION_TOKEN="token-prueba",
            VPS_CONSOLIDACION_TIMEOUT=1,
        ):
            with patch("ventas.consolidacion.urlopen", return_value=sin_acuse):
                with self.assertRaises(ErrorVenta):
                    consolidar_periodo(self.sucursal, periodo)
        self.assertTrue(CorteCaja.objects.filter(pk=corte.pk).exists())
        self.assertTrue(ReporteAdministrativo.objects.filter(pk=reporte.pk).exists())

        con_acuse = MagicMock()
        con_acuse.status = 200
        con_acuse.getcode.return_value = 200
        con_acuse.read.return_value = b'{"recibido":true,"acuse":"acuse-vps-001"}'
        con_acuse.__enter__.return_value = con_acuse
        con_acuse.__exit__.return_value = False
        with tempfile.TemporaryDirectory() as temporal:
            solicitudes_purga = Path(temporal) / "purgas-pendientes"
            with override_settings(
                VPS_CONSOLIDACION_URL="https://vps.invalid/api/consolidaciones",
                VPS_CONSOLIDACION_TOKEN="token-prueba",
                VPS_CONSOLIDACION_TIMEOUT=1,
                PURGAS_PENDIENTES_ROOT=solicitudes_purga,
            ):
                with patch("ventas.consolidacion.urlopen", return_value=con_acuse) as enviar:
                    consolidacion = consolidar_periodo(self.sucursal, periodo)
            pendientes = list(solicitudes_purga.glob("purga-*.json"))
            self.assertEqual(len(pendientes), 1)
            solicitud_pendiente = json.loads(pendientes[0].read_text(encoding="utf-8"))
            self.assertEqual(solicitud_pendiente["tipo"], "respaldos_periodo")

        solicitud = enviar.call_args.args[0]
        payload_enviado = json.loads(solicitud.data.decode("utf-8"))
        self.assertEqual(payload_enviado["periodo"], periodo.isoformat())
        self.assertEqual(payload_enviado["totales"]["ventas"], "321.00")
        self.assertEqual(consolidacion.estado, ConsolidacionMensual.Estado.CONFIRMADA)
        self.assertEqual(consolidacion.acuse_vps, "acuse-vps-001")
        self.assertIsNotNone(consolidacion.confirmado_en)
        self.assertIsNotNone(consolidacion.purgado_en)
        cierre = estado_cierre_mensual(self.sucursal, hoy=primer_dia_mes)
        self.assertTrue(cierre["purga_fisica_pendiente"])
        self.assertEqual(cierre["intervalo_purga_segundos"], 300)
        self.assertFalse(CorteCaja.objects.filter(pk=corte.pk).exists())
        self.assertFalse(ReporteAdministrativo.objects.filter(pk=reporte.pk).exists())
        consecutivo.refresh_from_db()
        self.assertEqual((consecutivo.serie, consecutivo.ultimo), (5, 0))