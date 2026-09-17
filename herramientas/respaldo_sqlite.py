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
import stat
import sys
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


BACKUP_ARTEFACTO_RE = re.compile(
    r"^(?P<backup>db-\d{8}-\d{6}(?:-\d+)?\.sqlite3)"
    r"(?P<sidecar>\.(?:json|sha256))?$"
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


def _write_text_atomic(path: Path, content: str, *, encoding: str) -> None:
    """Publica un sidecar completo o no lo publica."""

    temp_path = path.parent / (
        f".{path.name}.{os.getpid()}-{os.urandom(8).hex()}.tmp"
    )
    try:
        with temp_path.open("x", encoding=encoding, newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


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
    grupos: dict[str, list[tuple[Path, os.stat_result]]] = {}
    for path in backup_root.iterdir():
        match = BACKUP_ARTEFACTO_RE.fullmatch(path.name)
        if match is None:
            continue
        try:
            path_stat = path.lstat()
        except FileNotFoundError:
            continue
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if stat.S_ISLNK(path_stat.st_mode) or (
            getattr(path_stat, "st_file_attributes", 0) & reparse_flag
        ):
            raise BackupError(
                f"No se aplica retencion sobre enlaces o junctions: {path.name}."
            )
        if not stat.S_ISREG(path_stat.st_mode):
            continue
        grupos.setdefault(match.group("backup"), []).append((path, path_stat))

    eliminados: list[str] = []
    for group_name in sorted(grupos):
        group = grupos[group_name]
        if any(path in conservar for path, _ in group):
            continue
        # Se conserva el triplete completo si cualquiera de sus piezas sigue
        # dentro de retencion; nunca se separan DB, JSON y SHA por su mtime.
        if max(path_stat.st_mtime for _, path_stat in group) >= cutoff:
            continue
        for path, _ in sorted(group, key=lambda item: item[0].name):
            path.unlink()
            eliminados.append(path.name)
    return eliminados


SOLICITUD_PURGA_RE = re.compile(
    r"^purga-(?P<id>[0-9a-fA-F-]{36})\.json$"
)
BACKUP_NOMBRE_RE = re.compile(
    r"^db-(?P<fecha>\d{8})-(?P<hora>\d{6})(?:-(?P<sufijo>\d+))?\.sqlite3$"
)



def _es_reparse(path: Path) -> bool:
    info = path.lstat()
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & reparse_flag
    )


def _absoluta_sin_resolver(path: Path) -> Path:
    """Normaliza léxicamente sin seguir enlaces ni junctions."""

    return Path(os.path.abspath(os.fspath(path)))


def _validar_directorio_fisico(
    path: Path,
    *,
    etiqueta: str,
    permitir_inexistente: bool = False,
) -> Path:
    """Valida la ruta original y todos sus padres antes de cualquier resolve()."""

    original = _absoluta_sin_resolver(path)
    info_raiz: os.stat_result | None = None
    for candidato in (original, *original.parents):
        try:
            info = candidato.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise BackupError(f"No se pudo inspeccionar {etiqueta}: {candidato}.") from exc
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0) & reparse_flag
        ):
            raise BackupError(
                f"{etiqueta} o uno de sus padres es un enlace o junction: {candidato}."
            )
        if candidato == original:
            info_raiz = info
    if info_raiz is None:
        if permitir_inexistente:
            return original
        raise BackupError(f"No existe {etiqueta}: {original}.")
    if not stat.S_ISDIR(info_raiz.st_mode):
        raise BackupError(f"{etiqueta} no es una carpeta física: {original}.")
    return original


def _archivo_directo_seguro(path: Path, root: Path) -> os.stat_result:
    root = _validar_directorio_fisico(root, etiqueta="la raíz protegida")
    path = _absoluta_sin_resolver(path)
    if path.parent != root:
        raise BackupError(f"El archivo sale de su raíz: {path.name}.")
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise BackupError(f"Falta el archivo físico esperado: {path.name}.") from exc
    if _es_reparse(path) or not stat.S_ISREG(info.st_mode):
        raise BackupError(f"El artefacto no es un archivo regular directo: {path.name}.")
    # Sólo después del lstat de la ruta original se permite resolver como
    # verificación redundante de confinamiento.
    if path.resolve(strict=True).parent != root.resolve(strict=True):
        raise BackupError(f"El archivo sale de su raíz o es un enlace: {path.name}.")
    return info

