"""Integración de venta real contra un catálogo v3 sintético y aislado."""

import copy
import json
import uuid
from decimal import Decimal
from pathlib import Path

from django.contrib.auth.hashers import make_password
from django.test import TestCase, override_settings

from catalogo.models import IdentidadProductoCentral
from personas.models import Sucursal
from ventas.aprovisionamiento import marcar_esperando_catalogo, marcar_listo
from ventas.catalogo_central import aplicar_publicacion_catalogo, checksum_snapshot
from ventas.models import ConfiguracionSucursal, Mesa, Partida, Ticket
from ventas.services import (
    ErrorVenta,
    abrir_ticket,
    agregar_partida,
    ajustar_grupo_partidas,
    actualizar_partida,
    cobrar_ticket,
    procesar_ticket,
)
from ventas.views import _ticket_payload


FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "contracts/edge-central/fixtures/catalogo-publicacion-v3-lab01-promocion.json"
)


class VentaConCatalogoPublicacionTests(TestCase):
    def setUp(self):
        self.snapshot = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.sucursal = Sucursal.objects.create(
            id=uuid.UUID(self.snapshot["sucursal"]["id"]),
            clave=self.snapshot["sucursal"]["clave"],
            nombre="Laboratorio 01",
        )
        self.configuracion = ConfiguracionSucursal.objects.create(
            sucursal=self.sucursal,
            clave_administrador=make_password("7391"),
        )
        self.mesa = Mesa.objects.create(
            sucursal=self.sucursal,
            clave="M1",
            nombre="Mesa 1",
            canal=Mesa.Canal.COMEDOR,
        )
        self.ajustes = override_settings(
            SUCURSAL_CLAVE=self.sucursal.clave,
            CENTRAL_BRANCH_ID=str(self.sucursal.id),
            CENTRAL_POS_INSTANCE_ID=str(self.configuracion.instalacion_id),
        )
        self.ajustes.enable()
        self.addCleanup(self.ajustes.disable)
        marcar_esperando_catalogo(self.sucursal)

    def publicar(self, snapshot=None):
        return aplicar_publicacion_catalogo(self.sucursal, snapshot or self.snapshot)[0]

    def producto(self, codigo):
        return IdentidadProductoCentral.objects.get(
            sucursal=self.sucursal,
            producto__codigo=codigo,
        ).producto

    def test_catalogo_pendiente_bloquea_apertura_y_api_directa_rechaza_no_disponible(self):
        with self.assertRaisesMessage(ErrorVenta, "esperando catálogo"):
            abrir_ticket(self.mesa, atendio=None)
        self.assertEqual(Ticket.objects.count(), 0)
        self.publicar()
        with self.assertRaisesMessage(ErrorVenta, "verificaciones operativas"):
            abrir_ticket(self.mesa, atendio=None)

        marcar_listo(self.sucursal)
        respuesta = self.client.get("/")
        self.assertEqual(respuesta.status_code, 200)
        codigos = {p["codigo"] for p in respuesta.context["productos"]}
        self.assertEqual(codigos, {"LAB-PROMO", "LAB-TACO"})
        estado = self.client.get("/api/estado/").json()["aprovisionamiento"]
        self.assertEqual(estado["estado"], "listo")

        ticket, creado = abrir_ticket(self.mesa, atendio=None)
        self.assertTrue(creado)
        fuera = self.producto("LAB-FUERA")
        with self.assertRaisesMessage(ErrorVenta, "no está disponible"):
            agregar_partida(ticket, fuera)
        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/partidas/",
            data=json.dumps({"producto_id": str(fuera.id), "cantidad": 1}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(Partida.objects.filter(ticket=ticket).count(), 0)

    def test_promocion_manual_y_ticket_historico_sobreviven_nueva_publicacion(self):
        primera = self.publicar()
        marcar_listo(self.sucursal)
        promo = self.producto("LAB-PROMO")
        taco = self.producto("LAB-TACO")
        ticket, _ = abrir_ticket(self.mesa, atendio=None)

        # Una línea normal de taco no se consume por la promoción elegida después.
        independiente = agregar_partida(ticket, taco)
        raiz = agregar_partida(ticket, promo)
        self.assertIsNone(independiente.promocion_aplicada_id)
        self.assertEqual(independiente.precio_unitario, Decimal("25.00"))
        with self.assertRaisesMessage(ErrorVenta, "Completa la promoción"):
            procesar_ticket(ticket)
        grupo = raiz.promocion_definicion.grupos.get()
        componente = agregar_partida(
            ticket, taco, cantidad=2,
            promocion_aplicada=raiz, promocion_grupo_id=grupo.id,
        )
        self.assertEqual(componente.precio_lista_capturado, Decimal("25.00"))
        self.assertEqual(componente.precio_unitario, Decimal("0.00"))
        self.assertEqual(ticket.total, Decimal("125.00"))

        siguiente = copy.deepcopy(self.snapshot)
        siguiente["release_id"] = str(uuid.uuid4())
        siguiente["publicacion_id"] = str(uuid.uuid4())
        siguiente["publicacion_anterior_id"] = str(primera.publicacion_id)
        siguiente["version_sucursal"] = 2
        siguiente["contenido"]["productos"][0]["nombre"] = "Promoción renombrada"
        siguiente["contenido"]["productos"][0]["precio"]["importe"] = "120.00"
        siguiente["contenido"]["promociones"][0]["precio"] = "120.00"
        siguiente["contenido"]["promociones"][0]["grupos"][0]["cantidad"] = 1
        siguiente["contenido_sha256"] = checksum_snapshot(siguiente)
        self.publicar(siguiente)

        raiz.refresh_from_db()
        ticket.refresh_from_db()
        payload = _ticket_payload(ticket)
        raiz_payload = next(item for item in payload["partidas"] if item["id"] == str(raiz.id))
        self.assertEqual(raiz_payload["precio"], "100.00")
        self.assertEqual(raiz_payload["nombre"], "Promoción sintética")
        self.assertEqual(raiz_payload["grupos"][0]["cantidad"], 2)
        self.assertEqual(raiz_payload["grupos"][0]["seleccionada"], 2)
        procesar_ticket(ticket)
        cobrar_ticket(ticket, Ticket.FormaPago.EFECTIVO, Decimal("125.00"))
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, Ticket.Estado.PAGADO)
        self.assertEqual(ticket.total, Decimal("125.00"))

        nuevo, _ = abrir_ticket(self.mesa, atendio=None)
        nueva_raiz = agregar_partida(nuevo, promo)
        self.assertEqual(nueva_raiz.precio_unitario, Decimal("120.00"))
        self.assertEqual(nueva_raiz.promocion_definicion.grupos.get().cantidad, 1)
        self.assertEqual(raiz.precio_unitario, Decimal("100.00"))

        actualizar_partida(nueva_raiz, 2)
        nuevo_payload = _ticket_payload(nuevo)
        nueva_fila = next(
            item for item in nuevo_payload["partidas"] if item["id"] == str(nueva_raiz.id)
        )
        self.assertEqual(nueva_fila["grupos"][0]["cantidad"], 2)

    def test_dos_promociones_independientes_no_reutilizan_componentes(self):
        self.publicar()
        marcar_listo(self.sucursal)
        promo = self.producto("LAB-PROMO")
        taco = self.producto("LAB-TACO")
        ticket, _ = abrir_ticket(self.mesa, atendio=None)
        primera = agregar_partida(ticket, promo)
        segunda = agregar_partida(ticket, promo)
        grupo = primera.promocion_definicion.grupos.get()
        componente_primero = agregar_partida(
            ticket, taco, cantidad=2,
            promocion_aplicada=primera, promocion_grupo_id=grupo.id,
        )
        with self.assertRaisesMessage(ErrorVenta, "Completa la promoción"):
            procesar_ticket(ticket)
        componente_segundo = agregar_partida(
            ticket, taco, cantidad=2,
            promocion_aplicada=segunda, promocion_grupo_id=grupo.id,
        )
        self.assertNotEqual(
            componente_primero.promocion_aplicada_id,
            componente_segundo.promocion_aplicada_id,
        )
        with self.assertRaisesMessage(ErrorVenta, "independiente"):
            ajustar_grupo_partidas(
                ticket, [componente_primero.id, componente_segundo.id], 1,
            )
        self.assertEqual(ticket.total, Decimal("200.00"))
        procesar_ticket(ticket)
