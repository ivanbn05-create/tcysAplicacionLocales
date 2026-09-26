import os
import re
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from django.core.exceptions import ImproperlyConfigured

from personas.identidad import normalizar_clave_sucursal


BASE_DIR = Path(__file__).resolve().parent.parent


def cargar_entorno_local():
    """Carga ``.env`` sin sobrescribir variables definidas por Windows o Docker."""

    ruta = BASE_DIR / ".env"
    if not ruta.is_file():
        return
    for linea in ruta.read_text(encoding="utf-8-sig").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        nombre, valor = linea.split("=", 1)
        nombre = nombre.strip()
        valor = valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in {'"', "'"}:
            valor = valor[1:-1]
        if nombre:
            os.environ.setdefault(nombre, valor)


cargar_entorno_local()


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "si", "sí", "yes"}


def env_int(name, default):
    valor = os.getenv(name, str(default)).strip()
    try:
        return int(valor)
    except (TypeError, ValueError) as exc:
        raise ImproperlyConfigured(f"{name} debe ser un entero.") from exc


def env_uuid_canonico(name):
    valor = os.getenv(name, "").strip()
    if not valor:
        return ""
    try:
        identificador = UUID(valor)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ImproperlyConfigured(f"{name} debe ser un UUID canonico.") from exc
    if identificador.int == 0 or str(identificador) != valor:
        raise ImproperlyConfigured(f"{name} debe ser un UUID canonico no nulo.")
    return valor


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "").strip()
_CLAVES_INSEGURAS = (
    "cambiar",
    "solo-desarrollo",
    "clave-exclusiva-de-prueba",
    "django-insecure-",
)
if len(SECRET_KEY) < 50 or any(
    SECRET_KEY.lower().startswith(prefijo) for prefijo in _CLAVES_INSEGURAS
) or len(set(SECRET_KEY)) < 5:
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY es obligatoria, debe tener al menos 50 caracteres aleatorios "
        "y no puede ser un marcador de ejemplo."
    )

# La configuración principal siempre es de producción. El modo de prueba vive en
# pos.settings_development/pos.settings_test y nunca debe activarse desde .env.
DEBUG = False

_HOST_DNS = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\."
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)


def host_permitido(host):
    if not host or host != host.strip() or any(caracter in host for caracter in "*/\\"):
        return False
    if "://" in host or any(caracter.isspace() for caracter in host):
        return False
    candidato_ip = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        ip_address(candidato_ip)
        return ":" not in host or (host.startswith("[") and host.endswith("]"))
    except ValueError:
        return bool(_HOST_DNS.fullmatch(host)) and ":" not in host


def url_https_configurada(nombre, valor):
    valor = str(valor or "").strip()
    if not valor:
        return ""
    try:
        uri = urlsplit(valor)
        puerto = uri.port
    except ValueError as exc:
        raise ImproperlyConfigured(
            f"{nombre} debe ser una URL HTTPS absoluta sin credenciales embebidas."
        ) from exc
    if (
        any(caracter.isspace() for caracter in valor)
        or uri.scheme.lower() != "https"
        or not uri.netloc
        or not uri.hostname
        or uri.username is not None
        or uri.password is not None
        or uri.query
        or uri.fragment
        or (puerto is not None and not 1 <= puerto <= 65535)
    ):
        raise ImproperlyConfigured(
            f"{nombre} debe ser una URL HTTPS absoluta sin credenciales, query ni fragmento."
        )
    return valor.rstrip("/")


_hosts_configurados = os.getenv(
    "DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]"
).split(",")
if not _hosts_configurados or any(not host_permitido(host) for host in _hosts_configurados):
    raise ImproperlyConfigured(
        "DJANGO_ALLOWED_HOSTS sólo acepta hosts/IP concretos separados por coma, sin "
        "comodines, espacios, esquemas, rutas ni puertos."
    )
ALLOWED_HOSTS = _hosts_configurados

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
    "soporte",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "ventas.middleware.POSSessionAuthenticationMiddleware",
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

