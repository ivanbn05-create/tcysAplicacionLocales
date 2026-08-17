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


def _datos_termino(producto, termino=None):
    if not producto.permite_termino:
        return "", producto.nombre, producto.nombre_corto
    termino = termino or producto.termino_predeterminado
    if termino not in Producto.Termino.values:
        raise ErrorVenta("Término de preparación inválido.")
    abreviatura = producto.abreviaturas_termino.get(termino)
    if not abreviatura:
        raise ErrorVenta("El producto no tiene abreviatura para ese término.")
    etiqueta = Producto.Termino(termino).label.lower()
    return termino, f"{producto.nombre} {etiqueta}", abreviatura


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
def agregar_partida(ticket, producto, comensal=1, cantidad=Decimal("1.000"), termino=None):
    if ticket.estado != Ticket.Estado.ABIERTO:
        raise ErrorVenta("La orden ya fue procesada; no admite nuevas partidas.")
    if cantidad != cantidad.to_integral_value() or not 1 <= cantidad <= 99:
        raise ErrorVenta("La cantidad debe ser un entero entre 1 y 99.")
    precio = producto.precio_actual()
    if not producto.activo or not precio:
        raise ErrorVenta("El producto no tiene un precio activo.")
    termino, nombre_producto, nombre_corto = _datos_termino(producto, termino)
    partida = Partida.objects.create(
        sucursal=ticket.sucursal,
        ticket=ticket,
        producto=producto,
        comensal=comensal,
        cantidad=cantidad,
        precio_unitario=precio.importe,
        nombre_producto=nombre_producto,
        nombre_corto=nombre_corto,
        termino=termino,
    )
    _evento(ticket, "ticket.partida_agregada", {"partida_id": str(partida.id), "producto_id": str(producto.id)})
    return partida


@transaction.atomic
def actualizar_partida(partida, cantidad, termino=None):
    if partida.ticket.estado != Ticket.Estado.ABIERTO:
        raise ErrorVenta("La orden ya fue procesada.")
    if cantidad <= 0:
        ticket = partida.ticket
        partida_id = str(partida.id)
        partida.delete()
        _evento(ticket, "ticket.partida_eliminada", {"partida_id": partida_id})
        return None
    partida.cantidad = cantidad
    campos = ["cantidad"]
    if termino is not None:
        partida.termino, partida.nombre_producto, partida.nombre_corto = _datos_termino(partida.producto, termino)
        campos.extend(["termino", "nombre_producto", "nombre_corto"])
    partida.save(update_fields=campos)
    _evento(
        partida.ticket,
        "ticket.partida_actualizada",
        {"partida_id": str(partida.id), "cantidad": str(cantidad), "termino": partida.termino},
    )
    return partida


@transaction.atomic
def ajustar_grupo_partidas(ticket, partida_ids, cantidad, termino=None, eliminar=False):
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    if ticket.estado != Ticket.Estado.ABIERTO:
        raise ErrorVenta("La orden ya fue procesada.")
    partidas = list(
        Partida.objects.select_for_update()
        .select_related("producto", "ticket")
        .filter(ticket=ticket, id__in=partida_ids)
        .order_by("creada_en")
    )
    if not partidas:
        raise ErrorVenta("No se encontró el producto seleccionado.")
    principal = partidas[0]
    if any(
        partida.producto_id != principal.producto_id
        or partida.comensal != principal.comensal
        or partida.termino != principal.termino
        for partida in partidas[1:]
    ):
        raise ErrorVenta("La selección contiene partidas distintas.")
    if eliminar:
        ids = [str(partida.id) for partida in partidas]
        Partida.objects.filter(id__in=[partida.id for partida in partidas]).delete()
        _evento(ticket, "ticket.partidas_eliminadas", {"partida_ids": ids})
        return None
    if cantidad != cantidad.to_integral_value() or not 1 <= cantidad <= 99:
        raise ErrorVenta("La cantidad debe ser un entero entre 1 y 99.")
    duplicadas_destino = []
    if termino is not None and termino != principal.termino:
        termino_destino, _, _ = _datos_termino(principal.producto, termino)
        duplicadas_destino = list(
            Partida.objects.select_for_update()
            .filter(
                ticket=ticket,
                producto=principal.producto,
                comensal=principal.comensal,
                termino=termino_destino,
            )
            .exclude(id__in=[partida.id for partida in partidas])
        )
        cantidad += sum((partida.cantidad for partida in duplicadas_destino), Decimal("0"))
        if cantidad > 99:
            raise ErrorVenta("La cantidad acumulada no puede superar 99.")
    principal = actualizar_partida(principal, cantidad, termino)
    duplicadas = partidas[1:] + duplicadas_destino
    if duplicadas:
        Partida.objects.filter(id__in=[partida.id for partida in duplicadas]).delete()
    return principal


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
    comensales_con_producto = set(
        ticket.partidas.exclude(producto__categoria__nombre__iexact="Bebidas")
        .filter(comensal__in=comensales)
        .values_list("comensal", flat=True)
    )
    aplicados = []
    for comensal in comensales:
        if comensal not in comensales_con_producto:
            continue
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
    if ticket.canal == Mesa.Canal.DOMICILIO:
        if not all([ticket.cliente_id, ticket.cliente_nombre, ticket.cliente_domicilio]):
            raise ErrorVenta("Selecciona un cliente con domicilio antes de procesar la orden.")
        if ticket.cliente.comentarios_multiples:
            if not all([ticket.contacto_pedido_nombre, ticket.contacto_pedido_telefono]):
                raise ErrorVenta("Captura el nombre y teléfono del contacto para este pedido.")
        elif not ticket.cliente_telefono:
            raise ErrorVenta("Selecciona un cliente con teléfono antes de procesar la orden.")
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


@transaction.atomic
def cancelar_ticket(ticket):
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    if ticket.estado != Ticket.Estado.ABIERTO:
        raise ErrorVenta("Sólo se puede cancelar una orden que todavía está abierta.")
    ticket.partidas.all().delete()
    ticket.modificadores.all().delete()
    ticket.cliente = None
    ticket.telefono_cliente = None
    ticket.domicilio_cliente = None
    ticket.cliente_nombre = ""
    ticket.cliente_telefono = ""
    ticket.cliente_domicilio = ""
    ticket.cliente_referencia = ""
    ticket.contacto_pedido_nombre = ""
    ticket.contacto_pedido_telefono = ""
    ticket.comentario_general = ""
    ticket.entrega_aproximada = None
    ticket.estado = Ticket.Estado.CANCELADO
    ticket.cancelado_en = timezone.now()
    ticket.save(
        update_fields=[
            "cliente",
            "telefono_cliente",
            "domicilio_cliente",
            "cliente_nombre",
            "cliente_telefono",
            "cliente_domicilio",
            "cliente_referencia",
            "contacto_pedido_nombre",
            "contacto_pedido_telefono",
            "comentario_general",
            "entrega_aproximada",
            "estado",
            "cancelado_en",
            "actualizado_en",
        ]
    )
    _evento(ticket, "ticket.cancelado", {"mesa": ticket.mesa.nombre})
    return ticket
