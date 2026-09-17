from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from personas.models import Rol, UsuarioPOS
from personas.modulos import modulo_habilitado

from .models import (
    ConfiguracionSucursal,
    ControlEfectivoDia,
    CorteCaja,
    CorteSucursal,
    EventoOutbox,
    LiquidacionRepartidor,
    Mesa,
    MovimientoCaja,
    ReporteAdministrativo,
    SolicitudRepeticionTicket,
    SucursalPedido,
    Ticket,
)
from .purgas_fisicas import (
    REFERENCIA_CORTE,
    crear_solicitud_archivos,
    procesar_solicitud_archivos,
)
from .services import (
    ErrorVenta,
    cancelar_ticket,
    cobrar_ticket,
    completar_ticket_sucursal,
    guardar_ticket,
    registrar_evento,
)


CANALES_PARCIAL = (
    Mesa.Canal.COMEDOR,
    Mesa.Canal.LLEVAR,
    Mesa.Canal.DOMICILIO,
    Mesa.Canal.RECOGER,
)
CANALES_CAJA = (*CANALES_PARCIAL, Mesa.Canal.SUCURSALES)
ESTADOS_ACTIVOS = (Ticket.Estado.ABIERTO, Ticket.Estado.PROCESADO, Ticket.Estado.COBRAR)


def _clave_cuatro_digitos(valor, etiqueta="La clave"):
    clave = str(valor or "").strip()
    if len(clave) != 4 or not clave.isdigit():
        raise ErrorVenta(f"{etiqueta} debe contener exactamente cuatro dígitos.")
    return clave


def configuracion_sucursal(sucursal):
    try:
        return ConfiguracionSucursal.objects.get(sucursal=sucursal)
    except ConfiguracionSucursal.DoesNotExist as exc:
        raise ErrorVenta(
            "La clave de administrador no está configurada. "
            "Ejecuta el aprovisionamiento inicial de la sucursal."
        ) from exc


def _perfil_por_clave(sucursal, clave, tipos=None):
    clave = _clave_cuatro_digitos(clave, "El código")
    perfiles = UsuarioPOS.objects.select_related("rol").filter(
        sucursal=sucursal,
        activo=True,
    )
    if tipos is not None:
        perfiles = perfiles.filter(rol__tipo__in=tipos)
    return next((perfil for perfil in perfiles if perfil.check_clave(clave)), None)


def validar_clave_administrador(sucursal, clave):
    clave = _clave_cuatro_digitos(clave, "La clave de administrador")
    if not check_password(clave, configuracion_sucursal(sucursal).clave_administrador):
        raise ErrorVenta("La clave de administrador es incorrecta.")
    return True


def autenticar_acceso_administrador(sucursal, clave):
    clave = _clave_cuatro_digitos(clave, "La clave de acceso")
    if check_password(clave, configuracion_sucursal(sucursal).clave_administrador):
        return "administrador", None
    perfil = _perfil_por_clave(sucursal, clave, (Rol.Tipo.ELEVADO,))
    if perfil is None:
        raise ErrorVenta("La clave de acceso administrativo es incorrecta.")
    return "elevado", perfil


def validar_clave_administrativa(sucursal, clave):
    autenticar_acceso_administrador(sucursal, clave)
    return True


def _clave_usuario_disponible(sucursal, clave, excluir=None, incluir_administrador=True):
    perfiles = UsuarioPOS.objects.filter(sucursal=sucursal)
    if excluir:
        perfiles = perfiles.exclude(pk=excluir.pk)
    if any(perfil.check_clave(clave) for perfil in perfiles):
        raise ErrorVenta("Ese código ya está asignado a otra persona.")
    if (
        incluir_administrador
        and check_password(clave, configuracion_sucursal(sucursal).clave_administrador)
    ):
        raise ErrorVenta("Ese código está reservado para el administrador.")


@transaction.atomic
def cambiar_clave_administrador(sucursal, actual, nueva):
    actual = _clave_cuatro_digitos(actual, "La clave de administrador")
    nueva = _clave_cuatro_digitos(nueva, "La nueva clave")
    configuracion = ConfiguracionSucursal.objects.select_for_update().get(sucursal=sucursal)
    if not check_password(actual, configuracion.clave_administrador):
        raise ErrorVenta("La clave de administrador es incorrecta.")
    _clave_usuario_disponible(
        sucursal,
        nueva,
        incluir_administrador=False,
    )
    configuracion.clave_administrador = make_password(nueva)
    configuracion.save(update_fields=["clave_administrador", "actualizado_en"])


