from decimal import Decimal

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from catalogo.models import Producto
from personas.models import Sucursal, UsuarioPOS

from .models import EventoOutbox, Mesa, ModificadorTicket, Partida, Ticket
from .normalizacion import normalizar_telefono
from .orden import PRODUCTOS_SIEMPRE_AL_FINAL
from .promociones import (
    PROMOCIONES,
    capacidad_componentes,
    configuracion_promocion,
    promocion_disponible,
    tipo_componente,
    validar_cupo_componente,
    validar_promociones,
)


class ErrorVenta(ValueError):
    pass


CANALES_CONVERSION = {
    Mesa.Canal.COMEDOR: Mesa.Canal.LLEVAR,
    Mesa.Canal.LLEVAR: Mesa.Canal.COMEDOR,
    Mesa.Canal.DOMICILIO: Mesa.Canal.RECOGER,
    Mesa.Canal.RECOGER: Mesa.Canal.DOMICILIO,
}


def _limpiar_cliente_ticket(ticket):
    ticket.cliente = None
    ticket.telefono_cliente = None
    ticket.domicilio_cliente = None
    ticket.cliente_nombre = ""
    ticket.cliente_telefono = ""
    ticket.cliente_domicilio = ""
    ticket.cliente_referencia = ""
    ticket.contacto_pedido_nombre = ""
    ticket.contacto_pedido_telefono = ""


def validar_limite_productos_por_nombre(ticket):
    if not ticket.captura_por_nombres:
        return
    partidas = list(ticket.partidas.select_related("producto__categoria").all())
    claves = {
        (partida.producto_id, partida.termino)
        for partida in partidas
        if not configuracion_promocion(partida.producto)
        and partida.producto.codigo.upper() not in PRODUCTOS_SIEMPRE_AL_FINAL
        and partida.producto.categoria.nombre.lower() != "bebidas"
    }
    if len(claves) > 4:
        raise ErrorVenta("Los pedidos por nombre admiten un máximo de 4 productos principales.")
    return partidas


def validar_captura_por_nombres(ticket):
    partidas = validar_limite_productos_por_nombre(ticket)
    if partidas is None:
        return
    nombres = ticket.nombres_comensales or {}
    comensales = {
        partida.comensal
        for partida in partidas
        if not configuracion_promocion(partida.producto)
    }
    faltantes = [numero for numero in sorted(comensales) if not str(nombres.get(str(numero), "")).strip()]
    if faltantes:
        raise ErrorVenta(
            "Captura el nombre de "
            + ("la persona" if len(faltantes) == 1 else "las personas")
            + ": "
            + ", ".join(str(numero) for numero in faltantes)
            + "."
        )


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


