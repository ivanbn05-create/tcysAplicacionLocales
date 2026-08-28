"""Configuración aislada para la suite automatizada de Django."""

import os


if os.getenv("DJANGO_ALLOW_INSECURE_TEST_SETTINGS") != "1":
    raise RuntimeError(
        "pos.settings_test sólo puede activarse mediante `manage.py test`."
    )

os.environ["DJANGO_SECRET_KEY"] = (
    "tests-only-4pR8wQ1zN6kM3vC9xT2sL7dF5gH0jB4yU8iO1aE6nV3mK9qZ"
)
os.environ["DJANGO_ALLOWED_HOSTS"] = "localhost,127.0.0.1,[::1],testserver"
os.environ["DB_ENGINE"] = "sqlite"
os.environ["SQLITE_PATH"] = "runtime/tests/db.sqlite3"
os.environ["PRINT_BACKEND"] = "archivo"
os.environ["PRINT_SYNC"] = "true"
os.environ["PEDIDOS_SUCURSALES_AUTO_SYNC"] = "false"
os.environ["PEDIDOS_SUCURSALES_FUENTE"] = "desactivada"
# La suite tampoco debe heredar el perfil HTTPS/proxy de una instalación real.
os.environ["DJANGO_HTTPS"] = "false"
os.environ["WAITRESS_HOST"] = "127.0.0.1"
os.environ["WAITRESS_TRUSTED_PROXY"] = ""
os.environ["ALLOW_INSECURE_HTTP_LAN"] = "false"

from .settings import *  # noqa: E402,F403


DEBUG = True
TESTING = True
POS_REQUIRE_AUTH = False
MEDIA_ROOT = BASE_DIR / "runtime" / "tests" / "media"
PEDIDOS_SUCURSALES_FUENTE = "desactivada"
PEDIDOS_SUCURSALES_AUTO_SYNC = False
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "tocayos-tests",
    }
}
_LOG_DIR_TESTS = BASE_DIR / "runtime" / "tests"
_LOG_DIR_TESTS.mkdir(parents=True, exist_ok=True)
LOGGING["handlers"]["archivo"]["filename"] = _LOG_DIR_TESTS / "django.log"
