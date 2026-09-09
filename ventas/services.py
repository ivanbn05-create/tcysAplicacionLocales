from datetime import date, time, timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from catalogo.models import Producto
from personas.models import Sucursal, UsuarioPOS

from .models import (
    ConsecutivoFolio,
    EventoOutbox,
    Mesa,
    ModificadorTicket,
    Partida,
    ProductoSucursal,
    Ticket,
)
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


class TicketBloqueado(ErrorVenta):
    status_code = 423

    def __init__(self, ticket, device_id=""):
        self.ticket = ticket
        self.device_id = device_id
        nombre = etiqueta_bloqueo_ticket(ticket) or "otra tableta"
        super().__init__(f"{ticket.mesa.nombre} está tomada por {nombre}.")


class VersionEntidadDesactualizada(ErrorVenta):
    status_code = 409

    def __init__(self, ticket, device_id=""):
        self.ticket = ticket
        self.device_id = device_id
        super().__init__("La orden cambió desde que la abriste. Revisa la versión actual antes de guardar.")


CANALES_CONVERSION = {
    Mesa.Canal.COMEDOR: Mesa.Canal.LLEVAR,
    Mesa.Canal.LLEVAR: Mesa.Canal.COMEDOR,
    Mesa.Canal.DOMICILIO: Mesa.Canal.RECOGER,
    Mesa.Canal.RECOGER: Mesa.Canal.DOMICILIO,
}
ESTADOS_ACTIVOS = (Ticket.Estado.ABIERTO, Ticket.Estado.PROCESADO, Ticket.Estado.COBRAR)
TICKET_LOCK_FIELDS = [
    "bloqueo_device_id",
    "bloqueo_operador",
    "bloqueo_tomado_en",
    "bloqueo_heartbeat_en",
    "bloqueo_expira_en",
]
_ATENDIO_AUTOMATICO = object()


def validar_comanda_editable(ticket):
    editable = ticket.comanda_en_edicion or ticket.estado == Ticket.Estado.ABIERTO
    if (
        not editable
        or ticket.estado not in {Ticket.Estado.ABIERTO, Ticket.Estado.PROCESADO}
    ):
        raise ErrorVenta("La comanda actual ya fue procesada.")


def _contexto_comanda(ticket):
    return {
        "canal": ticket.canal,
        "mesa": ticket.mesa.nombre,
        "posicion_numero": ticket.mesa.orden,
        "total": str(ticket.total),
        "comentario_general": ticket.comentario_general,
        "comentarios_generales": list(ticket.comentarios_generales or []),
        "salsas_verduras": list(ticket.salsas_verduras or []),
        "tipo_entrega": ticket.tipo_entrega,
        "entrega_aproximada": (
            ticket.entrega_aproximada.strftime("%H:%M")
            if ticket.entrega_aproximada
            else ""
        ),
        "fecha_programada": (
            ticket.fecha_programada.isoformat() if ticket.fecha_programada else ""
        ),
        "hora_programada": (
            ticket.hora_programada.strftime("%H:%M") if ticket.hora_programada else ""
        ),
        "terminal": ticket.terminal,
        "paga_con": str(ticket.paga_con) if ticket.paga_con is not None else "",
        "captura_por_nombres": ticket.captura_por_nombres,
        "nombres_comensales": dict(ticket.nombres_comensales or {}),
        "cliente_nombre": ticket.cliente_nombre,
        "cliente_telefono": ticket.cliente_telefono,
        "cliente_domicilio": ticket.cliente_domicilio,
        "cliente_referencia": ticket.cliente_referencia,
        "contacto_pedido_nombre": ticket.contacto_pedido_nombre,
        "contacto_pedido_telefono": ticket.contacto_pedido_telefono,
    }


def _guardar_contexto_comanda(ticket):
    contextos = dict(ticket.contextos_comandas or {})
    contextos[str(ticket.comanda_actual)] = _contexto_comanda(ticket)
    ticket.contextos_comandas = contextos


