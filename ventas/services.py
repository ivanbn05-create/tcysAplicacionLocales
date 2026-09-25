from datetime import date, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Max, Q, Sum
from django.utils import timezone

from catalogo.models import Producto
from personas.models import Sucursal, UsuarioPOS
from personas.modulos import modulo_habilitado

from .models import (
    ConsecutivoFolio,
    EventoOutbox,
    Mesa,
    ModificadorTicket,
    Partida,
    ProductoSucursal,
    SolicitudRepeticionTicket,
    Ticket,
)
from .aprovisionamiento import exigir_catalogo_operativo, producto_vendible
from .normalizacion import normalizar_telefono
from .orden import PRODUCTOS_SIEMPRE_AL_FINAL
from .promociones import (
    cantidades_componentes,
    configuracion_promocion,
    grupos_promocion,
    promocion_disponible,
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
    codigo = "version_entidad_desactualizada"

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
ESTADOS_BLOQUEABLES = (*ESTADOS_ACTIVOS, Ticket.Estado.PROGRAMADO)
CODIGOS_COMPLEMENTOS_GLOBALES = frozenset({"CO8", "CO05", "CO1"})
TICKET_LOCK_FIELDS = [
    "bloqueo_device_id",
    "bloqueo_operador",
    "bloqueo_tomado_en",
    "bloqueo_heartbeat_en",
    "bloqueo_expira_en",
]
_ATENDIO_AUTOMATICO = object()
CANTIDAD_MAXIMA_POS = Decimal("9999")


def validar_comanda_editable(ticket):
    editable = (
        ticket.comanda_en_edicion
        or ticket.estado in {Ticket.Estado.ABIERTO, Ticket.Estado.PROGRAMADO}
    )
    if (
        not editable
        or ticket.estado
        not in {
            Ticket.Estado.ABIERTO,
            Ticket.Estado.PROCESADO,
            Ticket.Estado.PROGRAMADO,
        }
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
        ticket.estado in ESTADOS_BLOQUEABLES
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
    if ticket.estado not in ESTADOS_BLOQUEABLES:
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
        (
            ("personalizado", str(partida.id))
            if partida.personalizada
            else (partida.producto_id, partida.termino)
        )
        for partida in partidas
        if partida.personalizada
        or (
            not partida.promocion_definicion_id
            and partida.producto.codigo.upper() not in PRODUCTOS_SIEMPRE_AL_FINAL
            and partida.producto.categoria.nombre.lower() != "bebidas"
        )
    }
    if len(claves) > 4:
        raise ErrorVenta("Los pedidos por nombre admiten un máximo de 4 productos principales.")
    return partidas


def _es_complemento_global(partida):
    if partida.personalizada or partida.producto_id is None:
        return False
    return (
        partida.producto.codigo.upper() in CODIGOS_COMPLEMENTOS_GLOBALES
        or partida.producto.categoria.nombre.casefold() == "bebidas"
    )


def validar_captura_por_nombres(ticket):
    partidas = validar_limite_productos_por_nombre(ticket)
    if partidas is None:
        return
    nombres = ticket.nombres_comensales or {}
    comensales = {
        partida.comensal
        for partida in partidas
        if not partida.promocion_definicion_id
        and not _es_complemento_global(partida)
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
    evento = EventoOutbox.objects.create(
        sucursal=ticket.sucursal,
        agregado="ticket",
        agregado_id=ticket.id,
        tipo=tipo,
        datos={"ticket_id": str(ticket.id), "folio": ticket.folio, **(datos or {})},
    )
    from .sincronizacion_central import TIPOS_CIERRE_VENTA, convertir_evento_venta_central

    if tipo in TIPOS_CIERRE_VENTA:
        convertir_evento_venta_central(evento, ticket, tipo, datos)
    return evento


def registrar_evento(ticket, tipo, datos=None):
    _evento(ticket, tipo, datos)


def _eliminar_archivos_impresion_descartados(rutas):
    """Borra vistas previas huérfanas sin permitir salir de MEDIA_ROOT."""

    raiz = Path(settings.MEDIA_ROOT).resolve()
    for valor in rutas:
        relativa = Path(str(valor or ""))
        if not valor or relativa.is_absolute() or ".." in relativa.parts:
            continue
        destino = (raiz / relativa).resolve(strict=False)
        if not destino.is_relative_to(raiz) or not destino.is_file():
            continue
        try:
            destino.unlink()
        except OSError:
            # El trabajo ya quedó invalidado; un fallo físico no debe reabrir
            # la transacción operativa que terminó correctamente.
            continue


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


def _validar_acumulacion_normal(ticket, producto, comensal, termino, cantidad, excluir_id=None):
    # El límite de la captura normal permanece aunque sus líneas no se
    # consoliden ni se asignen automáticamente a una promoción.
    consulta = Partida.objects.filter(
        ticket=ticket,
        comanda_numero=ticket.comanda_actual,
        producto=producto,
        comensal=comensal,
        termino=termino,
        promocion_aplicada__isnull=True,
        promocion_definicion__isnull=True,
    )
    if excluir_id:
        consulta = consulta.exclude(pk=excluir_id)
    acumulada = consulta.aggregate(total=Sum("cantidad"))["total"] or Decimal("0")
    if acumulada + cantidad > CANTIDAD_MAXIMA_POS:
        raise ErrorVenta("La cantidad acumulada no puede superar 9999.")


def _restaurar_componentes_promocion(raiz):
    """Al quitar una promoción, sus partidas quedan vendidas a precio capturado."""
    for componente in Partida.objects.select_for_update().select_related("producto").filter(
        promocion_aplicada=raiz
    ):
        precio_lista = componente.precio_lista_capturado
        if precio_lista is None:
            precio = componente.producto.precio_actual()
            if not precio:
                raise ErrorVenta(
                    "No es posible quitar la promoción: un componente no conserva su precio de lista."
                )
            precio_lista = precio.importe
            componente.precio_lista_capturado = precio_lista
        componente.promocion_aplicada = None
        componente.promocion_grupo = None
        componente.precio_unitario = precio_lista
        componente.save(
            update_fields=[
                "promocion_aplicada",
                "promocion_grupo",
                "precio_unitario",
                "precio_lista_capturado",
            ]
        )


@transaction.atomic
def abrir_ticket(mesa, atendio=_ATENDIO_AUTOMATICO):
    # La sucursal es la barrera común con el corte de caja: mientras se crea
    # una orden, ningún cierre puede fijar su instante final y viceversa.
    sucursal = Sucursal.objects.select_for_update().get(pk=mesa.sucursal_id)
    mesa = Mesa.objects.select_for_update().get(pk=mesa.pk, sucursal=sucursal)
    activo = Ticket.objects.filter(
        mesa=mesa,
        estado__in=[Ticket.Estado.ABIERTO, Ticket.Estado.PROCESADO, Ticket.Estado.COBRAR],
    ).first()
    if activo:
        return activo, False
    try:
        exigir_catalogo_operativo(sucursal)
    except ValueError as exc:
        raise ErrorVenta(str(exc)) from exc

    consecutivo = _consecutivo_folio_bloqueado(sucursal)
    consecutivo.ultimo += 1
    consecutivo.save(update_fields=["ultimo", "actualizado_en"])
    if atendio is _ATENDIO_AUTOMATICO:
        atendio = UsuarioPOS.objects.filter(
            sucursal=sucursal,
            activo=True,
            es_sistema=False,
        ).first()
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
        sucursal=sucursal,
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
    promocion_grupo_id=None,
):
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    validar_comanda_editable(ticket)
    try:
        exigir_catalogo_operativo(ticket.sucursal)
    except ValueError as exc:
        raise ErrorVenta(str(exc)) from exc
    if producto.sucursal_id != ticket.sucursal_id or not producto_vendible(ticket.sucursal, producto):
        raise ErrorVenta("El producto no está disponible para venta en esta sucursal.")
    cantidad = Decimal(str(cantidad))
    if cantidad != cantidad.to_integral_value() or not 1 <= cantidad <= CANTIDAD_MAXIMA_POS:
        raise ErrorVenta("La cantidad debe ser un entero entre 1 y 9999.")
    precio = producto.precio_actual()
    if not precio:
        raise ErrorVenta("El producto no tiene un precio activo.")
    configuracion = configuracion_promocion(producto)
    if configuracion and promocion_aplicada:
        raise ErrorVenta("Una promoción no puede ser componente de otra promoción.")
    if configuracion and not promocion_disponible(producto, timezone.localdate()):
        raise ErrorVenta(f"La promoción {configuracion.codigo} no está disponible el día de hoy.")
    grupo = None
    if promocion_aplicada:
        promocion_aplicada = (
            Partida.objects.select_for_update()
            .select_related("producto", "promocion_definicion")
            .get(pk=promocion_aplicada.pk, ticket=ticket)
        )
        if (
            promocion_aplicada.promocion_aplicada_id
            or promocion_aplicada.comanda_numero != ticket.comanda_actual
            or not configuracion_promocion(promocion_aplicada.producto, partida=promocion_aplicada)
        ):
            raise ErrorVenta("La promoción seleccionada no pertenece a esta comanda.")
        try:
            grupo = validar_cupo_componente(
                promocion_aplicada, producto, cantidad, grupo_id=promocion_grupo_id
            )
        except ValueError as exc:
            raise ErrorVenta(str(exc)) from exc
    elif promocion_grupo_id:
        raise ErrorVenta("Selecciona primero la promoción principal.")

    termino, nombre_producto, nombre_corto = _datos_termino(producto, termino)
    if not configuracion and not promocion_aplicada:
        _validar_acumulacion_normal(ticket, producto, comensal, termino, cantidad)
    partida = Partida.objects.create(
        sucursal=ticket.sucursal,
        ticket=ticket,
        producto=producto,
        comanda_numero=ticket.comanda_actual,
        promocion_aplicada=promocion_aplicada,
        promocion_definicion=configuracion if configuracion else None,
        promocion_grupo=grupo,
        comensal=comensal,
        cantidad=cantidad,
        precio_unitario=(
            Decimal("0.00")
            if promocion_aplicada
            else configuracion.precio
            if configuracion
            else precio.importe
        ),
        precio_lista_capturado=configuracion.precio if configuracion else precio.importe,
        nombre_producto=nombre_producto,
        nombre_corto=nombre_corto,
        termino=termino,
    )
    if ticket.captura_por_nombres:
        validar_limite_productos_por_nombre(ticket)
    guardar_ticket(ticket, [])
    _evento(
        ticket,
        "ticket.partida_agregada",
        {"partida_id": str(partida.id), "producto_id": str(producto.id)},
    )
    return partida


@transaction.atomic
def agregar_partida_sucursal(ticket, producto, cantidad=Decimal("1.000")):
    """Agrega un concepto mayorista conservando precio, unidad y divisor."""
    ticket = Ticket.objects.select_for_update().select_related("mesa__cliente_sucursal").get(pk=ticket.pk)
    if ticket.canal != Mesa.Canal.SUCURSALES or not ticket.mesa.cliente_sucursal_id:
        raise ErrorVenta("La orden no pertenece al módulo de sucursales.")
    if ticket.estado != Ticket.Estado.ABIERTO:
        raise ErrorVenta("La orden ya fue procesada; no admite nuevas partidas.")
    try:
        exigir_catalogo_operativo(ticket.sucursal)
    except ValueError as exc:
        raise ErrorVenta(str(exc)) from exc
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
        precio_lista_capturado=precio.importe,
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
def agregar_partida_personalizada(
    ticket,
    nombre,
    precio_unitario,
    cantidad=Decimal("1.000"),
    comensal=1,
):
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    validar_comanda_editable(ticket)
    try:
        exigir_catalogo_operativo(ticket.sucursal)
    except ValueError as exc:
        raise ErrorVenta(str(exc)) from exc
    nombre = " ".join(str(nombre or "").split())[:180]
    if not nombre:
        raise ErrorVenta("Escribe el nombre del producto personalizado.")
    try:
        precio_unitario = Decimal(str(precio_unitario)).quantize(Decimal("0.01"))
        cantidad = Decimal(str(cantidad))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ErrorVenta("El precio o la cantidad no son válidos.") from exc
    if precio_unitario <= 0 or precio_unitario > Decimal("99999999.99"):
        raise ErrorVenta("El precio debe ser mayor que cero.")
    if cantidad != cantidad.to_integral_value() or not 1 <= cantidad <= CANTIDAD_MAXIMA_POS:
        raise ErrorVenta("La cantidad debe ser un entero entre 1 y 9999.")
    try:
        comensal = int(comensal or 1)
    except (TypeError, ValueError) as exc:
        raise ErrorVenta("El comensal no es válido.") from exc
    if ticket.canal == Mesa.Canal.SUCURSALES:
        comensal = 1
    elif not 1 <= comensal <= 24:
        raise ErrorVenta("El comensal debe estar entre 1 y 24.")
    partida = Partida.objects.create(
        sucursal=ticket.sucursal,
        ticket=ticket,
        personalizada=True,
        comanda_numero=ticket.comanda_actual,
        comensal=comensal,
        cantidad=cantidad,
        precio_unitario=precio_unitario,
        precio_lista_capturado=precio_unitario,
        nombre_producto=nombre,
        nombre_corto=nombre[:24],
    )
    if ticket.captura_por_nombres:
        validar_limite_productos_por_nombre(ticket)
    guardar_ticket(ticket, [])
    _evento(
        ticket,
        "ticket.partida_personalizada_agregada",
        {"partida_id": str(partida.id), "nombre": nombre, "precio": str(precio_unitario)},
    )
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
        partida_id = str(partida.id)
        if partida.promocion_definicion_id:
            _restaurar_componentes_promocion(partida)
        partida.delete()
        guardar_ticket(ticket, [])
        _evento(ticket, "ticket.partida_eliminada", {"partida_id": partida_id})
        return None
    if cantidad != cantidad.to_integral_value() or cantidad > CANTIDAD_MAXIMA_POS:
        raise ErrorVenta("La cantidad debe ser un entero entre 1 y 9999.")
    if partida.promocion_aplicada_id:
        try:
            validar_cupo_componente(
                partida.promocion_aplicada,
                partida.producto,
                cantidad,
                grupo_id=partida.promocion_grupo_id,
                excluir_ids=(partida.id,),
            )
        except ValueError as exc:
            raise ErrorVenta(str(exc)) from exc
    if partida.promocion_definicion_id:
        usadas = cantidades_componentes(partida)
        for grupo in grupos_promocion(partida):
            if usadas[grupo.id] > Decimal(str(grupo.cantidad)) * cantidad:
                raise ErrorVenta(
                    f"Reduce primero los componentes de {grupo.nombre or partida.nombre_producto}."
                )
    if not partida.personalizada and not partida.promocion_aplicada_id and not partida.promocion_definicion_id:
        termino_efectivo = (
            _datos_termino(partida.producto, termino)[0]
            if termino is not None else partida.termino
        )
        _validar_acumulacion_normal(
            ticket, partida.producto, partida.comensal,
            termino_efectivo, cantidad, excluir_id=partida.id,
        )
    partida.cantidad = cantidad
    campos = ["cantidad"]
    if termino is not None and not partida.personalizada:
        partida.termino, partida.nombre_producto, partida.nombre_corto = _datos_termino(partida.producto, termino)
        campos.extend(["termino", "nombre_producto", "nombre_corto"])
    partida.save(update_fields=campos)
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
        or partida.personalizada != principal.personalizada
        or partida.nombre_producto != principal.nombre_producto
        or partida.precio_unitario != principal.precio_unitario
        or partida.comensal != principal.comensal
        or partida.termino != principal.termino
        for partida in partidas[1:]
    ):
        raise ErrorVenta("La selección contiene partidas distintas.")
    if eliminar:
        ids = [str(partida.id) for partida in partidas]
        for partida in partidas:
            if partida.promocion_definicion_id:
                _restaurar_componentes_promocion(partida)
        Partida.objects.filter(id__in=[partida.id for partida in partidas]).delete()
        guardar_ticket(ticket, [])
        _evento(ticket, "ticket.partidas_eliminadas", {"partida_ids": ids})
        return None
    cantidad = Decimal(str(cantidad))
    if cantidad != cantidad.to_integral_value() or not 1 <= cantidad <= CANTIDAD_MAXIMA_POS:
        raise ErrorVenta("La cantidad debe ser un entero entre 1 y 9999.")
    if principal.promocion_aplicada_id or principal.promocion_definicion_id:
        if len(partidas) != 1:
            raise ErrorVenta("Edita cada promoción y componente de forma independiente.")
        return actualizar_partida(principal, cantidad, termino)
    if any(partida.promocion_aplicada_id or partida.promocion_definicion_id for partida in partidas):
        raise ErrorVenta("No es posible agrupar partidas de promociones distintas.")
    duplicadas_destino = []
    if termino is not None and termino != principal.termino and not principal.personalizada:
        termino_destino, _, _ = _datos_termino(principal.producto, termino)
        duplicadas_destino = list(
            Partida.objects.select_for_update()
            .filter(
                ticket=ticket,
                comanda_numero=ticket.comanda_actual,
                producto=principal.producto,
                comensal=principal.comensal,
                termino=termino_destino,
                promocion_aplicada__isnull=True,
                promocion_definicion__isnull=True,
            )
            .exclude(id__in=[partida.id for partida in partidas])
        )
        cantidad += sum((partida.cantidad for partida in duplicadas_destino), Decimal("0"))
        if cantidad > CANTIDAD_MAXIMA_POS:
            raise ErrorVenta("La cantidad acumulada no puede superar 9999.")
    duplicadas = partidas[1:] + duplicadas_destino
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
    if ticket.canal not in {Mesa.Canal.COMEDOR, Mesa.Canal.LLEVAR}:
        raise ErrorVenta("Este canal requiere crear un pedido independiente.")
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
def crear_ticket_repetido(ticket, clave_idempotencia, atendio=None):
    """Crea un pedido vacío del mismo cliente sin copiar estado transaccional."""

    # El corte usa Sucursal como barrera común. Debe tomarse antes del ticket
    # y de las posiciones para conservar el mismo orden de locks en todos los
    # flujos que pueden crear trabajo para el turno.
    sucursal = Sucursal.objects.select_for_update().get(pk=ticket.sucursal_id)
    ticket = (
        Ticket.objects.select_for_update()
        .select_related(
            "mesa",
            "cliente",
            "telefono_cliente",
            "domicilio_cliente",
            "atendio",
        )
        .get(pk=ticket.pk, sucursal=sucursal)
    )
    if ticket.canal not in {Mesa.Canal.DOMICILIO, Mesa.Canal.RECOGER}:
        raise ErrorVenta("Este canal agrega una comanda al pedido actual.")

    clave = str(clave_idempotencia or "").strip()
    if not 16 <= len(clave) <= 128 or any(ord(caracter) < 32 for caracter in clave):
        raise ErrorVenta("La clave idempotente del nuevo pedido no es válida.")
    existente = (
        SolicitudRepeticionTicket.objects.select_related("ticket_nuevo")
        .filter(ticket_origen=ticket, clave_idempotencia=clave)
        .first()
    )
    if existente:
        return existente.ticket_nuevo, False

    if ticket.canal == Mesa.Canal.DOMICILIO and not modulo_habilitado(ticket.sucursal, "domicilios"):
        raise ErrorVenta("El módulo de domicilios no está habilitado en esta sucursal.")
    if ticket.estado != Ticket.Estado.PROCESADO or ticket.comanda_en_edicion:
        raise ErrorVenta("Primero procesa el pedido actual.")
    if ticket.cortes_caja.exists() or ticket.liquidaciones_repartidor.exists():
        raise ErrorVenta("El pedido ya pertenece a un cierre.")

    posiciones = list(
        Mesa.objects.select_for_update()
        .filter(sucursal=sucursal, canal=ticket.canal, activa=True)
        .order_by("orden", "nombre")
    )
    ocupadas = set(
        Ticket.objects.filter(mesa__in=posiciones, estado__in=ESTADOS_ACTIVOS).values_list(
            "mesa_id", flat=True
        )
    )
    posicion = next((mesa for mesa in posiciones if mesa.id not in ocupadas), None)
    if posicion is None:
        raise ErrorVenta(
            f"No hay posiciones de {Mesa.Canal(ticket.canal).label.lower()} disponibles."
        )

    operador = atendio if atendio is not None else ticket.atendio
    nuevo, creado = abrir_ticket(posicion, atendio=operador)
    if not creado:
        raise ErrorVenta("La posición seleccionada dejó de estar disponible.")

    campos = []
    if ticket.canal == Mesa.Canal.DOMICILIO:
        for campo in (
            "cliente",
            "telefono_cliente",
            "domicilio_cliente",
            "cliente_nombre",
            "cliente_telefono",
            "cliente_domicilio",
            "cliente_referencia",
            "contacto_pedido_nombre",
            "contacto_pedido_telefono",
        ):
            setattr(nuevo, campo, getattr(ticket, campo))
            campos.append(campo)
    else:
        nuevo.cliente_nombre = ticket.cliente_nombre
        nuevo.cliente_telefono = ticket.cliente_telefono
        campos.extend(["cliente_nombre", "cliente_telefono"])
    guardar_ticket(nuevo, campos)

    SolicitudRepeticionTicket.objects.create(
        sucursal=sucursal,
        ticket_origen=ticket,
        ticket_nuevo=nuevo,
        clave_idempotencia=clave,
    )
    _evento(
        ticket,
        "ticket.repeticion_solicitada",
        {"ticket_nuevo_id": str(nuevo.id), "clave_idempotencia": clave},
    )
    _evento(
        nuevo,
        "ticket.creado_por_repeticion",
        {"ticket_origen_id": str(ticket.id)},
    )
    return nuevo, True