DB_ENGINE = os.getenv("DB_ENGINE", "").strip().lower() or "sqlite"
if DB_ENGINE not in {"sqlite", "postgres"}:
    raise ImproperlyConfigured("DB_ENGINE debe ser 'sqlite' o 'postgres'.")

if DB_ENGINE == "postgres":
    postgres_password = os.getenv("POSTGRES_PASSWORD", "").strip()
    if not postgres_password:
        raise ImproperlyConfigured("POSTGRES_PASSWORD es obligatoria al usar PostgreSQL.")
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB", "tocayos"),
            "USER": os.getenv("POSTGRES_USER", "tocayos"),
            "PASSWORD": postgres_password,
            "HOST": os.getenv("POSTGRES_HOST", "db"),
            "PORT": os.getenv("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
    }
else:
    sqlite_path = Path(os.getenv("SQLITE_PATH", "db.sqlite3"))
    if not sqlite_path.is_absolute():
        sqlite_path = BASE_DIR / sqlite_path
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": sqlite_path,
            "OPTIONS": {"timeout": 20, "transaction_mode": "IMMEDIATE"},
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]
LANGUAGE_CODE = "es-mx"
TIME_ZONE = "America/Mexico_City"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DATA_UPLOAD_MAX_MEMORY_SIZE = 1 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 1 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 1000
DATA_UPLOAD_MAX_NUMBER_FILES = 10

RUNTIME_DIR = BASE_DIR / "runtime"
CACHE_DIR = RUNTIME_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": str(CACHE_DIR),
        "TIMEOUT": 900,
        "OPTIONS": {"MAX_ENTRIES": 2000},
    }
}

# Cabeceras seguras compatibles con HTTP dentro de una LAN. Waitress no termina
# TLS; estas protecciones se activan sólo detrás de un proxy HTTPS confiable.
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 12 * 60 * 60
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
HTTPS_ENABLED = env_bool("DJANGO_HTTPS", False)
WAITRESS_HOST = os.getenv("WAITRESS_HOST", "127.0.0.1").strip()
WAITRESS_TRUSTED_PROXY = os.getenv("WAITRESS_TRUSTED_PROXY", "").strip()
ALLOW_INSECURE_HTTP_LAN = env_bool("ALLOW_INSECURE_HTTP_LAN", False)
try:
    _direccion_escucha = ip_address(WAITRESS_HOST)
except ValueError as exc:
    raise ImproperlyConfigured("WAITRESS_HOST debe ser una dirección IP concreta.") from exc
if _direccion_escucha.is_multicast:
    raise ImproperlyConfigured("WAITRESS_HOST no puede ser una dirección multicast.")

_direccion_proxy = None
if WAITRESS_TRUSTED_PROXY:
    try:
        _direccion_proxy = ip_address(WAITRESS_TRUSTED_PROXY)
    except ValueError as exc:
        raise ImproperlyConfigured(
            "WAITRESS_TRUSTED_PROXY debe ser una dirección IP concreta."
        ) from exc
    if not _direccion_proxy.is_loopback or not _direccion_escucha.is_loopback:
        raise ImproperlyConfigured(
            "El proxy confiable y Waitress deben comunicarse exclusivamente por loopback."
        )

if HTTPS_ENABLED and _direccion_proxy is None:
    raise ImproperlyConfigured(
        "DJANGO_HTTPS requiere WAITRESS_TRUSTED_PROXY y Waitress escuchando en loopback."
    )
if not HTTPS_ENABLED and not _direccion_escucha.is_loopback and not ALLOW_INSECURE_HTTP_LAN:
    raise ImproperlyConfigured(
        "Exponer HTTP fuera de loopback requiere ALLOW_INSECURE_HTTP_LAN=true de forma explícita."
    )

SESSION_COOKIE_SECURE = HTTPS_ENABLED
CSRF_COOKIE_SECURE = HTTPS_ENABLED
SECURE_SSL_REDIRECT = HTTPS_ENABLED
SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_HSTS_SECONDS", "31536000")) if HTTPS_ENABLED else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool("DJANGO_HSTS_PRELOAD", False)
if HTTPS_ENABLED:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "seguro": {
            "format": "{asctime} {levelname} {name}: {message}",
            "style": "{",
        }
    },
    "handlers": {
        "archivo": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOG_DIR / "django.log",
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 5,
            "encoding": "utf-8",
            "formatter": "seguro",
            "level": "WARNING",
        }
    },
    "loggers": {
        "django": {
            "handlers": ["archivo"],
            "level": "WARNING",
            "propagate": False,
        }
    },
}

