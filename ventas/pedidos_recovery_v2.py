"""Codec puro para recovery-v2 agregado de Pedidos (laboratorio).

No lee configuración ni toca SQLite. El integrador conserva ZIP, ACK, nonce y
recibo como archivos privados durables y hace el CAS del preestado en SQLite.
Contrato: tcysPedidosSucursales@ca1e93df, recovery-v2.schema.json r7.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from ventas.pedidos_api_v2 import ErrorContratoPedidos, _pedido

MAX_ZIP_BYTES = 100_000_000
MAX_ROWS = 5_000
MAX_TOMBSTONES_BYTES = 2_000_000
MAX_MANIFEST_BYTES = 65_536
MAX_ACK_BYTES = 10_000_000
MAX_RECEIPT_BYTES = 8_192
BASELINE_MEMBERS = frozenset(
    ("manifest.json", "orders.jsonl", "recovered_orders.jsonl", "tombstones.json")
)
ARCHIVE_MEMBERS = frozenset(("manifest.json", "pedidos.jsonl"))
PRESTATE_FIELDS = frozenset(
    ("cursor", "ultimo_cursor_confirmado", "ventana_desde", "ventana_hasta",
     "agua_alta_hasta", "sucursales_origen", "estado", "version_api")
)
MANIFEST_FIELDS = frozenset(
    ("type", "key_id", "recovery_id", "edge_id", "pos_branch_id", "sender_ids",
     "pos_prestate", "pos_prestate_sha256", "desde", "hasta", "purge_epoch",
     "freeze_id", "orders_sha256", "orders_count", "recovered_orders_sha256",
     "recovered_orders_count", "tombstones_sha256", "tombstones_count",
     "next_desde", "next_cursor")
)
ACK_FIELDS = frozenset(
    ("type", "key_id", "nonce", "issued_at", "recovery_id", "edge_id",
     "pos_branch_id", "sender_ids", "pos_prestate_sha256", "snapshot_sha256",
     "manifest_sha256", "orders_sha256", "tombstones_sha256",
     "received", "unresolved")
)
RECEIPT_FIELDS = frozenset(
    ("type", "key_id", "recovery_id", "edge_id", "pos_branch_id", "sender_ids",
     "pos_prestate_sha256", "snapshot_sha256", "manifest_sha256",
     "edge_ack_sha256", "status", "next_desde", "next_cursor", "issued_at")
)
SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class RecoveryV2Error(ValueError):
    """Evidencia inválida: nunca avanzar el checkpoint."""


@dataclass(frozen=True)
class PinnedPedidosKey:
    public_key_b64: str
    format_version: int = 1
    revoked: bool = False


@dataclass(frozen=True)
class BaselineV2:
    payload: dict[str, Any]
    snapshot_sha256: str
    manifest_sha256: str
    orders: tuple[dict[str, Any], ...]
    recovered_orders: tuple[dict[str, Any], ...]
    tombstones: tuple[dict[str, Any], ...]
    order_hashes: Mapping[tuple[int, str], str]

    @property
    def identities(self) -> frozenset[tuple[int, str]]:
        return frozenset(self.order_hashes) | frozenset(
            (row["sender_id"], row["codigo_publico"]) for row in self.tombstones
        )

    @property
    def recovered_identities(self) -> frozenset[tuple[int, str]]:
        return frozenset(
            (row["sender_id"], row["order"]["codigo_publico"])
            for row in self.recovered_orders
        )


@dataclass(frozen=True)
class ArchivedRowProof:
    estado: str
    eliminado: bool
    row_sha256: str
    importable: bool


@dataclass(frozen=True)
class VerifiedReceiptV2:
    payload: dict[str, Any]
    completed: bool
    receipt_sha256: str


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise RecoveryV2Error("JSON canónico inválido.") from exc


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values:
        if key in result:
            raise RecoveryV2Error("Clave JSON duplicada.")
        result[key] = value
    return result


def _invalid_number(_: str) -> Any:
    raise RecoveryV2Error("Número JSON no permitido por el contrato.")


def _json(raw: bytes, *, maximum: int, canonical_file: bool = False) -> Any:
    if type(raw) is not bytes or not raw or len(raw) > maximum:
        raise RecoveryV2Error("JSON ausente o sobredimensionado.")
    try:
        value = json.loads(
            raw.decode("utf-8", "strict"), object_pairs_hook=_pairs,
            parse_float=_invalid_number, parse_constant=_invalid_number,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError,
            RecursionError) as exc:
        raise RecoveryV2Error("JSON inválido o ambiguo.") from exc
    if canonical_file and raw != canonical_json(value) + b"\n":
        raise RecoveryV2Error("Archivo JSON no canónico.")
    return value


def _exact(value: Any, fields: frozenset[str] | set[str], name: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise RecoveryV2Error(f"Esquema de {name} desconocido o incompleto.")
    return value


def _uuid(value: Any, name: str) -> str:
    if type(value) is not str or len(value) != 36:
        raise RecoveryV2Error(f"UUID inválido: {name}.")
    try:
        result = str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise RecoveryV2Error(f"UUID inválido: {name}.") from exc
    if result != value:
        raise RecoveryV2Error(f"UUID no canónico: {name}.")
    return value


def _sha(value: Any, name: str) -> str:
    if type(value) is not str or not SHA_PATTERN.fullmatch(value):
        raise RecoveryV2Error(f"SHA-256 inválido: {name}.")
    return value


def _utc(value: Any, name: str) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise RecoveryV2Error(f"Fecha UTC inválida: {name}.")
    try:
        date = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise RecoveryV2Error(f"Fecha UTC inválida: {name}.") from exc
    if date.isoformat(timespec="microseconds").replace("+00:00", "Z") != value:
        raise RecoveryV2Error(f"Fecha UTC no canónica: {name}.")
    return date


def _senders(value: Any) -> tuple[int, ...]:
    if (type(value) is not list or not 1 <= len(value) <= 32
            or any(type(item) is not int or item <= 0 for item in value)
            or value != sorted(set(value))):
        raise RecoveryV2Error("Tupla de remitentes inválida.")
    return tuple(value)


def _prestate(value: Any, senders: tuple[int, ...]) -> dict[str, Any]:
    state = _exact(value, PRESTATE_FIELDS, "preestado")
    if (state["estado"] != "reconciliacion"
            or state["version_api"] != "v2"
            or _senders(state["sucursales_origen"]) != senders):
        raise RecoveryV2Error("Preestado/alcance incompatible.")
    for field in ("cursor", "ultimo_cursor_confirmado"):
        cursor = state[field]
        if cursor is not None:
            if type(cursor) is not str or len(cursor) > 2048 or not cursor.isascii():
                raise RecoveryV2Error("Cursor del preestado inválido.")
    start = _utc(state["ventana_desde"], "ventana_desde")
    end = _utc(state["ventana_hasta"], "ventana_hasta")
    if start >= end:
        raise RecoveryV2Error("Ventana de recuperación inválida.")
    high = state["agua_alta_hasta"]
    if high is not None and _utc(high, "agua_alta_hasta") != start:
        raise RecoveryV2Error("High-water no enlaza con el inicio.")
    return state


def _unb64(value: Any, size: int, name: str) -> bytes:
    if type(value) is not str or not value or "=" in value:
        raise RecoveryV2Error(f"Base64url inválido: {name}.")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, base64.binascii.Error) as exc:
        raise RecoveryV2Error(f"Base64url inválido: {name}.") from exc
    if (len(raw) != size
            or base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != value):
        raise RecoveryV2Error(f"Base64url no canónico: {name}.")
    return raw


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _verify_signed(
    envelope: Any, pins: Mapping[str, PinnedPedidosKey],
    expected_type: str, fields: frozenset[str],
) -> dict[str, Any]:
    data = _exact(envelope, {"payload", "signature_b64"}, "sobre firmado")
    payload = _exact(data["payload"], fields, expected_type)
    if payload["type"] != expected_type:
        raise RecoveryV2Error("Versión/tipo de protocolo desconocido.")
    key_id = _uuid(payload["key_id"], "key_id")
    pin = pins.get(key_id)
    if (type(pin) is not PinnedPedidosKey or pin.revoked is not False
            or pin.format_version != 1):
        raise RecoveryV2Error("Clave Pedidos desconocida, revocada o incompatible.")
    public = _unb64(pin.public_key_b64, 32, "clave pública")
    signature = _unb64(data["signature_b64"], 64, "firma")
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(
            signature, canonical_json(payload)
        )
    except (InvalidSignature, ValueError) as exc:
        raise RecoveryV2Error("Firma Ed25519 Pedidos inválida.") from exc
    return payload


def _zip_entries(raw: bytes, names: frozenset[str]) -> dict[str, bytes]:
    if type(raw) is not bytes or not raw or len(raw) > MAX_ZIP_BYTES:
        raise RecoveryV2Error("ZIP ausente o sobredimensionado.")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if (len(infos) != len(names) or {info.filename for info in infos} != names
                    or sum(info.file_size for info in infos) > MAX_ZIP_BYTES):
                raise RecoveryV2Error("ZIP parcial, duplicado o demasiado grande.")
            result = {}
            for info in infos:
                mode = (info.external_attr >> 16) & 0o170000
                if (info.flag_bits & 1 or mode == 0o120000
                        or info.compress_type not in (
                            zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED
                        )
                        or info.file_size > MAX_ZIP_BYTES):
                    raise RecoveryV2Error("Entrada ZIP insegura.")
                with archive.open(info) as stream:
                    data = stream.read(MAX_ZIP_BYTES + 1)
                    if stream.read(1) or len(data) != info.file_size:
                        raise RecoveryV2Error("Entrada ZIP corrupta o excesiva.")
                    result[info.filename] = data
            if sum(map(len, result.values())) > MAX_ZIP_BYTES:
                raise RecoveryV2Error("ZIP descomprimido excesivo.")
            return result
    except (zipfile.BadZipFile, RuntimeError, OSError, EOFError) as exc:
        raise RecoveryV2Error("ZIP corrupto o ilegible.") from exc


def _jsonl(raw: bytes, count: Any, name: str) -> tuple[dict[str, Any], ...]:
    if type(count) is not int or not 0 <= count <= MAX_ROWS:
        raise RecoveryV2Error(f"Conteo inválido: {name}.")
    if not raw and count == 0:
        return ()
    if not raw.endswith(b"\n"):
        raise RecoveryV2Error(f"JSONL truncado: {name}.")
    lines = raw.splitlines()
    if len(lines) != count:
        raise RecoveryV2Error(f"Conteo no coincide: {name}.")
    rows = []
    for line in lines:
        row = _json(line, maximum=MAX_ZIP_BYTES)
        if type(row) is not dict or line != canonical_json(row):
            raise RecoveryV2Error(f"Fila no canónica: {name}.")
        rows.append(row)
    return tuple(rows)


def _order(row: Any, sender_id: int) -> tuple[int, str]:
    if type(row) is not dict:
        raise RecoveryV2Error("Pedido no es objeto.")
    try:
        _pedido(row, "recovery.order", frozenset({sender_id}))
    except (ErrorContratoPedidos, KeyError, ValueError, TypeError) as exc:
        raise RecoveryV2Error("Pedido API v2 inválido.") from exc
    return sender_id, _uuid(row["codigo_publico"], "codigo_publico")


def _identity(value: Any, *, hashed: bool = False) -> tuple[int, str]:
    fields = {"sender_id", "codigo_publico", "order_sha256"} if hashed else {
        "sender_id", "codigo_publico"
    }
    row = _exact(value, fields, "identidad")
    sender = row["sender_id"]
    if type(sender) is not int or sender <= 0:
        raise RecoveryV2Error("sender_id inválido.")
    pair = sender, _uuid(row["codigo_publico"], "codigo_publico")
    if hashed:
        _sha(row["order_sha256"], "order_sha256")
    return pair


def _ordered_unique(pairs: Sequence[tuple[int, str]], name: str) -> None:
    if list(pairs) != sorted(set(pairs)):
        raise RecoveryV2Error(f"{name} desordenado o duplicado.")


def verify_baseline(
    snapshot: bytes, *, expected_snapshot_sha256: str,
    pedidos_keys: Mapping[str, PinnedPedidosKey], edge_id: str,
    pos_branch_id: str, sender_ids: Sequence[int],
    expected_prestate: Mapping[str, Any],
) -> BaselineV2:
    """Verifica ZIP/firma/alcance/preestado; nunca aplica ni mueve cursor."""
    if sha256(snapshot) != _sha(expected_snapshot_sha256, "snapshot_sha256"):
        raise RecoveryV2Error("SHA externo del ZIP no coincide.")
    edge = _uuid(edge_id, "edge_id")
    branch = _uuid(pos_branch_id, "pos_branch_id")
    senders = _senders(list(sender_ids))
    entries = _zip_entries(snapshot, BASELINE_MEMBERS)
    manifest_raw = entries["manifest.json"]
    envelope = _json(
        manifest_raw, maximum=MAX_MANIFEST_BYTES, canonical_file=True
    )
    payload = _verify_signed(
        envelope, pedidos_keys, "pedidos.recovery_manifest.v2", MANIFEST_FIELDS
    )
    for field in ("recovery_id", "freeze_id"):
        _uuid(payload[field], field)
    for field in ("pos_prestate_sha256", "purge_epoch", "orders_sha256",
                  "recovered_orders_sha256", "tombstones_sha256"):
        _sha(payload[field], field)
    if (payload["edge_id"] != edge or payload["pos_branch_id"] != branch
            or _senders(payload["sender_ids"]) != senders):
        raise RecoveryV2Error("Identidad o alcance de baseline ajeno.")
    state = _prestate(payload["pos_prestate"], senders)
    if (type(expected_prestate) is not dict or state != expected_prestate
            or sha256(canonical_json(state)) != payload["pos_prestate_sha256"]):
        raise RecoveryV2Error("Preestado firmado no coincide con SQLite.")
    start = _utc(payload["desde"], "desde")
    end = _utc(payload["hasta"], "hasta")
    if (start >= end or state["ventana_desde"] != payload["desde"]
            or state["ventana_hasta"] != payload["hasta"]
            or payload["next_desde"] != payload["hasta"]
            or payload["next_cursor"] is not None):
        raise RecoveryV2Error("Ventana/continuidad de baseline inválida.")
    for name, field in (
        ("orders.jsonl", "orders_sha256"),
        ("recovered_orders.jsonl", "recovered_orders_sha256"),
        ("tombstones.json", "tombstones_sha256"),
    ):
        if sha256(entries[name]) != payload[field]:
            raise RecoveryV2Error(f"Hash de {name} no coincide.")
    orders = _jsonl(entries["orders.jsonl"], payload["orders_count"], "orders")
    recovered = _jsonl(
        entries["recovered_orders.jsonl"], payload["recovered_orders_count"],
        "recovered_orders",
    )
    tombstones_raw = entries["tombstones.json"]
    if len(tombstones_raw) > MAX_TOMBSTONES_BYTES:
        raise RecoveryV2Error("Tombstones sobredimensionados.")
    tombstones = _json(
        tombstones_raw, maximum=MAX_TOMBSTONES_BYTES, canonical_file=True
    )
    if (type(tombstones) is not list
            or type(payload["tombstones_count"]) is not int
            or len(tombstones) != payload["tombstones_count"]
            or len(tombstones) > MAX_ROWS):
        raise RecoveryV2Error("Conteo de tombstones inválido.")
    hashes: dict[tuple[int, str], str] = {}
    order_pairs = []
    for row in orders:
        item = _exact(row, {"sender_id", "order"}, "orders.jsonl")
        sender = item["sender_id"]
        if type(sender) is not int or sender not in senders:
            raise RecoveryV2Error("Remitente de pedido ajeno.")
        pair = _order(item["order"], sender)
        order_pairs.append(pair)
        hashes[pair] = sha256(canonical_json(item["order"]))
    _ordered_unique(order_pairs, "Pedidos")
    recovered_pairs = []
    recovered_map = {}
    for row in recovered:
        item = _exact(
            row, {"sender_id", "order", "archive_row_sha256",
                  "exportacion_id", "archive_sha256"}, "recovered_orders.jsonl"
        )
        sender = item["sender_id"]
        if type(sender) is not int or sender not in senders:
            raise RecoveryV2Error("Remitente recuperado ajeno.")
        pair = _order(item["order"], sender)
        _sha(item["archive_row_sha256"], "archive_row_sha256")
        _sha(item["archive_sha256"], "archive_sha256")
        _uuid(item["exportacion_id"], "exportacion_id")
        recovered_pairs.append(pair)
        recovered_map[pair] = item
        if pair in hashes:
            raise RecoveryV2Error("Pedido vigente duplicado como recuperado.")
        hashes[pair] = sha256(canonical_json(item["order"]))
    _ordered_unique(recovered_pairs, "Recuperados")
    tombstone_pairs = []
    tombstone_map = {}
    for row in tombstones:
        item = _exact(
            row, {"sender_id", "codigo_publico", "pedido_id_origen",
                  "exportacion_id", "archive_sha256"}, "tombstone"
        )
        pair = _identity({
            "sender_id": item["sender_id"],
            "codigo_publico": item["codigo_publico"],
        })
        if pair[0] not in senders or pair in order_pairs:
            raise RecoveryV2Error("Tombstone ajeno o solapado con pedido vigente.")
        if type(item["pedido_id_origen"]) is not int or item["pedido_id_origen"] <= 0:
            raise RecoveryV2Error("ID original de tombstone inválido.")
        for field, validator in (
            ("exportacion_id", _uuid), ("archive_sha256", _sha)
        ):
            if item[field] is not None:
                validator(item[field], field)
        if (item["exportacion_id"] is None) != (item["archive_sha256"] is None):
            raise RecoveryV2Error("Referencia de archivo parcial.")
        tombstone_pairs.append(pair)
        tombstone_map[pair] = item
    _ordered_unique(tombstone_pairs, "Tombstones")
    if not set(recovered_map) <= set(tombstone_map):
        raise RecoveryV2Error("Recuperado sin tombstone.")
    for pair, row in recovered_map.items():
        tomb = tombstone_map[pair]
        if (row["order"]["id"] != tomb["pedido_id_origen"]
                or row["exportacion_id"] != tomb["exportacion_id"]
                or row["archive_sha256"] != tomb["archive_sha256"]):
            raise RecoveryV2Error("Recuperado no coincide con tombstone.")
    return BaselineV2(
        payload=payload, snapshot_sha256=expected_snapshot_sha256,
        manifest_sha256=sha256(manifest_raw), orders=orders,
        recovered_orders=recovered, tombstones=tuple(tombstones),
        order_hashes=hashes,
    )


def _archive_to_api_v2(row: dict[str, Any]) -> dict[str, Any]:
    """Reproduce la conversión determinista r7 de la fila de exportación."""
    required = {"id", "codigo_publico", "sucursal", "fecha_confirmacion",
                "total", "estado", "eliminado", "items"}
    if type(row) is not dict or not required <= set(row):
        raise RecoveryV2Error("Fila archivada incompleta.")
    if (row["estado"] != "confirmado" or row["eliminado"] is not False
            or type(row["items"]) is not list):
        raise RecoveryV2Error("Fila archivada no es pedido confirmado.")
    _exact(row["sucursal"], {"id", "nombre", "tipo"}, "sucursal archivada")
    result_items = []
    for item in row["items"]:
        _exact(
            item, {"id", "producto_id", "producto_nombre",
                   "producto_nombre_ticket", "unidad_medida", "unidad_abreviatura",
                   "cantidad_por_precio", "cantidad", "precio_unitario", "subtotal"},
            "item archivado",
        )
        result_items.append({
            "id": item["id"], "pedido_id": row["id"],
            "producto": {
                "id": item["producto_id"], "nombre": item["producto_nombre"],
                "nombre_ticket": item["producto_nombre_ticket"],
                "unidad_medida": item["unidad_medida"],
                "unidad_abreviatura": item["unidad_abreviatura"],
                "cantidad_por_precio": item["cantidad_por_precio"],
            },
            "cantidad": item["cantidad"],
            "precio_unitario": item["precio_unitario"], "subtotal": item["subtotal"],
        })
    stamp = row["fecha_confirmacion"]
    if type(stamp) is not str or len(stamp) > 64:
        raise RecoveryV2Error("Fecha archivada inválida.")
    try:
        date = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RecoveryV2Error("Fecha archivada inválida.") from exc
    if date.tzinfo is None or date.utcoffset() is None:
        raise RecoveryV2Error("Fecha archivada sin zona horaria.")
    date = date.astimezone(timezone.utc)
    result = {
        "id": row["id"], "codigo_publico": row["codigo_publico"],
        "fecha_confirmacion": date.isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        ),
        "total": row["total"], "sucursal": row["sucursal"], "items": result_items,
    }
    _order(result, row["sucursal"]["id"])
    return result


def verify_archive_rows(
    baseline: BaselineV2, archive_bytes: bytes, exportacion_id: str,
) -> Mapping[tuple[int, str], ArchivedRowProof]:
    """Prueba filas originales, incluido estado terminal, contra ZIP custodio.

    Un estado enviado/recibido nunca se declara importable. El llamador lo
    registra como unresolved/manual; ningún contenido terminal se reabre.
    """
    export_id = _uuid(exportacion_id, "exportacion_id")
    entries = _zip_entries(archive_bytes, ARCHIVE_MEMBERS)
    manifest = _json(entries["manifest.json"], maximum=MAX_MANIFEST_BYTES)
    if type(manifest) is not dict or manifest.get("lote_id") != export_id:
        raise RecoveryV2Error("Manifest de archivo no corresponde a exportación.")
    lines = entries["pedidos.jsonl"]
    if manifest.get("sha256_pedidos_jsonl") != sha256(lines):
        raise RecoveryV2Error("Hash de archivo custodio inválido.")
    tombstones = {
        (row["sender_id"], row["codigo_publico"]): row
        for row in baseline.tombstones
        if row["exportacion_id"] == export_id
    }
    if not tombstones:
        raise RecoveryV2Error("Archivo sin tombstones esperados.")
    archive_hash = sha256(archive_bytes)
    if any(row["archive_sha256"] != archive_hash for row in tombstones.values()):
        raise RecoveryV2Error("ZIP custodio alterado.")
    recovered = {
        (row["sender_id"], row["order"]["codigo_publico"]): row
        for row in baseline.recovered_orders
        if row["exportacion_id"] == export_id
    }
    if not lines.endswith(b"\n") or len(lines.splitlines()) > MAX_ROWS:
        raise RecoveryV2Error("JSONL de archivo custodio inválido.")
    proofs: dict[tuple[int, str], ArchivedRowProof] = {}
    for line in lines.splitlines():
        row = _json(line, maximum=MAX_ZIP_BYTES)
        if type(row) is not dict:
            raise RecoveryV2Error("Fila de archivo custodio inválida.")
        branch = row.get("sucursal")
        if type(branch) is not dict:
            raise RecoveryV2Error("Sucursal de archivo inválida.")
        pair = _identity({
            "sender_id": branch.get("id"),
            "codigo_publico": row.get("codigo_publico"),
        })
        tomb = tombstones.get(pair)
        if tomb is None:
            continue
        row_hash = sha256(canonical_json(row))
        if pair in proofs or row.get("id") != tomb["pedido_id_origen"]:
            raise RecoveryV2Error("Identidad original duplicada o incompatible.")
        status = row.get("estado")
        deleted = row.get("eliminado")
        if type(status) is not str or type(deleted) is not bool:
            raise RecoveryV2Error("Estado archivado inválido.")
        matched = recovered.get(pair)
        if matched is not None and row_hash != matched["archive_row_sha256"]:
            raise RecoveryV2Error("Hash de fila archivada incompatible.")
        importable = False
        if status == "confirmado" and deleted is False and matched is not None:
            importable = _archive_to_api_v2(row) == matched["order"]
            if not importable:
                raise RecoveryV2Error("Conversión archivada difiere de baseline.")
        elif matched is not None:
            raise RecoveryV2Error("Orden recuperada marcada como terminal.")
        proofs[pair] = ArchivedRowProof(
            estado=status, eliminado=deleted,
            row_sha256=row_hash, importable=importable,
        )
    if set(proofs) != set(tombstones):
        raise RecoveryV2Error("Faltan filas originales de tombstones.")
    return proofs


def verify_archive_proof(
    baseline: BaselineV2, archive_bytes: bytes, exportacion_id: str,
) -> frozenset[tuple[int, str]]:
    """API compatible: sólo parejas confirmadas con contenido v2 probado."""
    return frozenset(
        pair for pair, proof in verify_archive_rows(
            baseline, archive_bytes, exportacion_id
        ).items() if proof.importable
    )


def _ack_payload(
    baseline: BaselineV2, *, key_id: str, nonce: str, issued_at: str,
    received: Sequence[dict[str, Any]], unresolved: Sequence[dict[str, Any]],
    archive_proven: Sequence[tuple[int, str]],
) -> dict[str, Any]:
    received_map: dict[tuple[int, str], str] = {}
    received_pairs = []
    for row in received:
        pair = _identity(row, hashed=True)
        received_pairs.append(pair)
        if (pair not in baseline.order_hashes
                or row["order_sha256"] != baseline.order_hashes[pair]):
            raise RecoveryV2Error("ACK afirma pedido no importado o hash distinto.")
        received_map[pair] = row["order_sha256"]
    unresolved_pairs = [_identity(row) for row in unresolved]
    _ordered_unique(received_pairs, "received")
    _ordered_unique(unresolved_pairs, "unresolved")
    if (set(received_pairs) & set(unresolved_pairs)
            or set(received_pairs) | set(unresolved_pairs) != baseline.identities):
        raise RecoveryV2Error("ACK sin cobertura exacta o con solapamiento.")
    if not ((baseline.recovered_identities & set(received_pairs)) <= set(archive_proven)):
        raise RecoveryV2Error("Recuperado sin prueba de ZIP custodio.")
    manifest = baseline.payload
    return {
        "type": "pedidos.edge.recovery_ack.v2",
        "key_id": _uuid(key_id, "key_id"),
        "nonce": _uuid(nonce, "nonce"),
        "issued_at": issued_at,
        "recovery_id": manifest["recovery_id"],
        "edge_id": manifest["edge_id"],
        "pos_branch_id": manifest["pos_branch_id"],
        "sender_ids": manifest["sender_ids"],
        "pos_prestate_sha256": manifest["pos_prestate_sha256"],
        "snapshot_sha256": baseline.snapshot_sha256,
        "manifest_sha256": baseline.manifest_sha256,
        "orders_sha256": manifest["orders_sha256"],
        "tombstones_sha256": manifest["tombstones_sha256"],
        "received": list(received), "unresolved": list(unresolved),
    }


def sign_edge_ack(
    baseline: BaselineV2, *, edge_key_id: str,
    edge_private_key: Ed25519PrivateKey, nonce: str, issued_at: str,
    received: Sequence[dict[str, Any]], unresolved: Sequence[dict[str, Any]],
    archive_proven: Sequence[tuple[int, str]] = (),
) -> bytes:
    """Firma ACK nuevo. El llamador persiste nonce y bytes ANTES de transmitir."""
    _utc(issued_at, "issued_at")
    if not isinstance(edge_private_key, Ed25519PrivateKey):
        raise RecoveryV2Error("Clave privada Edge no válida.")
    payload = _ack_payload(
        baseline, key_id=edge_key_id, nonce=nonce, issued_at=issued_at,
        received=received, unresolved=unresolved, archive_proven=archive_proven,
    )
    signature = edge_private_key.sign(canonical_json(payload))
    raw = canonical_json({
        "payload": payload, "signature_b64": _b64(signature)
    }) + b"\n"
    if len(raw) > MAX_ACK_BYTES:
        raise RecoveryV2Error("ACK sobredimensionado.")
    return raw


def verify_receipt(
    baseline: BaselineV2, ack_bytes: bytes, receipt_bytes: bytes, *,
    pedidos_keys: Mapping[str, PinnedPedidosKey],
    archive_proven: Sequence[tuple[int, str]] = (),
) -> VerifiedReceiptV2:
    """Verifica recibo y vínculos; completed sólo autoriza CAS externo."""
    ack = _json(ack_bytes, maximum=MAX_ACK_BYTES, canonical_file=True)
    ack_payload = _exact(
        _exact(ack, {"payload", "signature_b64"}, "ACK")["payload"],
        ACK_FIELDS, "ACK payload",
    )
    if ack_payload["type"] != "pedidos.edge.recovery_ack.v2":
        raise RecoveryV2Error("Versión de ACK desconocida.")
    _unb64(ack["signature_b64"], 64, "firma Edge ACK")
    _utc(ack_payload["issued_at"], "ACK.issued_at")
    expected_ack = _ack_payload(
        baseline, key_id=ack_payload["key_id"], nonce=ack_payload["nonce"],
        issued_at=ack_payload["issued_at"],
        received=ack_payload["received"], unresolved=ack_payload["unresolved"],
        archive_proven=archive_proven,
    )
    for field in (
        "recovery_id", "edge_id", "pos_branch_id", "sender_ids",
        "pos_prestate_sha256", "snapshot_sha256", "manifest_sha256",
        "orders_sha256", "tombstones_sha256",
    ):
        if ack_payload[field] != expected_ack[field]:
            raise RecoveryV2Error("ACK no corresponde a baseline.")
    envelope = _json(
        receipt_bytes, maximum=MAX_RECEIPT_BYTES, canonical_file=True
    )
    receipt = _verify_signed(
        envelope, pedidos_keys, "pedidos.recovery_receipt.v2", RECEIPT_FIELDS
    )
    _utc(receipt["issued_at"], "receipt.issued_at")
    for field in (
        "recovery_id", "edge_id", "pos_branch_id", "sender_ids",
        "pos_prestate_sha256", "snapshot_sha256", "manifest_sha256",
    ):
        expected = (baseline.snapshot_sha256 if field == "snapshot_sha256"
                    else baseline.manifest_sha256 if field == "manifest_sha256"
                    else baseline.payload[field])
        if receipt[field] != expected:
            raise RecoveryV2Error("Recibo no corresponde a baseline.")
    if receipt["edge_ack_sha256"] != sha256(ack_bytes):
        raise RecoveryV2Error("Recibo no corresponde al ACK exacto.")
    status = receipt["status"]
    if status not in ("completada", "intervencion_manual"):
        raise RecoveryV2Error("Estado de recibo desconocido.")
    if receipt["next_cursor"] is not None:
        raise RecoveryV2Error("Cursor de recibo inválido.")
    if status == "completada":
        if (receipt["next_desde"] != baseline.payload["hasta"]
                or ack_payload["unresolved"]
                or set(_identity(item, hashed=True) for item in ack_payload["received"])
                != baseline.identities):
            raise RecoveryV2Error("Recibo completado sin cobertura verificable.")
    elif receipt["next_desde"] is not None:
        raise RecoveryV2Error("Recibo manual intenta avanzar ventana.")
    return VerifiedReceiptV2(
        payload=receipt, completed=status == "completada",
        receipt_sha256=sha256(receipt_bytes),
    )
