import logging
import socket
import threading
import time
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (AsignacionImpresoraTerminal, ConfiguracionImpresionTerminal, TrabajoImpresion)
from .network import resolver_ip_impresora
from .render import (
    enviar_tcp,
    guardar_png,
    guardar_png_reporte,
    render_comanda,
    render_cuenta,
    render_domicilio,
    render_reporte_administrativo,
    render_sucursal,
)


logger = logging.getLogger(__name__)
_purga_lock = threading.Lock()
_ultima_purga = 0.0


def trabajo_procesando_abandonado(trabajo, ahora=None):
    """Indica si el lease temporal de un trabajo PROCESANDO ya venció."""

    if trabajo.estado != TrabajoImpresion.Estado.PROCESANDO:
        return False
    ahora = ahora or timezone.now()
    limite = ahora - timedelta(
        seconds=int(getattr(settings, "PRINT_PROCESSING_TIMEOUT_SECONDS", 300))
    )
    return trabajo.procesado_en is None or trabajo.procesado_en <= limite


def _recuperar_trabajos_abandonados(ahora):
    limite = ahora - timedelta(
        seconds=int(getattr(settings, "PRINT_PROCESSING_TIMEOUT_SECONDS", 300))
    )
    ids = list(
        TrabajoImpresion.objects.select_for_update(skip_locked=True)
        .filter(estado=TrabajoImpresion.Estado.PROCESANDO)
        .filter(Q(procesado_en__isnull=True) | Q(procesado_en__lte=limite))
        .order_by("procesado_en", "creado_en")
        .values_list("pk", flat=True)[:100]
    )
    if ids:
        if settings.PRINT_BACKEND == "tcp":
            # Tras un reinicio no sabemos si la impresora recibió todos los bytes.
            # Evita un segundo papel hasta que soporte revise el trabajo.
            TrabajoImpresion.objects.filter(pk__in=ids).update(
                estado=TrabajoImpresion.Estado.ERROR,
                procesado_en=ahora,
                error="El servicio se reinició durante el envío. Revisa el papel antes de reintentar.",
            )
        else:
            TrabajoImpresion.objects.filter(pk__in=ids).update(
                estado=TrabajoImpresion.Estado.PENDIENTE,
                procesado_en=None,
                error="El procesamiento anterior se interrumpió; se reintentará.",
            )
    return len(ids)


def purgar_vistas_previas(forzar=False):
    """Elimina PNG con PII al vencer la retención, sin salir de MEDIA_ROOT."""

    global _ultima_purga
    ahora_monotono = time.monotonic()
    if not forzar and ahora_monotono - _ultima_purga < 3600:
        return 0
    if not _purga_lock.acquire(blocking=False):
        return 0
    eliminados = 0
    try:
        _ultima_purga = ahora_monotono
        dias = int(getattr(settings, "PRINT_PREVIEW_RETENTION_DAYS", 7))
        limite = timezone.now() - timedelta(days=dias)
        root = Path(settings.MEDIA_ROOT).resolve()
        trabajos = list(
            TrabajoImpresion.objects.exclude(archivo="")
            .filter(creado_en__lt=limite)
            .only("id", "archivo")[:500]
        )
        for trabajo in trabajos:
            relative = Path(trabajo.archivo)
            if relative.is_absolute():
                logger.warning("Se omitió una ruta absoluta durante la purga de impresiones.")
                continue
            try:
                candidate = (root / relative).resolve(strict=True)
            except (OSError, RuntimeError):
                TrabajoImpresion.objects.filter(pk=trabajo.pk).update(archivo="")
                continue
            if not candidate.is_relative_to(root) or not candidate.is_file():
                logger.warning("Se omitió una ruta fuera de MEDIA_ROOT durante la purga.")
                continue
            try:
                candidate.unlink()
            except OSError as exc:
                logger.warning("No fue posible purgar una impresión (%s).", type(exc).__name__)
                continue
            TrabajoImpresion.objects.filter(pk=trabajo.pk).update(archivo="")
            eliminados += 1
        return eliminados
    finally:
        _purga_lock.release()


