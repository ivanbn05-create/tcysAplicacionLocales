"""Configuración explícita para la prueba manual; nunca usar en producción."""

import os


if os.getenv("DJANGO_ALLOW_INSECURE_DEVELOPMENT", "").strip().lower() not in {
    "1",
    "true",
    "si",
    "sí",
    "yes",
}:
    raise RuntimeError(
        "pos.settings_development requiere DJANGO_ALLOW_INSECURE_DEVELOPMENT=true. "
        "Usa iniciar-prueba-lan.ps1 para activar el modo de prueba aislado."
    )

os.environ["DJANGO_SECRET_KEY"] = (
    "dev-local-9uL4oP7xQ2mN8vR5cK1sT6zA3bD0fG7hJ4kL9pW2yE8rU5iO1qX6"
)
os.environ["DJANGO_ALLOWED_HOSTS"] = "localhost,127.0.0.1,[::1],192.168.0.30,testserver"
os.environ["DB_ENGINE"] = "sqlite"
os.environ["SQLITE_PATH"] = "runtime/prueba/db.sqlite3"
os.environ["SUCURSAL_CLAVE"] = "ARBOLEDAS"
_verdaderos = {"1", "true", "si", "sí", "yes"}
_backend_impresion = os.getenv("PRINT_BACKEND", "archivo").strip().lower()
if _backend_impresion == "tcp" and os.getenv(
    "DJANGO_ALLOW_REAL_PRINTER_DEVELOPMENT", ""
).strip().lower() not in _verdaderos:
    raise RuntimeError(
        "La impresión TCP en desarrollo requiere "
        "DJANGO_ALLOW_REAL_PRINTER_DEVELOPMENT=true de forma explícita."
    )
os.environ["PRINT_BACKEND"] = _backend_impresion
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
# Una prueba local nunca debe heredar el destino ni el secreto del VPS.
os.environ["VPS_CONSOLIDACION_URL"] = ""
os.environ["VPS_CONSOLIDACION_TOKEN"] = ""
# El perfil de prueba no hereda TLS, confianza de proxy ni la decisión de exponer
# HTTP que pudiera existir en el .env de producción.
os.environ["DJANGO_HTTPS"] = "false"
os.environ["WAITRESS_HOST"] = "127.0.0.1"
os.environ["WAITRESS_TRUSTED_PROXY"] = ""
os.environ["ALLOW_INSECURE_HTTP_LAN"] = "false"

from .settings import *  # noqa: E402,F403


DEBUG = True
POS_REQUIRE_AUTH = False
MEDIA_ROOT = BASE_DIR / "runtime" / "prueba" / "media"
PEDIDOS_SUCURSALES_FUENTE = "desactivada"
PEDIDOS_SUCURSALES_AUTO_SYNC = False
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "tocayos-prueba-manual",
    }
}
_LOG_DIR_PRUEBA = BASE_DIR / "runtime" / "prueba"
_LOG_DIR_PRUEBA.mkdir(parents=True, exist_ok=True)
LOGGING["handlers"]["archivo"]["filename"] = _LOG_DIR_PRUEBA / "django.log"