_SUCURSAL_CLAVE_CONFIGURADA = os.getenv("SUCURSAL_CLAVE", "").strip()
if not _SUCURSAL_CLAVE_CONFIGURADA:
    raise ImproperlyConfigured(
        "SUCURSAL_CLAVE es obligatoria. Configura explícitamente la identidad "
        "de esta instalación antes de iniciar Django."
    )
try:
    SUCURSAL_CLAVE = normalizar_clave_sucursal(_SUCURSAL_CLAVE_CONFIGURADA)
except ValueError as exc:
    raise ImproperlyConfigured(f"SUCURSAL_CLAVE no es válida: {exc}") from exc
POS_REQUIRE_AUTH = True
LOGIN_URL = "/acceso/"
POS_LOGIN_MAX_ATTEMPTS = int(os.getenv("POS_LOGIN_MAX_ATTEMPTS", "5"))
POS_LOGIN_MAX_IP_ATTEMPTS = int(os.getenv("POS_LOGIN_MAX_IP_ATTEMPTS", "20"))
POS_LOGIN_LOCKOUT_SECONDS = int(os.getenv("POS_LOGIN_LOCKOUT_SECONDS", "900"))
POS_TICKET_LOCK_LEASE_SECONDS = int(os.getenv("POS_TICKET_LOCK_LEASE_SECONDS", "15"))
PRINT_BACKEND = os.getenv("PRINT_BACKEND", "archivo").lower()
if PRINT_BACKEND not in {"tcp", "archivo"}:
    raise ImproperlyConfigured("PRINT_BACKEND debe ser 'tcp' o 'archivo'.")
PRINT_SYNC = env_bool("PRINT_SYNC", DEBUG)
try:
    PRINT_PROCESSING_TIMEOUT_SECONDS = int(
        os.getenv("PRINT_PROCESSING_TIMEOUT_SECONDS", "300")
    )
except ValueError as exc:
    raise ImproperlyConfigured(
        "PRINT_PROCESSING_TIMEOUT_SECONDS debe ser un entero entre 30 y 3600."
    ) from exc
PRINTER_PORT = int(os.getenv("PRINTER_PORT", "9100"))
PRINTER_TIMEOUT = float(os.getenv("PRINTER_TIMEOUT", "5"))
if not 30 <= PRINT_PROCESSING_TIMEOUT_SECONDS <= 3600:
    raise ImproperlyConfigured(
        "PRINT_PROCESSING_TIMEOUT_SECONDS debe ser un entero entre 30 y 3600."
    )
if not 1 <= PRINTER_PORT <= 65535:
    raise ImproperlyConfigured("PRINTER_PORT debe estar entre 1 y 65535.")
if not 0 < PRINTER_TIMEOUT <= 60:
    raise ImproperlyConfigured("PRINTER_TIMEOUT debe ser mayor que 0 y máximo 60 segundos.")
PRINT_PREVIEW_RETENTION_DAYS = int(os.getenv("PRINT_PREVIEW_RETENTION_DAYS", "7"))
if not 0 <= PRINT_PREVIEW_RETENTION_DAYS <= 30:
    raise ImproperlyConfigured("PRINT_PREVIEW_RETENTION_DAYS debe estar entre 0 y 30.")
PRINTER_HOSTS = {
    "caja": os.getenv("PRINTER_CAJA_HOST", "").strip(),
    "cocina": os.getenv("PRINTER_COCINA_HOST", "").strip(),
    "barra": os.getenv("PRINTER_BARRA_HOST", "").strip(),
}
if PRINT_BACKEND == "tcp" and any(
    not host_permitido(host) for host in PRINTER_HOSTS.values()
):
    raise ImproperlyConfigured(
        "PRINTER_CAJA_HOST, PRINTER_COCINA_HOST y PRINTER_BARRA_HOST deben ser "
        "direcciones concretas cuando PRINT_BACKEND=tcp."
    )