def _reasignar_componentes_promocion(ticket):
    """Distribuye automáticamente los productos capturados entre las promociones activas."""
    raices = list(
        Partida.objects.select_for_update()
        .select_related("producto")
        .filter(ticket=ticket, promocion_aplicada__isnull=True, producto__codigo__in=PROMOCIONES)
        .order_by("creada_en")
    )
    regulares = list(
        Partida.objects.select_for_update()
        .select_related("producto__categoria")
        .filter(ticket=ticket)
        .exclude(producto__codigo__in=PROMOCIONES)
        .order_by("creada_en")
    )

    # Primero normaliza los fragmentos creados por asignaciones anteriores. Así,
    # bajar o eliminar una promoción conserva los productos y sólo recalcula su cobro.
    consolidadas = {}
    for partida in regulares:
        clave = (partida.producto_id, partida.comensal, partida.termino)
        precio = partida.producto.precio_actual()
        if not precio:
            raise ErrorVenta(f"{partida.producto.nombre} ya no tiene un precio activo.")
        if clave not in consolidadas:
            partida.promocion_aplicada = None
            partida.precio_unitario = precio.importe
            partida.save(update_fields=["promocion_aplicada", "precio_unitario"])
            consolidadas[clave] = partida
            continue
        principal = consolidadas[clave]
        principal.cantidad += partida.cantidad
        principal.save(update_fields=["cantidad"])
        partida.delete()

    disponibles = list(consolidadas.values())
    for raiz in raices:
        for tipo, requerida in capacidad_componentes(raiz).items():
            faltante = requerida
            for partida in disponibles:
                if faltante <= 0:
                    break
                if partida.promocion_aplicada_id or tipo_componente(partida.producto) != tipo:
                    continue
                if partida.cantidad <= faltante:
                    partida.promocion_aplicada = raiz
                    partida.precio_unitario = Decimal("0.00")
                    partida.save(update_fields=["promocion_aplicada", "precio_unitario"])
                    faltante -= partida.cantidad
                    continue

                # Una misma captura puede contener unidades promocionales y normales.
                # Se divide internamente sin mostrar un modo de captura diferente.
                promocional = Partida.objects.create(
                    sucursal=partida.sucursal,
                    ticket=partida.ticket,
                    producto=partida.producto,
                    promocion_aplicada=raiz,
                    comensal=partida.comensal,
                    cantidad=faltante,
                    precio_unitario=Decimal("0.00"),
                    nombre_producto=partida.nombre_producto,
                    nombre_corto=partida.nombre_corto,
                    termino=partida.termino,
                    comentario=partida.comentario,
                    procesada=partida.procesada,
                )
                partida.cantidad -= faltante
                partida.save(update_fields=["cantidad"])
                disponibles.append(promocional)
                faltante = Decimal("0")


@transaction.atomic
def abrir_ticket(mesa):
    mesa = Mesa.objects.select_for_update().get(pk=mesa.pk)
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
def convertir_tipo_ticket(ticket, canal_destino):
    ticket = (
        Ticket.objects.select_for_update()
        .select_related("mesa", "cliente")
        .get(pk=ticket.pk)
    )
    if ticket.estado != Ticket.Estado.ABIERTO:
        raise ErrorVenta("Sólo una orden abierta puede cambiar de tipo.")
    if CANALES_CONVERSION.get(ticket.canal) != canal_destino:
        raise ErrorVenta("El cambio de tipo solicitado no es válido para esta orden.")

    mesas = list(
        Mesa.objects.select_for_update()
        .filter(sucursal=ticket.sucursal, canal=canal_destino, activa=True)
        .order_by("orden", "nombre")
    )
    ocupadas = set(
        Ticket.objects.filter(
            mesa__in=mesas,
            estado__in=[Ticket.Estado.ABIERTO, Ticket.Estado.PROCESADO, Ticket.Estado.COBRAR],
        ).values_list("mesa_id", flat=True)
    )
    mesa_destino = next((mesa for mesa in mesas if mesa.id not in ocupadas), None)
    if not mesa_destino:
        raise ErrorVenta(f"No hay posiciones de {Mesa.Canal(canal_destino).label.lower()} disponibles.")

    canal_anterior = ticket.canal
    mesa_anterior = ticket.mesa
    if canal_anterior == Mesa.Canal.DOMICILIO and canal_destino == Mesa.Canal.RECOGER:
        nombre = ticket.contacto_pedido_nombre or ticket.cliente_nombre
        telefono = ticket.contacto_pedido_telefono or ticket.cliente_telefono
        _limpiar_cliente_ticket(ticket)
        ticket.cliente_nombre = nombre
        ticket.cliente_telefono = telefono
    elif canal_anterior == Mesa.Canal.RECOGER and canal_destino == Mesa.Canal.DOMICILIO:
        _limpiar_cliente_ticket(ticket)
    elif canal_anterior == Mesa.Canal.LLEVAR and canal_destino == Mesa.Canal.COMEDOR:
        ticket.cliente_nombre = ""
        ticket.cliente_telefono = ""

    ticket.mesa = mesa_destino
    ticket.canal = canal_destino
    ticket.save()
    _evento(
        ticket,
        "ticket.tipo_convertido",
        {
            "canal_anterior": canal_anterior,
            "canal_nuevo": canal_destino,
            "mesa_anterior": mesa_anterior.nombre,
            "mesa_nueva": mesa_destino.nombre,
        },
    )
    return ticket


