import json

from django.core.management import call_command
from django.test import TestCase, override_settings

from catalogo.models import Producto
from personas.models import Sucursal

from .models import Mesa, Ticket
from .services import (
    ErrorVenta,
    abrir_ticket,
    agregar_partida,
    validar_captura_por_nombres,
)


@override_settings(
    SUCURSAL_CLAVE="ARBOLEDAS",
    POS_REQUIRE_AUTH=False,
    PEDIDOS_SUCURSALES_AUTO_SYNC=False,
)
class ConcurrenciaYComplementosDev9Tests(TestCase):
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

    def test_respuesta_stale_incluye_codigo_estable_y_no_sobrescribe(self):
        mesa = Mesa.objects.get(sucursal=self.sucursal, clave="MESA-6")
        ticket, creado = abrir_ticket(mesa)
        self.assertTrue(creado)
        payload = {
            "id": str(ticket.id),
            "version_entidad": ticket.version_entidad,
        }
        ticket.comentario_general = "Servidor vigente"
        ticket.version_entidad += 1
        ticket.save(update_fields=["comentario_general", "version_entidad"])

        respuesta = self.client.patch(
            f"/api/tickets/{ticket.id}/",
            data=json.dumps(
                {
                    "comentario_general": "Dato atrasado",
                    "device_id": "caja-dev9",
                    "version_entidad": payload["version_entidad"],
                }
            ),
            content_type="application/json",
            HTTP_X_POS_DEVICE_ID="caja-dev9",
        )

        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(
            respuesta.json()["codigo"],
            "version_entidad_desactualizada",
        )
        self.assertEqual(respuesta.json()["ticket"]["comentario_general"], "Servidor vigente")
        ticket.refresh_from_db()
        self.assertEqual(ticket.comentario_general, "Servidor vigente")

    def test_bebidas_y_consomes_globales_no_exigen_nombre_pero_barbacoa_si(self):
        mesa = Mesa.objects.get(sucursal=self.sucursal, clave="MESA-7")
        ticket, _ = abrir_ticket(mesa)
        ticket.captura_por_nombres = True
        ticket.nombres_comensales = {}
        ticket.save(update_fields=["captura_por_nombres", "nombres_comensales"])

        agregar_partida(
            ticket,
            Producto.objects.get(sucursal=self.sucursal, codigo="CO05"),
            comensal=1,
        )
        agregar_partida(
            ticket,
            Producto.objects.get(sucursal=self.sucursal, codigo="CC"),
            comensal=2,
        )
        validar_captura_por_nombres(ticket)

        agregar_partida(
            ticket,
            Producto.objects.get(sucursal=self.sucursal, codigo="BBQ05"),
            comensal=3,
        )
        with self.assertRaisesRegex(ErrorVenta, "persona.*3"):
            validar_captura_por_nombres(ticket)