@transaction.atomic
def procesar_ticket(ticket):
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    if ticket.estado == Ticket.Estado.PROGRAMADO:
        raise ErrorVenta(
            "El pedido sigue programado; desprográmalo o espera su activación antes de procesarlo."
        )
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
def reactivar_ticket_sucursal(ticket, reactivado_por=None, nivel_acceso=""):
    """Devuelve a edición un pedido de sucursal ya procesado.

    La autorización se valida en la vista. Esta transición conserva las
    impresiones completadas, descarta las que aún no salieron y mantiene el
    bloqueo del dispositivo que solicita la reactivación.
    """

    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    if ticket.canal != Mesa.Canal.SUCURSALES:
        raise ErrorVenta("La orden no pertenece al módulo de sucursales.")
    if ticket.estado != Ticket.Estado.PROCESADO:
        raise ErrorVenta("Sólo se puede reactivar un pedido de sucursal procesado.")
    if ticket.cortes_sucursal.exists() or ticket.cortes_caja.exists():
        raise ErrorVenta("El pedido ya pertenece a un corte y no puede reactivarse.")
    if reactivado_por is not None and (
        not reactivado_por.activo
        or reactivado_por.sucursal_id != ticket.sucursal_id
    ):
        raise ErrorVenta("El usuario que reactiva no está activo en esta sucursal.")

    from impresion.models import TrabajoImpresion
    from impresion.services import trabajo_procesando_abandonado

    trabajos = list(
        TrabajoImpresion.objects.select_for_update()
        .filter(
            ticket=ticket,
            formato=TrabajoImpresion.Formato.SUCURSAL,
        )
        .filter(
            Q(comanda_numero=ticket.comanda_actual)
            | Q(comanda_numero__isnull=True)
        )
    )
    ahora = timezone.now()
    procesando_vigentes = [
        trabajo
        for trabajo in trabajos
        if trabajo.estado == TrabajoImpresion.Estado.PROCESANDO
        and not trabajo_procesando_abandonado(trabajo, ahora=ahora)
    ]
    if procesando_vigentes:
        raise ErrorVenta(
            "La impresión del pedido sigue en curso. Espera a que termine antes de reactivarlo."
        )
    abandonados = [
        trabajo
        for trabajo in trabajos
        if trabajo_procesando_abandonado(trabajo, ahora=ahora)
    ]
    descartables = [
        trabajo
        for trabajo in trabajos
        if trabajo.estado != TrabajoImpresion.Estado.IMPRESO
    ]
    rutas_descartadas = [trabajo.archivo for trabajo in descartables if trabajo.archivo]
    if descartables:
        TrabajoImpresion.objects.filter(
            pk__in=[trabajo.pk for trabajo in descartables]
        ).delete()
        if rutas_descartadas:
            transaction.on_commit(
                lambda rutas=tuple(rutas_descartadas): (
                    _eliminar_archivos_impresion_descartados(rutas)
                )
            )

    ticket.estado = Ticket.Estado.ABIERTO
    ticket.comanda_en_edicion = True
    ticket.procesado_en = None
    ticket.partidas.filter(comanda_numero=ticket.comanda_actual).update(
        procesada=False
    )
    guardar_ticket(
        ticket,
        ["estado", "comanda_en_edicion", "procesado_en"],
    )
    _evento(
        ticket,
        "ticket.sucursal_reactivado",
        {
            "nivel_acceso": str(nivel_acceso or ""),
            "reactivado_por_id": (
                str(reactivado_por.id) if reactivado_por is not None else ""
            ),
            "reactivado_por_nombre": (
                reactivado_por.nombre if reactivado_por is not None else "Administrador"
            ),
            "impresiones_descartadas": len(descartables),
            "impresiones_abandonadas": len(abandonados),
        },
    )
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
def cancelar_ticket(
    ticket,
    permitir_procesado=False,
    cancelado_por=None,
    cancelado_por_nombre="",
):
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
    if cancelado_por is not None:
        if not cancelado_por.activo or cancelado_por.sucursal_id != ticket.sucursal_id:
            raise ErrorVenta("El usuario que cancela no está activo en esta sucursal.")
        ticket.cancelado_por = cancelado_por
        ticket.cancelado_por_nombre = cancelado_por.nombre
    elif cancelado_por_nombre:
        ticket.cancelado_por = None
        ticket.cancelado_por_nombre = str(cancelado_por_nombre).strip()[:180]
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
            "cancelado_por",
            "cancelado_por_nombre",
        ],
        limpiar_bloqueo=True,
    )
    _evento(
        ticket,
        "ticket.cancelado",
        {
            "mesa": ticket.mesa.nombre,
            "cancelado_por": ticket.cancelado_por_nombre,
        },
    )
    return ticket
