from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from impresion.models import ConfiguracionImpresionTerminal, TrabajoImpresion
from impresion.services import encolar_impresiones, procesar_trabajo, resolver_ruta_impresion
from personas.models import Sucursal
from ventas.models import Mesa, Ticket


@override_settings(
    PRINT_SYNC=False,
    PRINT_BACKEND="archivo",
    PRINTER_HOSTS={
        "caja": "192.0.2.10",
        "cocina": "192.0.2.11",
        "barra": "192.0.2.12",
    },
    PRINTER_PORT=9100,
)
class EnrutamientoImpresionTerminalTests(TestCase):
    def setUp(self):
        self.sucursal = Sucursal.objects.create(
            clave="DEV10-PRINT",
            nombre="Pruebas impresión dev.10",
        )
        self.mesa = Mesa.objects.create(
            sucursal=self.sucursal,
            canal=Mesa.Canal.LLEVAR,
            clave="LLEVAR-PRINT",
            nombre="Llevar impresión",
        )
        self.ticket = Ticket.objects.create(
            sucursal=self.sucursal,
            mesa=self.mesa,
            folio=1,
            canal=Mesa.Canal.LLEVAR,
            estado=Ticket.Estado.COBRAR,
            descuento_porcentaje=Decimal("0.00"),
        )
        self.configuracion = ConfiguracionImpresionTerminal.objects.create(
            sucursal=self.sucursal,
            device_id="tablet-caja-01",
            nombre="Tablet caja",
            host_caja="198.51.100.20",
            host_cocina="198.51.100.21",
            host_barra="198.51.100.22",
            puerto=9200,
        )

    def test_encolado_conserva_snapshot_aunque_cambie_la_terminal(self):
        trabajo = encolar_impresiones(
            self.ticket,
            TrabajoImpresion.Formato.CUENTA,
            device_id="tablet-caja-01",
        )[0]
        self.assertEqual(trabajo.device_id, "tablet-caja-01")
        self.assertEqual(str(trabajo.printer_host), "198.51.100.20")
        self.assertEqual(trabajo.printer_port, 9200)
        self.assertEqual(trabajo.origen_ruta, "terminal")

        self.configuracion.host_caja = "203.0.113.55"
        self.configuracion.puerto = 9300
        self.configuracion.save(update_fields=["host_caja", "puerto", "actualizado_en"])

        with (
            override_settings(PRINT_BACKEND="tcp"),
            patch("impresion.services.render_cuenta", return_value=object()),
            patch(
                "impresion.services.guardar_png",
                return_value=("pruebas/cuenta.png", None),
            ),
            patch("impresion.services.enviar_tcp") as enviar_tcp,
        ):
            procesar_trabajo(trabajo)

        enviar_tcp.assert_called_once()
        self.assertEqual(enviar_tcp.call_args.kwargs["host"], "198.51.100.20")
        self.assertEqual(enviar_tcp.call_args.kwargs["puerto"], 9200)
        trabajo.refresh_from_db()
        self.assertEqual(trabajo.estado, TrabajoImpresion.Estado.IMPRESO)
        self.assertEqual(str(trabajo.printer_host), "198.51.100.20")
        self.assertEqual(trabajo.printer_port, 9200)

    def test_terminal_desconocida_usa_ruta_legacy_sin_inventar_destino(self):
        ruta = resolver_ruta_impresion(
            self.sucursal,
            "terminal-no-registrada",
            TrabajoImpresion.Destino.CAJA,
        )

        self.assertEqual(ruta["device_id"], "terminal-no-registrada")
        self.assertEqual(ruta["printer_host"], "192.0.2.10")
        self.assertEqual(ruta["printer_port"], 9100)
        self.assertEqual(ruta["origen_ruta"], "legacy_env")