def _restaurar_contexto_comanda(ticket, numero):
    contexto = (ticket.contextos_comandas or {}).get(str(numero)) or {}
    if not contexto:
        return []

    campos = [
        "comentario_general",
        "comentarios_generales",
        "salsas_verduras",
        "tipo_entrega",
        "entrega_aproximada",
        "fecha_programada",
        "hora_programada",
        "terminal",
        "paga_con",
        "captura_por_nombres",
        "nombres_comensales",
        "cliente_nombre",
        "cliente_telefono",
        "cliente_domicilio",
        "cliente_referencia",
        "contacto_pedido_nombre",
        "contacto_pedido_telefono",
    ]
    ticket.comentario_general = contexto.get("comentario_general", "")
    ticket.comentarios_generales = list(contexto.get("comentarios_generales") or [])
    ticket.salsas_verduras = list(contexto.get("salsas_verduras") or [])
    ticket.tipo_entrega = contexto.get("tipo_entrega") or Ticket.TipoEntrega.APROXIMADA
    try:
        ticket.entrega_aproximada = time.fromisoformat(contexto.get("entrega_aproximada") or "")
    except ValueError:
        ticket.entrega_aproximada = None
    try:
        ticket.fecha_programada = date.fromisoformat(contexto.get("fecha_programada") or "")
    except ValueError:
        ticket.fecha_programada = None
    try:
        ticket.hora_programada = time.fromisoformat(contexto.get("hora_programada") or "")
    except ValueError:
        ticket.hora_programada = None
    ticket.terminal = bool(contexto.get("terminal"))
    ticket.paga_con = contexto.get("paga_con") or None
    ticket.captura_por_nombres = bool(contexto.get("captura_por_nombres"))
    ticket.nombres_comensales = dict(contexto.get("nombres_comensales") or {})
    for campo in (
        "cliente_nombre",
        "cliente_telefono",
        "cliente_domicilio",
        "cliente_referencia",
        "contacto_pedido_nombre",
        "contacto_pedido_telefono",
    ):
        setattr(ticket, campo, contexto.get(campo) or "")
    return campos


def lease_bloqueo_ticket_segundos():
    return max(15, int(getattr(settings, "POS_TICKET_LOCK_LEASE_SECONDS", 15)))


def _bloqueo_vigente(ticket, ahora=None):
    ahora = ahora or timezone.now()
    return (
        ticket.estado in ESTADOS_ACTIVOS
        and bool(ticket.bloqueo_device_id)
        and ticket.bloqueo_expira_en is not None
        and ticket.bloqueo_expira_en > ahora
    )


def etiqueta_bloqueo_ticket(ticket):
    if ticket.bloqueo_operador_id:
        return ticket.bloqueo_operador.nombre
    if ticket.bloqueo_device_id:
        return f"Tableta {ticket.bloqueo_device_id[-6:]}"
    return ""


def bloqueo_ticket_payload(ticket, device_id="", ahora=None):
    ahora = ahora or timezone.now()
    activo = _bloqueo_vigente(ticket, ahora)
    return {
        "activo": activo,
        "device_id": ticket.bloqueo_device_id if activo else "",
        "operador_id": str(ticket.bloqueo_operador_id) if activo and ticket.bloqueo_operador_id else "",
        "tomado_por": etiqueta_bloqueo_ticket(ticket) if activo else "",
        "tomado_en": ticket.bloqueo_tomado_en.isoformat() if activo and ticket.bloqueo_tomado_en else "",
        "heartbeat_en": ticket.bloqueo_heartbeat_en.isoformat() if activo and ticket.bloqueo_heartbeat_en else "",
        "expira_en": ticket.bloqueo_expira_en.isoformat() if activo and ticket.bloqueo_expira_en else "",
        "lease_segundos": lease_bloqueo_ticket_segundos(),
        "es_mio": activo and bool(device_id) and ticket.bloqueo_device_id == device_id,
        "expirado": bool(ticket.bloqueo_device_id) and not activo,
    }


def _limpiar_bloqueo_instancia(ticket):
    ticket.bloqueo_device_id = ""
    ticket.bloqueo_operador = None
    ticket.bloqueo_tomado_en = None
    ticket.bloqueo_heartbeat_en = None
    ticket.bloqueo_expira_en = None


def guardar_ticket(ticket, update_fields=None, limpiar_bloqueo=False):
    ticket.version_entidad = (ticket.version_entidad or 0) + 1
    if limpiar_bloqueo:
        _limpiar_bloqueo_instancia(ticket)
    if update_fields is None:
        ticket.save()
        return ticket
    campos = set(update_fields)
    campos.update(["version_entidad", "actualizado_en"])
    if limpiar_bloqueo:
        campos.update(TICKET_LOCK_FIELDS)
    ticket.save(update_fields=list(campos))
    return ticket