def identificar_usuario_ventas(sucursal, clave, perfil_administrador=None):
    clave = _clave_cuatro_digitos(clave, "El código")
    perfil = _perfil_por_clave(sucursal, clave)
    if perfil is not None:
        return perfil
    if check_password(clave, configuracion_sucursal(sucursal).clave_administrador):
        if (
            perfil_administrador is not None
            and perfil_administrador.sucursal_id == sucursal.id
            and perfil_administrador.activo
        ):
            return perfil_administrador
        perfil = (
            UsuarioPOS.objects.select_related("rol")
            .filter(
                sucursal=sucursal,
                activo=True,
                rol__tipo=Rol.Tipo.ENCARGADO,
            )
            .order_by("creado_en", "id")
            .first()
        )
        if perfil is not None:
            return perfil
        raise ErrorVenta(
            "El administrador no tiene un perfil POS activo para operar Ventas."
        )
    raise ErrorVenta("El código no corresponde a un usuario activo.")


def identificar_mesero(sucursal, clave):
    """Alias conservado para integraciones internas de versiones anteriores."""

    return identificar_usuario_ventas(sucursal, clave)


def usuario_payload(usuario):
    return {
        "id": str(usuario.id),
        "nombre": usuario.nombre,
        "tipo": usuario.rol.tipo,
        "tipo_etiqueta": (
            "Operador principal"
            if usuario.rol.tipo == Rol.Tipo.ENCARGADO
            else usuario.rol.get_tipo_display()
        ),
        "activo": usuario.activo,
    }


@transaction.atomic
def guardar_usuario(sucursal, datos, usuario=None):
    nombre = str(datos.get("nombre", "")).strip()[:100]
    tipo = str(datos.get("tipo", ""))
    if not nombre:
        raise ErrorVenta("Escribe el nombre de la persona.")
    if usuario is not None:
        usuario = (
            UsuarioPOS.objects.select_for_update()
            .select_related("rol")
            .get(pk=usuario.pk, sucursal=sucursal)
        )
    es_operador_principal = bool(
        usuario is not None and usuario.rol.tipo == Rol.Tipo.ENCARGADO
    )
    if es_operador_principal and tipo != Rol.Tipo.ENCARGADO:
        raise ErrorVenta("El operador principal conserva su función.")
    tipos_permitidos = {
        Rol.Tipo.ELEVADO,
        Rol.Tipo.MESERO,
        Rol.Tipo.REPARTIDOR,
    }
    if es_operador_principal:
        tipos_permitidos.add(Rol.Tipo.ENCARGADO)
    if tipo not in tipos_permitidos:
        raise ErrorVenta("Selecciona Elevado, Mesero o Repartidor.")
    permisos_elevados = tipo == Rol.Tipo.ELEVADO
    rol, _ = Rol.objects.get_or_create(
        sucursal=sucursal,
        tipo=tipo,
        defaults={
            "nombre": Rol.Tipo(tipo).label,
            "puede_cobrar": permisos_elevados,
            "puede_reimprimir": permisos_elevados,
            "puede_cancelar": permisos_elevados,
            "puede_sincronizar": permisos_elevados,
        },
    )
    if usuario is None:
        clave = _clave_cuatro_digitos(datos.get("clave"), "El código")
        _clave_usuario_disponible(sucursal, clave)
        usuario = UsuarioPOS(sucursal=sucursal, rol=rol, nombre=nombre)
        usuario.set_clave(clave)
    else:
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


def _limite_turno(sucursal, ahora=None):
    cortes = CorteCaja.objects.filter(sucursal=sucursal)
    if ahora is not None:
        instante = ahora
        if isinstance(instante, date) and not isinstance(instante, datetime):
            instante = datetime.combine(instante, time.max)
        if timezone.is_naive(instante):
            instante = timezone.make_aware(instante)
        cortes = cortes.filter(fin__lte=instante)
    ultimo = cortes.order_by("-fin").first()
    return ultimo.fin if ultimo else None


def _tickets_turno(sucursal, ahora=None):
    limite = _limite_turno(sucursal, ahora)
    tickets = Ticket.objects.filter(sucursal=sucursal)
    if limite is not None:
        tickets = tickets.filter(
            Q(creado_en__gt=limite) | Q(activado_programado_en__gt=limite),
        )
    return tickets