# Puente de sólo lectura con la base de pedidos. Supabase tiene prioridad y la
# SQLite hermana queda como respaldo para desarrollo sin conexión.
PEDIDOS_SUCURSALES_DATABASE_URL = os.getenv("PEDIDOS_SUCURSALES_DATABASE_URL", "").strip()
PEDIDOS_SUCURSALES_FUENTE = os.getenv("PEDIDOS_SUCURSALES_FUENTE", "desactivada").strip().lower()
if PEDIDOS_SUCURSALES_FUENTE not in {"desactivada", "supabase", "sqlite", "api_v2"}:
    raise ImproperlyConfigured(
        "PEDIDOS_SUCURSALES_FUENTE debe ser desactivada, supabase, sqlite o api_v2."
    )
PEDIDOS_SUCURSALES_POSTGRES = {
    "host": os.getenv("PEDIDOS_SUCURSALES_DB_HOST", "").strip(),
    "port": int(os.getenv("PEDIDOS_SUCURSALES_DB_PORT", "5432")),
    "dbname": os.getenv("PEDIDOS_SUCURSALES_DB_NAME", "postgres"),
    "user": os.getenv("PEDIDOS_SUCURSALES_DB_USER", "").strip(),
    "password": os.getenv("PEDIDOS_SUCURSALES_DB_PASSWORD", ""),
    "sslmode": os.getenv("PEDIDOS_SUCURSALES_DB_SSLMODE", "verify-full"),
    "sslrootcert": os.getenv("PEDIDOS_SUCURSALES_DB_SSLROOTCERT", "").strip(),
    "connect_timeout": int(os.getenv("PEDIDOS_SUCURSALES_DB_TIMEOUT", "8")),
}
PEDIDOS_SUCURSALES_MAX_PEDIDOS = int(os.getenv("PEDIDOS_SUCURSALES_MAX_PEDIDOS", "500"))
PEDIDOS_SUCURSALES_DB = Path(
    os.getenv("PEDIDOS_SUCURSALES_DB", str(BASE_DIR.parent / "tcysPedidosSucursales" / "db.sqlite3"))
)
PEDIDOS_SUCURSALES_AUTO_SYNC = env_bool("PEDIDOS_SUCURSALES_AUTO_SYNC", False)
PEDIDOS_SUCURSALES_SYNC_SECONDS = int(os.getenv("PEDIDOS_SUCURSALES_SYNC_SECONDS", "300"))
PEDIDOS_SUCURSALES_HORA_INICIO = os.getenv("PEDIDOS_SUCURSALES_HORA_INICIO", "06:00")
# Cinco minutos de gracia garantizan una lectura posterior al último pedido de las 17:30.
PEDIDOS_SUCURSALES_HORA_FIN = os.getenv("PEDIDOS_SUCURSALES_HORA_FIN", "17:35")

# API Pedidos v2. Se mantiene separada de Supabase y de las credenciales Central.
PEDIDOS_API_BASE_URL = url_https_configurada(
    "PEDIDOS_API_BASE_URL",
    os.getenv("PEDIDOS_API_BASE_URL", ""),
)
PEDIDOS_API_ENDPOINT = os.getenv("PEDIDOS_API_ENDPOINT", "/api/v2/pos/pedidos/").strip()
if (
    not PEDIDOS_API_ENDPOINT.startswith("/")
    or PEDIDOS_API_ENDPOINT.startswith("//")
    or "?" in PEDIDOS_API_ENDPOINT
    or "#" in PEDIDOS_API_ENDPOINT
    or "\\" in PEDIDOS_API_ENDPOINT
):
    raise ImproperlyConfigured("PEDIDOS_API_ENDPOINT debe ser una ruta absoluta relativa al mismo origen.")
