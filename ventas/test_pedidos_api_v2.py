import copy
import json
import socket
import ssl
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

from ventas.pedidos_api_v2 import (
    ClientePedidosV2,
    ErrorAlcancePedidos,
    ErrorAutenticacionPedidos,
    ErrorBrechaRetencion,
    ErrorConfiguracionPedidos,
    ErrorContratoPedidos,
    ErrorEndpointPedidos,
    ErrorLimitePedidos,
    ErrorTLSPedidos,
    ErrorTamanoRespuestaPedidos,
    ErrorTemporalPedidos,
    ErrorTransportePedidos,
    EstadoSincronizacionPedidos,
    RespuestaHTTP,
    _leer_cuerpo_limitado,
    parsear_pagina_v2,
)


URL = "https://pedidos.example.invalid/api/v2/pos/pedidos/"
TOKEN = "token-pedidos-prueba-" + ("x" * 48)
DESDE = datetime(2026, 9, 22, 14, tzinfo=timezone.utc)
HASTA = DESDE + timedelta(hours=1)


def pedido_payload(
    numero=1,
    *,
    sucursal_id=3,
    fecha="2026-09-22T14:30:00.123456Z",
):
    return {
        "id": 12000 + numero,
        "codigo_publico": f"00000000-0000-4000-8000-{numero:012d}",
        "fecha_confirmacion": fecha,
        "total": "245.50",
        "sucursal": {
            "id": sucursal_id,
            "nombre": "Sucursal de prueba",
            "tipo": "sucursal",
        },
        "items": [
            {
                "id": 45000 + numero,
                "pedido_id": 12000 + numero,
                "producto": {
                    "id": 8,
                    "nombre": "Producto de prueba",
                    "nombre_ticket": "PROD PRUEBA",
                    "unidad_medida": "PIEZA (PZA)",
                    "unidad_abreviatura": "PZA",
                    "cantidad_por_precio": "1.000",
                },
                "cantidad": "2.000",
                "precio_unitario": "122.75",
                "subtotal": "245.50",
            }
        ],
    }


def pagina_payload(
    pedidos=None,
    *,
    request_id="pos-prueba-001",
    limite=100,
    has_more=False,
    next_cursor=None,
):
    pedidos = [pedido_payload()] if pedidos is None else pedidos
    return {
        "version": "v2",
        "request_id": request_id,
        "data": pedidos,
        "page": {
            "has_more": has_more,
            "limit": limite,
            "next_cursor": next_cursor,
            "returned": len(pedidos),
        },
    }


def error_payload(code, request_id="pos-prueba-001"):
    return {
        "error": {
            "code": code,
            "message": "Mensaje remoto que no debe copiarse a excepciones.",
            "request_id": request_id,
        }
    }


def respuesta(
    status,
    payload,
    *,
    request_id="pos-prueba-001",
    headers=None,
    url_final=URL,
):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    cabeceras = {
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
        "X-Request-ID": request_id,
    }
    cabeceras.update(headers or {})
    return RespuestaHTTP(status, cabeceras, body, url_final)


class TransporteSecuencial:
    def __init__(self, *resultados):
        self.resultados = list(resultados)
        self.solicitudes = []

    def __call__(self, solicitud, *, timeout, max_response_bytes):
        self.solicitudes.append((solicitud, timeout, max_response_bytes))
        if not self.resultados:
            raise AssertionError("El cliente hizo una solicitud adicional.")
        resultado = self.resultados.pop(0)
        if isinstance(resultado, BaseException):
            raise resultado
        return resultado


def cliente(transporte, **opciones):
    return ClientePedidosV2(
        url=URL,
        token=TOKEN,
        sucursal_ids=opciones.pop("sucursal_ids", [3]),
        transporte=transporte,
        dormir=opciones.pop("dormir", lambda _: None),
        **opciones,
    )

