"""Cliente estricto y aislado para la API POS v2 de tcysPedidosSucursales.

Este modulo no lee settings ni persiste modelos. La integracion decide de donde
obtiene la URL, el token y el mapa de sucursales, y debe aplicar cada pagina y
su checkpoint dentro de una unica transaccion SQLite.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import socket
import ssl
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


API_VERSION = "v2"
ESTADOS_TRANSITORIOS = frozenset({429, 500, 502, 503, 504})
PATRON_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
PATRON_DECIMAL = re.compile(r"^-?[0-9]+(?:\.[0-9]+)?$")
PATRON_FECHA_UTC = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$"
)
PATRON_CODIGO_ERROR = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class EstadoSincronizacionPedidos(StrEnum):
    ACTIVA = "activa"
    RECONCILIACION_REQUERIDA = "reconciliacion_requerida"


class ErrorPedidosApi(Exception):
    """Base sin payloads, URLs ni credenciales en su representacion."""

    def __init__(
        self,
        mensaje: str,
        *,
        status: int | None = None,
        code: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(mensaje)
        self.status = status
        self.code = code
        self.request_id = request_id


class ErrorConfiguracionPedidos(ErrorPedidosApi):
    pass


class ErrorContratoPedidos(ErrorPedidosApi):
    pass


class ErrorTamanoRespuestaPedidos(ErrorContratoPedidos):
    pass


class ErrorAutenticacionPedidos(ErrorPedidosApi):
    pass


class ErrorAlcancePedidos(ErrorPedidosApi):
    pass


class ErrorEndpointPedidos(ErrorPedidosApi):
    pass


class ErrorBrechaRetencion(ErrorPedidosApi):
    estado = EstadoSincronizacionPedidos.RECONCILIACION_REQUERIDA


class ErrorLimitePedidos(ErrorPedidosApi):
    def __init__(self, *args: Any, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.retry_after = retry_after


class ErrorTemporalPedidos(ErrorPedidosApi):
    pass


class ErrorTLSPedidos(ErrorPedidosApi):
    pass


class ErrorTransportePedidos(ErrorPedidosApi):
    pass


@dataclass(frozen=True, slots=True)
class ProductoPedidoRemoto:
    id: int
    nombre: str
    nombre_ticket: str
    unidad_medida: str
    unidad_abreviatura: str
    cantidad_por_precio: Decimal


@dataclass(frozen=True, slots=True)
class ItemPedidoRemoto:
    id: int
    pedido_id: int
    producto: ProductoPedidoRemoto
    cantidad: Decimal
    precio_unitario: Decimal
    subtotal: Decimal


@dataclass(frozen=True, slots=True)
class SucursalPedidoRemota:
    id: int
    nombre: str
    tipo: str


@dataclass(frozen=True, slots=True)
class PedidoRemoto:
    id: int
    codigo_publico: uuid.UUID
    fecha_confirmacion: datetime
    total: Decimal
    sucursal: SucursalPedidoRemota
    items: tuple[ItemPedidoRemoto, ...]
    order_canonical_json: str
    order_sha256: str

    @property
    def identidad_remota(self) -> tuple[int, uuid.UUID]:
        """Identidad doble para conciliacion; codigo_publico es la clave durable."""

        return self.id, self.codigo_publico


@dataclass(frozen=True, slots=True)
class PaginaPedidosV2:
    request_id: str
    pedidos: tuple[PedidoRemoto, ...]
    has_more: bool
    limit: int
    next_cursor: str | None
    returned: int


@dataclass(frozen=True, slots=True)
class CheckpointPaginaPedidos:
    cursor_entrada: str | None
    cursor_siguiente: str | None
    ventana_completa: bool
    numero_pagina: int


@dataclass(frozen=True, slots=True)
class ResultadoSincronizacionPedidos:
    paginas: int
    pedidos: int
    ventana_completa: bool


@dataclass(frozen=True, slots=True)
class RespuestaHTTP:
    status: int
    headers: Mapping[str, str]
    body: bytes
    url_final: str


class TransportePedidos(Protocol):
    def __call__(
        self,
        solicitud: Request,
        *,
        timeout: float,
        max_response_bytes: int,
    ) -> RespuestaHTTP: ...


class _BloquearRedirecciones(HTTPRedirectHandler):
    """Impide que urllib reenvie Authorization a cualquier redireccion."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _normalizar_headers(headers: Any) -> dict[str, str]:
    resultado: dict[str, str] = {}
    for nombre, valor in headers.items():
        resultado[str(nombre).lower()] = str(valor).strip()
    return resultado


