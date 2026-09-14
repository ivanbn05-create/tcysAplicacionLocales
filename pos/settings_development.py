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
os.environ["PRINT_BACKEND"] = "archivo"
os.environ["PRINT_SYNC"] = "true"
os.environ["PEDIDOS_SUCURSALES_AUTO_SYNC"] = "false"
os.environ["PEDIDOS_SUCURSALES_FUENTE"] = "desactivada"
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
