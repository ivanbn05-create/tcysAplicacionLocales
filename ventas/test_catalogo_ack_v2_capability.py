"""ACK de catálogo v2 grande: capacidad explícita, durabilidad y transición v3."""

import copy
import uuid
from datetime import timedelta
from types import SimpleNamespace

from django.contrib.auth.hashers import make_password
from django.test import TestCase, override_settings
from django.utils import timezone

from personas.models import Sucursal
from catalogo.models import PublicacionCatalogoCentral
from ventas.catalogo_central import (
    ErrorCatalogoCentral,
    aplicar_publicacion_catalogo,
    checksum_snapshot,
)
from ventas.central_api import ErrorContratoCentral, ErrorTransporteCentral
from ventas.models import ConfiguracionSucursal, EventoOutbox
from ventas.sincronizacion_central import (
    hash_payload,
    json_canonico,
    sincronizar_outbox_central,
)


CAPACIDAD = {
    "x-catalog-ack-profile": "mappings-large-1",
    "x-catalog-ack-max-body-bytes": "1048576",
    "cache-control": "no-store",
}


class _CentralAckFalso:
    def __init__(self, ack, *, capacidad=CAPACIDAD, estado_options=204, perder_primero=False, estado_post=200, error_post=False, ack_id_otro=False):
        self.ack = ack
        self.capacidad = capacidad
        self.estado_options = estado_options
        self.perder_primero = perder_primero
        self.estado_post = estado_post
        self.error_post = error_post
        self.ack_id_otro = ack_id_otro
        self.llamadas = []
        self.posts = []

    def solicitar(self, **argumentos):
        self.llamadas.append(argumentos)
        if argumentos["metodo"] == "OPTIONS":
            return SimpleNamespace(
                status=self.estado_options,
                headers=self.capacidad,
                datos={},
            )
        self.posts.append(argumentos)
        if self.perder_primero and len(self.posts) == 1:
            raise ErrorTransporteCentral("La respuesta se perdió tras recibir el ACK.")
        if self.error_post:
            raise ErrorContratoCentral("Respuesta no parseable del proxy.")
        return SimpleNamespace(
            status=self.estado_post,
            headers={},
            datos={
                "recibido": True,
                "acuse": str(uuid.uuid4()),
                "ack_id": str(uuid.uuid4()) if self.ack_id_otro else str(self.ack.id),
                "estado_registrado": "aplicado",
            },
        )