def _validar_content_length(headers: Mapping[str, str], maximo: int) -> None:
    valor = headers.get("content-length")
    if valor is None:
        return
    if not valor.isascii() or not valor.isdigit():
        raise ErrorContratoPedidos("Content-Length invalido en la respuesta de Pedidos.")
    if int(valor) > maximo:
        raise ErrorTamanoRespuestaPedidos("La respuesta de Pedidos excede el limite permitido.")


def _leer_cuerpo_limitado(respuesta: Any, maximo: int) -> bytes:
    headers = _normalizar_headers(respuesta.headers)
    _validar_content_length(headers, maximo)
    contenido = respuesta.read(maximo + 1)
    if len(contenido) > maximo:
        raise ErrorTamanoRespuestaPedidos("La respuesta de Pedidos excede el limite permitido.")
    return contenido


class _TransporteUrllib:
    def __init__(self, contexto_ssl: ssl.SSLContext) -> None:
        self._opener = build_opener(
            HTTPSHandler(context=contexto_ssl),
            _BloquearRedirecciones(),
        )

    def __call__(
        self,
        solicitud: Request,
        *,
        timeout: float,
        max_response_bytes: int,
    ) -> RespuestaHTTP:
        respuesta: Any
        try:
            respuesta = self._opener.open(solicitud, timeout=timeout)
        except HTTPError as exc:
            respuesta = exc
        try:
            headers = _normalizar_headers(respuesta.headers)
            contenido = _leer_cuerpo_limitado(respuesta, max_response_bytes)
            return RespuestaHTTP(
                status=int(respuesta.getcode()),
                headers=headers,
                body=contenido,
                url_final=str(respuesta.geturl()),
            )
        finally:
            respuesta.close()


def _origen(url: str) -> tuple[str, str, int]:
    partes = urlsplit(url)
    try:
        puerto = partes.port
    except ValueError as exc:
        raise ErrorConfiguracionPedidos("La URL de Pedidos contiene un puerto invalido.") from exc
    if (
        partes.scheme.lower() != "https"
        or not partes.hostname
        or partes.username is not None
        or partes.password is not None
    ):
        raise ErrorConfiguracionPedidos(
            "La URL de Pedidos debe ser HTTPS absoluta y no incluir credenciales."
        )
    return "https", partes.hostname.lower(), puerto or 443


def _validar_url(url: str) -> str:
    if not isinstance(url, str) or not url or any(caracter.isspace() for caracter in url):
        raise ErrorConfiguracionPedidos("La URL de Pedidos no es valida.")
    _origen(url)
    partes = urlsplit(url)
    if partes.query or partes.fragment:
        raise ErrorConfiguracionPedidos(
            "La URL de Pedidos no debe incluir query string ni fragmento."
        )
    if not partes.path.startswith("/"):
        raise ErrorConfiguracionPedidos("La URL de Pedidos debe incluir una ruta absoluta.")
    return url


def _crear_contexto_ssl(ca_bundle: str | Path | None) -> ssl.SSLContext:
    try:
        contexto = ssl.create_default_context(
            cafile=str(Path(ca_bundle)) if ca_bundle is not None else None
        )
    except (OSError, ssl.SSLError) as exc:
        raise ErrorConfiguracionPedidos("No fue posible cargar el CA bundle de Pedidos.") from exc
    if contexto.verify_mode != ssl.CERT_REQUIRED or not contexto.check_hostname:
        raise ErrorConfiguracionPedidos("La verificacion TLS de Pedidos debe permanecer activa.")
    return contexto


def _fecha_consulta(valor: datetime, nombre: str) -> str:
    if not isinstance(valor, datetime) or valor.tzinfo is None or valor.utcoffset() is None:
        raise ErrorConfiguracionPedidos(f"{nombre} debe incluir zona horaria.")
    return valor.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _entero_positivo(valor: Any, ruta: str) -> int:
    if type(valor) is not int or valor <= 0:
        raise ErrorContratoPedidos(f"Campo entero positivo invalido en {ruta}.")
    return valor


def _texto(
    valor: Any,
    ruta: str,
    *,
    maximo: int,
    permitir_vacio: bool = False,
) -> str:
    if type(valor) is not str or len(valor) > maximo or (not permitir_vacio and not valor):
        raise ErrorContratoPedidos(f"Campo de texto invalido en {ruta}.")
    if any(ord(caracter) < 32 and caracter not in "\t" for caracter in valor):
        raise ErrorContratoPedidos(f"Campo de texto invalido en {ruta}.")
    return valor


