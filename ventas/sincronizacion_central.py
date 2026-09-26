"""Outbox durable para los flujos candidatos POS -> Backend Central.

La sincronizacion se ejecuta fuera del camino critico de venta. Las rutas v2
permanecen apagadas hasta que agente1 concilie el contrato y exista TLS de
laboratorio valido.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import F, Q
from django.utils import timezone

from .central_api import (
    ClienteCentral,
    ErrorContratoCentral,
    ErrorTLSCentral,
    ErrorTransporteCentral,
)
from .models import Cliente, ConfiguracionSucursal, EventoOutbox


TIPOS_CIERRE_VENTA = {
    "ticket.pagado",
    "ticket.sucursal_completado",
    "ticket.liquidado_repartidor",
    "ticket.cancelado",
}
RUTAS_CENTRAL = {
    EventoOutbox.Destino.CENTRAL_VENTAS: "/api/v2/edge/ventas/lotes/",
    EventoOutbox.Destino.CENTRAL_CLIENTES: "/api/v2/edge/clientes/eventos/",
}
MAX_BODY_POR_DESTINO = {
    EventoOutbox.Destino.CENTRAL_VENTAS: 256 * 1024,
    EventoOutbox.Destino.CENTRAL_CLIENTES: 64 * 1024,
    EventoOutbox.Destino.CENTRAL_CATALOGO_ACK: 16 * 1024,
}
ACK_V2_LARGE_PROFILE = "mappings-large-1"
ACK_V2_LARGE_MAX_BYTES = 1024 * 1024


def _limite_payload_evento(evento):
    if (
        evento.destino == EventoOutbox.Destino.CENTRAL_CATALOGO_ACK
        and evento.version_contrato == 3
    ):
        return 1024 * 1024
    return MAX_BODY_POR_DESTINO[evento.destino]


def _capacidad_ack_v2_grande(respuesta):
    """Sólo un OPTIONS autenticado, explícito y no cacheable amplía el ACK v2."""

    if respuesta.status != 204:
        return False
    headers = respuesta.headers
    cache_control = {
        token.strip().lower()
        for token in headers.get("cache-control", "").split(",")
    }
    return (
        headers.get("x-catalog-ack-profile") == ACK_V2_LARGE_PROFILE
        and headers.get("x-catalog-ack-max-body-bytes") == str(ACK_V2_LARGE_MAX_BYTES)
        and "no-store" in cache_control
    )


def json_canonico(datos):
    return json.dumps(
        datos,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def hash_payload(datos):
    return hashlib.sha256(json_canonico(datos)).hexdigest()


def _instante(valor):
    if valor is None:
        return None
    return valor.isoformat()


def _uuid_canonico(valor, campo):
    texto = str(valor or "")
    try:
        normalizado = str(uuid.UUID(texto))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ErrorContratoCentral(f"El ACK Central contiene {campo} invalido.") from exc
    if texto != normalizado:
        raise ErrorContratoCentral(f"El ACK Central contiene {campo} no canonico.")
    return texto


def _timestamp(valor, campo):
    if type(valor) is not str:
        raise ErrorContratoCentral(f"El ACK Central contiene {campo} invalido.")
    try:
        instante = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ErrorContratoCentral(f"El ACK Central contiene {campo} invalido.") from exc
    if instante.tzinfo is None or instante.utcoffset() is None:
        raise ErrorContratoCentral(f"El ACK Central contiene {campo} sin zona.")
    return instante


def obtener_pos_instance_id(sucursal):
    """Devuelve la identidad durable local y comprueba el valor del entorno si existe."""

    try:
        configuracion = ConfiguracionSucursal.objects.only("instalacion_id").get(
            sucursal=sucursal
        )
    except ConfiguracionSucursal.DoesNotExist as exc:
        raise ErrorContratoCentral(
            "La sucursal no tiene una identidad de instalacion aprovisionada."
        ) from exc
    local = str(configuracion.instalacion_id)
    configurado = str(getattr(settings, "CENTRAL_POS_INSTANCE_ID", "") or "").strip()
    if configurado:
        _uuid_canonico(configurado, "CENTRAL_POS_INSTANCE_ID")
        if configurado != local:
            raise ErrorContratoCentral(
                "CENTRAL_POS_INSTANCE_ID no coincide con la identidad durable local."
            )
    return local


def _partida_payload(partida):
    if partida.personalizada:
        origen = "personalizado"
        producto_id = None
        codigo = ""
    elif partida.producto_id:
        origen = "menu"
        producto_id = str(partida.producto_id)
        codigo = partida.producto.codigo
    else:
        origen = "sucursal"
        producto_id = str(partida.producto_sucursal_id)
        codigo = str(partida.producto_sucursal.origen_id)
    return {
        "id": str(partida.id),
        "origen": origen,
        "producto_id": producto_id,
        "codigo": codigo,
        "nombre_cobrado": partida.nombre_producto,
        "nombre_corto_cobrado": partida.nombre_corto,
        "cantidad": str(partida.cantidad),
        "cantidad_por_precio": str(partida.cantidad_por_precio),
        "unidad": partida.unidad,
        "precio_unitario": str(partida.precio_unitario),
        "importe": str(partida.importe),
    }


def convertir_evento_venta_central(evento, ticket, tipo, datos_extra=None):
    """Congela la venta cobrada usando nombres y precios ya guardados en Partida."""

    partidas = list(
        ticket.partidas.select_related("producto", "producto_sucursal").order_by(
            "creada_en", "id"
        )
    )
    ocurrido = (
        ticket.pagado_en
        or ticket.cancelado_en
        or ticket.procesado_en
        or ticket.actualizado_en
        or evento.creado_en
    )
    cancelada = tipo == "ticket.cancelado"
    venta = {
        "serie_folio": int(ticket.serie_folio),
        "folio": int(ticket.folio),
        "canal": ticket.canal,
        "estado": "cancelado" if cancelada else "pagado",
        "cliente_id": str(ticket.cliente_id) if ticket.cliente_id else None,
        "creado_en": _instante(ticket.creado_en),
        "cerrado_en": _instante(ocurrido),
        "moneda": "MXN",
        "subtotal": str(ticket.subtotal),
        "descuento_porcentaje": str(ticket.descuento_porcentaje),
        "total": str(ticket.total),
        "forma_pago": ticket.forma_pago or None,
        "partidas": [_partida_payload(partida) for partida in partidas],
    }
    evento_detalle = {
        "evento_id": str(evento.id),
        "tipo": "venta.cancelada" if cancelada else "venta.cerrada",
        "venta_id": str(ticket.id),
        "version_agregado": int(ticket.version_entidad),
        "ocurrido_en": _instante(ocurrido),
        "venta": venta,
    }
    eventos = [evento_detalle]
    payload = {
        "version_contrato": 2,
        "lote_id": str(evento.id),
        "sucursal": {
            "id": str(ticket.sucursal_id),
            "clave": ticket.sucursal.clave,
        },
        "creado_en": _instante(evento.creado_en),
        "conteo": 1,
        "eventos": eventos,
        "contenido_sha256": hash_payload(eventos),
    }
    evento.destino = EventoOutbox.Destino.CENTRAL_VENTAS
    evento.estado_entrega = EventoOutbox.EstadoEntrega.PENDIENTE
    evento.version_contrato = 2
    evento.version_origen = int(ticket.version_entidad)
    evento.datos = payload
    evento.payload_hash = hash_payload(payload)
    evento.save(
        update_fields=[
            "destino",
            "estado_entrega",
            "version_contrato",
            "version_origen",
            "datos",
            "payload_hash",
        ]
    )
    return evento


def _telefono_central(item):
    return {
        "id": str(item.id),
        "numero": item.numero,
        "etiqueta": item.etiqueta,
        "principal": bool(item.principal),
        "activo": bool(item.activo),
        "creado_en": _instante(item.creado_en),
    }


def _domicilio_central(item):
    return {
        "id": str(item.id),
        "etiqueta": item.etiqueta,
        "calle": item.calle,
        "numero_exterior": item.numero_exterior,
        "numero_interior": item.numero_interior,
        "colonia": item.colonia,
        "codigo_postal": item.codigo_postal,
        "municipio": item.municipio,
        "referencia": item.referencia,
        "principal": bool(item.principal),
        "activo": bool(item.activo),
        "creado_en": _instante(item.creado_en),
    }


def encolar_cliente_central(cliente):
    """Crea una instantanea completa y permanente en la transaccion local."""

    cliente = (
        Cliente.objects.select_related("sucursal")
        .prefetch_related("telefonos", "domicilios")
        .get(pk=cliente.pk)
    )
    cliente_datos = {
        "clave_corta": cliente.clave_corta,
        "nombre": cliente.nombre,
        "notas": cliente.notas,
        "comentarios_multiples": bool(cliente.comentarios_multiples),
        "activo": bool(cliente.activo),
        "creado_en": _instante(cliente.creado_en),
        "actualizado_en": _instante(cliente.actualizado_en),
        "telefonos": [
            _telefono_central(item)
            for item in cliente.telefonos.all().order_by("creado_en", "id")
        ],
        "domicilios": [
            _domicilio_central(item)
            for item in cliente.domicilios.all().order_by("creado_en", "id")
        ],
    }
    evento_id = uuid.uuid4()
    payload = {
        "version_contrato": 2,
        "evento_id": str(evento_id),
        "sucursal": {
            "id": str(cliente.sucursal_id),
            "clave": cliente.sucursal.clave,
        },
        "cliente_id": str(cliente.id),
        "version_origen": int(cliente.version_entidad),
        "operacion": "upsert" if cliente.activo else "desactivar",
        "ocurrido_en": _instante(cliente.actualizado_en),
        "cliente": cliente_datos,
        "contenido_sha256": hash_payload(cliente_datos),
    }
    return EventoOutbox.objects.create(
        id=evento_id,
        sucursal=cliente.sucursal,
        agregado="cliente",
        agregado_id=cliente.id,
        tipo="cliente.upsert" if cliente.activo else "cliente.desactivar",
        datos=payload,
        destino=EventoOutbox.Destino.CENTRAL_CLIENTES,
        estado_entrega=EventoOutbox.EstadoEntrega.PENDIENTE,
        version_contrato=2,
        version_origen=cliente.version_entidad,
        payload_hash=hash_payload(payload),
    )


def _flujo_habilitado(destino, version_contrato=None):
    if destino == EventoOutbox.Destino.CENTRAL_VENTAS:
        return bool(settings.CENTRAL_ENABLE_SALES_V2)
    if destino == EventoOutbox.Destino.CENTRAL_CLIENTES:
        return bool(settings.CENTRAL_ENABLE_CUSTOMERS_V2)
    if destino == EventoOutbox.Destino.CENTRAL_CATALOGO_ACK:
        if version_contrato == 2:
            return bool(settings.CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2)
        if version_contrato == 3:
            return bool(settings.CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V3)
    return False


def _validar_identidad_evento(evento):
    if (
        str(evento.sucursal_id) != settings.CENTRAL_BRANCH_ID
        or evento.sucursal.clave != settings.CENTRAL_BRANCH_CODE
    ):
        raise ErrorContratoCentral(
            "La identidad local no coincide con CENTRAL_BRANCH_ID/CODE."
        )
    identidad_payload = evento.datos.get("sucursal")
    if identidad_payload != {
        "id": str(evento.sucursal_id),
        "clave": evento.sucursal.clave,
    }:
        raise ErrorContratoCentral("El payload outbox contiene otra sucursal.")
    obtener_pos_instance_id(evento.sucursal)


def _ruta_evento(evento):
    if evento.destino == EventoOutbox.Destino.CENTRAL_CATALOGO_ACK:
        publicacion_id = _uuid_canonico(
            evento.datos.get("publicacion_id"), "publicacion_id"
        )
        contrato = evento.version_contrato
        if type(contrato) is not int or contrato not in {2, 3}:
            raise ErrorContratoCentral("Version de ACK de catalogo no soportada.")
        if evento.datos.get("version_contrato") != contrato:
            raise ErrorContratoCentral("Version de ACK no coincide con el outbox durable.")
        return f"/api/v{contrato}/edge/catalogo/publicaciones/{publicacion_id}/acuse/"
    try:
        return RUTAS_CENTRAL[evento.destino]
    except KeyError as exc:
        raise ErrorContratoCentral("El destino outbox no tiene ruta Central.") from exc


def _validar_ack(evento, datos):
    if type(datos) is not dict:
        raise ErrorContratoCentral("El Central no devolvio un objeto ACK.")
    if evento.destino == EventoOutbox.Destino.CENTRAL_VENTAS:
        esperados = {
            "recibido",
            "acuse",
            "lote_id",
            "estado",
            "contenido_sha256",
            "conteo_aceptado",
            "primera_recepcion_en",
        }
        if set(datos) != esperados or datos.get("recibido") is not True:
            raise ErrorContratoCentral("El Central no devolvio el ACK de ventas exacto.")
        if _uuid_canonico(datos["lote_id"], "lote_id") != str(evento.id):
            raise ErrorContratoCentral("El ACK de ventas corresponde a otro lote.")
        if datos["estado"] not in {"recibido", "purgado"}:
            raise ErrorContratoCentral("El ACK de ventas contiene un estado desconocido.")
        if datos["contenido_sha256"] != evento.datos.get("contenido_sha256"):
            raise ErrorContratoCentral("El hash del ACK de ventas no coincide.")
        if datos["conteo_aceptado"] != evento.datos.get("conteo"):
            raise ErrorContratoCentral("El ACK de ventas contiene otro conteo.")
        _timestamp(datos["primera_recepcion_en"], "primera_recepcion_en")
        estado = datos["estado"]
    elif evento.destino == EventoOutbox.Destino.CENTRAL_CLIENTES:
        esperados = {
            "recibido",
            "acuse",
            "evento_id",
            "cliente_id",
            "estado",
            "version_aplicada",
        }
        if set(datos) != esperados or datos.get("recibido") is not True:
            raise ErrorContratoCentral("El Central no devolvio el ACK de cliente exacto.")
        if _uuid_canonico(datos["evento_id"], "evento_id") != str(evento.id):
            raise ErrorContratoCentral("El ACK de cliente corresponde a otro evento.")
        if _uuid_canonico(datos["cliente_id"], "cliente_id") != str(evento.agregado_id):
            raise ErrorContratoCentral("El ACK de cliente corresponde a otro cliente.")
        if datos["estado"] not in {"creado", "actualizado", "repetido", "desactualizado"}:
            raise ErrorContratoCentral("El ACK de cliente contiene un estado desconocido.")
        if type(datos["version_aplicada"]) is not int or datos["version_aplicada"] < evento.version_origen:
            raise ErrorContratoCentral("El ACK de cliente no confirma una version segura.")
        estado = datos["estado"]
    else:
        esperados = {"recibido", "acuse", "ack_id", "estado_registrado"}
        if set(datos) != esperados or datos.get("recibido") is not True:
            raise ErrorContratoCentral("El Central no devolvio el ACK de catalogo exacto.")
        if _uuid_canonico(datos["ack_id"], "ack_id") != str(evento.id):
            raise ErrorContratoCentral("El ACK Central corresponde a otro acuse local.")
        if datos["estado_registrado"] != evento.datos.get("estado"):
            raise ErrorContratoCentral("El estado registrado del catalogo no coincide.")
        estado = datos["estado_registrado"]
    acuse = _uuid_canonico(datos.get("acuse"), "acuse")
    return acuse, estado


DURACION_RECLAMO_OUTBOX = timedelta(minutes=3)


def _reclamar_evento(
    evento_id,
    *,
    intentos_esperados,
    ahora=None,
    duracion=DURACION_RECLAMO_OUTBOX,
):
    """Reserva un intento mediante CAS sin retener una transaccion durante la red.

    ``intentos`` actua como generacion monotona y ``proximo_intento_en`` como
    vencimiento durable. SQLite serializa el UPDATE condicional; si el proceso
    muere, otro worker puede recuperar el evento al vencer la concesion.
    """

    ahora = ahora or timezone.now()
    if duracion <= timedelta(0):
        raise ValueError("La duracion del reclamo outbox debe ser positiva.")
    intentos_esperados = int(intentos_esperados)
    generacion = intentos_esperados + 1
    vence_en = ahora + duracion
    actualizado = (
        EventoOutbox.objects.filter(
            pk=evento_id,
            estado_entrega=EventoOutbox.EstadoEntrega.PENDIENTE,
            intentos=intentos_esperados,
        )
        .filter(Q(proximo_intento_en__isnull=True) | Q(proximo_intento_en__lte=ahora))
        .update(
            intentos=F("intentos") + 1,
            proximo_intento_en=vence_en,
        )
    )
    if actualizado != 1:
        return None
    try:
        return EventoOutbox.objects.select_related("sucursal").get(
            pk=evento_id,
            estado_entrega=EventoOutbox.EstadoEntrega.PENDIENTE,
            intentos=generacion,
            proximo_intento_en=vence_en,
        )
    except EventoOutbox.DoesNotExist:
        # La concesion vencio y otro worker la recupero antes de esta lectura.
        return None


def _actualizar_evento(
    evento_id,
    *,
    intento,
    estado,
    http=None,
    error="",
    acuse="",
    remoto="",
    demora=None,
):
    """Aplica el resultado solo si aun pertenece a la generacion reclamada.

    El filtro por PENDIENTE hace monotono a ENTREGADO: una respuesta tardia no
    puede degradarlo. Los campos de ACK solo se escriben al entregar, por lo que
    un fallo tampoco puede borrar una confirmacion ya persistida.
    """

    actualizaciones = {
        "estado_entrega": estado,
        "ultima_respuesta_http": http,
        "ultimo_error": str(error)[:1000],
        "proximo_intento_en": (
            timezone.now() + demora if demora is not None else None
        ),
    }
    if estado == EventoOutbox.EstadoEntrega.ENTREGADO:
        actualizaciones.update(
            {
                "acuse_remoto": str(acuse)[:160],
                "estado_remoto": str(remoto)[:32],
                "publicado_en": timezone.now(),
            }
        )
    actualizado = EventoOutbox.objects.filter(
        pk=evento_id,
        estado_entrega=EventoOutbox.EstadoEntrega.PENDIENTE,
        intentos=int(intento),
    ).update(**actualizaciones)
    return actualizado == 1

def _demora_reintento(evento, respuesta=None):
    if respuesta is not None:
        valor = str(respuesta.headers.get("retry-after", "")).strip()
        if valor.isascii() and valor.isdigit():
            return timedelta(seconds=max(1, min(int(valor), 3600)))
    return timedelta(minutes=min(60, 2 ** min(evento.intentos, 5)))


def sincronizar_outbox_central(*, limite=50, cliente_factory=ClienteCentral):
    """Procesa pendientes sin bloquear ventas, cobros, impresion o SQLite local."""

    ahora = timezone.now()
    candidatos = list(
        EventoOutbox.objects.filter(
            estado_entrega=EventoOutbox.EstadoEntrega.PENDIENTE,
            destino__in=[
                EventoOutbox.Destino.CENTRAL_VENTAS,
                EventoOutbox.Destino.CENTRAL_CLIENTES,
                EventoOutbox.Destino.CENTRAL_CATALOGO_ACK,
            ],
        )
        .filter(Q(proximo_intento_en__isnull=True) | Q(proximo_intento_en__lte=ahora))
        .order_by("creado_en")[: max(1, min(int(limite), 200))]
    )
    resultado = {
        "entregados": 0,
        "pendientes": 0,
        "suspendidos": 0,
        "conciliacion": 0,
    }
    clientes = {}
    for candidato in candidatos:
        if not _flujo_habilitado(candidato.destino, candidato.version_contrato):
            resultado["pendientes"] += 1
            continue

        evento = _reclamar_evento(
            candidato.pk,
            intentos_esperados=candidato.intentos,
        )
        if evento is None:
            continue
        intento = evento.intentos

        try:
            _validar_identidad_evento(evento)
        except ErrorContratoCentral:
            if _actualizar_evento(
                evento.id,
                intento=intento,
                estado=EventoOutbox.EstadoEntrega.SUSPENDIDO,
                error="La identidad Central configurada no coincide con la instalacion local.",
            ):
                resultado["suspendidos"] += 1
            continue
        if hash_payload(evento.datos) != evento.payload_hash:
            if _actualizar_evento(
                evento.id,
                intento=intento,
                estado=EventoOutbox.EstadoEntrega.CUARENTENA,
                error="El payload local ya no coincide con su hash inmutable.",
            ):
                resultado["suspendidos"] += 1
            continue
        tamano_payload = len(json_canonico(evento.datos))
        ack_v2_grande = (
            evento.destino == EventoOutbox.Destino.CENTRAL_CATALOGO_ACK
            and evento.version_contrato == 2
            and tamano_payload > _limite_payload_evento(evento)
        )
        limite_aplicable = (
            ACK_V2_LARGE_MAX_BYTES if ack_v2_grande else _limite_payload_evento(evento)
        )
        if tamano_payload > limite_aplicable:
            if _actualizar_evento(
                evento.id,
                intento=intento,
                estado=EventoOutbox.EstadoEntrega.CUARENTENA,
                error="El payload excede el limite contractual del flujo.",
            ):
                resultado["suspendidos"] += 1
            continue

        token_tipo = (
            "catalogo"
            if evento.destino == EventoOutbox.Destino.CENTRAL_CATALOGO_ACK
            else "ingesta"
        )
        token = (
            settings.CENTRAL_CATALOG_TOKEN
            if token_tipo == "catalogo"
            else settings.CENTRAL_INGEST_TOKEN
        )
        if token_tipo not in clientes:
            clientes[token_tipo] = cliente_factory(
                base_url=settings.CENTRAL_API_BASE_URL,
                token=token,
                ca_bundle=settings.CENTRAL_API_CA_BUNDLE or None,
                timeout=max(
                    settings.CENTRAL_CONNECT_TIMEOUT_SECONDS,
                    settings.CENTRAL_READ_TIMEOUT_SECONDS,
                ),
                max_response_bytes=settings.CENTRAL_MAX_RESPONSE_BYTES,
            )
        try:
            ruta = _ruta_evento(evento)
        except ErrorContratoCentral:
            if _actualizar_evento(
                evento.id,
                intento=intento,
                estado=EventoOutbox.EstadoEntrega.CUARENTENA,
                error="El evento local no tiene una ruta de ACK válida.",
            ):
                resultado["suspendidos"] += 1
            continue
        try:
            parametros_post = {
                "metodo": "POST",
                "ruta": ruta,
                "payload": evento.datos,
                "idempotencia": str(evento.id),
            }
            if ack_v2_grande:
                try:
                    capacidad = clientes[token_tipo].solicitar(
                        metodo="OPTIONS", ruta=ruta
                    )
                except ErrorContratoCentral:
                    capacidad = None
                if capacidad is not None and capacidad.status in {401, 403}:
                    if _actualizar_evento(
                        evento.id,
                        intento=intento,
                        estado=EventoOutbox.EstadoEntrega.SUSPENDIDO,
                        http=capacidad.status,
                        error="La credencial Central no autoriza el perfil ACK v2 grande.",
                    ):
                        resultado["suspendidos"] += 1
                    continue
                if capacidad is None or not _capacidad_ack_v2_grande(capacidad):
                    if _actualizar_evento(
                        evento.id,
                        intento=intento,
                        estado=EventoOutbox.EstadoEntrega.PENDIENTE,
                        error="El Central aún no acredita el perfil ACK v2 grande; se conserva el evento.",
                        demora=_demora_reintento(evento),
                    ):
                        resultado["pendientes"] += 1
                    continue
                parametros_post["perfil_ack"] = ACK_V2_LARGE_PROFILE
            respuesta = clientes[token_tipo].solicitar(**parametros_post)
            if respuesta.status in {200, 201}:
                try:
                    acuse, estado_remoto = _validar_ack(evento, respuesta.datos)
                except ErrorContratoCentral:
                    if not ack_v2_grande:
                        raise
                    if _actualizar_evento(
                        evento.id,
                        intento=intento,
                        estado=EventoOutbox.EstadoEntrega.CONCILIACION,
                        http=respuesta.status,
                        error="El Central devolvió un ACK incompatible; se conserva el evento para conciliación.",
                    ):
                        resultado["conciliacion"] += 1
                    continue
                if _actualizar_evento(
                    evento.id,
                    intento=intento,
                    estado=EventoOutbox.EstadoEntrega.ENTREGADO,
                    http=respuesta.status,
                    acuse=acuse,
                    remoto=estado_remoto,
                ):
                    resultado["entregados"] += 1
                continue
            if respuesta.status in {401, 403}:
                estado = EventoOutbox.EstadoEntrega.SUSPENDIDO
                demora = None
                contador = "suspendidos"
            elif respuesta.status == 409:
                estado = EventoOutbox.EstadoEntrega.CONCILIACION
                demora = None
                contador = "conciliacion"
            elif respuesta.status == 413 and ack_v2_grande:
                # Un proxy o worker antiguo pudo no aplicar el perfil recién anunciado.
                estado = EventoOutbox.EstadoEntrega.PENDIENTE
                demora = _demora_reintento(evento, respuesta)
                contador = "pendientes"
            elif respuesta.status in {400, 413, 415, 422}:
                estado = EventoOutbox.EstadoEntrega.CUARENTENA
                demora = None
                contador = "suspendidos"
            elif respuesta.status == 404:
                estado = EventoOutbox.EstadoEntrega.PENDIENTE
                demora = timedelta(hours=6)
                contador = "pendientes"
            elif respuesta.status == 429 or 500 <= respuesta.status <= 599:
                estado = EventoOutbox.EstadoEntrega.PENDIENTE
                demora = _demora_reintento(evento, respuesta)
                contador = "pendientes"
            else:
                estado = EventoOutbox.EstadoEntrega.CUARENTENA
                demora = None
                contador = "suspendidos"
            if _actualizar_evento(
                evento.id,
                intento=intento,
                estado=estado,
                http=respuesta.status,
                error=f"Respuesta HTTP {respuesta.status}; consulta la matriz de integracion.",
                demora=demora,
            ):
                resultado[contador] += 1
        except (ErrorTLSCentral, ErrorTransporteCentral):
            if _actualizar_evento(
                evento.id,
                intento=intento,
                estado=EventoOutbox.EstadoEntrega.PENDIENTE,
                error="Fallo de transporte o TLS; se conserva el evento.",
                demora=_demora_reintento(evento),
            ):
                resultado["pendientes"] += 1
        except ErrorContratoCentral:
            # El ACK v2 grande ya fue validado localmente; una respuesta/proxy
            # incompatible no demuestra que ese evento durable sea inválido.
            estado = (
                EventoOutbox.EstadoEntrega.PENDIENTE
                if ack_v2_grande
                else EventoOutbox.EstadoEntrega.CUARENTENA
            )
            if _actualizar_evento(
                evento.id,
                intento=intento,
                estado=estado,
                error="Respuesta Central incompatible con el contrato candidato.",
                demora=_demora_reintento(evento) if ack_v2_grande else None,
            ):
                resultado["pendientes" if ack_v2_grande else "suspendidos"] += 1
    return resultado