PEDIDOS_API_TOKEN = os.getenv("PEDIDOS_API_TOKEN", "").strip()
PEDIDOS_API_BOUND_IDENTITY = env_bool("PEDIDOS_API_BOUND_IDENTITY", False)
PEDIDOS_API_EDGE_ID = env_uuid_canonico("PEDIDOS_API_EDGE_ID")
PEDIDOS_API_POS_BRANCH_ID = env_uuid_canonico("PEDIDOS_API_POS_BRANCH_ID")
if PEDIDOS_API_BOUND_IDENTITY and (not PEDIDOS_API_EDGE_ID or not PEDIDOS_API_POS_BRANCH_ID):
    raise ImproperlyConfigured(
        "PEDIDOS_API_BOUND_IDENTITY exige PEDIDOS_API_EDGE_ID y PEDIDOS_API_POS_BRANCH_ID."
    )
if not PEDIDOS_API_BOUND_IDENTITY and (PEDIDOS_API_EDGE_ID or PEDIDOS_API_POS_BRANCH_ID):
    raise ImproperlyConfigured(
        "Las identidades vinculadas de Pedidos exigen PEDIDOS_API_BOUND_IDENTITY=true."
    )
PEDIDOS_API_CA_BUNDLE = os.getenv("PEDIDOS_API_CA_BUNDLE", "").strip()
try:
    PEDIDOS_API_SUCURSAL_IDS = tuple(
        sorted({
            int(valor.strip())
            for valor in os.getenv("PEDIDOS_API_SUCURSAL_IDS", "").split(",")
            if valor.strip()
        })
    )
except ValueError as exc:
    raise ImproperlyConfigured("PEDIDOS_API_SUCURSAL_IDS solo acepta enteros positivos.") from exc
if any(valor <= 0 for valor in PEDIDOS_API_SUCURSAL_IDS):
    raise ImproperlyConfigured("PEDIDOS_API_SUCURSAL_IDS solo acepta enteros positivos.")
PEDIDOS_API_PAGE_SIZE = env_int("PEDIDOS_API_PAGE_SIZE", 100)
PEDIDOS_API_CONNECT_TIMEOUT_SECONDS = env_int("PEDIDOS_API_CONNECT_TIMEOUT_SECONDS", 5)
PEDIDOS_API_READ_TIMEOUT_SECONDS = env_int("PEDIDOS_API_READ_TIMEOUT_SECONDS", 15)
PEDIDOS_API_MAX_RESPONSE_BYTES = env_int("PEDIDOS_API_MAX_RESPONSE_BYTES", 1048576)
PEDIDOS_API_MAX_RETRIES = env_int("PEDIDOS_API_MAX_RETRIES", 2)
if not 1 <= PEDIDOS_API_PAGE_SIZE <= 500:
    raise ImproperlyConfigured("PEDIDOS_API_PAGE_SIZE debe estar entre 1 y 500.")
if not 1 <= PEDIDOS_API_CONNECT_TIMEOUT_SECONDS <= 30:
    raise ImproperlyConfigured("PEDIDOS_API_CONNECT_TIMEOUT_SECONDS debe estar entre 1 y 30.")
if not 1 <= PEDIDOS_API_READ_TIMEOUT_SECONDS <= 60:
    raise ImproperlyConfigured("PEDIDOS_API_READ_TIMEOUT_SECONDS debe estar entre 1 y 60.")
if not 4096 <= PEDIDOS_API_MAX_RESPONSE_BYTES <= 4 * 1024 * 1024:
    raise ImproperlyConfigured("PEDIDOS_API_MAX_RESPONSE_BYTES debe estar entre 4096 y 4194304.")
if not 0 <= PEDIDOS_API_MAX_RETRIES <= 5:
    raise ImproperlyConfigured("PEDIDOS_API_MAX_RETRIES debe estar entre 0 y 5.")
if PEDIDOS_API_TOKEN and not 32 <= len(PEDIDOS_API_TOKEN) <= 512:
    raise ImproperlyConfigured("PEDIDOS_API_TOKEN debe tener entre 32 y 512 caracteres.")
if PEDIDOS_API_CA_BUNDLE and not Path(PEDIDOS_API_CA_BUNDLE).is_file():
    raise ImproperlyConfigured("PEDIDOS_API_CA_BUNDLE debe apuntar a un archivo existente.")
