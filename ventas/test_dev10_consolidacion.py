"""Invariantes de idempotencia y conciliación de la consolidación mensual dev.10."""

import copy
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from personas.models import Sucursal

from .consolidacion import (
    ErrorConsolidacionHTTP,
    _BloquearRedirecciones,
    _enviar_vps,
    consolidar_periodo,
)
from .models import ConsolidacionMensual
from .services import ErrorVenta


class ConsolidacionMensualSeguraTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.sucursal = Sucursal.objects.create(
            clave="DEV10-CONSOLIDACION",
            nombre="Pruebas consolidación dev.10",
        )
        ultimo_mes = date(timezone.localdate().year, timezone.localdate().month, 1) - timedelta(days=1)
        self.periodo = date(ultimo_mes.year, ultimo_mes.month, 1)
        self.totales = {
            "periodo": self.periodo.isoformat(),
            "cortes": 1,
            "ventas": "123.45",
            "resultado_caja": "120.00",
            "ingresos": "0.00",
            "gastos": "3.45",
            "terminales": "20.00",
            "canales": {"comedor": "123.45"},
            "apps": {},
            "sucursales": {},
        }

    def test_transporte_bloquea_redirecciones_y_rechaza_cambio_de_origen(self):
        handler = _BloquearRedirecciones()
        self.assertIsNone(
            handler.redirect_request(
                None,
                None,
                302,
                "Found",
                {},
                "https://otro.example.test/captura",
            )
        )

        respuesta = MagicMock()
        respuesta.status = 200
        respuesta.getcode.return_value = 200
        respuesta.geturl.return_value = "https://otro.example.test/captura"
        respuesta.headers = {"Content-Type": "application/json"}
        respuesta.read.return_value = (
            b'{"recibido":true,"acuse":"acuse","estado":"recibido"}'
        )
        respuesta.__enter__.return_value = respuesta
        respuesta.__exit__.return_value = False
        with (
            override_settings(
                VPS_CONSOLIDACION_URL="https://central.example.test/api/v1/edge/consolidaciones-mensuales/",
                VPS_CONSOLIDACION_TOKEN="",
                VPS_CONSOLIDACION_TIMEOUT=1,
            ),
            patch("ventas.consolidacion.urlopen", return_value=respuesta),
        ):
            with self.assertRaisesMessage(ErrorVenta, "cambiar el origen"):
                _enviar_vps({"idempotencia": "11111111-1111-4111-8111-111111111111"})
    def test_reintento_reutiliza_payload_hash_e_idempotencia_y_red_va_fuera_de_atomic(self):
        enviados = []
        estados_atomic = []

        def enviar(payload):
            estados_atomic.append(connection.in_atomic_block)
            enviados.append(copy.deepcopy(payload))
            if len(enviados) == 1:
                raise ErrorVenta("VPS temporalmente fuera de línea")
            return "acuse-reintento", "recibido"

        with (
            patch("ventas.consolidacion.construir_totales", return_value=self.totales),
            patch("ventas.consolidacion._enviar_vps", side_effect=enviar),
            patch("ventas.consolidacion._purgar_periodo") as purgar,
        ):
            with self.assertRaisesMessage(ErrorVenta, "fuera de línea"):
                consolidar_periodo(self.sucursal, self.periodo)
            primer_estado = ConsolidacionMensual.objects.get(
                sucursal=self.sucursal,
                periodo=self.periodo,
            )
            payload_guardado = copy.deepcopy(primer_estado.payload_inmutable)
            hash_guardado = primer_estado.payload_hash
            idempotencia_guardada = primer_estado.idempotencia

            resultado = consolidar_periodo(self.sucursal, self.periodo)

        self.assertEqual(estados_atomic, [False, False])
        self.assertEqual(enviados[0], enviados[1])
        self.assertEqual(enviados[0], payload_guardado)
        self.assertEqual(resultado.payload_inmutable, payload_guardado)
        self.assertEqual(resultado.payload_hash, hash_guardado)
        self.assertEqual(resultado.idempotencia, idempotencia_guardada)
        self.assertEqual(resultado.intentos, 2)
        self.assertEqual(resultado.acuse_vps, "acuse-reintento")
        self.assertEqual(resultado.estado_vps, "recibido")
        purgar.assert_called_once()

    def test_conflicto_409_entra_en_conciliacion_y_no_regenera_ni_reenvia(self):
        with (
            patch("ventas.consolidacion.construir_totales", return_value=self.totales),
            patch(
                "ventas.consolidacion._enviar_vps",
                side_effect=ErrorConsolidacionHTTP("Conflicto remoto", status=409),
            ) as enviar,
        ):
            with self.assertRaisesMessage(ErrorVenta, "Conflicto remoto"):
                consolidar_periodo(self.sucursal, self.periodo)

        conciliacion = ConsolidacionMensual.objects.get(
            sucursal=self.sucursal,
            periodo=self.periodo,
        )
        self.assertEqual(
            conciliacion.estado,
            ConsolidacionMensual.Estado.CONCILIACION,
        )
        idempotencia_guardada = conciliacion.idempotencia
        payload_guardado = copy.deepcopy(conciliacion.payload_inmutable)
        enviar.assert_called_once()

        with patch("ventas.consolidacion._enviar_vps") as segundo_envio:
            with self.assertRaisesMessage(ErrorVenta, "requiere conciliacion"):
                consolidar_periodo(self.sucursal, self.periodo)

        conciliacion.refresh_from_db()
        self.assertEqual(conciliacion.idempotencia, idempotencia_guardada)
        self.assertEqual(conciliacion.payload_inmutable, payload_guardado)
        segundo_envio.assert_not_called()

    def test_estado_purgado_del_vps_se_conserva_en_el_registro_local(self):
        with (
            patch("ventas.consolidacion.construir_totales", return_value=self.totales),
            patch(
                "ventas.consolidacion._enviar_vps",
                return_value=("acuse-purgado", "purgado"),
            ),
            patch("ventas.consolidacion._purgar_periodo") as purgar,
        ):
            resultado = consolidar_periodo(self.sucursal, self.periodo)

        self.assertEqual(resultado.estado, ConsolidacionMensual.Estado.CONFIRMADA)
        self.assertEqual(resultado.acuse_vps, "acuse-purgado")
        self.assertEqual(resultado.estado_vps, "purgado")
        purgar.assert_called_once()
