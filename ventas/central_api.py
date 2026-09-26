"""Transporte HTTPS estricto para contratos candidatos Edge <-> Central.

Las rutas v2 permanecen desactivadas por settings hasta que agente1 reconcilie
contratos, scopes y despliegue TLS. Este modulo no registra tokens ni payloads.
"""

from __future__ import annotations

import json
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


class ErrorCentral(Exception):
    def __init__(self, mensaje, *, status=None, request_id=""):
        super().__init__(mensaje)
        self.status = status
        self.request_id = request_id


class ErrorConfiguracionCentral(ErrorCentral):
    pass


class ErrorTLSCentral(ErrorCentral):
    pass


class ErrorTransporteCentral(ErrorCentral):
    pass


class ErrorContratoCentral(ErrorCentral):
    pass


@dataclass(frozen=True, slots=True)
class RespuestaCentral:
    status: int
    headers: dict[str, str]
    datos: dict[str, Any]
    body_bytes: bytes


class _SinRedirecciones(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _origen(url):
    partes = urlsplit(url)
    if (
        partes.scheme.lower() != "https"
        or not partes.hostname
        or partes.username is not None
        or partes.password is not None
    ):
        raise ErrorConfiguracionCentral(
            "La URL Central debe ser HTTPS absoluta y no incluir credenciales."
        )
    try:
        puerto = partes.port or 443
    except ValueError as exc:
        raise ErrorConfiguracionCentral("La URL Central contiene un puerto invalido.") from exc
    return partes.hostname.lower(), puerto


def _objeto_sin_duplicados(pares):
    resultado = {}
    for clave, valor in pares:
        if clave in resultado:
            raise ValueError("clave duplicada")
        resultado[clave] = valor
    return resultado


def _json_estricto(body):
    try:
        datos = json.loads(
            body,
            object_pairs_hook=_objeto_sin_duplicados,
            parse_constant=lambda _valor: (_ for _ in ()).throw(ValueError("constante")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ErrorContratoCentral("El Central no respondio JSON valido.") from exc
    if type(datos) is not dict:
        raise ErrorContratoCentral("La respuesta Central debe ser un objeto JSON.")
    return datos


class ClienteCentral:
    def __init__(
        self,
        *,
        base_url,
        token,
        ca_bundle=None,
        timeout=15,
        max_response_bytes=2 * 1024 * 1024,
        opener=None,
    ):
        self.base_url = str(base_url or "").rstrip("/") + "/"
        _origen(self.base_url)
        if type(token) is not str or not 32 <= len(token) <= 512 or any(
            caracter.isspace() or caracter == "," for caracter in token
        ):
            raise ErrorConfiguracionCentral("La credencial Central no cumple el formato requerido.")
        if not 1 <= float(timeout) <= 60:
            raise ErrorConfiguracionCentral("El timeout Central debe estar entre 1 y 60.")
        if not 4096 <= int(max_response_bytes) <= 4 * 1024 * 1024:
            raise ErrorConfiguracionCentral("El limite de respuesta Central no es valido.")
        self._token = token
        self.timeout = float(timeout)
        self.max_response_bytes = int(max_response_bytes)
        if opener is None:
            try:
                contexto = ssl.create_default_context(
                    cafile=str(Path(ca_bundle)) if ca_bundle else None
                )
            except (OSError, ssl.SSLError) as exc:
                raise ErrorConfiguracionCentral("No fue posible cargar el CA bundle Central.") from exc
            if contexto.verify_mode != ssl.CERT_REQUIRED or not contexto.check_hostname:
                raise ErrorConfiguracionCentral("La verificacion TLS Central debe permanecer activa.")
            opener = build_opener(HTTPSHandler(context=contexto), _SinRedirecciones())
        self._opener = opener

    def __repr__(self):
        return f"ClienteCentral(base_url={self.base_url!r})"

    def solicitar(self, *, metodo, ruta, payload=None, idempotencia=""):
        if (
            type(ruta) is not str
            or not ruta.startswith("/")
            or ruta.startswith("//")
            or "?" in ruta
            or "#" in ruta
            or "\\" in ruta
        ):
            raise ErrorConfiguracionCentral("La ruta Central no es valida.")
        url = urljoin(self.base_url, ruta.lstrip("/"))
        if _origen(url) != _origen(self.base_url):
            raise ErrorConfiguracionCentral("La ruta Central intento cambiar de origen.")
        body = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
            "Cache-Control": "no-store",
            "User-Agent": "LosTocayosPOS/central-v2-candidate",
        }
        if payload is not None:
            body = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if idempotencia:
            headers["Idempotency-Key"] = str(idempotencia)
        solicitud = Request(url, data=body, headers=headers, method=metodo)
        try:
            respuesta = self._opener.open(solicitud, timeout=self.timeout)
        except HTTPError as exc:
            respuesta = exc
        except (ssl.SSLCertVerificationError, ssl.SSLError) as exc:
            raise ErrorTLSCentral("No fue posible verificar TLS con el Central.") from exc
        except URLError as exc:
            if isinstance(exc.reason, (ssl.SSLCertVerificationError, ssl.SSLError)):
                raise ErrorTLSCentral("No fue posible verificar TLS con el Central.") from exc
            raise ErrorTransporteCentral("No fue posible conectar con el Central.") from exc
        except (TimeoutError, OSError) as exc:
            raise ErrorTransporteCentral("No fue posible conectar con el Central.") from exc

        try:
            final_url = str(respuesta.geturl())
            if _origen(final_url) != _origen(self.base_url):
                raise ErrorContratoCentral("El Central intento cambiar de origen.")
            normalizados = {
                str(nombre).lower(): str(valor).strip()
                for nombre, valor in respuesta.headers.items()
            }
            content_length = normalizados.get("content-length")
            if content_length is not None:
                if not content_length.isascii() or not content_length.isdigit():
                    raise ErrorContratoCentral("Content-Length Central no es valido.")
                if int(content_length) > self.max_response_bytes:
                    raise ErrorContratoCentral("La respuesta Central supera el limite.")
            contenido = respuesta.read(self.max_response_bytes + 1)
            if len(contenido) > self.max_response_bytes:
                raise ErrorContratoCentral("La respuesta Central supera el limite.")
            status = int(respuesta.getcode())
            if status in {204, 304}:
                if contenido:
                    raise ErrorContratoCentral("Una respuesta sin contenido incluyo un cuerpo.")
                datos = {}
            else:
                content_type = normalizados.get("content-type", "").split(";", 1)[0].lower()
                if content_type != "application/json":
                    raise ErrorContratoCentral("El Central no respondio application/json.")
                datos = _json_estricto(contenido)
            return RespuestaCentral(
                status=status,
                headers=normalizados,
                datos=datos,
                body_bytes=contenido,
            )
        finally:
            respuesta.close()