@transaction.atomic
def asegurar_bloqueo_ticket(ticket, device_id, operador=None, ahora=None):
    ahora = ahora or timezone.now()
    if not device_id:
        ticket = (
            Ticket.objects.select_for_update()
            .select_related("mesa", "bloqueo_operador", "atendio")
            .get(pk=ticket.pk)
        )
        if _bloqueo_vigente(ticket, ahora):
            raise TicketBloqueado(ticket, device_id)
        if ticket.bloqueo_device_id:
            _limpiar_bloqueo_instancia(ticket)
            ticket.save(update_fields=TICKET_LOCK_FIELDS)
        return ticket

    ticket = (
        Ticket.objects.select_for_update()
        .select_related("mesa", "bloqueo_operador", "atendio")
        .get(pk=ticket.pk)
    )
    if ticket.estado not in ESTADOS_ACTIVOS:
        if ticket.bloqueo_device_id:
            _limpiar_bloqueo_instancia(ticket)
            ticket.save(update_fields=TICKET_LOCK_FIELDS)
        return ticket

    if _bloqueo_vigente(ticket, ahora) and ticket.bloqueo_device_id != device_id:
        raise TicketBloqueado(ticket, device_id)

    tomar_nuevo = ticket.bloqueo_device_id != device_id or not _bloqueo_vigente(ticket, ahora)
    ticket.bloqueo_device_id = device_id
    if operador is not None:
        ticket.bloqueo_operador = operador
    elif not ticket.bloqueo_operador_id:
        ticket.bloqueo_operador = ticket.atendio
    if tomar_nuevo:
        ticket.bloqueo_tomado_en = ahora
    ticket.bloqueo_heartbeat_en = ahora
    ticket.bloqueo_expira_en = ahora + timedelta(seconds=lease_bloqueo_ticket_segundos())
    ticket.save(update_fields=TICKET_LOCK_FIELDS)
    return ticket


@transaction.atomic
def liberar_bloqueo_ticket(ticket, device_id, ahora=None):
    if not device_id:
        return Ticket.objects.select_for_update().get(pk=ticket.pk)
    ahora = ahora or timezone.now()
    ticket = (
        Ticket.objects.select_for_update()
        .select_related("mesa", "bloqueo_operador")
        .get(pk=ticket.pk)
    )
    if ticket.bloqueo_device_id == device_id or not _bloqueo_vigente(ticket, ahora):
        _limpiar_bloqueo_instancia(ticket)
        ticket.save(update_fields=TICKET_LOCK_FIELDS)
        return ticket
    raise TicketBloqueado(ticket, device_id)


def validar_version_entidad(ticket, version_esperada, device_id=""):
    if version_esperada in (None, ""):
        return
    try:
        version = int(version_esperada)
    except (TypeError, ValueError) as exc:
        raise ErrorVenta("La versión de la orden no es válida.") from exc
    if version != ticket.version_entidad:
        raise VersionEntidadDesactualizada(ticket, device_id)


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
    partidas = list(
        ticket.partidas.select_related("producto__categoria").filter(
            comanda_numero=ticket.comanda_actual
        )
    )
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


def _consecutivo_folio_bloqueado(sucursal):
    """Obtiene el contador vigente y lo repara si hubo altas fuera del servicio."""

    Sucursal.objects.select_for_update().get(pk=sucursal.pk)
    ultimo_ticket = (
        Ticket.objects.filter(sucursal=sucursal)
        .order_by("-serie_folio", "-folio")
        .values("serie_folio", "folio")
        .first()
    )
    serie_inicial = ultimo_ticket["serie_folio"] if ultimo_ticket else 1
    folio_inicial = ultimo_ticket["folio"] if ultimo_ticket else 0
    consecutivo, creado = ConsecutivoFolio.objects.select_for_update().get_or_create(
        sucursal=sucursal,
        defaults={"serie": serie_inicial, "ultimo": folio_inicial},
    )
    if creado or not ultimo_ticket:
        return consecutivo
    if ultimo_ticket["serie_folio"] > consecutivo.serie:
        consecutivo.serie = ultimo_ticket["serie_folio"]
        consecutivo.ultimo = ultimo_ticket["folio"]
        consecutivo.save(update_fields=["serie", "ultimo", "actualizado_en"])
    elif (
        ultimo_ticket["serie_folio"] == consecutivo.serie
        and ultimo_ticket["folio"] > consecutivo.ultimo
    ):
        consecutivo.ultimo = ultimo_ticket["folio"]
        consecutivo.save(update_fields=["ultimo", "actualizado_en"])
    return consecutivo


