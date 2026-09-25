"""Valida contratos Edge↔VPS sin Django ni dependencias externas.

El validador implementa únicamente las palabras clave Draft 2020-12 usadas por
los schemas versionados en contracts/edge-central. Las reglas de negocio que
JSON Schema no expresa se comprueban por separado contra fixtures sintéticos.
"""

from __future__ import annotations

import hashlib
import json
import re
import unittest
import uuid
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = REPO_ROOT / "contracts" / "edge-central"
SCHEMA_ROOT = CONTRACT_ROOT / "schemas"
FIXTURE_ROOT = CONTRACT_ROOT / "fixtures"
DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"


class ContractError(ValueError):
    """Un schema, fixture o vínculo entre ambos no cumple el contrato."""


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"clave JSON duplicada: {key}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ContractError(f"constante JSON no finita: {value}")


def load_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError(f"{path}: no es UTF-8") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=_invalid_constant,
        )
    except json.JSONDecodeError as exc:
        raise ContractError(f"{path}: JSON inválido: {exc}") from exc


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    return left == right


def _matches_type(instance: Any, expected: str) -> bool:
    if expected == "null":
        return instance is None
    if expected == "object":
        return isinstance(instance, dict)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    raise ContractError(f"tipo de schema no soportado: {expected}")


