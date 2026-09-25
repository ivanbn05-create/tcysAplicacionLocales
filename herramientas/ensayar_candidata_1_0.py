"""Ensayo aislado de instalación SQLite y migración desde dev.10.

No crea servicios Windows ni toca instalaciones existentes.
Usa un directorio temporal y el Python del venv de esta rama.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASELINE_COMMIT = "e046f2bf74096d76eea50762aa07253f9b073a3d"
SANTA_ANITA_ID = "22c91fd6-21dc-402d-ac68-3fcf6c6d9a47"
MARKER_ID = "9deaeaf0-c73d-4a48-bf86-4070dcf09e9e"


def run(args: list[str], *, cwd: Path, env: dict[str, str], stdout=None) -> None:
    process = subprocess.run(args, cwd=cwd, env=env, stdout=stdout, stderr=subprocess.PIPE, text=stdout is None)
    if process.returncode:
        raise RuntimeError(
            f"Fallo comando aislado {args[1:3]}: {process.stderr[-3000:]}"
        )


def django(python: Path, source: Path, env: dict[str, str], *args: str) -> None:
    run([str(python), str(source / "manage.py"), *args], cwd=source, env=env)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", type=Path, default=ROOT / ".venv" / "Scripts" / "python.exe")
    parser.add_argument("--baseline-commit", default=BASELINE_COMMIT)
    parser.add_argument("--fresh-only", action="store_true")
    args = parser.parse_args(argv)
    python = args.python.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="tocayos-production1-ensayo-") as temp:
        work = Path(temp)
        old = work / "baseline-dev10"
        old.mkdir()
        archive = work / "baseline.zip"
        with archive.open("wb") as output:
            run(
                ["git", "archive", "--format=zip", args.baseline_commit],
                cwd=ROOT, env=os.environ.copy(), stdout=output,
            )
        with zipfile.ZipFile(archive) as package:
            for entry in package.infolist():
                target = (old / entry.filename).resolve()
                if not target.is_relative_to(old.resolve()):
                    raise RuntimeError("Git archive salió del directorio aislado.")
            package.extractall(old)
        database = work / "runtime" / "db.sqlite3"
        database.parent.mkdir()
        env = os.environ.copy()
        env.update({
            "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
            "DJANGO_DEBUG": "false",
            "DJANGO_ALLOWED_HOSTS": "localhost,127.0.0.1",
            "DJANGO_HTTPS": "false",
            "ALLOW_INSECURE_HTTP_LAN": "true",
            "DB_ENGINE": "sqlite",
            "SQLITE_PATH": str(database),
            "SUCURSAL_CLAVE": "SANTA_ANITA",
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        if args.fresh_only:
            django(python, ROOT, env, "migrate", "--noinput")
            django(
                python, ROOT, env, "aprovisionar_sucursal", "--clave", "SANTA_ANITA",
                "--nombre", "Santa Anita", "--sucursal-id", SANTA_ANITA_ID,
            )
            django(python, ROOT, env, "inicializar_operacion_sucursal")
            django(python, ROOT, env, "configurar_modulos_sucursal", "--iniciales")
            django(python, ROOT, env, "check")
            fresh_check = (
                "from personas.models import Sucursal; "
                "from personas.modulos import modulos_efectivos; "
                "import uuid; "
                f"s=Sucursal.objects.get(id=uuid.UUID('{SANTA_ANITA_ID}')); "
                "assert Sucursal.objects.count() == 1; "
                "m=modulos_efectivos(s); "
                "assert all(m.get(k) for k in "
                "{'pos','catalogo','impresion','respaldos','domicilios','programados','reparto'}); "
                "assert not m.get('pedidos_sucursales')"
            )
            django(python, ROOT, env, "shell", "-c", fresh_check)
            print(json.dumps({
                "status": "ok", "mode": "fresh-current",
                "target_version": (ROOT / "VERSION").read_text(encoding="utf-8").strip(),
                "branch_id": SANTA_ANITA_ID,
                "sqlite_bytes": database.stat().st_size,
                "windows_service_touched": False,
            }, sort_keys=True))
            return 0
        django(python, old, env, "migrate", "--noinput")
        django(
            python, old, env, "aprovisionar_sucursal", "--clave", "SANTA_ANITA",
            "--nombre", "Santa Anita", "--sucursal-id", SANTA_ANITA_ID,
        )
        django(python, old, env, "inicializar_operacion_sucursal")
        marker_code = (
            "from personas.models import Sucursal; "
            "from ventas.models import EventoOutbox; "
            "import uuid; "
            f"s=Sucursal.objects.get(id=uuid.UUID('{SANTA_ANITA_ID}')); "
            f"EventoOutbox.objects.create(id=uuid.UUID('{MARKER_ID}'), sucursal=s, "
            "agregado='ensayo', agregado_id=uuid.uuid4(), tipo='upgrade_lab', "
            "datos={'marcador':'persistente'}, destino='central_ventas_v2', "
            "estado_entrega='pendiente')"
        )
        django(python, old, env, "shell", "-c", marker_code)
        old_bytes = database.stat().st_size
        django(python, ROOT, env, "migrate", "--noinput")
        django(python, ROOT, env, "configurar_modulos_sucursal", "--iniciales")
        verify_code = (
            "from personas.models import Sucursal; "
            "from personas.modulos import modulos_efectivos; "
            "from ventas.models import EventoOutbox; "
            "import uuid; "
            f"s=Sucursal.objects.get(id=uuid.UUID('{SANTA_ANITA_ID}')); "
            f"e=EventoOutbox.objects.get(id=uuid.UUID('{MARKER_ID}')); "
            "assert s.clave == 'SANTA_ANITA' and Sucursal.objects.count() == 1; "
            "assert e.estado_entrega == 'pendiente' and e.datos == {'marcador':'persistente'}; "
            "m=modulos_efectivos(s); "
            "assert all(m.get(k) for k in {'pos','catalogo','impresion','respaldos','domicilios','programados','reparto'}); "
            "assert not m.get('pedidos_sucursales')"
        )
        django(python, ROOT, env, "shell", "-c", verify_code)
        django(python, ROOT, env, "check")
        print(json.dumps({
            "status": "ok",
            "baseline_commit": args.baseline_commit,
            "target_version": (ROOT / "VERSION").read_text(encoding="utf-8").strip(),
            "branch_id": SANTA_ANITA_ID,
            "outbox_id": MARKER_ID,
            "sqlite_before_bytes": old_bytes,
            "sqlite_after_bytes": database.stat().st_size,
            "windows_service_touched": False,
        }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())