def resolver_ruta_impresion(sucursal, device_id, destino):
    """Resuelve una ruta y devuelve un snapshot que el worker no recalcula."""

    identificador = str(device_id or "").strip()
    if len(identificador) > 128 or any(ord(caracter) < 32 for caracter in identificador):
        identificador = ""
    configuracion = None
    if identificador:
        configuracion = (
            ConfiguracionImpresionTerminal.objects.filter(
                sucursal=sucursal,
                device_id=identificador,
                activa=True,
            )
            .only(
                "device_id",
                "host_caja",
                "host_cocina",
                "host_barra",
                "puerto",
            )
            .first()
        )
    if configuracion is not None:
        asignaciones = list(
            AsignacionImpresoraTerminal.objects.select_related("impresora")
            .filter(terminal=configuracion, destino__in=[destino, "todos"])
        )
        # La ruta específica prevalece sobre la general. No se redirige un
        # recurso desactivado a otra impresora sin decisión del técnico.
        asignacion = next(
            (item for item in asignaciones if item.destino == destino),
            next((item for item in asignaciones if item.destino == "todos"), None),
        )
        if asignacion is not None:
            impresora = asignacion.impresora
            return {
                "device_id": identificador,
                "printer_host": impresora.host if impresora.activa else "",
                "printer_port": impresora.puerto,
                "printer_name": impresora.nombre,
                "origen_ruta": "recurso" if impresora.activa else "recurso_inactivo",
            }
        host = configuracion.host_para(destino)
        if host:
            return {
                "device_id": identificador,
                "printer_host": str(host),
                "printer_port": int(configuracion.puerto),
                "printer_name": "",
                "origen_ruta": "terminal",
            }
    host = str(settings.PRINTER_HOSTS.get(destino) or "").strip()
    return {
        "device_id": identificador,
        "printer_host": host or None,
        "printer_port": int(settings.PRINTER_PORT),
        "printer_name": "",
        "origen_ruta": "legacy_env",
    }


def encolar_impresiones(ticket, formato, comanda_numero=None, device_id=""):
    purgar_vistas_previas()
    destinos = []
    numero_trabajo = None
    if formato in {
        TrabajoImpresion.Formato.CUENTA,
        TrabajoImpresion.Formato.DOMICILIO,
        TrabajoImpresion.Formato.SUCURSAL,
    }:
        destinos = [TrabajoImpresion.Destino.CAJA]
    else:
        numero_trabajo = int(comanda_numero or ticket.comanda_actual or 1)
        productos = list(
            ticket.partidas.select_related("producto__categoria").filter(
                comanda_numero=numero_trabajo,
            )
        )
        if ticket.canal in {"comedor", "llevar", "domicilio", "recoger"} and productos:
            destinos.append(TrabajoImpresion.Destino.COCINA)
        else:
            if any(p.producto.destino_impresion == "cocina" for p in productos):
                destinos.append(TrabajoImpresion.Destino.COCINA)
            if any(p.producto.destino_impresion == "barra" for p in productos):
                destinos.append(TrabajoImpresion.Destino.BARRA)
    trabajos = []
    for destino in destinos:
        ruta = resolver_ruta_impresion(ticket.sucursal, device_id, destino)
        trabajos.append(
            TrabajoImpresion.objects.create(
                sucursal=ticket.sucursal,
                ticket=ticket,
                formato=formato,
                destino=destino,
                comanda_numero=numero_trabajo,
                **ruta,
            )
        )
    if settings.PRINT_SYNC:
        for trabajo in trabajos:
            procesar_trabajo(trabajo)
    return trabajos


def encolar_reporte(reporte, device_id=""):
    formatos = {
        "liquidacion": TrabajoImpresion.Formato.LIQUIDACION,
        "parcial": TrabajoImpresion.Formato.PARCIAL,
        "corte_caja": TrabajoImpresion.Formato.CORTE_CAJA,
        "corte_sucursal": TrabajoImpresion.Formato.CORTE_SUCURSAL,
    }
    formato = formatos[reporte.tipo]
    ruta = resolver_ruta_impresion(
        reporte.sucursal,
        device_id,
        TrabajoImpresion.Destino.CAJA,
    )
    trabajo = TrabajoImpresion.objects.create(
        sucursal=reporte.sucursal,
        reporte=reporte,
        formato=formato,
        destino=TrabajoImpresion.Destino.CAJA,
        **ruta,
    )
    if settings.PRINT_SYNC:
        procesar_trabajo(trabajo)
    return [trabajo]


def sondear_impresora(destino="caja"):
    """Comprueba el socket TCP sin transmitir bytes ni generar papel."""

    if destino not in settings.PRINTER_HOSTS:
        raise ValueError("Destino de impresión inválido.")
    host = settings.PRINTER_HOSTS[destino]
    if not host:
        return {
            "destino": destino,
            "host": "",
            "puerto": settings.PRINTER_PORT,
            "configurada": False,
            "alcanzable": False,
            "mensaje": "No hay una dirección configurada para esta impresora.",
        }
    try:
        with socket.create_connection(
            (host, settings.PRINTER_PORT),
            timeout=settings.PRINTER_TIMEOUT,
        ):
            pass
        return {
            "destino": destino,
            "host": host,
            "puerto": settings.PRINTER_PORT,
            "configurada": True,
            "alcanzable": True,
            "mensaje": "Conectividad TCP disponible; no se enviaron datos.",
        }
    except OSError as exc:
        logger.warning(
            "No se pudo conectar con la impresora configurada (%s).",
            type(exc).__name__,
        )
        return {
            "destino": destino,
            "host": host,
            "puerto": settings.PRINTER_PORT,
            "configurada": True,
            "alcanzable": False,
            "mensaje": "Impresora sin conexión TCP.",
        }