if PEDIDOS_SUCURSALES_FUENTE == "api_v2":
    if not PEDIDOS_API_BASE_URL or not PEDIDOS_API_TOKEN or not PEDIDOS_API_SUCURSAL_IDS:
        raise ImproperlyConfigured(
            "api_v2 exige PEDIDOS_API_BASE_URL, PEDIDOS_API_TOKEN "
            "y PEDIDOS_API_SUCURSAL_IDS explicitos."
        )

# Backend Central candidato. Todos los flujos v2 permanecen apagados por defecto.
CENTRAL_API_BASE_URL = url_https_configurada(
    "CENTRAL_API_BASE_URL",
    os.getenv("CENTRAL_API_BASE_URL", ""),
)
CENTRAL_BRANCH_ID = env_uuid_canonico("CENTRAL_BRANCH_ID")
CENTRAL_BRANCH_CODE = os.getenv("CENTRAL_BRANCH_CODE", "").strip()
CENTRAL_POS_INSTANCE_ID = env_uuid_canonico("CENTRAL_POS_INSTANCE_ID")
if CENTRAL_BRANCH_CODE:
    try:
        CENTRAL_BRANCH_CODE = normalizar_clave_sucursal(CENTRAL_BRANCH_CODE)
    except ValueError as exc:
        raise ImproperlyConfigured("CENTRAL_BRANCH_CODE no es canonico.") from exc
CENTRAL_INGEST_TOKEN = os.getenv("CENTRAL_INGEST_TOKEN", "").strip()
CENTRAL_CATALOG_TOKEN = os.getenv("CENTRAL_CATALOG_TOKEN", "").strip()
CENTRAL_API_CA_BUNDLE = os.getenv("CENTRAL_API_CA_BUNDLE", "").strip()
CENTRAL_ENABLE_SALES_V2 = env_bool("CENTRAL_ENABLE_SALES_V2", False)
CENTRAL_ENABLE_CUSTOMERS_V2 = env_bool("CENTRAL_ENABLE_CUSTOMERS_V2", False)
CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2 = env_bool(
    "CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2",
    False,
)
CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V3 = env_bool(
    "CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V3",
    False,
)
CENTRAL_CONNECT_TIMEOUT_SECONDS = env_int("CENTRAL_CONNECT_TIMEOUT_SECONDS", 5)
CENTRAL_READ_TIMEOUT_SECONDS = env_int("CENTRAL_READ_TIMEOUT_SECONDS", 15)
CENTRAL_MAX_RESPONSE_BYTES = env_int("CENTRAL_MAX_RESPONSE_BYTES", 2 * 1048576)
CENTRAL_SYNC_INTERVAL_SECONDS = env_int("CENTRAL_SYNC_INTERVAL_SECONDS", 300)
if not 1 <= CENTRAL_CONNECT_TIMEOUT_SECONDS <= 30:
    raise ImproperlyConfigured("CENTRAL_CONNECT_TIMEOUT_SECONDS debe estar entre 1 y 30.")
if not 1 <= CENTRAL_READ_TIMEOUT_SECONDS <= 60:
    raise ImproperlyConfigured("CENTRAL_READ_TIMEOUT_SECONDS debe estar entre 1 y 60.")
if not 4096 <= CENTRAL_MAX_RESPONSE_BYTES <= 4 * 1024 * 1024:
    raise ImproperlyConfigured("CENTRAL_MAX_RESPONSE_BYTES debe estar entre 4096 y 4194304.")
if not 30 <= CENTRAL_SYNC_INTERVAL_SECONDS <= 86400:
    raise ImproperlyConfigured("CENTRAL_SYNC_INTERVAL_SECONDS debe estar entre 30 y 86400.")
if (CENTRAL_ENABLE_SALES_V2 or CENTRAL_ENABLE_CUSTOMERS_V2
        or CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2 or CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V3):
    if not CENTRAL_API_BASE_URL or not CENTRAL_BRANCH_ID or not CENTRAL_BRANCH_CODE or not CENTRAL_POS_INSTANCE_ID:
        raise ImproperlyConfigured(
            "Un flujo Central v2 activo exige URL, branch UUID/codigo e instance UUID."
        )
