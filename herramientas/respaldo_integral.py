"""Paquete H18 de respaldo integral para una instalación Edge SQLite.

El manifiesto comprueba integridad, no autenticidad criptográfica. El wrapper Windows
protege el paquete y el destino con ACL antes de escribir secretos.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid
from contextlib import closing
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

SCHEMA = 1
MARKER = "H18:ISOLATED\n"
TRUST_KEYS = (
    "CENTRAL_API_CA_BUNDLE",
    "PEDIDOS_API_CA_BUNDLE",
    "PEDIDOS_SUCURSALES_DB_SSLROOTCERT",
)
DB_REL = Path("runtime/db.sqlite3")
RELEASE_TRUST_REL = "trust/release-trust.json"
SKIP_RUNTIME = {"db.sqlite3", "db.sqlite3-wal", "db.sqlite3-shm", "db.sqlite3-journal"}
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class BackupIntegralError(RuntimeError):
    pass


def _fail(message: str) -> None:
    raise BackupIntegralError(message)


def _physical(path: Path, *, directory: bool | None = None) -> None:
    if path.is_symlink():
        _fail("Se encontró un enlace en una ruta del respaldo.")
    try:
        info = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise BackupIntegralError("Falta una ruta requerida del respaldo.") from exc
    if getattr(info, "st_file_attributes", 0) & REPARSE_POINT:
        _fail("Se encontró un punto de reparación en una ruta del respaldo.")
    if directory is True and not stat.S_ISDIR(info.st_mode):
        _fail("Se esperaba un directorio físico.")
    if directory is False and not stat.S_ISREG(info.st_mode):
        _fail("Se esperaba un archivo físico.")


def _physical_parents(path: Path, stop: Path) -> None:
    current = path
    while True:
        if current.exists() or current.is_symlink():
            _physical(current)
        if current == stop:
            break
        if stop not in current.parents:
            _fail("La ruta sale de la raíz autorizada.")
        current = current.parent


def _relative(value: str) -> Path:
    if not isinstance(value, str) or "\\" in value:
        _fail("Ruta relativa inválida en el manifiesto.")
    raw_parts = value.split("/")
    if not raw_parts or any(
        part in {"", ".", ".."} or part.rstrip(" .") != part
        or re.search(r'[<>:"|?*]', part)
        or part.split(".")[0].upper() in {
            "CON", "PRN", "AUX", "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }
        for part in raw_parts
    ):
        _fail("Ruta relativa inválida en el manifiesto.")
    posix = PurePosixPath(value)
    if posix.is_absolute():
        _fail("Ruta relativa inválida en el manifiesto.")
    return Path(*raw_parts)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _entry(root: Path, path: Path) -> dict[str, object]:
    _physical(path, directory=False)
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": _hash(path),
    }


def _walk(root: Path):
    if not root.exists():
        return
    _physical(root, directory=True)
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        _physical(current_path, directory=True)
        for name in sorted(dirs):
            _physical(current_path / name, directory=True)
        for name in sorted(files):
            path = current_path / name
            _physical(path, directory=False)
            yield path


def _sqlite_check(path: Path) -> dict[str, object]:
    _physical(path, directory=False)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if result != ("ok",):
            _fail("SQLite no supera integrity_check.")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            _fail("SQLite no supera foreign_key_check.")
        tables = [
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        counts = {}
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            counts[table] = connection.execute(f"SELECT count(*) FROM {quoted}").fetchone()[0]
    return {"integrity_check": "ok", "foreign_key_errors": 0, "table_counts": counts}


def _dotenv(path: Path) -> tuple[str, dict[str, str]]:
    _physical(path, directory=False)
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise BackupIntegralError(".env no es UTF-8 válido.") from exc
    values: dict[str, str] = {}
    for line in content.splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if match:
            if match[1] in values:
                _fail(".env contiene claves duplicadas.")
            values[match[1]] = match[2]
    if values.get("DB_ENGINE", "sqlite").strip().lower() != "sqlite":
        _fail("H18 requiere DB_ENGINE=sqlite.")
    return content, values


def _copy_file(source: Path, target: Path) -> None:
    _physical(source, directory=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _copy_tree(source: Path, destination: Path, *, skip: set[str] | None = None) -> None:
    for file in _walk(source):
        relative = file.relative_to(source)
        if skip and len(relative.parts) == 1 and relative.name in skip:
            continue
        _copy_file(file, destination / relative)


def _source_db(root: Path, values: dict[str, str]) -> Path:
    raw = values.get("SQLITE_PATH", "").strip()
    if not raw:
        _fail("SQLITE_PATH falta en .env.")
    configured = Path(raw)
    if not configured.is_absolute():
        configured = root / configured
    _physical_parents(root / DB_REL, root)
    _physical(root / DB_REL, directory=False)
    _physical(configured, directory=False)
    configured = configured.resolve(strict=True)
    expected = (root / DB_REL).resolve(strict=True)
    if configured != expected:
        _fail("H18 exige que SQLITE_PATH apunte a runtime/db.sqlite3.")
    return expected


def _release_trust(path: Path) -> None:
    _physical(path, directory=False)
    if path.stat().st_size > 65536:
        _fail("Trust store de release demasiado grande.")
    try:
        trust = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BackupIntegralError("Trust store de release inválido.") from exc
    if type(trust) is not dict or set(trust) != {"schema_version", "keys"} or trust["schema_version"] != 1:
        _fail("Contrato de trust store de release desconocido.")
    keys = trust["keys"]
    if type(keys) is not list or not keys:
        _fail("Trust store de release sin claves públicas.")
    seen = set()
    for key in keys:
        if type(key) is not dict or set(key) != {"key_id", "public_xml", "status"}:
            _fail("Entrada de trust store de release inválida.")
        public_xml = key["public_xml"]
        key_id = key["key_id"]
        if type(public_xml) is not str or type(key_id) is not str or type(key["status"]) is not str:
            _fail("Entrada de trust store de release inválida.")
        if key["status"] not in {"trusted", "revoked"} or not re.fullmatch(r"[0-9a-f]{64}", key_id):
            _fail("Estado o identidad de clave pública inválido.")
        if key_id in seen or hashlib.sha256(public_xml.encode("utf-8")).hexdigest() != key_id:
            _fail("Identidad de clave pública discordante.")
        seen.add(key_id)
        try:
            root = ElementTree.fromstring(public_xml)
            children = list(root)
            if root.tag != "RSAKeyValue" or [item.tag for item in children] != ["Modulus", "Exponent"]:
                _fail("Trust store contiene material distinto de clave pública RSA.")
            if any(list(item) or item.attrib or not item.text for item in children):
                _fail("Clave pública RSA inválida.")
            for item in children:
                base64.b64decode(item.text, validate=True)
        except (ElementTree.ParseError, ValueError, binascii.Error) as exc:
            raise BackupIntegralError("XML de clave pública inválido.") from exc


def create(source_root: Path, output_root: Path, release_trust_path: Path | None = None) -> dict[str, object]:
    _physical(source_root, directory=True)
    _physical(output_root, directory=True)
    source_root = source_root.resolve(strict=True)
    output_root = output_root.resolve(strict=True)
    for excluded in ("runtime", "certs", "media"):
        path = source_root / excluded
        if output_root == path or path in output_root.parents:
            _fail("El destino del paquete no puede estar dentro de los datos capturados.")
    version_path = source_root / "VERSION"
    _physical(version_path, directory=False)
    release_version = version_path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}", release_version):
        _fail("VERSION de la instalación no es válida.")
    env_path = source_root / ".env"
    _, values = _dotenv(env_path)
    env_hash = _hash(env_path)
    database = _source_db(source_root, values)
    if release_trust_path is None:
        if release_version.split(".")[0] == "1":
            _fail("Release 1.x requiere trust store de firmas externo.")
    else:
        if not release_trust_path.is_absolute():
            _fail("Trust store de release exige ruta absoluta.")
        _physical(release_trust_path, directory=False)
        release_trust_path = release_trust_path.resolve(strict=True)
        if release_trust_path == source_root or source_root in release_trust_path.parents:
            _fail("Trust store de release debe ser externo a la instalación.")
        _release_trust(release_trust_path)
    stage = Path(tempfile.mkdtemp(prefix=".h18-pendiente-", dir=output_root))
    try:
        _copy_file(env_path, stage / ".env")
        if _hash(stage / ".env") != env_hash:
            _fail(".env cambió durante su copia.")
        for folder in ("runtime", "certs", "media"):
            _copy_tree(
                source_root / folder, stage / folder,
                skip=SKIP_RUNTIME if folder == "runtime" else None,
            )
        db_copy = stage / DB_REL
        db_copy.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(db_copy)) as target:
                source.backup(target)
                target.commit()
        sqlite_result = _sqlite_check(db_copy)
        trust: dict[str, str] = {}
        for key in TRUST_KEYS:
            raw = values.get(key, "").strip().strip('"').strip("'")
            if not raw:
                continue
            file = Path(raw)
            if not file.is_absolute():
                file = source_root / file
            _physical(file, directory=False)
            file = file.resolve(strict=True)
            relative = f"trust/{key}.pem"
            _copy_file(file, stage / _relative(relative))
            trust[key] = relative
        if release_trust_path is not None:
            source_trust_hash = _hash(release_trust_path)
            _copy_file(release_trust_path, stage / _relative(RELEASE_TRUST_REL))
            if _hash(stage / _relative(RELEASE_TRUST_REL)) != source_trust_hash:
                _fail("Trust store de release cambió durante su copia.")
        if _hash(env_path) != env_hash:
            _fail(".env cambió durante el respaldo.")
        files = [_entry(stage, file) for file in _walk(stage)]
        names = [item["path"] for item in files]
        if len(names) != len(set(name.lower() for name in names)):
            _fail("El paquete contiene nombres de archivo ambiguos.")
        manifest = {
            "schema": SCHEMA,
            "release_version": release_version,
            "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "sqlite": sqlite_result,
            "trust": trust,
            "release_trust": RELEASE_TRUST_REL if release_trust_path is not None else None,
            "files": sorted(files, key=lambda item: item["path"]),
        }
        (stage / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        name = "h18-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        published = output_root / name
        stage.rename(published)
        return {
            "status": "ok", "bundle": str(published),
            "files": len(files), "sqlite_sha256": _hash(published / DB_REL),
        }
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def verify(bundle: Path) -> dict[str, object]:
    _physical(bundle, directory=True)
    bundle = bundle.resolve(strict=True)
    manifest_path = bundle / "manifest.json"
    _physical(manifest_path, directory=False)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BackupIntegralError("Manifiesto H18 inválido.") from exc
    if type(manifest) is not dict or manifest.get("schema") != SCHEMA:
        _fail("Versión de manifiesto H18 no soportada.")
    release_version = manifest.get("release_version")
    if type(release_version) is not str or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}", release_version
    ):
        _fail("VERSION de release H18 inválida.")
    entries = manifest.get("files")
    if type(entries) is not list or not entries:
        _fail("Manifiesto H18 sin archivos.")
    seen = set()
    expected = {"manifest.json"}
    for item in entries:
        if type(item) is not dict or set(item) != {"path", "bytes", "sha256"}:
            _fail("Entrada de manifiesto H18 inválida.")
        relative = _relative(item["path"])
        key = relative.as_posix().lower()
        if key in seen or key == "manifest.json":
            _fail("Ruta duplicada en manifiesto H18.")
        seen.add(key)
        expected.add(relative.as_posix())
        file = bundle / relative
        _physical_parents(file, bundle)
        _physical(file, directory=False)
        if type(item["bytes"]) is not int or item["bytes"] < 0 or file.stat().st_size != item["bytes"]:
            _fail("Tamaño de archivo H18 discordante.")
        if type(item["sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            _fail("SHA-256 H18 inválido.")
        if _hash(file) != item["sha256"]:
            _fail("SHA-256 H18 discordante.")
    actual = {file.relative_to(bundle).as_posix() for file in _walk(bundle)}
    if actual != expected:
        _fail("El paquete H18 tiene archivos faltantes o ajenos.")
    if ".env" not in expected or DB_REL.as_posix() not in expected:
        _fail("Paquete H18 incompleto.")
    trust = manifest.get("trust")
    if type(trust) is not dict or not set(trust) <= set(TRUST_KEYS):
        _fail("Índice de confianza H18 inválido.")
    for key, value in trust.items():
        if value != f"trust/{key}.pem" or value not in expected:
            _fail("Archivo de confianza H18 inválido.")
    release_trust = manifest.get("release_trust")
    if release_trust not in (None, RELEASE_TRUST_REL):
        _fail("Índice de trust store de release inválido.")
    if release_version.split(".")[0] == "1" and release_trust is None:
        _fail("Release 1.x requiere trust store de firmas externo.")
    if release_trust is not None:
        if release_trust not in expected:
            _fail("Trust store de release faltante.")
        _release_trust(bundle / _relative(RELEASE_TRUST_REL))
    _, env_values = _dotenv(bundle / ".env")
    configured_trust = {key for key in TRUST_KEYS if env_values.get(key, "").strip()}
    if configured_trust != set(trust):
        _fail("El índice de confianza no cubre .env.")
    sqlite_result = _sqlite_check(bundle / DB_REL)
    if sqlite_result != manifest.get("sqlite"):
        _fail("El estado SQLite H18 difiere del manifiesto.")
    return manifest


def _rewrite_env(content: str, trust: dict[str, str]) -> str:
    replacements = {"SQLITE_PATH": DB_REL.as_posix()}
    replacements.update({key: f"certs/h18/{key}.pem" for key in trust})
    lines = content.splitlines(keepends=True)
    present = set()
    for index, line in enumerate(lines):
        match = re.match(r"^([A-Z][A-Z0-9_]*)=(.*?)(\r?\n)?$", line)
        if match and match[1] in replacements:
            key = match[1]
            if key in present:
                _fail(".env contiene claves duplicadas en restore.")
            present.add(key)
            lines[index] = key + "=" + replacements[key] + (match[3] or "")
    if present != set(replacements):
        _fail(".env carece de una ruta necesaria para restore.")
    return "".join(lines)


def restore(
    bundle: Path, target_root: Path, release_trust_path: Path | None = None
) -> dict[str, object]:
    manifest = verify(bundle)
    _physical(bundle, directory=True)
    _physical(target_root, directory=True)
    bundle = bundle.resolve(strict=True)
    target_root = target_root.resolve(strict=True)
    if target_root == bundle or target_root in bundle.parents or bundle in target_root.parents:
        _fail("Restore H18 exige un destino ajeno al paquete.")
    release_destination = None
    if manifest["release_trust"] is not None:
        if release_trust_path is None or not release_trust_path.is_absolute():
            _fail("Restore requiere ruta absoluta externa de trust store.")
        release_destination = release_trust_path
        parent = release_destination.parent
        _physical(parent, directory=True)
        _physical_parents(parent, Path(parent.anchor))
        parent = parent.resolve(strict=True)
        release_destination = parent / release_destination.name
        if (release_destination.exists() or release_destination.is_symlink()
            or target_root == parent or target_root in parent.parents
            or parent in target_root.parents or bundle == parent or bundle in parent.parents
            or parent in bundle.parents):
            _fail("Destino de trust store de release no es externo y nuevo.")
    marker = target_root / ".h18-restauracion-aislada"
    _physical(marker, directory=False)
    if marker.read_text(encoding="utf-8") != MARKER:
        _fail("Falta la marca exacta de instalación aislada.")
    for required in ("manage.py", "VERSION"):
        _physical(target_root / required, directory=False)
    target_version = (target_root / "VERSION").read_text(encoding="utf-8").strip()
    if target_version != manifest["release_version"]:
        _fail("La release de destino no coincide con el respaldo.")
    files = manifest["files"]
    target_files: dict[str, Path] = {}
    for item in files:
        relative = _relative(item["path"])
        if item["path"] == RELEASE_TRUST_REL:
            continue
        if relative.parts[0] == "trust":
            if len(relative.parts) != 2:
                _fail("Ruta de confianza H18 inválida.")
            destination = target_root / "certs" / "h18" / relative.name
        else:
            destination = target_root / relative
        _physical_parents(destination.parent, target_root)
        if destination.exists() or destination.is_symlink():
            _physical(destination, directory=False)
            if relative.parts[0] == "certs" and _hash(destination) == item["sha256"]:
                continue
            _fail("Restore H18 no sobrescribe datos existentes.")
        target_files[item["path"]] = destination
    stage = Path(tempfile.mkdtemp(prefix=".h18-stage-", dir=target_root))
    created: list[Path] = []
    try:
        # Cada byte usado se contrasta con el manifiesto después de verify().
        # El staging completo se valida antes de publicar un solo archivo.
        for item in files:
            name = item["path"]
            if name == RELEASE_TRUST_REL and release_destination is not None:
                staged = stage / "release-trust-pending.json"
            elif name in target_files:
                staged = stage / _relative(name)
            else:
                continue
            source = bundle / _relative(name)
            _physical(source, directory=False)
            if _hash(source) != item["sha256"]:
                _fail("El paquete H18 cambió después de verify.")
            _copy_file(source, staged)
            if _hash(staged) != item["sha256"]:
                _fail("El archivo en staging H18 difiere del manifiesto.")
            if name == ".env":
                env_text, _ = _dotenv(staged)
                restored_env = _rewrite_env(env_text, manifest["trust"])
                expected_env_hash = hashlib.sha256(restored_env.encode("utf-8")).hexdigest()
                staged.write_bytes(restored_env.encode("utf-8"))
                if _hash(staged) != expected_env_hash:
                    _fail(".env reescrito difiere del staging esperado.")
        for name, destination in target_files.items():
            staged = stage / _relative(name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            staged.replace(destination)
            created.append(destination)
        if release_destination is not None:
            staged_release = stage / "release-trust-pending.json"
            staged_release.replace(release_destination)
            created.append(release_destination)
        result = _sqlite_check(target_root / DB_REL)
        if result != manifest["sqlite"]:
            _fail("SQLite restaurada no coincide con el manifiesto.")
        return {
            "status": "ok", "target": str(target_root),
            "files": len(target_files) + int(release_destination is not None),
            "sqlite_sha256": _hash(target_root / DB_REL),
        }
    except BaseException:
        for path in reversed(created):
            try:
                path.unlink()
            except OSError:
                pass
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paquete integral H18 para Edge SQLite.")
    commands = parser.add_subparsers(dest="action", required=True)
    create_parser = commands.add_parser("create")
    create_parser.add_argument("--source-root", required=True, type=Path)
    create_parser.add_argument("--output-root", required=True, type=Path)
    create_parser.add_argument("--release-trust-path", type=Path)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--bundle", required=True, type=Path)
    restore_parser = commands.add_parser("restore")
    restore_parser.add_argument("--bundle", required=True, type=Path)
    restore_parser.add_argument("--target-root", required=True, type=Path)
    restore_parser.add_argument("--release-trust-path", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "create":
            result = create(args.source_root, args.output_root, args.release_trust_path)
        elif args.action == "verify":
            manifest = verify(args.bundle)
            result = {
                "status": "ok", "files": len(manifest["files"]),
                "sqlite_sha256": next(
                    item["sha256"] for item in manifest["files"]
                    if item["path"] == DB_REL.as_posix()
                ),
            }
        else:
            result = restore(args.bundle, args.target_root, args.release_trust_path)
    except (BackupIntegralError, OSError, sqlite3.Error, ValueError) as exc:
        # No mostrar mensajes de OSError ni rutas que puedan incluir secretos.
        print(json.dumps({"status": "error", "reason": type(exc).__name__}))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
