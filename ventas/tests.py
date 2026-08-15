import json
import tempfile
from unittest.mock import patch
from decimal import Decimal
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase, override_settings

from catalogo.models import Producto
from impresion.models import TrabajoImpresion
from impresion.render import escpos_raster, render_comanda, render_cuenta
from impresion.services import encolar_impresiones
from personas.models import Sucursal

from .models import EventoOutbox, Mesa, ModificadorTicket, Ticket
from .services import abrir_ticket, agregar_partida, alternar_modificador, cobrar_ticket, procesar_ticket


class FlujoPOSTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("cargar_datos_iniciales", verbosity=0)

    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory()
        self.ajustes = override_settings(MEDIA_ROOT=self.temporal.name, PRINT_SYNC=True, PRINT_BACKEND="archivo")
        self.ajustes.enable()
        self.sucursal = Sucursal.objects.get(clave="ARBOLEDAS")
        self.producto = Producto.objects.get(sucursal=self.sucursal, codigo="TB")

    def tearDown(self):
        self.ajustes.disable()
        self.temporal.cleanup()

    def test_catalogo_confirmado_del_menu(self):
        self.assertEqual(Producto.objects.filter(sucursal=self.sucursal, activo=True).count(), 23)
        self.assertEqual(self.producto.precio_actual().importe, Decimal("25.00"))
        self.assertEqual(Mesa.objects.filter(sucursal=self.sucursal, canal="comedor").count(), 24)

    def test_flujo_completo_genera_outbox_y_dos_formatos(self):
        mesa = Mesa.objects.get(sucursal=self.sucursal, clave="MESA-2")
        ticket, creado = abrir_ticket(mesa)
        self.assertTrue(creado)
        agregar_partida(ticket, self.producto, comensal=1, cantidad=Decimal("2"))
        agregar_partida(ticket, self.producto, comensal=2, cantidad=Decimal("1"))
        alternar_modificador(ticket, 1, "Ceb", "Cebolla")
        ticket.refresh_from_db()
        self.assertEqual(ticket.total, Decimal("75.00"))

        procesar_ticket(ticket)
        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/imprimir/",
            data=json.dumps({"formato": "comanda"}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        trabajo = TrabajoImpresion.objects.get(formato="comanda", ticket=ticket)
        self.assertEqual(trabajo.estado, TrabajoImpresion.Estado.GENERADO)
        self.assertTrue((Path(self.temporal.name) / trabajo.archivo).is_file())

        cobrar_ticket(ticket, Ticket.FormaPago.EFECTIVO, Decimal("100"))
        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/imprimir/",
            data=json.dumps({"formato": "cuenta"}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        cuenta = TrabajoImpresion.objects.get(formato="cuenta", ticket=ticket)
        self.assertTrue((Path(self.temporal.name) / cuenta.archivo).is_file())
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, Ticket.Estado.PAGADO)
        self.assertGreaterEqual(EventoOutbox.objects.filter(agregado_id=ticket.id).count(), 6)

    def test_apertura_disponible_en_los_tres_canales(self):
        for canal in [Mesa.Canal.COMEDOR, Mesa.Canal.DOMICILIO, Mesa.Canal.SUCURSALES]:
            mesa = Mesa.objects.filter(sucursal=self.sucursal, canal=canal).first()
            respuesta = self.client.post(
                "/api/tickets/abrir/",
                data=json.dumps({"mesa_id": str(mesa.id)}),
                content_type="application/json",
            )
            self.assertEqual(respuesta.status_code, 200)
            self.assertEqual(respuesta.json()["ticket"]["canal"], canal)

    def test_tabletas_solo_presenta_el_acceso_de_comedor(self):
        respuesta = self.client.get("/tabletas/")
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'data-modo-tableta="true"')
        self.assertNotContains(respuesta, 'data-canal="domicilio"')
        self.assertNotContains(respuesta, 'data-canal="sucursales"')

    def test_comensal_24_bebidas_y_preparacion_global(self):
        mesa = Mesa.objects.get(sucursal=self.sucursal, clave="MESA-20")
        bebida = Producto.objects.get(sucursal=self.sucursal, codigo="AM")
        ticket, _ = abrir_ticket(mesa)
        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/partidas/",
            data=json.dumps({"producto_id": str(bebida.id), "comensal": 24, "cantidad": 2}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["ticket"]["partidas"][0]["categoria"], "Bebidas")
        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/modificadores/",
            data=json.dumps({"comensales": [19, 20, 21, 22, 23, 24], "codigo": "C/T", "nombre": "Con todo"}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(ModificadorTicket.objects.filter(ticket=ticket, codigo="C/T").count(), 6)
        imagen = render_comanda(ticket, "cocina")
        self.assertGreater(imagen.height, 900)
        trabajos = encolar_impresiones(ticket, TrabajoImpresion.Formato.COMANDA)
        self.assertEqual(len(trabajos), 1)
        self.assertEqual(trabajos[0].destino, TrabajoImpresion.Destino.COCINA)

    def test_render_termico_es_raster_escpos(self):
        mesa = Mesa.objects.get(sucursal=self.sucursal, clave="MESA-3")
        ticket, _ = abrir_ticket(mesa)
        agregar_partida(ticket, self.producto, comensal=1)
        ticket.refresh_from_db()
        comanda = render_comanda(ticket, "cocina")
        cuenta = render_cuenta(ticket)
        self.assertEqual(comanda.width, 576)
        self.assertEqual(cuenta.width, 576)
        self.assertGreater(comanda.height, 600)
        self.assertGreater(cuenta.height, 600)
        self.assertLess(cuenta.height, 800)
        datos = escpos_raster(comanda)
        self.assertTrue(datos.startswith(b"\x1b@\x1dv0\x00"))

    def test_tcp_marca_impreso_solo_despues_de_enviar(self):
        mesa = Mesa.objects.get(sucursal=self.sucursal, clave="MESA-4")
        ticket, _ = abrir_ticket(mesa)
        agregar_partida(ticket, self.producto, comensal=1)
        with override_settings(PRINT_BACKEND="tcp"), patch("impresion.services.enviar_tcp") as enviar:
            trabajo = encolar_impresiones(ticket, TrabajoImpresion.Formato.COMANDA)[0]
        enviar.assert_called_once()
        trabajo.refresh_from_db()
        self.assertEqual(trabajo.estado, TrabajoImpresion.Estado.IMPRESO)

    def test_error_tcp_conserva_png_y_no_reporta_impreso(self):
        mesa = Mesa.objects.get(sucursal=self.sucursal, clave="MESA-5")
        ticket, _ = abrir_ticket(mesa)
        agregar_partida(ticket, self.producto, comensal=1)
        with override_settings(PRINT_BACKEND="tcp"), patch(
            "impresion.services.enviar_tcp", side_effect=OSError("impresora no disponible")
        ):
            trabajo = encolar_impresiones(ticket, TrabajoImpresion.Formato.COMANDA)[0]
        trabajo.refresh_from_db()
        self.assertEqual(trabajo.estado, TrabajoImpresion.Estado.ERROR)
        self.assertIn("impresora no disponible", trabajo.error)
        self.assertTrue((Path(self.temporal.name) / trabajo.archivo).is_file())