if CENTRAL_INGEST_TOKEN and not 32 <= len(CENTRAL_INGEST_TOKEN) <= 512:
    raise ImproperlyConfigured("CENTRAL_INGEST_TOKEN debe tener entre 32 y 512 caracteres.")
if CENTRAL_CATALOG_TOKEN and not 32 <= len(CENTRAL_CATALOG_TOKEN) <= 512:
    raise ImproperlyConfigured("CENTRAL_CATALOG_TOKEN debe tener entre 32 y 512 caracteres.")
if (CENTRAL_ENABLE_SALES_V2 or CENTRAL_ENABLE_CUSTOMERS_V2) and not CENTRAL_INGEST_TOKEN:
    raise ImproperlyConfigured("Ventas/clientes v2 exigen CENTRAL_INGEST_TOKEN independiente.")
if (CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2 or CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V3) and not CENTRAL_CATALOG_TOKEN:
    raise ImproperlyConfigured("Catalogo Central exige CENTRAL_CATALOG_TOKEN independiente.")
if CENTRAL_API_CA_BUNDLE and not Path(CENTRAL_API_CA_BUNDLE).is_file():
    raise ImproperlyConfigured("CENTRAL_API_CA_BUNDLE debe apuntar a un archivo existente.")

# Consolidación mensual Edge -> VPS. URL y token vacíos mantienen los envíos desactivados.
VPS_CONSOLIDACION_URL = os.getenv("VPS_CONSOLIDACION_URL", "").strip()
VPS_CONSOLIDACION_TOKEN = os.getenv("VPS_CONSOLIDACION_TOKEN", "").strip()
if bool(VPS_CONSOLIDACION_URL) != bool(VPS_CONSOLIDACION_TOKEN):
    raise ImproperlyConfigured(
        "VPS_CONSOLIDACION_URL y VPS_CONSOLIDACION_TOKEN deben configurarse juntos."
    )
if VPS_CONSOLIDACION_URL:
    try:
        _vps_consolidacion_uri = urlsplit(VPS_CONSOLIDACION_URL)
        _vps_consolidacion_port = _vps_consolidacion_uri.port
    except ValueError as exc:
        raise ImproperlyConfigured(
            "VPS_CONSOLIDACION_URL debe ser una URL HTTPS absoluta sin credenciales embebidas."
        ) from exc
    if (
        any(caracter.isspace() for caracter in VPS_CONSOLIDACION_URL)
        or _vps_consolidacion_uri.scheme.lower() != "https"
        or not _vps_consolidacion_uri.netloc
        or not _vps_consolidacion_uri.hostname
        or _vps_consolidacion_uri.username is not None
        or _vps_consolidacion_uri.password is not None
        or _vps_consolidacion_uri.fragment
    ):
        raise ImproperlyConfigured(
            "VPS_CONSOLIDACION_URL debe ser una URL HTTPS absoluta sin credenciales embebidas."
        )
try:
    VPS_CONSOLIDACION_TIMEOUT = int(os.getenv("VPS_CONSOLIDACION_TIMEOUT", "10"))
except ValueError as exc:
    raise ImproperlyConfigured(
        "VPS_CONSOLIDACION_TIMEOUT debe ser un entero entre 1 y 60 segundos."
    ) from exc
if not 1 <= VPS_CONSOLIDACION_TIMEOUT <= 60:
    raise ImproperlyConfigured(
        "VPS_CONSOLIDACION_TIMEOUT debe ser un entero entre 1 y 60 segundos."
    )

_tokens_remotos_configurados = tuple(
    token
    for token in (
        PEDIDOS_API_TOKEN,
        CENTRAL_INGEST_TOKEN,
        CENTRAL_CATALOG_TOKEN,
        VPS_CONSOLIDACION_TOKEN,
    )
    if token
)
if len(_tokens_remotos_configurados) != len(set(_tokens_remotos_configurados)):
    raise ImproperlyConfigured(
        "Cada flujo remoto debe usar una credencial distinta; revisa los tokens configurados."
    )
