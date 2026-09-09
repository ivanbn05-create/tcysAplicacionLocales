from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from personas.models import Rol, UsuarioPOS

from .models import (
    ConfiguracionSucursal,
    CorteCaja,
    CorteSucursal,
    LiquidacionRepartidor,
    Mesa,
    MovimientoCaja,
    ReporteAdministrativo,
    SucursalPedido,
    Ticket,
)
from .services import (
    ErrorVenta,
    cancelar_ticket,
    cobrar_ticket,
    completar_ticket_sucursal,
    guardar_ticket,
    registrar_evento,
)


CANALES_CAJA = (
    Mesa.Canal.COMEDOR,
    Mesa.Canal.LLEVAR,
    Mesa.Canal.DOMICILIO,
    Mesa.Canal.RECOGER,
    Mesa.Canal.SUCURSALES,
)
ESTADOS_ACTIVOS = (Ticket.Estado.ABIERTO, Ticket.Estado.PROCESADO, Ticket.Estado.COBRAR)


def _clave_cuatro_digitos(valor, etiqueta="La clave"):
    clave = str(valor or "").strip()
    if len(clave) != 4 or not clave.isdigit():
        raise ErrorVenta(f"{etiqueta} debe contener exactamente cuatro dígitos.")
    return clave


def configuracion_sucursal(sucursal):
    configuracion, creada = ConfiguracionSucursal.objects.get_or_create(
        sucursal=sucursal,
        defaults={"clave_administrador": make_password("1212")},
    )
    if creada:
        configuracion.refresh_from_db()
    return configuracion


def validar_clave_administrador(sucursal, clave):
    clave = _clave_cuatro_digitos(clave, "La clave de administrador")
    if not check_password(clave, configuracion_sucursal(sucursal).clave_administrador):
        raise ErrorVenta("La clave de administrador es incorrecta.")
    return True


@transaction.atomic
def cambiar_clave_administrador(sucursal, actual, nueva):
    validar_clave_administrador(sucursal, actual)
    nueva = _clave_cuatro_digitos(nueva, "La nueva clave")
    configuracion = ConfiguracionSucursal.objects.select_for_update().get(sucursal=sucursal)
    configuracion.clave_administrador = make_password(nueva)
    configuracion.save(update_fields=["clave_administrador", "actualizado_en"])


def _perfil_por_clave(sucursal, clave, tipos):
    clave = _clave_cuatro_digitos(clave, "El código")
    perfiles = UsuarioPOS.objects.select_related("rol").filter(
        sucursal=sucursal,
        activo=True,
        rol__tipo__in=tipos,
    )
    return next((perfil for perfil in perfiles if perfil.check_clave(clave)), None)


def identificar_mesero(sucursal, clave):
    perfil = _perfil_por_clave(
        sucursal,
        clave,
        (Rol.Tipo.MESERO, Rol.Tipo.ENCARGADO),
    )
    if perfil is None:
        raise ErrorVenta("El código no corresponde a un mesero activo.")
    return perfil


def _clave_usuario_disponible(sucursal, clave, excluir=None):
    perfiles = UsuarioPOS.objects.filter(sucursal=sucursal)
    if excluir:
        perfiles = perfiles.exclude(pk=excluir.pk)
    if any(perfil.check_clave(clave) for perfil in perfiles):
        raise ErrorVenta("Ese código ya está asignado a otra persona.")


def usuario_payload(usuario):
    return {
        "id": str(usuario.id),
        "nombre": usuario.nombre,
        "tipo": usuario.rol.tipo,
        "tipo_etiqueta": usuario.rol.get_tipo_display(),
        "activo": usuario.activo,
    }


@transaction.atomic
def guardar_usuario(sucursal, datos, usuario=None):
    nombre = str(datos.get("nombre", "")).strip()[:100]
    tipo = str(datos.get("tipo", ""))
    if not nombre:
        raise ErrorVenta("Escribe el nombre de la persona.")
    if tipo not in {Rol.Tipo.MESERO, Rol.Tipo.REPARTIDOR}:
        raise ErrorVenta("Selecciona Mesero o Repartidor.")
    rol, _ = Rol.objects.get_or_create(
        sucursal=sucursal,
        tipo=tipo,
        defaults={"nombre": Rol.Tipo(tipo).label},
    )
    if usuario is None:
        clave = _clave_cuatro_digitos(datos.get("clave"), "El código")
        _clave_usuario_disponible(sucursal, clave)
        usuario = UsuarioPOS(sucursal=sucursal, rol=rol, nombre=nombre)
        usuario.set_clave(clave)
    else:
        usuario = UsuarioPOS.objects.select_for_update().get(pk=usuario.pk, sucursal=sucursal)
        usuario.rol = rol
        usuario.nombre = nombre
        if datos.get("clave") not in (None, ""):
            clave = _clave_cuatro_digitos(datos.get("clave"), "El código")
            _clave_usuario_disponible(sucursal, clave, excluir=usuario)
            usuario.set_clave(clave)
    if "activo" in datos:
        usuario.activo = bool(datos["activo"])
    usuario.save()
    return usuario