def _decimal(
    valor: Any,
    ruta: str,
    *,
    max_enteros: int,
    max_decimales: int,
    positivo: bool = False,
) -> Decimal:
    if type(valor) is not str or len(valor) > 64 or not PATRON_DECIMAL.fullmatch(valor):
        raise ErrorContratoPedidos(f"Decimal invalido en {ruta}.")
    texto_magnitud = valor.removeprefix("-")
    enteros, separador, decimales = texto_magnitud.partition(".")
    enteros_significativos = enteros.lstrip("0") or "0"
    if (
        max_enteros <= 0
        or max_decimales < 0
        or len(enteros_significativos) > max_enteros
        or (separador and len(decimales) > max_decimales)
    ):
        raise ErrorContratoPedidos(f"Decimal fuera de rango en {ruta}.")
    try:
        numero = Decimal(valor)
    except InvalidOperation as exc:
        raise ErrorContratoPedidos(f"Decimal invalido en {ruta}.") from exc
    if not numero.is_finite() or (positivo and numero <= 0) or (not positivo and numero < 0):
        raise ErrorContratoPedidos(f"Decimal fuera de rango en {ruta}.")
    return numero

def _fecha_respuesta(valor: Any, ruta: str) -> datetime:
    if type(valor) is not str or not PATRON_FECHA_UTC.fullmatch(valor):
        raise ErrorContratoPedidos(f"Timestamp UTC invalido en {ruta}.")
    try:
        fecha = datetime.fromisoformat(valor[:-1] + "+00:00")
    except ValueError as exc:
        raise ErrorContratoPedidos(f"Timestamp UTC invalido en {ruta}.") from exc
    return fecha


def _uuid_canonico(valor: Any, ruta: str) -> uuid.UUID:
    if type(valor) is not str or len(valor) != 36:
        raise ErrorContratoPedidos(f"UUID invalido en {ruta}.")
    try:
        resultado = uuid.UUID(valor)
    except (ValueError, AttributeError) as exc:
        raise ErrorContratoPedidos(f"UUID invalido en {ruta}.") from exc
    if valor != str(resultado):
        raise ErrorContratoPedidos(f"UUID no canonico en {ruta}.")
    return resultado


def _objeto_exacto(valor: Any, campos: set[str], ruta: str) -> dict[str, Any]:
    if type(valor) is not dict or set(valor) != campos:
        raise ErrorContratoPedidos(f"Esquema invalido en {ruta}.")
    return valor


def _lista(valor: Any, ruta: str) -> list[Any]:
    if type(valor) is not list:
        raise ErrorContratoPedidos(f"Lista invalida en {ruta}.")
    return valor


def _objeto_sin_duplicados(pares: list[tuple[str, Any]]) -> dict[str, Any]:
    resultado: dict[str, Any] = {}
    for clave, valor in pares:
        if clave in resultado:
            raise ValueError("clave JSON duplicada")
        resultado[clave] = valor
    return resultado