def _resolve_internal_ref(root_schema: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise ContractError(f"$ref interno no soportado: {reference}")
    current: Any = root_schema
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            raise ContractError(f"$ref interno inexistente: {reference}")
        current = current[part]
    if not isinstance(current, dict):
        raise ContractError(f"$ref no apunta a un schema: {reference}")
    return current


def _validate_format(instance: str, format_name: str, path: str) -> None:
    try:
        if format_name == "uuid":
            parsed = uuid.UUID(instance)
            if str(parsed) != instance.lower():
                raise ValueError("UUID no canónico")
        elif format_name == "date":
            if date.fromisoformat(instance).isoformat() != instance:
                raise ValueError("fecha no canónica")
        elif format_name == "date-time":
            parsed_dt = datetime.fromisoformat(instance.replace("Z", "+00:00"))
            if parsed_dt.tzinfo is None or parsed_dt.utcoffset() is None:
                raise ValueError("falta zona")
        else:
            raise ContractError(f"{path}: format no soportado: {format_name}")
    except (ValueError, TypeError) as exc:
        raise ContractError(f"{path}: {format_name} inválido") from exc


def validate_schema(
    instance: Any,
    schema: dict[str, Any],
    *,
    root_schema: dict[str, Any] | None = None,
    path: str = "$",
) -> None:
    root_schema = root_schema or schema

    if "$ref" in schema:
        validate_schema(
            instance,
            _resolve_internal_ref(root_schema, schema["$ref"]),
            root_schema=root_schema,
            path=path,
        )

    if "const" in schema and not _json_equal(instance, schema["const"]):
        raise ContractError(f"{path}: no coincide con const")
    if "enum" in schema and not any(_json_equal(instance, item) for item in schema["enum"]):
        raise ContractError(f"{path}: valor fuera de enum")

    expected_types = schema.get("type")
    if expected_types is not None:
        if isinstance(expected_types, str):
            expected_types = [expected_types]
        if not any(_matches_type(instance, item) for item in expected_types):
            raise ContractError(f"{path}: tipo inválido; se esperaba {expected_types}")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in instance]
        if missing:
            raise ContractError(f"{path}: faltan propiedades {missing}")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            raise ContractError(f"{path}: excede maxProperties")
        if "minProperties" in schema and len(instance) < schema["minProperties"]:
            raise ContractError(f"{path}: no alcanza minProperties")

        properties = schema.get("properties", {})
        for key, value in instance.items():
            child_path = f"{path}.{key}"
            if key in properties:
                validate_schema(value, properties[key], root_schema=root_schema, path=child_path)
                continue
            additional = schema.get("additionalProperties", True)
            if additional is False:
                raise ContractError(f"{path}: propiedad no permitida {key}")
            if isinstance(additional, dict):
                validate_schema(value, additional, root_schema=root_schema, path=child_path)

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            raise ContractError(f"{path}: no alcanza minItems")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise ContractError(f"{path}: excede maxItems")
        if schema.get("uniqueItems"):
            encoded = [canonical_json(item) for item in instance]
            if len(encoded) != len(set(encoded)):
                raise ContractError(f"{path}: contiene items duplicados")
        if "items" in schema:
            for index, value in enumerate(instance):
                validate_schema(
                    value,
                    schema["items"],
                    root_schema=root_schema,
                    path=f"{path}[{index}]",
                )

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            raise ContractError(f"{path}: no alcanza minLength")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise ContractError(f"{path}: excede maxLength")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            raise ContractError(f"{path}: no cumple pattern")
        if "format" in schema:
            _validate_format(instance, schema["format"], path)

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise ContractError(f"{path}: menor que minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            raise ContractError(f"{path}: mayor que maximum")


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ContractError("valor no serializable como JSON canónico") from exc


def sha256_canonical(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None, "timestamp sin zona")
    return parsed


def validate_http_metadata(case: dict[str, Any], body: dict[str, Any]) -> None:
    http = case.get("http")
    if not http or http.get("method") == "RESPONSE":
        return
    headers = http.get("headers", {})
    if "Authorization" in headers:
        require(
            re.fullmatch(r"Bearer \$\{[A-Z][A-Z0-9_]*\}", headers["Authorization"]) is not None,
            f"{case['name']}: Authorization debe ser placeholder",
        )
    if http["method"] == "POST":
        require(headers.get("Content-Type") == "application/json", "POST sin Content-Type JSON")
    expected_id = {
        "monthly-request": body.get("idempotencia"),
        "sales-request": body.get("lote_id"),
        "customer-request": body.get("evento_id"),
        "catalog-ack": body.get("ack_id"),
    }.get(case.get("semantic"))
    if expected_id is not None:
        require(headers.get("Idempotency-Key") == expected_id, "Idempotency-Key no coincide")


def semantic_monthly(body: dict[str, Any], _related: Any = None) -> None:
    require(body["periodo"] == body["totales"]["periodo"], "periodos mensuales no coinciden")
    require(body["periodo"].endswith("-01"), "periodo no inicia el mes")


def semantic_monthly_response(body: dict[str, Any], _related: Any = None) -> None:
    require(body["recibido"] is True, "acuse mensual no recibido")


def semantic_sales(body: dict[str, Any], _related: Any = None) -> None:
    events = body["eventos"]
    require(body["conteo"] == len(events), "conteo de eventos no coincide")
    require(body["contenido_sha256"] == sha256_canonical(events), "checksum de eventos no coincide")
    event_ids = [event["evento_id"] for event in events]
    require(len(event_ids) == len(set(event_ids)), "evento_id duplicado")

    for event in events:
        sale = event["venta"]
        require(parse_datetime(sale["cerrado_en"]) >= parse_datetime(sale["creado_en"]), "cierre anterior a creación")
        if event["tipo"] == "venta.cerrada":
            require(sale["estado"] == "pagado", "venta.cerrada debe estar pagada")
        if event["tipo"] == "venta.cancelada":
            require(sale["estado"] == "cancelado", "venta.cancelada debe estar cancelada")
        line_ids = [line["id"] for line in sale["partidas"]]
        require(len(line_ids) == len(set(line_ids)), "partida duplicada")
        subtotal = sum((Decimal(line["importe"]) for line in sale["partidas"]), Decimal("0.00"))
        require(subtotal == Decimal(sale["subtotal"]), "subtotal no coincide con partidas")
        discount = Decimal(sale["descuento_porcentaje"])
        expected_total = (subtotal * (Decimal("100.00") - discount) / Decimal("100.00")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        require(expected_total == Decimal(sale["total"]), "total no coincide con descuento")
        for line in sale["partidas"]:
            if line["origen"] == "personalizado":
                require(line["producto_id"] is None, "personalizado no debe referir producto")
            else:
                require(line["producto_id"] is not None, "partida de catálogo sin producto_id")
            calculated = (
                Decimal(line["cantidad"])
                / Decimal(line["cantidad_por_precio"])
                * Decimal(line["precio_unitario"])
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            require(calculated == Decimal(line["importe"]), "importe de partida no coincide")


def semantic_sales_response(body: dict[str, Any], related: dict[str, Any]) -> None:
    require(body["lote_id"] == related["lote_id"], "acuse de lote ajeno")
    require(body["contenido_sha256"] == related["contenido_sha256"], "acuse con checksum distinto")
    require(body["conteo_aceptado"] == related["conteo"], "acuse con conteo distinto")


def semantic_customer(body: dict[str, Any], _related: Any = None) -> None:
    customer = body["cliente"]
    require(body["contenido_sha256"] == sha256_canonical(customer), "checksum de cliente no coincide")
    require(
        parse_datetime(customer["actualizado_en"]) >= parse_datetime(customer["creado_en"]),
        "cliente actualizado antes de crearse",
    )
    require(parse_datetime(body["ocurrido_en"]) >= parse_datetime(customer["actualizado_en"]), "evento anterior al cliente")
    for key in ("telefonos", "domicilios"):
        identifiers = [item["id"] for item in customer[key]]
        require(len(identifiers) == len(set(identifiers)), f"IDs duplicados en {key}")
        active_primary = [item for item in customer[key] if item["activo"] and item["principal"]]
        require(len(active_primary) <= 1, f"más de un principal activo en {key}")


def semantic_customer_response(body: dict[str, Any], related: dict[str, Any]) -> None:
    require(body["evento_id"] == related["evento_id"], "acuse de evento de cliente ajeno")
    require(body["cliente_id"] == related["cliente_id"], "acuse de cliente ajeno")
    require(body["version_aplicada"] == related["version_origen"], "versión aplicada inesperada")


def semantic_catalog(body: dict[str, Any], related: dict[str, Any] | None = None) -> None:
    content = body["contenido"]
    require(body["contenido_sha256"] == sha256_canonical(content), "checksum de catálogo no coincide")
    require(body["conteos"]["categorias"] == len(content["categorias"]), "conteo de categorías no coincide")
    require(body["conteos"]["productos"] == len(content["productos"]), "conteo de productos no coincide")
    category_ids = [item["categoria_central_id"] for item in content["categorias"]]
    product_ids = [item["producto_central_id"] for item in content["productos"]]
    require(len(category_ids) == len(set(category_ids)), "categoría duplicada")
    require(len(product_ids) == len(set(product_ids)), "producto duplicado")
    require(len({item["codigo"] for item in content["categorias"]}) == len(category_ids), "código de categoría duplicado")
    require(len({item["codigo"] for item in content["productos"]}) == len(product_ids), "código de producto duplicado")
    category_set = set(category_ids)
    apply_date = date.fromisoformat(body["aplicar_desde"])
    for product in content["productos"]:
        require(product["categoria_central_id"] in category_set, "producto refiere categoría inexistente")
        price = product["precio"]
        start = date.fromisoformat(price["vigente_desde"])
        end = date.fromisoformat(price["vigente_hasta"]) if price["vigente_hasta"] else None
        require(start <= apply_date, "precio todavía no vigente al aplicar")
        require(end is None or end >= apply_date, "precio vencido al aplicar")
    if body["version_sucursal"] == 1:
        require(body["publicacion_anterior_id"] is None, "primera publicación tiene predecesora")
    else:
        require(body["publicacion_anterior_id"] is not None, "publicación posterior sin predecesora")
    if related is not None:
        require(body["sucursal"] == related["sucursal"], "cadena de catálogo cambia de sucursal")
        require(
            body["version_sucursal"] == related["version_sucursal"] + 1,
            "la publicación no es la versión inmediata siguiente",
        )
        require(
            body["publicacion_anterior_id"] == related["publicacion_id"],
            "publicacion_anterior_id no coincide con la publicación activa",
        )


def semantic_catalog_ack(body: dict[str, Any], related: dict[str, Any] | None = None) -> None:
    code = body["resultado"]["codigo"]
    if body["estado"] in {"recibido", "aplicado"}:
        require(code == "ok", "ACK exitoso con código de error")
    else:
        require(code != "ok", "ACK rechazado con código ok")
    central_category_ids = [item["categoria_central_id"] for item in body["mapeos_categoria"]]
    local_category_ids = [item["categoria_local_id"] for item in body["mapeos_categoria"]]
    central_ids = [item["producto_central_id"] for item in body["mapeos_producto"]]
    local_ids = [item["producto_local_id"] for item in body["mapeos_producto"]]
    require(len(central_category_ids) == len(set(central_category_ids)), "mapeo de categoría central duplicado")
    require(len(local_category_ids) == len(set(local_category_ids)), "mapeo de categoría local duplicado")
    require(len(central_ids) == len(set(central_ids)), "mapeo central duplicado")
    require(len(local_ids) == len(set(local_ids)), "mapeo local duplicado")
    if related is not None and body["estado"] == "aplicado":
        for key in ("release_id", "publicacion_id", "version_sucursal", "contenido_sha256"):
            require(body[key] == related[key], f"ACK no coincide en {key}")
        require(body["sucursal"] == related["sucursal"], "ACK de sucursal distinta")
        expected_categories = {item["categoria_central_id"] for item in related["contenido"]["categorias"]}
        expected_products = {item["producto_central_id"] for item in related["contenido"]["productos"]}
        require(set(central_category_ids) == expected_categories, "ACK aplicado no mapea todas las categorías")
        require(set(central_ids) == expected_products, "ACK aplicado no mapea todos los productos")


def semantic_catalog_ack_response(body: dict[str, Any], related: dict[str, Any]) -> None:
    require(body["ack_id"] == related["ack_id"], "respuesta de otro ACK")
    require(body["estado_registrado"] == related["estado"], "estado central distinto al ACK")


def semantic_identity_map(body: dict[str, Any], _related: Any = None) -> None:
    pos_ids: set[str] = set()
    pedidos_ids: set[int] = set()
    for mapping in body["mapeos"]:
        pos = mapping["pos"]
        pedidos = mapping["pedidos"]
        central = mapping["central"]
        require(pos["sucursal_id"] == central["branch_source_id"], "UUID POS/Central distinto")
        require(pos["sucursal_clave"] == central["branch_code"], "código POS/Central distinto")
        require(pos["sucursal_id"] not in pos_ids, "sucursal POS duplicada")
        require(pedidos["sucursal_cliente_id"] not in pedidos_ids, "SucursalCliente duplicada")
        pos_ids.add(pos["sucursal_id"])
        pedidos_ids.add(pedidos["sucursal_cliente_id"])


SEMANTIC_VALIDATORS = {
    "monthly-request": semantic_monthly,
    "monthly-response": semantic_monthly_response,
    "sales-request": semantic_sales,
    "sales-response": semantic_sales_response,
    "customer-request": semantic_customer,
    "customer-response": semantic_customer_response,
    "catalog-publication": semantic_catalog,
    "catalog-ack": semantic_catalog_ack,
    "catalog-ack-response": semantic_catalog_ack_response,
    "identity-map": semantic_identity_map,
}


class EdgeCentralContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_json(FIXTURE_ROOT / "index.json")

    def _validate_case(self, case: dict[str, Any]) -> None:
        schema_path = CONTRACT_ROOT / case["schema"]
        fixture_path = CONTRACT_ROOT / case["fixture"]
        schema = load_json(schema_path)
        body = load_json(fixture_path)
        validate_schema(body, schema)
        related = None
        if case.get("relates_to"):
            related = load_json(CONTRACT_ROOT / case["relates_to"])
        SEMANTIC_VALIDATORS[case["semantic"]](body, related)
        validate_http_metadata(case, body)

    def test_all_json_is_utf8_unique_key_json(self) -> None:
        paths = sorted(CONTRACT_ROOT.rglob("*.json"))
        self.assertGreater(len(paths), 20)
        for path in paths:
            with self.subTest(path=path.relative_to(CONTRACT_ROOT)):
                load_json(path)

    def test_schema_catalog_is_draft_2020_12(self) -> None:
        identifiers: set[str] = set()
        for path in sorted(SCHEMA_ROOT.glob("*.schema.json")):
            with self.subTest(schema=path.name):
                schema = load_json(path)
                self.assertEqual(schema.get("$schema"), DRAFT_2020_12)
                self.assertEqual(schema.get("type"), "object")
                self.assertTrue(schema.get("$id", "").startswith("https://contracts.lostocayos.invalid/"))
                self.assertNotIn(schema["$id"], identifiers)
                identifiers.add(schema["$id"])

    def test_manifest_cases_and_semantics(self) -> None:
        names: set[str] = set()
        for case in self.manifest["cases"]:
            with self.subTest(case=case["name"]):
                self.assertNotIn(case["name"], names)
                names.add(case["name"])
                if case["expected_valid"]:
                    self._validate_case(case)
                else:
                    with self.assertRaises(ContractError):
                        self._validate_case(case)

    def test_openapi_routes_statuses_scopes_and_references(self) -> None:
        api = load_json(CONTRACT_ROOT / "openapi.json")
        self.assertEqual(api["openapi"], "3.1.0")
        expected = {
            ("post", "/api/v1/edge/consolidaciones-mensuales/"): ("implementado-candidato-privado", "sales:v1:write"),
            ("post", "/api/v2/edge/ventas/lotes/"): ("propuesto-desactivado", "sales:v2:write"),
            ("post", "/api/v2/edge/clientes/eventos/"): ("propuesto-desactivado", "customers:v2:write"),
            ("get", "/api/v2/edge/catalogo/publicaciones/actual/"): ("propuesto-desactivado", "catalog:v2:read"),
            ("get", "/api/v2/edge/catalogo/publicaciones/{publicacion_id}/"): ("propuesto-desactivado", "catalog:v2:read"),
            ("post", "/api/v2/edge/catalogo/publicaciones/{publicacion_id}/acuse/"): ("propuesto-desactivado", "catalog:v2:ack"),
            ("get", "/api/v3/edge/catalogo/publicaciones/actual/"): ("propuesto-desactivado", "catalog:v3:read"),
            ("get", "/api/v3/edge/catalogo/publicaciones/{uuid}/"): ("propuesto-desactivado", "catalog:v3:read"),
            ("post", "/api/v3/edge/catalogo/publicaciones/{uuid}/acuse/"): ("propuesto-desactivado", "catalog:v3:ack"),
        }
        actual: dict[tuple[str, str], tuple[str, str]] = {}
        for path, path_item in api["paths"].items():
            for method, operation in path_item.items():
                if method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                actual[(method, path)] = (
                    operation.get("x-contract-status"),
                    operation.get("x-required-scope"),
                )
        self.assertEqual(actual, expected)
        self.assertNotIn("/api/v1/edge/clientes/", api["paths"])
        monthly = api["paths"]["/api/v1/edge/consolidaciones-mensuales/"]["post"]
        self.assertEqual(
            monthly["requestBody"]["content"]["application/json"]["schema"]["$ref"],
            "./schemas/consolidacion-mensual-v1-receptor-actual.schema.json",
        )
        self.assertEqual(
            monthly["x-pos-emitter-profile"]["$ref"],
            "./schemas/consolidacion-mensual-v1-request.schema.json",
        )

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                if "$ref" in value:
                    reference = value["$ref"]
                    if reference.startswith("#/"):
                        _resolve_internal_ref(api, reference)
                    else:
                        relative = reference.split("#", 1)[0]
                        target = (CONTRACT_ROOT / relative).resolve()
                        self.assertTrue(target.is_relative_to(CONTRACT_ROOT.resolve()))
                        self.assertTrue(target.is_file(), reference)
                        load_json(target)
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(api)

    def test_manifest_http_routes_exist_and_statuses_are_declared(self) -> None:
        api = load_json(CONTRACT_ROOT / "openapi.json")
        for case in self.manifest["cases"]:
            http = case.get("http")
            if not http or http["method"] == "RESPONSE":
                continue
            method = http["method"].lower()
            path = http["path"]
            normalized = re.sub(
                r"/api/v2/edge/catalogo/publicaciones/[0-9a-f-]+/acuse/",
                "/api/v2/edge/catalogo/publicaciones/{publicacion_id}/acuse/",
                path,
            )
            with self.subTest(case=case["name"]):
                operation = api["paths"][normalized][method]
                self.assertIn(str(http["expected_status"]), operation["responses"])
                if http.get("scope"):
                    self.assertEqual(http["scope"], operation["x-required-scope"])

    def test_catalog_ack_v3_fixture_is_exhaustive_and_versioned(self) -> None:
        snapshot = load_json(FIXTURE_ROOT / "catalogo-publicacion-v3-lab01-promocion.json")
        request_schema = load_json(SCHEMA_ROOT / "catalogo-ack-v3-request.schema.json")
        response_schema = load_json(SCHEMA_ROOT / "catalogo-ack-v3-response.schema.json")
        aplicado = load_json(FIXTURE_ROOT / "catalogo-ack-v3-request-aplicado.json")
        rechazado = load_json(FIXTURE_ROOT / "catalogo-ack-v3-request-rechazado.json")
        respuesta = load_json(FIXTURE_ROOT / "catalogo-ack-v3-response-aplicado.json")
        for ack in (aplicado, rechazado):
            validate_schema(ack, request_schema)
            self.assertEqual(ack["version_contrato"], 3)
            self.assertEqual(ack["publicacion_id"], snapshot["publicacion_id"])
            self.assertEqual(ack["release_id"], snapshot["release_id"])
        validate_schema(respuesta, response_schema)
        self.assertEqual(respuesta["ack_id"], aplicado["ack_id"])
        self.assertEqual(aplicado["contenido_sha256"], snapshot["contenido_sha256"])
        self.assertEqual(
            {item["categoria_central_id"] for item in aplicado["mapeos_categoria"]},
            {item["categoria_central_id"] for item in snapshot["contenido"]["categorias"]},
        )
        self.assertEqual(
            {item["producto_central_id"] for item in aplicado["mapeos_producto"]},
            {item["producto_central_id"] for item in snapshot["contenido"]["productos"]},
        )
        self.assertEqual(rechazado["mapeos_producto"], [])

    def test_ack_identity_is_stable_before_and_after_purge(self) -> None:
        monthly_received = load_json(FIXTURE_ROOT / "consolidacion-mensual-v1-response-recibido.json")
        monthly_purged = load_json(FIXTURE_ROOT / "consolidacion-mensual-v1-response-purgado.json")
        self.assertEqual(monthly_received["acuse"], monthly_purged["acuse"])
        sales_received = load_json(FIXTURE_ROOT / "ventas-lote-v2-response-recibido.json")
        sales_purged = load_json(FIXTURE_ROOT / "ventas-lote-v2-response-purgado.json")
        for key in ("acuse", "lote_id", "contenido_sha256", "conteo_aceptado", "primera_recepcion_en"):
            self.assertEqual(sales_received[key], sales_purged[key])

    def test_fixtures_respect_documented_size_limits(self) -> None:
        limits = {
            "consolidacion-mensual-v1-request.json": 256 * 1024,
            "ventas-lote-v2-request.json": 256 * 1024,
            "cliente-evento-v2-request.json": 64 * 1024,
            "catalogo-ack-v2-request-aplicado.json": 16 * 1024,
            "catalogo-ack-v2-request-rechazado.json": 16 * 1024,
            "catalogo-ack-v3-request-aplicado.json": 1024 * 1024,
            "catalogo-ack-v3-request-rechazado.json": 1024 * 1024,
            "catalogo-publicacion-v2-lab01-global.json": 1024 * 1024,
            "catalogo-publicacion-v2-lab02-excepcion.json": 1024 * 1024,
        }
        for filename, limit in limits.items():
            with self.subTest(fixture=filename):
                self.assertLessEqual((FIXTURE_ROOT / filename).stat().st_size, limit)

    def test_documented_security_boundaries_are_present(self) -> None:
        credentials = (CONTRACT_ROOT / "CREDENCIALES_Y_VARIABLES.md").read_text(encoding="utf-8")
        actions = (CONTRACT_ROOT / "MATRIZ_HTTP_ACCIONES.md").read_text(encoding="utf-8")
        handoff = (CONTRACT_ROOT / "HANDOFF_AGENTE1_CENTRAL.md").read_text(encoding="utf-8")
        for text in (credentials, handoff):
            self.assertIn("PEDIDOS_API_TOKEN", text)
            self.assertIn("CENTRAL_INGEST_TOKEN", text)
            self.assertIn("CENTRAL_CATALOG_TOKEN", text)
            self.assertIn("verify=False", text)
        for scope in ("orders:v2:read", "sales:v2:write", "customers:v2:write", "catalog:v2:read", "catalog:v2:ack", "catalog:v3:read", "catalog:v3:ack"):
            self.assertIn(scope, credentials)
        for command in ("issue_scoped_edge_token", "rotate_edge_token", "revoke_edge_token"):
            self.assertIn(command, credentials)
        self.assertIn("410 `retention_gap`", actions)
        self.assertIn("REQUIERE_CONCILIACION", actions)
        self.assertIn("conservar último cursor", actions)
        self.assertIn("propuesto-desactivado", handoff)
        self.assertIn("No desplegar", handoff)


if __name__ == "__main__":
    unittest.main(verbosity=2)
