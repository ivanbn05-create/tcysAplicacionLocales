import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "si", "sí", "yes"}


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "solo-desarrollo-cambiar-en-produccion")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [host.strip() for host in os.getenv("DJANGO_ALLOWED_HOSTS", "*").split(",") if host.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "personas",
    "catalogo",
    "ventas",
    "impresion",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "pos.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]
WSGI_APPLICATION = "pos.wsgi.application"

if os.getenv("DB_ENGINE", "sqlite").lower() == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB", "tocayos"),
            "USER": os.getenv("POSTGRES_USER", "tocayos"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", "tocayos-local"),
            "HOST": os.getenv("POSTGRES_HOST", "db"),
            "PORT": os.getenv("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
    }
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = "es-mx"
TIME_ZONE = "America/Mexico_City"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SUCURSAL_CLAVE = os.getenv("SUCURSAL_CLAVE", "ARBOLEDAS")
PRINT_BACKEND = os.getenv("PRINT_BACKEND", "tcp").lower()
if PRINT_BACKEND not in {"tcp", "archivo"}:
    raise ValueError("PRINT_BACKEND debe ser 'tcp' o 'archivo'.")
PRINT_SYNC = env_bool("PRINT_SYNC", DEBUG)
PRINTER_PORT = int(os.getenv("PRINTER_PORT", "9100"))
PRINTER_TIMEOUT = float(os.getenv("PRINTER_TIMEOUT", "5"))
PRINTER_HOSTS = {
    "caja": os.getenv("PRINTER_CAJA_HOST", "192.168.0.33"),
    "cocina": os.getenv("PRINTER_COCINA_HOST", "192.168.0.33"),
    "barra": os.getenv("PRINTER_BARRA_HOST", "192.168.0.33"),
}
