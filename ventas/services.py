from decimal import Decimal

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from catalogo.models import Producto
from personas.models import Sucursal, UsuarioPOS

from .models import EventoOutbox, Mesa, ModificadorTicket, Partida, Ticket


class ErrorVenta(ValueError):
    pass


def _evento(ticket, tipo, datos=None):
    EventoOutbox.objects.create(
        sucursal=ticket.sucursal,
        agregado="ticket",
        agregado_id=ticket.id,
        tipo=tipo,
        datos={"ticket_id": str(ticket.id), "folio": ticket.folio, **(datos or {})},
    )


def registrar_evento(ticket, tipo, datos=None):
    _evento(ticket, tipo, datos)


@transaction.atomic
def abrir_ticket(mesa):
    activo = Ticket.objects.filter(
        mesa=mesa,
        estado__in=[Ticket.Estado.ABIERTO, Ticket.Estado.PROCESADO, Ticket.Estado.COBRAR],
    ).first()
    if activo:
        return activo, False
    # Serializa la asignación de folios por sucursal también cuando la base está concurrida.
    Sucursal.objects.select_for_update().get(pk=mesa.sucursal_id)
    ultimo = Ticket.objects.filter(sucursal=mesa.sucursal).aggregate(Max("folio"))["folio__max"] or 0
    atendio = UsuarioPOS.objects.filter(sucursal=mesa.sucursal, activo=True).first()
    ticket = Ticket.objects.create(
        sucursal=mesa.sucursal,
        mesa=mesa,
        atendio=atendio,
        folio=ultimo + 1,
        canal=mesa.canal,
    )
    _evento(ticket, "ticket.abierto", {"canal": ticket.canal, "mesa": mesa.nombre})
    return ticket, True


@transaction.atomic
def agregar_partida(ticket, producto, comensal=1, cantidad=Decimal("1.000")):
    if ticket.estado != Ticket.Estado.ABIERTO:
        raise ErrorVenta("La orden ya fue procesada; no admite nuevas partidas.")
    precio = producto.precio_actual()
    if not producto.activo or not precio:
        raise ErrorVenta("El producto no tiene un precio activo.")
    partida = Partida.objects.create(
        sucursal=ticket.sucursal,
        ticket=ticket,
        producto=producto,
        comensal=comensal,
        cantidad=cantidad,
        precio_unitario=precio.importe,
        nombre_producto=producto.nombre,
        nombre_corto=producto.nombre_corto,
    )
    _evento(ticket, "ticket.partida_agregada", {"partida_id": str(partida.id), "producto_id": str(producto.id)})
    return partida


@transaction.atomic
def actualizar_partida(partida, cantidad):
    if partida.ticket.estado != Ticket.Estado.ABIERTO:
        raise ErrorVenta("La orden ya fue procesada.")
    if cantidad <= 0:
        ticket = partida.ticket
        partida_id = str(partida.id)
        partida.delete()
        _evento(ticket, "ticket.partida_eliminada", {"partida_id": partida_id})
        return None
    partida.cantidad = cantidad
    partida.save(update_fields=["cantidad"])
    _evento(partida.ticket, "ticket.partida_actualizada", {"partida_id": str(partida.id), "cantidad": str(cantidad)})
    return partida


@transaction.atomic
def alternar_modificador(ticket, comensal, codigo, nombre):
    if ticket.estado != Ticket.Estado.ABIERTO:
        raise ErrorVenta("La orden ya fue procesada.")
    existente = ModificadorTicket.objects.filter(ticket=ticket, comensal=comensal, codigo=codigo).first()
    if existente:
        existente.delete()
        activo = False
    else:
        ModificadorTicket.objects.create(
            sucursal=ticket.sucursal,
            ticket=ticket,
            comensal=comensal,
            codigo=codigo,
            nombre=nombre,
        )
        activo = True
    _evento(ticket, "ticket.modificador", {"comensal": comensal, "codigo": codigo, "activo": activo})
    return activo


@transaction.atomic
def asegurar_modificador(ticket, comensales, codigo, nombre):
    if ticket.estado != Ticket.Estado.ABIERTO:
        raise ErrorVenta("La orden ya fue procesada.")
    aplicados = []
    for comensal in comensales:
        _, creado = ModificadorTicket.objects.get_or_create(
            sucursal=ticket.sucursal,
            ticket=ticket,
            comensal=comensal,
            codigo=codigo,
            defaults={"nombre": nombre},
        )
        aplicados.append(comensal)
        if creado:
            _evento(ticket, "ticket.modificador", {"comensal": comensal, "codigo": codigo, "activo": True})
    return aplicados


@transaction.atomic
def procesar_ticket(ticket):
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    if ticket.estado != Ticket.Estado.ABIERTO:
        raise ErrorVenta("La orden no está abierta.")
    if not ticket.partidas.exists():
        raise ErrorVenta("Agrega al menos un producto.")
    ticket.estado = Ticket.Estado.PROCESADO
    ticket.procesado_en = timezone.now()
    ticket.save(update_fields=["estado", "procesado_en", "actualizado_en"])
    ticket.partidas.update(procesada=True)
    _evento(ticket, "ticket.procesado", {"total": str(ticket.total)})
    return ticket


@transaction.atomic
def cobrar_ticket(ticket, forma_pago, importe_recibido=None):
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    if ticket.estado not in [Ticket.Estado.PROCESADO, Ticket.Estado.COBRAR]:
        raise ErrorVenta("Primero procesa la orden.")
    ticket.estado = Ticket.Estado.PAGADO
    ticket.forma_pago = forma_pago
    ticket.importe_recibido = importe_recibido
    ticket.pagado_en = timezone.now()
    ticket.save(update_fields=["estado", "forma_pago", "importe_recibido", "pagado_en", "actualizado_en"])
    _evento(ticket, "ticket.pagado", {"total": str(ticket.total), "forma_pago": forma_pago})
    return ticket