def inicio_turno(sucursal, ahora=None):
    """Primer pedido del turno; ``None`` hasta que exista uno tras el corte."""

    limite = _limite_turno(sucursal, ahora)
    candidatos = []
    for creado_en, activado_programado_en, tipo_entrega in _tickets_turno(
        sucursal, ahora
    ).values_list("creado_en", "activado_programado_en", "tipo_entrega"):
        if activado_programado_en is not None:
            if limite is None or activado_programado_en > limite:
                candidatos.append(activado_programado_en)
            continue
        if tipo_entrega == Ticket.TipoEntrega.PROGRAMADA:
            # Crear o cancelar una reserva futura no inicia la operación. El
            # instante operativo se fija cuando se activa y se conserva luego.
            continue
        if limite is None or creado_en > limite:
            candidatos.append(creado_en)
    return min(candidatos) if candidatos else None


def _movimientos_turno(sucursal):
    movimientos = MovimientoCaja.objects.filter(sucursal=sucursal)
    limite = _limite_turno(sucursal)
    if limite is not None:
        movimientos = movimientos.filter(creado_en__gt=limite)
    return movimientos


def _posicion_libre(sucursal, canal, ticket=None):
    posiciones = list(
        Mesa.objects.select_for_update()
        .filter(sucursal=sucursal, canal=canal, activa=True)
        .order_by("orden", "nombre")
    )
    ocupadas = set(
        Ticket.objects.filter(
            mesa__in=posiciones,
            estado__in=ESTADOS_ACTIVOS,
        )
        .exclude(pk=getattr(ticket, "pk", None))
        .values_list("mesa_id", flat=True)
    )
    return next(
        (posicion for posicion in posiciones if posicion.id not in ocupadas),
        None,
    )


@transaction.atomic
def activar_programados(sucursal, ahora=None):
    # También se invoca al consultar el estado: el cierre pendiente deja
    # PROGRAMADO intacto y no convierte una lectura en un error 500.
    from .consolidacion import exigir_mes_operativo

    try:
        exigir_mes_operativo(sucursal)
    except ErrorVenta:
        return 0
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
        .filter(sucursal=sucursal, estado=Ticket.Estado.PROGRAMADO)
        .filter(
            Q(fecha_programada__lt=local.date())
            | Q(fecha_programada=local.date(), hora_programada__lte=local.time())
            | Q(fecha_programada=local.date(), hora_programada__isnull=True)
        )
        .order_by("fecha_programada", "hora_programada", "creado_en")
    )
    activados = 0
    for ticket in programados:
        posicion = _posicion_libre(sucursal, ticket.canal, ticket=ticket)
        if posicion is None:
            continue
        activado_en = timezone.now()
        ticket.mesa = posicion
        ticket.estado = Ticket.Estado.PROCESADO
        ticket.activado_programado_en = activado_en
        guardar_ticket(
            ticket,
            ["mesa", "estado", "activado_programado_en"],
            limpiar_bloqueo=True,
        )
        registrar_evento(
            ticket,
            "ticket.programado_activado",
            {
                "fecha_programada": ticket.fecha_programada.isoformat(),
                "hora_programada": (
                    ticket.hora_programada.isoformat()
                    if ticket.hora_programada
                    else ""
                ),
                "mesa": posicion.nombre,
                "canal": ticket.canal,
            },
        )
        activados += 1
    return activados


@transaction.atomic
def programar_ticket(ticket, fecha_programada, hora_programada):
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    if ticket.canal not in {Mesa.Canal.DOMICILIO, Mesa.Canal.RECOGER}:
        raise ErrorVenta("Sólo Domicilio y Recoger pueden dejarse programados.")
    if ticket.estado not in {Ticket.Estado.PROCESADO, Ticket.Estado.PROGRAMADO}:
        raise ErrorVenta("Primero procesa e imprime el pedido.")
    if ticket.estado == Ticket.Estado.PROCESADO and ticket.comanda_en_edicion:
        raise ErrorVenta("Primero procesa e imprime el pedido.")
    if ticket.liquidaciones_repartidor.exists() or ticket.cortes_caja.exists():
        raise ErrorVenta("El pedido ya pertenece a una liquidación o corte.")
    programado_para = timezone.make_aware(
        datetime.combine(fecha_programada, hora_programada),
        timezone.get_current_timezone(),
    )
    if programado_para <= timezone.now():
        raise ErrorVenta(
            "La fecha y hora programadas deben ser posteriores al momento actual."
        )
    reprogramado = ticket.estado == Ticket.Estado.PROGRAMADO
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
        "ticket.programado_reprogramado" if reprogramado else "ticket.programado",
        {"programado_para": programado_para.isoformat(), "canal": ticket.canal},
    )
    return ticket


