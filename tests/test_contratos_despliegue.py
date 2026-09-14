from pathlib import Path
import os
import re
import subprocess
import sys
import unittest

from herramientas.release_servidor import _validar_lock_exacto


RAIZ = Path(__file__).resolve().parents[1]


class ContratosDespliegueTests(unittest.TestCase):
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

    def _importar_settings(self, clave, *, db_engine="sqlite", expresion="s.SUCURSAL_CLAVE"):
        entorno = {
            nombre: valor
            for nombre, valor in os.environ.items()
            if not nombre.startswith(
                (
                    "DJANGO_", "WAITRESS_", "POSTGRES_", "POS_", "PRINT_",
                    "PRINTER_", "PEDIDOS_SUCURSALES_", "THERMAL_",
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


if __name__ == "__main__":
    unittest.main()