def cabecera_enviada(solicitud, nombre):
    return {clave.lower(): valor for clave, valor in solicitud.header_items()}.get(nombre.lower())


class LecturaLimitadaTests(unittest.TestCase):
    class Flujo:
        def __init__(self, contenido, content_length=None):
            self.contenido = contenido
            self.lecturas = []
            self.headers = {}
            if content_length is not None:
                self.headers["Content-Length"] = content_length

        def read(self, cantidad):
            self.lecturas.append(cantidad)
            return self.contenido[:cantidad]

    def test_content_length_excesivo_se_rechaza_antes_de_leer(self):
        flujo = self.Flujo(b"{}", "999")
        with self.assertRaises(ErrorTamanoRespuestaPedidos):
            _leer_cuerpo_limitado(flujo, 10)
        self.assertEqual(flujo.lecturas, [])

    def test_lectura_real_se_limita_a_maximo_mas_un_byte(self):
        flujo = self.Flujo(b"x" * 20)
        with self.assertRaises(ErrorTamanoRespuestaPedidos):
            _leer_cuerpo_limitado(flujo, 10)
        self.assertEqual(flujo.lecturas, [11])


class ClientePedidosV2Tests(unittest.TestCase):
    def test_pagina_valida_con_identidad_doble_y_query_explicito(self):
        transporte = TransporteSecuencial(
            respuesta(200, pagina_payload(limite=2, request_id="req-uno"), request_id="req-uno")
        )
        api = cliente(transporte, sucursal_ids=[3, 1, 3])

        pagina = api.listar_pagina(
            desde=DESDE,
            hasta=HASTA,
            limite=2,
            cursor="cursor-opaco",
            request_id="req-uno",
        )

        self.assertEqual(pagina.returned, 1)
        self.assertEqual(pagina.pedidos[0].total, Decimal("245.50"))
        self.assertEqual(
            pagina.pedidos[0].identidad_remota,
            (12001, uuid.UUID("00000000-0000-4000-8000-000000000001")),
        )
        solicitud = transporte.solicitudes[0][0]
        query = parse_qs(urlsplit(solicitud.full_url).query)
        self.assertEqual(query["sucursal_id"], ["1,3"])
        self.assertEqual(query["cursor"], ["cursor-opaco"])
        self.assertEqual(solicitud.get_header("Authorization"), f"Bearer {TOKEN}")
        self.assertIsNone(cabecera_enviada(solicitud, "X-POS-Edge-ID"))
        self.assertIsNone(cabecera_enviada(solicitud, "X-POS-Branch-ID"))
        self.assertNotIn(TOKEN, repr(api))

    def test_credencial_agregada_envia_identidades_explicitamente(self):
        edge_id = "11111111-1111-4111-8111-111111111111"
        branch_id = "22222222-2222-4222-8222-222222222222"
        transporte = TransporteSecuencial(
            respuesta(200, pagina_payload(limite=2, request_id="req-agg"), request_id="req-agg")
        )
        api = cliente(
            transporte, edge_id=edge_id, pos_branch_id=branch_id,
            sucursal_ids=[3],
        )

        api.listar_pagina(desde=DESDE, hasta=HASTA, limite=2, request_id="req-agg")

        solicitud = transporte.solicitudes[0][0]
        self.assertEqual(cabecera_enviada(solicitud, "X-POS-Edge-ID"), edge_id)
        self.assertEqual(cabecera_enviada(solicitud, "X-POS-Branch-ID"), branch_id)
        self.assertEqual(solicitud.get_header("Authorization"), f"Bearer {TOKEN}")
        self.assertNotIn(TOKEN, repr(api))

    def test_credencial_agregada_rechaza_identidades_incompletas_o_no_canonicas(self):
        valido = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        casos = [
            {"edge_id": valido},
            {"pos_branch_id": valido},
            {"edge_id": valido.upper(), "pos_branch_id": valido},
            {"edge_id": valido, "pos_branch_id": "00000000-0000-0000-0000-000000000000"},
            {"edge_id": "no-uuid", "pos_branch_id": valido},
        ]
        for caso in casos:
            with self.subTest(caso=caso), self.assertRaises(ErrorConfiguracionPedidos):
                cliente(TransporteSecuencial(), **caso)

    def test_configuracion_rechaza_http_userinfo_query_token_y_alcance_ambiguo(self):
        casos = [
            {"url": "http://pedidos.invalid/api", "token": TOKEN, "sucursal_ids": [1]},
            {
                "url": "https://usuario:secreto@pedidos.invalid/api",
                "token": TOKEN,
                "sucursal_ids": [1],
            },
            {"url": URL + "?x=1", "token": TOKEN, "sucursal_ids": [1]},
            {"url": URL, "token": "corto", "sucursal_ids": [1]},
            {"url": URL, "token": TOKEN, "sucursal_ids": []},
        ]
        for caso in casos:
            with self.subTest(caso=caso), self.assertRaises(ErrorConfiguracionPedidos):
                ClientePedidosV2(**caso, transporte=TransporteSecuencial())

    def test_contexto_tls_siempre_verifica_certificado_y_hostname(self):
        api = cliente(TransporteSecuencial())
        self.assertEqual(api._contexto_ssl.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(api._contexto_ssl.check_hostname)
        with self.assertRaises(ErrorConfiguracionPedidos):
            ClientePedidosV2(
                url=URL,
                token=TOKEN,
                sucursal_ids=[3],
                ca_bundle="ruta-que-no-existe.pem",
                transporte=TransporteSecuencial(),
            )

    def test_retention_gap_es_estado_explicito_sin_reintento(self):
        transporte = TransporteSecuencial(
            respuesta(410, error_payload("retention_gap"), request_id="gap-001")
        )
        api = cliente(transporte, max_reintentos=3)

        with self.assertRaises(ErrorBrechaRetencion) as captura:
            api.listar_pagina(desde=DESDE, hasta=HASTA, cursor="cursor-conservado")

        self.assertEqual(
            captura.exception.estado,
            EstadoSincronizacionPedidos.RECONCILIACION_REQUERIDA,
        )
        self.assertEqual(captura.exception.code, "retention_gap")
        self.assertEqual(len(transporte.solicitudes), 1)
        self.assertNotIn(TOKEN, str(captura.exception))
        self.assertNotIn("Mensaje remoto", str(captura.exception))

    def test_410_con_codigo_desconocido_no_se_disfraza_de_brecha(self):
        api = cliente(
            TransporteSecuencial(respuesta(410, error_payload("otro_error")))
        )
        with self.assertRaises(ErrorContratoPedidos):
            api.listar_pagina(desde=DESDE, hasta=HASTA)

    def test_errores_permanentes_tienen_excepcion_tipificada_y_no_reintentan(self):
        casos = [
            (401, "unauthorized", ErrorAutenticacionPedidos),
            (403, "forbidden", ErrorAlcancePedidos),
            (404, "not_found", ErrorEndpointPedidos),
            (426, "https_required", ErrorTLSPedidos),
        ]
        for status, code, excepcion in casos:
            with self.subTest(status=status):
                transporte = TransporteSecuencial(respuesta(status, error_payload(code)))
                api = cliente(transporte, max_reintentos=4)
                with self.assertRaises(excepcion):
                    api.listar_pagina(desde=DESDE, hasta=HASTA)
                self.assertEqual(len(transporte.solicitudes), 1)

    def test_429_respeta_retry_after_acotado_y_reusa_request_id(self):
        pausas = []
        transporte = TransporteSecuencial(
            respuesta(
                429,
                error_payload("rate_limited", "req-rate"),
                request_id="req-rate",
                headers={"Retry-After": "9999"},
            ),
            respuesta(200, pagina_payload(request_id="req-rate"), request_id="req-rate"),
        )
        api = cliente(
            transporte,
            edge_id="11111111-1111-4111-8111-111111111111",
            pos_branch_id="22222222-2222-4222-8222-222222222222",
            max_reintentos=1,
            max_retry_after=7,
            dormir=pausas.append,
        )

        pagina = api.listar_pagina(
            desde=DESDE, hasta=HASTA, request_id="req-rate"
        )

        self.assertEqual(pagina.returned, 1)
        self.assertEqual(pausas, [7.0])
        self.assertEqual(
            [solicitud.get_header("X-request-id") for solicitud, _, _ in transporte.solicitudes],
            ["req-rate", "req-rate"],
        )
        self.assertEqual(
            [cabecera_enviada(solicitud, "X-POS-Edge-ID") for solicitud, _, _ in transporte.solicitudes],
            [api.edge_id, api.edge_id],
        )
        self.assertEqual(
            [cabecera_enviada(solicitud, "X-POS-Branch-ID") for solicitud, _, _ in transporte.solicitudes],
            [api.pos_branch_id, api.pos_branch_id],
        )

    def test_429_agotado_conserva_retry_after_tipado(self):
        api = cliente(
            TransporteSecuencial(
                respuesta(
                    429,
                    error_payload("rate_limited"),
                    headers={"Retry-After": "15"},
                )
            ),
            max_reintentos=0,
        )
        with self.assertRaises(ErrorLimitePedidos) as captura:
            api.listar_pagina(desde=DESDE, hasta=HASTA)
        self.assertEqual(captura.exception.retry_after, 15.0)

    def test_retry_after_invalido_usa_backoff_inyectado(self):
        pausas = []
        transporte = TransporteSecuencial(
            respuesta(
                429,
                error_payload("rate_limited", "req-backoff"),
                request_id="req-backoff",
                headers={"Retry-After": "fecha-invalida"},
            ),
            respuesta(
                200,
                pagina_payload(request_id="req-backoff"),
                request_id="req-backoff",
            ),
        )
        api = cliente(
            transporte,
            max_reintentos=1,
            dormir=pausas.append,
            backoff=lambda _: 3.25,
        )
        api.listar_pagina(desde=DESDE, hasta=HASTA, request_id="req-backoff")
        self.assertEqual(pausas, [3.25])

    def test_solo_estados_transitorios_se_reintentan(self):
        pausas = []
        temporal = TransporteSecuencial(
            respuesta(503, error_payload("service_unavailable", "req-temp"), request_id="req-temp"),
            respuesta(200, pagina_payload(request_id="req-temp"), request_id="req-temp"),
        )
        cliente(
            temporal,
            max_reintentos=1,
            dormir=pausas.append,
            backoff=lambda _: 0.5,
        ).listar_pagina(desde=DESDE, hasta=HASTA, request_id="req-temp")
        self.assertEqual(len(temporal.solicitudes), 2)
        self.assertEqual(pausas, [0.5])

        permanente = TransporteSecuencial(respuesta(400, error_payload("invalid_parameter")))
        with self.assertRaises(ErrorContratoPedidos):
            cliente(permanente, max_reintentos=3).listar_pagina(
                desde=DESDE, hasta=HASTA
            )
        self.assertEqual(len(permanente.solicitudes), 1)

    def test_error_temporal_agotado_es_tipado(self):
        api = cliente(
            TransporteSecuencial(respuesta(502, {"error": "proxy"})),
            max_reintentos=0,
        )
        with self.assertRaises(ErrorTemporalPedidos):
            api.listar_pagina(desde=DESDE, hasta=HASTA)

    def test_tls_no_se_reintenta_y_red_no_tls_si(self):
        tls = TransporteSecuencial(ssl.SSLCertVerificationError("certificado"))
        with self.assertRaises(ErrorTLSPedidos):
            cliente(tls, max_reintentos=3).listar_pagina(desde=DESDE, hasta=HASTA)
        self.assertEqual(len(tls.solicitudes), 1)

        pausas = []
        red = TransporteSecuencial(
            URLError(socket.gaierror("dns")),
            respuesta(200, pagina_payload(request_id="req-red"), request_id="req-red"),
        )
        cliente(
            red, max_reintentos=1, dormir=pausas.append, backoff=lambda _: 1
        ).listar_pagina(desde=DESDE, hasta=HASTA, request_id="req-red")
        self.assertEqual(len(red.solicitudes), 2)
        self.assertEqual(pausas, [1.0])

    def test_fallo_de_red_agotado_no_expone_detalle(self):
        api = cliente(
            TransporteSecuencial(URLError("host-interno-sensible")),
            max_reintentos=0,
        )
        with self.assertRaises(ErrorTransportePedidos) as captura:
            api.listar_pagina(desde=DESDE, hasta=HASTA)
        self.assertNotIn("host-interno-sensible", str(captura.exception))

    def test_rechaza_content_length_y_cuerpo_sobre_limite(self):
        maximo = 1024
        cuerpo = b"{}"
        declarado = RespuestaHTTP(
            200,
            {
                "Content-Type": "application/json",
                "Content-Length": str(maximo + 1),
                "X-Request-ID": "req-size",
            },
            cuerpo,
            URL,
        )
        with self.assertRaises(ErrorTamanoRespuestaPedidos):
            cliente(TransporteSecuencial(declarado), max_response_bytes=maximo).listar_pagina(
                desde=DESDE, hasta=HASTA
            )

        real = RespuestaHTTP(
            200,
            {"Content-Type": "application/json", "X-Request-ID": "req-size"},
            b"x" * (maximo + 1),
            URL,
        )
        with self.assertRaises(ErrorTamanoRespuestaPedidos):
            cliente(TransporteSecuencial(real), max_response_bytes=maximo).listar_pagina(
                desde=DESDE, hasta=HASTA
            )

    def test_rechaza_origen_final_distinto_y_redireccion(self):
        cruzado = respuesta(
            200,
            pagina_payload(),
            url_final="https://otro.example.invalid/api/v2/pos/pedidos/",
        )
        with self.assertRaises(ErrorContratoPedidos):
            cliente(TransporteSecuencial(cruzado)).listar_pagina(
                desde=DESDE, hasta=HASTA
            )

        redireccion = RespuestaHTTP(
            302,
            {"Location": "https://otro.example.invalid/", "X-Request-ID": "req-redir"},
            b"",
            URL,
        )
        transporte = TransporteSecuencial(redireccion)
        with self.assertRaises(ErrorContratoPedidos):
            cliente(transporte, max_reintentos=3).listar_pagina(desde=DESDE, hasta=HASTA)
        self.assertEqual(len(transporte.solicitudes), 1)

    def test_rechaza_content_type_request_id_y_version_desconocidos(self):
        casos = []
        payload_version = pagina_payload()
        payload_version["version"] = "v3"
        casos.append(respuesta(200, payload_version))
        casos.append(
            respuesta(
                200,
                pagina_payload(),
                headers={"Content-Type": "text/html"},
            )
        )
        casos.append(
            respuesta(200, pagina_payload(request_id="uno"), request_id="otro")
        )
        for caso in casos:
            with self.subTest(caso=caso), self.assertRaises(ErrorContratoPedidos):
                cliente(TransporteSecuencial(caso)).listar_pagina(
                    desde=DESDE, hasta=HASTA
                )

    def test_rechaza_esquema_extra_json_duplicado_y_decimal_no_textual(self):
        extra = pagina_payload()
        extra["campo_nuevo"] = True
        with self.assertRaises(ErrorContratoPedidos):
            cliente(TransporteSecuencial(respuesta(200, extra))).listar_pagina(
                desde=DESDE, hasta=HASTA
            )

        body_duplicado = (
            b'{"version":"v2","version":"v2","request_id":"dup",'
            b'"data":[],"page":{"has_more":false,"limit":100,'
            b'"next_cursor":null,"returned":0}}'
        )
        duplicado = RespuestaHTTP(
            200,
            {
                "Content-Type": "application/json",
                "Content-Length": str(len(body_duplicado)),
                "X-Request-ID": "dup",
            },
            body_duplicado,
            URL,
        )
        with self.assertRaises(ErrorContratoPedidos):
            cliente(TransporteSecuencial(duplicado)).listar_pagina(
                desde=DESDE, hasta=HASTA
            )

        decimal_numero = pagina_payload()
        decimal_numero["data"][0]["total"] = 245.5
        with self.assertRaises(ErrorContratoPedidos):
            cliente(TransporteSecuencial(respuesta(200, decimal_numero))).listar_pagina(
                desde=DESDE, hasta=HASTA
            )

    def test_rechaza_uuid_item_sucursal_y_orden_inconsistentes(self):
        casos = []
        uuid_invalido = pagina_payload()
        uuid_invalido["data"][0]["codigo_publico"] = "no-uuid"
        casos.append(uuid_invalido)

        item_ajeno = pagina_payload()
        item_ajeno["data"][0]["items"][0]["pedido_id"] = 999
        casos.append(item_ajeno)

        sucursal_ajena = pagina_payload()
        sucursal_ajena["data"][0]["sucursal"]["id"] = 4
        casos.append(sucursal_ajena)

        fuera_orden = pagina_payload(
            [
                pedido_payload(2, fecha="2026-09-22T14:40:00.000000Z"),
                pedido_payload(1, fecha="2026-09-22T14:30:00.000000Z"),
            ]
        )
        casos.append(fuera_orden)
        for payload in casos:
            with self.subTest(payload=payload), self.assertRaises(ErrorContratoPedidos):
                cliente(TransporteSecuencial(respuesta(200, payload))).listar_pagina(
                    desde=DESDE, hasta=HASTA
                )

    def test_rechaza_paginacion_inconsistente(self):
        casos = [
            pagina_payload(has_more=True, next_cursor=None),
            pagina_payload(has_more=False, next_cursor="sobrante"),
            pagina_payload(has_more=True, next_cursor="cursor", pedidos=[]),
        ]
        casos[0]["page"]["returned"] = 1
        for payload in casos:
            with self.subTest(payload=payload), self.assertRaises(ErrorContratoPedidos):
                cliente(TransporteSecuencial(respuesta(200, payload))).listar_pagina(
                    desde=DESDE, hasta=HASTA
                )

    def test_valida_ventana_limite_cursor_y_request_id_antes_de_red(self):
        casos = [
            {"desde": DESDE, "hasta": DESDE},
            {"desde": DESDE, "hasta": DESDE + timedelta(days=32)},
            {"desde": DESDE, "hasta": HASTA, "limite": 501},
            {"desde": DESDE, "hasta": HASTA, "cursor": "x" * 2049},
            {"desde": DESDE, "hasta": HASTA, "request_id": "id con espacios"},
        ]
        for parametros in casos:
            transporte = TransporteSecuencial()
            with self.subTest(parametros=parametros), self.assertRaises(ErrorConfiguracionPedidos):
                cliente(transporte).listar_pagina(**parametros)
            self.assertEqual(transporte.solicitudes, [])

    def test_callback_recibe_checkpoint_y_se_ejecuta_antes_de_siguiente_pagina(self):
        eventos = []
        primera = respuesta(
            200,
            pagina_payload(
                [pedido_payload(1)],
                request_id="pag-1",
                limite=1,
                has_more=True,
                next_cursor="cursor-1",
            ),
            request_id="pag-1",
        )
        segunda = respuesta(
            200,
            pagina_payload(
                [
                    pedido_payload(
                        2, fecha="2026-09-22T14:31:00.000000Z"
                    )
                ],
                request_id="pag-2",
                limite=1,
            ),
            request_id="pag-2",
        )

        class TransporteOrdenado(TransporteSecuencial):
            def __call__(self, *args, **kwargs):
                eventos.append(f"solicitud-{len(self.solicitudes) + 1}")
                return super().__call__(*args, **kwargs)

        transporte = TransporteOrdenado(primera, segunda)

        def aplicar(pagina, checkpoint):
            eventos.append(f"aplicar-{checkpoint.numero_pagina}")
            if checkpoint.numero_pagina == 1:
                self.assertEqual(checkpoint.cursor_siguiente, "cursor-1")
                self.assertFalse(checkpoint.ventana_completa)
            else:
                self.assertEqual(checkpoint.cursor_entrada, "cursor-1")
                self.assertTrue(checkpoint.ventana_completa)

        resultado = cliente(transporte).sincronizar_ventana(
            desde=DESDE,
            hasta=HASTA,
            limite=1,
            aplicar_pagina=aplicar,
        )
        self.assertEqual(eventos, ["solicitud-1", "aplicar-1", "solicitud-2", "aplicar-2"])
        self.assertEqual((resultado.paginas, resultado.pedidos), (2, 2))

    def test_error_del_callback_impide_solicitar_otra_pagina(self):
        primera = respuesta(
            200,
            pagina_payload(limite=1, has_more=True, next_cursor="cursor-1"),
        )
        transporte = TransporteSecuencial(primera)

        def fallar(_pagina, _checkpoint):
            raise RuntimeError("rollback local")

        with self.assertRaisesRegex(RuntimeError, "rollback local"):
            cliente(transporte).sincronizar_ventana(
                desde=DESDE,
                hasta=HASTA,
                limite=1,
                aplicar_pagina=fallar,
            )
        self.assertEqual(len(transporte.solicitudes), 1)

    def test_cursor_repetido_se_rechaza_antes_del_callback(self):
        pagina = respuesta(
            200,
            pagina_payload(limite=1, has_more=True, next_cursor="mismo"),
        )
        transporte = TransporteSecuencial(pagina)
        aplicadas = []
        with self.assertRaises(ErrorContratoPedidos):
            cliente(transporte).sincronizar_ventana(
                desde=DESDE,
                hasta=HASTA,
                limite=1,
                cursor_inicial="mismo",
                aplicar_pagina=lambda *args: aplicadas.append(args),
            )
        self.assertEqual(aplicadas, [])

    def test_410_despues_de_una_pagina_conserva_el_checkpoint_confirmado(self):
        primera = respuesta(
            200,
            pagina_payload(
                limite=1,
                has_more=True,
                next_cursor="cursor-confirmado",
                request_id="antes-gap",
            ),
            request_id="antes-gap",
        )
        brecha = respuesta(
            410,
            error_payload("retention_gap", "durante-gap"),
            request_id="durante-gap",
        )
        transporte = TransporteSecuencial(primera, brecha)
        checkpoints = []

        with self.assertRaises(ErrorBrechaRetencion):
            cliente(transporte).sincronizar_ventana(
                desde=DESDE,
                hasta=HASTA,
                limite=1,
                aplicar_pagina=lambda _pagina, checkpoint: checkpoints.append(checkpoint),
            )

        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(checkpoints[0].cursor_siguiente, "cursor-confirmado")
        self.assertFalse(checkpoints[0].ventana_completa)
    def test_parser_publico_valida_fixture_sin_hacer_red(self):
        payload = pagina_payload(request_id="fixture-001")
        body = json.dumps(payload).encode()
        pagina = parsear_pagina_v2(
            body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Content-Length": str(len(body)),
                "X-Request-ID": "fixture-001",
            },
            sucursales_permitidas=[3],
            limite_solicitado=100,
            max_response_bytes=4096,
        )
        self.assertEqual(pagina.pedidos[0].sucursal.id, 3)


if __name__ == "__main__":
    unittest.main()
