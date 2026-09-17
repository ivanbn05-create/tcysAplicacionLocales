import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


RAIZ = Path(__file__).resolve().parents[1]


class _ServiceFramework:
    def __init__(self, args):
        self.args = args


def _cargar_modulo_servicio():
    dependencias_win32 = {
        "servicemanager": SimpleNamespace(
            LogInfoMsg=lambda *args: None,
            LogErrorMsg=lambda *args: None,
            SetEventSourceName=lambda *args: None,
        ),
        "win32event": SimpleNamespace(),
        "win32service": SimpleNamespace(SERVICE_STOP_PENDING=3),
        "win32serviceutil": SimpleNamespace(
            ServiceFramework=_ServiceFramework,
            HandleCommandLine=lambda *args: None,
        ),
    }
    especificacion = importlib.util.spec_from_file_location(
        "_servicio_windows_bajo_prueba", RAIZ / "servicio_windows.py"
    )
    modulo = importlib.util.module_from_spec(especificacion)
    with patch.dict(sys.modules, dependencias_win32):
        especificacion.loader.exec_module(modulo)
    return modulo


class EntornoServicioWindowsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.servicio = _cargar_modulo_servicio()

    def test_env_prevalece_y_los_escapes_inseguros_no_pueden_activarse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text(
                "\n".join(
                    (
                        "DJANGO_SECRET_KEY=desde-archivo",
                        "DB_ENGINE=sqlite",
                        "db_engine=postgres",
                        "SQLITE_PATH=runtime/correcta.sqlite3",
                        "SUCURSAL_CLAVE=NORTE",
                        "sucursal_clave=OTRA",
                        "PRINT_BACKEND=archivo",
                        "print_backend=tcp",
                        "PRINTER_CAJA_HOST=10.0.0.20",
                        "VPS_CONSOLIDACION_URL=https://vps.local/api/consolidaciones",
                        "VPS_CONSOLIDACION_TOKEN=desde-archivo",
                        "VPS_CONSOLIDACION_TIMEOUT=12",
                        "DJANGO_SETTINGS_MODULE=pos.settings_development",
                        "DJANGO_ALLOW_INSECURE_DEVELOPMENT=true",
                        "DJANGO_ALLOW_INSECURE_TEST_SETTINGS=true",
                        "PYTHONPATH=inyectado-desde-env",
                        "NO_RELACIONADA=desde-env-no-permitido",
                    )
                ),
                encoding="utf-8",
            )
            heredado = {
                "PATH": "ruta-del-sistema",
                "DJANGO_SECRET_KEY": "heredado",
                "DJANGO_SETTINGS_MODULE": "pos.settings_test",
                "DB_ENGINE": "postgres",
                "DB_REPLICA_HOST": "host-ajeno",
                "SQLITE_PATH": "otra.sqlite3",
                "POSTGRES_PASSWORD": "secreto-ajeno",
                "SUCURSAL_CLAVE": "OTRA",
                "SUCURSAL_NOMBRE": "Otra sucursal",
                "PRINT_BACKEND": "tcp",
                "PRINT_SYNC": "false",
                "PRINTER_CAJA_HOST": "192.0.2.50",
                "VPS_CONSOLIDACION_URL": "https://host-ajeno.invalid/consolidar",
                "VPS_CONSOLIDACION_TOKEN": "token-ajeno",
                "VPS_CONSOLIDACION_TIMEOUT": "59",
                "PYTHONHOME": "python-ajeno",
                "PYTHONPATH": "codigo-ajeno",
                "PYTHONUSERBASE": "perfil-ajeno",
                "PYTHONSTARTUP": "inicio-ajeno.py",
                "PYTHONINSPECT": "1",
                "PYTHONWARNINGS": "error",
                "PYTHONUTF8": "0",
                "PYTHONSAFEPATH": "0",
                "PIP_CONFIG_FILE": "config-hostil.ini",
                "PIP_TARGET": "destino-ajeno",
                "VIRTUAL_ENV": "venv-ajena",
                "__PYVENV_LAUNCHER__": "python-ajeno.exe",
                "NO_RELACIONADA": "se-conserva",
            }
            with patch.dict(os.environ, heredado, clear=True), patch.object(
                self.servicio, "BASE_DIR", root
            ):
                self.servicio._cargar_entorno()
                self.assertEqual(os.environ["DJANGO_SECRET_KEY"], "desde-archivo")
                self.assertEqual(os.environ["DB_ENGINE"], "sqlite")
                self.assertEqual(os.environ["SQLITE_PATH"], "runtime/correcta.sqlite3")
                self.assertEqual(os.environ["SUCURSAL_CLAVE"], "NORTE")
                self.assertEqual(os.environ["PRINT_BACKEND"], "archivo")
                self.assertEqual(os.environ["PRINTER_CAJA_HOST"], "10.0.0.20")
                self.assertEqual(
                    os.environ["VPS_CONSOLIDACION_URL"],
                    "https://vps.local/api/consolidaciones",
                )
                self.assertEqual(os.environ["VPS_CONSOLIDACION_TOKEN"], "desde-archivo")
                self.assertEqual(os.environ["VPS_CONSOLIDACION_TIMEOUT"], "12")
                self.assertEqual(os.environ["DJANGO_SETTINGS_MODULE"], "pos.settings")
                self.assertEqual(os.environ["NO_RELACIONADA"], "se-conserva")
                self.assertEqual(os.environ["PYTHONNOUSERSITE"], "1")
                self.assertEqual(os.environ["PYTHONDONTWRITEBYTECODE"], "1")
                self.assertEqual(os.environ["PYTHONUTF8"], "1")
                self.assertEqual(os.environ["PIP_CONFIG_FILE"], os.devnull)
                self.assertEqual(os.environ["PIP_DISABLE_PIP_VERSION_CHECK"], "1")
                self.assertEqual(os.environ["PIP_NO_INPUT"], "1")
                for nombre in (
                    "DB_REPLICA_HOST",
                    "POSTGRES_PASSWORD",
                    "SUCURSAL_NOMBRE",
                    "PRINT_SYNC",
                    "DJANGO_ALLOW_INSECURE_DEVELOPMENT",
                    "DJANGO_ALLOW_INSECURE_TEST_SETTINGS",
                    "PYTHONHOME",
                    "PYTHONPATH",
                    "PYTHONUSERBASE",
                    "PYTHONSTARTUP",
                    "PYTHONINSPECT",
                    "PYTHONWARNINGS",
                    "PYTHONSAFEPATH",
                    "PIP_TARGET",
                    "VIRTUAL_ENV",
                    "__PYVENV_LAUNCHER__",
                ):
                    self.assertNotIn(nombre, os.environ)

    def test_sin_env_tambien_descarta_configuracion_heredada(self):
        with tempfile.TemporaryDirectory() as directory:
            heredado = {
                "DB_ENGINE": "oracle",
                "SUCURSAL_CLAVE": "OTRA",
                "PRINT_BACKEND": "tcp",
                "VPS_CONSOLIDACION_URL": "https://host-ajeno.invalid/consolidar",
                "VPS_CONSOLIDACION_TOKEN": "token-ajeno",
                "VPS_CONSOLIDACION_TIMEOUT": "59",
                "DJANGO_SETTINGS_MODULE": "pos.settings_test",
                "DJANGO_ALLOW_INSECURE_TEST_SETTINGS": "true",
            }
            with patch.dict(os.environ, heredado, clear=True), patch.object(
                self.servicio, "BASE_DIR", Path(directory)
            ):
                self.servicio._cargar_entorno()
                self.assertEqual(os.environ["DJANGO_SETTINGS_MODULE"], "pos.settings")
                for nombre in (
                    "DB_ENGINE",
                    "SUCURSAL_CLAVE",
                    "PRINT_BACKEND",
                    "VPS_CONSOLIDACION_URL",
                    "VPS_CONSOLIDACION_TOKEN",
                    "VPS_CONSOLIDACION_TIMEOUT",
                    "DJANGO_ALLOW_INSECURE_TEST_SETTINGS",
                ):
                    self.assertNotIn(nombre, os.environ)


if __name__ == "__main__":
    unittest.main()
