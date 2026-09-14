"""Pruebas del despliegue sin leer .env ni acceder a datos de produccion."""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile


SOURCE_DIRS = ("catalogo", "datos", "herramientas", "impresion", "personas", "pos", "tests", "ventas")


def main() -> int:
    source = Path(__file__).resolve().parent.parent
    # La copia contiene solo codigo/assets. No incluye .env, certs ni bases reales.
    with tempfile.TemporaryDirectory(prefix="tocayos-deploy-check-") as temporary:
        snapshot = Path(temporary).resolve()
        if snapshot.parent != Path(tempfile.gettempdir()).resolve():
            raise RuntimeError("Directorio temporal inesperado.")
        shutil.copyfile(source / "manage.py", snapshot / "manage.py")
        for name in SOURCE_DIRS:
            if (source / name).is_dir():
                shutil.copytree(
                    source / name,
                    snapshot / name,
                    ignore=shutil.ignore_patterns(
                        "__pycache__", "*.pyc", "*.sqlite3*", ".env*", "*.pem",
                        "*.key", "*.pfx", "*.p12", "*.log",
                    ),
                )
        # Entorno minimo: no se heredan conexiones, credenciales ni ajustes Django.
        environment = {
            name: value for name, value in os.environ.items()
            if name.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC", "PATHEXT"}
        }
        environment.update({
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
            "DJANGO_ALLOWED_HOSTS": "localhost,127.0.0.1",
            "DB_ENGINE": "sqlite",
            "SQLITE_PATH": str(snapshot / "check.sqlite3"),
            "SUCURSAL_CLAVE": "ARBOLEDAS",
            "PEDIDOS_SUCURSALES_FUENTE": "desactivada",
            "PEDIDOS_SUCURSALES_AUTO_SYNC": "false",
            "PRINT_BACKEND": "archivo",
            "PRINT_SYNC": "true",
            "DJANGO_HTTPS": "true",
            "DJANGO_HSTS_INCLUDE_SUBDOMAINS": "true",
            "DJANGO_HSTS_PRELOAD": "true",
            "WAITRESS_HOST": "127.0.0.1",
            "WAITRESS_TRUSTED_PROXY": "127.0.0.1",
            "ALLOW_INSECURE_HTTP_LAN": "false",
        })
        commands = (
            ("Migraciones Django declaradas",
             ["manage.py", "makemigrations", "--check", "--dry-run"]),
            ("Suite Django aislada", ["manage.py", "test", "--noinput"]),
            ("Check deploy efimero HTTPS", ["manage.py", "check", "--deploy", "--fail-level", "WARNING"]),
            ("Respaldo SQLite: WAL, integridad, restauracion y retencion",
             ["-m", "unittest", "discover", "-s", "tests", "-p", "test_respaldo_sqlite.py", "-v"]),
            ("Host de servicio Windows",
             ["-m", "unittest", "discover", "-s", "tests", "-p", "test_host_servicio_windows.py", "-v"]),
        )
        for label, arguments in commands:
            print(label, flush=True)
            result = subprocess.run([sys.executable, *arguments], cwd=snapshot, env=environment)
            if result.returncode:
                return result.returncode
        environment.update({
            "DJANGO_HTTPS": "false",
            "DJANGO_HSTS_INCLUDE_SUBDOMAINS": "false",
            "DJANGO_HSTS_PRELOAD": "false",
            "WAITRESS_HOST": "0.0.0.0",
            "WAITRESS_TRUSTED_PROXY": "",
            "ALLOW_INSECURE_HTTP_LAN": "true",
        })
        print("Check deploy efimero HTTP LAN: se esperan avisos de TLS/cookies.", flush=True)
        return subprocess.run(
            [sys.executable, "manage.py", "check", "--deploy"], cwd=snapshot, env=environment
        ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
