"""Valida el contrato Pedidos v2 con biblioteca estándar y el cliente POS real.

No abre red, no carga Django y no usa credenciales reales.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


CONTRACT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = CONTRACT_ROOT.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ventas.pedidos_api_v2 import (  # noqa: E402
    ClientePedidosV2,
    ErrorAlcancePedidos,
    ErrorAutenticacionPedidos,
    ErrorBrechaRetencion,
    ErrorContratoPedidos,
    EstadoSincronizacionPedidos,
    RespuestaHTTP,
    parsear_pagina_v2,
)


INDEX_PATH = CONTRACT_ROOT / "fixtures" / "index.json"
URL = "https://pedidos.example.invalid/api/v2/pos/pedidos/"
TOKEN_SINTETICO = "token-sintetico-pedidos-v2-" + ("x" * 32)
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$"
)


class ContractError(ValueError):
    pass


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"Clave JSON duplicada: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text, object_pairs_hook=_object_without_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"JSON inválido: {path}") from exc


def exact_keys(value: Any, expected: set[str], path: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != expected:
        raise ContractError(f"{path}: propiedades distintas de {sorted(expected)}")
    return value


def decimal_text(
    value: Any,
    *,
    max_integers: int,
    max_decimals: int,
    positive: bool,
    path: str,
) -> Decimal:
    if type(value) is not str or len(value) > 64:
        raise ContractError(f"{path}: decimal no textual o demasiado largo")
    match = re.fullmatch(r"-?([0-9]+)(?:\.([0-9]+))?", value)
    if match is None:
        raise ContractError(f"{path}: sintaxis decimal inválida")
    integer_digits = match.group(1).lstrip("0") or "0"
    fraction = match.group(2) or ""
    if len(integer_digits) > max_integers or len(fraction) > max_decimals:
        raise ContractError(f"{path}: magnitud o escala fuera de rango")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ContractError(f"{path}: decimal inválido") from exc
    if not result.is_finite() or result < 0 or (positive and result <= 0):
        raise ContractError(f"{path}: decimal fuera de rango")
    return result


def validate_page(body: Any, *, request_id: str, requested_limit: int) -> None:
    root = exact_keys(body, {"version", "request_id", "data", "page"}, "respuesta")
    if root["version"] != "v2" or root["request_id"] != request_id:
        raise ContractError("Versión o request_id inconsistente")
    if REQUEST_ID_RE.fullmatch(request_id) is None:
        raise ContractError("request_id inválido")
    if type(root["data"]) is not list or len(root["data"]) > requested_limit:
        raise ContractError("data excede el límite solicitado")

    order_keys: list[tuple[datetime, int]] = []
    ids: set[int] = set()
    public_codes: set[str] = set()
    for order_index, raw_order in enumerate(root["data"]):
        path = f"data[{order_index}]"
        order = exact_keys(
            raw_order,
            {"id", "codigo_publico", "fecha_confirmacion", "total", "sucursal", "items"},
            path,
        )
        if type(order["id"]) is not int or order["id"] <= 0:
            raise ContractError(f"{path}.id inválido")
        try:
            parsed_uuid = uuid.UUID(order["codigo_publico"])
        except (ValueError, TypeError, AttributeError) as exc:
            raise ContractError(f"{path}.codigo_publico inválido") from exc
        if str(parsed_uuid) != order["codigo_publico"]:
            raise ContractError(f"{path}.codigo_publico no canónico")
        if order["id"] in ids or order["codigo_publico"] in public_codes:
            raise ContractError("Identidad de Pedido duplicada")
        ids.add(order["id"])
        public_codes.add(order["codigo_publico"])

        timestamp = order["fecha_confirmacion"]
        if type(timestamp) is not str or TIMESTAMP_RE.fullmatch(timestamp) is None:
            raise ContractError(f"{path}.fecha_confirmacion inválida")
        parsed_timestamp = datetime.fromisoformat(timestamp[:-1] + "+00:00")
        order_keys.append((parsed_timestamp, order["id"]))

        branch = exact_keys(order["sucursal"], {"id", "nombre", "tipo"}, f"{path}.sucursal")
        if type(branch["id"]) is not int or branch["id"] <= 0:
            raise ContractError("SucursalCliente.id inválido")
        if type(branch["nombre"]) is not str or not 1 <= len(branch["nombre"]) <= 120:
            raise ContractError("Nombre display de sucursal inválido")
        if branch["tipo"] != "sucursal":
            raise ContractError("Tipo remoto no autorizado")

        items = order["items"]
        if type(items) is not list or not 1 <= len(items) <= 100:
            raise ContractError("Cantidad de conceptos inválida")
        calculated_total = Decimal("0.00")
        item_ids: set[int] = set()
        product_ids: set[int] = set()
        for item_index, raw_item in enumerate(items):
            item_path = f"{path}.items[{item_index}]"
            item = exact_keys(
                raw_item,
                {"id", "pedido_id", "producto", "cantidad", "precio_unitario", "subtotal"},
                item_path,
            )
            if type(item["id"]) is not int or item["id"] <= 0 or item["id"] in item_ids:
                raise ContractError("Item.id inválido o duplicado")
            item_ids.add(item["id"])
            if item["pedido_id"] != order["id"]:
                raise ContractError("item.pedido_id no coincide")
            product = exact_keys(
                item["producto"],
                {"id", "nombre", "nombre_ticket", "unidad_medida", "unidad_abreviatura", "cantidad_por_precio"},
                f"{item_path}.producto",
            )
            if type(product["id"]) is not int or product["id"] <= 0 or product["id"] in product_ids:
                raise ContractError("Producto.id inválido o duplicado")
            product_ids.add(product["id"])
            quantity_per_price = decimal_text(
                product["cantidad_por_precio"],
                max_integers=3,
                max_decimals=3,
                positive=True,
                path=f"{item_path}.producto.cantidad_por_precio",
            )
            quantity = decimal_text(
                item["cantidad"],
                max_integers=3,
                max_decimals=3,
                positive=True,
                path=f"{item_path}.cantidad",
            )
            unit_price = decimal_text(
                item["precio_unitario"],
                max_integers=8,
                max_decimals=2,
                positive=False,
                path=f"{item_path}.precio_unitario",
            )
            subtotal = decimal_text(
                item["subtotal"],
                max_integers=18,
                max_decimals=2,
                positive=False,
                path=f"{item_path}.subtotal",
            )
            calculated = ((quantity / quantity_per_price) * unit_price).quantize(Decimal("0.01"))
            if subtotal != calculated:
                raise ContractError("Subtotal inconsistente")
            calculated_total += subtotal

        total = decimal_text(
            order["total"],
            max_integers=18,
            max_decimals=2,
            positive=False,
            path=f"{path}.total",
        )
        if total != calculated_total.quantize(Decimal("0.01")):
            raise ContractError("Total inconsistente")

    if order_keys != sorted(order_keys) or len(order_keys) != len(set(order_keys)):
        raise ContractError("Pedidos fuera de orden")

    page = exact_keys(
        root["page"],
        {"has_more", "limit", "next_cursor", "returned"},
        "page",
    )
    if type(page["has_more"]) is not bool or page["limit"] != requested_limit:
        raise ContractError("Metadatos de página inválidos")
    if type(page["returned"]) is not int or page["returned"] != len(root["data"]):
        raise ContractError("page.returned no coincide con data")
    cursor = page["next_cursor"]
    if page["has_more"]:
        if not root["data"] or type(cursor) is not str or not 1 <= len(cursor) <= 2048:
            raise ContractError("Página intermedia sin datos/cursor")
    elif cursor is not None:
        raise ContractError("Página final con cursor")


def validate_error(body: Any, *, request_id: str) -> str:
    root = exact_keys(body, {"error"}, "respuesta_error")
    error = exact_keys(root["error"], {"code", "message", "request_id"}, "error")
    if error["request_id"] != request_id or REQUEST_ID_RE.fullmatch(request_id) is None:
        raise ContractError("request_id de error inconsistente")
    if type(error["code"]) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error["code"]) is None:
        raise ContractError("Código de error inválido")
    if type(error["message"]) is not str or not error["message"]:
        raise ContractError("Mensaje de error inválido")
    return error["code"]


class FixtureTransport:
    def __init__(self, response: RespuestaHTTP):
        self.response = response
        self.requests = []

    def __call__(self, request, *, timeout, max_response_bytes):
        self.requests.append(request)
        return self.response


class PedidosV2ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = load_json(INDEX_PATH)
        cls.cases = {case["name"]: case for case in cls.index["cases"]}

    def fixture_body(self, case: dict[str, Any]) -> tuple[bytes, Any]:
        path = CONTRACT_ROOT / case["response"]["body"]
        raw = path.read_bytes()
        return raw, load_json(path)

    def test_todos_los_json_son_utf8_sin_claves_duplicadas(self):
        for path in sorted(CONTRACT_ROOT.rglob("*.json")):
            with self.subTest(path=path.relative_to(CONTRACT_ROOT)):
                self.assertIsNotNone(load_json(path))

    def test_metadata_no_afirma_merge_deploy_y_conserva_legacy(self):
        candidate = self.index["candidate"]
        self.assertEqual(candidate["commit"], "8fad56815f856b4286a2f60f488480960e34cde5")
        self.assertEqual(candidate["status"], "candidate_unmerged_undeployed")
        self.assertTrue(candidate["v1_legacy_preserved"])
        self.assertEqual(self.index["operation"]["required_scope"], "orders:v2:read")
        serialized = json.dumps(self.index, ensure_ascii=False)
        self.assertNotIn("allowed_codigo_publico", serialized)
        self.assertNotIn("SucursalCliente.codigo_publico", serialized)
        self.assertIn("Pedido.codigo_publico", serialized)

    def test_openapi_referencias_ruta_scope_y_limites(self):
        openapi = load_json(CONTRACT_ROOT / "openapi.json")
        operation = openapi["paths"]["/api/v2/pos/pedidos/"]["get"]
        self.assertEqual(operation["x-contract-status"], "candidato-sin-merge-sin-deploy")
        self.assertEqual(operation["x-required-scope"], "orders:v2:read")
        bearer = openapi["components"]["securitySchemes"]["PedidosBearer"]
        self.assertEqual(bearer["type"], "http")
        self.assertEqual(bearer["scheme"], "bearer")
        self.assertEqual(bearer["bearerFormat"], "opaque")
        self.assertNotIn("flows", bearer)
        self.assertNotIn("tokenUrl", json.dumps(openapi))
        self.assertEqual(operation["security"], [{"PedidosBearer": []}])
        parameter_names = {
            openapi["components"]["parameters"][item["$ref"].rsplit("/", 1)[1]]["name"]
            for item in operation["parameters"]
        }
        self.assertEqual(
            parameter_names,
            {"desde", "hasta", "sucursal_id", "limite", "cursor", "X-Request-ID"},
        )
        for reference in self._external_references(openapi):
            self.assertTrue((CONTRACT_ROOT / reference).is_file(), reference)
        for schema_path in (CONTRACT_ROOT / "schemas").glob("*.json"):
            schema = load_json(schema_path)
            self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")

    def _external_references(self, value: Any):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "$ref" and isinstance(child, str) and not child.startswith("#"):
                    yield child.removeprefix("./")
                else:
                    yield from self._external_references(child)
        elif isinstance(value, list):
            for child in value:
                yield from self._external_references(child)

    def test_indice_http_tiene_rutas_headers_y_fixtures_reproducibles(self):
        self.assertEqual(len(self.cases), 7)
        for name, case in self.cases.items():
            with self.subTest(case=name):
                request = case["request"]
                response = case["response"]
                self.assertEqual(request["headers"]["Authorization"], "Bearer ${PEDIDOS_API_TOKEN}")
                self.assertEqual(request["headers"]["X-Request-ID"], response["headers"]["X-Request-ID"])
                self.assertEqual(request["query"]["sucursal_id"], "3")
                self.assertTrue((CONTRACT_ROOT / response["body"]).is_file())
                self.assertTrue((CONTRACT_ROOT / response["schema"]).is_file())

    def test_paginas_200_validas_coinciden_con_parser_pos(self):
        for name in ("pagina_con_datos", "pagina_vacia"):
            case = self.cases[name]
            raw, body = self.fixture_body(case)
            request_id = case["response"]["headers"]["X-Request-ID"]
            limit = int(case["request"]["query"]["limite"])
            with self.subTest(case=name):
                validate_page(body, request_id=request_id, requested_limit=limit)
                parsed = parsear_pagina_v2(
                    raw,
                    headers={
                        "Content-Type": "application/json",
                        "Content-Length": str(len(raw)),
                        "X-Request-ID": request_id,
                    },
                    sucursales_permitidas=[3],
                    limite_solicitado=limit,
                    max_response_bytes=1048576,
                )
                self.assertEqual(parsed.returned, len(body["data"]))

    def test_401_403_y_410_producen_excepciones_tipadas(self):
        expected = {
            "credencial_invalida": ErrorAutenticacionPedidos,
            "sucursal_fuera_de_scope": ErrorAlcancePedidos,
            "retention_gap_cursor_firmado": ErrorBrechaRetencion,
            "cursor_legado_numerico": ErrorBrechaRetencion,
        }
        for name, error_type in expected.items():
            case = self.cases[name]
            raw, body = self.fixture_body(case)
            request_id = case["response"]["headers"]["X-Request-ID"]
            code = validate_error(body, request_id=request_id)
            if case["response"]["status"] == 410:
                self.assertEqual(code, "retention_gap")
            response = RespuestaHTTP(
                case["response"]["status"],
                {
                    "Content-Type": "application/json",
                    "Content-Length": str(len(raw)),
                    "X-Request-ID": request_id,
                },
                raw,
                URL,
            )
            transport = FixtureTransport(response)
            api = ClientePedidosV2(
                url=URL,
                token=TOKEN_SINTETICO,
                sucursal_ids=[3],
                transporte=transport,
                max_reintentos=0,
            )
            query = case["request"]["query"]
            with self.subTest(case=name), self.assertRaises(error_type) as captured:
                api.listar_pagina(
                    desde=datetime.fromisoformat(query["desde"].replace("Z", "+00:00")),
                    hasta=datetime.fromisoformat(query["hasta"].replace("Z", "+00:00")),
                    limite=int(query["limite"]),
                    cursor=query.get("cursor"),
                    request_id=request_id,
                )
            self.assertEqual(len(transport.requests), 1)
            sent_query = parse_qs(urlsplit(transport.requests[0].full_url).query)
            if "cursor" in query:
                self.assertEqual(sent_query["cursor"], [query["cursor"]])
            if error_type is ErrorBrechaRetencion:
                self.assertEqual(
                    captured.exception.estado,
                    EstadoSincronizacionPedidos.RECONCILIACION_REQUERIDA,
                )

    def test_pagina_invalida_es_rechazada_por_semantica_y_cliente(self):
        case = self.cases["pagina_returned_inconsistente"]
        raw, body = self.fixture_body(case)
        request_id = case["response"]["headers"]["X-Request-ID"]
        limit = int(case["request"]["query"]["limite"])
        with self.assertRaises(ContractError):
            validate_page(body, request_id=request_id, requested_limit=limit)
        with self.assertRaises(ErrorContratoPedidos):
            parsear_pagina_v2(
                raw,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(raw)),
                    "X-Request-ID": request_id,
                },
                sucursales_permitidas=[3],
                limite_solicitado=limit,
                max_response_bytes=1048576,
            )


if __name__ == "__main__":
    unittest.main()
