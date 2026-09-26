"""Contrato local de catálogo v3: snapshot completo, availability y alistamiento."""

import copy
import json
from io import StringIO
from types import SimpleNamespace
import uuid
from pathlib import Path
from unittest.mock import Mock, patch
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings

from catalogo.models import (
    EstadoAprovisionamientoCatalogo,
    IdentidadProductoCentral,
    Precio,
    Producto,
    PublicacionCatalogoCentral,
)
from personas.models import Sucursal
from ventas.aprovisionamiento import (
    CatalogoInicialPendiente,
    estado_aprovisionamiento,
    exigir_catalogo_operativo,
    ids_productos_vendibles,
    iniciar_aprovisionamiento,
    marcar_esperando_catalogo,
    marcar_listo,
    producto_vendible,
)
from ventas.catalogo_central import (
    ErrorCatalogoCentral,
    aplicar_publicacion_catalogo,
    checksum_snapshot,
    encolar_ack_catalogo_rechazado,
)
from ventas.models import ConfiguracionSucursal, DefinicionPromocion, EventoOutbox
from ventas.promociones import _publicacion_activa
from ventas.sincronizacion_central import _limite_payload_evento, _ruta_evento, hash_payload, json_canonico


class CatalogoV3AprovisionamientoTests(TestCase):
    def setUp(self):
        self.sucursal = Sucursal.objects.create(clave="CAT_V3", nombre="Catálogo v3")
        self.config = ConfiguracionSucursal.objects.create(
            sucursal=self.sucursal,
            clave_administrador=make_password("7391"),
        )
        self.categoria_id = uuid.uuid4()
        self.vendible_id = uuid.uuid4()
        self.no_disponible_id = uuid.uuid4()

    def snapshot(self, *, version=1, anterior=None, disponible=False, importe="35.00"):
        productos = []
        for identidad, codigo, disponible_local in (
            (self.vendible_id, "VENDIBLE", True),
            (self.no_disponible_id, "OTRA", disponible),
        ):
            productos.append({
                "producto_central_id": str(identidad),
                "categoria_central_id": str(self.categoria_id),
                "codigo": codigo,
                "nombre": "Producto " + codigo,
                "nombre_corto": codigo[:8],
                "orden": len(productos) + 1,
                "permite_termino": False,
                "termino_predeterminado": "",
                "abreviaturas_termino": {},
                "destino_impresion": "cocina",
                "activo": True,
                "disponible_sucursal": disponible_local,
                "imagen": {"politica": "conservar_local_o_placeholder", "asset": None},
                "precio": {
                    "importe": importe,
                    "origen": "global",
                    "vigente_desde": "2026-01-01",
                    "vigente_hasta": None,
                },
            })
        contenido = {
            "categorias": [{
                "categoria_central_id": str(self.categoria_id),
                "codigo": "COMIDA",
                "nombre": "Comida",
                "orden": 1,
                "activa": True,
            }],
            "productos": productos,
            "promociones": [],
        }
        publicacion = {
            "version_contrato": 3,
            "tipo": "snapshot_completo",
            "release_id": str(uuid.uuid4()),
            "publicacion_id": str(uuid.uuid4()),
            "publicacion_anterior_id": str(anterior) if anterior else None,
            "version_sucursal": version,
            "sucursal": {"id": str(self.sucursal.id), "clave": self.sucursal.clave},
            "publicada_en": "2026-09-24T12:00:00-06:00",
            "aplicar_desde": "2026-09-24",
            "moneda": "MXN",
            "conteos": {"categorias": 1, "productos": 2, "promociones": 0},
            "contenido": contenido,
        }
        publicacion["contenido_sha256"] = checksum_snapshot(publicacion)
        return publicacion

    def identidad_settings(self):
        return override_settings(
            CENTRAL_BRANCH_ID=str(self.sucursal.id),
            CENTRAL_POS_INSTANCE_ID=str(self.config.instalacion_id),
        )

    def central_v3_settings(self):
        return override_settings(
            CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2=False,
            CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V3=True,
            SUCURSAL_CLAVE=self.sucursal.clave,
            CENTRAL_API_BASE_URL="https://central.example.invalid",
            CENTRAL_BRANCH_ID=str(self.sucursal.id),
            CENTRAL_BRANCH_CODE=self.sucursal.clave,
            CENTRAL_POS_INSTANCE_ID=str(self.config.instalacion_id),
            CENTRAL_CATALOG_TOKEN="T" * 40,
        )

    def test_edge_enrolado_espera_catalogo_y_no_vende_sin_menu(self):
        with self.identidad_settings():
            iniciar_aprovisionamiento(self.sucursal)
            self.assertEqual(estado_aprovisionamiento(self.sucursal)["estado"], "enrolado")
            marcar_esperando_catalogo(self.sucursal)
            estado = estado_aprovisionamiento(self.sucursal)
            self.assertEqual(estado["estado"], "esperando_catalogo_inicial")
            self.assertFalse(estado["listo"])
            with self.assertRaises(CatalogoInicialPendiente):
                exigir_catalogo_operativo(self.sucursal)
            self.assertEqual(ids_productos_vendibles(self.sucursal), set())
        # Una configuración de entorno perdida tras reinicio no reactiva el modo legacy.
        self.assertEqual(estado_aprovisionamiento(self.sucursal)["estado"], "esperando_catalogo_inicial")
        with self.assertRaises(CatalogoInicialPendiente):
            exigir_catalogo_operativo(self.sucursal)

    def test_sincronizador_sin_snapshot_persiste_espera_y_no_habilita_menu(self):
        with override_settings(
            CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2=False,
            CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V3=True,
            SUCURSAL_CLAVE=self.sucursal.clave,
            CENTRAL_API_BASE_URL="https://central.example.invalid",
            CENTRAL_BRANCH_ID=str(self.sucursal.id),
            CENTRAL_BRANCH_CODE=self.sucursal.clave,
            CENTRAL_POS_INSTANCE_ID=str(self.config.instalacion_id),
            CENTRAL_CATALOG_TOKEN="T" * 40,
        ):
            cliente = SimpleNamespace(solicitar=Mock(return_value=SimpleNamespace(status=204)))
            with patch(
                "ventas.management.commands.sincronizar_catalogo_central.ClienteCentral",
                return_value=cliente,
            ):
                call_command("sincronizar_catalogo_central", stdout=StringIO())
            cliente.solicitar.assert_called_once_with(
                metodo="GET",
                ruta="/api/v3/edge/catalogo/publicaciones/actual/",
            )
            self.assertEqual(
                estado_aprovisionamiento(self.sucursal)["estado"],
                "esperando_catalogo_inicial",
            )
            self.assertFalse(PublicacionCatalogoCentral.objects.exists())
            with self.assertRaises(CatalogoInicialPendiente):
                exigir_catalogo_operativo(self.sucursal)

    def test_primera_publicacion_aplica_atomica_pero_no_autoalista(self):
        with self.identidad_settings():
            marcar_esperando_catalogo(self.sucursal)
            publicacion, creada = aplicar_publicacion_catalogo(self.sucursal, self.snapshot())
            self.assertTrue(creada)
            self.assertEqual(publicacion.version_contrato, 3)
            estado = estado_aprovisionamiento(self.sucursal)
            self.assertEqual(estado["estado"], "catalogo_aplicado")
            self.assertFalse(estado["listo"])
            no_disponible = IdentidadProductoCentral.objects.get(
                central_id=self.no_disponible_id
            ).producto
            self.assertFalse(no_disponible.activo)
            self.assertFalse(no_disponible.disponible_sucursal)
            self.assertFalse(Precio.objects.get(producto=no_disponible).activo)
            self.assertFalse(producto_vendible(self.sucursal, no_disponible))
            self.assertEqual(IdentidadProductoCentral.objects.count(), 2)
            ack = EventoOutbox.objects.get(tipo="catalogo.aplicado")
            self.assertEqual(ack.version_contrato, 3)
            self.assertEqual(ack.datos["version_contrato"], 3)
            self.assertEqual(len(ack.datos["mapeos_producto"]), 2)
            self.assertEqual(
                _ruta_evento(ack),
                f"/api/v3/edge/catalogo/publicaciones/{publicacion.publicacion_id}/acuse/",
            )
            marcar_listo(self.sucursal)
            self.assertEqual(estado_aprovisionamiento(self.sucursal)["estado"], "listo")
            vendible = IdentidadProductoCentral.objects.get(central_id=self.vendible_id).producto
            self.assertTrue(producto_vendible(self.sucursal, vendible))
            self.assertFalse(producto_vendible(self.sucursal, no_disponible))
            exigir_catalogo_operativo(self.sucursal)

    def test_rechazo_v3_encola_acuse_v3_sin_aplicar_catalogo(self):
        datos = self.snapshot()
        datos["contenido_sha256"] = "0" * 64
        with self.identidad_settings():
            with self.assertRaises(ErrorCatalogoCentral) as error:
                aplicar_publicacion_catalogo(self.sucursal, datos)
            ack = encolar_ack_catalogo_rechazado(
                self.sucursal, datos, error.exception, version_contrato=3
            )
        self.assertIsNotNone(ack)
        self.assertEqual(ack.version_contrato, 3)
        self.assertEqual(ack.datos["version_contrato"], 3)
        self.assertEqual(ack.datos["estado"], "rechazado")
        self.assertEqual(
            _ruta_evento(ack),
            f"/api/v3/edge/catalogo/publicaciones/{datos['publicacion_id']}/acuse/",
        )
        self.assertFalse(PublicacionCatalogoCentral.objects.exists())

    def test_dos_polls_de_publicacion_invalida_reutilizan_ack(self):
        datos = self.snapshot()
        datos["contenido_sha256"] = "0" * 64
        with override_settings(
            CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2=False,
            CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V3=True,
            SUCURSAL_CLAVE=self.sucursal.clave,
            CENTRAL_API_BASE_URL="https://central.example.invalid",
            CENTRAL_BRANCH_ID=str(self.sucursal.id),
            CENTRAL_BRANCH_CODE=self.sucursal.clave,
            CENTRAL_POS_INSTANCE_ID=str(self.config.instalacion_id),
            CENTRAL_CATALOG_TOKEN="T" * 40,
        ):
            cliente = SimpleNamespace(
                solicitar=Mock(return_value=SimpleNamespace(status=200, datos=datos))
            )
            with patch(
                "ventas.management.commands.sincronizar_catalogo_central.ClienteCentral",
                return_value=cliente,
            ):
                for _ in range(2):
                    with self.assertRaises(CommandError):
                        call_command("sincronizar_catalogo_central", stdout=StringIO())
        self.assertEqual(cliente.solicitar.call_count, 2)
        acks = EventoOutbox.objects.filter(tipo="catalogo.rechazado")
        self.assertEqual(acks.count(), 1)
        ack = acks.get()
        self.assertEqual(ack.version_contrato, 3)
        self.assertEqual(ack.payload_hash, hash_payload(ack.datos))
        self.assertFalse(PublicacionCatalogoCentral.objects.exists())

    def test_acuse_v3_admite_mapeos_exhaustivos_mayores_de_16_kib(self):
        datos = self.snapshot()
        plantilla = datos["contenido"]["productos"][0]
        for indice in range(130):
            producto = copy.deepcopy(plantilla)
            producto["producto_central_id"] = str(uuid.uuid4())
            producto["codigo"] = f"MAS-{indice:03d}"
            producto["nombre"] = f"Producto adicional {indice}"
            producto["nombre_corto"] = f"MAS{indice}"
            datos["contenido"]["productos"].append(producto)
        datos["conteos"]["productos"] = len(datos["contenido"]["productos"])
        datos["contenido_sha256"] = checksum_snapshot(datos)
        with self.identidad_settings():
            aplicar_publicacion_catalogo(self.sucursal, datos)
        ack = EventoOutbox.objects.get(tipo="catalogo.aplicado")
        self.assertEqual(len(ack.datos["mapeos_producto"]), 132)
        self.assertGreater(len(json_canonico(ack.datos)), 16 * 1024)
        self.assertLessEqual(len(json_canonico(ack.datos)), _limite_payload_evento(ack))

    def test_publicacion_corrupta_no_avanza_estado_precio_ni_ultima_valida(self):
        with self.identidad_settings():
            primera, _ = aplicar_publicacion_catalogo(self.sucursal, self.snapshot())
            marcar_listo(self.sucursal)
            siguiente = self.snapshot(version=2, anterior=primera.publicacion_id, importe="75.00")
            siguiente["contenido"]["productos"][0]["precio"]["importe"] = "88.00"
            with self.assertRaises(ErrorCatalogoCentral) as captura:
                aplicar_publicacion_catalogo(self.sucursal, siguiente)
            self.assertEqual(captura.exception.codigo, "checksum_invalido")
            self.assertEqual(PublicacionCatalogoCentral.objects.count(), 1)
            self.assertEqual(estado_aprovisionamiento(self.sucursal)["estado"], "listo")
            producto = IdentidadProductoCentral.objects.get(central_id=self.vendible_id).producto
            self.assertEqual(producto.precio_actual().importe, Decimal("35.00"))

    def test_snapshot_nuevo_puede_retirar_y_reponer_disponibilidad_sin_perder_mapping(self):
        with self.identidad_settings():
            primera, _ = aplicar_publicacion_catalogo(self.sucursal, self.snapshot(disponible=True))
            marcar_listo(self.sucursal)
            extra = IdentidadProductoCentral.objects.get(central_id=self.no_disponible_id)
            self.assertTrue(producto_vendible(self.sucursal, extra.producto))
            segunda, _ = aplicar_publicacion_catalogo(
                self.sucursal,
                self.snapshot(version=2, anterior=primera.publicacion_id, disponible=False),
            )
            extra.producto.refresh_from_db()
            self.assertEqual(estado_aprovisionamiento(self.sucursal)["estado"], "listo")
            self.assertFalse(producto_vendible(self.sucursal, extra.producto))
            self.assertTrue(IdentidadProductoCentral.objects.filter(pk=extra.pk).exists())
            aplicar_publicacion_catalogo(
                self.sucursal,
                self.snapshot(version=3, anterior=segunda.publicacion_id, disponible=True),
            )
            extra.producto.refresh_from_db()
            self.assertTrue(producto_vendible(self.sucursal, extra.producto))
            self.assertEqual(IdentidadProductoCentral.objects.get(pk=extra.pk).producto_id, extra.producto_id)

    def test_rechaza_snapshot_sin_availability_o_sin_vendibles(self):
        faltante = self.snapshot()
        del faltante["contenido"]["productos"][0]["disponible_sucursal"]
        faltante["contenido_sha256"] = checksum_snapshot(faltante)
        with self.assertRaises(ErrorCatalogoCentral) as captura:
            aplicar_publicacion_catalogo(self.sucursal, faltante)
        self.assertEqual(captura.exception.codigo, "schema_no_soportado")
        vacio = self.snapshot()
        for producto in vacio["contenido"]["productos"]:
            producto["disponible_sucursal"] = False
        vacio["contenido_sha256"] = checksum_snapshot(vacio)
        with self.assertRaises(ErrorCatalogoCentral):
            aplicar_publicacion_catalogo(self.sucursal, vacio)
        self.assertFalse(PublicacionCatalogoCentral.objects.exists())
        self.assertFalse(EstadoAprovisionamientoCatalogo.objects.exists())

    def test_precio_local_divergente_bloquea_producto_hasta_publicacion_nueva(self):
        with self.identidad_settings():
            primera, _ = aplicar_publicacion_catalogo(self.sucursal, self.snapshot())
            marcar_listo(self.sucursal)
            producto = IdentidadProductoCentral.objects.get(central_id=self.vendible_id).producto
            precio = producto.precio_actual()
            precio.importe = Decimal("99.00")
            precio.save(update_fields=["importe"])
            self.assertFalse(producto_vendible(self.sucursal, producto))
            self.assertEqual(estado_aprovisionamiento(self.sucursal)["estado"], "catalogo_aplicado")
            with self.assertRaises(CatalogoInicialPendiente):
                exigir_catalogo_operativo(self.sucursal)
            aplicar_publicacion_catalogo(
                self.sucursal,
                self.snapshot(version=2, anterior=primera.publicacion_id),
            )
            self.assertTrue(producto_vendible(self.sucursal, producto))
            self.assertEqual(estado_aprovisionamiento(self.sucursal)["estado"], "listo")

    def test_menu_valido_sobrevive_sin_red_y_nueva_instancia_orm(self):
        with self.identidad_settings():
            aplicar_publicacion_catalogo(self.sucursal, self.snapshot())
            marcar_listo(self.sucursal)
            otra_instancia = Sucursal.objects.get(pk=self.sucursal.pk)
            with patch("ventas.central_api.ClienteCentral", side_effect=AssertionError("No requiere VPS")):
                self.assertEqual(estado_aprovisionamiento(otra_instancia)["estado"], "listo")
                self.assertEqual(len(ids_productos_vendibles(otra_instancia)), 1)

    def test_snapshot_persistido_con_checksum_alterado_no_autoriza_venta(self):
        with self.identidad_settings():
            publicacion, _ = aplicar_publicacion_catalogo(self.sucursal, self.snapshot())
            marcar_listo(self.sucursal)
            dañado = publicacion.snapshot
            dañado["contenido"]["productos"][0]["precio"]["importe"] = "999.00"
            publicacion.snapshot = dañado
            publicacion.save(update_fields=["snapshot"])
            self.assertEqual(ids_productos_vendibles(self.sucursal), set())
            self.assertFalse(estado_aprovisionamiento(self.sucursal)["listo"])

    def test_v3_inicia_cadena_propia_tras_v2_y_conserva_acks_historicos(self):
        def v2(*, version=1, anterior=None):
            datos = self.snapshot(version=version, anterior=anterior)
            datos["version_contrato"] = 2
            del datos["conteos"]["promociones"]
            del datos["contenido"]["promociones"]
            for producto in datos["contenido"]["productos"]:
                del producto["disponible_sucursal"]
            datos["contenido_sha256"] = checksum_snapshot(datos)
            return datos

        with self.identidad_settings():
            primera_v2, _ = aplicar_publicacion_catalogo(self.sucursal, v2())
            segunda_v2, _ = aplicar_publicacion_catalogo(
                self.sucursal, v2(version=2, anterior=primera_v2.publicacion_id)
            )
            acks_v2 = list(
                EventoOutbox.objects.filter(
                    destino=EventoOutbox.Destino.CENTRAL_CATALOGO_ACK,
                    version_contrato=2,
                ).order_by("creado_en")
            )
            self.assertEqual(len(acks_v2), 2)
            datos_v3 = self.snapshot()
            primera_v3, creada = aplicar_publicacion_catalogo(self.sucursal, datos_v3)
            self.assertTrue(creada)
            repetida, creada_repetida = aplicar_publicacion_catalogo(self.sucursal, datos_v3)
            self.assertFalse(creada_repetida)
            self.assertEqual(repetida.pk, primera_v3.pk)
            self.assertEqual(primera_v3.version, 1)
            self.assertEqual(primera_v3.version_contrato, 3)
            self.assertEqual(PublicacionCatalogoCentral.objects.count(), 3)
            self.assertEqual(estado_aprovisionamiento(self.sucursal)["version_contrato"], 3)
            self.assertEqual(_publicacion_activa(self.sucursal.id).pk, primera_v3.pk)
            primera_v2.refresh_from_db()
            self.assertEqual(primera_v2.snapshot["version_contrato"], 2)
            for ack in acks_v2:
                self.assertEqual(ack.datos["version_contrato"], 2)
                self.assertEqual(ack.payload_hash, hash_payload(ack.datos))
                self.assertEqual(
                    _ruta_evento(ack),
                    f"/api/v2/edge/catalogo/publicaciones/{ack.agregado_id}/acuse/",
                )
            marcar_listo(self.sucursal)
            self.assertTrue(estado_aprovisionamiento(self.sucursal)["listo"])
            vendible = IdentidadProductoCentral.objects.get(
                central_id=self.vendible_id
            ).producto
            self.assertTrue(producto_vendible(self.sucursal, vendible))
            segunda_v3, _ = aplicar_publicacion_catalogo(
                self.sucursal,
                self.snapshot(version=2, anterior=primera_v3.publicacion_id, importe="37.00"),
            )
            self.assertEqual(segunda_v3.version_contrato, 3)
            self.assertEqual(estado_aprovisionamiento(self.sucursal)["version"], 2)
            self.assertEqual(vendible.precio_actual().importe, Decimal("37.00"))
            with self.assertRaises(ErrorCatalogoCentral):
                aplicar_publicacion_catalogo(
                    self.sucursal,
                    v2(version=3, anterior=segunda_v2.publicacion_id),
                )

    def test_no_admite_downgrade_a_v2_tras_publicacion_v3(self):
        primera, _ = aplicar_publicacion_catalogo(self.sucursal, self.snapshot())
        vieja = self.snapshot(version=2, anterior=primera.publicacion_id)
        vieja["version_contrato"] = 2
        del vieja["conteos"]["promociones"]
        del vieja["contenido"]["promociones"]
        for producto in vieja["contenido"]["productos"]:
            del producto["disponible_sucursal"]
        vieja["contenido_sha256"] = checksum_snapshot(vieja)
        with self.assertRaises(ErrorCatalogoCentral) as captura:
            aplicar_publicacion_catalogo(self.sucursal, vieja)
        self.assertEqual(captura.exception.codigo, "schema_no_soportado")
        self.assertEqual(PublicacionCatalogoCentral.objects.count(), 1)



    def test_restore_v3_1_recupera_v3_2_y_v3_3_y_segundo_poll_es_idempotente(self):
        with self.identidad_settings():
            primera, _ = aplicar_publicacion_catalogo(self.sucursal, self.snapshot())
            marcar_listo(self.sucursal)
        segunda = self.snapshot(
            version=2, anterior=primera.publicacion_id, importe="37.00"
        )
        tercera = self.snapshot(
            version=3, anterior=segunda["publicacion_id"], importe="39.00"
        )
        rutas = []

        def responder(**kwargs):
            ruta = kwargs["ruta"]
            rutas.append(ruta)
            if ruta.endswith("/publicaciones/actual/"):
                return SimpleNamespace(status=200, datos=tercera)
            if ruta.endswith(f"/publicaciones/{segunda['publicacion_id']}/"):
                return SimpleNamespace(status=200, datos=segunda)
            self.fail(f"Descarga historica inesperada: {ruta}")

        cliente = SimpleNamespace(solicitar=Mock(side_effect=responder))
        with self.central_v3_settings(), patch(
            "ventas.management.commands.sincronizar_catalogo_central.ClienteCentral",
            return_value=cliente,
        ):
            call_command("sincronizar_catalogo_central", stdout=StringIO())
            call_command("sincronizar_catalogo_central", stdout=StringIO())

        self.assertEqual(
            rutas,
            [
                "/api/v3/edge/catalogo/publicaciones/actual/",
                f"/api/v3/edge/catalogo/publicaciones/{segunda['publicacion_id']}/",
                "/api/v3/edge/catalogo/publicaciones/actual/",
            ],
        )
        self.assertEqual(
            list(PublicacionCatalogoCentral.objects.filter(
                sucursal=self.sucursal, version_contrato=3
            ).order_by("version").values_list("version", flat=True)),
            [1, 2, 3],
        )
        self.assertEqual(
            EventoOutbox.objects.filter(
                sucursal=self.sucursal, tipo="catalogo.aplicado",
                version_contrato=3,
            ).count(),
            3,
        )
        self.assertFalse(
            EventoOutbox.objects.filter(
                sucursal=self.sucursal, tipo="catalogo.rechazado"
            ).exists()
        )
        producto = IdentidadProductoCentral.objects.get(
            sucursal=self.sucursal, central_id=self.vendible_id
        ).producto
        self.assertEqual(producto.precio_actual().importe, Decimal("39.00"))
        self.assertTrue(estado_aprovisionamiento(self.sucursal)["listo"])

    def test_edge_vacio_recupera_cadena_v3_completa_sin_autoalistar(self):
        primera = self.snapshot()
        segunda = self.snapshot(
            version=2, anterior=primera["publicacion_id"], importe="37.00"
        )
        tercera = self.snapshot(
            version=3, anterior=segunda["publicacion_id"], importe="39.00"
        )
        respuestas = {
            "/api/v3/edge/catalogo/publicaciones/actual/": tercera,
            f"/api/v3/edge/catalogo/publicaciones/{segunda['publicacion_id']}/": segunda,
            f"/api/v3/edge/catalogo/publicaciones/{primera['publicacion_id']}/": primera,
        }
        rutas = []

        def responder(**kwargs):
            ruta = kwargs["ruta"]
            rutas.append(ruta)
            return SimpleNamespace(status=200, datos=respuestas[ruta])

        cliente = SimpleNamespace(solicitar=Mock(side_effect=responder))
        with self.central_v3_settings(), patch(
            "ventas.management.commands.sincronizar_catalogo_central.ClienteCentral",
            return_value=cliente,
        ):
            call_command("sincronizar_catalogo_central", stdout=StringIO())

        self.assertEqual(len(rutas), 3)
        self.assertEqual(
            list(PublicacionCatalogoCentral.objects.filter(
                sucursal=self.sucursal, version_contrato=3
            ).order_by("version").values_list("version", flat=True)),
            [1, 2, 3],
        )
        self.assertEqual(
            EventoOutbox.objects.filter(
                sucursal=self.sucursal, tipo="catalogo.aplicado",
                version_contrato=3,
            ).count(),
            3,
        )
        self.assertEqual(
            estado_aprovisionamiento(self.sucursal)["estado"],
            "catalogo_aplicado",
        )
        self.assertFalse(estado_aprovisionamiento(self.sucursal)["listo"])

    def test_historial_v3_ausente_corrupto_o_ciclico_conserva_ancla_y_ack(self):
        with self.identidad_settings():
            primera_local, _ = aplicar_publicacion_catalogo(
                self.sucursal, self.snapshot()
            )
            marcar_listo(self.sucursal)
        ack_original = EventoOutbox.objects.get(tipo="catalogo.aplicado")
        for caso in ("ausente", "id_ajeno", "checksum", "ciclo"):
            with self.subTest(caso=caso):
                segunda = self.snapshot(
                    version=2, anterior=primera_local.publicacion_id
                )
                tercera = self.snapshot(
                    version=3, anterior=segunda["publicacion_id"]
                )
                historica = copy.deepcopy(segunda)
                if caso == "id_ajeno":
                    historica["publicacion_id"] = str(uuid.uuid4())
                elif caso == "checksum":
                    historica["contenido_sha256"] = "0" * 64
                elif caso == "ciclo":
                    historica["publicacion_anterior_id"] = tercera["publicacion_id"]
                if caso in {"id_ajeno", "ciclo"}:
                    historica["contenido_sha256"] = checksum_snapshot(historica)

                def responder(**kwargs):
                    ruta = kwargs["ruta"]
                    if ruta.endswith("/publicaciones/actual/"):
                        return SimpleNamespace(status=200, datos=tercera)
                    if caso == "ausente":
                        return SimpleNamespace(status=404, datos={})
                    return SimpleNamespace(status=200, datos=historica)

                cliente = SimpleNamespace(solicitar=Mock(side_effect=responder))
                with self.central_v3_settings(), patch(
                    "ventas.management.commands.sincronizar_catalogo_central.ClienteCentral",
                    return_value=cliente,
                ):
                    with self.assertRaises(CommandError):
                        call_command("sincronizar_catalogo_central", stdout=StringIO())
                self.assertEqual(
                    list(PublicacionCatalogoCentral.objects.filter(
                        sucursal=self.sucursal, version_contrato=3
                    ).values_list("version", flat=True)),
                    [1],
                )
                self.assertEqual(
                    list(EventoOutbox.objects.filter(
                        sucursal=self.sucursal, tipo="catalogo.aplicado"
                    ).values_list("id", flat=True)),
                    [ack_original.id],
                )
                self.assertTrue(estado_aprovisionamiento(self.sucursal)["listo"])

    def test_actual_v3_obsoleta_no_contradice_ack_historico(self):
        with self.identidad_settings():
            primera, _ = aplicar_publicacion_catalogo(self.sucursal, self.snapshot())
            marcar_listo(self.sucursal)
            segunda, _ = aplicar_publicacion_catalogo(
                self.sucursal,
                self.snapshot(version=2, anterior=primera.publicacion_id),
            )
        cliente = SimpleNamespace(solicitar=Mock(return_value=SimpleNamespace(
            status=200, datos=primera.snapshot
        )))
        with self.central_v3_settings(), patch(
            "ventas.management.commands.sincronizar_catalogo_central.ClienteCentral",
            return_value=cliente,
        ):
            with self.assertRaisesMessage(CommandError, "obsoleta"):
                call_command("sincronizar_catalogo_central", stdout=StringIO())
        self.assertEqual(
            PublicacionCatalogoCentral.objects.filter(
                sucursal=self.sucursal, version_contrato=3
            ).order_by("-version").first().pk,
            segunda.pk,
        )
        self.assertEqual(
            EventoOutbox.objects.filter(
                sucursal=self.sucursal, tipo="catalogo.aplicado"
            ).count(),
            2,
        )
        self.assertFalse(
            EventoOutbox.objects.filter(
                sucursal=self.sucursal, tipo="catalogo.rechazado"
            ).exists()
        )
        self.assertTrue(estado_aprovisionamiento(self.sucursal)["listo"])

    def test_publicacion_futura_no_impide_recuperar_prefijo_vigente(self):
        primera = self.snapshot()
        segunda = self.snapshot(
            version=2, anterior=primera["publicacion_id"], importe="37.00"
        )
        tercera = self.snapshot(
            version=3, anterior=segunda["publicacion_id"], importe="39.00"
        )
        segunda["aplicar_desde"] = "2030-01-01"
        tercera["aplicar_desde"] = "2030-01-02"
        respuestas = {
            "/api/v3/edge/catalogo/publicaciones/actual/": tercera,
            f"/api/v3/edge/catalogo/publicaciones/{segunda['publicacion_id']}/": segunda,
            f"/api/v3/edge/catalogo/publicaciones/{primera['publicacion_id']}/": primera,
        }
        cliente = SimpleNamespace(
            solicitar=Mock(side_effect=lambda **kwargs: SimpleNamespace(
                status=200, datos=respuestas[kwargs["ruta"]]
            ))
        )
        with self.central_v3_settings(), patch(
            "ventas.management.commands.sincronizar_catalogo_central.ClienteCentral",
            return_value=cliente,
        ):
            call_command("sincronizar_catalogo_central", stdout=StringIO())
        self.assertEqual(
            list(PublicacionCatalogoCentral.objects.filter(
                sucursal=self.sucursal, version_contrato=3
            ).values_list("version", flat=True)),
            [1],
        )
        self.assertEqual(
            EventoOutbox.objects.filter(
                sucursal=self.sucursal, tipo="catalogo.aplicado",
                version_contrato=3,
            ).count(),
            1,
        )

    def test_restore_producto_nuevo_conserva_mapping_y_ack_historico(self):
        """El UUIDv5 v3 permite confirmar otra vez la misma publicación."""
        from django.db import transaction

        from ventas.sincronizacion_central import sincronizar_outbox_central

        with self.identidad_settings():
            primera, _ = aplicar_publicacion_catalogo(self.sucursal, self.snapshot())
            marcar_listo(self.sucursal)
        EventoOutbox.objects.filter(
            sucursal=self.sucursal, tipo="catalogo.aplicado"
        ).update(estado_entrega=EventoOutbox.EstadoEntrega.ENTREGADO)
        nueva_identidad = uuid.uuid4()
        segunda = self.snapshot(
            version=2, anterior=primera.publicacion_id, importe="37.00"
        )
        nuevo = copy.deepcopy(segunda["contenido"]["productos"][0])
        nuevo.update({
            "producto_central_id": str(nueva_identidad),
            "codigo": "NUEVO-RESTORE",
            "nombre": "Producto nuevo tras restore",
            "nombre_corto": "NUEVO",
            "orden": 3,
        })
        segunda["contenido"]["productos"].append(nuevo)
        segunda["conteos"]["productos"] = 3
        segunda["contenido_sha256"] = checksum_snapshot(segunda)

        punto = transaction.savepoint()
        with self.identidad_settings():
            aplicar_publicacion_catalogo(self.sucursal, segunda)
        mapping_pre_restore = IdentidadProductoCentral.objects.get(
            sucursal=self.sucursal, central_id=nueva_identidad
        ).producto_id
        ack_historico = EventoOutbox.objects.get(
            sucursal=self.sucursal,
            tipo="catalogo.aplicado",
            agregado_id=uuid.UUID(segunda["publicacion_id"]),
        )
        mapeos_historicos = copy.deepcopy(ack_historico.datos["mapeos_producto"])
        transaction.savepoint_rollback(punto)
        self.assertFalse(IdentidadProductoCentral.objects.filter(
            sucursal=self.sucursal, central_id=nueva_identidad
        ).exists())

        cliente_catalogo = SimpleNamespace(solicitar=Mock(return_value=SimpleNamespace(
            status=200, datos=segunda
        )))
        with self.central_v3_settings(), patch(
            "ventas.management.commands.sincronizar_catalogo_central.ClienteCentral",
            return_value=cliente_catalogo,
        ):
            call_command("sincronizar_catalogo_central", stdout=StringIO())
        mapping_reaplicado = IdentidadProductoCentral.objects.get(
            sucursal=self.sucursal, central_id=nueva_identidad
        ).producto_id
        self.assertEqual(mapping_pre_restore, mapping_reaplicado)
        self.assertEqual(mapping_reaplicado.version, 5)
        ack = EventoOutbox.objects.get(
            sucursal=self.sucursal,
            tipo="catalogo.aplicado",
            agregado_id=uuid.UUID(segunda["publicacion_id"]),
        )
        self.assertEqual(ack.datos["mapeos_producto"], mapeos_historicos)

        rutas = []
        class ClienteConfirmacion:
            def solicitar(self, **kwargs):
                rutas.append(kwargs["ruta"])
                return SimpleNamespace(
                    status=200,
                    headers={},
                    datos={
                        "recibido": True,
                        "acuse": str(uuid.uuid4()),
                        "ack_id": str(ack.id),
                        "estado_registrado": "aplicado",
                    },
                )

        with self.central_v3_settings():
            resultado = sincronizar_outbox_central(
                cliente_factory=lambda **_opciones: ClienteConfirmacion()
            )
        ack.refresh_from_db()
        self.assertEqual(resultado["entregados"], 1)
        self.assertEqual(ack.estado_entrega, EventoOutbox.EstadoEntrega.ENTREGADO)
        self.assertEqual(len(rutas), 1)
        self.assertIn("/api/v3/", rutas[0])
        self.assertTrue(estado_aprovisionamiento(self.sucursal)["listo"])

    def test_ack_v3_409_conserva_evento_en_conciliacion(self):
        from ventas.sincronizacion_central import sincronizar_outbox_central

        with self.identidad_settings():
            aplicar_publicacion_catalogo(self.sucursal, self.snapshot())
        ack = EventoOutbox.objects.get(tipo="catalogo.aplicado")
        hash_inicial = ack.payload_hash
        llamadas = []

        class ClienteConflicto:
            def solicitar(self, **kwargs):
                llamadas.append(kwargs)
                return SimpleNamespace(
                    status=409,
                    headers={},
                    datos={"codigo": "ack_state_conflict"},
                )

        with self.central_v3_settings():
            resultado = sincronizar_outbox_central(
                cliente_factory=lambda **_opciones: ClienteConflicto()
            )
            segundo = sincronizar_outbox_central(
                cliente_factory=lambda **_opciones: ClienteConflicto()
            )
        ack.refresh_from_db()
        self.assertEqual(resultado["conciliacion"], 1)
        self.assertEqual(segundo["conciliacion"], 0)
        self.assertEqual(len(llamadas), 1)
        self.assertIn("/api/v3/", llamadas[0]["ruta"])
        self.assertEqual(ack.estado_entrega, EventoOutbox.EstadoEntrega.CONCILIACION)
        self.assertEqual(ack.payload_hash, hash_inicial)
        self.assertEqual(ack.datos["ack_id"], str(ack.id))

    def test_colision_uuid_v3_de_categoria_o_producto_rechaza_sin_remapear(self):
        from catalogo.models import Categoria
        from ventas.catalogo_central import _id_local_v3

        with self.identidad_settings():
            primera, _ = aplicar_publicacion_catalogo(self.sucursal, self.snapshot())
        categoria_local = Producto.objects.get(
            sucursal=self.sucursal, codigo="VENDIBLE"
        ).categoria
        producto_central_nuevo = uuid.uuid4()
        segunda_producto = self.snapshot(
            version=2, anterior=primera.publicacion_id
        )
        producto_nuevo = copy.deepcopy(segunda_producto["contenido"]["productos"][0])
        producto_nuevo.update({
            "producto_central_id": str(producto_central_nuevo),
            "codigo": "NUEVO-COLISION",
            "nombre": "Nuevo producto",
            "nombre_corto": "NUEVO",
            "orden": 3,
        })
        segunda_producto["contenido"]["productos"].append(producto_nuevo)
        segunda_producto["conteos"]["productos"] = 3
        segunda_producto["contenido_sha256"] = checksum_snapshot(segunda_producto)
        Producto.objects.create(
            id=_id_local_v3(self.sucursal.id, "producto", producto_central_nuevo),
            sucursal=self.sucursal,
            categoria=categoria_local,
            codigo="COLISION-LOCAL",
            nombre="Colisión local",
            nombre_corto="COLISION",
        )
        with self.identidad_settings(), self.assertRaises(ErrorCatalogoCentral) as error:
            aplicar_publicacion_catalogo(self.sucursal, segunda_producto)
        self.assertEqual(error.exception.codigo, "conflicto_local")
        self.assertFalse(IdentidadProductoCentral.objects.filter(
            sucursal=self.sucursal, central_id=producto_central_nuevo
        ).exists())

        categoria_central_nueva = uuid.uuid4()
        segunda_categoria = self.snapshot(
            version=2, anterior=primera.publicacion_id
        )
        segunda_categoria["contenido"]["categorias"].append({
            "categoria_central_id": str(categoria_central_nueva),
            "codigo": "OTRA-CAT",
            "nombre": "Otra categoría",
            "orden": 2,
            "activa": True,
        })
        segunda_categoria["conteos"]["categorias"] = 2
        segunda_categoria["contenido_sha256"] = checksum_snapshot(segunda_categoria)
        Categoria.objects.create(
            id=_id_local_v3(self.sucursal.id, "categoria", categoria_central_nueva),
            sucursal=self.sucursal,
            nombre="Colisión de categoría",
        )
        with self.identidad_settings(), self.assertRaises(ErrorCatalogoCentral) as error:
            aplicar_publicacion_catalogo(self.sucursal, segunda_categoria)
        self.assertEqual(error.exception.codigo, "conflicto_local")
        self.assertEqual(
            list(PublicacionCatalogoCentral.objects.filter(
                sucursal=self.sucursal, version_contrato=3
            ).values_list("version", flat=True)),
            [1],
        )
        self.assertEqual(
            EventoOutbox.objects.filter(
                sucursal=self.sucursal, tipo="catalogo.aplicado"
            ).count(),
            1,
        )

    def test_altas_v2_conservan_uuid4_sin_migrar_identidad(self):
        datos = self.snapshot()
        datos["version_contrato"] = 2
        del datos["conteos"]["promociones"]
        del datos["contenido"]["promociones"]
        for producto in datos["contenido"]["productos"]:
            del producto["disponible_sucursal"]
        datos["contenido_sha256"] = checksum_snapshot(datos)
        with self.identidad_settings():
            aplicar_publicacion_catalogo(self.sucursal, datos)
        producto = IdentidadProductoCentral.objects.get(
            sucursal=self.sucursal, central_id=self.vendible_id
        ).producto
        self.assertEqual(producto.id.version, 4)
        self.assertEqual(producto.categoria_id.version, 4)

    def test_ack_v3_respuesta_perdida_reintenta_misma_identidad_y_acepta_repetido(self):
        """Un ACK recibido por Central con respuesta perdida conserva su idempotencia."""
        from datetime import timedelta

        from django.utils import timezone

        from ventas.central_api import ErrorTransporteCentral
        from ventas.sincronizacion_central import sincronizar_outbox_central

        with self.identidad_settings():
            aplicar_publicacion_catalogo(self.sucursal, self.snapshot())
        ack = EventoOutbox.objects.get(tipo="catalogo.aplicado")
        llamadas = []

        class ClienteConRespuestaPerdida:
            def solicitar(self, **kwargs):
                llamadas.append(kwargs)
                if len(llamadas) == 1:
                    raise ErrorTransporteCentral("Respuesta perdida después de recepción")
                return SimpleNamespace(
                    status=200,
                    headers={},
                    datos={
                        "recibido": True,
                        "acuse": str(uuid.uuid4()),
                        "ack_id": str(ack.id),
                        "estado_registrado": "aplicado",
                    },
                )

        cliente = ClienteConRespuestaPerdida()
        with override_settings(
            CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2=False,
            CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V3=True,
            CENTRAL_API_BASE_URL="https://central.example.invalid",
            CENTRAL_BRANCH_ID=str(self.sucursal.id),
            CENTRAL_BRANCH_CODE=self.sucursal.clave,
            CENTRAL_POS_INSTANCE_ID=str(self.config.instalacion_id),
            CENTRAL_CATALOG_TOKEN="T" * 40,
        ):
            primero = sincronizar_outbox_central(
                cliente_factory=lambda **_opciones: cliente
            )
            ack.refresh_from_db()
            self.assertEqual(primero["pendientes"], 1)
            self.assertEqual(
                ack.estado_entrega, EventoOutbox.EstadoEntrega.PENDIENTE
            )
            EventoOutbox.objects.filter(pk=ack.pk).update(
                proximo_intento_en=timezone.now() - timedelta(seconds=1)
            )
            segundo = sincronizar_outbox_central(
                cliente_factory=lambda **_opciones: cliente
            )
            tercero = sincronizar_outbox_central(
                cliente_factory=lambda **_opciones: cliente
            )

        self.assertEqual(segundo["entregados"], 1)
        self.assertEqual(tercero["entregados"], 0)
        self.assertEqual(len(llamadas), 2)
        self.assertEqual(llamadas[0]["idempotencia"], str(ack.id))
        self.assertEqual(llamadas[1]["idempotencia"], str(ack.id))
        self.assertEqual(llamadas[0]["payload"], llamadas[1]["payload"])
        self.assertEqual(llamadas[0]["ruta"], llamadas[1]["ruta"])
        self.assertIn("/api/v3/", llamadas[0]["ruta"])
        ack.refresh_from_db()
        self.assertEqual(ack.estado_entrega, EventoOutbox.EstadoEntrega.ENTREGADO)
        self.assertEqual(ack.intentos, 2)

