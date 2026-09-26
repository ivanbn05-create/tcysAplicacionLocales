"""Aplicación local y deliberada del recovery-v2 agregado de Pedidos.

Esta ruta sólo usa archivos privados y comandos de operador. Ningún error,
pedido sin prueba histórica o recibo manual avanza el checkpoint de Pedidos.
"""
from __future__ import annotations

import json
import os
import stat
import uuid
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Mapping, Sequence

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from ventas.integracion_sucursales import (
    ORIGEN_API_V2,
    PedidoRemotoInvalido,
    PedidoRemotoRequiereConciliacion,
    PedidoRemotoSinCapacidad,
    _adaptar_pedido_api_v2,
    _importar_pedido,
)
from ventas.models import (
    ConfiguracionSucursal,
    EstadoSincronizacionPedidos,
    SucursalPedido,
    PedidoSucursalImportado,
    RecuperacionPedidosV2,
)
from ventas.pedidos_api_v2 import ErrorContratoPedidos, _pedido
from ventas.pedidos_recovery_v2 import (
    MAX_ZIP_BYTES,
    PinnedPedidosKey,
    RecoveryV2Error,
    canonical_json,
    sha256,
    sign_edge_ack,
    verify_archive_rows,
    verify_baseline,
    verify_receipt,
)

_PRIVATE_SUBDIR = "pedidos-recovery-v2"
_MAX_PRIVATE_KEY_BYTES = 16_384
_MAX_PIN_FILE_BYTES = 65_536
_ALLOWED_WINDOWS_SIDS = {
    "S-1-5-18",     # SYSTEM
    "S-1-5-19",     # LOCAL SERVICE
    "S-1-5-32-544", # Administradores
}


class RecuperacionLocalError(RecoveryV2Error):
    """Bloqueo local seguro: se necesita intervención, sin mover cursor."""