@transaction.atomic
def desprogramar_ticket(ticket):
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    if ticket.estado != Ticket.Estado.PROGRAMADO:
        raise ErrorVenta("El pedido ya no está programado.")
    posicion = _posicion_libre(ticket.sucursal, ticket.canal, ticket=ticket)
    if posicion is None:
        raise ErrorVenta(
            f"No hay posiciones de {ticket.get_canal_display().lower()} disponibles."
        )
    ticket.mesa = posicion
    ticket.estado = Ticket.Estado.PROCESADO
    ticket.tipo_entrega = Ticket.TipoEntrega.APROXIMADA
    ticket.fecha_programada = None
    ticket.hora_programada = None
    ticket.activado_programado_en = timezone.now()
    if ticket.canal == Mesa.Canal.DOMICILIO:
        ticket.entrega_aproximada = (
            timezone.localtime(timezone.now()) + timedelta(minutes=50)
        ).time().replace(second=0, microsecond=0)
    else:
        ticket.entrega_aproximada = None
    guardar_ticket(
        ticket,
        [
            "mesa",
            "estado",
            "tipo_entrega",
            "fecha_programada",
            "hora_programada",
            "activado_programado_en",
            "entrega_aproximada",
        ],
        limpiar_bloqueo=True,
    )
    registrar_evento(
        ticket,
        "ticket.programado_retirado",
        {"mesa": posicion.nombre, "canal": ticket.canal},
    )
    return ticket


@transaction.atomic
def eliminar_ticket_programado(ticket):
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    if ticket.estado != Ticket.Estado.PROGRAMADO:
        raise ErrorVenta("Sólo se puede eliminar una reserva todavía programada.")
    SolicitudRepeticionTicket.objects.filter(
        Q(ticket_origen=ticket) | Q(ticket_nuevo=ticket)
    ).delete()
    EventoOutbox.objects.filter(
        agregado="ticket",
        agregado_id=ticket.id,
    ).delete()
    ticket.delete()

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


def cancelar_ticket_administrador(ticket, cancelado_por=None):
    if ticket.liquidaciones_repartidor.exists() or ticket.cortes_caja.exists() or ticket.cortes_sucursal.exists():
        raise ErrorVenta("El pedido ya forma parte de un cierre y no puede cancelarse.")
    return cancelar_ticket(
        ticket,
        permitir_procesado=True,
        cancelado_por=cancelado_por,
        cancelado_por_nombre=(
            "" if cancelado_por is not None else "Administrador maestro"
        ),
    )


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
    totales_completos = totales_parciales(sucursal)
    totales = {canal: totales_completos[canal] for canal in CANALES_PARCIAL}
    total = sum(totales.values(), Decimal("0.00"))
    inicio = inicio_turno(sucursal)
    reporte = ReporteAdministrativo.objects.create(
        sucursal=sucursal,
        tipo=ReporteAdministrativo.Tipo.PARCIAL,
        datos={
            "titulo": "REPORTE PARCIAL",
            "canales": {canal: str(valor) for canal, valor in totales.items()},
            "total": str(total),
            "inicio": inicio.isoformat() if inicio else None,
            "fin": timezone.now().isoformat(),
        },
    )
    return reporte


def _conteo_vacio():
    return {denominacion: 0 for denominacion in ControlEfectivoDia.DENOMINACIONES}


def _normalizar_conteo(valores, etiqueta):
    if valores in (None, ""):
        valores = {}
    if not isinstance(valores, dict):
        raise ErrorVenta(f"{etiqueta} no tiene un formato válido.")
    desconocidas = set(valores) - set(ControlEfectivoDia.DENOMINACIONES)
    if desconocidas:
        raise ErrorVenta(f"{etiqueta} contiene denominaciones desconocidas.")
    resultado = _conteo_vacio()
    for denominacion in ControlEfectivoDia.DENOMINACIONES:
        valor = valores.get(denominacion, 0)
        try:
            cantidad = int(valor)
        except (TypeError, ValueError) as exc:
            raise ErrorVenta(
                f"La cantidad de {denominacion} en {etiqueta.lower()} no es válida."
            ) from exc
        if str(valor).strip() not in {str(cantidad), f"{cantidad}.0"}:
            raise ErrorVenta(
                f"La cantidad de {denominacion} debe ser un número entero."
            )
        if not 0 <= cantidad <= 99999:
            raise ErrorVenta(
                f"La cantidad de {denominacion} debe estar entre 0 y 99999."
            )
        resultado[denominacion] = cantidad
    return resultado