def _verificar_triplete(backup_path: Path, backup_root: Path, timeout: float) -> None:
    info = _archivo_directo_seguro(backup_path, backup_root)
    sha_path = Path(str(backup_path) + ".sha256")
    metadata_path = Path(str(backup_path) + ".json")
    _archivo_directo_seguro(sha_path, backup_root)
    _archivo_directo_seguro(metadata_path, backup_root)
    digest = _hash_file(backup_path)
    esperado = f"{digest}  {backup_path.name}\n"
    try:
        sha_text = sha_path.read_text(encoding="ascii")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError(f"El triplete no tiene sidecars válidos: {backup_path.name}.") from exc
    if sha_text != esperado:
        raise BackupError(f"El SHA-256 lateral no coincide: {backup_path.name}.")
    if (
        not isinstance(metadata, dict)
        or metadata.get("status") != "ok"
        or metadata.get("backup_file") != backup_path.name
        or metadata.get("sha256") != digest
        or int(metadata.get("size_bytes") or -1) != info.st_size
        or (metadata.get("sqlite_backup_check") or {}).get("integrity_check") != "ok"
        or (metadata.get("restore_check") or {}).get("integrity_check") != "ok"
    ):
        raise BackupError(f"El JSON lateral no acredita el triplete: {backup_path.name}.")
    _verificar_sqlite(backup_path, timeout)


def _clave_nombre_respaldo(path: Path) -> tuple[str, str, int]:
    match = BACKUP_NOMBRE_RE.fullmatch(path.name)
    if match is None:
        raise BackupError(f"Nombre de respaldo no admitido: {path.name}.")
    return (
        match.group("fecha"),
        match.group("hora"),
        int(match.group("sufijo") or 0),
    )


def _tabla_existe(connection: sqlite3.Connection, tabla: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (tabla,),
    ).fetchone() is not None


def _uuid_sqlite(valor: str) -> str:
    try:
        return uuid.UUID(str(valor)).hex
    except (ValueError, TypeError) as exc:
        raise BackupError("La solicitud contiene un UUID inválido.") from exc


def _mes_siguiente_texto(periodo: dt.date) -> dt.date:
    if periodo.month == 12:
        return dt.date(periodo.year + 1, 1, 1)
    return dt.date(periodo.year, periodo.month + 1, 1)


def _rango_utc(periodo: dt.date) -> tuple[str, str]:
    zona = ZoneInfo("America/Mexico_City")
    inicio = dt.datetime.combine(periodo, dt.time.min, tzinfo=zona).astimezone(
        dt.timezone.utc
    )
    fin = dt.datetime.combine(
        _mes_siguiente_texto(periodo),
        dt.time.min,
        tzinfo=zona,
    ).astimezone(dt.timezone.utc)
    formato = "%Y-%m-%d %H:%M:%S"
    return inicio.replace(tzinfo=None).strftime(formato), fin.replace(
        tzinfo=None
    ).strftime(formato)


