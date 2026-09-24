import uuid
from datetime import timedelta
from types import SimpleNamespace

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.utils import timezone

from personas.models import Sucursal
from ventas.models import ConfiguracionSucursal, EventoOutbox
from ventas.sincronizacion_central import (
    _actualizar_evento,
    _reclamar_evento,
    hash_payload,
    sincronizar_outbox_central,
)


class OutboxCentralConcurrenciaTests(TestCase):
    def setUp(self):
        self.sucursal = Sucursal.objects.create(
            clave="DEV10-CAS",
            nombre="Pruebas CAS outbox",
        )

    def _evento(self):
        return EventoOutbox.objects.create(
            sucursal=self.sucursal,
            agregado="venta",
            agregado_id=uuid.uuid4(),
            tipo="ticket.pagado",
            datos={"fixture": True},
            destino=EventoOutbox.Destino.CENTRAL_VENTAS,
            estado_entrega=EventoOutbox.EstadoEntrega.PENDIENTE,
            payload_hash="a" * 64,
        )

    def test_entregado_es_monotono_y_un_fallo_tardio_no_borra_el_ack(self):
        evento = self._evento()
        reclamo = _reclamar_evento(
            evento.id,
            intentos_esperados=0,
            duracion=timedelta(seconds=30),
        )
        self.assertIsNotNone(reclamo)

        aplicado = _actualizar_evento(
            evento.id,
            intento=reclamo.intentos,
            estado=EventoOutbox.EstadoEntrega.ENTREGADO,
            http=201,
            acuse="11111111-1111-4111-8111-111111111111",
            remoto="recibido",
        )
        self.assertTrue(aplicado)
        evento.refresh_from_db()
        publicado_en = evento.publicado_en

        fallo_tardio = _actualizar_evento(
            evento.id,
            intento=reclamo.intentos,
            estado=EventoOutbox.EstadoEntrega.PENDIENTE,
            http=503,
            error="Respuesta tardia del segundo worker",
            demora=timedelta(minutes=2),
        )
        self.assertFalse(fallo_tardio)

        evento.refresh_from_db()
        self.assertEqual(
            evento.estado_entrega,
            EventoOutbox.EstadoEntrega.ENTREGADO,
        )
        self.assertEqual(evento.ultima_respuesta_http, 201)
        self.assertEqual(
            evento.acuse_remoto,
            "11111111-1111-4111-8111-111111111111",
        )
        self.assertEqual(evento.estado_remoto, "recibido")
        self.assertEqual(evento.ultimo_error, "")
        self.assertEqual(evento.publicado_en, publicado_en)
        self.assertIsNone(evento.proximo_intento_en)

    def test_reclamo_es_exclusivo_y_se_recupera_al_vencer(self):
        evento = self._evento()
        inicio = timezone.now()
        duracion = timedelta(seconds=30)
        primero = _reclamar_evento(
            evento.id,
            intentos_esperados=0,
            ahora=inicio,
            duracion=duracion,
        )
        self.assertIsNotNone(primero)
        self.assertEqual(primero.intentos, 1)

        competidor = _reclamar_evento(
            evento.id,
            intentos_esperados=1,
            ahora=inicio + timedelta(seconds=29),
            duracion=duracion,
        )
        self.assertIsNone(competidor)

        recuperado = _reclamar_evento(
            evento.id,
            intentos_esperados=1,
            ahora=inicio + timedelta(seconds=31),
            duracion=duracion,
        )
        self.assertIsNotNone(recuperado)
        self.assertEqual(recuperado.intentos, 2)

        resultado_viejo = _actualizar_evento(
            evento.id,
            intento=primero.intentos,
            estado=EventoOutbox.EstadoEntrega.ENTREGADO,
            http=201,
            acuse="22222222-2222-4222-8222-222222222222",
            remoto="recibido",
        )
        self.assertFalse(resultado_viejo)
        resultado_actual = _actualizar_evento(
            evento.id,
            intento=recuperado.intentos,
            estado=EventoOutbox.EstadoEntrega.PENDIENTE,
            error="Fallo del intento recuperado",
            demora=timedelta(minutes=1),
        )
        self.assertTrue(resultado_actual)

        evento.refresh_from_db()
        self.assertEqual(evento.intentos, 2)
        self.assertEqual(
            evento.estado_entrega,
            EventoOutbox.EstadoEntrega.PENDIENTE,
        )
        self.assertEqual(evento.acuse_remoto, "")
        self.assertEqual(evento.estado_remoto, "")
        self.assertEqual(evento.ultimo_error, "Fallo del intento recuperado")
        self.assertGreater(evento.proximo_intento_en, timezone.now())

    def test_reentrada_del_sincronizador_no_duplica_la_solicitud(self):
        configuracion = ConfiguracionSucursal.objects.create(
            sucursal=self.sucursal,
            clave_administrador=make_password("3141"),
        )
        evento_id = uuid.uuid4()
        eventos = [{"evento_id": str(uuid.uuid4())}]
        payload = {
            "version_contrato": 2,
            "lote_id": str(evento_id),
            "sucursal": {
                "id": str(self.sucursal.id),
                "clave": self.sucursal.clave,
            },
            "creado_en": timezone.now().isoformat(),
            "conteo": 1,
            "eventos": eventos,
            "contenido_sha256": hash_payload(eventos),
        }
        evento = EventoOutbox.objects.create(
            id=evento_id,
            sucursal=self.sucursal,
            agregado="venta",
            agregado_id=uuid.uuid4(),
            tipo="ticket.pagado",
            datos=payload,
            destino=EventoOutbox.Destino.CENTRAL_VENTAS,
            estado_entrega=EventoOutbox.EstadoEntrega.PENDIENTE,
            version_contrato=2,
            payload_hash=hash_payload(payload),
        )
        llamadas = []
        resultados_reentrantes = []

        class ClienteReentrante:
            def solicitar(cliente, **kwargs):
                llamadas.append(kwargs)
                resultados_reentrantes.append(
                    sincronizar_outbox_central(
                        limite=1,
                        cliente_factory=lambda **_opciones: cliente,
                    )
                )
                return SimpleNamespace(
                    status=201,
                    headers={},
                    datos={
                        "recibido": True,
                        "acuse": "33333333-3333-4333-8333-333333333333",
                        "lote_id": str(evento.id),
                        "estado": "recibido",
                        "contenido_sha256": payload["contenido_sha256"],
                        "conteo_aceptado": 1,
                        "primera_recepcion_en": timezone.now().isoformat(),
                    },
                )

        cliente = ClienteReentrante()
        with self.settings(
            CENTRAL_ENABLE_SALES_V2=True,
            CENTRAL_ENABLE_CUSTOMERS_V2=False,
            CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2=False,
            CENTRAL_API_BASE_URL="https://central.example.test",
            CENTRAL_INGEST_TOKEN="i" * 32,
            CENTRAL_CATALOG_TOKEN="c" * 32,
            CENTRAL_API_CA_BUNDLE="",
            CENTRAL_CONNECT_TIMEOUT_SECONDS=1,
            CENTRAL_READ_TIMEOUT_SECONDS=1,
            CENTRAL_MAX_RESPONSE_BYTES=65536,
            CENTRAL_BRANCH_ID=str(self.sucursal.id),
            CENTRAL_BRANCH_CODE=self.sucursal.clave,
            CENTRAL_POS_INSTANCE_ID=str(configuracion.instalacion_id),
        ):
            resultado = sincronizar_outbox_central(
                limite=1,
                cliente_factory=lambda **_opciones: cliente,
            )

        self.assertEqual(len(llamadas), 1)
        self.assertEqual(resultados_reentrantes[0]["entregados"], 0)
        self.assertEqual(resultado["entregados"], 1)
        evento.refresh_from_db()
        self.assertEqual(
            evento.estado_entrega,
            EventoOutbox.EstadoEntrega.ENTREGADO,
        )
        self.assertEqual(evento.intentos, 1)
        self.assertEqual(
            evento.acuse_remoto,
            "33333333-3333-4333-8333-333333333333",
        )
