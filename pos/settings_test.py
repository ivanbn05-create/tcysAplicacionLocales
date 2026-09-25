"""Configuración aislada para la suite automatizada de Django."""

import os
import tempfile
from pathlib import Path


if os.getenv("DJANGO_ALLOW_INSECURE_TEST_SETTINGS") != "1":
    raise RuntimeError(
        "pos.settings_test sólo puede activarse mediante `manage.py test`."
    )

_TEST_RUNTIME_DIR = Path(tempfile.gettempdir()) / "tocayos-pos-tests"
_TEST_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

os.environ["DJANGO_SECRET_KEY"] = (
    "tests-only-4pR8wQ1zN6kM3vC9xT2sL7dF5gH0jB4yU8iO1aE6nV3mK9qZ"
)
os.environ["DJANGO_ALLOWED_HOSTS"] = "localhost,127.0.0.1,[::1],testserver"
os.environ["DB_ENGINE"] = "sqlite"
os.environ["SQLITE_PATH"] = str(_TEST_RUNTIME_DIR / "db.sqlite3")
os.environ["SUCURSAL_CLAVE"] = "ARBOLEDAS"
os.environ["PRINT_BACKEND"] = "archivo"
os.environ["PRINT_SYNC"] = "true"
os.environ["PEDIDOS_SUCURSALES_AUTO_SYNC"] = "false"
os.environ["PEDIDOS_SUCURSALES_FUENTE"] = "desactivada"
# Los perfiles de prueba nunca heredan endpoints ni secretos de integracion.
for _nombre_secreto in (
    "PEDIDOS_API_BASE_URL",
    "PEDIDOS_API_TOKEN",
    "PEDIDOS_API_SUCURSAL_IDS",
    "PEDIDOS_API_CA_BUNDLE",
    "CENTRAL_API_BASE_URL",
    "CENTRAL_BRANCH_ID",
    "CENTRAL_BRANCH_CODE",
    "CENTRAL_POS_INSTANCE_ID",
    "CENTRAL_INGEST_TOKEN",
    "CENTRAL_CATALOG_TOKEN",
    "CENTRAL_API_CA_BUNDLE",
):
    os.environ[_nombre_secreto] = ""
os.environ["CENTRAL_ENABLE_SALES_V2"] = "false"
os.environ["CENTRAL_ENABLE_CUSTOMERS_V2"] = "false"
os.environ["CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2"] = "false"
os.environ["CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V3"] = "false"
# La suite no debe heredar credenciales ni un destino VPS del host.
os.environ["VPS_CONSOLIDACION_URL"] = ""
os.environ["VPS_CONSOLIDACION_TOKEN"] = ""
# La suite tampoco debe heredar el perfil HTTPS/proxy de una instalación real.
os.environ["DJANGO_HTTPS"] = "false"
os.environ["WAITRESS_HOST"] = "127.0.0.1"
os.environ["WAITRESS_TRUSTED_PROXY"] = ""
os.environ["ALLOW_INSECURE_HTTP_LAN"] = "false"

from .settings import *  # noqa: E402,F403


DEBUG = True
TESTING = True
POS_REQUIRE_AUTH = False
MEDIA_ROOT = _TEST_RUNTIME_DIR / "media"
PEDIDOS_SUCURSALES_FUENTE = "desactivada"
PEDIDOS_SUCURSALES_AUTO_SYNC = False
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "tocayos-tests",
    }
}
LOGGING["handlers"]["archivo"]["filename"] = _TEST_RUNTIME_DIR / "django.log"
