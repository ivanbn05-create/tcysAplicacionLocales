import json
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from impresion.services import estado_impresora, sondear_impresora


HOSTS = {
    "caja": "192.0.2.10",
    "cocina": "192.0.2.11",
    "barra": "192.0.2.12",
}


class DiagnosticoImpresorasTests(SimpleTestCase):
    @override_settings(PRINT_BACKEND="archivo", PRINTER_HOSTS=HOSTS)
    @patch("impresion.services.socket.create_connection")
    def test_estado_archivo_explica_que_la_impresion_fisica_esta_desactivada(
        self, conectar
    ):
        estado = estado_impresora("caja")

        conectar.assert_not_called()
        self.assertFalse(estado["disponible"])
        self.assertEqual(estado["codigo"], "impresion_fisica_desactivada")
        self.assertIn("PRINT_BACKEND=archivo", estado["mensaje"])

    @override_settings(
        PRINTER_HOSTS=HOSTS,
        PRINTER_PORT=9100,
        PRINTER_TIMEOUT=2,
    )
    @patch("impresion.services.socket.create_connection")
    def test_sondeo_abre_el_socket_sin_enviar_datos(self, conectar):
        conexion = MagicMock()
        conectar.return_value.__enter__.return_value = conexion

        resultado = sondear_impresora("cocina")

        conectar.assert_called_once_with(("192.0.2.11", 9100), timeout=2)
        conexion.sendall.assert_not_called()
        self.assertTrue(resultado["alcanzable"])

    @override_settings(
        PRINT_BACKEND="archivo",
        PRINTER_HOSTS=HOSTS,
        PRINTER_PORT=9100,
        PRINTER_TIMEOUT=2,
    )
    @patch("impresion.services.socket.create_connection")
    def test_comando_json_distingue_conectividad_de_backend_activo(self, conectar):
        salida = StringIO()

        call_command("diagnosticar_impresoras", "--json", stdout=salida)

        resultado = json.loads(salida.getvalue())
        self.assertFalse(resultado["impresion_fisica_activa"])
        self.assertFalse(resultado["envio_de_datos"])
        self.assertEqual(len(resultado["destinos"]), 3)
        self.assertTrue(
            all(item["alcanzable"] for item in resultado["destinos"])
        )

    @override_settings(
        PRINT_BACKEND="archivo",
        PRINTER_HOSTS=HOSTS,
        PRINTER_PORT=9100,
        PRINTER_TIMEOUT=2,
    )
    @patch("impresion.services.socket.create_connection")
    def test_exigir_tcp_falla_aunque_el_host_responda_si_el_backend_es_archivo(
        self, conectar
    ):
        with self.assertRaisesMessage(CommandError, "PRINT_BACKEND no es tcp"):
            call_command(
                "diagnosticar_impresoras",
                "--exigir-tcp",
                stdout=StringIO(),
            )

    @override_settings(
        PRINT_BACKEND="tcp",
        PRINTER_HOSTS=HOSTS,
        PRINTER_PORT=9100,
        PRINTER_TIMEOUT=2,
    )
    @patch("impresion.services.socket.create_connection")
    def test_exigir_tcp_aprueba_todos_los_destinos_alcanzables(self, conectar):
        call_command(
            "diagnosticar_impresoras",
            "--exigir-tcp",
            stdout=StringIO(),
        )

        self.assertEqual(conectar.call_count, 3)

    @override_settings(
        PRINT_BACKEND="tcp",
        PRINTER_HOSTS=HOSTS,
        PRINTER_PORT=9100,
        PRINTER_TIMEOUT=2,
    )
    @patch(
        "impresion.services.socket.create_connection",
        side_effect=OSError("detalle de red reservado"),
    )
    def test_exigir_tcp_falla_si_un_destino_no_responde(self, conectar):
        with self.assertLogs("impresion.services", level="WARNING"), self.assertRaisesMessage(
            CommandError, "caja, cocina, barra"
        ):
            call_command(
                "diagnosticar_impresoras",
                "--exigir-tcp",
                stdout=StringIO(),
            )

    @override_settings(
        PRINTER_HOSTS={"caja": "", "cocina": "", "barra": ""},
    )
    @patch("impresion.services.socket.create_connection")
    def test_sondeo_sin_host_no_intenta_abrir_socket(self, conectar):
        resultado = sondear_impresora("caja")

        conectar.assert_not_called()
        self.assertFalse(resultado["configurada"])
        self.assertFalse(resultado["alcanzable"])