@transaction.atomic
def reiniciar_folios(sucursal):
    consecutivo = _consecutivo_folio_bloqueado(sucursal)
    serie_anterior = consecutivo.serie
    consecutivo.serie += 1
    consecutivo.ultimo = 0
    consecutivo.save(update_fields=["serie", "ultimo", "actualizado_en"])
    EventoOutbox.objects.create(
        sucursal=sucursal,
        agregado="sucursal",
        agregado_id=sucursal.id,
        tipo="folios.reiniciados",
        datos={
            "serie_anterior": serie_anterior,
            "serie_nueva": consecutivo.serie,
        },
    )
    return consecutivo


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
        .filter(
            ticket=ticket,
            comanda_numero=ticket.comanda_actual,
            promocion_aplicada__isnull=True,
            producto__codigo__in=PROMOCIONES,
        )
        .order_by("creada_en")
    )
    regulares = list(
        Partida.objects.select_for_update()
        .select_related("producto__categoria")
        .filter(ticket=ticket, comanda_numero=ticket.comanda_actual)
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
                    comanda_numero=ticket.comanda_actual,
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
def abrir_ticket(mesa, atendio=_ATENDIO_AUTOMATICO):
    mesa = Mesa.objects.select_for_update().get(pk=mesa.pk)
    activo = Ticket.objects.filter(
        mesa=mesa,
        estado__in=[Ticket.Estado.ABIERTO, Ticket.Estado.PROCESADO, Ticket.Estado.COBRAR],
    ).first()
    if activo:
        return activo, False
    consecutivo = _consecutivo_folio_bloqueado(mesa.sucursal)
    consecutivo.ultimo += 1
    consecutivo.save(update_fields=["ultimo", "actualizado_en"])
    if atendio is _ATENDIO_AUTOMATICO:
        atendio = UsuarioPOS.objects.filter(sucursal=mesa.sucursal, activo=True).first()
    elif atendio is not None and (
        not atendio.activo or atendio.sucursal_id != mesa.sucursal_id
    ):
        raise ErrorVenta("El perfil del operador no está activo en esta sucursal.")
    valores_iniciales = {}
    if mesa.canal == Mesa.Canal.DOMICILIO:
        entrega = timezone.localtime(timezone.now()) + timedelta(minutes=50)
        valores_iniciales = {
            "comentarios_generales": [{"codigo": "C/T", "nombre": "CON TODO"}],
            "salsas_verduras": [{"prefijo": "", "elementos": ["Con Todo"]}],
            "entrega_aproximada": entrega.time().replace(second=0, microsecond=0),
        }
    ticket = Ticket.objects.create(
        sucursal=mesa.sucursal,
        mesa=mesa,
        atendio=atendio,
        folio=consecutivo.ultimo,
        serie_folio=consecutivo.serie,
        canal=mesa.canal,
        **valores_iniciales,
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
    campos_ticket = ["mesa", "canal"]
    if canal_anterior == Mesa.Canal.DOMICILIO and canal_destino == Mesa.Canal.RECOGER:
        nombre = ticket.contacto_pedido_nombre or ticket.cliente_nombre
        telefono = ticket.contacto_pedido_telefono or ticket.cliente_telefono
        _limpiar_cliente_ticket(ticket)
        ticket.cliente_nombre = nombre
        ticket.cliente_telefono = telefono
        campos_ticket.extend(
            [
                "cliente",
                "telefono_cliente",
                "domicilio_cliente",
                "cliente_nombre",
                "cliente_telefono",
                "cliente_domicilio",
                "cliente_referencia",
                "contacto_pedido_nombre",
                "contacto_pedido_telefono",
            ]
        )
    elif canal_anterior == Mesa.Canal.RECOGER and canal_destino == Mesa.Canal.DOMICILIO:
        _limpiar_cliente_ticket(ticket)
        campos_ticket.extend(
            [
                "cliente",
                "telefono_cliente",
                "domicilio_cliente",
                "cliente_nombre",
                "cliente_telefono",
                "cliente_domicilio",
                "cliente_referencia",
                "contacto_pedido_nombre",
                "contacto_pedido_telefono",
            ]
        )
    elif canal_anterior == Mesa.Canal.LLEVAR and canal_destino == Mesa.Canal.COMEDOR:
        ticket.cliente_nombre = ""
        ticket.cliente_telefono = ""
        campos_ticket.extend(["cliente_nombre", "cliente_telefono"])

    ticket.mesa = mesa_destino
    ticket.canal = canal_destino
    guardar_ticket(ticket, campos_ticket)
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
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    validar_comanda_editable(ticket)
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
        comanda_numero=ticket.comanda_actual,
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
            comanda_numero=ticket.comanda_actual,
            producto=producto,
            comensal=comensal,
            termino=termino,
        ).order_by("creada_en").first()
    )
    guardar_ticket(ticket, [])
    _evento(ticket, "ticket.partida_agregada", {"partida_id": str(partida.id), "producto_id": str(producto.id)})
    return partida