def _total_conteo(valores):
    return sum(
        (
            Decimal(denominacion) * Decimal(int(valores.get(denominacion, 0)))
            for denominacion in ControlEfectivoDia.DENOMINACIONES
        ),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))


def _normalizar_apps(valores):
    if valores in (None, ""):
        valores = {}
    if not isinstance(valores, dict):
        raise ErrorVenta("Las ventas de Apps no tienen un formato válido.")
    desconocidas = set(valores) - set(ControlEfectivoDia.APPS)
    if desconocidas:
        raise ErrorVenta("Las ventas de Apps contienen plataformas desconocidas.")
    resultado = {}
    for app in ControlEfectivoDia.APPS:
        try:
            importe = _dinero(valores.get(app, 0))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ErrorVenta(f"El importe de {app} no es válido.") from exc
        if not Decimal("0.00") <= importe <= Decimal("99999999.99"):
            raise ErrorVenta(f"El importe de {app} debe ser positivo o cero.")
        resultado[app] = str(importe)
    return resultado


def control_efectivo_dia(sucursal, fecha=None):
    fecha = fecha or timezone.localdate()
    control = ControlEfectivoDia.objects.filter(
        sucursal=sucursal,
        fecha=fecha,
    ).first()
    if control is not None:
        return control
    anterior = (
        ControlEfectivoDia.objects.filter(sucursal=sucursal, fecha__lt=fecha)
        .order_by("-fecha")
        .first()
    )
    fondo_anterior = (
        dict(anterior.fondo_siguiente)
        if anterior is not None and anterior.fondo_siguiente
        else _conteo_vacio()
    )
    return ControlEfectivoDia.objects.create(
        sucursal=sucursal,
        fecha=fecha,
        fondo_anterior=fondo_anterior,
        fondo_siguiente=_conteo_vacio(),
        ventas_apps={app: "0.00" for app in ControlEfectivoDia.APPS},
    )


def _totales_movimientos(sucursal):
    movimientos = list(_movimientos_turno(sucursal))
    return {
        tipo: sum(
            (
                movimiento.importe
                for movimiento in movimientos
                if movimiento.tipo == tipo
            ),
            Decimal("0.00"),
        )
        for tipo in MovimientoCaja.Tipo.values
    }


def control_efectivo_payload(sucursal, control=None):
    control = control or control_efectivo_dia(sucursal)
    movimientos = _totales_movimientos(sucursal)
    ventas = sum(
        (
            importe
            for canal, importe in totales_parciales(sucursal).items()
            if canal in CANALES_PARCIAL
        ),
        Decimal("0.00"),
    )
    fondo_anterior = _total_conteo(control.fondo_anterior or {})
    fondo_siguiente = _total_conteo(control.fondo_siguiente or {})
    total_caja = (
        fondo_anterior
        + movimientos[MovimientoCaja.Tipo.INGRESO]
        + ventas
        - movimientos[MovimientoCaja.Tipo.GASTO]
        - movimientos[MovimientoCaja.Tipo.TERMINAL]
        - fondo_siguiente
    )
    return {
        "fecha": control.fecha.isoformat(),
        "fondo_anterior": {
            **_conteo_vacio(),
            **(control.fondo_anterior or {}),
        },
        "fondo_siguiente": {
            **_conteo_vacio(),
            **(control.fondo_siguiente or {}),
        },
        "ventas_apps": {
            **{app: "0.00" for app in ControlEfectivoDia.APPS},
            **(control.ventas_apps or {}),
        },
        "totales": {
            "fondo_anterior": str(fondo_anterior),
            "ingresos": str(movimientos[MovimientoCaja.Tipo.INGRESO]),
            "ventas": str(ventas),
            "gastos": str(movimientos[MovimientoCaja.Tipo.GASTO]),
            "terminales": str(movimientos[MovimientoCaja.Tipo.TERMINAL]),
            "fondo_siguiente": str(fondo_siguiente),
            "resultado_caja": str(total_caja),
        },
    }