@transaction.atomic
def agregar_partida(
    ticket,
    producto,
    comensal=1,
    cantidad=Decimal("1.000"),
    termino=None,
    promocion_aplicada=None,
):
    if ticket.estado != Ticket.Estado.ABIERTO:
        raise ErrorVenta("La orden ya fue procesada; no admite nuevas partidas.")
    cantidad = Decimal(str(cantidad))
    if cantidad != cantidad.to_integral_value() or not 1 <= cantidad <= 99:
        raise ErrorVenta("La cantidad debe ser un entero entre 1 y 99.")
    precio = producto.precio_actual()
    if not producto.activo or not precio:
        raise ErrorVenta("El producto no tiene un precio activo.")
    configuracion = configuracion_promocion(producto)
    if configuracion and promocion_aplicada:
        raise ErrorVenta("Una promoción no puede ser componente de otra promoción.")
    if configuracion and not promocion_disponible(producto, timezone.localdate()):
        raise ErrorVenta(f"La promoción {producto.codigo} no está disponible el día de hoy.")
    if promocion_aplicada and (
        promocion_aplicada.ticket_id != ticket.id or not configuracion_promocion(promocion_aplicada.producto)
    ):
        raise ErrorVenta("La promoción seleccionada no pertenece a esta orden.")
    if promocion_aplicada:
        try:
            validar_cupo_componente(promocion_aplicada, producto, cantidad)
        except ValueError as exc:
            raise ErrorVenta(str(exc)) from exc
    termino, nombre_producto, nombre_corto = _datos_termino(producto, termino)
    partida = Partida.objects.create(
        sucursal=ticket.sucursal,
        ticket=ticket,
        producto=producto,
        promocion_aplicada=None,
        comensal=comensal,
        cantidad=cantidad,
        precio_unitario=precio.importe,
        nombre_producto=nombre_producto,
        nombre_corto=nombre_corto,
        termino=termino,
    )
    _reasignar_componentes_promocion(ticket)
    if ticket.captura_por_nombres:
        validar_limite_productos_por_nombre(ticket)
    partida = (
        Partida.objects.filter(pk=partida.pk).first()
        or Partida.objects.filter(
            ticket=ticket,
            producto=producto,
            comensal=comensal,
            termino=termino,
        ).order_by("creada_en").first()
    )
    _evento(ticket, "ticket.partida_agregada", {"partida_id": str(partida.id), "producto_id": str(producto.id)})
    return partida