@transaction.atomic
def agregar_partida_sucursal(ticket, producto, cantidad=Decimal("1.000")):
    """Agrega un concepto mayorista conservando precio, unidad y divisor."""
    ticket = Ticket.objects.select_for_update().select_related("mesa__cliente_sucursal").get(pk=ticket.pk)
    if ticket.canal != Mesa.Canal.SUCURSALES or not ticket.mesa.cliente_sucursal_id:
        raise ErrorVenta("La orden no pertenece al módulo de sucursales.")
    if ticket.estado != Ticket.Estado.ABIERTO:
        raise ErrorVenta("La orden ya fue procesada; no admite nuevas partidas.")
    if not producto.activo or producto.sucursal_id != ticket.sucursal_id:
        raise ErrorVenta("El producto de sucursal no está disponible.")
    existente = Partida.objects.filter(ticket=ticket, producto_sucursal=producto).first()
    if existente:
        return existente
    cantidad = Decimal(str(cantidad))
    if cantidad < Decimal("0.001") or cantidad > Decimal("999.999") or cantidad != cantidad.quantize(Decimal("0.001")):
        raise ErrorVenta("La cantidad debe estar entre 0.001 y 999.999, con máximo tres decimales.")
    precio = producto.precio_actual(ticket.mesa.cliente_sucursal)
    if not precio:
        raise ErrorVenta(f"{producto.nombre} no tiene precio vigente para esta sucursal.")
    partida = Partida.objects.create(
        sucursal=ticket.sucursal,
        ticket=ticket,
        producto_sucursal=producto,
        comensal=1,
        cantidad=cantidad,
        precio_unitario=precio.importe,
        cantidad_por_precio=producto.cantidad_por_precio,
        unidad=producto.unidad,
        nombre_producto=precio.nombre_ticket or producto.nombre_ticket or producto.nombre,
        nombre_corto=(precio.nombre_ticket or producto.nombre_ticket or producto.nombre)[:24],
    )
    _evento(
        ticket,
        "ticket.partida_sucursal_agregada",
        {"partida_id": str(partida.id), "producto_origen_id": producto.origen_id},
    )
    guardar_ticket(ticket, [])
    return partida


@transaction.atomic
def actualizar_partida_sucursal(partida, cantidad):
    ticket = Ticket.objects.select_for_update().get(pk=partida.ticket_id)
    partida = Partida.objects.select_for_update().select_related("ticket").get(pk=partida.pk)
    partida.ticket = ticket
    if not partida.producto_sucursal_id:
        raise ErrorVenta("La partida no pertenece al catálogo de sucursales.")
    if partida.ticket.estado != Ticket.Estado.ABIERTO:
        raise ErrorVenta("La orden ya fue procesada.")
    cantidad = Decimal(str(cantidad))
    if cantidad <= 0:
        ticket = partida.ticket
        partida_id = str(partida.id)
        partida.delete()
        guardar_ticket(ticket, [])
        _evento(ticket, "ticket.partida_sucursal_eliminada", {"partida_id": partida_id})
        return None
    if cantidad > Decimal("999.999") or cantidad != cantidad.quantize(Decimal("0.001")):
        raise ErrorVenta("La cantidad debe tener máximo tres decimales y no superar 999.999.")
    partida.cantidad = cantidad
    partida.save(update_fields=["cantidad"])
    _evento(
        partida.ticket,
        "ticket.partida_sucursal_actualizada",
        {"partida_id": str(partida.id), "cantidad": str(cantidad)},
    )
    guardar_ticket(partida.ticket, [])
    return partida


