from pathlib import Path
import os
import re
import subprocess
import sys
import unittest

from herramientas.release_servidor import _validar_lock_exacto


RAIZ = Path(__file__).resolve().parents[1]


class ContratosDespliegueTests(unittest.TestCase):
    def test_validacion_aislada_incluye_version_de_release(self):
        validador = (
            RAIZ / "herramientas" / "validar_despliegue.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'shutil.copyfile(source / "VERSION", snapshot / "VERSION")',
            validador,
        )

    def test_bootstrap_escritorio_propaga_el_resultado_del_instalador(self):
        bootstrap = (RAIZ / "desktop" / "InstallerBootstrap.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn("private static int Main()", bootstrap)
        self.assertIn("installer.WaitForExit();", bootstrap)
        self.assertIn("if (installer.ExitCode != 0)", bootstrap)
        self.assertIn("return installer.ExitCode;", bootstrap)
        self.assertIn("return 0;", bootstrap)
        self.assertIn("return 1;", bootstrap)

    def test_version_de_escritorio_es_coherente_y_el_registro_usa_el_ejecutable(self):
        bootstrap = (RAIZ / "desktop" / "InstallerBootstrap.cs").read_text(
            encoding="utf-8"
        )
        cliente = (RAIZ / "desktop" / "TocayosPOS.cs").read_text(encoding="utf-8")
        patron_version = re.compile(r'AssemblyVersion\("([^"]+)"\)')
        self.assertEqual(patron_version.findall(bootstrap), ["0.3.0.0"])
        self.assertEqual(patron_version.findall(cliente), ["0.3.0.0"])

        instalador = (
            RAIZ / "desktop" / "package" / "Instalar-LosTocayosPOS.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "$displayVersion = [Diagnostics.FileVersionInfo]::GetVersionInfo("
            "$ejecutableDestino).FileVersion",
            instalador,
        )
        self.assertIn(
            "Set-ItemProperty -Path $registro -Name DisplayVersion -Value "
            "$displayVersion",
            instalador,
        )
        self.assertNotIn('-Name DisplayVersion -Value "0.3.0.0"', instalador)

    def test_integracion_escritorio_usa_windows_powershell_del_sistema(self):
        instalador = (
            RAIZ / "desktop" / "package" / "Instalar-LosTocayosPOS.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "$systemDirectory = "
            "[Environment]::GetFolderPath([Environment+SpecialFolder]::System)",
            instalador,
        )
        self.assertIn(
            '$powershell = Join-Path $systemDirectory '
            '"WindowsPowerShell\\v1.0\\powershell.exe"',
            instalador,
        )
        self.assertIn("-Target $powershell", instalador)
        self.assertIn("'\"' + $powershell + '\" -NoProfile", instalador)

    def test_lock_es_exacto_y_activa_pywin32_en_el_target(self):
        lock = RAIZ / "requirements-lock.txt"
        pins = _validar_lock_exacto(lock)
        self.assertIn("pywin32==311", pins)
        self.assertTrue(all("==" in pin for pin in pins))
        self.assertIn(
            'pywin32==311; sys_platform == "win32"',
            lock.read_text(encoding="utf-8").splitlines(),
        )

    def test_entrypoint_exige_identidad_y_semilla_explicita(self):
        bytes_entrypoint = (RAIZ / "entrypoint.sh").read_bytes()
        self.assertNotIn(b"\r\n", bytes_entrypoint)
        texto = bytes_entrypoint.decode("utf-8")
        self.assertIn('${SUCURSAL_CLAVE:?SUCURSAL_CLAVE es obligatoria}', texto)
        self.assertIn('${SUCURSAL_NOMBRE:?SUCURSAL_NOMBRE es obligatoria}', texto)
        self.assertIn("python manage.py \"$@\"", texto)
        self.assertIn("--sucursal-id", texto)
        self.assertLess(
            texto.index("python manage.py verificar_identidad_local"),
            texto.index("python manage.py migrate --noinput"),
        )
        self.assertNotIn("cargar_datos_iniciales", texto)
        self.assertNotIn("INICIALIZAR_DATOS_ARBOLEDAS", texto)
        self.assertIn("*.sh text eol=lf", (RAIZ / ".gitattributes").read_text("utf-8"))

    def test_imagen_usa_lock_y_excluye_estado_local(self):
        dockerfile = (RAIZ / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("pip install --no-cache-dir -r requirements-lock.txt", dockerfile)
        self.assertNotIn("pip install --no-cache-dir -r requirements.txt\n", dockerfile)

        exclusiones = (RAIZ / ".dockerignore").read_text(encoding="utf-8").splitlines()
        for obligatoria in (".git", ".venv", ".env", "runtime/", "backups/", "logs/", "media/"):
            self.assertIn(obligatoria, exclusiones)
        self.assertIn("!certs/prod-ca-2021.crt", exclusiones)

    def test_compose_exige_sucursal_y_worker_espera_salud(self):
        texto = (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("SUCURSAL_CLAVE: ${SUCURSAL_CLAVE:?", texto)
        self.assertIn("SUCURSAL_NOMBRE: ${SUCURSAL_NOMBRE:?", texto)
        self.assertIn("condition: service_healthy", texto)
        self.assertIn("urllib.request.urlopen", texto)
        self.assertEqual(
            texto.count("- ${TOCAYOS_DOCKER_ENV_FILE:-.env.docker}"), 2
        )
        self.assertNotIn("      - .env\n", texto)

    def test_plantilla_no_apunta_a_impresoras_reales(self):
        lineas = (RAIZ / ".env.example").read_text(encoding="utf-8").splitlines()
        self.assertIn("PRINT_BACKEND=archivo", lineas)
        for nombre in ("PRINTER_CAJA_HOST", "PRINTER_COCINA_HOST", "PRINTER_BARRA_HOST"):
            self.assertIn(f"{nombre}=", lineas)
        self.assertIn("VPS_CONSOLIDACION_URL=", lineas)
        self.assertIn("VPS_CONSOLIDACION_TOKEN=", lineas)
        self.assertIn("VPS_CONSOLIDACION_TIMEOUT=10", lineas)

    def _importar_settings(
        self,
        clave,
        *,
        db_engine="sqlite",
        expresion="s.SUCURSAL_CLAVE",
        extra_env=None,
    ):
        entorno = {
            nombre: valor
            for nombre, valor in os.environ.items()
            if not nombre.startswith(
                (
                    "DJANGO_", "WAITRESS_", "POSTGRES_", "POS_", "PRINT_",
                    "PRINTER_", "PEDIDOS_SUCURSALES_", "VPS_CONSOLIDACION_",
                    "THERMAL_",
                )
            )
            and nombre not in {"DB_ENGINE", "SQLITE_PATH", "SUCURSAL_CLAVE"}
        }
        entorno.update(
            {
                "DJANGO_SECRET_KEY": "prueba-segura-" + "0123456789abcdef" * 4,
                "DJANGO_ALLOWED_HOSTS": "localhost,127.0.0.1",
                "WAITRESS_HOST": "127.0.0.1",
                "DJANGO_HTTPS": "false",
                "ALLOW_INSECURE_HTTP_LAN": "false",
                "DB_ENGINE": db_engine,
                "SUCURSAL_CLAVE": clave,
                "PRINT_BACKEND": "archivo",
            }
        )
        if extra_env:
            entorno.update(extra_env)
        codigo = (
            "import sys; "
            f"sys.path.insert(0, {str(RAIZ)!r}); "
            f"import pos.settings as s; print({expresion})"
        )
        return subprocess.run(
            [sys.executable, "-I", "-c", codigo],
            env=entorno,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_settings_falla_sin_identidad_y_normaliza_clave(self):
        ausente = self._importar_settings("")
        self.assertNotEqual(ausente.returncode, 0)
        self.assertIn("SUCURSAL_CLAVE es obligatoria", ausente.stderr)

        invalida = self._importar_settings("CON ESPACIOS")
        self.assertNotEqual(invalida.returncode, 0)
        self.assertIn("SUCURSAL_CLAVE no es válida", invalida.stderr)

        valida = self._importar_settings("norte_2")
        self.assertEqual(valida.returncode, 0, valida.stderr)
        self.assertEqual(valida.stdout.strip(), "NORTE_2")

    def test_settings_valida_db_engine_y_trata_vacio_como_sqlite(self):
        invalido = self._importar_settings("NORTE", db_engine="oracle")
        self.assertNotEqual(invalido.returncode, 0)
        self.assertIn("DB_ENGINE debe ser 'sqlite' o 'postgres'", invalido.stderr)

        heredado = self._importar_settings(
            "NORTE",
            db_engine="",
            expresion="s.DATABASES['default']['ENGINE']",
        )
        self.assertEqual(heredado.returncode, 0, heredado.stderr)
        self.assertEqual(heredado.stdout.strip(), "django.db.backends.sqlite3")

    def test_settings_valida_consolidacion_vps_opcional(self):
        desactivada = self._importar_settings(
            "NORTE",
            expresion="(bool(s.VPS_CONSOLIDACION_URL), bool(s.VPS_CONSOLIDACION_TOKEN), s.VPS_CONSOLIDACION_TIMEOUT)",
        )
        self.assertEqual(desactivada.returncode, 0, desactivada.stderr)
        self.assertEqual(desactivada.stdout.strip(), "(False, False, 10)")

        valida = self._importar_settings(
            "NORTE",
            expresion="(bool(s.VPS_CONSOLIDACION_URL), bool(s.VPS_CONSOLIDACION_TOKEN), s.VPS_CONSOLIDACION_TIMEOUT)",
            extra_env={
                "VPS_CONSOLIDACION_URL": "https://vps.example/api/consolidaciones",
                "VPS_CONSOLIDACION_TOKEN": "token-de-prueba",
                "VPS_CONSOLIDACION_TIMEOUT": "60",
            },
        )
        self.assertEqual(valida.returncode, 0, valida.stderr)
        self.assertEqual(valida.stdout.strip(), "(True, True, 60)")

        casos_invalidos = (
            (
                {"VPS_CONSOLIDACION_URL": "https://vps.example/api", "VPS_CONSOLIDACION_TOKEN": ""},
                "deben configurarse juntos",
            ),
            (
                {"VPS_CONSOLIDACION_URL": "", "VPS_CONSOLIDACION_TOKEN": "token-de-prueba"},
                "deben configurarse juntos",
            ),
            (
                {"VPS_CONSOLIDACION_URL": "http://vps.example/api", "VPS_CONSOLIDACION_TOKEN": "token-de-prueba"},
                "URL HTTPS absoluta",
            ),
            (
                {"VPS_CONSOLIDACION_URL": "/api/consolidaciones", "VPS_CONSOLIDACION_TOKEN": "token-de-prueba"},
                "URL HTTPS absoluta",
            ),
            (
                {"VPS_CONSOLIDACION_URL": "https://usuario:secreto@vps.example/api", "VPS_CONSOLIDACION_TOKEN": "token-de-prueba"},
                "sin credenciales embebidas",
            ),
            (
                {"VPS_CONSOLIDACION_TIMEOUT": "0"},
                "entero entre 1 y 60",
            ),
            (
                {"VPS_CONSOLIDACION_TIMEOUT": "61"},
                "entero entre 1 y 60",
            ),
            (
                {"VPS_CONSOLIDACION_TIMEOUT": "1.5"},
                "entero entre 1 y 60",
            ),
        )
        for variables, mensaje in casos_invalidos:
            with self.subTest(variables=tuple(sorted(variables))):
                resultado = self._importar_settings("NORTE", extra_env=variables)
                self.assertNotEqual(resultado.returncode, 0)
                self.assertIn(mensaje, resultado.stderr)


if __name__ == "__main__":
    unittest.main()