@transaction.atomic
def actualizar_partida(partida, cantidad, termino=None, validar_componente=True):
    if partida.ticket.estado != Ticket.Estado.ABIERTO:
        raise ErrorVenta("La orden ya fue procesada.")
    cantidad = Decimal(str(cantidad))
    if cantidad <= 0:
        ticket = partida.ticket
        partida_id = str(partida.id)
        partida.delete()
        _reasignar_componentes_promocion(ticket)
        _evento(ticket, "ticket.partida_eliminada", {"partida_id": partida_id})
        return None
    if cantidad != cantidad.to_integral_value() or cantidad > 99:
        raise ErrorVenta("La cantidad debe ser un entero entre 1 y 99.")
    partida.cantidad = cantidad
    campos = ["cantidad"]
    if termino is not None:
        partida.termino, partida.nombre_producto, partida.nombre_corto = _datos_termino(partida.producto, termino)
        campos.extend(["termino", "nombre_producto", "nombre_corto"])
    partida.save(update_fields=campos)
    _reasignar_componentes_promocion(partida.ticket)
    if partida.ticket.captura_por_nombres:
        validar_limite_productos_por_nombre(partida.ticket)
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
        _reasignar_componentes_promocion(ticket)
        _evento(ticket, "ticket.partidas_eliminadas", {"partida_ids": ids})
        return None
    cantidad = Decimal(str(cantidad))
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
    duplicadas = partidas[1:] + duplicadas_destino
    if principal.promocion_aplicada_id:
        precio = principal.producto.precio_actual()
        if not precio:
            raise ErrorVenta("El producto no tiene un precio activo.")
        principal.promocion_aplicada = None
        principal.precio_unitario = precio.importe
        principal.save(update_fields=["promocion_aplicada", "precio_unitario"])
    if duplicadas:
        Partida.objects.filter(id__in=[partida.id for partida in duplicadas]).delete()
    principal = actualizar_partida(principal, cantidad, termino, validar_componente=False)
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
        ticket.modificadores.filter(comensal=comensal).delete()
        if ticket.comentarios_generales:
            ticket.comentarios_generales = []
            ticket.save(update_fields=["comentarios_generales", "actualizado_en"])
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
def alternar_comentario_general(ticket, codigo, nombre):
    if ticket.estado != Ticket.Estado.ABIERTO:
        raise ErrorVenta("La orden ya fue procesada.")
    comentarios = list(ticket.comentarios_generales or [])
    indice = next((i for i, item in enumerate(comentarios) if item.get("codigo") == codigo), None)
    if indice is None:
        comentarios.append({"codigo": codigo, "nombre": nombre})
        activo = True
        ticket.modificadores.all().delete()
    else:
        comentarios.pop(indice)
        activo = False
    ticket.comentarios_generales = comentarios
    ticket.save(update_fields=["comentarios_generales", "actualizado_en"])
    _evento(ticket, "ticket.comentario_general", {"codigo": codigo, "activo": activo})
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
    if comensales_con_producto and ticket.comentarios_generales:
        ticket.comentarios_generales = []
        ticket.save(update_fields=["comentarios_generales", "actualizado_en"])
    for comensal in comensales:
        if comensal not in comensales_con_producto:
            continue
        ModificadorTicket.objects.filter(ticket=ticket, comensal=comensal).exclude(codigo=codigo).delete()
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
    try:
        validar_promociones(ticket)
    except ValueError as exc:
        raise ErrorVenta(str(exc)) from exc
    if ticket.canal == Mesa.Canal.DOMICILIO:
        if not all([ticket.cliente_id, ticket.cliente_nombre, ticket.cliente_domicilio]):
            raise ErrorVenta("Selecciona un cliente con domicilio antes de procesar la orden.")
        if ticket.cliente.comentarios_multiples:
            if not all([ticket.contacto_pedido_nombre, ticket.contacto_pedido_telefono]):
                raise ErrorVenta("Captura el nombre y teléfono del contacto para este pedido.")
        elif not ticket.cliente_telefono:
            raise ErrorVenta("Selecciona un cliente con teléfono antes de procesar la orden.")
    elif ticket.canal == Mesa.Canal.RECOGER:
        if not ticket.cliente_nombre.strip():
            raise ErrorVenta("Captura el nombre del cliente que recogerá el pedido.")
        if len(normalizar_telefono(ticket.cliente_telefono)) < 7:
            raise ErrorVenta("Captura un celular válido para el pedido a recoger.")
    elif ticket.canal == Mesa.Canal.LLEVAR and not ticket.cliente_nombre.strip():
        raise ErrorVenta("Captura el nombre del cliente para llevar.")
    validar_captura_por_nombres(ticket)
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
    _limpiar_cliente_ticket(ticket)
    ticket.comentario_general = ""
    ticket.comentarios_generales = []
    ticket.salsas_verduras = []
    ticket.tipo_entrega = Ticket.TipoEntrega.APROXIMADA
    ticket.entrega_aproximada = None
    ticket.terminal = False
    ticket.paga_con = None
    ticket.captura_por_nombres = False
    ticket.nombres_comensales = {}
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
            "comentarios_generales",
            "salsas_verduras",
            "tipo_entrega",
            "entrega_aproximada",
            "terminal",
            "paga_con",
            "captura_por_nombres",
            "nombres_comensales",
            "estado",
            "cancelado_en",
            "actualizado_en",
        ]
    )
    _evento(ticket, "ticket.cancelado", {"mesa": ticket.mesa.nombre})
    return ticket
