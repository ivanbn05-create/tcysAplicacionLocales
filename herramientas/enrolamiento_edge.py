"""Cliente Edge para el contrato Central enrollment-v1 privado y apagado por defecto.

La tarjeta es privada y contiene un código de un uso más UUID esperados. Nunca
se reintenta un timeout ambiguo: Central pudo consumir el código.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import ssl
import sys
from urllib import error, parse, request
import uuid

MAX_CARD_BYTES = 4096
MAX_RESPONSE_BYTES = 65536
DEFAULT_ENDPOINT = "/api/v1/enrollment/claim/"
BRANCH_CODES = frozenset({
    "ARBOLEDAS", "AGUILAS", "ESTANCIA", "PLAZA_DEL_SOL", "SANTA_ANITA",
})
CORE_MODULES = frozenset({
    "pos", "catalogo", "impresion", "respaldos", "domicilios",
    "pedidos_programados", "reparto",
})
OPTIONAL_MODULES = frozenset({"pedidos_sucursales"})
CODE_RE = re.compile(r"^tcys-enroll-v1_[A-Za-z0-9_-]{43}$")
TOKEN_RE = re.compile(r"^tcys_[A-Za-z0-9_-]{64}$")
INGEST_SCOPES = frozenset({
    "sales:v2:write", "customers:v2:write", "terminals:v1:write",
})
CATALOG_SCOPES = frozenset({"catalog:v2:read", "catalog:v2:ack"})
CATALOG_SCOPES_V3 = frozenset({"catalog:v3:read", "catalog:v3:ack"})
CATALOG_SCOPES_TRANSITION = CATALOG_SCOPES | CATALOG_SCOPES_V3


class EnrollmentError(ValueError):
    """Fallo seguro de enrolamiento sin incluir secretos."""


@dataclass(frozen=True)
class EnrollmentCard:
    code: str
    expected_branch_id: str
    expected_edge_id: str
    request_id: str


@dataclass(frozen=True)
class EnrollmentReceipt:
    branch_id: str
    branch_code: str
    branch_name: str
    edge_id: str
    edge_label: str
    modules: tuple[str, ...]
    central_ingest_token: str
    central_catalog_token: str
    ingest_credential_id: str
    catalog_credential_id: str
    request_id: str

    def private_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "request_id": self.request_id,
            "branch": {
                "id": self.branch_id,
                "code": self.branch_code,
                "name": self.branch_name,
            },
            "edge": {"id": self.edge_id, "label": self.edge_label},
            "modules": list(self.modules),
            "credentials": {
                "central_ingest_token": self.central_ingest_token,
                "central_catalog_token": self.central_catalog_token,
                "ingest_credential_id": self.ingest_credential_id,
                "catalog_credential_id": self.catalog_credential_id,
            },
        }


def _uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise EnrollmentError(f"{label} debe ser UUID.")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise EnrollmentError(f"{label} debe ser UUID.") from exc
    if parsed.int == 0:
        raise EnrollmentError(f"{label} no puede ser UUID vacío.")
    return str(parsed)


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EnrollmentError("JSON con claves duplicadas.")
        result[key] = value
    return result


def _parse_json(payload: bytes, limit: int) -> object:
    if len(payload) > limit:
        raise EnrollmentError("Documento de enrolamiento demasiado grande.")
    try:
        return json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise EnrollmentError("Documento de enrolamiento inválido.") from exc


def _only_keys(value: object, required: set[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != required:
        raise EnrollmentError(f"{label} no cumple el contrato.")
    return value


def validate_card(payload: bytes) -> EnrollmentCard:
    data = _only_keys(
        _parse_json(payload, MAX_CARD_BYTES),
        {"code", "expected_branch_id", "expected_edge_id", "request_id"},
        "Tarjeta",
    )
    code = data["code"]
    if not isinstance(code, str) or not CODE_RE.fullmatch(code):
        raise EnrollmentError("Código de tarjeta inválido.")
    branch_id = _uuid(data["expected_branch_id"], "expected_branch_id")
    edge_id = _uuid(data["expected_edge_id"], "expected_edge_id")
    request_id = _uuid(data["request_id"], "request_id")
    if len({branch_id, edge_id, request_id}) != 3:
        raise EnrollmentError("La tarjeta reutiliza UUID de identidad.")
    return EnrollmentCard(code, branch_id, edge_id, request_id)


def read_private_card(path: Path) -> EnrollmentCard:
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise EnrollmentError("La tarjeta debe ser un archivo privado absoluto.")
    if path.stat().st_size > MAX_CARD_BYTES:
        raise EnrollmentError("Tarjeta demasiado grande.")
    return validate_card(path.read_bytes())


def _credential(
    value: object,
    expected_scopes: frozenset[str] | tuple[frozenset[str], ...],
    label: str,
) -> tuple[str, str]:
    data = _only_keys(
        value, {"token", "credential_id", "scopes", "expires_at"}, label,
    )
    token = data["token"]
    if not isinstance(token, str) or not TOKEN_RE.fullmatch(token):
        raise EnrollmentError(f"{label} contiene un token inválido.")
    credential_id = _uuid(data["credential_id"], f"{label}.credential_id")
    scopes = data["scopes"]
    allowed = expected_scopes if isinstance(expected_scopes, tuple) else (expected_scopes,)
    if (
        not isinstance(scopes, list) or any(not isinstance(scope, str) for scope in scopes)
        or len(scopes) != len(set(scopes)) or frozenset(scopes) not in allowed
    ):
        raise EnrollmentError(f"{label} tiene scopes inesperados.")
    expires_at = data["expires_at"]
    if not isinstance(expires_at, str):
        raise EnrollmentError(f"{label} no tiene vencimiento válido.")
    try:
        expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EnrollmentError(f"{label} no tiene vencimiento válido.") from exc
    if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
        raise EnrollmentError(f"{label} tiene una credencial vencida.")
    return token, credential_id


def validate_receipt(payload: bytes, card: EnrollmentCard) -> EnrollmentReceipt:
    data = _only_keys(
        _parse_json(payload, MAX_RESPONSE_BYTES),
        {"version", "request_id", "branch", "edge", "modules", "credentials"},
        "Respuesta",
    )
    if data["version"] != "1.0":
        raise EnrollmentError("Versión de contrato de enrolamiento desconocida.")
    if _uuid(data["request_id"], "request_id") != card.request_id:
        raise EnrollmentError("La respuesta no corresponde a esta solicitud.")
    branch = _only_keys(data["branch"], {"id", "code", "name"}, "Sucursal")
    edge = _only_keys(data["edge"], {"id", "label"}, "Edge")
    branch_id = _uuid(branch["id"], "branch.id")
    edge_id = _uuid(edge["id"], "edge.id")
    if branch_id != card.expected_branch_id or edge_id != card.expected_edge_id:
        raise EnrollmentError("Identidad Central discordante con la tarjeta.")
    code = branch["code"]
    if not isinstance(code, str) or code not in BRANCH_CODES:
        raise EnrollmentError("Sucursal no reconocida para Production 1.0.")
    name = branch["name"]
    label = edge["label"]
    if (
        not isinstance(name, str) or not 1 <= len(name.strip()) <= 120
        or not isinstance(label, str) or not 1 <= len(label.strip()) <= 100
        or any(ord(ch) < 32 for ch in name + label)
    ):
        raise EnrollmentError("Nombre o etiqueta de identidad inválida.")
    modules = data["modules"]
    if not isinstance(modules, list) or any(not isinstance(m, str) for m in modules):
        raise EnrollmentError("Módulos inválidos.")
    module_set = set(modules)
    if (
        len(module_set) != len(modules) or not CORE_MODULES <= module_set
        or not module_set <= CORE_MODULES | OPTIONAL_MODULES
    ):
        raise EnrollmentError("Módulos fuera del catálogo autorizado.")
    if "pedidos_sucursales" in module_set and code != "ARBOLEDAS":
        raise EnrollmentError("Pedidos de sucursales no autorizado para este piloto.")
    credentials = _only_keys(data["credentials"], {"ingest", "catalog"}, "Credenciales")
    ingest, ingest_id = _credential(credentials["ingest"], INGEST_SCOPES, "Ingesta")
    catalog, catalog_id = _credential(
        credentials["catalog"],
        (CATALOG_SCOPES, CATALOG_SCOPES_V3, CATALOG_SCOPES_TRANSITION),
        "Catálogo",
    )
    if ingest == catalog or ingest_id == catalog_id:
        raise EnrollmentError("Credenciales remotas reutilizadas.")
    return EnrollmentReceipt(
        branch_id=branch_id, branch_code=code, branch_name=name.strip(),
        edge_id=edge_id, edge_label=label.strip(), modules=tuple(sorted(module_set)),
        central_ingest_token=ingest, central_catalog_token=catalog,
        ingest_credential_id=ingest_id, catalog_credential_id=catalog_id,
        request_id=card.request_id,
    )


def _endpoint_url(base_url: str, endpoint: str) -> str:
    base = parse.urlsplit(base_url)
    if (
        base.scheme != "https" or not base.hostname or base.username or base.password
        or base.query or base.fragment or base.path not in ("", "/")
    ):
        raise EnrollmentError("Central debe ser un origen HTTPS sin credenciales.")
    suffix = parse.urlsplit(endpoint)
    if (
        not endpoint.startswith("/") or endpoint.startswith("//")
        or suffix.scheme or suffix.netloc or suffix.query or suffix.fragment
        or suffix.path != endpoint or ".." in endpoint.split("/")
    ):
        raise EnrollmentError("Ruta de enrolamiento inválida.")
    return parse.urlunsplit((base.scheme, base.netloc, endpoint, "", ""))


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise EnrollmentError("Central intentó redirigir el enrolamiento.")


def claim(
    base_url: str,
    card: EnrollmentCard,
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    ca_bundle: str | None = None,
    opener: object | None = None,
) -> EnrollmentReceipt:
    url = _endpoint_url(base_url, endpoint)
    body = json.dumps(
        {
            "code": card.code,
            "expected_branch_id": card.expected_branch_id,
            "expected_edge_id": card.expected_edge_id,
            "request_id": card.request_id,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    http_request = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    if opener is None:
        context = ssl.create_default_context(cafile=ca_bundle)
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        opener = request.build_opener(request.HTTPSHandler(context=context), _NoRedirect())
    try:
        with opener.open(http_request, timeout=15) as response:
            if response.status != 201 or response.headers.get_content_type() != "application/json":
                raise EnrollmentError("Central rechazó el enrolamiento o devolvió otro formato.")
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except error.HTTPError as exc:
        if exc.code in (400, 401, 403, 404, 409, 410, 422, 426, 429):
            raise EnrollmentError("Tarjeta inválida, vencida, consumida o no autorizada; solicita otra.") from None
        raise EnrollmentError("Central no completó el enrolamiento; confirma el estado antes de reintentar.") from None
    except (error.URLError, TimeoutError, OSError):
        raise EnrollmentError("Estado incierto; confirma en Central antes de usar otra tarjeta.") from None
    return validate_receipt(payload, card)


def write_private_receipt(receipt: EnrollmentReceipt, path: Path) -> None:
    if not path.is_absolute() or not path.parent.is_dir():
        raise EnrollmentError("El recibo requiere una carpeta privada absoluta existente.")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(receipt.private_dict(), stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reclamar tarjeta Edge Production 1.0")
    parser.add_argument("--central-url", required=True)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--ca-bundle")
    parser.add_argument("--card", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        card = read_private_card(args.card)
        receipt = claim(args.central_url, card, endpoint=args.endpoint, ca_bundle=args.ca_bundle)
        write_private_receipt(receipt, args.receipt)
    except EnrollmentError as exc:
        print(f"Enrolamiento detenido: {exc}", file=sys.stderr)
        return 1
    print(f"Identidad recibida: {receipt.branch_name} ({receipt.branch_code}), Edge {receipt.edge_id}.")
    print("Confirma la identidad en el instalador antes de aplicar el recibo privado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())