def estado_impresora(destino="caja"):
    if settings.PRINT_BACKEND != "tcp":
        return {
            "backend": settings.PRINT_BACKEND,
            "disponible": False,
            "destino": destino,
            "codigo": "impresion_fisica_desactivada",
            "mensaje": (
                "Impresión física desactivada (PRINT_BACKEND=archivo): "
                "sólo se generará una vista previa."
            ),
        }
    sondeo = sondear_impresora(destino)
    return {
        "backend": "tcp",
        "disponible": sondeo["alcanzable"],
        "destino": destino,
        "codigo": "conectada" if sondeo["alcanzable"] else "sin_conexion",
        "mensaje": (
            "Impresora térmica conectada."
            if sondeo["alcanzable"]
            else sondeo["mensaje"]
        ),
    }


@transaction.atomic
def reclamar_siguiente():
    ahora = timezone.now()
    _recuperar_trabajos_abandonados(ahora)
    trabajo = (
        TrabajoImpresion.objects.select_for_update(skip_locked=True)
        .filter(estado=TrabajoImpresion.Estado.PENDIENTE)
        .order_by("creado_en")
        .first()
    )
    if trabajo:
        trabajo.estado = TrabajoImpresion.Estado.PROCESANDO
        trabajo.intentos += 1
        trabajo.procesado_en = ahora
        trabajo.error = ""
        trabajo.save(
            update_fields=["estado", "intentos", "procesado_en", "error"]
        )
    return trabajo


def procesar_trabajo(trabajo):
    try:
        trabajo.estado = TrabajoImpresion.Estado.PROCESANDO
        if trabajo.intentos == 0:
            trabajo.intentos = 1
        trabajo.procesado_en = timezone.now()
        trabajo.error = ""
        trabajo.save(
            update_fields=["estado", "intentos", "procesado_en", "error"]
        )
        ticket = trabajo.ticket
        if trabajo.reporte_id:
            imagen = render_reporte_administrativo(trabajo.reporte)
        elif trabajo.formato == TrabajoImpresion.Formato.CUENTA:
            imagen = render_cuenta(ticket)
        elif trabajo.formato == TrabajoImpresion.Formato.DOMICILIO:
            imagen = render_domicilio(ticket)
        elif trabajo.formato == TrabajoImpresion.Formato.SUCURSAL:
            imagen = render_sucursal(ticket)
        else:
            imagen = render_comanda(
                ticket,
                trabajo.destino,
                trabajo.comanda_numero,
            )
        if trabajo.reporte_id:
            relativo, _ = guardar_png_reporte(
                imagen,
                trabajo.reporte,
                trabajo.formato,
                trabajo.destino,
            )
        else:
            relativo, _ = guardar_png(imagen, ticket, trabajo.formato, trabajo.destino)
        trabajo.archivo = relativo
        trabajo.save(update_fields=["archivo"])
        if settings.PRINT_BACKEND == "tcp":
            if trabajo.origen_ruta == "recurso_inactivo":
                raise OSError("El recurso de impresora está inactivo.")
            host = trabajo.printer_host or settings.PRINTER_HOSTS[trabajo.destino]
            puerto = trabajo.printer_port or settings.PRINTER_PORT
            if trabajo.origen_ruta == "recurso":
                # Fijar la IP validada evita que una segunda resolución DNS
                # redirija el socket a un destino fuera de la LAN.
                host = resolver_ip_impresora(host, puerto)
            enviar_tcp(
                imagen,
                trabajo.destino,
                host=host,
                puerto=puerto,
            )
            trabajo.estado = TrabajoImpresion.Estado.IMPRESO
        else:
            trabajo.estado = TrabajoImpresion.Estado.GENERADO
        trabajo.procesado_en = timezone.now()
        trabajo.error = ""
        trabajo.save(update_fields=["estado", "archivo", "procesado_en", "error"])
    except Exception as exc:
        logger.warning("Falló un trabajo de impresión (%s).", type(exc).__name__)
        trabajo.estado = TrabajoImpresion.Estado.ERROR
        trabajo.error = "No fue posible completar la impresión."
        trabajo.procesado_en = timezone.now()
        trabajo.save(update_fields=["estado", "error", "procesado_en"])
    return trabajo
