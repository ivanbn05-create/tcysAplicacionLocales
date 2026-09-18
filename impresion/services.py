import logging
import socket
import threading
import time
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import TrabajoImpresion
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


def encolar_impresiones(ticket, formato, comanda_numero=None):
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
    trabajos = [
        TrabajoImpresion.objects.create(
            sucursal=ticket.sucursal,
            ticket=ticket,
            formato=formato,
            destino=destino,
            comanda_numero=numero_trabajo,
        )
        for destino in destinos
    ]
    if settings.PRINT_SYNC:
        for trabajo in trabajos:
            procesar_trabajo(trabajo)
    return trabajos


def encolar_reporte(reporte):
    formatos = {
        "liquidacion": TrabajoImpresion.Formato.LIQUIDACION,
        "parcial": TrabajoImpresion.Formato.PARCIAL,
        "corte_caja": TrabajoImpresion.Formato.CORTE_CAJA,
        "corte_sucursal": TrabajoImpresion.Formato.CORTE_SUCURSAL,
    }
    formato = formatos[reporte.tipo]
    trabajo = TrabajoImpresion.objects.create(
        sucursal=reporte.sucursal,
        reporte=reporte,
        formato=formato,
        destino=TrabajoImpresion.Destino.CAJA,
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
    trabajo = (
        TrabajoImpresion.objects.select_for_update(skip_locked=True)
        .filter(estado=TrabajoImpresion.Estado.PENDIENTE)
        .order_by("creado_en")
        .first()
    )
    if trabajo:
        trabajo.estado = TrabajoImpresion.Estado.PROCESANDO
        trabajo.intentos += 1
        trabajo.save(update_fields=["estado", "intentos"])
    return trabajo


def procesar_trabajo(trabajo):
    try:
        trabajo.estado = TrabajoImpresion.Estado.PROCESANDO
        if trabajo.intentos == 0:
            trabajo.intentos = 1
        trabajo.save(update_fields=["estado", "intentos"])
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
            enviar_tcp(imagen, trabajo.destino)
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
        trabajo.save(update_fields=["estado", "error"])
    return trabajo