def inicio_turno(sucursal, ahora=None):
    ultimo = CorteCaja.objects.filter(sucursal=sucursal).order_by("-fin").first()
    if ultimo:
        return ultimo.fin
    ahora = timezone.localtime(ahora or timezone.now())
    return timezone.make_aware(datetime.combine(ahora.date(), time.min))


def _tickets_turno(sucursal, ahora=None):
    inicio = inicio_turno(sucursal, ahora)
    return Ticket.objects.filter(
        sucursal=sucursal,
    ).filter(
        Q(creado_en__gte=inicio) | Q(activado_programado_en__gte=inicio),
    )


@transaction.atomic
def activar_programados(sucursal, ahora=None):
    if isinstance(ahora, datetime):
        instante = ahora
        if timezone.is_naive(instante):
            instante = timezone.make_aware(instante)
    elif isinstance(ahora, date):
        instante = timezone.make_aware(datetime.combine(ahora, time.max))
    else:
        instante = timezone.now()
    local = timezone.localtime(instante)
    programados = list(
        Ticket.objects.select_for_update()
        .filter(
            sucursal=sucursal,
            estado=Ticket.Estado.PROGRAMADO,
        )
        .filter(
            Q(fecha_programada__lt=local.date())
            | Q(
                fecha_programada=local.date(),
                hora_programada__lte=local.time(),
            )
            | Q(
                fecha_programada=local.date(),
                hora_programada__isnull=True,
            )
        )
        .order_by("fecha_programada", "hora_programada", "creado_en")
    )
    if not programados:
        return 0
    posiciones = list(
        Mesa.objects.select_for_update()
        .filter(sucursal=sucursal, canal=Mesa.Canal.DOMICILIO, activa=True)
        .order_by("orden", "nombre")
    )
    ocupadas = set(
        Ticket.objects.filter(mesa__in=posiciones, estado__in=ESTADOS_ACTIVOS).values_list(
            "mesa_id", flat=True
        )
    )
    libres = [posicion for posicion in posiciones if posicion.id not in ocupadas]
    activados = 0
    for ticket, posicion in zip(programados, libres):
        ahora = timezone.now()
        ticket.mesa = posicion
        ticket.canal = Mesa.Canal.DOMICILIO
        ticket.estado = Ticket.Estado.PROCESADO
        ticket.activado_programado_en = ahora
        guardar_ticket(ticket, ["mesa", "canal", "estado", "activado_programado_en"], limpiar_bloqueo=True)
        registrar_evento(
            ticket,
            "ticket.programado_activado",
            {
                "fecha_programada": ticket.fecha_programada.isoformat(),
                "hora_programada": ticket.hora_programada.isoformat() if ticket.hora_programada else "",
                "mesa": posicion.nombre,
            },
        )
        activados += 1
    return activados


@transaction.atomic
def programar_ticket(ticket, fecha_programada, hora_programada):
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    if ticket.canal != Mesa.Canal.DOMICILIO:
        raise ErrorVenta("Sólo los pedidos a domicilio pueden dejarse programados.")
    if ticket.estado != Ticket.Estado.PROCESADO or ticket.comanda_en_edicion:
        raise ErrorVenta("Primero procesa e imprime el pedido.")
    if ticket.liquidaciones_repartidor.exists() or ticket.cortes_caja.exists():
        raise ErrorVenta("El pedido ya pertenece a una liquidación o corte.")
    programado_para = timezone.make_aware(
        datetime.combine(fecha_programada, hora_programada),
        timezone.get_current_timezone(),
    )
    if programado_para <= timezone.now():
        raise ErrorVenta("La fecha y hora programadas deben ser posteriores al momento actual.")
    ticket.fecha_programada = fecha_programada
    ticket.hora_programada = hora_programada
    ticket.tipo_entrega = Ticket.TipoEntrega.PROGRAMADA
    ticket.entrega_aproximada = hora_programada
    ticket.activado_programado_en = None
    ticket.estado = Ticket.Estado.PROGRAMADO
    ticket.repartidor = None
    guardar_ticket(
        ticket,
        [
            "fecha_programada",
            "hora_programada",
            "tipo_entrega",
            "entrega_aproximada",
            "activado_programado_en",
            "estado",
            "repartidor",
        ],
        limpiar_bloqueo=True,
    )
    registrar_evento(
        ticket,
        "ticket.programado",
        {"programado_para": programado_para.isoformat()},
    )
    return ticket


