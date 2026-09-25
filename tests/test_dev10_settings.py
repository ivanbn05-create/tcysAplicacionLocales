"""Validación aislada de configuración remota para la candidata dev.10."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


RAIZ = Path(__file__).resolve().parents[1]
TOKEN_PEDIDOS = "p" * 32
TOKEN_INGESTA = "i" * 32
TOKEN_CATALOGO = "c" * 32
BRANCH_ID = "11111111-1111-4111-8111-111111111111"
INSTANCE_ID = "22222222-2222-4222-8222-222222222222"


class SettingsRemotosDev10Tests(unittest.TestCase):
    def importar(self, *, extra=None, expresion="True"):
        prefixes = (
            "DJANGO_",
            "WAITRESS_",
            "POSTGRES_",
            "POS_",
            "PRINT_",
            "PRINTER_",
            "PEDIDOS_SUCURSALES_",
            "PEDIDOS_API_",
            "CENTRAL_",
            "VPS_CONSOLIDACION_",
            "THERMAL_",
        )
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(prefixes)
            and key not in {"DB_ENGINE", "SQLITE_PATH", "SUCURSAL_CLAVE"}
        }
        env.update(
            {
                "DJANGO_SECRET_KEY": "settings-dev10-" + "0123456789abcdef" * 4,
                "DJANGO_ALLOWED_HOSTS": "localhost,127.0.0.1",
                "WAITRESS_HOST": "127.0.0.1",
                "DJANGO_HTTPS": "false",
                "ALLOW_INSECURE_HTTP_LAN": "false",
                "DB_ENGINE": "sqlite",
                "SUCURSAL_CLAVE": "DEV10",
                "PRINT_BACKEND": "archivo",
            }
        )
        env.update(extra or {})
        code = (
            "import sys; "
            f"sys.path.insert(0, {str(RAIZ)!r}); "
            f"import pos.settings as s; print({expresion})"
        )
        return subprocess.run(
            [sys.executable, "-I", "-c", code],
            cwd=RAIZ,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def central_base(self):
        return {
            "CENTRAL_API_BASE_URL": "https://central.example.test",
            "CENTRAL_BRANCH_ID": BRANCH_ID,
            "CENTRAL_BRANCH_CODE": "dev10",
            "CENTRAL_POS_INSTANCE_ID": INSTANCE_ID,
        }

    def test_todos_los_flujos_remotos_inician_apagados(self):
        result = self.importar(
            expresion=(
                "(s.PEDIDOS_SUCURSALES_FUENTE, "
                "s.CENTRAL_ENABLE_SALES_V2, "
                "s.CENTRAL_ENABLE_CUSTOMERS_V2, "
                "s.CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2, "
                "s.CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V3, "
                "bool(s.PEDIDOS_API_TOKEN), bool(s.CENTRAL_INGEST_TOKEN), "
                "bool(s.CENTRAL_CATALOG_TOKEN))"
            )
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            "('desactivada', False, False, False, False, False, False, False)",
        )

    def test_pedidos_v2_exige_https_token_fuerte_e_ids_positivos(self):
        base = {
            "PEDIDOS_SUCURSALES_FUENTE": "api_v2",
            "PEDIDOS_API_BASE_URL": "https://pedidos.example.test",
            "PEDIDOS_API_TOKEN": TOKEN_PEDIDOS,
            "PEDIDOS_API_SUCURSAL_IDS": "17",
        }
        valid = self.importar(
            extra=base,
            expresion="(s.PEDIDOS_SUCURSALES_FUENTE, s.PEDIDOS_API_SUCURSAL_IDS)",
        )
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertEqual(valid.stdout.strip(), "('api_v2', (17,))")

        cases = (
            ({**base, "PEDIDOS_API_BASE_URL": "http://pedidos.example.test"}, "HTTPS"),
            ({**base, "PEDIDOS_API_TOKEN": "corto"}, "entre 32 y 512"),
            ({**base, "PEDIDOS_API_SUCURSAL_IDS": ""}, "exige PEDIDOS_API_BASE_URL"),
            ({**base, "PEDIDOS_API_SUCURSAL_IDS": "0"}, "enteros positivos"),
            ({**base, "PEDIDOS_API_ENDPOINT": "//otro.example/ruta"}, "ruta absoluta relativa"),
        )
        for env, expected in cases:
            with self.subTest(expected=expected):
                result = self.importar(extra=env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

    def test_central_exige_identidad_canonica_https_y_token_por_flujo(self):
        base = self.central_base()
        sales = self.importar(
            extra={
                **base,
                "CENTRAL_ENABLE_SALES_V2": "true",
                "CENTRAL_INGEST_TOKEN": TOKEN_INGESTA,
            },
            expresion="(s.CENTRAL_ENABLE_SALES_V2, bool(s.CENTRAL_CATALOG_TOKEN))",
        )
        self.assertEqual(sales.returncode, 0, sales.stderr)
        self.assertEqual(sales.stdout.strip(), "(True, False)")

        catalog = self.importar(
            extra={
                **base,
                "CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2": "true",
                "CENTRAL_CATALOG_TOKEN": TOKEN_CATALOGO,
            },
            expresion="(s.CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2, bool(s.CENTRAL_INGEST_TOKEN))",
        )
        self.assertEqual(catalog.returncode, 0, catalog.stderr)
        self.assertEqual(catalog.stdout.strip(), "(True, False)")
        catalog_v3 = self.importar(
            extra={
                **base,
                "CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V3": "true",
                "CENTRAL_CATALOG_TOKEN": TOKEN_CATALOGO,
            },
            expresion="(s.CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V3, s.CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2)",
        )
        self.assertEqual(catalog_v3.returncode, 0, catalog_v3.stderr)
        self.assertEqual(catalog_v3.stdout.strip(), "(True, False)")

        cases = (
            (
                {
                    **base,
                    "CENTRAL_API_BASE_URL": "http://central.example.test",
                    "CENTRAL_ENABLE_SALES_V2": "true",
                    "CENTRAL_INGEST_TOKEN": TOKEN_INGESTA,
                },
                "HTTPS",
            ),
            (
                {
                    **base,
                    "CENTRAL_BRANCH_ID": "00000000-0000-0000-0000-000000000000",
                    "CENTRAL_ENABLE_SALES_V2": "true",
                    "CENTRAL_INGEST_TOKEN": TOKEN_INGESTA,
                },
                "UUID canonico no nulo",
            ),
            (
                {
                    **base,
                    "CENTRAL_POS_INSTANCE_ID": "NO-UUID",
                    "CENTRAL_ENABLE_SALES_V2": "true",
                    "CENTRAL_INGEST_TOKEN": TOKEN_INGESTA,
                },
                "UUID canonico",
            ),
            (
                {
                    **base,
                    "CENTRAL_ENABLE_SALES_V2": "true",
                    "CENTRAL_INGEST_TOKEN": "corto",
                },
                "CENTRAL_INGEST_TOKEN debe tener",
            ),
            (
                {
                    **base,
                    "CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2": "true",
                    "CENTRAL_CATALOG_TOKEN": "",
                },
                "CENTRAL_CATALOG_TOKEN independiente",
            ),
            (
                {
                    **base,
                    "CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V3": "true",
                    "CENTRAL_CATALOG_TOKEN": "",
                },
                "CENTRAL_CATALOG_TOKEN independiente",
            ),
        )
        for env, expected in cases:
            with self.subTest(expected=expected):
                result = self.importar(extra=env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

    def test_cada_flujo_remoto_rechaza_tokens_reutilizados(self):
        casos = (
            {
                "PEDIDOS_API_TOKEN": TOKEN_PEDIDOS,
                "CENTRAL_INGEST_TOKEN": TOKEN_PEDIDOS,
            },
            {
                "CENTRAL_INGEST_TOKEN": TOKEN_INGESTA,
                "CENTRAL_CATALOG_TOKEN": TOKEN_INGESTA,
            },
            {
                "CENTRAL_CATALOG_TOKEN": TOKEN_CATALOGO,
                "VPS_CONSOLIDACION_URL": "https://central.example.test/api/v1/edge/consolidaciones-mensuales/",
                "VPS_CONSOLIDACION_TOKEN": TOKEN_CATALOGO,
            },
        )
        for extra in casos:
            with self.subTest(variables=tuple(extra)):
                result = self.importar(extra=extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Cada flujo remoto debe usar una credencial distinta", result.stderr)
    def test_bundles_ca_configurados_deben_existir(self):
        for variable in ("PEDIDOS_API_CA_BUNDLE", "CENTRAL_API_CA_BUNDLE"):
            result = self.importar(extra={variable: "C:/ruta/ca-inexistente.pem"})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("debe apuntar a un archivo existente", result.stderr)

        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "ca.pem"
            bundle.write_text("fixture sin uso de red", encoding="utf-8")
            result = self.importar(
                extra={"CENTRAL_API_CA_BUNDLE": str(bundle)},
                expresion="bool(s.CENTRAL_API_CA_BUNDLE)",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "True")

    def test_codigo_de_clientes_no_contiene_bypass_tls(self):
        files = (
            RAIZ / "ventas" / "pedidos_api_v2.py",
            RAIZ / "ventas" / "central_api.py",
            RAIZ / "ventas" / "sincronizacion_central.py",
            RAIZ / "ventas" / "catalogo_central.py",
        )
        forbidden = ("verify=False", "CERT_NONE", "_create_unverified_context")
        for path in files:
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, text, f"{path.name} contiene {token}")


if __name__ == "__main__":
    unittest.main()
