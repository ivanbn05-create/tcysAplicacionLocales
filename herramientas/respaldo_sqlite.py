"""Respaldo consistente y verificable de la base SQLite del POS."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any


BACKUP_ARTEFACTO_RE = re.compile(
    r"^db-\d{8}-\d{6}(?:-\d+)?\.sqlite3(?:\.(?:json|sha256))?$"
)


class BackupError(RuntimeError):
    """Error controlado de respaldo, verificacion o retencion."""


def _json_default(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dt.datetime):
        return value.isoformat()
    return str(value)


def _append_json_log(log_path: Path, record: dict[str, Any]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log_file:
        json.dump(record, log_file, ensure_ascii=False, default=_json_default, sort_keys=True)
        log_file.write("\n")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _connect_read_only(path: Path, timeout: float) -> sqlite3.Connection:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=timeout)
    connection.execute("PRAGMA query_only = ON")
    return connection


def _verificar_sqlite(path: Path, timeout: float) -> dict[str, Any]:
    try:
        with closing(_connect_read_only(path, timeout)) as connection:
            integrity_rows = [row[0] for row in connection.execute("PRAGMA integrity_check")]
            if integrity_rows != ["ok"]:
                raise BackupError("PRAGMA integrity_check no devolvio ok.")
            foreign_key_error = connection.execute("PRAGMA foreign_key_check").fetchone()
            if foreign_key_error is not None:
                raise BackupError("PRAGMA foreign_key_check encontro inconsistencias.")
            schema_objects = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table', 'index', 'view', 'trigger')"
            ).fetchone()[0]
            page_count = connection.execute("PRAGMA page_count").fetchone()[0]
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
            user_version = connection.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        raise BackupError("La copia no es una base SQLite valida.") from exc
    return {
        "integrity_check": "ok",
        "foreign_key_check": "ok",
        "page_count": page_count,
        "page_size": page_size,
        "schema_objects": schema_objects,
        "user_version": user_version,
    }


def _comprobar_restauracion(backup_path: Path, backup_root: Path, timeout: float) -> dict[str, Any]:
    restore_probe = backup_root / f".restore-check-{os.getpid()}-{backup_path.stem}.sqlite3"
    try:
        shutil.copy2(backup_path, restore_probe)
        resultado = _verificar_sqlite(restore_probe, timeout)
    finally:
        restore_probe.unlink(missing_ok=True)
    return resultado


def _siguiente_nombre_respaldo(backup_root: Path, now: dt.datetime) -> Path:
    stamp = now.astimezone().strftime("%Y%m%d-%H%M%S")
    candidate = backup_root / f"db-{stamp}.sqlite3"
    if not candidate.exists():
        return candidate
    for suffix in range(1, 1000):
        candidate = backup_root / f"db-{stamp}-{suffix}.sqlite3"
        if not candidate.exists():
            return candidate
    raise BackupError("No fue posible generar un nombre unico para el respaldo.")


def _aplicar_retencion(
    backup_root: Path,
    retention_days: int,
    now: dt.datetime,
    conservar: set[Path],
) -> list[str]:
    cutoff = now.timestamp() - (retention_days * 24 * 60 * 60)
    eliminados: list[str] = []
    for path in backup_root.iterdir():
        if not path.is_file() or path.resolve() in conservar:
            continue
        if not BACKUP_ARTEFACTO_RE.fullmatch(path.name):
            continue
        if path.stat().st_mtime >= cutoff:
            continue
        path.unlink()
        eliminados.append(path.name)
    return eliminados


def realizar_respaldo(
    database_path: Path,
    backup_root: Path,
    log_path: Path,
    retention_days: int,
    timeout: float = 30.0,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Copia la base mediante sqlite3.backup, verifica copia/restauracion y rota."""

    if retention_days < 1:
        raise BackupError("retention_days debe ser mayor o igual a 1.")
    if timeout <= 0:
        raise BackupError("timeout debe ser mayor a cero.")

    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    started_at = dt.datetime.now(dt.timezone.utc)
    database_path = database_path.resolve()
    backup_root = backup_root.resolve()
    log_path = log_path.resolve()

    record_base: dict[str, Any] = {
        "database": str(database_path),
        "retention_days": retention_days,
        "started_at": started_at,
    }
    temp_backup: Path | None = None
    try:
        if not database_path.is_file():
            raise BackupError(f"No existe la base SQLite esperada: {database_path}.")
        backup_root.mkdir(parents=True, exist_ok=True)
        final_backup = _siguiente_nombre_respaldo(backup_root, now)
        temp_backup = backup_root / f".{final_backup.name}.{os.getpid()}.tmp"
        temp_backup.unlink(missing_ok=True)

        with closing(_connect_read_only(database_path, timeout)) as source:
            with closing(sqlite3.connect(temp_backup, timeout=timeout)) as destination:
                source.backup(destination, pages=1024, sleep=0.05)
                destination.commit()

        backup_check = _verificar_sqlite(temp_backup, timeout)
        restore_check = _comprobar_restauracion(temp_backup, backup_root, timeout)
        os.replace(temp_backup, final_backup)
        temp_backup = None
        sha256 = _hash_file(final_backup)
        sha_path = Path(str(final_backup) + ".sha256")
        metadata_path = Path(str(final_backup) + ".json")
        sha_path.write_text(f"{sha256}  {final_backup.name}\n", encoding="ascii")
        conservar = {final_backup.resolve(), sha_path.resolve(), metadata_path.resolve()}
        eliminados = _aplicar_retencion(backup_root, retention_days, now, conservar)

        metadata: dict[str, Any] = {
            **record_base,
            "backup": str(final_backup),
            "backup_file": final_backup.name,
            "backup_root": str(backup_root),
            "finished_at": dt.datetime.now(dt.timezone.utc),
            "size_bytes": final_backup.stat().st_size,
            "sha256": sha256,
            "status": "ok",
            "sqlite_backup_check": backup_check,
            "restore_check": restore_check,
            "retention_deleted": eliminados,
        }
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, default=_json_default, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        _append_json_log(log_path, metadata)
        return metadata
    except Exception as exc:
        if temp_backup is not None:
            temp_backup.unlink(missing_ok=True)
        error_record = {
            **record_base,
            "finished_at": dt.datetime.now(dt.timezone.utc),
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        _append_json_log(log_path, error_record)
        raise


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Respalda runtime/db.sqlite3 con verificacion de integridad."
    )
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--backup-root", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--retention-days", required=True, type=int)
    parser.add_argument("--timeout", default=30.0, type=float)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        metadata = realizar_respaldo(
            database_path=args.database,
            backup_root=args.backup_root,
            log_path=args.log,
            retention_days=args.retention_days,
            timeout=args.timeout,
        )
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": metadata["status"],
                "backup_file": metadata["backup_file"],
                "sha256": metadata["sha256"],
                "size_bytes": metadata["size_bytes"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