@transaction.atomic
def reasignar_ticket(ticket, mesa_destino):
    ticket = Ticket.objects.select_for_update().select_related("cliente").get(pk=ticket.pk)
    mesa_destino = Mesa.objects.select_for_update().get(pk=mesa_destino.pk)
    if ticket.estado not in ESTADOS_ACTIVOS:
        raise ErrorVenta("El pedido ya no admite reasignación.")
    if mesa_destino.sucursal_id != ticket.sucursal_id or not mesa_destino.activa:
        raise ErrorVenta("La posición de destino no está disponible en esta sucursal.")
    if mesa_destino.canal == Mesa.Canal.SUCURSALES:
        raise ErrorVenta("Los pedidos de sucursal se reasignan desde su propio módulo.")
    if Ticket.objects.filter(mesa=mesa_destino, estado__in=ESTADOS_ACTIVOS).exclude(pk=ticket.pk).exists():
        raise ErrorVenta("La posición de destino ya está ocupada.")
    if mesa_destino.canal == Mesa.Canal.DOMICILIO and not all(
        [ticket.cliente_nombre, ticket.cliente_domicilio]
    ):
        raise ErrorVenta("El pedido necesita cliente y domicilio antes de cambiar a Domicilio.")
    anterior = ticket.mesa.nombre
    canal_anterior = ticket.canal
    ticket.mesa = mesa_destino
    ticket.canal = mesa_destino.canal
    if ticket.canal != Mesa.Canal.DOMICILIO:
        ticket.repartidor = None
    guardar_ticket(ticket, ["mesa", "canal", "repartidor"])
    registrar_evento(
        ticket,
        "ticket.reasignado",
        {
            "mesa_anterior": anterior,
            "mesa_nueva": mesa_destino.nombre,
            "canal_anterior": canal_anterior,
            "canal_nuevo": ticket.canal,
        },
    )
    return ticket


@transaction.atomic
def asignar_repartidor(ticket, repartidor):
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    repartidor = UsuarioPOS.objects.select_for_update().select_related("rol").get(pk=repartidor.pk)
    if (
        ticket.canal != Mesa.Canal.DOMICILIO
        or ticket.estado != Ticket.Estado.PROCESADO
        or ticket.comanda_en_edicion
    ):
        raise ErrorVenta("Sólo un domicilio procesado puede asignarse a repartidor.")
    if (
        repartidor.sucursal_id != ticket.sucursal_id
        or not repartidor.activo
        or repartidor.rol.tipo != Rol.Tipo.REPARTIDOR
    ):
        raise ErrorVenta("Selecciona un repartidor activo de esta sucursal.")
    if ticket.liquidaciones_repartidor.exists():
        raise ErrorVenta("El pedido ya pertenece a un total de repartidor.")
    ticket.repartidor = repartidor
    guardar_ticket(ticket, ["repartidor"])
    registrar_evento(ticket, "ticket.repartidor_asignado", {"repartidor_id": str(repartidor.id)})
    return ticket