@transaction.atomic
def guardar_control_efectivo(sucursal, datos):
    control = ControlEfectivoDia.objects.select_for_update().filter(
        sucursal=sucursal,
        fecha=timezone.localdate(),
    ).first()
    if control is None:
        control = control_efectivo_dia(sucursal)
        control = ControlEfectivoDia.objects.select_for_update().get(pk=control.pk)
    control.fondo_anterior = _normalizar_conteo(
        datos.get("fondo_anterior"),
        "El fondo del día anterior",
    )
    control.fondo_siguiente = _normalizar_conteo(
        datos.get("fondo_siguiente"),
        "El fondo del día siguiente",
    )
    control.ventas_apps = _normalizar_apps(datos.get("ventas_apps"))
    control.save(
        update_fields=[
            "fondo_anterior",
            "fondo_siguiente",
            "ventas_apps",
            "actualizado_en",
        ]
    )
    return control


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
    tipo = {
        "entrada": MovimientoCaja.Tipo.INGRESO,
        "salida": MovimientoCaja.Tipo.GASTO,
    }.get(tipo, tipo)
    if tipo not in MovimientoCaja.Tipo.values:
        raise ErrorVenta("Selecciona Ingreso, Gasto o Terminal.")
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


def _purgar_detalle_diario(
    sucursal,
    detalle_turno,
    movimientos,
    inicio,
    corte,
):
    from impresion.models import TrabajoImpresion

    ticket_ids = [ticket.id for ticket in detalle_turno]
    movimientos_ids = [movimiento.id for movimiento in movimientos]
    liquidaciones = list(
        LiquidacionRepartidor.objects.filter(
            sucursal=sucursal,
            tickets__id__in=ticket_ids,
        ).distinct()
    )
    cortes_sucursal = list(
        CorteSucursal.objects.filter(
            sucursal=sucursal,
            tickets__id__in=ticket_ids,
        ).distinct()
    )
    reporte_ids = {
        item.reporte_id for item in [*liquidaciones, *cortes_sucursal]
    }
    limite_reportes = inicio or timezone.make_aware(
        datetime.combine(timezone.localdate(), time.min)
    )
    reporte_ids.update(
        ReporteAdministrativo.objects.filter(
            sucursal=sucursal,
            tipo=ReporteAdministrativo.Tipo.PARCIAL,
            creado_en__gte=limite_reportes,
        ).values_list("id", flat=True)
    )
    rutas = list(
        TrabajoImpresion.objects.filter(
            Q(ticket_id__in=ticket_ids) | Q(reporte_id__in=reporte_ids)
        ).values_list("archivo", flat=True)
    )

    if ticket_ids:
        SolicitudRepeticionTicket.objects.filter(
            Q(ticket_origen_id__in=ticket_ids) | Q(ticket_nuevo_id__in=ticket_ids)
        ).delete()
        EventoOutbox.objects.filter(
            agregado="ticket",
            agregado_id__in=ticket_ids,
        ).delete()
    if liquidaciones:
        LiquidacionRepartidor.objects.filter(
            id__in=[item.id for item in liquidaciones]
        ).delete()
    if cortes_sucursal:
        CorteSucursal.objects.filter(
            id__in=[item.id for item in cortes_sucursal]
        ).delete()
    if reporte_ids:
        ReporteAdministrativo.objects.filter(id__in=reporte_ids).delete()
    if ticket_ids:
        Ticket.objects.filter(id__in=ticket_ids).delete()
    if movimientos_ids:
        MovimientoCaja.objects.filter(id__in=movimientos_ids).delete()

    corte.detalle_eliminado_en = timezone.now()
    corte.save(update_fields=["detalle_eliminado_en"])
    solicitud = crear_solicitud_archivos(
        rutas,
        referencia_tipo=REFERENCIA_CORTE,
        referencia_id=corte.pk,
    )
    if solicitud is not None:
        transaction.on_commit(
            lambda path=solicitud: procesar_solicitud_archivos(path)
        )