def _contiene_detalle_periodo(
    connection: sqlite3.Connection,
    sucursal_id: str,
    periodo: dt.date,
) -> bool:
    sucursal = _uuid_sqlite(sucursal_id)
    necesarias = {
        "ventas_ticket",
        "ventas_movimientocaja",
        "ventas_controlefectivodia",
        "ventas_cortecaja",
        "ventas_reporteadministrativo",
        "ventas_cortesucursal",
        "ventas_liquidacionrepartidor",
        "impresion_trabajoimpresion",
    }
    faltantes = sorted(tabla for tabla in necesarias if not _tabla_existe(connection, tabla))
    if faltantes:
        raise BackupError(
            "El respaldo no permite acreditar la ausencia de detalle; faltan: "
            + ", ".join(faltantes)
        )
    columnas_ticket = {
        fila[1] for fila in connection.execute("PRAGMA table_info(ventas_ticket)")
    }
    if "activado_programado_en" not in columnas_ticket:
        raise BackupError(
            "El respaldo no contiene activado_programado_en para fechar tickets."
        )
    inicio_utc, fin_utc = _rango_utc(periodo)
    siguiente = _mes_siguiente_texto(periodo).isoformat()
    consultas = (
        (
            """
            SELECT 1 FROM ventas_ticket
            WHERE sucursal_id=? AND estado<>'programado'
              AND COALESCE(activado_programado_en, creado_en)>=?
              AND COALESCE(activado_programado_en, creado_en)<?
            LIMIT 1
            """,
            (sucursal, inicio_utc, fin_utc),
        ),
        (
            """
            SELECT 1 FROM ventas_movimientocaja
            WHERE sucursal_id=? AND creado_en>=? AND creado_en<?
            LIMIT 1
            """,
            (sucursal, inicio_utc, fin_utc),
        ),
        (
            """
            SELECT 1 FROM ventas_controlefectivodia
            WHERE sucursal_id=? AND fecha>=? AND fecha<?
            LIMIT 1
            """,
            (sucursal, periodo.isoformat(), siguiente),
        ),
        (
            """
            SELECT 1 FROM ventas_cortecaja
            WHERE sucursal_id=? AND (
                (inicio IS NOT NULL AND inicio>=? AND inicio<?)
                OR (inicio IS NULL AND fin>=? AND fin<?)
            )
            LIMIT 1
            """,
            (sucursal, inicio_utc, fin_utc, inicio_utc, fin_utc),
        ),
        (
            """
            SELECT 1 FROM ventas_reporteadministrativo
            WHERE sucursal_id=? AND creado_en>=? AND creado_en<?
            LIMIT 1
            """,
            (sucursal, inicio_utc, fin_utc),
        ),
        (
            """
            SELECT 1 FROM ventas_cortesucursal
            WHERE sucursal_id=? AND creado_en>=? AND creado_en<?
            LIMIT 1
            """,
            (sucursal, inicio_utc, fin_utc),
        ),
        (
            """
            SELECT 1 FROM ventas_liquidacionrepartidor
            WHERE sucursal_id=? AND creado_en>=? AND creado_en<?
            LIMIT 1
            """,
            (sucursal, inicio_utc, fin_utc),
        ),
        (
            """
            SELECT 1 FROM impresion_trabajoimpresion
            WHERE sucursal_id=? AND creado_en>=? AND creado_en<?
            LIMIT 1
            """,
            (sucursal, inicio_utc, fin_utc),
        ),
    )
    return any(connection.execute(sql, parametros).fetchone() for sql, parametros in consultas)


def _validar_consolidacion_sqlite(
    connection: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    estados: tuple[str, ...],
) -> None:
    if not _tabla_existe(connection, "ventas_consolidacionmensual"):
        raise BackupError("La base no contiene el registro de consolidación.")
    referencia = _uuid_sqlite(payload["referencia_id"])
    sucursal = _uuid_sqlite(payload["sucursal_id"])
    marcadores = ",".join("?" for _ in estados)
    fila = connection.execute(
        f"""
        SELECT periodo, estado, acuse_vps, confirmado_en, purgado_en
        FROM ventas_consolidacionmensual
        WHERE id=? AND sucursal_id=? AND estado IN ({marcadores})
        """,
        (referencia, sucursal, *estados),
    ).fetchone()
    if (
        fila is None
        or str(fila[0])[:10] != payload["periodo"]
        or not str(fila[2] or "").strip()
        or not fila[3]
        or not fila[4]
    ):
        raise BackupError("La base no acredita una consolidación confirmada y acotada.")