@transaction.atomic
def aplicar_accion_tickets_lote(
    sucursal,
    accion,
    ticket_ids,
    forma_pago=None,
    repartidor=None,
):
    if accion not in {"cobrar", "completar_sucursales", "asignar_repartidor"}:
        raise ErrorVenta("La acción en lote no es válida.")
    if not ticket_ids:
        raise ErrorVenta("Selecciona al menos un pedido.")
    if len(ticket_ids) > 200:
        raise ErrorVenta("Se permiten como máximo 200 pedidos por operación.")
    tickets_encontrados = {
        ticket.id: ticket
        for ticket in Ticket.objects.select_for_update()
        .filter(sucursal=sucursal, id__in=ticket_ids)
        .prefetch_related("partidas")
    }
    if len(tickets_encontrados) != len(ticket_ids):
        raise ErrorVenta("Uno o más pedidos ya no están disponibles.")
    tickets = [tickets_encontrados[ticket_id] for ticket_id in ticket_ids]

    resultado = []
    if accion == "cobrar":
        forma_pago = forma_pago or Ticket.FormaPago.EFECTIVO
        if forma_pago not in Ticket.FormaPago.values:
            raise ErrorVenta("Selecciona una forma de pago válida.")
        if any(ticket.canal in {Mesa.Canal.DOMICILIO, Mesa.Canal.SUCURSALES} for ticket in tickets):
            raise ErrorVenta("La selección incluye pedidos que no se cobran con esta acción.")
        for ticket in tickets:
            resultado.append(cobrar_ticket(ticket, forma_pago, ticket.total))
    elif accion == "completar_sucursales":
        for ticket in tickets:
            resultado.append(completar_ticket_sucursal(ticket))
    else:
        if repartidor is None:
            raise ErrorVenta("Selecciona un repartidor.")
        for ticket in tickets:
            resultado.append(asignar_repartidor(ticket, repartidor))
    return resultado