class AckCatalogoV2GrandeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sucursal = Sucursal.objects.create(
            clave="ACK277", nombre="ACK catálogo 277"
        )
        cls.config = ConfiguracionSucursal.objects.create(
            sucursal=cls.sucursal,
            clave_administrador=make_password("7391"),
        )
        cls.categoria_id = uuid.uuid4()
        cls.productos_ids = [
            uuid.uuid5(uuid.NAMESPACE_URL, f"ack-v2-277/{indice}")
            for indice in range(277)
        ]

    def ajustes(self, *, v2=True, v3=False):
        return override_settings(
            CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2=v2,
            CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V3=v3,
            SUCURSAL_CLAVE=self.sucursal.clave,
            CENTRAL_API_BASE_URL="https://central.example.invalid",
            CENTRAL_BRANCH_ID=str(self.sucursal.id),
            CENTRAL_BRANCH_CODE=self.sucursal.clave,
            CENTRAL_POS_INSTANCE_ID=str(self.config.instalacion_id),
            CENTRAL_CATALOG_TOKEN="T" * 40,
        )

    def publicacion_v2(self, *, cantidad=277, version=1, anterior=None):
        productos = []
        for indice, identidad in enumerate(self.productos_ids[:cantidad]):
            productos.append(
                {
                    "producto_central_id": str(identidad),
                    "categoria_central_id": str(self.categoria_id),
                    "codigo": f"P{indice:03d}",
                    "nombre": f"Producto {indice:03d}",
                    "nombre_corto": f"P{indice:03d}",
                    "orden": indice + 1,
                    "permite_termino": False,
                    "termino_predeterminado": "",
                    "abreviaturas_termino": {},
                    "destino_impresion": "cocina",
                    "activo": True,
                    "imagen": {
                        "politica": "conservar_local_o_placeholder",
                        "asset": None,
                    },
                    "precio": {
                        "importe": "35.00",
                        "origen": "global",
                        "vigente_desde": "2026-01-01",
                        "vigente_hasta": None,
                    },
                }
            )
        datos = {
            "version_contrato": 2,
            "tipo": "snapshot_completo",
            "release_id": str(uuid.uuid4()),
            "publicacion_id": str(uuid.uuid4()),
            "publicacion_anterior_id": str(anterior) if anterior else None,
            "version_sucursal": version,
            "sucursal": {
                "id": str(self.sucursal.id),
                "clave": self.sucursal.clave,
            },
            "publicada_en": "2026-09-24T12:00:00-06:00",
            "aplicar_desde": "2026-09-24",
            "moneda": "MXN",
            "conteos": {"categorias": 1, "productos": cantidad},
            "contenido": {
                "categorias": [
                    {
                        "categoria_central_id": str(self.categoria_id),
                        "codigo": "COMIDA",
                        "nombre": "Comida",
                        "orden": 1,
                        "activa": True,
                    }
                ],
                "productos": productos,
            },
        }
        datos["contenido_sha256"] = checksum_snapshot(datos)
        return datos

    def aplicar_v2(self, *, cantidad=277):
        with self.ajustes():
            aplicar_publicacion_catalogo(
                self.sucursal, self.publicacion_v2(cantidad=cantidad)
            )
        return EventoOutbox.objects.get(
            sucursal=self.sucursal, tipo="catalogo.aplicado", version_contrato=2
        )

    def test_277_productos_ack_perdido_repetido_con_perfil_explicito(self):
        ack = self.aplicar_v2()
        tamano = len(json_canonico(ack.datos))
        self.assertEqual(len(ack.datos["mapeos_producto"]), 277)
        self.assertGreater(tamano, 16 * 1024)
        self.assertLessEqual(tamano, 1024 * 1024)
        self.assertEqual(ack.payload_hash, hash_payload(ack.datos))
        central = _CentralAckFalso(ack, perder_primero=True)
        with self.ajustes():
            primero = sincronizar_outbox_central(
                cliente_factory=lambda **_opciones: central
            )
            ack.refresh_from_db()
            self.assertEqual(primero["pendientes"], 1)
            self.assertEqual(ack.estado_entrega, EventoOutbox.EstadoEntrega.PENDIENTE)
            EventoOutbox.objects.filter(pk=ack.pk).update(
                proximo_intento_en=timezone.now() - timedelta(seconds=1)
            )
            segundo = sincronizar_outbox_central(
                cliente_factory=lambda **_opciones: central
            )
            tercero = sincronizar_outbox_central(
                cliente_factory=lambda **_opciones: central
            )
        self.assertEqual(segundo["entregados"], 1)
        self.assertEqual(tercero["entregados"], 0)
        self.assertEqual(len(central.posts), 2)
        self.assertEqual(central.posts[0]["perfil_ack"], "mappings-large-1")
        self.assertEqual(central.posts[1]["perfil_ack"], "mappings-large-1")
        self.assertEqual(central.posts[0]["payload"], central.posts[1]["payload"])
        self.assertEqual(central.posts[0]["idempotencia"], str(ack.id))
        self.assertEqual(central.posts[1]["idempotencia"], str(ack.id))
        options = [c for c in central.llamadas if c["metodo"] == "OPTIONS"]
        self.assertGreaterEqual(len(options), 2)
        self.assertEqual(options[0]["ruta"], central.posts[0]["ruta"])
        self.assertIn("/api/v2/", options[0]["ruta"])
        ack.refresh_from_db()
        self.assertEqual(ack.estado_entrega, EventoOutbox.EstadoEntrega.ENTREGADO)
        self.assertEqual(ack.intentos, 2)

    def test_capacidad_ausente_o_405_deja_ack_pendiente_sin_post(self):
        ack = self.aplicar_v2()
        for estado, headers in ((204, {}), (405, {})):
            central = _CentralAckFalso(
                ack, capacidad=headers, estado_options=estado
            )
            with self.ajustes():
                resultado = sincronizar_outbox_central(
                    cliente_factory=lambda **_opciones: central
                )
            ack.refresh_from_db()
            self.assertEqual(resultado["pendientes"], 1)
            self.assertEqual(ack.estado_entrega, EventoOutbox.EstadoEntrega.PENDIENTE)
            self.assertEqual(central.posts, [])
            self.assertEqual(
                [c["metodo"] for c in central.llamadas], ["OPTIONS"]
            )
            EventoOutbox.objects.filter(pk=ack.pk).update(
                proximo_intento_en=timezone.now() - timedelta(seconds=1)
            )

    def test_413_o_respuesta_no_parseable_tras_options_conserva_ack(self):
        ack = self.aplicar_v2()
        payload_original = copy.deepcopy(ack.datos)
        for estado_post, error_post in ((413, False), (200, True)):
            central = _CentralAckFalso(
                ack, estado_post=estado_post, error_post=error_post
            )
            with self.ajustes():
                resultado = sincronizar_outbox_central(
                    cliente_factory=lambda **_opciones: central
                )
            ack.refresh_from_db()
            self.assertEqual(resultado["pendientes"], 1)
            self.assertEqual(ack.estado_entrega, EventoOutbox.EstadoEntrega.PENDIENTE)
            self.assertEqual(ack.datos, payload_original)
            self.assertEqual(ack.payload_hash, hash_payload(payload_original))
            self.assertEqual(len(central.posts), 1)
            self.assertEqual(central.posts[0]["perfil_ack"], "mappings-large-1")
            EventoOutbox.objects.filter(pk=ack.pk).update(
                proximo_intento_en=timezone.now() - timedelta(seconds=1)
            )

    def test_200_ack_de_otro_id_exige_conciliacion(self):
        ack = self.aplicar_v2()
        central = _CentralAckFalso(ack, ack_id_otro=True)
        with self.ajustes():
            resultado = sincronizar_outbox_central(
                cliente_factory=lambda **_opciones: central
            )
        ack.refresh_from_db()
        self.assertEqual(resultado["conciliacion"], 1)
        self.assertEqual(ack.estado_entrega, EventoOutbox.EstadoEntrega.CONCILIACION)
        self.assertEqual(ack.payload_hash, hash_payload(ack.datos))
        self.assertEqual(len(central.posts), 1)

    def test_ruta_local_invalida_va_cuarentena_sin_options(self):
        ack = self.aplicar_v2()
        datos = copy.deepcopy(ack.datos)
        datos["publicacion_id"] = "id-invalido"
        EventoOutbox.objects.filter(pk=ack.pk).update(
            datos=datos, payload_hash=hash_payload(datos)
        )
        central = _CentralAckFalso(ack)
        with self.ajustes():
            resultado = sincronizar_outbox_central(
                cliente_factory=lambda **_opciones: central
            )
        ack.refresh_from_db()
        self.assertEqual(resultado["suspendidos"], 1)
        self.assertEqual(ack.estado_entrega, EventoOutbox.EstadoEntrega.CUARENTENA)
        self.assertEqual(central.llamadas, [])

    def test_transicion_v2_a_v3_y_rollback_de_flag_conservan_ack_v2(self):
        ack_v2 = self.aplicar_v2()
        datos_v2 = copy.deepcopy(ack_v2.datos)
        v3 = self.publicacion_v2()
        v3["version_contrato"] = 3
        v3["conteos"]["promociones"] = 0
        v3["contenido"]["promociones"] = []
        for producto in v3["contenido"]["productos"]:
            producto["disponible_sucursal"] = True
        v3["contenido_sha256"] = checksum_snapshot(v3)
        with self.ajustes(v2=True, v3=True):
            aplicar_publicacion_catalogo(self.sucursal, v3)
        ack_v3 = EventoOutbox.objects.get(
            sucursal=self.sucursal, tipo="catalogo.aplicado", version_contrato=3
        )
        ack_v2.refresh_from_db()
        self.assertEqual(ack_v2.datos, datos_v2)
        self.assertEqual(ack_v2.payload_hash, hash_payload(datos_v2))
        self.assertEqual(
            PublicacionCatalogoCentral.objects.filter(sucursal=self.sucursal).count(),
            2,
        )
        with self.ajustes(v2=True, v3=False):
            with self.assertRaises(ErrorCatalogoCentral):
                aplicar_publicacion_catalogo(
                    self.sucursal,
                    self.publicacion_v2(
                        version=2,
                        anterior=ack_v2.agregado_id,
                    ),
                )
            central_v2 = _CentralAckFalso(ack_v2)
            resultado_v2 = sincronizar_outbox_central(
                cliente_factory=lambda **_opciones: central_v2
            )
        self.assertEqual(resultado_v2["entregados"], 1)
        self.assertEqual(len(central_v2.posts), 1)
        self.assertIn("/api/v2/", central_v2.posts[0]["ruta"])
        ack_v2.refresh_from_db()
        ack_v3.refresh_from_db()
        self.assertEqual(ack_v2.estado_entrega, EventoOutbox.EstadoEntrega.ENTREGADO)
        self.assertEqual(ack_v3.estado_entrega, EventoOutbox.EstadoEntrega.PENDIENTE)
        with self.ajustes(v2=False, v3=True):
            central_v3 = _CentralAckFalso(ack_v3)
            resultado_v3 = sincronizar_outbox_central(
                cliente_factory=lambda **_opciones: central_v3
            )
        self.assertEqual(resultado_v3["entregados"], 1)
        self.assertEqual(
            [c["metodo"] for c in central_v3.llamadas], ["POST"]
        )
        self.assertNotIn("perfil_ack", central_v3.posts[0])
        self.assertIn("/api/v3/", central_v3.posts[0]["ruta"])

    def test_mas_de_un_mib_se_cuarentena_antes_de_post(self):
        ack = self.aplicar_v2()
        datos = copy.deepcopy(ack.datos)
        datos["mapeos_producto"].append(
            {**datos["mapeos_producto"][0], "relleno": "x" * (1024 * 1024)}
        )
        EventoOutbox.objects.filter(pk=ack.pk).update(
            datos=datos, payload_hash=hash_payload(datos)
        )
        llamada = []
        with self.ajustes():
            resultado = sincronizar_outbox_central(
                cliente_factory=lambda **opciones: llamada.append(opciones)
            )
        ack.refresh_from_db()
        self.assertGreater(len(json_canonico(datos)), 1024 * 1024)
        self.assertEqual(resultado["suspendidos"], 1)
        self.assertEqual(ack.estado_entrega, EventoOutbox.EstadoEntrega.CUARENTENA)
        self.assertEqual(llamada, [])

    def test_ack_v2_pequeno_mantiene_ruta_legada_sin_options(self):
        ack = self.aplicar_v2(cantidad=1)
        self.assertLessEqual(len(json_canonico(ack.datos)), 16 * 1024)
        central = _CentralAckFalso(ack, capacidad={})
        with self.ajustes():
            resultado = sincronizar_outbox_central(
                cliente_factory=lambda **_opciones: central
            )
        self.assertEqual(resultado["entregados"], 1)
        self.assertEqual([c["metodo"] for c in central.llamadas], ["POST"])
        self.assertNotIn("perfil_ack", central.posts[0])