@transaction.atomic
def crear_corte_caja(sucursal):
    bloqueos = bloqueos_corte(sucursal)
    if bloqueos:
        raise ErrorVenta(" ".join(bloqueos))
    inicio = inicio_turno(sucursal)
    fin = timezone.now()
    detalle_turno = list(
        _tickets_turno(sucursal)
        .select_for_update()
        .select_related("mesa__cliente_sucursal")
        .exclude(estado=Ticket.Estado.PROGRAMADO)
    )
    tickets = [
        ticket
        for ticket in detalle_turno
        if ticket.estado != Ticket.Estado.CANCELADO
    ]
    movimientos = list(
        _movimientos_turno(sucursal).select_for_update().filter(
            cortes_caja__isnull=True
        )
    )
    totales = {
        canal: sum(
            (ticket.total for ticket in tickets if ticket.canal == canal),
            Decimal("0.00"),
        )
        for canal in CANALES_CAJA
    }
    ingresos = sum(
        (
            movimiento.importe
            for movimiento in movimientos
            if movimiento.tipo == MovimientoCaja.Tipo.INGRESO
        ),
        Decimal("0.00"),
    )
    gastos = sum(
        (
            movimiento.importe
            for movimiento in movimientos
            if movimiento.tipo == MovimientoCaja.Tipo.GASTO
        ),
        Decimal("0.00"),
    )
    terminales = sum(
        (
            movimiento.importe
            for movimiento in movimientos
            if movimiento.tipo == MovimientoCaja.Tipo.TERMINAL
        ),
        Decimal("0.00"),
    )
    total_ventas = sum(
        (totales[canal] for canal in CANALES_PARCIAL),
        Decimal("0.00"),
    )
    control = control_efectivo_dia(sucursal, timezone.localdate(fin))
    fondo_anterior = _total_conteo(control.fondo_anterior or {})
    fondo_siguiente = _total_conteo(control.fondo_siguiente or {})
    total_caja = (
        fondo_anterior
        + ingresos
        + total_ventas
        - gastos
        - terminales
        - fondo_siguiente
    )
    totales_sucursales = {}
    if modulo_habilitado(sucursal, "pedidos_sucursales"):
        for ticket in tickets:
            if ticket.canal != Mesa.Canal.SUCURSALES:
                continue
            cliente = ticket.mesa.cliente_sucursal
            nombre = cliente.nombre if cliente is not None else ticket.mesa.nombre
            totales_sucursales[nombre] = (
                Decimal(str(totales_sucursales.get(nombre, "0.00")))
                + ticket.total
            )
    totales_sucursales = {
        nombre: str(valor.quantize(Decimal("0.01")))
        for nombre, valor in totales_sucursales.items()
    }
    ventas_apps = {
        app: str(_dinero((control.ventas_apps or {}).get(app, 0)))
        for app in ControlEfectivoDia.APPS
    }
    datos = {
        "titulo": "CORTE DE CAJA",
        "inicio": inicio.isoformat() if inicio else None,
        "fin": fin.isoformat(),
        "canales": {canal: str(valor) for canal, valor in totales.items()},
        "fondo_anterior": str(fondo_anterior),
        "ingresos": str(ingresos),
        "gastos": str(gastos),
        "terminales": str(terminales),
        "fondo_siguiente": str(fondo_siguiente),
        "entradas": str(ingresos),
        "salidas": str(gastos),
        "total_ventas": str(total_ventas),
        "total_caja": str(total_caja),
        "ventas_apps": ventas_apps,
        "totales_sucursales": totales_sucursales,
        "movimientos": [
            {
                "tipo": movimiento.tipo,
                "concepto": movimiento.concepto,
                "importe": str(movimiento.importe),
            }
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
        total_entradas=ingresos,
        total_salidas=gastos,
        total_fondo_anterior=fondo_anterior,
        total_terminales=terminales,
        total_fondo_siguiente=fondo_siguiente,
        ventas_apps=ventas_apps,
        totales_sucursales=totales_sucursales,
        total_caja=total_caja,
    )

    fecha_siguiente = timezone.localdate(fin) + timedelta(days=1)
    siguiente, _ = ControlEfectivoDia.objects.select_for_update().get_or_create(
        sucursal=sucursal,
        fecha=fecha_siguiente,
        defaults={
            "fondo_anterior": dict(control.fondo_siguiente or _conteo_vacio()),
            "fondo_siguiente": _conteo_vacio(),
            "ventas_apps": {
                app: "0.00" for app in ControlEfectivoDia.APPS
            },
        },
    )
    siguiente.fondo_anterior = dict(
        control.fondo_siguiente or _conteo_vacio()
    )
    siguiente.save(update_fields=["fondo_anterior", "actualizado_en"])

    _purgar_detalle_diario(
        sucursal,
        detalle_turno,
        movimientos,
        inicio,
        corte,
    )
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
    cliente_sucursal = getattr(ticket.mesa, "cliente_sucursal", None)
    return {
        "id": str(ticket.id),
        "folio": ticket.folio,
        "canal": ticket.canal,
        "canal_etiqueta": ticket.get_canal_display(),
        "estado": ticket.estado,
        "estado_etiqueta": ticket.get_estado_display(),
        "mesa_id": str(ticket.mesa_id),
        "mesa": ticket.mesa.nombre,
        "cliente_sucursal_id": (
            str(cliente_sucursal.id) if cliente_sucursal else ""
        ),
        "cliente_sucursal": (
            cliente_sucursal.nombre if cliente_sucursal else ""
        ),
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
        "cancelado_en": (
            ticket.cancelado_en.isoformat() if ticket.cancelado_en else ""
        ),
        "cancelado_por_nombre": ticket.cancelado_por_nombre,
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
    inicio = inicio_turno(sucursal)
    modulo_sucursales = modulo_habilitado(sucursal, "pedidos_sucursales")
    usuarios = list(
        UsuarioPOS.objects.select_related("rol")
        .filter(
            sucursal=sucursal,
            rol__tipo__in=[
                Rol.Tipo.ENCARGADO,
                Rol.Tipo.ELEVADO,
                Rol.Tipo.MESERO,
                Rol.Tipo.REPARTIDOR,
            ],
        )
        .order_by("rol__tipo", "nombre")
    )
    consulta_turno = (
        _tickets_turno(sucursal)
        .select_related(
            "mesa__cliente_sucursal",
            "repartidor",
            "atendio",
            "cancelado_por",
        )
        .prefetch_related("partidas")
    )
    if not modulo_sucursales:
        consulta_turno = consulta_turno.exclude(canal=Mesa.Canal.SUCURSALES)
    tickets_turno = list(
        consulta_turno.exclude(
            estado__in=[Ticket.Estado.CANCELADO, Ticket.Estado.PROGRAMADO]
        ).order_by("creado_en")
    )
    cancelaciones = list(
        consulta_turno.filter(estado=Ticket.Estado.CANCELADO).order_by(
            "cancelado_en",
            "creado_en",
        )
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
    consulta_posiciones = Mesa.objects.filter(sucursal=sucursal, activa=True)
    if not modulo_sucursales:
        consulta_posiciones = consulta_posiciones.exclude(
            canal=Mesa.Canal.SUCURSALES
        )
    posiciones = [
        {
            "id": str(mesa.id),
            "nombre": mesa.nombre,
            "canal": mesa.canal,
            "canal_etiqueta": mesa.get_canal_display(),
            "disponible": mesa.id not in activos_por_mesa,
            "ticket_id": (
                str(activos_por_mesa[mesa.id].id)
                if mesa.id in activos_por_mesa
                else ""
            ),
            "ticket_folio": (
                activos_por_mesa[mesa.id].folio
                if mesa.id in activos_por_mesa
                else None
            ),
            "ticket_estado": (
                activos_por_mesa[mesa.id].estado
                if mesa.id in activos_por_mesa
                else ""
            ),
        }
        for mesa in consulta_posiciones.order_by("canal", "orden", "nombre")
    ]
    sucursales = (
        list(
            SucursalPedido.objects.filter(
                sucursal=sucursal,
                activa=True,
            ).order_by("tipo", "nombre")
        )
        if modulo_sucursales
        else []
    )
    control = control_efectivo_dia(sucursal)
    from .consolidacion import estado_cierre_mensual

    return {
        "sucursal": {"id": str(sucursal.id), "nombre": sucursal.nombre},
        "inicio_turno": inicio.isoformat() if inicio else None,
        "usuarios": [usuario_payload(usuario) for usuario in usuarios],
        "repartidores": [
            usuario_payload(usuario)
            for usuario in usuarios
            if usuario.rol.tipo == Rol.Tipo.REPARTIDOR and usuario.activo
        ],
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
        "cancelaciones": [
            _ticket_admin_payload(ticket) for ticket in cancelaciones
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
            for movimiento in _movimientos_turno(sucursal)[:50]
        ],
        "control_efectivo": control_efectivo_payload(sucursal, control),
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
                    and ticket.estado
                    in [Ticket.Estado.PROCESADO, Ticket.Estado.COBRAR]
                ),
            }
            for cliente in sucursales
        ],
        "totales_parciales": {
            canal: str(valor)
            for canal, valor in totales_parciales(sucursal).items()
            if modulo_sucursales or canal != Mesa.Canal.SUCURSALES
        },
        "cortes_caja": [
            {
                "id": str(corte.id),
                "reporte_id": str(corte.reporte_id),
                "inicio": corte.inicio.isoformat() if corte.inicio else "",
                "fin": corte.fin.isoformat(),
                "total_ventas": str(corte.total_ventas),
                "total_caja": str(corte.total_caja),
            }
            for corte in CorteCaja.objects.filter(sucursal=sucursal)
            .select_related("reporte")
            .order_by("-fin")[:62]
        ],
        "cierre_mensual": estado_cierre_mensual(sucursal),
    }