def _validar_triplete_post_purga(
    backup_path: Path,
    backup_root: Path,
    payload: dict[str, Any],
    timeout: float,
) -> None:
    _verificar_triplete(backup_path, backup_root, timeout)
    periodo = dt.date.fromisoformat(payload["periodo"])
    with closing(_connect_read_only(backup_path, timeout)) as connection:
        _validar_consolidacion_sqlite(
            connection,
            payload,
            estados=("confirmada", "purgada"),
        )
        if _contiene_detalle_periodo(
            connection,
            payload["sucursal_id"],
            periodo,
        ):
            raise BackupError("El respaldo nuevo todavía contiene detalle del periodo.")


def _depurar_respaldos_periodo(
    backup_root: Path,
    nuevo_backup: Path,
    payload: dict[str, Any],
    timeout: float,
) -> list[str]:
    backup_root = backup_root.resolve()
    nuevo_backup = nuevo_backup.resolve()
    if nuevo_backup.parent != backup_root:
        raise BackupError("El respaldo nuevo queda fuera de la raíz protegida.")
    _validar_triplete_post_purga(
        nuevo_backup,
        backup_root,
        payload,
        timeout,
    )
    _clave_nombre_respaldo(nuevo_backup)
    periodo = dt.date.fromisoformat(payload["periodo"])
    eliminados: list[str] = []
    for candidato in sorted(backup_root.glob("db-*.sqlite3")):
        if candidato == nuevo_backup:
            continue
        # El reloj/nombre no acredita antigüedad. Se inspecciona el contenido
        # de cada triplete salvo el post-purga designado.
        _clave_nombre_respaldo(candidato)
        # Nunca se borra una base aislada: primero debe acreditar su triplete.
        _verificar_triplete(candidato, backup_root, timeout)
        with closing(_connect_read_only(candidato, timeout)) as connection:
            contiene = _contiene_detalle_periodo(
                connection,
                payload["sucursal_id"],
                periodo,
            )
        if not contiene:
            continue
        trio = (
            candidato,
            Path(str(candidato) + ".sha256"),
            Path(str(candidato) + ".json"),
        )
        # Se elimina primero la base sensible. Si un sidecar falla, la solicitud
        # permanece para intervención/reintento y nunca se declara completada.
        for pieza in trio:
            pieza.unlink()
            eliminados.append(pieza.name)
    return eliminados