@transaction.atomic
def actualizar_partida(partida, cantidad, termino=None, validar_componente=True):
    ticket = Ticket.objects.select_for_update().get(pk=partida.ticket_id)
    partida = Partida.objects.select_for_update().select_related("producto", "ticket").get(pk=partida.pk)
    partida.ticket = ticket
    validar_comanda_editable(ticket)
    if partida.comanda_numero != ticket.comanda_actual:
        raise ErrorVenta("Las comandas anteriores son de sólo lectura.")
    cantidad = Decimal(str(cantidad))
    if cantidad <= 0:
        ticket = partida.ticket
        partida_id = str(partida.id)
        partida.delete()
        _reasignar_componentes_promocion(ticket)
        guardar_ticket(ticket, [])
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
    guardar_ticket(partida.ticket, [])
    return partida


@transaction.atomic
def ajustar_grupo_partidas(ticket, partida_ids, cantidad, termino=None, eliminar=False):
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    validar_comanda_editable(ticket)
    partidas = list(
        Partida.objects.select_for_update()
        .select_related("producto", "ticket")
        .filter(
            ticket=ticket,
            comanda_numero=ticket.comanda_actual,
            id__in=partida_ids,
        )
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
        guardar_ticket(ticket, [])
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
                comanda_numero=ticket.comanda_actual,
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
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    validar_comanda_editable(ticket)
    campos_ticket = []
    existente = ModificadorTicket.objects.filter(
        ticket=ticket,
        comanda_numero=ticket.comanda_actual,
        comensal=comensal,
        codigo=codigo,
    ).first()
    if existente:
        existente.delete()
        activo = False
    else:
        ticket.modificadores.filter(
            comanda_numero=ticket.comanda_actual,
            comensal=comensal,
        ).delete()
        if ticket.comentarios_generales:
            ticket.comentarios_generales = []
            campos_ticket.append("comentarios_generales")
        ModificadorTicket.objects.create(
            sucursal=ticket.sucursal,
            ticket=ticket,
            comanda_numero=ticket.comanda_actual,
            comensal=comensal,
            codigo=codigo,
            nombre=nombre,
        )
        activo = True
    guardar_ticket(ticket, campos_ticket)
    _evento(ticket, "ticket.modificador", {"comensal": comensal, "codigo": codigo, "activo": activo})
    return activo


@transaction.atomic
def alternar_comentario_general(ticket, codigo, nombre):
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    validar_comanda_editable(ticket)
    comentarios = list(ticket.comentarios_generales or [])
    indice = next((i for i, item in enumerate(comentarios) if item.get("codigo") == codigo), None)
    if indice is None:
        comentarios.append({"codigo": codigo, "nombre": nombre})
        activo = True
        ticket.modificadores.filter(comanda_numero=ticket.comanda_actual).delete()
    else:
        comentarios.pop(indice)
        activo = False
    ticket.comentarios_generales = comentarios
    guardar_ticket(ticket, ["comentarios_generales"])
    _evento(ticket, "ticket.comentario_general", {"codigo": codigo, "activo": activo})
    return activo


@transaction.atomic
def asegurar_modificador(ticket, comensales, codigo, nombre):
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    validar_comanda_editable(ticket)
    comensales_con_producto = set(
        ticket.partidas.exclude(producto__categoria__nombre__iexact="Bebidas")
        .filter(
            comanda_numero=ticket.comanda_actual,
            comensal__in=comensales,
        )
        .values_list("comensal", flat=True)
    )
    aplicados = []
    campos_ticket = []
    if comensales_con_producto and ticket.comentarios_generales:
        ticket.comentarios_generales = []
        campos_ticket.append("comentarios_generales")
    for comensal in comensales:
        if comensal not in comensales_con_producto:
            continue
        ModificadorTicket.objects.filter(
            ticket=ticket,
            comanda_numero=ticket.comanda_actual,
            comensal=comensal,
        ).exclude(codigo=codigo).delete()
        _, creado = ModificadorTicket.objects.get_or_create(
            sucursal=ticket.sucursal,
            ticket=ticket,
            comanda_numero=ticket.comanda_actual,
            comensal=comensal,
            codigo=codigo,
            defaults={"nombre": nombre},
        )
        aplicados.append(comensal)
        if creado:
            _evento(ticket, "ticket.modificador", {"comensal": comensal, "codigo": codigo, "activo": True})
    if aplicados or campos_ticket:
        guardar_ticket(ticket, campos_ticket)
    return aplicados


@transaction.atomic
def agregar_comanda(ticket):
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    if ticket.canal == Mesa.Canal.SUCURSALES:
        raise ErrorVenta("Los pedidos de sucursal no admiten comandas adicionales.")
    if ticket.estado != Ticket.Estado.PROCESADO or ticket.comanda_en_edicion:
        raise ErrorVenta("Primero procesa la comanda actual.")
    if ticket.liquidaciones_repartidor.exists() or ticket.cortes_caja.exists():
        raise ErrorVenta("El pedido ya pertenece a un cierre.")
    if str(ticket.comanda_actual) not in (ticket.contextos_comandas or {}):
        _guardar_contexto_comanda(ticket)
    ticket.comanda_actual += 1
    ticket.comanda_en_edicion = True
    if ticket.captura_por_nombres:
        ticket.nombres_comensales = {}
    guardar_ticket(
        ticket,
        ["comanda_actual", "comanda_en_edicion", "contextos_comandas", "nombres_comensales"],
    )
    _evento(
        ticket,
        "ticket.comanda_agregada",
        {"comanda_numero": ticket.comanda_actual},
    )
    return ticket


@transaction.atomic
def procesar_ticket(ticket):
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    validar_comanda_editable(ticket)
    partidas_actuales = ticket.partidas.filter(comanda_numero=ticket.comanda_actual)
    if not partidas_actuales.exists():
        raise ErrorVenta("Agrega al menos un producto.")
    if ticket.canal != Mesa.Canal.SUCURSALES:
        try:
            validar_promociones(ticket)
        except ValueError as exc:
            raise ErrorVenta(str(exc)) from exc
    if ticket.canal == Mesa.Canal.DOMICILIO:
        if not ticket.cliente_id or not ticket.cliente_nombre.strip():
            raise ErrorVenta("Selecciona un cliente antes de procesar la orden.")
        if ticket.cliente.comentarios_multiples:
            if not all([ticket.contacto_pedido_nombre, ticket.contacto_pedido_telefono]):
                raise ErrorVenta("Captura el nombre y teléfono del contacto para este pedido.")
    elif ticket.canal == Mesa.Canal.RECOGER:
        if not ticket.cliente_nombre.strip():
            raise ErrorVenta("Captura el nombre del cliente que recogerá el pedido.")
        if len(normalizar_telefono(ticket.cliente_telefono)) < 7:
            raise ErrorVenta("Captura un celular válido para el pedido a recoger.")
    elif ticket.canal == Mesa.Canal.LLEVAR and not ticket.cliente_nombre.strip():
        raise ErrorVenta("Captura el nombre del cliente para llevar.")
    if ticket.canal != Mesa.Canal.SUCURSALES:
        validar_captura_por_nombres(ticket)
    _guardar_contexto_comanda(ticket)
    ticket.estado = Ticket.Estado.PROCESADO
    ticket.comanda_en_edicion = False
    ticket.procesado_en = timezone.now()
    guardar_ticket(
        ticket,
        ["estado", "comanda_en_edicion", "procesado_en", "contextos_comandas"],
    )
    partidas_actuales.update(procesada=True)
    _evento(
        ticket,
        "ticket.procesado",
        {"total": str(ticket.total), "comanda_numero": ticket.comanda_actual},
    )
    return ticket


@transaction.atomic
def cobrar_ticket(ticket, forma_pago, importe_recibido=None):
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    if ticket.comanda_en_edicion:
        raise ErrorVenta("Procesa la comanda actual antes de cobrar.")
    if ticket.canal == Mesa.Canal.DOMICILIO:
        raise ErrorVenta("Los domicilios se liquidan mediante su repartidor desde Administrador.")
    if ticket.estado not in [Ticket.Estado.PROCESADO, Ticket.Estado.COBRAR]:
        raise ErrorVenta("Primero procesa la orden.")
    if forma_pago not in Ticket.FormaPago.values:
        raise ErrorVenta("Forma de pago inválida.")
    total = ticket.total
    try:
        recibido = Decimal(str(importe_recibido)) if importe_recibido is not None else None
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ErrorVenta("El importe recibido no es válido.") from exc
    if recibido is not None:
        if not recibido.is_finite():
            raise ErrorVenta("El importe recibido debe ser un número finito.")
        try:
            cuantizado = recibido.quantize(Decimal("0.01"))
        except InvalidOperation as exc:
            raise ErrorVenta("El importe recibido no es válido.") from exc
        if recibido != cuantizado or recibido <= 0 or recibido > Decimal("99999999.99"):
            raise ErrorVenta("El importe recibido debe ser positivo y tener máximo dos decimales.")
    if forma_pago == Ticket.FormaPago.EFECTIVO:
        if recibido is None or recibido < total:
            raise ErrorVenta("El importe recibido no puede ser menor que el total.")
    else:
        if recibido is not None and recibido != total:
            raise ErrorVenta("El importe de tarjeta debe coincidir con el total.")
        recibido = total
    ticket.estado = Ticket.Estado.PAGADO
    ticket.forma_pago = forma_pago
    ticket.importe_recibido = recibido
    ticket.pagado_en = timezone.now()
    guardar_ticket(
        ticket,
        ["estado", "forma_pago", "importe_recibido", "pagado_en"],
        limpiar_bloqueo=True,
    )
    _evento(ticket, "ticket.pagado", {"total": str(ticket.total), "forma_pago": forma_pago})
    return ticket


@transaction.atomic
def completar_ticket_sucursal(ticket):
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    if ticket.comanda_en_edicion:
        raise ErrorVenta("Procesa la comanda actual antes de completar el pedido.")
    if ticket.canal != Mesa.Canal.SUCURSALES:
        raise ErrorVenta("La orden no pertenece al módulo de sucursales.")
    if ticket.estado not in [Ticket.Estado.PROCESADO, Ticket.Estado.COBRAR]:
        raise ErrorVenta("Primero procesa e imprime el pedido de sucursal.")
    ticket.estado = Ticket.Estado.PAGADO
    ticket.pagado_en = timezone.now()
    guardar_ticket(ticket, ["estado", "pagado_en"], limpiar_bloqueo=True)
    _evento(ticket, "ticket.sucursal_completado", {"total": str(ticket.total)})
    return ticket


@transaction.atomic
def cancelar_comanda_adicional(ticket):
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    if not (
        ticket.estado == Ticket.Estado.PROCESADO
        and ticket.comanda_en_edicion
        and ticket.comanda_actual > 1
    ):
        raise ErrorVenta("No hay una comanda agregada pendiente por cancelar.")

    numero_cancelado = ticket.comanda_actual
    ticket.partidas.filter(comanda_numero=numero_cancelado).delete()
    ticket.modificadores.filter(comanda_numero=numero_cancelado).delete()
    contextos = dict(ticket.contextos_comandas or {})
    contextos.pop(str(numero_cancelado), None)
    ticket.contextos_comandas = contextos
    ticket.comanda_actual -= 1
    ticket.comanda_en_edicion = False
    campos = _restaurar_contexto_comanda(ticket, ticket.comanda_actual)
    guardar_ticket(
        ticket,
        campos + ["comanda_actual", "comanda_en_edicion", "contextos_comandas"],
    )
    _evento(
        ticket,
        "ticket.comanda_cancelada",
        {"comanda_numero": numero_cancelado},
    )
    return ticket


@transaction.atomic
def cancelar_ticket(ticket, permitir_procesado=False):
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    estados_permitidos = [Ticket.Estado.ABIERTO]
    if permitir_procesado:
        estados_permitidos.extend([Ticket.Estado.PROCESADO, Ticket.Estado.COBRAR, Ticket.Estado.PROGRAMADO])
    if ticket.estado not in estados_permitidos:
        raise ErrorVenta("El pedido ya no puede cancelarse.")
    # Una orden abierta aún no es un comprobante operativo: se limpia como antes.
    # Una orden procesada conserva sus partidas para auditoría, aunque queda fuera
    # de reportes y cortes por su estado cancelado.
    if ticket.estado == Ticket.Estado.ABIERTO:
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
        ticket.fecha_programada = None
        ticket.hora_programada = None
        ticket.repartidor = None
    ticket.fecha_programada = None
    ticket.hora_programada = None
    ticket.repartidor = None
    ticket.estado = Ticket.Estado.CANCELADO
    ticket.cancelado_en = timezone.now()
    guardar_ticket(
        ticket,
        [
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
            "fecha_programada",
            "hora_programada",
            "repartidor",
            "captura_por_nombres",
            "nombres_comensales",
            "estado",
            "cancelado_en",
        ],
        limpiar_bloqueo=True,
    )
    _evento(ticket, "ticket.cancelado", {"mesa": ticket.mesa.nombre})
    return ticket