class FixtureCatalogoV3Tests(TestCase):
    def test_fixture_sintetico_aplica_promocion_y_availability(self):
        ruta = (
            Path(__file__).resolve().parents[1]
            / "contracts/edge-central/fixtures/catalogo-publicacion-v3-lab01-promocion.json"
        )
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        sucursal = Sucursal.objects.create(
            id=uuid.UUID(datos["sucursal"]["id"]),
            clave=datos["sucursal"]["clave"],
            nombre="Laboratorio 01",
        )
        ConfiguracionSucursal.objects.create(
            sucursal=sucursal, clave_administrador=make_password("7391")
        )
        publicacion, creada = aplicar_publicacion_catalogo(sucursal, datos)
        self.assertTrue(creada)
        self.assertEqual(publicacion.version_contrato, 3)
        self.assertEqual(IdentidadProductoCentral.objects.filter(sucursal=sucursal).count(), 3)
        fuera = IdentidadProductoCentral.objects.get(
            sucursal=sucursal,
            central_id=uuid.UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee3"),
        ).producto
        self.assertFalse(fuera.activo)
        self.assertFalse(fuera.disponible_sucursal)
        promocion = DefinicionPromocion.objects.get(publicacion=publicacion)
        self.assertEqual(promocion.codigo, "LAB-PROMO")
        self.assertEqual(promocion.grupos.get().cantidad, 2)

    def test_fixture_v3_promocion_se_activa_tras_dos_publicaciones_v2(self):
        base = Path(__file__).resolve().parents[1] / "contracts/edge-central/fixtures"
        legado = json.loads(
            (base / "catalogo-publicacion-v2-lab01-global.json").read_text(encoding="utf-8")
        )
        inicial_v3 = json.loads(
            (base / "catalogo-publicacion-v3-lab01-promocion.json").read_text(encoding="utf-8")
        )
        sucursal = Sucursal.objects.create(
            id=uuid.UUID(legado["sucursal"]["id"]),
            clave=legado["sucursal"]["clave"],
            nombre="Laboratorio transición",
        )
        ConfiguracionSucursal.objects.create(
            sucursal=sucursal, clave_administrador=make_password("7391")
        )
        primera_v2, _ = aplicar_publicacion_catalogo(sucursal, legado)
        segunda = copy.deepcopy(legado)
        segunda["release_id"] = str(uuid.uuid4())
        segunda["publicacion_id"] = str(uuid.uuid4())
        segunda["publicacion_anterior_id"] = str(primera_v2.publicacion_id)
        segunda["version_sucursal"] = 2
        segunda_v2, _ = aplicar_publicacion_catalogo(sucursal, segunda)
        primera_v3, _ = aplicar_publicacion_catalogo(sucursal, inicial_v3)
        self.assertEqual(primera_v3.version, 1)
        self.assertEqual(_publicacion_activa(sucursal.id).pk, primera_v3.pk)
        principal = IdentidadProductoCentral.objects.get(
            sucursal=sucursal,
            central_id=uuid.UUID(inicial_v3["contenido"]["promociones"][0]["producto_central_id"]),
        ).producto
        from ventas.promociones import configuracion_promocion
        definicion = configuracion_promocion(principal)
        self.assertIsNotNone(definicion)
        self.assertEqual(definicion.publicacion_id, primera_v3.pk)
        self.assertEqual(definicion.origen, DefinicionPromocion.Origen.CENTRAL)
        self.assertEqual(
            PublicacionCatalogoCentral.objects.get(pk=segunda_v2.pk).version_contrato, 2
        )

    def test_retirar_promocion_exige_desactivar_su_producto_principal(self):
        ruta = (
            Path(__file__).resolve().parents[1]
            / "contracts/edge-central/fixtures/catalogo-publicacion-v3-lab01-promocion.json"
        )
        inicial = json.loads(ruta.read_text(encoding="utf-8"))
        sucursal = Sucursal.objects.create(
            id=uuid.UUID(inicial["sucursal"]["id"]),
            clave=inicial["sucursal"]["clave"],
            nombre="Laboratorio 01",
        )
        ConfiguracionSucursal.objects.create(
            sucursal=sucursal, clave_administrador=make_password("7391")
        )
        primera, _ = aplicar_publicacion_catalogo(sucursal, inicial)
        siguiente = copy.deepcopy(inicial)
        siguiente["release_id"] = str(uuid.uuid4())
        siguiente["publicacion_id"] = str(uuid.uuid4())
        siguiente["publicacion_anterior_id"] = str(primera.publicacion_id)
        siguiente["version_sucursal"] = 2
        siguiente["contenido"]["promociones"] = []
        siguiente["conteos"]["promociones"] = 0
        siguiente["contenido_sha256"] = checksum_snapshot(siguiente)
        with self.assertRaises(ErrorCatalogoCentral) as captura:
            aplicar_publicacion_catalogo(sucursal, siguiente)
        self.assertEqual(captura.exception.codigo, "schema_no_soportado")
        self.assertEqual(PublicacionCatalogoCentral.objects.filter(sucursal=sucursal).count(), 1)
        self.assertEqual(DefinicionPromocion.objects.filter(sucursal=sucursal).count(), 1)
        siguiente["contenido"]["productos"][0]["disponible_sucursal"] = False
        siguiente["contenido_sha256"] = checksum_snapshot(siguiente)
        segunda, _ = aplicar_publicacion_catalogo(sucursal, siguiente)
        self.assertEqual(DefinicionPromocion.objects.filter(sucursal=sucursal).count(), 1)
        principal = IdentidadProductoCentral.objects.get(
            sucursal=sucursal,
            central_id=uuid.UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee2"),
        ).producto
        self.assertFalse(principal.activo)
        self.assertEqual(segunda.version, 2)