def _json_estricto(contenido: bytes) -> Any:
    try:
        return json.loads(
            contenido,
            object_pairs_hook=_objeto_sin_duplicados,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constante invalida")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ErrorContratoPedidos("La respuesta de Pedidos no contiene JSON valido.") from exc


def _producto(valor: Any, ruta: str) -> ProductoPedidoRemoto:
    datos = _objeto_exacto(
        valor,
        {
            "id",
            "nombre",
            "nombre_ticket",
            "unidad_medida",
            "unidad_abreviatura",
            "cantidad_por_precio",
        },
        ruta,
    )
    return ProductoPedidoRemoto(
        id=_entero_positivo(datos["id"], f"{ruta}.id"),
        nombre=_texto(datos["nombre"], f"{ruta}.nombre", maximo=80),
        nombre_ticket=_texto(
            datos["nombre_ticket"], f"{ruta}.nombre_ticket", maximo=24, permitir_vacio=True
        ),
        unidad_medida=_texto(datos["unidad_medida"], f"{ruta}.unidad_medida", maximo=40),
        unidad_abreviatura=_texto(
            datos["unidad_abreviatura"], f"{ruta}.unidad_abreviatura", maximo=8
        ),
        cantidad_por_precio=_decimal(
            datos["cantidad_por_precio"],
            f"{ruta}.cantidad_por_precio",
            max_enteros=3,
            max_decimales=3,
            positivo=True
        ),
    )


def _item(valor: Any, ruta: str, pedido_id: int) -> ItemPedidoRemoto:
    datos = _objeto_exacto(
        valor,
        {"id", "pedido_id", "producto", "cantidad", "precio_unitario", "subtotal"},
        ruta,
    )
    item_pedido_id = _entero_positivo(datos["pedido_id"], f"{ruta}.pedido_id")
    if item_pedido_id != pedido_id:
        raise ErrorContratoPedidos(f"El item no pertenece a su pedido en {ruta}.")
    return ItemPedidoRemoto(
        id=_entero_positivo(datos["id"], f"{ruta}.id"),
        pedido_id=item_pedido_id,
        producto=_producto(datos["producto"], f"{ruta}.producto"),
        cantidad=_decimal(
            datos["cantidad"],
            f"{ruta}.cantidad",
            max_enteros=3,
            max_decimales=3,
            positivo=True,
        ),
        precio_unitario=_decimal(
            datos["precio_unitario"],
            f"{ruta}.precio_unitario",
            max_enteros=8,
            max_decimales=2,
        ),
        subtotal=_decimal(
            datos["subtotal"],
            f"{ruta}.subtotal",
            max_enteros=18,
            max_decimales=2,
        ),
    )


def _pedido(valor: Any, ruta: str, sucursales_permitidas: frozenset[int]) -> PedidoRemoto:
    datos = _objeto_exacto(
        valor,
        {"id", "codigo_publico", "fecha_confirmacion", "total", "sucursal", "items"},
        ruta,
    )
    pedido_id = _entero_positivo(datos["id"], f"{ruta}.id")
    sucursal_datos = _objeto_exacto(
        datos["sucursal"], {"id", "nombre", "tipo"}, f"{ruta}.sucursal"
    )
    sucursal_id = _entero_positivo(sucursal_datos["id"], f"{ruta}.sucursal.id")
    if sucursal_id not in sucursales_permitidas:
        raise ErrorContratoPedidos("La respuesta contiene una sucursal fuera del alcance configurado.")
    tipo = _texto(sucursal_datos["tipo"], f"{ruta}.sucursal.tipo", maximo=24)
    if tipo != "sucursal":
        raise ErrorContratoPedidos("La respuesta contiene una entidad que no es sucursal.")
    items = tuple(
        _item(item, f"{ruta}.items[{indice}]", pedido_id)
        for indice, item in enumerate(_lista(datos["items"], f"{ruta}.items"))
    )
    ids_items = [item.id for item in items]
    if len(ids_items) != len(set(ids_items)):
        raise ErrorContratoPedidos(f"Hay IDs de item duplicados en {ruta}.")
    order_canonical_json = json.dumps(
        valor, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return PedidoRemoto(
        id=pedido_id,
        codigo_publico=_uuid_canonico(datos["codigo_publico"], f"{ruta}.codigo_publico"),
        fecha_confirmacion=_fecha_respuesta(
            datos["fecha_confirmacion"], f"{ruta}.fecha_confirmacion"
        ),
        total=_decimal(
            datos["total"],
            f"{ruta}.total",
            max_enteros=18,
            max_decimales=2,
        ),
        sucursal=SucursalPedidoRemota(
            id=sucursal_id,
            nombre=_texto(sucursal_datos["nombre"], f"{ruta}.sucursal.nombre", maximo=120),
            tipo=tipo,
        ),
        items=items,
        order_canonical_json=order_canonical_json,
        order_sha256=hashlib.sha256(order_canonical_json.encode("utf-8")).hexdigest(),
    )


def parsear_pagina_v2(
    contenido: bytes,
    *,
    headers: Mapping[str, str],
    sucursales_permitidas: Sequence[int],
    limite_solicitado: int,
    max_response_bytes: int,
) -> PaginaPedidosV2:
    """Valida el contrato 8fad568 y convierte una pagina sin efectos laterales."""

    if len(contenido) > max_response_bytes:
        raise ErrorTamanoRespuestaPedidos("La respuesta de Pedidos excede el limite permitido.")
    headers_normalizados = {str(k).lower(): str(v).strip() for k, v in headers.items()}
    _validar_content_length(headers_normalizados, max_response_bytes)
    content_type = headers_normalizados.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise ErrorContratoPedidos("La API de Pedidos no respondio application/json.")
    request_id_header = headers_normalizados.get("x-request-id", "")
    if not PATRON_REQUEST_ID.fullmatch(request_id_header):
        raise ErrorContratoPedidos("X-Request-ID ausente o invalido.")

    raiz = _objeto_exacto(
        _json_estricto(contenido), {"version", "request_id", "data", "page"}, "respuesta"
    )
    if raiz["version"] != API_VERSION:
        raise ErrorContratoPedidos("Version desconocida de la API de Pedidos.")
    request_id = _texto(raiz["request_id"], "respuesta.request_id", maximo=64)
    if not PATRON_REQUEST_ID.fullmatch(request_id) or request_id != request_id_header:
        raise ErrorContratoPedidos("request_id inconsistente en la respuesta de Pedidos.")

    permitidas = frozenset(sucursales_permitidas)
    if not permitidas:
        raise ErrorConfiguracionPedidos("Se requiere un alcance explicito de sucursales.")
    pedidos = tuple(
        _pedido(pedido, f"data[{indice}]", permitidas)
        for indice, pedido in enumerate(_lista(raiz["data"], "data"))
    )
    ids = [pedido.id for pedido in pedidos]
    parejas = [(pedido.sucursal.id, pedido.codigo_publico) for pedido in pedidos]
    if len(ids) != len(set(ids)) or len(parejas) != len(set(parejas)):
        raise ErrorContratoPedidos("La pagina contiene identidades de pedido duplicadas.")
    claves_orden = [(pedido.fecha_confirmacion, pedido.id) for pedido in pedidos]
    if claves_orden != sorted(claves_orden) or len(claves_orden) != len(set(claves_orden)):
        raise ErrorContratoPedidos("Los pedidos no respetan el orden del contrato.")

    page = _objeto_exacto(
        raiz["page"], {"has_more", "limit", "next_cursor", "returned"}, "page"
    )
    if type(page["has_more"]) is not bool:
        raise ErrorContratoPedidos("page.has_more debe ser booleano.")
    if type(page["limit"]) is not int or page["limit"] != limite_solicitado:
        raise ErrorContratoPedidos("page.limit no coincide con la solicitud.")
    if type(page["returned"]) is not int or page["returned"] != len(pedidos):
        raise ErrorContratoPedidos("page.returned no coincide con data.")
    if len(pedidos) > limite_solicitado:
        raise ErrorContratoPedidos("La pagina excede el limite solicitado.")
    cursor = page["next_cursor"]
    if page["has_more"]:
        if type(cursor) is not str or not cursor or len(cursor) > 2048 or not pedidos:
            raise ErrorContratoPedidos("Paginacion inconsistente en la respuesta de Pedidos.")
    elif cursor is not None:
        raise ErrorContratoPedidos("La ultima pagina debe tener next_cursor nulo.")
    return PaginaPedidosV2(
        request_id=request_id,
        pedidos=pedidos,
        has_more=page["has_more"],
        limit=page["limit"],
        next_cursor=cursor,
        returned=page["returned"],
    )


def _detalle_error(respuesta: RespuestaHTTP) -> tuple[str | None, str | None]:
    try:
        raiz = _json_estricto(respuesta.body)
        if type(raiz) is not dict or set(raiz) != {"error"}:
            return None, None
        error = raiz["error"]
        if type(error) is not dict or set(error) != {"code", "message", "request_id"}:
            return None, None
        code = error["code"]
        request_id = error["request_id"]
        if type(code) is not str or not PATRON_CODIGO_ERROR.fullmatch(code):
            code = None
        if type(request_id) is not str or not PATRON_REQUEST_ID.fullmatch(request_id):
            request_id = None
        return code, request_id
    except ErrorContratoPedidos:
        return None, None


def _retry_after(headers: Mapping[str, str], maximo: float) -> float | None:
    valor = headers.get("retry-after")
    if valor is None or not valor.isascii() or not valor.isdigit():
        return None
    return min(float(int(valor)), maximo)


def _backoff_exponencial(numero_reintento: int) -> float:
    return min(30.0, 0.5 * (2 ** (numero_reintento - 1)))


class ClientePedidosV2:
    """Cliente GET sin estado durable; nunca participa en el camino de venta local."""

    def __init__(
        self,
        *,
        url: str,
        token: str,
        sucursal_ids: Sequence[int],
        edge_id: str | None = None,
        pos_branch_id: str | None = None,
        ca_bundle: str | Path | None = None,
        timeout: float = 15.0,
        max_response_bytes: int = 2 * 1024 * 1024,
        max_reintentos: int = 2,
        max_retry_after: float = 120.0,
        max_window_days: int = 31,
        max_paginas: int = 1000,
        transporte: TransportePedidos | None = None,
        dormir: Callable[[float], None] = time.sleep,
        backoff: Callable[[int], float] = _backoff_exponencial,
    ) -> None:
        self._url = _validar_url(url)
        if (
            type(token) is not str
            or not 32 <= len(token) <= 512
            or any(caracter.isspace() or caracter == "," for caracter in token)
        ):
            raise ErrorConfiguracionPedidos("El token de Pedidos no cumple el formato requerido.")
        ids = tuple(sorted(set(sucursal_ids)))
        if not ids or any(type(valor) is not int or valor <= 0 for valor in ids):
            raise ErrorConfiguracionPedidos("Se requieren IDs enteros positivos de sucursal.")
        if (edge_id is None) != (pos_branch_id is None):
            raise ErrorConfiguracionPedidos("La credencial agregada exige ambas identidades de Pedidos.")
        for nombre, valor in (("edge_id", edge_id), ("pos_branch_id", pos_branch_id)):
            if valor is None:
                continue
            if type(valor) is not str:
                raise ErrorConfiguracionPedidos(f"{nombre} debe ser un UUID canonico.")
            try:
                identidad = uuid.UUID(valor)
            except (ValueError, AttributeError) as exc:
                raise ErrorConfiguracionPedidos(f"{nombre} debe ser un UUID canonico.") from exc
            if identidad.int == 0 or str(identidad) != valor:
                raise ErrorConfiguracionPedidos(f"{nombre} debe ser un UUID canonico no nulo.")
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ErrorConfiguracionPedidos("El timeout de Pedidos debe ser positivo.")
        if type(max_response_bytes) is not int or not 1024 <= max_response_bytes <= 64 * 1024 * 1024:
            raise ErrorConfiguracionPedidos("El limite de respuesta de Pedidos no es valido.")
        if type(max_reintentos) is not int or not 0 <= max_reintentos <= 10:
            raise ErrorConfiguracionPedidos("El numero de reintentos de Pedidos no es valido.")
        if (
            not isinstance(max_retry_after, (int, float))
            or not math.isfinite(max_retry_after)
            or not 0 <= max_retry_after <= 3600
        ):
            raise ErrorConfiguracionPedidos("El limite de Retry-After no es valido.")
        if type(max_window_days) is not int or not 1 <= max_window_days <= 90:
            raise ErrorConfiguracionPedidos("La ventana maxima de Pedidos no es valida.")
        if type(max_paginas) is not int or not 1 <= max_paginas <= 10000:
            raise ErrorConfiguracionPedidos("El limite de paginas de Pedidos no es valido.")

        self._token = token
        self.sucursal_ids = ids
        self.edge_id = edge_id
        self.pos_branch_id = pos_branch_id
        self.timeout = float(timeout)
        self.max_response_bytes = max_response_bytes
        self.max_reintentos = max_reintentos
        self.max_retry_after = float(max_retry_after)
        self.max_window = timedelta(days=max_window_days)
        self.max_paginas = max_paginas
        self._contexto_ssl = _crear_contexto_ssl(ca_bundle)
        self._transporte = transporte or _TransporteUrllib(self._contexto_ssl)
        self._dormir = dormir
        self._backoff = backoff

    def __repr__(self) -> str:
        return f"ClientePedidosV2(url={self._url!r}, sucursal_ids={self.sucursal_ids!r})"

    def _url_pagina(
        self,
        *,
        desde: datetime,
        hasta: datetime,
        limite: int,
        cursor: str | None,
    ) -> str:
        desde_texto = _fecha_consulta(desde, "desde")
        hasta_texto = _fecha_consulta(hasta, "hasta")
        desde_utc = desde.astimezone(timezone.utc)
        hasta_utc = hasta.astimezone(timezone.utc)
        if hasta_utc <= desde_utc:
            raise ErrorConfiguracionPedidos("hasta debe ser posterior a desde.")
        if hasta_utc - desde_utc > self.max_window:
            raise ErrorConfiguracionPedidos("La ventana excede el maximo configurado.")
        if type(limite) is not int or not 1 <= limite <= 500:
            raise ErrorConfiguracionPedidos("limite debe estar entre 1 y 500.")
        if cursor is not None and (
            type(cursor) is not str or not cursor or len(cursor) > 2048
        ):
            raise ErrorConfiguracionPedidos("El cursor de Pedidos no es valido.")
        parametros: list[tuple[str, str]] = [
            ("desde", desde_texto),
            ("hasta", hasta_texto),
            ("sucursal_id", ",".join(str(valor) for valor in self.sucursal_ids)),
            ("limite", str(limite)),
        ]
        if cursor is not None:
            parametros.append(("cursor", cursor))
        partes = urlsplit(self._url)
        return urlunsplit((partes.scheme, partes.netloc, partes.path, urlencode(parametros), ""))

    def _solicitud(self, url: str, request_id: str) -> Request:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
            "Cache-Control": "no-store",
            "X-Request-ID": request_id,
            "User-Agent": "LosTocayosPOS/pedidos-api-v2",
        }
        if self.edge_id is not None:
            headers["X-POS-Edge-ID"] = self.edge_id
            headers["X-POS-Branch-ID"] = self.pos_branch_id
        return Request(url, method="GET", headers=headers)

    def _esperar(self, intento: int, retry_after: float | None = None) -> None:
        if retry_after is None:
            try:
                espera = float(self._backoff(intento))
            except (TypeError, ValueError, OverflowError) as exc:
                raise ErrorConfiguracionPedidos("La funcion de backoff devolvio un valor invalido.") from exc
            if not math.isfinite(espera) or espera < 0:
                raise ErrorConfiguracionPedidos("La funcion de backoff devolvio un valor invalido.")
            espera = min(espera, self.max_retry_after)
        else:
            espera = retry_after
        self._dormir(espera)

    def _validar_destino_respuesta(self, respuesta: RespuestaHTTP) -> None:
        try:
            if _origen(respuesta.url_final) != _origen(self._url):
                raise ErrorContratoPedidos("La API de Pedidos intento cambiar de origen.")
        except ErrorConfiguracionPedidos as exc:
            raise ErrorContratoPedidos("La API de Pedidos devolvio una URL final invalida.") from exc

    def _mapear_error(self, respuesta: RespuestaHTTP) -> ErrorPedidosApi:
        code, request_id = _detalle_error(respuesta)
        comunes = {"status": respuesta.status, "code": code, "request_id": request_id}
        if respuesta.status == 401:
            return ErrorAutenticacionPedidos("Credencial de Pedidos rechazada.", **comunes)
        if respuesta.status == 403:
            return ErrorAlcancePedidos("La credencial no autoriza este alcance de Pedidos.", **comunes)
        if respuesta.status == 404:
            return ErrorEndpointPedidos("La API POS v2 de Pedidos no esta disponible.", **comunes)
        if respuesta.status == 410:
            if code != "retention_gap":
                return ErrorContratoPedidos("Respuesta 410 desconocida de la API de Pedidos.", **comunes)
            return ErrorBrechaRetencion(
                "La sincronizacion de Pedidos requiere conciliacion supervisada.", **comunes
            )
        if respuesta.status == 426:
            return ErrorTLSPedidos("La API de Pedidos exige una conexion TLS valida.", **comunes)
        if respuesta.status == 429:
            return ErrorLimitePedidos(
                "La API de Pedidos limito temporalmente las solicitudes.",
                retry_after=_retry_after(respuesta.headers, self.max_retry_after),
                **comunes,
            )
        if respuesta.status in {500, 502, 503, 504}:
            return ErrorTemporalPedidos("La API de Pedidos no esta disponible temporalmente.", **comunes)
        if 300 <= respuesta.status < 400:
            return ErrorContratoPedidos("La API de Pedidos intento redirigir la solicitud.", **comunes)
        return ErrorContratoPedidos("Respuesta HTTP no contemplada por el contrato de Pedidos.", **comunes)

    def listar_pagina(
        self,
        *,
        desde: datetime,
        hasta: datetime,
        limite: int = 100,
        cursor: str | None = None,
        request_id: str | None = None,
    ) -> PaginaPedidosV2:
        """Obtiene una pagina. Nunca modifica ni interpreta el cursor opaco."""

        request_id = request_id or str(uuid.uuid4())
        if not PATRON_REQUEST_ID.fullmatch(request_id):
            raise ErrorConfiguracionPedidos("X-Request-ID no cumple el formato del contrato.")
        url = self._url_pagina(desde=desde, hasta=hasta, limite=limite, cursor=cursor)

        for numero_intento in range(self.max_reintentos + 1):
            solicitud = self._solicitud(url, request_id)
            try:
                respuesta = self._transporte(
                    solicitud,
                    timeout=self.timeout,
                    max_response_bytes=self.max_response_bytes,
                )
            except (ssl.SSLCertVerificationError, ssl.SSLError) as exc:
                raise ErrorTLSPedidos("No fue posible verificar TLS con la API de Pedidos.") from exc
            except URLError as exc:
                if isinstance(exc.reason, (ssl.SSLCertVerificationError, ssl.SSLError)):
                    raise ErrorTLSPedidos("No fue posible verificar TLS con la API de Pedidos.") from exc
                if numero_intento < self.max_reintentos:
                    self._esperar(numero_intento + 1)
                    continue
                raise ErrorTransportePedidos("No fue posible conectar con la API de Pedidos.") from exc
            except (TimeoutError, socket.timeout, ConnectionError, OSError) as exc:
                if numero_intento < self.max_reintentos:
                    self._esperar(numero_intento + 1)
                    continue
                raise ErrorTransportePedidos("No fue posible conectar con la API de Pedidos.") from exc

            self._validar_destino_respuesta(respuesta)
            headers = {str(k).lower(): str(v).strip() for k, v in respuesta.headers.items()}
            _validar_content_length(headers, self.max_response_bytes)
            if len(respuesta.body) > self.max_response_bytes:
                raise ErrorTamanoRespuestaPedidos(
                    "La respuesta de Pedidos excede el limite permitido."
                )
            if respuesta.status == 200:
                return parsear_pagina_v2(
                    respuesta.body,
                    headers=headers,
                    sucursales_permitidas=self.sucursal_ids,
                    limite_solicitado=limite,
                    max_response_bytes=self.max_response_bytes,
                )
            error = self._mapear_error(
                RespuestaHTTP(respuesta.status, headers, respuesta.body, respuesta.url_final)
            )
            if respuesta.status in ESTADOS_TRANSITORIOS and numero_intento < self.max_reintentos:
                espera = error.retry_after if isinstance(error, ErrorLimitePedidos) else None
                self._esperar(numero_intento + 1, espera)
                continue
            raise error
        raise AssertionError("Bucle de reintentos inalcanzable")

    def iterar_paginas(
        self,
        *,
        desde: datetime,
        hasta: datetime,
        limite: int = 100,
        cursor_inicial: str | None = None,
    ) -> Iterator[PaginaPedidosV2]:
        """Entrega paginas validadas y detecta loops o retrocesos entre ellas."""

        cursor = cursor_inicial
        cursores_vistos = {cursor} if cursor is not None else set()
        parejas_vistas: set[tuple[int, uuid.UUID]] = set()
        ultima_clave: tuple[datetime, int] | None = None
        for _numero in range(1, self.max_paginas + 1):
            pagina = self.listar_pagina(
                desde=desde, hasta=hasta, limite=limite, cursor=cursor
            )
            for pedido in pagina.pedidos:
                clave = pedido.fecha_confirmacion, pedido.id
                if ultima_clave is not None and clave <= ultima_clave:
                    raise ErrorContratoPedidos("La paginacion retrocedio o repitio pedidos.")
                pareja = (pedido.sucursal.id, pedido.codigo_publico)
                if pareja in parejas_vistas:
                    raise ErrorContratoPedidos("La paginacion repitio una identidad de pedido.")
                ultima_clave = clave
                parejas_vistas.add(pareja)
            if pagina.has_more and pagina.next_cursor in cursores_vistos:
                raise ErrorContratoPedidos("La API de Pedidos repitio un cursor.")
            yield pagina
            if not pagina.has_more:
                return
            cursor = pagina.next_cursor
            cursores_vistos.add(cursor)
        raise ErrorContratoPedidos("La API de Pedidos excedio el limite de paginas.")

    def sincronizar_ventana(
        self,
        *,
        desde: datetime,
        hasta: datetime,
        aplicar_pagina: Callable[[PaginaPedidosV2, CheckpointPaginaPedidos], None],
        limite: int = 100,
        cursor_inicial: str | None = None,
    ) -> ResultadoSincronizacionPedidos:
        """Aplica paginas con un callback transaccional.

        El callback debe hacer upsert de la pagina y persistir ``checkpoint`` en
        la misma transaccion. Esta funcion nunca atrapa errores del callback ni
        avanza a la siguiente pagina antes de que este termine.
        """

        cursor_entrada = cursor_inicial
        paginas = 0
        pedidos = 0
        for pagina in self.iterar_paginas(
            desde=desde,
            hasta=hasta,
            limite=limite,
            cursor_inicial=cursor_inicial,
        ):
            paginas += 1
            checkpoint = CheckpointPaginaPedidos(
                cursor_entrada=cursor_entrada,
                cursor_siguiente=pagina.next_cursor,
                ventana_completa=not pagina.has_more,
                numero_pagina=paginas,
            )
            aplicar_pagina(pagina, checkpoint)
            pedidos += pagina.returned
            cursor_entrada = pagina.next_cursor
        return ResultadoSincronizacionPedidos(
            paginas=paginas,
            pedidos=pedidos,
            ventana_completa=True,
        )


__all__ = [
    "API_VERSION",
    "CheckpointPaginaPedidos",
    "ClientePedidosV2",
    "ErrorAlcancePedidos",
    "ErrorAutenticacionPedidos",
    "ErrorBrechaRetencion",
    "ErrorConfiguracionPedidos",
    "ErrorContratoPedidos",
    "ErrorEndpointPedidos",
    "ErrorLimitePedidos",
    "ErrorPedidosApi",
    "ErrorTLSPedidos",
    "ErrorTamanoRespuestaPedidos",
    "ErrorTemporalPedidos",
    "ErrorTransportePedidos",
    "EstadoSincronizacionPedidos",
    "ItemPedidoRemoto",
    "PaginaPedidosV2",
    "PedidoRemoto",
    "ProductoPedidoRemoto",
    "RespuestaHTTP",
    "ResultadoSincronizacionPedidos",
    "SucursalPedidoRemota",
    "parsear_pagina_v2",
]