def _cargar_solicitudes(
    request_root: Path,
) -> tuple[list[tuple[Path, dict[str, Any]]], list[str]]:
    request_root = _validar_directorio_fisico(
        request_root,
        etiqueta="la raíz de solicitudes",
        permitir_inexistente=True,
    )
    try:
        request_root.lstat()
    except FileNotFoundError:
        return [], []

    solicitudes: list[tuple[Path, dict[str, Any]]] = []
    errores: list[str] = []
    for path in sorted(request_root.iterdir()):
        match = SOLICITUD_PURGA_RE.fullmatch(path.name)
        if match is None:
            continue
        try:
            _archivo_directo_seguro(path, request_root)
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(payload, dict)
                or payload.get("version") != 1
                or payload.get("id") != match.group("id")
            ):
                raise BackupError(f"Solicitud fuera de contrato: {path.name}.")
            _uuid_sqlite(payload.get("id"))
            _uuid_sqlite(payload.get("referencia_id"))
        except (
            BackupError,
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            errores.append(f"{path.name}: {type(exc).__name__}: {exc}")
            continue
        solicitudes.append((path, payload))
    return solicitudes, errores


def _registrar_error_solicitud(path: Path, payload: dict[str, Any], exc: Exception) -> None:
    actualizado = dict(payload)
    actualizado["intentos"] = int(actualizado.get("intentos") or 0) + 1
    actualizado["ultimo_error"] = f"{type(exc).__name__}: {exc}"[:500]
    actualizado["ultimo_intento_en"] = dt.datetime.now(dt.timezone.utc).isoformat()
    _write_text_atomic(
        path,
        json.dumps(actualizado, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _registrar_respaldo_descartado(
    path: Path,
    payload: dict[str, Any],
    nombre: str,
    exc: Exception,
) -> dict[str, Any]:
    actualizado = dict(payload)
    historial = actualizado.get("respaldos_post_purga_descartados")
    if not isinstance(historial, list):
        historial = []
    historial = [
        *historial[-9:],
        {
            "nombre": nombre[:100],
            "descartado_en": dt.datetime.now(dt.timezone.utc).isoformat(),
            "motivo": f"{type(exc).__name__}: {exc}"[:300],
        },
    ]
    actualizado["respaldos_post_purga_descartados"] = historial
    actualizado.pop("respaldo_post_purga", None)
    actualizado.pop("respaldo_asignado_en", None)
    _write_text_atomic(
        path,
        json.dumps(actualizado, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return actualizado


def _tripletes_asignados_pendientes(
    request_root: Path,
    backup_root: Path,
) -> set[Path]:
    solicitudes, _ = _cargar_solicitudes(request_root)
    conservar: set[Path] = set()
    for _, payload in solicitudes:
        if payload.get("tipo") != "respaldos_periodo":
            continue
        nombre = str(payload.get("respaldo_post_purga") or "")
        if BACKUP_NOMBRE_RE.fullmatch(nombre) is None:
            continue
        base = backup_root / nombre
        conservar.update(
            {
                base,
                Path(str(base) + ".sha256"),
                Path(str(base) + ".json"),
            }
        )
    return conservar

def _validar_referencia_archivos(
    connection: sqlite3.Connection,
    payload: dict[str, Any],
) -> None:
    referencia = _uuid_sqlite(payload["referencia_id"])
    tipo = payload.get("referencia_tipo")
    if tipo == "corte":
        if not _tabla_existe(connection, "ventas_cortecaja"):
            raise BackupError("La base no contiene cortes de caja.")
        fila = connection.execute(
            "SELECT detalle_eliminado_en FROM ventas_cortecaja WHERE id=?",
            (referencia,),
        ).fetchone()
        if fila is None or not fila[0]:
            raise BackupError("El corte aún no acredita la purga lógica.")
        return
    if tipo == "consolidacion":
        if not _tabla_existe(connection, "ventas_consolidacionmensual"):
            raise BackupError("La base no contiene consolidaciones.")
        fila = connection.execute(
            """
            SELECT estado, acuse_vps, confirmado_en, purgado_en
            FROM ventas_consolidacionmensual WHERE id=?
            """,
            (referencia,),
        ).fetchone()
        if (
            fila is None
            or fila[0] not in {"confirmada", "purgada"}
            or not str(fila[1] or "").strip()
            or not fila[2]
            or not fila[3]
        ):
            raise BackupError("La consolidación aún no acredita la purga lógica.")
        return
    raise BackupError("La solicitud usa una referencia no admitida.")



def _ruta_media_segura(media_root: Path, relativa: str) -> Path:
    if not isinstance(relativa, str):
        raise BackupError("La ruta de impresión no es texto.")
    normalizada = relativa.strip().replace("\\", "/")
    partes = tuple(parte for parte in normalizada.split("/") if parte)
    if (
        not partes
        or normalizada.startswith("/")
        or any(parte in {".", ".."} for parte in partes)
        or ":" in partes[0]
        or "/".join(partes) != normalizada
    ):
        raise BackupError("La ruta de impresión no es relativa y canónica.")

    raiz = _validar_directorio_fisico(
        media_root,
        etiqueta="MEDIA_ROOT",
    )
    candidato = raiz.joinpath(*partes)
    if candidato.parent == candidato:
        raise BackupError("La ruta de impresión no es acotada.")

    # Se inspecciona cada componente léxico antes de resolver. Esto conserva la
    # evidencia de un reparse point incluso si apunta dentro o fuera de la raíz.
    actual = raiz
    for parte in partes:
        actual = actual / parte
        try:
            info = actual.lstat()
        except FileNotFoundError:
            continue
        if _es_reparse(actual):
            raise BackupError("No se borran enlaces ni puntos de reanálisis.")
        if actual == candidato and not stat.S_ISREG(info.st_mode):
            raise BackupError("La ruta de impresión no es un archivo físico.")

    raiz_resuelta = raiz.resolve(strict=True)
    resuelto = candidato.resolve(strict=False)
    try:
        dentro = resuelto.is_relative_to(raiz_resuelta)
    except AttributeError:
        dentro = resuelto == raiz_resuelta or raiz_resuelta in resuelto.parents
    if not dentro:
        raise BackupError("La ruta de impresión sale de MEDIA_ROOT.")
    return candidato


def _procesar_solicitudes_purga(
    *,
    database_path: Path,
    backup_root: Path,
    nuevo_backup: Path | None,
    request_root: Path,
    media_root: Path,
    timeout: float,
) -> dict[str, Any]:
    solicitudes, errores_carga = _cargar_solicitudes(request_root)
    if not solicitudes and not errores_carga:
        return {
            "solicitudes_completadas": [],
            "respaldos_eliminados": [],
            "requiere_respaldo": False,
            "solicitudes_pendientes": False,
            "errores": [],
        }

    completadas: list[str] = []
    respaldos_eliminados: list[str] = []
    errores: list[str] = list(errores_carga)
    requiere_respaldo = False

    for path, payload in solicitudes:
        if payload.get("tipo") in {"archivos_impresion", "respaldos_periodo"}:
            continue
        exc = BackupError("La solicitud usa un tipo de purga no admitido.")
        try:
            _registrar_error_solicitud(path, payload, exc)
        except OSError as write_exc:
            errores.append(f"{path.name}: no se pudo registrar el error: {write_exc}")
        errores.append(f"{path.name}: {exc}")

    # Cada solicitud de archivos se procesa de forma independiente. Un fallo
    # diario ajeno no impide cerrar otra consolidación.
    for path, payload in solicitudes:
        if payload.get("tipo") != "archivos_impresion":
            continue
        try:
            rutas = payload.get("rutas")
            if (
                not isinstance(rutas, list)
                or rutas != sorted(set(rutas))
                or not rutas
                or any(not isinstance(valor, str) for valor in rutas)
            ):
                raise BackupError("La lista de archivos no es canónica.")
            with closing(sqlite3.connect(database_path, timeout=timeout)) as connection:
                _validar_referencia_archivos(connection, payload)
            for relativa in rutas:
                archivo = _ruta_media_segura(media_root, relativa)
                if archivo.exists():
                    # Revalida raíz, padres y archivo inmediatamente antes de
                    # la operación irreversible.
                    archivo = _ruta_media_segura(media_root, relativa)
                    archivo.unlink()
            _archivo_directo_seguro(path, request_root)
            path.unlink()
            completadas.append(payload["id"])
        except (BackupError, OSError, sqlite3.DatabaseError) as exc:
            try:
                _registrar_error_solicitud(path, payload, exc)
            except OSError as write_exc:
                errores.append(f"{path.name}: no se pudo registrar el error: {write_exc}")
            errores.append(f"{path.name}: {exc}")

    solicitudes, errores_recarga = _cargar_solicitudes(request_root)
    errores.extend(errores_recarga)
    referencias_archivos = {
        payload.get("referencia_id")
        for _, payload in solicitudes
        if (
            payload.get("tipo") == "archivos_impresion"
            and payload.get("referencia_tipo") == "consolidacion"
        )
    }

    for path, payload_original in solicitudes:
        if payload_original.get("tipo") != "respaldos_periodo":
            continue
        payload = dict(payload_original)
        try:
            if payload.get("referencia_tipo") != "consolidacion":
                raise BackupError("La solicitud de respaldos no refiere una consolidación.")
            try:
                periodo = dt.date.fromisoformat(str(payload.get("periodo")))
            except ValueError as exc:
                raise BackupError("El periodo solicitado no es una fecha válida.") from exc
            if periodo.day != 1:
                raise BackupError("El periodo solicitado no inicia en el primer día.")
            _uuid_sqlite(payload.get("sucursal_id"))

            rutas_esperadas = payload.get("rutas_archivos")
            if (
                not isinstance(rutas_esperadas, list)
                or rutas_esperadas != sorted(set(rutas_esperadas))
                or any(not isinstance(valor, str) for valor in rutas_esperadas)
            ):
                raise BackupError(
                    "La solicitud mensual no acredita sus archivos físicos esperados."
                )
            for relativa in rutas_esperadas:
                archivo = _ruta_media_segura(media_root, relativa)
                if archivo.exists():
                    raise BackupError(
                        "Aún existe un archivo físico esperado por la consolidación."
                    )
            if payload["referencia_id"] in referencias_archivos:
                raise BackupError("Aún quedan archivos físicos de la consolidación.")

            with closing(sqlite3.connect(database_path, timeout=timeout)) as connection:
                _validar_consolidacion_sqlite(
                    connection,
                    payload,
                    estados=("confirmada", "purgada"),
                )
                if _contiene_detalle_periodo(
                    connection,
                    payload["sucursal_id"],
                    periodo,
                ):
                    raise BackupError("La base activa todavía contiene detalle del periodo.")

            nombre_post_purga = str(payload.get("respaldo_post_purga") or "")
            respaldo_post_purga: Path | None = None
            if nombre_post_purga:
                try:
                    if BACKUP_NOMBRE_RE.fullmatch(nombre_post_purga) is None:
                        raise BackupError("La solicitud referencia un respaldo no admitido.")
                    candidato = backup_root / nombre_post_purga
                    _validar_triplete_post_purga(
                        candidato,
                        backup_root,
                        payload,
                        timeout,
                    )
                    respaldo_post_purga = candidato
                except (BackupError, OSError, sqlite3.DatabaseError) as exc:
                    payload = _registrar_respaldo_descartado(
                        path,
                        payload,
                        nombre_post_purga,
                        exc,
                    )
                    if nuevo_backup is None:
                        requiere_respaldo = True
                        _registrar_error_solicitud(path, payload, exc)
                        errores.append(
                            f"{path.name}: el respaldo asignado requiere reemplazo: {exc}"
                        )
                        continue

            if respaldo_post_purga is None:
                if nuevo_backup is None:
                    requiere_respaldo = True
                    continue
                respaldo_post_purga = nuevo_backup.resolve()
                if respaldo_post_purga.parent != backup_root.resolve():
                    raise BackupError("El respaldo post-purga queda fuera de la raíz.")
                _validar_triplete_post_purga(
                    respaldo_post_purga,
                    backup_root,
                    payload,
                    timeout,
                )
                payload["respaldo_post_purga"] = respaldo_post_purga.name
                payload["respaldo_asignado_en"] = dt.datetime.now(
                    dt.timezone.utc
                ).isoformat()
                _write_text_atomic(
                    path,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
                    encoding="utf-8",
                )

            eliminados = _depurar_respaldos_periodo(
                backup_root,
                respaldo_post_purga,
                payload,
                timeout,
            )
            with closing(sqlite3.connect(database_path, timeout=timeout)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE ventas_consolidacionmensual
                    SET estado='purgada', purgado_en=?, ultimo_error=''
                    WHERE id=? AND estado IN ('confirmada', 'purgada')
                    """,
                    (
                        dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(" "),
                        _uuid_sqlite(payload["referencia_id"]),
                    ),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    raise BackupError("No se pudo cerrar la consolidación de forma atómica.")
                connection.commit()
            _archivo_directo_seguro(path, request_root)
            path.unlink()
            completadas.append(payload["id"])
            respaldos_eliminados.extend(eliminados)
        except (BackupError, OSError, sqlite3.DatabaseError) as exc:
            try:
                _registrar_error_solicitud(path, payload, exc)
            except OSError as write_exc:
                errores.append(f"{path.name}: no se pudo registrar el error: {write_exc}")
            errores.append(f"{path.name}: {exc}")

    finales, errores_finales = _cargar_solicitudes(request_root)
    errores.extend(errores_finales)
    errores = list(dict.fromkeys(errores))
    return {
        "solicitudes_completadas": completadas,
        "respaldos_eliminados": respaldos_eliminados,
        "requiere_respaldo": requiere_respaldo,
        "solicitudes_pendientes": bool(finales or errores_finales),
        "errores": errores,
    }

def realizar_respaldo(
    database_path: Path,
    backup_root: Path,
    log_path: Path,
    retention_days: int,
    timeout: float = 30.0,
    now: dt.datetime | None = None,
    request_root: Path | None = None,
    media_root: Path | None = None,
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
    if (request_root is None) != (media_root is None):
        raise BackupError("request_root y media_root deben configurarse juntos.")
    if request_root is not None:
        request_root = _validar_directorio_fisico(
            request_root,
            etiqueta="la raíz de solicitudes",
            permitir_inexistente=True,
        )
        media_root = _validar_directorio_fisico(
            media_root,
            etiqueta="MEDIA_ROOT",
            permitir_inexistente=True,
        )

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
        _write_text_atomic(
            sha_path,
            f"{sha256}  {final_backup.name}\n",
            encoding="ascii",
        )
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
            "retention_deleted": [],
        }
        _write_text_atomic(
            metadata_path,
            json.dumps(metadata, ensure_ascii=False, default=_json_default, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        # La tarea privilegiada usa exclusivamente el respaldo recién publicado
        # y verificado para cerrar solicitudes físicas pendientes.
        resultado_purgas = {
            "solicitudes_completadas": [],
            "respaldos_eliminados": [],
            "requiere_respaldo": False,
            "solicitudes_pendientes": False,
            "errores": [],
        }
        if request_root is not None:
            resultado_purgas = _procesar_solicitudes_purga(
                database_path=database_path,
                backup_root=backup_root,
                nuevo_backup=final_backup,
                request_root=request_root,
                media_root=media_root,
                timeout=timeout,
            )
        metadata["physical_purge"] = resultado_purgas

        # Solo se rota cuando el nuevo respaldo ya forma un triplete completo.
        conservar = {final_backup, sha_path, metadata_path}
        if request_root is not None:
            conservar.update(
                _tripletes_asignados_pendientes(request_root, backup_root)
            )
        eliminados = _aplicar_retencion(backup_root, retention_days, now, conservar)
        metadata["retention_deleted"] = eliminados
        metadata["finished_at"] = dt.datetime.now(dt.timezone.utc)
        _write_text_atomic(
            metadata_path,
            json.dumps(metadata, ensure_ascii=False, default=_json_default, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        _append_json_log(log_path, metadata)
        if resultado_purgas["errores"]:
            raise BackupError(
                "Quedaron solicitudes de purga física pendientes: "
                + " | ".join(resultado_purgas["errores"])
            )
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
        try:
            _append_json_log(log_path, error_record)
        except Exception as log_exc:
            exc.add_note(
                f"Ademas no se pudo registrar el error del respaldo: "
                f"{type(log_exc).__name__}: {log_exc}"
            )
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
    parser.add_argument("--request-root", type=Path)
    parser.add_argument("--media-root", type=Path)
    parser.add_argument("--process-pending-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.process_pending_only:
            if args.request_root is None or args.media_root is None:
                raise BackupError(
                    "--process-pending-only requiere request-root y media-root."
                )
            resultado = _procesar_solicitudes_purga(
                database_path=args.database.resolve(),
                backup_root=args.backup_root.resolve(),
                nuevo_backup=None,
                request_root=args.request_root,
                media_root=args.media_root,
                timeout=args.timeout,
            )
            if resultado["requiere_respaldo"]:
                estado = "requiere_respaldo"
                exit_code = 0
            elif resultado["solicitudes_pendientes"]:
                estado = "pendientes_con_error"
                exit_code = 2
            else:
                estado = "sin_solicitudes_pendientes"
                exit_code = 0
            print(
                json.dumps(
                    {"status": estado, **resultado},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return exit_code
        metadata = realizar_respaldo(
            database_path=args.database,
            backup_root=args.backup_root,
            log_path=args.log,
            retention_days=args.retention_days,
            timeout=args.timeout,
            request_root=args.request_root,
            media_root=args.media_root,
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
