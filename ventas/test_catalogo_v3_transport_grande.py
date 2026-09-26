"""Prueba de tamaño HTTP del snapshot v3 sin cambiar su límite semántico."""

import copy
import json
import uuid
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings
from django.http import JsonResponse
from django.test import SimpleTestCase

from ventas.catalogo_central import (
    MAX_SNAPSHOT_BYTES,
    bytes_canonicos_snapshot,
    checksum_snapshot,
    validar_publicacion_catalogo,
)
from ventas.central_api import ClienteCentral, ErrorContratoCentral


_FIJACION = (
    Path(__file__).resolve().parents[1]
    / "contracts"
    / "edge-central"
    / "fixtures"
    / "catalogo-publicacion-v3-lab01-promocion.json"
)
_URL = "https://central.example.invalid/api/v3/edge/catalogo/publicaciones/actual/"


class _RespuestaHTTP(BytesIO):
    def __init__(self, cuerpo, *, declarar_longitud=True):
        super().__init__(cuerpo)
        self.headers = {"Content-Type": "application/json"}
        if declarar_longitud:
            self.headers["Content-Length"] = str(len(cuerpo))

    def geturl(self):
        return _URL

    def getcode(self):
        return 200


class _Opener:
    def __init__(self, cuerpo, *, declarar_longitud=True):
        self.cuerpo = cuerpo
        self.declarar_longitud = declarar_longitud

    def open(self, _solicitud, *, timeout):
        return _RespuestaHTTP(
            self.cuerpo, declarar_longitud=self.declarar_longitud
        )


def _snapshot_grande_valido():
    """Acerca el JSON canónico a 1 MiB con productos válidos e identidades únicas."""

    raiz = json.loads(_FIJACION.read_text(encoding="utf-8"))
    originales = raiz["contenido"]["productos"]
    plantilla = originales[-1]
    adicionales = []
    for indice in range(3000):
        producto = copy.deepcopy(plantilla)
        producto["producto_central_id"] = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"tcys-lab-catalogo-grande-{indice}")
        )
        producto["codigo"] = f"LAB-G-{indice:04d}"
        producto["nombre"] = (
            f"Producto de certificación número {indice:04d} " + "á" * 100
        )
        producto["nombre_corto"] = f"G-{indice:04d}"
        producto["orden"] = 100 + indice
        adicionales.append(producto)

    def con_cantidad(cantidad):
        raiz["contenido"]["productos"] = originales + adicionales[:cantidad]
        raiz["conteos"]["productos"] = len(raiz["contenido"]["productos"])
        raiz["contenido_sha256"] = checksum_snapshot(raiz)
        return len(bytes_canonicos_snapshot(raiz, excluir_checksum=False))

    minimo, maximo = 0, len(adicionales)
    while minimo < maximo:
        medio = (minimo + maximo + 1) // 2
        if con_cantidad(medio) <= MAX_SNAPSHOT_BYTES:
            minimo = medio
        else:
            maximo = medio - 1
    con_cantidad(minimo)
    return raiz


class CatalogoV3TransporteGrandeTests(SimpleTestCase):
    def _cliente(self, cuerpo, *, declarar_longitud=True):
        return ClienteCentral(
            base_url="https://central.example.invalid",
            token="T" * 40,
            max_response_bytes=settings.CENTRAL_MAX_RESPONSE_BYTES,
            opener=_Opener(cuerpo, declarar_longitud=declarar_longitud),
        )

    def test_snapshot_canonico_valido_cabe_en_respuesta_http_django(self):
        snapshot = _snapshot_grande_valido()
        identidad = SimpleNamespace(
            id=uuid.UUID(snapshot["sucursal"]["id"]),
            clave=snapshot["sucursal"]["clave"],
        )
        validar_publicacion_catalogo(snapshot, identidad)
        canonicos = len(bytes_canonicos_snapshot(snapshot, excluir_checksum=False))
        cuerpo_http = JsonResponse(snapshot).content
        self.assertLessEqual(canonicos, MAX_SNAPSHOT_BYTES)
        self.assertGreater(canonicos, 950 * 1024)
        self.assertGreater(len(cuerpo_http), 1024 * 1024)
        respuesta = self._cliente(cuerpo_http).solicitar(
            metodo="GET",
            ruta="/api/v3/edge/catalogo/publicaciones/actual/",
        )
        self.assertEqual(respuesta.status, 200)
        self.assertEqual(respuesta.datos, snapshot)

    def test_respuesta_sin_content_length_mayor_que_limite_sigue_rechazada(self):
        limite = settings.CENTRAL_MAX_RESPONSE_BYTES
        cuerpo = b'{"dato":"' + b"a" * limite + b'"}'
        with self.assertRaisesMessage(ErrorContratoCentral, "supera el limite"):
            self._cliente(
                cuerpo, declarar_longitud=False
            ).solicitar(
                metodo="GET",
                ruta="/api/v3/edge/catalogo/publicaciones/actual/",
            )