def _uuid_canonico(value: object, label: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RecuperacionLocalError(f"{label} debe ser UUID canónico.") from exc
    if str(parsed) != str(value):
        raise RecuperacionLocalError(f"{label} debe ser UUID canónico.")
    return str(parsed)


def _fecha_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or timezone.is_naive(value):
        raise RecuperacionLocalError("El checkpoint contiene una fecha sin zona horaria.")
    return value.astimezone(dt_timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def exportar_preestado(sucursal) -> dict[str, object]:
    """Captura los ocho campos r7; no inventa un cursor vacío tras un 410."""
    estado = EstadoSincronizacionPedidos.objects.get(sucursal=sucursal)
    if (estado.estado != EstadoSincronizacionPedidos.Estado.RECONCILIACION
            or estado.version_api != "v2"):
        raise RecuperacionLocalError("Pedidos no está en conciliación v2.")
    if estado.ventana_desde is None or estado.ventana_hasta is None:
        raise RecuperacionLocalError("La ventana de conciliación está incompleta.")
    try:
        senders = [int(value) for value in estado.sucursales_origen]
    except (TypeError, ValueError) as exc:
        raise RecuperacionLocalError("El alcance de Pedidos es inválido.") from exc
    if not senders or senders != sorted(set(senders)):
        raise RecuperacionLocalError("El alcance de Pedidos es inválido.")
    if (estado.agua_alta_hasta is not None
            and estado.agua_alta_hasta != estado.ventana_desde):
        raise RecuperacionLocalError("El high-water no coincide con la ventana activa.")
    if estado.ventana_desde >= estado.ventana_hasta:
        raise RecuperacionLocalError("La ventana de conciliación es inválida.")
    return {
        "cursor": estado.cursor or None,
        "ultimo_cursor_confirmado": estado.ultimo_cursor_confirmado or None,
        "ventana_desde": _fecha_utc(estado.ventana_desde),
        "ventana_hasta": _fecha_utc(estado.ventana_hasta),
        "agua_alta_hasta": _fecha_utc(estado.agua_alta_hasta),
        "sucursales_origen": senders,
        "estado": "reconciliacion",
        "version_api": "v2",
    }


def _check_reparse(path: Path) -> None:
    try:
        attributes = path.lstat().st_file_attributes
    except AttributeError:
        attributes = 0
    except FileNotFoundError:
        return
    if path.is_symlink() or (
        os.name == "nt" and attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    ):
        raise RecuperacionLocalError("Ruta privada enlazada o redirigida.")


def _check_private(path: Path, *, directory: bool) -> None:
    _check_reparse(path)
    if (not path.is_dir() if directory else not path.is_file()):
        raise RecuperacionLocalError("Falta archivo o directorio privado.")
    if os.name != "nt":
        metadata = path.stat()
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise RecuperacionLocalError("Permisos POSIX inseguros para recuperación.")
        return
    try:
        import win32api
        import win32con
        import win32security

        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
        )
        current_sid = win32security.ConvertSidToStringSid(
            win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        )
        security = win32security.GetFileSecurity(
            str(path), win32security.DACL_SECURITY_INFORMATION
        )
        dacl = security.GetSecurityDescriptorDacl()
        if dacl is None:
            raise RecuperacionLocalError("DACL privada ausente.")
        allowed = _ALLOWED_WINDOWS_SIDS | {current_sid}
        for index in range(dacl.GetAceCount()):
            ace = dacl.GetAce(index)
            ace_type = ace[0][0]
            if ace_type == win32security.ACCESS_ALLOWED_ACE_TYPE:
                sid = win32security.ConvertSidToStringSid(ace[2])
                if sid not in allowed:
                    raise RecuperacionLocalError("DACL privada concede acceso amplio.")
            elif ace_type != win32security.ACCESS_DENIED_ACE_TYPE:
                raise RecuperacionLocalError("DACL privada contiene un ACE desconocido.")
    except RecuperacionLocalError:
        raise
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        raise RecuperacionLocalError("No se pudo verificar la DACL privada.") from exc


def _runtime_root(runtime_dir: Path | None) -> Path:
    configured = Path(settings.RUNTIME_DIR).resolve()
    if runtime_dir is not None and Path(runtime_dir).resolve() != configured:
        raise RecuperacionLocalError("El runtime debe ser el configurado para H18.")
    _check_reparse(configured)
    if not configured.is_dir():
        raise RecuperacionLocalError("No existe runtime privado de H18.")
    return configured


def _private_directory(root: Path, recovery_id: str | None = None) -> Path:
    base = root / _PRIVATE_SUBDIR
    base.mkdir(mode=0o700, exist_ok=True)
    if os.name != "nt":
        os.chmod(base, 0o700)
    _check_private(base, directory=True)
    if recovery_id is None:
        return base
    target = base / _uuid_canonico(recovery_id, "recovery_id")
    target.mkdir(mode=0o700, exist_ok=True)
    if os.name != "nt":
        os.chmod(target, 0o700)
    _check_private(target, directory=True)
    return target


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_private(path: Path, payload: bytes) -> None:
    if path.exists():
        if _read_private(path, max_bytes=max(len(payload), 1)) != payload:
            raise RecuperacionLocalError("Archivo privado existente con contenido distinto.")
        return
    _check_private(path.parent, directory=True)
    temp = path.parent / (path.name + "." + str(uuid.uuid4()) + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(temp, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            if os.name != "nt":
                os.chmod(temp, 0o600)
            _check_private(temp, directory=False)
            if path.exists():
                if _read_private(path, max_bytes=max(len(payload), 1)) != payload:
                    raise RecuperacionLocalError("Archivo privado concurrente distinto.")
                temp.unlink()
                return
            os.replace(temp, path)
            _fsync_directory(path.parent)
            _check_private(path, directory=False)
        except Exception:
            if temp.exists():
                temp.unlink()
            raise
    except OSError as exc:
        raise RecuperacionLocalError("No se pudo persistir el archivo privado.") from exc


def _read_private(path: Path, *, max_bytes: int) -> bytes:
    _check_private(path, directory=False)
    if path.stat().st_size > max_bytes:
        raise RecuperacionLocalError("Archivo privado excede límite.")
    try:
        with path.open("rb") as stream:
            data = stream.read(max_bytes + 1)
    except OSError as exc:
        raise RecuperacionLocalError("No se pudo leer archivo privado.") from exc
    if len(data) > max_bytes:
        raise RecuperacionLocalError("Archivo privado excede límite.")
    return data


def _state_write_lock(sucursal) -> EstadoSincronizacionPedidos:
    changed = EstadoSincronizacionPedidos.objects.filter(
        sucursal=sucursal
    ).update(detalle_seguro=F("detalle_seguro"))
    if changed != 1:
        raise RecuperacionLocalError("No existe checkpoint de Pedidos para la sucursal.")
    return EstadoSincronizacionPedidos.objects.select_for_update().get(
        sucursal=sucursal
    )


def _private_key(path: Path, root: Path) -> Ed25519PrivateKey:
    candidate = Path(path).resolve()
    if not candidate.is_relative_to(root):
        raise RecuperacionLocalError("La clave privada Edge debe estar en runtime H18.")
    data = _read_private(candidate, max_bytes=_MAX_PRIVATE_KEY_BYTES)
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except (TypeError, ValueError) as exc:
        raise RecuperacionLocalError("Clave privada Edge inválida.") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise RecuperacionLocalError("Se esperaba Ed25519 Edge.")
    return key


def cargar_pins_pedidos(path: Path) -> dict[str, PinnedPedidosKey]:
    """Lee el trust store público fijado en certs, sin aceptar claves del ZIP."""
    candidate = Path(path)
    certs = (Path(settings.BASE_DIR) / "certs").resolve()
    _check_reparse(candidate)
    if not candidate.resolve().is_relative_to(certs):
        raise RecuperacionLocalError("El pin público debe residir en certs.")
    if not candidate.is_file() or candidate.stat().st_size > _MAX_PIN_FILE_BYTES:
        raise RecuperacionLocalError("Trust store Pedidos ausente o sobredimensionado.")
    if os.name == "nt":
        _check_private(candidate, directory=False)
    try:
        document = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise RecuperacionLocalError("Trust store Pedidos inválido.") from exc
    if type(document) is not dict or set(document) != {"format_version", "keys"}:
        raise RecuperacionLocalError("Formato del trust store desconocido.")
    if document["format_version"] != 1 or type(document["keys"]) is not dict:
        raise RecuperacionLocalError("Versión del trust store desconocida.")
    pins = {}
    for key_id, value in document["keys"].items():
        canonical = _uuid_canonico(key_id, "key_id")
        if type(value) is not dict or set(value) != {
            "public_key_b64", "format_version", "revoked"
        }:
            raise RecuperacionLocalError("Entrada de clave Pedidos inválida.")
        pin = PinnedPedidosKey(**value)
        if (type(pin.public_key_b64) is not str
                or type(pin.format_version) is not int
                or type(pin.revoked) is not bool):
            raise RecuperacionLocalError("Entrada de clave Pedidos inválida.")
        pins[canonical] = pin
    if not pins:
        raise RecuperacionLocalError("Trust store Pedidos vacío.")
    return pins

def _check_bound_scope(edge_id: str, pos_branch_id: str, sender_ids: Sequence[int], sucursal) -> None:
    if not getattr(settings, "PEDIDOS_API_BOUND_IDENTITY", False):
        raise RecuperacionLocalError("Recovery-v2 exige credencial Pedidos vinculada al Edge.")
    if (edge_id != settings.PEDIDOS_API_EDGE_ID
            or pos_branch_id != settings.PEDIDOS_API_POS_BRANCH_ID
            or list(sender_ids) != list(settings.PEDIDOS_API_SUCURSAL_IDS)):
        raise RecuperacionLocalError("Identidad o alcance no coincide con Pedidos v2 configurado.")
    config = ConfiguracionSucursal.objects.filter(sucursal=sucursal).first()
    if (config is None or str(config.instalacion_id) != edge_id
            or str(sucursal.id) != pos_branch_id):
        raise RecuperacionLocalError("El Edge o la sucursal POS no coincide con SQLite.")
    confirmed = list(SucursalPedido.objects.filter(
        sucursal=sucursal, activa=True,
        tipo=SucursalPedido.Tipo.SUCURSAL,
        identidad_confirmada_en__isnull=False,
        origen_id__in=sender_ids,
    ).order_by("origen_id").values_list("origen_id", flat=True))
    if confirmed != list(sender_ids):
        raise RecuperacionLocalError("Falta aprobar el mapa de remitentes en SQLite.")


def _read_untrusted(path: Path, maximum: int) -> bytes:
    candidate = Path(path)
    _check_reparse(candidate)
    if not candidate.is_file() or candidate.stat().st_size > maximum:
        raise RecuperacionLocalError("Archivo recibido ausente o sobredimensionado.")
    try:
        with candidate.open("rb") as source:
            data = source.read(maximum + 1)
    except OSError as exc:
        raise RecuperacionLocalError("No se pudo leer archivo recibido.") from exc
    if len(data) > maximum:
        raise RecuperacionLocalError("Archivo recibido sobredimensionado.")
    return data


def _archive_inputs(baseline, archivos_custodia: Mapping[str, Path]) -> dict[str, bytes]:
    referenced = {
        row["exportacion_id"] for row in baseline.recovered_orders
    }
    if not isinstance(archivos_custodia, Mapping):
        raise RecuperacionLocalError("Archivos custodios inválidos.")
    result: dict[str, bytes] = {}
    for key_id, path in archivos_custodia.items():
        export_id = _uuid_canonico(key_id, "exportacion_id")
        if export_id not in referenced:
            raise RecuperacionLocalError("Se entregó un archivo custodio ajeno.")
        if export_id in result:
            raise RecuperacionLocalError("Archivo custodio duplicado.")
        result[export_id] = _read_untrusted(Path(path), MAX_ZIP_BYTES)
    return result


def _archive_proofs(baseline, archives: Mapping[str, bytes]) -> dict[tuple[int, str], object]:
    proofs = {}
    for export_id, archive in archives.items():
        for pair, proof in verify_archive_rows(
            baseline, archive, export_id
        ).items():
            if pair in proofs:
                raise RecuperacionLocalError("Prueba archivada duplicada.")
            proofs[pair] = proof
    return proofs


def _from_prestate_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise RecuperacionLocalError("Fecha de recovery inválida.") from exc
    if timezone.is_naive(parsed):
        raise RecuperacionLocalError("Fecha de recovery sin zona horaria.")
    return parsed.astimezone(dt_timezone.utc)


def _stored_baseline(
    acta: RecuperacionPedidosV2, root: Path,
    pedidos_keys: Mapping[str, PinnedPedidosKey],
):
    expected = Path(_PRIVATE_SUBDIR) / str(acta.id) / "baseline.zip"
    if Path(acta.snapshot_path) != expected:
        raise RecuperacionLocalError("Ruta relativa de baseline no corresponde al acta.")
    path = root / expected
    raw = _read_private(path, max_bytes=MAX_ZIP_BYTES)
    baseline = verify_baseline(
        raw,
        expected_snapshot_sha256=acta.snapshot_sha256,
        pedidos_keys=pedidos_keys,
        edge_id=str(acta.edge_id),
        pos_branch_id=str(acta.pos_branch_id),
        sender_ids=acta.sender_ids,
        expected_prestate=acta.prestate,
    )
    if (baseline.payload["recovery_id"] != str(acta.id)
            or baseline.manifest_sha256 != acta.manifest_sha256
            or baseline.payload["orders_sha256"] != acta.orders_sha256
            or baseline.payload["recovered_orders_sha256"] != acta.recovered_orders_sha256
            or baseline.payload["tombstones_sha256"] != acta.tombstones_sha256
            or baseline.payload["key_id"] != acta.pedidos_key_id
            or _from_prestate_timestamp(baseline.payload["hasta"]) != acta.hasta):
        raise RecuperacionLocalError("Baseline restaurada no coincide con acta SQLite.")
    return baseline


def _stored_archive_proofs(
    baseline, root: Path, recovery_id: str
) -> dict[tuple[int, str], object]:
    folder = _private_directory(root, recovery_id) / "archives"
    referenced = {row["exportacion_id"] for row in baseline.recovered_orders}
    if not folder.exists():
        return {}
    _check_private(folder, directory=True)
    proofs = {}
    for export_id in sorted(referenced):
        path = folder / (export_id + ".zip")
        if not path.exists():
            continue
        archive = _read_private(path, max_bytes=MAX_ZIP_BYTES)
        for pair, proof in verify_archive_rows(
            baseline, archive, export_id
        ).items():
            if pair in proofs:
                raise RecuperacionLocalError("Archivo custodio duplicado en H18.")
            proofs[pair] = proof
    for entry in folder.iterdir():
        if entry.name not in {key + ".zip" for key in referenced}:
            raise RecuperacionLocalError("Archivo custodio inesperado en H18.")
    return proofs


def _existing_proved(
    sucursal, sender_id: int, codigo_publico: str, order_hash: str
) -> bool:
    imported = PedidoSucursalImportado.objects.filter(
        sucursal=sucursal,
        origen=ORIGEN_API_V2,
        sender_id=sender_id,
        codigo_publico=codigo_publico,
    ).first()
    return bool(
        imported
        and imported.order_sha256 == order_hash
        and imported.order_canonical_json
        and sha256(imported.order_canonical_json.encode("utf-8")) == order_hash
        and imported.ticket_id
    )


def _assert_same_prestate(sucursal, expected: dict[str, object]) -> None:
    _state_write_lock(sucursal)
    if exportar_preestado(sucursal) != expected:
        raise RecuperacionLocalError("El checkpoint cambió durante recuperación; no se firma ACK.")


def _import_orders(sucursal, baseline, archive_proofs, expected_prestate):
    rows = {}
    for row in baseline.orders:
        pair = (row["sender_id"], row["order"]["codigo_publico"])
        rows[pair] = row["order"]
    for row in baseline.recovered_orders:
        pair = (row["sender_id"], row["order"]["codigo_publico"])
        proof = archive_proofs.get(pair)
        if proof is not None and proof.importable:
            rows[pair] = row["order"]

    # Cada pedido obtiene una transacción corta. Una caída deja importaciones
    # idempotentes probables por pareja/hash, con el cursor todavía inmóvil.
    for pair in sorted(rows):
        sender_id, public_id = pair
        order = rows[pair]
        order_hash = baseline.order_hashes[pair]
        if _existing_proved(sucursal, sender_id, public_id, order_hash):
            continue
        try:
            remote = _pedido(order, "recovery.order", frozenset({sender_id}))
            if remote.order_sha256 != order_hash:
                raise PedidoRemotoRequiereConciliacion(
                    "Hash del pedido de recovery no coincide."
                )
            adapted, items = _adaptar_pedido_api_v2(remote)
        except (PedidoRemotoInvalido, PedidoRemotoRequiereConciliacion,
                ErrorContratoPedidos):
            continue
        with transaction.atomic():
            _assert_same_prestate(sucursal, expected_prestate)
            try:
                with transaction.atomic():
                    _importar_pedido(
                        sucursal, adapted, items, ORIGEN_API_V2,
                        identidad_estricta=True,
                        sender_id=sender_id,
                        order_canonical_json=remote.order_canonical_json,
                        order_sha256=remote.order_sha256,
                    )
                    if not _existing_proved(
                        sucursal, sender_id, public_id, order_hash
                    ):
                        raise PedidoRemotoRequiereConciliacion(
                            "No se pudo probar la identidad y contenido importado."
                        )
            except (PedidoRemotoInvalido, PedidoRemotoRequiereConciliacion,
                    PedidoRemotoSinCapacidad, IntegrityError):
                # El savepoint del pedido revierte un ticket parcial; el proceso
                # sigue para enumerar todos los irresueltos sin tocar checkpoint.
                pass
    return rows


def _classify_persisted(sucursal, baseline, importable_rows):
    received = []
    unresolved = []
    for sender_id, public_id in sorted(baseline.identities):
        pair = (sender_id, public_id)
        order_hash = baseline.order_hashes.get(pair)
        if (pair in importable_rows and order_hash
                and _existing_proved(sucursal, sender_id, public_id, order_hash)):
            received.append({
                "sender_id": sender_id,
                "codigo_publico": public_id,
                "order_sha256": order_hash,
            })
        else:
            unresolved.append({"sender_id": sender_id, "codigo_publico": public_id})
    return received, unresolved


def preparar_recuperacion(
    sucursal, *, snapshot_path: Path, snapshot_sha256: str,
    pedidos_keys: Mapping[str, PinnedPedidosKey],
    edge_id: str, pos_branch_id: str, sender_ids: Sequence[int],
    edge_key_id: str, edge_private_key_path: Path,
    archivos_custodia: Mapping[str, Path],
    runtime_dir: Path | None = None,
) -> RecuperacionPedidosV2:
    """Importa probado y firma una sola vez. No transmite automáticamente el ACK."""
    edge = _uuid_canonico(edge_id, "edge_id")
    branch = _uuid_canonico(pos_branch_id, "pos_branch_id")
    key_id = _uuid_canonico(edge_key_id, "edge_key_id")
    _check_bound_scope(edge, branch, sender_ids, sucursal)
    root = _runtime_root(runtime_dir)
    private_key = _private_key(Path(edge_private_key_path), root)
    snapshot = _read_untrusted(Path(snapshot_path), MAX_ZIP_BYTES)
    prestate = exportar_preestado(sucursal)
    if list(sender_ids) != prestate["sucursales_origen"]:
        raise RecuperacionLocalError("Alcance distinto del checkpoint en conciliación.")
    baseline = verify_baseline(
        snapshot,
        expected_snapshot_sha256=snapshot_sha256,
        pedidos_keys=pedidos_keys,
        edge_id=edge,
        pos_branch_id=branch,
        sender_ids=sender_ids,
        expected_prestate=prestate,
    )
    recovery_id = baseline.payload["recovery_id"]
    archive_bytes = _archive_inputs(baseline, archivos_custodia)
    proofs = _archive_proofs(baseline, archive_bytes)
    folder = _private_directory(root, recovery_id)
    relative = Path(_PRIVATE_SUBDIR) / recovery_id / "baseline.zip"
    _write_private(root / relative, snapshot)
    if archive_bytes:
        archives_dir = folder / "archives"
        archives_dir.mkdir(mode=0o700, exist_ok=True)
        if os.name != "nt":
            os.chmod(archives_dir, 0o700)
        _check_private(archives_dir, directory=True)
        for export_id, content in sorted(archive_bytes.items()):
            _write_private(archives_dir / (export_id + ".zip"), content)

    existing = RecuperacionPedidosV2.objects.filter(pk=recovery_id).first()
    if existing is not None:
        if (existing.sucursal_id != sucursal.pk
                or existing.snapshot_sha256 != baseline.snapshot_sha256
                or existing.prestate != prestate
                or existing.edge_key_id != key_id):
            raise RecuperacionLocalError("Acta existente distinta: no se regenera ACK.")
        _stored_baseline(existing, root, pedidos_keys)
        acuse_persistido(existing, runtime_dir=root)
        return existing

    importable_rows = _import_orders(sucursal, baseline, proofs, prestate)
    with transaction.atomic():
        _assert_same_prestate(sucursal, prestate)
        existing = RecuperacionPedidosV2.objects.filter(pk=recovery_id).first()
        if existing is not None:
            if existing.snapshot_sha256 != baseline.snapshot_sha256:
                raise RecuperacionLocalError("Acta concurrente distinta.")
            acuse_persistido(existing, runtime_dir=root)
            return existing
        if RecuperacionPedidosV2.objects.filter(
            sucursal=sucursal,
            estado=RecuperacionPedidosV2.Estado.ACUSE_PENDIENTE,
        ).exists():
            raise RecuperacionLocalError("Ya hay un ACK pendiente para esta sucursal.")
        received, unresolved = _classify_persisted(
            sucursal, baseline, importable_rows
        )
        nonce = uuid.uuid4()
        while RecuperacionPedidosV2.objects.filter(ack_nonce=nonce).exists():
            nonce = uuid.uuid4()
        ack = sign_edge_ack(
            baseline, edge_key_id=key_id, edge_private_key=private_key,
            nonce=str(nonce),
            issued_at=_fecha_utc(timezone.now()),
            received=received, unresolved=unresolved,
            archive_proven=sorted(
                pair for pair, proof in proofs.items() if proof.importable
            ),
        )
        acta = RecuperacionPedidosV2.objects.create(
            id=uuid.UUID(recovery_id), sucursal=sucursal,
            edge_id=uuid.UUID(edge), pos_branch_id=uuid.UUID(branch),
            sender_ids=list(sender_ids), prestate=prestate,
            prestate_sha256=sha256(canonical_json(prestate)),
            snapshot_sha256=baseline.snapshot_sha256,
            manifest_sha256=baseline.manifest_sha256,
            orders_sha256=baseline.payload["orders_sha256"],
            recovered_orders_sha256=baseline.payload["recovered_orders_sha256"],
            tombstones_sha256=baseline.payload["tombstones_sha256"],
            snapshot_path=relative.as_posix(),
            hasta=_from_prestate_timestamp(baseline.payload["hasta"]),
            pedidos_key_id=baseline.payload["key_id"],
            edge_key_id=key_id, ack_nonce=nonce,
            ack_json=ack.decode("utf-8"), ack_sha256=sha256(ack),
            estado=RecuperacionPedidosV2.Estado.ACUSE_PENDIENTE,
            detalle_seguro=(
                "ACK pendiente con identidades no resueltas."
                if unresolved else "ACK pendiente de recibo firmado."
            ),
        )
    acuse_persistido(acta, runtime_dir=root)
    return acta


def acuse_persistido(
    acta: RecuperacionPedidosV2, *, runtime_dir: Path | None = None
) -> Path:
    """Devuelve el archivo ACK exacto para envío/reenvío; nunca cambia nonce."""
    root = _runtime_root(runtime_dir)
    folder = _private_directory(root, str(acta.id))
    ack = acta.ack_json.encode("utf-8")
    if not ack.endswith(b"\n") or sha256(ack) != acta.ack_sha256:
        raise RecuperacionLocalError("ACK persistido en SQLite no coincide con su SHA.")
    path = folder / "edge-ack.json"
    _write_private(path, ack)
    return path

def preestado_persistido(sucursal, *, runtime_dir: Path | None = None) -> Path:
    """Emite archivo privado para preparar la baseline en Pedidos."""
    state = exportar_preestado(sucursal)
    root = _runtime_root(runtime_dir)
    folder = _private_directory(root)
    path = folder / ("prestate-" + str(uuid.uuid4()) + ".json")
    _write_private(path, canonical_json(state) + b"\n")
    return path


def _verify_received_still_persisted(sucursal, ack_bytes: bytes) -> None:
    try:
        envelope = json.loads(ack_bytes)
        received = envelope["payload"]["received"]
    except (ValueError, KeyError, TypeError) as exc:
        raise RecuperacionLocalError("ACK local ilegible.") from exc
    for row in received:
        if not _existing_proved(
            sucursal, row["sender_id"], row["codigo_publico"],
            row["order_sha256"],
        ):
            raise RecuperacionLocalError(
                "Un pedido reconocido dejó de estar probado en SQLite."
            )


def aplicar_recibo(
    acta: RecuperacionPedidosV2, *, receipt_path: Path,
    pedidos_keys: Mapping[str, PinnedPedidosKey],
    runtime_dir: Path | None = None,
) -> bool:
    """Sólo el recibo completado y el CAS íntegro cierran la ventana."""
    root = _runtime_root(runtime_dir)
    acta = RecuperacionPedidosV2.objects.get(pk=acta.pk)
    baseline = _stored_baseline(acta, root, pedidos_keys)
    ack_path = acuse_persistido(acta, runtime_dir=root)
    ack = _read_private(ack_path, max_bytes=10_000_000)
    if sha256(ack) != acta.ack_sha256:
        raise RecuperacionLocalError("ACK privado no corresponde al acta.")
    proofs = _stored_archive_proofs(baseline, root, str(acta.id))
    archive_proven = sorted(
        pair for pair, proof in proofs.items() if proof.importable
    )
    receipt_raw = _read_untrusted(Path(receipt_path), 8_192)
    receipt = verify_receipt(
        baseline, ack, receipt_raw,
        pedidos_keys=pedidos_keys,
        archive_proven=archive_proven,
    )
    folder = _private_directory(root, str(acta.id))
    _write_private(folder / "receipt.json", receipt_raw)

    with transaction.atomic():
        _state_write_lock(acta.sucursal)
        locked = RecuperacionPedidosV2.objects.select_for_update().get(pk=acta.pk)
        if (locked.snapshot_sha256 != baseline.snapshot_sha256
                or locked.ack_sha256 != sha256(ack)
                or locked.prestate_sha256 != sha256(canonical_json(locked.prestate))):
            raise RecuperacionLocalError("Acta cambió durante verificación del recibo.")
        if locked.estado != RecuperacionPedidosV2.Estado.ACUSE_PENDIENTE:
            if locked.receipt_sha256 != receipt.receipt_sha256:
                raise RecuperacionLocalError("Recibo distinto para acta ya cerrada.")
            return locked.estado == RecuperacionPedidosV2.Estado.COMPLETADA
        if exportar_preestado(acta.sucursal) != locked.prestate:
            raise RecuperacionLocalError("Preestado cambió; no se mueve el cursor.")
        if receipt.completed:
            _verify_received_still_persisted(acta.sucursal, ack)
        locked.receipt_json = receipt_raw.decode("utf-8")
        locked.receipt_sha256 = receipt.receipt_sha256
        locked.estado = (
            RecuperacionPedidosV2.Estado.COMPLETADA if receipt.completed
            else RecuperacionPedidosV2.Estado.INTERVENCION_MANUAL
        )
        locked.detalle_seguro = (
            "Recovery completado por recibo firmado."
            if receipt.completed else "Recovery requiere intervención manual."
        )
        locked.save(update_fields=[
            "receipt_json", "receipt_sha256", "estado", "detalle_seguro",
            "actualizado_en",
        ])
        if receipt.completed:
            state = EstadoSincronizacionPedidos.objects.get(sucursal=acta.sucursal)
            if (state.agua_alta_hasta is not None
                    and state.agua_alta_hasta != state.ventana_desde):
                raise RecuperacionLocalError("High-water cambió durante cierre.")
            state.ventana_desde = locked.hasta
            state.ventana_hasta = None
            state.agua_alta_hasta = locked.hasta
            state.cursor = ""
            state.ultimo_cursor_confirmado = ""
            state.estado = EstadoSincronizacionPedidos.Estado.LISTO
            state.detalle_seguro = ""
            state.conciliacion_requerida_en = None
            state.ultima_sincronizacion_en = timezone.now()
            state.ultimo_recovery_id = locked.id
            state.ultimo_recovery_snapshot_sha256 = locked.snapshot_sha256
            state.save(update_fields=[
                "ventana_desde", "ventana_hasta", "agua_alta_hasta",
                "cursor", "ultimo_cursor_confirmado", "estado",
                "detalle_seguro", "conciliacion_requerida_en",
                "ultima_sincronizacion_en", "ultimo_recovery_id",
                "ultimo_recovery_snapshot_sha256", "actualizado_en",
            ])
    return receipt.completed