@transaction.atomic
def aplicar_descuento(ticket, porcentaje):
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    try:
        porcentaje = Decimal(str(porcentaje)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ErrorVenta("El descuento no es válido.") from exc
    if not Decimal("0.00") <= porcentaje <= Decimal("100.00"):
        raise ErrorVenta("El descuento debe estar entre 0 y 100 por ciento.")
    if ticket.estado == Ticket.Estado.CANCELADO or ticket.liquidaciones_repartidor.exists() or ticket.cortes_caja.exists():
        raise ErrorVenta("El pedido ya fue liquidado o cerrado y no admite descuentos.")
    ticket.descuento_porcentaje = porcentaje
    guardar_ticket(ticket, ["descuento_porcentaje"])
    registrar_evento(ticket, "ticket.descuento", {"porcentaje": str(porcentaje)})
    return ticket


def cancelar_ticket_administrador(ticket):
    if ticket.liquidaciones_repartidor.exists() or ticket.cortes_caja.exists() or ticket.cortes_sucursal.exists():
        raise ErrorVenta("El pedido ya forma parte de un cierre y no puede cancelarse.")
    return cancelar_ticket(ticket, permitir_procesado=True)


def _dinero(valor):
    return Decimal(valor or 0).quantize(Decimal("0.01"))


@transaction.atomic
def crear_liquidacion_repartidor(sucursal, repartidor, fondo=Decimal("0.00")):
    try:
        fondo = _dinero(fondo)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ErrorVenta("El fondo no es válido.") from exc
    if fondo < 0 or fondo > Decimal("99999999.99"):
        raise ErrorVenta("El fondo debe ser un importe positivo o cero.")
    repartidor = UsuarioPOS.objects.select_for_update().select_related("rol").get(
        pk=repartidor.pk,
        sucursal=sucursal,
        activo=True,
        rol__tipo=Rol.Tipo.REPARTIDOR,
    )
    tickets = list(
        _tickets_turno(sucursal)
        .select_for_update()
        .select_related("repartidor")
        .prefetch_related("partidas")
        .filter(
            canal=Mesa.Canal.DOMICILIO,
            estado=Ticket.Estado.PROCESADO,
            repartidor=repartidor,
            liquidaciones_repartidor__isnull=True,
        )
        .order_by("folio")
    )
    if not tickets:
        raise ErrorVenta("El repartidor no tiene domicilios pendientes de liquidar.")
    if any(ticket.comanda_en_edicion for ticket in tickets):
        raise ErrorVenta("Procesa todas las comandas antes de liquidar al repartidor.")
    total_terminal = sum((ticket.total for ticket in tickets if ticket.terminal), Decimal("0.00"))
    total_efectivo = sum((ticket.total for ticket in tickets if not ticket.terminal), Decimal("0.00"))
    total_pedidos = total_terminal + total_efectivo
    datos_tickets = [
        {
            "folio": ticket.folio,
            "domicilio": ticket.cliente_domicilio or "Domicilio no capturado",
            "total": str(ticket.total),
            "terminal": ticket.terminal,
        }
        for ticket in tickets
    ]
    datos = {
        "titulo": "TOTAL DE REPARTIDOR",
        "repartidor": repartidor.nombre,
        "pedidos": datos_tickets,
        "cantidad_pedidos": len(tickets),
        "total_efectivo": str(total_efectivo),
        "total_terminal": str(total_terminal),
        "total_pedidos": str(total_pedidos),
        "fondo": str(fondo),
        "total_a_entregar": str(total_pedidos + fondo),
    }
    reporte = ReporteAdministrativo.objects.create(
        sucursal=sucursal,
        tipo=ReporteAdministrativo.Tipo.LIQUIDACION_REPARTIDOR,
        datos=datos,
    )
    liquidacion = LiquidacionRepartidor.objects.create(
        sucursal=sucursal,
        repartidor=repartidor,
        reporte=reporte,
        fondo=fondo,
        total_efectivo=total_efectivo,
        total_terminal=total_terminal,
        total_pedidos=total_pedidos,
        total_a_entregar=total_pedidos + fondo,
    )
    liquidacion.tickets.add(*tickets)
    ahora = timezone.now()
    for ticket in tickets:
        ticket.estado = Ticket.Estado.PAGADO
        ticket.forma_pago = (
            Ticket.FormaPago.TARJETA if ticket.terminal else Ticket.FormaPago.EFECTIVO
        )
        ticket.importe_recibido = ticket.total
        ticket.pagado_en = ahora
        guardar_ticket(
            ticket,
            ["estado", "forma_pago", "importe_recibido", "pagado_en"],
            limpiar_bloqueo=True,
        )
        registrar_evento(
            ticket,
            "ticket.liquidado_repartidor",
            {"liquidacion_id": str(liquidacion.id), "total": str(ticket.total)},
        )
    return liquidacion, reporte


def _tickets_reporte(sucursal):
    return (
        _tickets_turno(sucursal)
        .exclude(estado=Ticket.Estado.CANCELADO)
        .exclude(estado=Ticket.Estado.PROGRAMADO)
        .prefetch_related("partidas")
    )


def totales_parciales(sucursal):
    tickets = list(_tickets_reporte(sucursal))
    return {
        canal: sum((ticket.total for ticket in tickets if ticket.canal == canal), Decimal("0.00"))
        for canal in CANALES_CAJA
    }


@transaction.atomic
def crear_reporte_parcial(sucursal):
    totales = totales_parciales(sucursal)
    total = sum(totales.values(), Decimal("0.00"))
    reporte = ReporteAdministrativo.objects.create(
        sucursal=sucursal,
        tipo=ReporteAdministrativo.Tipo.PARCIAL,
        datos={
            "titulo": "REPORTE PARCIAL",
            "canales": {canal: str(valor) for canal, valor in totales.items()},
            "total": str(total),
            "inicio": inicio_turno(sucursal).isoformat(),
            "fin": timezone.now().isoformat(),
        },
    )
    return reporte


@transaction.atomic
def agregar_movimiento(sucursal, tipo, concepto, importe):
    tipo, concepto, importe = _datos_movimiento(tipo, concepto, importe)
    return MovimientoCaja.objects.create(
        sucursal=sucursal,
        tipo=tipo,
        concepto=concepto,
        importe=importe,
    )


def _datos_movimiento(tipo, concepto, importe):
    if tipo not in MovimientoCaja.Tipo.values:
        raise ErrorVenta("Selecciona si el movimiento es entrada o salida.")
    concepto = str(concepto or "").strip()[:180]
    if not concepto:
        raise ErrorVenta("Escribe el concepto del movimiento.")
    try:
        importe = _dinero(importe)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ErrorVenta("El importe no es válido.") from exc
    if importe <= 0 or importe > Decimal("99999999.99"):
        raise ErrorVenta("El importe debe ser mayor que cero.")
    return tipo, concepto, importe


@transaction.atomic
def actualizar_movimiento(sucursal, movimiento, tipo, concepto, importe):
    movimiento = MovimientoCaja.objects.select_for_update().get(
        pk=movimiento.pk,
        sucursal=sucursal,
    )
    if movimiento.cortes_caja.exists():
        raise ErrorVenta("El movimiento pertenece a un corte y ya no puede editarse.")
    tipo, concepto, importe = _datos_movimiento(tipo, concepto, importe)
    movimiento.tipo = tipo
    movimiento.concepto = concepto
    movimiento.importe = importe
    movimiento.save(update_fields=["tipo", "concepto", "importe"])
    return movimiento


@transaction.atomic
def eliminar_movimiento(sucursal, movimiento):
    movimiento = MovimientoCaja.objects.select_for_update().get(
        pk=movimiento.pk,
        sucursal=sucursal,
    )
    if movimiento.cortes_caja.exists():
        raise ErrorVenta("El movimiento pertenece a un corte y ya no puede eliminarse.")
    movimiento.delete()


def bloqueos_corte(sucursal):
    tickets = list(_tickets_reporte(sucursal).select_related("repartidor"))
    bloqueos = []
    ordinarios = [
        ticket
        for ticket in tickets
        if ticket.canal in {Mesa.Canal.COMEDOR, Mesa.Canal.LLEVAR, Mesa.Canal.RECOGER}
        and ticket.estado != Ticket.Estado.PAGADO
    ]
    if ordinarios:
        bloqueos.append(f"Faltan {len(ordinarios)} pedido(s) por cobrar.")
    domicilios_sin_repartidor = [
        ticket
        for ticket in tickets
        if ticket.canal == Mesa.Canal.DOMICILIO
        and ticket.estado != Ticket.Estado.PAGADO
        and not ticket.repartidor_id
    ]
    if domicilios_sin_repartidor:
        bloqueos.append(f"Faltan {len(domicilios_sin_repartidor)} domicilio(s) por asignar.")
    domicilios_sin_liquidar = [
        ticket
        for ticket in tickets
        if ticket.canal == Mesa.Canal.DOMICILIO
        and ticket.estado != Ticket.Estado.PAGADO
        and ticket.repartidor_id
        and not ticket.liquidaciones_repartidor.exists()
    ]
    if domicilios_sin_liquidar:
        bloqueos.append(f"Faltan {len(domicilios_sin_liquidar)} domicilio(s) en totales de repartidor.")
    sucursales_sin_completar = [
        ticket
        for ticket in tickets
        if ticket.canal == Mesa.Canal.SUCURSALES
        and ticket.estado != Ticket.Estado.PAGADO
    ]
    if sucursales_sin_completar:
        bloqueos.append(
            f"Faltan {len(sucursales_sin_completar)} pedido(s) de sucursal por completar."
        )
    return bloqueos


@transaction.atomic
def crear_corte_caja(sucursal):
    bloqueos = bloqueos_corte(sucursal)
    if bloqueos:
        raise ErrorVenta(" ".join(bloqueos))
    inicio = inicio_turno(sucursal)
    fin = timezone.now()
    tickets = list(_tickets_reporte(sucursal).select_for_update())
    movimientos = list(
        MovimientoCaja.objects.select_for_update().filter(
            sucursal=sucursal,
            creado_en__gte=inicio,
            cortes_caja__isnull=True,
        )
    )
    totales = {
        canal: sum((ticket.total for ticket in tickets if ticket.canal == canal), Decimal("0.00"))
        for canal in CANALES_CAJA
    }
    entradas = sum(
        (movimiento.importe for movimiento in movimientos if movimiento.tipo == MovimientoCaja.Tipo.ENTRADA),
        Decimal("0.00"),
    )
    salidas = sum(
        (movimiento.importe for movimiento in movimientos if movimiento.tipo == MovimientoCaja.Tipo.SALIDA),
        Decimal("0.00"),
    )
    total_ventas = sum(totales.values(), Decimal("0.00"))
    total_caja = total_ventas + entradas - salidas
    datos = {
        "titulo": "CORTE DE CAJA",
        "inicio": inicio.isoformat(),
        "fin": fin.isoformat(),
        "canales": {canal: str(valor) for canal, valor in totales.items()},
        "entradas": str(entradas),
        "salidas": str(salidas),
        "total_ventas": str(total_ventas),
        "total_caja": str(total_caja),
        "movimientos": [
            {"tipo": movimiento.tipo, "concepto": movimiento.concepto, "importe": str(movimiento.importe)}
            for movimiento in movimientos
        ],
    }
    reporte = ReporteAdministrativo.objects.create(
        sucursal=sucursal,
        tipo=ReporteAdministrativo.Tipo.CORTE_CAJA,
        datos=datos,
    )
    corte = CorteCaja.objects.create(
        sucursal=sucursal,
        inicio=inicio,
        fin=fin,
        reporte=reporte,
        totales_canales={canal: str(valor) for canal, valor in totales.items()},
        total_ventas=total_ventas,
        total_entradas=entradas,
        total_salidas=salidas,
        total_caja=total_caja,
    )
    corte.tickets.add(*tickets)
    corte.movimientos.add(*movimientos)
    return corte, reporte


@transaction.atomic
def crear_corte_sucursal(sucursal, cliente_sucursal):
    cliente_sucursal = SucursalPedido.objects.select_for_update().get(
        pk=cliente_sucursal.pk,
        sucursal=sucursal,
        activa=True,
    )
    tickets = list(
        Ticket.objects.select_for_update()
        .filter(
            sucursal=sucursal,
            canal=Mesa.Canal.SUCURSALES,
            mesa__cliente_sucursal=cliente_sucursal,
            estado__in=[Ticket.Estado.PROCESADO, Ticket.Estado.COBRAR],
            cortes_sucursal__isnull=True,
        )
        .prefetch_related("partidas")
        .order_by("folio")
    )
    if not tickets:
        raise ErrorVenta("La sucursal no tiene pedidos procesados pendientes de corte.")
    agrupadas = {}
    for ticket in tickets:
        for partida in ticket.partidas.all():
            clave = partida.nombre_producto
            fila = agrupadas.setdefault(
                clave,
                {"nombre": partida.nombre_producto, "cantidad": Decimal("0.000"), "importe": Decimal("0.00")},
            )
            fila["cantidad"] += partida.cantidad
            fila["importe"] += partida.importe
    partidas = [
        {"nombre": fila["nombre"], "cantidad": str(fila["cantidad"]), "importe": str(fila["importe"])}
        for fila in agrupadas.values()
    ]
    total = sum((ticket.total for ticket in tickets), Decimal("0.00"))
    reporte = ReporteAdministrativo.objects.create(
        sucursal=sucursal,
        tipo=ReporteAdministrativo.Tipo.CORTE_SUCURSAL,
        datos={
            "titulo": "CORTE DE SUCURSAL",
            "sucursal_cliente": cliente_sucursal.nombre,
            "tickets": [ticket.folio for ticket in tickets],
            "partidas": partidas,
            "total": str(total),
        },
    )
    corte = CorteSucursal.objects.create(
        sucursal=sucursal,
        cliente_sucursal=cliente_sucursal,
        reporte=reporte,
        partidas=partidas,
        total=total,
    )
    corte.tickets.add(*tickets)
    ahora = timezone.now()
    Ticket.objects.filter(pk__in=[ticket.pk for ticket in tickets]).update(
        estado=Ticket.Estado.PAGADO,
        pagado_en=ahora,
        actualizado_en=ahora,
        version_entidad=F("version_entidad") + 1,
        bloqueo_device_id="",
        bloqueo_operador=None,
        bloqueo_tomado_en=None,
        bloqueo_heartbeat_en=None,
        bloqueo_expira_en=None,
    )
    return corte, reporte


def _ticket_admin_payload(ticket):
    atendio = ticket.atendio.nombre if ticket.atendio_id else ""
    creado_en = ticket.creado_en.isoformat()
    return {
        "id": str(ticket.id),
        "folio": ticket.folio,
        "canal": ticket.canal,
        "canal_etiqueta": ticket.get_canal_display(),
        "estado": ticket.estado,
        "estado_etiqueta": ticket.get_estado_display(),
        "mesa_id": str(ticket.mesa_id),
        "mesa": ticket.mesa.nombre,
        "total": str(ticket.total),
        "descuento_porcentaje": str(ticket.descuento_porcentaje),
        "cliente_nombre": ticket.cliente_nombre,
        "cliente_domicilio": ticket.cliente_domicilio,
        "terminal": ticket.terminal,
        "repartidor_id": str(ticket.repartidor_id) if ticket.repartidor_id else "",
        "repartidor": ticket.repartidor.nombre if ticket.repartidor_id else "",
        "fecha_programada": ticket.fecha_programada.isoformat() if ticket.fecha_programada else "",
        "hora_programada": ticket.hora_programada.strftime("%H:%M") if ticket.hora_programada else "",
        "atendio_id": str(ticket.atendio_id) if ticket.atendio_id else "",
        "atendio": atendio,
        "creado_en": creado_en,
        "detalles": {
            "atendio": atendio,
            "creado": creado_en,
            "creado_en": creado_en,
        },
        "comanda_actual": ticket.comanda_actual,
        "comanda_en_edicion": ticket.comanda_en_edicion,
    }


def resumen_administrador(sucursal):
    activar_programados(sucursal)
    usuarios = list(
        UsuarioPOS.objects.select_related("rol")
        .filter(sucursal=sucursal, rol__tipo__in=[Rol.Tipo.MESERO, Rol.Tipo.REPARTIDOR])
        .order_by("rol__tipo", "nombre")
    )
    tickets_turno = list(
        _tickets_turno(sucursal)
        .select_related("mesa__cliente_sucursal", "repartidor", "atendio")
        .prefetch_related("partidas")
        .exclude(estado__in=[Ticket.Estado.CANCELADO, Ticket.Estado.PROGRAMADO])
        .order_by("creado_en")
    )
    programados = list(
        Ticket.objects.filter(
            sucursal=sucursal,
            estado=Ticket.Estado.PROGRAMADO,
        )
        .select_related("mesa__cliente_sucursal", "repartidor", "atendio")
        .prefetch_related("partidas")
        .order_by("fecha_programada", "hora_programada", "creado_en")
    )
    tickets = [*tickets_turno, *programados]
    activos = [ticket for ticket in tickets if ticket.estado in ESTADOS_ACTIVOS]
    activos_por_mesa = {ticket.mesa_id: ticket for ticket in activos}
    posiciones = [
        {
            "id": str(mesa.id),
            "nombre": mesa.nombre,
            "canal": mesa.canal,
            "canal_etiqueta": mesa.get_canal_display(),
            "disponible": mesa.id not in activos_por_mesa,
            "ticket_id": (
                str(activos_por_mesa[mesa.id].id) if mesa.id in activos_por_mesa else ""
            ),
            "ticket_folio": (
                activos_por_mesa[mesa.id].folio if mesa.id in activos_por_mesa else None
            ),
            "ticket_estado": (
                activos_por_mesa[mesa.id].estado if mesa.id in activos_por_mesa else ""
            ),
        }
        for mesa in Mesa.objects.filter(sucursal=sucursal, activa=True)
        .order_by("canal", "orden", "nombre")
    ]
    sucursales = list(
        SucursalPedido.objects.filter(sucursal=sucursal, activa=True).order_by("tipo", "nombre")
    )
    return {
        "sucursal": {"id": str(sucursal.id), "nombre": sucursal.nombre},
        "inicio_turno": inicio_turno(sucursal).isoformat(),
        "usuarios": [usuario_payload(usuario) for usuario in usuarios],
        "repartidores": [usuario_payload(usuario) for usuario in usuarios if usuario.rol.tipo == Rol.Tipo.REPARTIDOR and usuario.activo],
        "tickets": [_ticket_admin_payload(ticket) for ticket in tickets],
        "domicilios_sin_repartidor": [
            _ticket_admin_payload(ticket)
            for ticket in tickets
            if ticket.canal == Mesa.Canal.DOMICILIO
            and ticket.estado == Ticket.Estado.PROCESADO
            and not ticket.repartidor_id
        ],
        "programados": [
            _ticket_admin_payload(ticket) for ticket in programados
        ],
        "bloqueos_corte": bloqueos_corte(sucursal),
        "posiciones": posiciones,
        "posiciones_disponibles": [
            posicion for posicion in posiciones if posicion["disponible"]
        ],
        "movimientos": [
            {
                "id": str(movimiento.id),
                "tipo": movimiento.tipo,
                "concepto": movimiento.concepto,
                "importe": str(movimiento.importe),
                "creado_en": movimiento.creado_en.isoformat(),
            }
            for movimiento in MovimientoCaja.objects.filter(
                sucursal=sucursal,
                creado_en__gte=inicio_turno(sucursal),
            )[:50]
        ],
        "sucursales": [
            {
                "id": str(cliente.id),
                "nombre": cliente.nombre,
                "tipo": cliente.tipo,
                "pendientes": sum(
                    1
                    for ticket in tickets
                    if ticket.canal == Mesa.Canal.SUCURSALES
                    and ticket.mesa.cliente_sucursal_id == cliente.id
                    and ticket.estado in [Ticket.Estado.PROCESADO, Ticket.Estado.COBRAR]
                ),
            }
            for cliente in sucursales
        ],
        "totales_parciales": {canal: str(valor) for canal, valor in totales_parciales(sucursal).items()},
    }
