import socket

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import TrabajoImpresion
from .render import enviar_tcp, guardar_png, render_comanda, render_cuenta, render_domicilio


def encolar_impresiones(ticket, formato):
    destinos = []
    if formato in {TrabajoImpresion.Formato.CUENTA, TrabajoImpresion.Formato.DOMICILIO}:
        destinos = [TrabajoImpresion.Destino.CAJA]
    else:
        productos = list(ticket.partidas.select_related("producto__categoria").all())
        if ticket.canal in {"comedor", "domicilio"} and productos:
            destinos.append(TrabajoImpresion.Destino.COCINA)
        else:
            if any(p.producto.destino_impresion == "cocina" for p in productos):
                destinos.append(TrabajoImpresion.Destino.COCINA)
            if any(p.producto.destino_impresion == "barra" for p in productos):
                destinos.append(TrabajoImpresion.Destino.BARRA)
    trabajos = [
        TrabajoImpresion.objects.create(sucursal=ticket.sucursal, ticket=ticket, formato=formato, destino=destino)
        for destino in destinos
    ]
    if settings.PRINT_SYNC:
        for trabajo in trabajos:
            procesar_trabajo(trabajo)
    return trabajos


def estado_impresora(destino="caja"):
    host = settings.PRINTER_HOSTS[destino]
    if settings.PRINT_BACKEND != "tcp":
        return {
            "backend": settings.PRINT_BACKEND,
            "disponible": False,
            "destino": destino,
            "host": host,
            "puerto": settings.PRINTER_PORT,
            "mensaje": "Modo vista previa: no se enviará papel a la impresora.",
        }
    try:
        with socket.create_connection((host, settings.PRINTER_PORT), timeout=settings.PRINTER_TIMEOUT):
            pass
        return {
            "backend": "tcp",
            "disponible": True,
            "destino": destino,
            "host": host,
            "puerto": settings.PRINTER_PORT,
            "mensaje": "Impresora térmica conectada.",
        }
    except OSError as exc:
        return {
            "backend": "tcp",
            "disponible": False,
            "destino": destino,
            "host": host,
            "puerto": settings.PRINTER_PORT,
            "mensaje": f"Impresora sin conexión: {exc}",
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
        if trabajo.formato == TrabajoImpresion.Formato.CUENTA:
            imagen = render_cuenta(ticket)
        elif trabajo.formato == TrabajoImpresion.Formato.DOMICILIO:
            imagen = render_domicilio(ticket)
        else:
            imagen = render_comanda(ticket, trabajo.destino)
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
        trabajo.estado = TrabajoImpresion.Estado.ERROR
        trabajo.error = str(exc)
        trabajo.save(update_fields=["estado", "error"])
    return trabajo
