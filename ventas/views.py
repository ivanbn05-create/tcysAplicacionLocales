import json
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.core.exceptions import RequestDataTooBig, ValidationError
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_cache_control
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from catalogo.models import Producto
from impresion.models import TrabajoImpresion
from impresion.services import encolar_impresiones, encolar_reporte, estado_impresora
from personas.models import Rol, Sucursal, UsuarioPOS
from personas.modulos import modulo_habilitado, modulos_efectivos
from pos.version import APP_VERSION

from .admin_services import (
    actualizar_movimiento,
    aplicar_accion_tickets_lote,
    agregar_movimiento,
    aplicar_descuento,
    asignar_repartidor,
    activar_programados,
    autenticar_acceso_administrador,
    cambiar_clave_administrador,
    cancelar_ticket_administrador,
    crear_corte_caja,
    crear_corte_sucursal,
    crear_liquidacion_repartidor,
    crear_reporte_parcial,
    desprogramar_ticket,
    eliminar_movimiento,
    eliminar_ticket_programado,
    guardar_control_efectivo,
    guardar_usuario,
    identificar_usuario_ventas,
    programar_ticket,
    reasignar_ticket,
    resumen_administrador,
    usuario_payload,
    validar_clave_administrador,
    validar_clave_administrativa,
)

from .api_throttle import (
    clave_intentos,
    limpiar_fallos,
    limite_agotado,
    registrar_fallo,
    respuesta_limite,
)
from .consolidacion import consolidar_periodo

from .clientes import (
    ErrorCliente,
    buscar_clientes,
    cliente_payload,
    duplicados_por_nombre,
    guardar_cliente,
)
from .integracion_sucursales import sincronizar_pedidos_confirmados
from .imagenes import respuesta_miniatura_producto
from .menu_imagenes import (
    IMAGEN_MENU_PREDETERMINADA,
    IMAGEN_MENU_POR_CODIGO,
    RUTA_MENU_ESTATICO,
    imagen_producto_url,
)
from .models import (
    Cliente,
    DomicilioCliente,
    Mesa,
    MovimientoCaja,
    Partida,
    ProductoSucursal,
    ReporteAdministrativo,
    SucursalPedido,
    TelefonoCliente,
    Ticket,
)
from .normalizacion import normalizar_telefono
from .orden import ordenar_partidas
from .promociones import configuracion_promocion, promocion_disponible, promociones_pendientes
from .services import (
    ErrorVenta,
    TicketBloqueado,
    abrir_ticket,
    asegurar_bloqueo_ticket,
    asegurar_modificador,
    ajustar_grupo_partidas,
    actualizar_partida,
    actualizar_partida_sucursal,
    agregar_comanda,
    agregar_partida,
    agregar_partida_personalizada,
    agregar_partida_sucursal,
    alternar_comentario_general,
    alternar_modificador,
    cancelar_comanda_adicional,
    cobrar_ticket,
    cancelar_ticket,
    liberar_bloqueo_ticket,
    bloqueo_ticket_payload,
    completar_ticket_sucursal,
    convertir_tipo_ticket,
    crear_ticket_repetido,
    guardar_ticket,
    procesar_ticket,
    reiniciar_folios,
    registrar_evento,
    validar_limite_productos_por_nombre,
    validar_comanda_editable,
    validar_version_entidad,
)


PREFIJOS_SALSA = {"", "+ Más", "Nada más"}
OPCIONES_SALSA = {
    "Con Todo", "Sin Nada", "Sólo Salsas", "Individual", "Verde", "Roja", "Pepino", "Rábano", "Cebolla",
    "Limón", "Morada", "Serrano", "Cilantro", "Cacahuate", "Chipotle", "Mexicana",
    "Verde Tomate", "Habanero", "Roja Taquera",
}
MODIFICADORES_PERMITIDOS = {
    "C/T": "CON TODO",
    "S/N": "SIN NADA",
    "CEB": "CEBOLLA",
    "CH G": "CHILE GÜERO",
    "CH V": "CHILE VERDE",
    "LLEVAR": "LLEVAR",
}
COMENTARIOS_GENERALES_PERMITIDOS = {
    "C/T": "CON TODO",
    "S/N": "SIN NADA",
    "CEB": "CEBOLLA",
    "CH G": "CHILE GÜERO",
    "CH V": "CHILE VERDE",
    "LLEVAR": "LLEVAR",
    "TODO_PLATO": "TODO POR PLATO",
    "CEB_PLATO": "CEBOLLA POR PLATO",
    "CH_PLATO": "CHILE POR PLATO",
    "TODO_APARTE": "TODO A PARTE",
    "CEB_APARTE": "CEBOLLA A PARTE",
    "CH_APARTE": "CHILE A PARTE",
    "MAS_GUERO": "MÁS CHILE GÜERO",
    "MAS_VERDE": "MÁS CHILE VERDE",
    "MAS_CEB": "MÁS CEBOLLA",
    **MODIFICADORES_PERMITIDOS,
}

# Cambiar este valor obliga a las terminales y tabletas instaladas a descargar
# los recursos de interfaz de esta entrega, incluso si conservan una caché PWA.
ASSET_VERSION = APP_VERSION
PWA_CACHE = f"tocayos-pos-{ASSET_VERSION}"


class ErrorSolicitudJSON(ErrorVenta, ErrorCliente):
    """Error de entrada común a las API de ventas y clientes."""


class EdicionProgramadaNoAutorizada(ErrorVenta):
    status_code = 403


def permiso_pos(campo):
    """Aplica autorización de negocio además de la sesión Django."""

    def decorar(vista):
        @wraps(vista)
        def protegida(request, *args, **kwargs):
            if not getattr(settings, "POS_REQUIRE_AUTH", True):
                return vista(request, *args, **kwargs)
            if request.user.is_superuser:
                return vista(request, *args, **kwargs)
            perfil = getattr(request, "pos_user", None)
            if perfil is None or not getattr(perfil.rol, campo, False):
                response = JsonResponse(
                    {"error": "La cuenta no tiene permiso para realizar esta operación."},
                    status=403,
                )
                patch_cache_control(response, no_store=True, private=True)
                return response
            return vista(request, *args, **kwargs)

        return protegida

    return decorar


def requiere_modulo(clave):
    """Rechaza una capacidad deshabilitada aunque se invoque la API directamente."""

    def decorar(vista):
        @wraps(vista)
        def protegida(request, *args, **kwargs):
            if not modulo_habilitado(_sucursal(), clave):
                response = JsonResponse(
                    {
                        "error": "El módulo solicitado no está habilitado en esta sucursal.",
                        "codigo": "modulo_no_habilitado",
                        "modulo": clave,
                    },
                    status=403,
                )
                patch_cache_control(response, no_store=True, private=True)
                return response
            return vista(request, *args, **kwargs)

        return protegida

    return decorar


def transaccion_en_metodos(*metodos):
    """Abre una transacción inmediata sólo para métodos que mutan estado."""

    permitidos = set(metodos)

    def decorar(vista):
        @wraps(vista)
        def envuelta(request, *args, **kwargs):
            if request.method not in permitidos:
                return vista(request, *args, **kwargs)
            with transaction.atomic():
                return vista(request, *args, **kwargs)

        return envuelta

    return decorar


PERMISOS_ADMINISTRADOR = {
    "gestionar_usuarios": True,
    "reiniciar_folios": True,
    "cambiar_clave_maestra": True,
}
PERMISOS_ELEVADO = {
    "gestionar_usuarios": False,
    "reiniciar_folios": False,
    "cambiar_clave_maestra": False,
}


def _limpiar_acceso_administrador(request):
    for clave in (
        "admin_autorizado_hasta",
        "admin_nivel",
        "admin_perfil_id",
    ):
        request.session.pop(clave, None)


def _acceso_administrador_actual(request):
    try:
        autorizado_hasta = float(request.session.get("admin_autorizado_hasta", 0) or 0)
    except (TypeError, ValueError):
        autorizado_hasta = 0
    if autorizado_hasta < timezone.now().timestamp():
        _limpiar_acceso_administrador(request)
        return None

    nivel = request.session.get("admin_nivel")
    if nivel == "administrador":
        return {
            "nivel": nivel,
            "perfil": None,
            "permisos": dict(PERMISOS_ADMINISTRADOR),
        }
    if nivel == "elevado":
        perfil = (
            UsuarioPOS.objects.select_related("rol")
            .filter(
                pk=request.session.get("admin_perfil_id"),
                sucursal=_sucursal(),
                activo=True,
                rol__tipo=Rol.Tipo.ELEVADO,
            )
            .first()
        )
        if perfil is not None:
            return {
                "nivel": nivel,
                "perfil": perfil,
                "permisos": dict(PERMISOS_ELEVADO),
            }

    _limpiar_acceso_administrador(request)
    return None


def acceso_administrador(vista):
    @wraps(vista)
    def protegida(request, *args, **kwargs):
        acceso = _acceso_administrador_actual(request)
        if acceso is None:
            return JsonResponse(
                {"error": "Vuelve a ingresar una clave de acceso administrativo."},
                status=401,
            )
        request.acceso_administrador = acceso
        return vista(request, *args, **kwargs)

    return protegida


def acceso_administrador_maestro(vista):
    @wraps(vista)
    def protegida(request, *args, **kwargs):
        acceso = _acceso_administrador_actual(request)
        if acceso is None:
            return JsonResponse(
                {"error": "Vuelve a ingresar una clave de acceso administrativo."},
                status=401,
            )
        if acceso["nivel"] != "administrador":
            return JsonResponse(
                {
                    "error": "Esta acción requiere la clave maestra del administrador.",
                    "codigo": "administrador_maestro_requerido",
                },
                status=403,
            )
        request.acceso_administrador = acceso
        return vista(request, *args, **kwargs)

    return protegida


def _validar_clave_admin_datos(sucursal, datos):
    validar_clave_administrativa(sucursal, datos.get("clave_administrador"))


def _sucursal():
    try:
        return Sucursal.objects.get(clave=settings.SUCURSAL_CLAVE, activa=True)
    except Sucursal.DoesNotExist as exc:
        raise Http404(
            "La sucursal configurada no está aprovisionada o se encuentra inactiva."
        ) from exc


def _json(request):
    try:
        body = request.body
    except RequestDataTooBig as exc:
        raise ErrorSolicitudJSON("El cuerpo JSON supera el tamaño permitido.") from exc
    max_bytes = max(1024, int(getattr(settings, "POS_MAX_JSON_BODY_BYTES", 131072)))
    if len(body) > max_bytes:
        raise ErrorSolicitudJSON("El cuerpo JSON supera el tamaño permitido.")
    try:
        data = json.loads(body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ErrorSolicitudJSON("El cuerpo JSON no es válido.") from exc
    if not isinstance(data, dict):
        raise ErrorSolicitudJSON("El cuerpo JSON debe ser un objeto.")
    return data


def _device_id(request, datos=None):
    valor = request.headers.get("X-POS-Device-ID") or (datos or {}).get("device_id") or ""
    device_id = str(valor).strip()
    if not device_id:
        return ""
    if len(device_id) > 128 or any(ord(caracter) < 32 for caracter in device_id):
        raise ErrorSolicitudJSON("El identificador del dispositivo no es válido.")
    return device_id


def _operador_actual_pos(request, sucursal):
    perfil_id = request.session.get("mesero_pos_id")
    if perfil_id:
        return UsuarioPOS.objects.select_related("rol").filter(
            pk=perfil_id,
            sucursal=sucursal,
            activo=True,
        ).first()
    if not getattr(settings, "POS_REQUIRE_AUTH", True):
        return UsuarioPOS.objects.filter(sucursal=sucursal, activo=True).first()
    return None


def _respuesta_error_venta(error, device_id=""):
    status = getattr(error, "status_code", 400)
    payload = {"error": str(error)}
    ticket = getattr(error, "ticket", None)
    if ticket is not None:
        payload["ticket"] = _ticket_payload(ticket, device_id)
        payload["bloqueo"] = payload["ticket"]["bloqueo"]
    return JsonResponse(payload, status=status)


def _asegurar_edicion_ticket(
    request,
    ticket,
    datos=None,
    validar_version=True,
    permitir_programado=False,
):
    datos = datos or {}
    device_id = _device_id(request, datos)
    operador = _operador_actual_pos(request, ticket.sucursal)
    if ticket.estado == Ticket.Estado.PROGRAMADO:
        if not permitir_programado or datos.get("edicion_programada") is not True:
            raise ErrorVenta(
                "Abre el pedido desde Administración para editarlo sin cambiar su programación."
            )
        acceso = _acceso_administrador_actual(request)
        if acceso is None:
            raise EdicionProgramadaNoAutorizada(
                "El acceso administrativo venció. Vuelve a Programados para autorizar la edición."
            )
        if not device_id:
            raise ErrorSolicitudJSON(
                "El identificador del dispositivo es obligatorio para editar un pedido programado."
            )
        operador = acceso["perfil"] or operador
    ticket = asegurar_bloqueo_ticket(ticket, device_id, operador=operador)
    if validar_version and device_id and "version_entidad" not in datos:
        raise ErrorSolicitudJSON("La versión de la orden es obligatoria.")
    if validar_version:
        validar_version_entidad(ticket, datos.get("version_entidad"), device_id)
    return ticket, device_id


def _ticket(ticket_id):
    try:
        return Ticket.objects.select_related(
            "mesa__cliente_sucursal",
            "cliente",
            "telefono_cliente",
            "domicilio_cliente",
            "atendio",
            "sucursal",
            "bloqueo_operador",
        ).get(pk=ticket_id, sucursal=_sucursal())
    except Ticket.DoesNotExist as exc:
        raise Http404("Ticket no encontrado") from exc


def _trabajos_payload(trabajos):
    resultado = []
    for trabajo in trabajos:
        trabajo.refresh_from_db()
        resultado.append(
            {
                "id": str(trabajo.id),
                "formato": trabajo.formato,
                "destino": trabajo.destino,
                "estado": trabajo.estado,
                "backend": settings.PRINT_BACKEND,
                "error": "No fue posible completar la impresión." if trabajo.error else "",
                "url": reverse("ventas:api_archivo_impresion", args=[trabajo.id]) if trabajo.archivo else "",
            }
        )
    return resultado


def _catalogo_sucursal_payload(ticket):
    cliente_sucursal = ticket.mesa.cliente_sucursal
    if not ticket.mesa.cliente_sucursal_id:
        return []
    vistos = set()
    resultado = []
    precios = (
        cliente_sucursal.precios.select_related("producto")
        .filter(vigente_desde__lte=timezone.localdate(), producto__activo=True)
        .order_by("producto__orden", "-vigente_desde")
    )
    for precio in precios:
        if precio.producto_id in vistos:
            continue
        vistos.add(precio.producto_id)
        producto = precio.producto
        resultado.append(
            {
                "id": str(producto.id),
                "origen_id": producto.origen_id,
                "nombre": producto.nombre,
                "nombre_ticket": precio.nombre_ticket or producto.nombre_ticket,
                "unidad": producto.unidad,
                "cantidad_por_precio": str(producto.cantidad_por_precio),
                "precio": str(precio.importe),
                "orden": producto.orden,
            }
        )
    return resultado


def _ticket_payload(ticket, device_id=""):
    comanda_en_edicion = (
        ticket.comanda_en_edicion or ticket.estado == Ticket.Estado.ABIERTO
    )
    partidas = []
    es_sucursal = ticket.canal == Mesa.Canal.SUCURSALES
    if es_sucursal:
        consulta_partidas = ticket.partidas.select_related(
            "producto_sucursal"
        ).order_by("personalizada", "producto_sucursal__orden", "creada_en")
        for partida in consulta_partidas:
            producto = partida.producto_sucursal
            personalizada = bool(partida.personalizada)
            partidas.append(
                {
                    "id": str(partida.id),
                    "producto_id": (
                        "" if personalizada else str(partida.producto_sucursal_id)
                    ),
                    "producto_sucursal_id": (
                        "" if personalizada else str(partida.producto_sucursal_id)
                    ),
                    "personalizado": personalizada,
                    "codigo": (
                        "PERSONALIZADO"
                        if personalizada
                        else str(producto.origen_id)
                    ),
                    "nombre": partida.nombre_producto,
                    "nombre_catalogo": (
                        partida.nombre_producto
                        if personalizada
                        else producto.nombre
                    ),
                    "nombre_corto": partida.nombre_corto,
                    "comensal": 1,
                    "comanda_numero": partida.comanda_numero,
                    "cantidad": str(partida.cantidad),
                    "precio": str(partida.precio_unitario),
                    "importe": str(partida.importe),
                    "cantidad_por_precio": str(partida.cantidad_por_precio),
                    "unidad": partida.unidad,
                    "categoria": (
                        "Personalizado" if personalizada else "Sucursales"
                    ),
                    "destino": "caja",
                    "termino": "",
                    "orden": 99999 if personalizada else producto.orden,
                    "es_promocion": False,
                    "promocion_id": "",
                }
            )
    else:
        consulta_partidas = ticket.partidas.select_related(
            "producto__categoria",
            "promocion_aplicada__producto",
        ).all()
        for partida in ordenar_partidas(consulta_partidas):
            personalizada = bool(partida.personalizada)
            producto = partida.producto
            promocion = (
                None
                if personalizada
                else configuracion_promocion(producto)
            )
            partidas.append(
                {
                    "id": str(partida.id),
                    "producto_id": (
                        "" if personalizada else str(partida.producto_id)
                    ),
                    "producto_sucursal_id": "",
                    "personalizado": personalizada,
                    "codigo": (
                        "PERSONALIZADO"
                        if personalizada
                        else producto.codigo
                    ),
                    "nombre": partida.nombre_producto,
                    "nombre_corto": partida.nombre_corto,
                    "comensal": partida.comensal,
                    "comanda_numero": partida.comanda_numero,
                    "cantidad": str(partida.cantidad),
                    "precio": str(partida.precio_unitario),
                    "importe": str(partida.importe),
                    "cantidad_por_precio": str(partida.cantidad_por_precio),
                    "unidad": partida.unidad,
                    "categoria": (
                        "Personalizado"
                        if personalizada
                        else producto.categoria.nombre
                    ),
                    "destino": (
                        "cocina"
                        if personalizada
                        else producto.destino_impresion
                    ),
                    "termino": partida.termino,
                    "orden": 99999 if personalizada else producto.orden,
                    "es_promocion": bool(promocion),
                    "promocion_id": (
                        str(partida.promocion_aplicada_id)
                        if partida.promocion_aplicada_id
                        else ""
                    ),
                }
            )
    modificadores = [
        {
            "id": str(mod.id),
            "comensal": mod.comensal,
            "comanda_numero": mod.comanda_numero,
            "codigo": mod.codigo,
            "nombre": mod.nombre,
        }
        for mod in ticket.modificadores.all()
    ]
    cliente = {
        "id": str(ticket.cliente_id) if ticket.cliente_id else "",
        "clave_corta": ticket.cliente.clave_corta if ticket.cliente_id else "",
        "nombre": ticket.cliente_nombre or (ticket.cliente.nombre if ticket.cliente_id else ""),
        "telefono": ticket.cliente_telefono,
        "telefono_id": str(ticket.telefono_cliente_id) if ticket.telefono_cliente_id else "",
        "domicilio": ticket.cliente_domicilio,
        "domicilio_id": str(ticket.domicilio_cliente_id) if ticket.domicilio_cliente_id else "",
        "referencia": ticket.cliente_referencia,
        "notas": ticket.cliente.notas if ticket.cliente_id else "",
        "comentarios_multiples": ticket.cliente.comentarios_multiples if ticket.cliente_id else False,
        "contacto_pedido_nombre": ticket.contacto_pedido_nombre,
        "contacto_pedido_telefono": ticket.contacto_pedido_telefono,
    }
    pendientes = [] if es_sucursal else promociones_pendientes(ticket)
    cliente_sucursal = ticket.mesa.cliente_sucursal
    return {
        "id": str(ticket.id),
        "folio": ticket.folio,
        "estado": ticket.estado,
        "canal": ticket.canal,
        "version_entidad": ticket.version_entidad,
        "bloqueo": bloqueo_ticket_payload(ticket, device_id),
        "mesa_id": str(ticket.mesa_id),
        "mesa": ticket.mesa.nombre,
        "posicion_numero": ticket.mesa.orden,
        "cliente_sucursal": {
            "id": str(cliente_sucursal.id) if cliente_sucursal else "",
            "origen_id": cliente_sucursal.origen_id if cliente_sucursal else None,
            "nombre": cliente_sucursal.nombre if cliente_sucursal else "",
            "tipo": cliente_sucursal.tipo if cliente_sucursal else "",
        },
        "catalogo_sucursal": _catalogo_sucursal_payload(ticket) if es_sucursal else [],
        "subtotal": str(ticket.subtotal),
        "total": str(ticket.total),
        "descuento_porcentaje": str(ticket.descuento_porcentaje),
        "creado_en": ticket.creado_en.isoformat(),
        "comentario_general": ticket.comentario_general,
        "comentarios_generales": ticket.comentarios_generales,
        "salsas_verduras": ticket.salsas_verduras,
        "tipo_entrega": ticket.tipo_entrega,
        "entrega_aproximada": ticket.entrega_aproximada.strftime("%H:%M") if ticket.entrega_aproximada else "",
        "terminal": ticket.terminal,
        "paga_con": str(ticket.paga_con) if ticket.paga_con is not None else "",
        "fecha_programada": ticket.fecha_programada.isoformat() if ticket.fecha_programada else "",
        "hora_programada": ticket.hora_programada.strftime("%H:%M") if ticket.hora_programada else "",
        "repartidor": {
            "id": str(ticket.repartidor_id) if ticket.repartidor_id else "",
            "nombre": ticket.repartidor.nombre if ticket.repartidor_id else "",
        },
        "captura_por_nombres": ticket.captura_por_nombres,
        "nombres_comensales": ticket.nombres_comensales,
        "promocion_pendiente_id": pendientes[0] if pendientes else "",
        "promociones_pendientes": pendientes,
        "comanda_actual": ticket.comanda_actual,
        "comanda_en_edicion": comanda_en_edicion,
        "contextos_comandas": ticket.contextos_comandas or {},
        "cantidad_comandas": ticket.comanda_actual,
        "puede_agregar_comanda": (
            ticket.canal != Mesa.Canal.SUCURSALES
            and ticket.estado == Ticket.Estado.PROCESADO
            and not comanda_en_edicion
            and not ticket.liquidaciones_repartidor.exists()
            and not ticket.cortes_caja.exists()
        ),
        "comandas": [
            {
                "numero": numero,
                "procesada": (
                    numero < ticket.comanda_actual
                    or (numero == ticket.comanda_actual and not comanda_en_edicion)
                ),
            }
            for numero in range(1, ticket.comanda_actual + 1)
        ],
        "cliente": cliente,
        "partidas": partidas,
        "modificadores": modificadores,
    }


def _inicio(request, modo_tableta=False):
    sucursal = _sucursal()
    modulos = modulos_efectivos(sucursal)
    if modulos["programados"]:
        activar_programados(sucursal)
    perfil = getattr(request, "pos_user", None)
    acceso_total = not getattr(settings, "POS_REQUIRE_AUTH", True) or request.user.is_superuser
    permisos = {
        "cobrar": acceso_total or bool(perfil and perfil.rol.puede_cobrar),
        "reimprimir": acceso_total or bool(perfil and perfil.rol.puede_reimprimir),
        "cancelar": acceso_total or bool(perfil and perfil.rol.puede_cancelar),
        "sincronizar": acceso_total or bool(perfil and perfil.rol.puede_sincronizar),
    }
    productos = []
    for producto in Producto.objects.select_related("categoria").filter(sucursal=sucursal, activo=True):
        precio = producto.precio_actual()
        if precio:
            promocion = configuracion_promocion(producto)
            productos.append(
                {
                    "id": str(producto.id),
                    "codigo": producto.codigo,
                    "nombre": producto.nombre,
                    "corto": producto.nombre_corto,
                    "categoria": producto.categoria.nombre,
                    "precio": str(precio.importe),
                    "destino": producto.destino_impresion,
                    "permite_termino": producto.permite_termino,
                    "termino_predeterminado": producto.termino_predeterminado,
                    "abreviaturas_termino": producto.abreviaturas_termino,
                    "imagen_url": imagen_producto_url(producto),
                    "orden": producto.orden,
                    "es_promocion": bool(promocion),
                    "promocion_dias": promocion["dias_texto"] if promocion else "",
                    "disponible_hoy": promocion_disponible(producto, timezone.localdate()) if promocion else True,
                }
            )
    canales_habilitados = {Mesa.Canal.COMEDOR, Mesa.Canal.RECOGER, Mesa.Canal.LLEVAR}
    if modulos["domicilios"]:
        canales_habilitados.add(Mesa.Canal.DOMICILIO)
    if modulos["pedidos_sucursales"]:
        canales_habilitados.add(Mesa.Canal.SUCURSALES)
    posiciones = [
        {
            "id": str(mesa.id),
            "canal": mesa.canal,
            "clave": mesa.clave,
            "nombre": mesa.nombre,
            "orden": mesa.orden,
            "cliente_sucursal_id": str(mesa.cliente_sucursal_id) if mesa.cliente_sucursal_id else "",
            "cliente_sucursal_nombre": mesa.cliente_sucursal.nombre if mesa.cliente_sucursal_id else "",
            "cliente_sucursal_orden": mesa.cliente_sucursal.origen_id if mesa.cliente_sucursal_id else 0,
        }
        for mesa in Mesa.objects.select_related("cliente_sucursal").filter(
            sucursal=sucursal, activa=True, canal__in=canales_habilitados
        )
    ]
    response = render(
        request,
        "ventas/inicio.html",
        {
            "sucursal": sucursal,
            "productos": productos,
            "posiciones": posiciones,
            "modo_tableta": modo_tableta,
            "asset_version": ASSET_VERSION,
            "permisos": permisos,
            "modulos": modulos,
        },
    )
    patch_cache_control(response, no_store=True, private=True)
    return response


def inicio(request):
    return _inicio(request)


def tabletas(request):
    return _inicio(request, modo_tableta=True)


def administrador(request):
    sucursal = _sucursal()
    response = render(
        request,
        "ventas/administrador.html",
        {
            "sucursal": sucursal,
            "asset_version": ASSET_VERSION,
            "modulos": modulos_efectivos(sucursal),
        },
    )
    patch_cache_control(response, no_store=True, private=True)
    return response


@require_GET
def salud(request):
    """Sonda deliberadamente mínima: no consulta DB ni revela infraestructura."""

    response = JsonResponse({"estado": "ok"})
    patch_cache_control(response, no_store=True)
    return response


@require_GET
def api_estado(request):
    sucursal = _sucursal()
    modulos = modulos_efectivos(sucursal)
    try:
        device_id = _device_id(request)
    except ErrorSolicitudJSON:
        device_id = ""
    if modulos["programados"]:
        activar_programados(sucursal)
    integracion = {
        "activa": settings.PEDIDOS_SUCURSALES_AUTO_SYNC and modulos["pedidos_sucursales"],
        "importados": 0,
        "mensaje": (
            "Sincronización de sucursales bajo demanda."
            if modulos["pedidos_sucursales"]
            else "Módulo de pedidos entre sucursales deshabilitado."
        ),
    }
    activos = Ticket.objects.filter(
        sucursal=sucursal,
        estado__in=[Ticket.Estado.ABIERTO, Ticket.Estado.PROCESADO, Ticket.Estado.COBRAR],
    ).select_related("mesa", "bloqueo_operador")
    tickets = {
        str(ticket.mesa_id): {
            "ticket_id": str(ticket.id),
            "folio": ticket.folio,
            "estado": ticket.estado,
            "total": str(ticket.total),
            "cliente_nombre": (
                ticket.cliente_nombre
                if ticket.canal == Mesa.Canal.LLEVAR
                else ""
            ),
            "version_entidad": ticket.version_entidad,
            "bloqueo": bloqueo_ticket_payload(ticket, device_id),
        }
        for ticket in activos
    }
    programados = [
        {
            "ticket_id": str(ticket.id),
            "folio": ticket.folio,
            "estado": ticket.estado,
            "canal": ticket.canal,
            "total": str(ticket.total),
            "fecha_programada": ticket.fecha_programada.isoformat() if ticket.fecha_programada else "",
            "hora_programada": ticket.hora_programada.strftime("%H:%M") if ticket.hora_programada else "",
            "entrega_aproximada": ticket.entrega_aproximada.strftime("%H:%M") if ticket.entrega_aproximada else "",
            "cliente_nombre": ticket.cliente_nombre,
        }
        for ticket in Ticket.objects.filter(
            sucursal=sucursal,
            estado=Ticket.Estado.PROGRAMADO,
            canal__in=[Mesa.Canal.DOMICILIO, Mesa.Canal.RECOGER],
        )
        .prefetch_related("partidas")
        .order_by("fecha_programada", "hora_programada", "creado_en")
    ] if modulos["programados"] else []
    return JsonResponse(
        {"tickets": tickets, "programados": programados, "integracion_sucursales": integracion}
    )


@require_POST
def api_identificar_operador(request):
    sucursal = _sucursal()
    clave_limite = clave_intentos(request, "operador", sucursal)
    if limite_agotado(clave_limite):
        return respuesta_limite()
    try:
        perfil = identificar_usuario_ventas(
            sucursal,
            _json(request).get("clave"),
            perfil_administrador=getattr(request, "pos_user", None),
        )
    except ErrorVenta as exc:
        registrar_fallo(clave_limite)
        if limite_agotado(clave_limite):
            return respuesta_limite()
        return JsonResponse({"error": str(exc)}, status=400)

    limpiar_fallos(clave_limite)
    request.session["mesero_pos_id"] = str(perfil.id)
    request.session["mesero_pos_nombre"] = perfil.nombre
    return JsonResponse({"operador": usuario_payload(perfil)})


@require_POST
def api_salir_operador(request):
    request.session.pop("mesero_pos_id", None)
    request.session.pop("mesero_pos_nombre", None)
    return JsonResponse({"ok": True})


@require_POST
@requiere_modulo("pedidos_sucursales")
@permiso_pos("puede_sincronizar")
def api_sincronizar_sucursales(request):
    integracion = sincronizar_pedidos_confirmados(_sucursal())
    return JsonResponse({"integracion_sucursales": integracion})


@require_GET
def imagen_producto(request, producto_id):
    producto = Producto.objects.filter(
        pk=producto_id,
        sucursal=_sucursal(),
        activo=True,
    ).first()
    if producto is None:
        raise Http404("Producto no encontrado.")
    return respuesta_miniatura_producto(request, producto)


@require_GET
def api_estado_impresion(request):
    return JsonResponse(estado_impresora())


@require_GET
def api_archivo_impresion(request, trabajo_id):
    try:
        trabajo = TrabajoImpresion.objects.only("archivo").get(pk=trabajo_id, sucursal=_sucursal())
    except TrabajoImpresion.DoesNotExist as exc:
        raise Http404("Vista previa no encontrada.") from exc
    if not trabajo.archivo:
        raise Http404("Vista previa no encontrada.")

    root = Path(settings.MEDIA_ROOT).resolve()
    relative = Path(trabajo.archivo)
    if relative.is_absolute():
        raise Http404("Vista previa no encontrada.")
    try:
        candidate = (root / relative).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise Http404("Vista previa no encontrada.") from exc
    if not candidate.is_relative_to(root) or candidate.suffix.lower() != ".png" or not candidate.is_file():
        raise Http404("Vista previa no encontrada.")

    try:
        response = FileResponse(candidate.open("rb"), content_type="image/png")
    except OSError as exc:
        raise Http404("Vista previa no encontrada.") from exc
    response["Content-Disposition"] = f'inline; filename="impresion-{trabajo.id}.png"'
    response["X-Content-Type-Options"] = "nosniff"
    patch_cache_control(response, no_store=True, private=True)
    return response


@require_POST
def api_abrir_ticket(request):
    device_id = ""
    try:
        datos = _json(request)
        sucursal = _sucursal()
        device_id = _device_id(request, datos)
        with transaction.atomic():
            mesa = Mesa.objects.get(pk=datos.get("mesa_id"), sucursal=sucursal, activa=True)
            modulo_canal = {
                Mesa.Canal.DOMICILIO: "domicilios",
                Mesa.Canal.SUCURSALES: "pedidos_sucursales",
            }.get(mesa.canal)
            if modulo_canal and not modulo_habilitado(sucursal, modulo_canal):
                raise ErrorVenta("El canal solicitado no está habilitado en esta sucursal.")
            perfil = _operador_actual_pos(request, sucursal)
            if perfil is None:
                raise ErrorVenta("Identifícate con tu código antes de tomar una comanda.")
            ticket, creado = abrir_ticket(mesa, atendio=perfil)
            ticket = asegurar_bloqueo_ticket(ticket, device_id, operador=perfil)
        return JsonResponse({"creado": creado, "ticket": _ticket_payload(ticket, device_id)})
    except Mesa.DoesNotExist:
        return JsonResponse({"error": "Posición no encontrada."}, status=400)
    except (TicketBloqueado, ErrorVenta) as exc:
        return _respuesta_error_venta(exc, device_id)


@require_http_methods(["GET", "PATCH"])
def api_ticket(request, ticket_id):
    ticket = _ticket(ticket_id)
    try:
        device_id = _device_id(request)
    except ErrorSolicitudJSON as exc:
        return _respuesta_error_venta(exc)

    if request.method == "PATCH":
        try:
            datos = _json(request)
            with transaction.atomic():
                ticket = (
                    Ticket.objects.select_for_update()
                    .select_related(
                        "mesa__cliente_sucursal",
                        "cliente",
                        "telefono_cliente",
                        "domicilio_cliente",
                        "atendio",
                        "sucursal",
                        "bloqueo_operador",
                    )
                    .get(pk=ticket.pk)
                )
                ticket, device_id = _asegurar_edicion_ticket(
                    request,
                    ticket,
                    datos,
                    permitir_programado=True,
                )
                validar_comanda_editable(ticket)
                if "comentario_general" in datos:
                    comentario = str(datos["comentario_general"]).strip()
                    if len(comentario) > 500:
                        raise ErrorVenta("El comentario no puede superar 500 caracteres.")
                    ticket.comentario_general = comentario
                if "captura_por_nombres" in datos:
                    if ticket.canal not in {
                        Mesa.Canal.COMEDOR,
                        Mesa.Canal.LLEVAR,
                        Mesa.Canal.DOMICILIO,
                        Mesa.Canal.RECOGER,
                    }:
                        raise ErrorVenta("Este tipo de orden no admite captura por nombres.")
                    ticket.captura_por_nombres = bool(datos["captura_por_nombres"])
                    validar_limite_productos_por_nombre(ticket)
                if "nombres_comensales" in datos:
                    nombres = datos["nombres_comensales"]
                    if not isinstance(nombres, dict):
                        raise ErrorVenta("La lista de nombres no es válida.")
                    if len(nombres) > 24:
                        raise ErrorVenta("La lista de nombres no puede superar 24 personas.")
                    nombres_limpios = {}
                    for clave, valor in nombres.items():
                        numero = int(clave)
                        nombre = str(valor).strip()[:60]
                        if not 1 <= numero <= 24:
                            raise ErrorVenta("El número de persona está fuera de rango.")
                        if nombre:
                            nombres_limpios[str(numero)] = nombre
                    ticket.nombres_comensales = nombres_limpios
                if "cliente_nombre" in datos or "cliente_telefono" in datos:
                    if ticket.canal not in {Mesa.Canal.RECOGER, Mesa.Canal.LLEVAR}:
                        raise ErrorVenta("Los datos directos de cliente sólo aplican a recoger o llevar.")
                    if "cliente_nombre" in datos:
                        ticket.cliente_nombre = str(datos["cliente_nombre"]).strip()[:180]
                    if "cliente_telefono" in datos:
                        ticket.cliente_telefono = str(datos["cliente_telefono"]).strip()[:30]
                if "contacto_pedido_nombre" in datos:
                    ticket.contacto_pedido_nombre = str(datos["contacto_pedido_nombre"]).strip()[:180]
                if "contacto_pedido_telefono" in datos:
                    ticket.contacto_pedido_telefono = str(datos["contacto_pedido_telefono"]).strip()[:30]
                if not ticket.cliente_id or not ticket.cliente.comentarios_multiples:
                    ticket.contacto_pedido_nombre = ""
                    ticket.contacto_pedido_telefono = ""
                if ticket.contacto_pedido_telefono and len(normalizar_telefono(ticket.contacto_pedido_telefono)) < 7:
                    raise ErrorVenta("El teléfono del contacto debe contener al menos 7 dígitos.")
                if "tipo_entrega" in datos:
                    tipo_entrega = str(datos["tipo_entrega"])
                    if tipo_entrega not in Ticket.TipoEntrega.values:
                        raise ErrorVenta("El tipo de entrega no es válido.")
                    ticket.tipo_entrega = tipo_entrega
                if "entrega_aproximada" in datos:
                    entrega = datos.get("entrega_aproximada")
                    ticket.entrega_aproximada = datetime.strptime(entrega, "%H:%M").time() if entrega else None
                if "terminal" in datos:
                    ticket.terminal = bool(datos["terminal"])
                    if ticket.terminal:
                        ticket.paga_con = None
                if "paga_con" in datos:
                    paga_con = datos.get("paga_con")
                    ticket.paga_con = Decimal(str(paga_con)) if paga_con not in (None, "") else None
                    if ticket.paga_con is not None:
                        if ticket.paga_con != ticket.paga_con.to_integral_value() or ticket.paga_con <= 0:
                            raise ErrorVenta("Paga con debe ser un número entero natural.")
                        if ticket.paga_con < ticket.total:
                            raise ErrorVenta("Paga con no puede ser menor que el total del pedido.")
                        ticket.terminal = False
                if "salsas_verduras" in datos:
                    grupos = datos["salsas_verduras"]
                    if not isinstance(grupos, list):
                        raise ErrorVenta("La selección de salsas y verduras no es válida.")
                    if len(grupos) > len(PREFIJOS_SALSA):
                        raise ErrorVenta("La selección contiene demasiados grupos.")
                    normalizados = []
                    for grupo in grupos:
                        if not isinstance(grupo, dict):
                            raise ErrorVenta("La selección de salsas y verduras no es válida.")
                        prefijo = str(grupo.get("prefijo", ""))
                        elementos_crudos = grupo.get("elementos", [])
                        if not isinstance(elementos_crudos, list) or len(elementos_crudos) > len(OPCIONES_SALSA):
                            raise ErrorVenta("La selección de salsas y verduras no es válida.")
                        elementos = list(dict.fromkeys(str(item) for item in elementos_crudos))
                        if (
                            prefijo not in PREFIJOS_SALSA
                            or not elementos
                            or any(item not in OPCIONES_SALSA for item in elementos)
                        ):
                            raise ErrorVenta("La selección de salsas y verduras contiene una opción no válida.")
                        normalizados.append({"prefijo": prefijo, "elementos": elementos})
                    ticket.salsas_verduras = normalizados
                guardar_ticket(ticket)
                registrar_evento(ticket, "ticket.datos_actualizados", {"canal": ticket.canal})
        except (ErrorVenta, InvalidOperation, ValueError) as exc:
            return _respuesta_error_venta(exc, device_id)

    return JsonResponse({"ticket": _ticket_payload(ticket, device_id)})


@require_POST
def api_agregar_comanda(request, ticket_id):
    device_id = ""
    try:
        datos = _json(request)
        with transaction.atomic():
            ticket_origen = _ticket(ticket_id)
            ticket_origen, device_id = _asegurar_edicion_ticket(request, ticket_origen, datos)
            if ticket_origen.canal in {Mesa.Canal.DOMICILIO, Mesa.Canal.RECOGER}:
                ticket, creado = crear_ticket_repetido(
                    ticket_origen,
                    datos.get("idempotency_key"),
                    atendio=_operador_actual_pos(request, ticket_origen.sucursal),
                )
                ticket = asegurar_bloqueo_ticket(
                    ticket,
                    device_id,
                    operador=_operador_actual_pos(request, ticket.sucursal),
                )
                modo = "ticket_nuevo"
            else:
                ticket = agregar_comanda(ticket_origen)
                creado = False
                modo = "comanda"
        return JsonResponse(
            {
                "ticket": _ticket_payload(_ticket(ticket.id), device_id),
                "ticket_origen_id": str(ticket_origen.id),
                "creado": creado,
                "modo": modo,
            }
        )
    except ErrorVenta as exc:
        return _respuesta_error_venta(exc, device_id)


@require_POST
def api_buscar_clientes(request):
    try:
        datos = _json(request)
        consulta = str(datos.get("q", "")).strip()
        if len(consulta) > 180:
            raise ErrorSolicitudJSON("La búsqueda es demasiado larga.")
        limite = int(datos.get("limite", 10))
        return JsonResponse({"resultados": buscar_clientes(_sucursal(), consulta, limite)})
    except ErrorSolicitudJSON as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except (TypeError, ValueError):
        return JsonResponse({"error": "El límite de resultados no es válido."}, status=400)


@require_POST
def api_clientes(request):
    try:
        datos = _json(request)
        duplicados = duplicados_por_nombre(_sucursal(), datos.get("nombre", ""))
        if duplicados and not datos.get("confirmar_duplicado"):
            return JsonResponse(
                {"error": "Ya existe un cliente con ese nombre.", "duplicados": duplicados}, status=409
            )
        cliente = guardar_cliente(_sucursal(), datos)
        return JsonResponse({"cliente": cliente_payload(cliente)}, status=201)
    except ErrorCliente as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_http_methods(["GET", "PATCH"])
def api_cliente(request, cliente_id):
    try:
        cliente = Cliente.objects.get(pk=cliente_id, sucursal=_sucursal(), activo=True)
        if request.method == "PATCH":
            datos = _json(request)
            duplicados = duplicados_por_nombre(_sucursal(), datos.get("nombre", ""), excluir=cliente.id)
            if duplicados and not datos.get("confirmar_duplicado"):
                return JsonResponse(
                    {"error": "Ya existe otro cliente con ese nombre.", "duplicados": duplicados}, status=409
                )
            cliente = guardar_cliente(_sucursal(), datos, cliente=cliente)
        return JsonResponse({"cliente": cliente_payload(cliente)})
    except Cliente.DoesNotExist:
        return JsonResponse({"error": "Cliente no encontrado."}, status=404)
    except ErrorCliente as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_http_methods(["POST", "DELETE"])
def api_ticket_cliente(request, ticket_id):
    device_id = ""
    try:
        datos = _json(request)
        with transaction.atomic():
            ticket = _ticket(ticket_id)
            ticket = (
                Ticket.objects.select_for_update()
                .select_related(
                    "mesa__cliente_sucursal",
                    "cliente",
                    "telefono_cliente",
                    "domicilio_cliente",
                    "atendio",
                    "sucursal",
                    "bloqueo_operador",
                )
                .get(pk=ticket.pk)
            )
            ticket, device_id = _asegurar_edicion_ticket(
                request,
                ticket,
                datos,
                permitir_programado=True,
            )
            if ticket.estado not in {Ticket.Estado.ABIERTO, Ticket.Estado.PROGRAMADO}:
                raise ErrorVenta("La orden ya fue procesada.")
            if ticket.canal != Mesa.Canal.DOMICILIO:
                raise ErrorVenta("Sólo los pedidos a domicilio admiten clientes.")
            if request.method == "DELETE":
                ticket.cliente = None
                ticket.telefono_cliente = None
                ticket.domicilio_cliente = None
                ticket.cliente_nombre = ""
                ticket.cliente_telefono = ""
                ticket.cliente_domicilio = ""
                ticket.cliente_referencia = ""
                ticket.contacto_pedido_nombre = ""
                ticket.contacto_pedido_telefono = ""
            else:
                cliente = Cliente.objects.get(pk=datos.get("cliente_id"), sucursal=ticket.sucursal, activo=True)
                telefono = None
                if datos.get("telefono_id"):
                    telefono = TelefonoCliente.objects.get(
                        pk=datos.get("telefono_id"), cliente=cliente, sucursal=ticket.sucursal, activo=True
                    )
                domicilio = None
                if datos.get("domicilio_id"):
                    domicilio = DomicilioCliente.objects.get(
                        pk=datos.get("domicilio_id"), cliente=cliente, sucursal=ticket.sucursal, activo=True
                    )
                ticket.cliente = cliente
                ticket.telefono_cliente = telefono
                ticket.domicilio_cliente = domicilio
                ticket.cliente_nombre = cliente.nombre
                ticket.cliente_telefono = telefono.numero if telefono else ""
                ticket.cliente_domicilio = domicilio.texto_completo if domicilio else ""
                ticket.cliente_referencia = domicilio.referencia if domicilio else ""
                # El contacto pertenece exclusivamente al pedido actual. Nunca debe
                # sobrevivir al cambio o a la nueva selección de una empresa.
                ticket.contacto_pedido_nombre = ""
                ticket.contacto_pedido_telefono = ""
            guardar_ticket(ticket)
            registrar_evento(
                ticket,
                "ticket.cliente_asignado",
                {"cliente_id": str(ticket.cliente_id) if ticket.cliente_id else None},
            )
        return JsonResponse({"ticket": _ticket_payload(ticket, device_id)})
    except (Cliente.DoesNotExist, TelefonoCliente.DoesNotExist, DomicilioCliente.DoesNotExist):
        return JsonResponse({"error": "El cliente, teléfono o domicilio seleccionado ya no está disponible."}, status=400)
    except ErrorVenta as exc:
        return _respuesta_error_venta(exc, device_id)


@require_POST
def api_convertir_ticket(request, ticket_id):
    device_id = ""
    try:
        datos = _json(request)
        with transaction.atomic():
            ticket = _ticket(ticket_id)
            ticket, device_id = _asegurar_edicion_ticket(request, ticket, datos)
            canal_destino = str(datos.get("canal", ""))
            modulo_canal = {
                Mesa.Canal.DOMICILIO: "domicilios",
                Mesa.Canal.SUCURSALES: "pedidos_sucursales",
            }.get(canal_destino)
            if modulo_canal and not modulo_habilitado(ticket.sucursal, modulo_canal):
                raise ErrorVenta("El canal solicitado no está habilitado en esta sucursal.")
            ticket = convertir_tipo_ticket(ticket, canal_destino)
        return JsonResponse({"ticket": _ticket_payload(_ticket(ticket.id), device_id)})
    except ErrorVenta as exc:
        return _respuesta_error_venta(exc, device_id)


@require_http_methods(["POST", "DELETE"])
def api_bloqueo_ticket(request, ticket_id):
    device_id = ""
    try:
        datos = _json(request)
        with transaction.atomic():
            ticket = _ticket(ticket_id)
            device_id = _device_id(request, datos)
            if not device_id:
                raise ErrorSolicitudJSON("El identificador del dispositivo es obligatorio para tomar una mesa.")
            if request.method == "DELETE":
                ticket = liberar_bloqueo_ticket(ticket, device_id)
            else:
                ticket = asegurar_bloqueo_ticket(
                    ticket,
                    device_id,
                    operador=_operador_actual_pos(request, ticket.sucursal),
                )
        payload = _ticket_payload(ticket, device_id)
        return JsonResponse({"ticket": payload, "bloqueo": payload["bloqueo"]})
    except ErrorVenta as exc:
        return _respuesta_error_venta(exc, device_id)


@require_POST
def api_agregar_partida(request, ticket_id):
    device_id = ""
    try:
        datos = _json(request)
        with transaction.atomic():
            ticket = _ticket(ticket_id)
            ticket, device_id = _asegurar_edicion_ticket(
                request,
                ticket,
                datos,
                permitir_programado=True,
            )
            if datos.get("personalizado"):
                agregar_partida_personalizada(
                    ticket,
                    datos.get("nombre_producto"),
                    datos.get("precio_unitario"),
                    cantidad=datos.get("cantidad", "1"),
                    comensal=datos.get("comensal", 1),
                )
            elif ticket.canal == Mesa.Canal.SUCURSALES:
                producto = ProductoSucursal.objects.get(
                    pk=datos.get("producto_sucursal_id")
                    or datos.get("producto_id"),
                    sucursal=ticket.sucursal,
                )
                agregar_partida_sucursal(
                    ticket,
                    producto,
                    Decimal(str(datos.get("cantidad", "1"))),
                )
            else:
                producto = Producto.objects.get(
                    pk=datos.get("producto_id"),
                    sucursal=ticket.sucursal,
                )
                comensal = int(datos.get("comensal", 1))
                cantidad = Decimal(str(datos.get("cantidad", "1")))
                if not 1 <= comensal <= 24 or cantidad <= 0:
                    raise ErrorVenta("Comensal o cantidad fuera de rango.")
                promocion_aplicada = None
                if datos.get("promocion_id"):
                    promocion_aplicada = Partida.objects.select_related(
                        "producto"
                    ).get(
                        pk=datos["promocion_id"],
                        ticket=ticket,
                        promocion_aplicada__isnull=True,
                    )
                agregar_partida(
                    ticket,
                    producto,
                    comensal,
                    cantidad,
                    datos.get("termino"),
                    promocion_aplicada=promocion_aplicada,
                )
            ticket.refresh_from_db()
        return JsonResponse(
            {"ticket": _ticket_payload(_ticket(ticket.id), device_id)}
        )
    except (
        Producto.DoesNotExist,
        ProductoSucursal.DoesNotExist,
        Partida.DoesNotExist,
        ErrorVenta,
        InvalidOperation,
        ValueError,
    ) as exc:
        if isinstance(exc, ErrorVenta):
            return _respuesta_error_venta(exc, device_id)
        return JsonResponse(
            {"error": str(exc) or "Producto no encontrado."},
            status=400,
        )

@require_http_methods(["PATCH", "DELETE"])
def api_partida(request, partida_id):
    device_id = ""
    try:
        datos = _json(request)
        with transaction.atomic():
            partida = Partida.objects.select_related("ticket__sucursal").get(pk=partida_id, sucursal=_sucursal())
            ticket = partida.ticket
            ticket, device_id = _asegurar_edicion_ticket(
                request,
                ticket,
                datos,
                permitir_programado=True,
            )
            cantidad = Decimal("0") if request.method == "DELETE" else Decimal(str(datos.get("cantidad", partida.cantidad)))
            termino = datos.get("termino") if "termino" in datos else None
            if partida.producto_sucursal_id:
                actualizar_partida_sucursal(partida, cantidad)
            else:
                actualizar_partida(partida, cantidad, termino)
            ticket.refresh_from_db()
        return JsonResponse({"ticket": _ticket_payload(_ticket(ticket.id), device_id)})
    except (Partida.DoesNotExist, ErrorVenta, InvalidOperation) as exc:
        if isinstance(exc, ErrorVenta):
            return _respuesta_error_venta(exc, device_id)
        return JsonResponse({"error": str(exc) or "Partida no encontrada."}, status=400)


@require_POST
def api_ajustar_partidas(request, ticket_id):
    device_id = ""
    try:
        datos = _json(request)
        with transaction.atomic():
            ticket = _ticket(ticket_id)
            ticket, device_id = _asegurar_edicion_ticket(
                request,
                ticket,
                datos,
                permitir_programado=True,
            )
            partida_ids = [str(valor) for valor in datos.get("partida_ids", [])]
            eliminar = bool(datos.get("eliminar", False))
            cantidad = Decimal(str(datos.get("cantidad", "1")))
            ajustar_grupo_partidas(ticket, partida_ids, cantidad, datos.get("termino"), eliminar)
            ticket.refresh_from_db()
        return JsonResponse({"ticket": _ticket_payload(_ticket(ticket.id), device_id)})
    except (ErrorVenta, InvalidOperation, ValidationError, ValueError) as exc:
        if isinstance(exc, ErrorVenta):
            return _respuesta_error_venta(exc, device_id)
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
def api_modificador(request, ticket_id):
    device_id = ""
    try:
        datos = _json(request)
        with transaction.atomic():
            ticket = _ticket(ticket_id)
            ticket, device_id = _asegurar_edicion_ticket(
                request,
                ticket,
                datos,
                permitir_programado=True,
            )
            codigo = str(datos.get("codigo", "")).strip()[:20]
            if datos.get("tipo") == "general":
                nombre = COMENTARIOS_GENERALES_PERMITIDOS.get(codigo)
                if not nombre:
                    raise ErrorVenta("Comentario general inválido.")
                alternar_comentario_general(ticket, codigo, nombre)
            else:
                nombre = MODIFICADORES_PERMITIDOS.get(codigo)
                if not nombre:
                    raise ErrorVenta("Modificador inválido.")
                comensales = datos.get("comensales")
                if comensales is not None:
                    comensales = sorted({int(valor) for valor in comensales})
                    if not codigo or not comensales or any(not 1 <= comensal <= 24 for comensal in comensales):
                        raise ErrorVenta("Modificador inválido.")
                    asegurar_modificador(ticket, comensales, codigo, nombre)
                else:
                    comensal = int(datos.get("comensal", 1))
                    if not codigo or not 1 <= comensal <= 24:
                        raise ErrorVenta("Modificador inválido.")
                    alternar_modificador(ticket, comensal, codigo, nombre)
        return JsonResponse({"ticket": _ticket_payload(_ticket(ticket.id), device_id)})
    except (ErrorVenta, ValueError) as exc:
        if isinstance(exc, ErrorVenta):
            return _respuesta_error_venta(exc, device_id)
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
def api_procesar(request, ticket_id):
    device_id = ""
    try:
        datos = _json(request)
        with transaction.atomic():
            ticket = _ticket(ticket_id)
            ticket, device_id = _asegurar_edicion_ticket(request, ticket, datos)
            ticket = procesar_ticket(ticket)
        if ticket.canal == Mesa.Canal.SUCURSALES:
            trabajos = list(encolar_impresiones(ticket, TrabajoImpresion.Formato.SUCURSAL))
        else:
            trabajos = list(encolar_impresiones(ticket, TrabajoImpresion.Formato.COMANDA))
        if ticket.canal == Mesa.Canal.DOMICILIO:
            trabajos.extend(encolar_impresiones(ticket, TrabajoImpresion.Formato.DOMICILIO))
        return JsonResponse({"ticket": _ticket_payload(_ticket(ticket.id), device_id), "impresiones": _trabajos_payload(trabajos)})
    except ErrorVenta as exc:
        return _respuesta_error_venta(exc, device_id)


@require_POST
def api_cobrar(request, ticket_id):
    device_id = ""
    try:
        datos = _json(request)
        ticket_actual = _ticket(ticket_id)
        if ticket_actual.canal == Mesa.Canal.SUCURSALES:
            raise ErrorVenta("Los pedidos de sucursal se completan sin registrar un cobro de caja.")
        _validar_clave_admin_datos(ticket_actual.sucursal, datos)
        forma = datos.get("forma_pago") or Ticket.FormaPago.EFECTIVO
        if forma not in Ticket.FormaPago.values:
            raise ErrorVenta("Forma de pago inválida.")
        recibido = datos.get("importe_recibido")
        if recibido in (None, ""):
            recibido = ticket_actual.total
        with transaction.atomic():
            ticket_actual, device_id = _asegurar_edicion_ticket(request, ticket_actual, datos)
            ticket = cobrar_ticket(ticket_actual, forma, Decimal(str(recibido)) if recibido not in (None, "") else None)
        return JsonResponse(
            {
                "ticket": _ticket_payload(_ticket(ticket.id), device_id),
                "impresiones": [],
            }
        )
    except (ErrorVenta, InvalidOperation) as exc:
        if isinstance(exc, ErrorVenta):
            return _respuesta_error_venta(exc, device_id)
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
@requiere_modulo("pedidos_sucursales")
def api_completar_sucursal(request, ticket_id):
    device_id = ""
    try:
        datos = _json(request)
        with transaction.atomic():
            ticket = _ticket(ticket_id)
            _validar_clave_admin_datos(ticket.sucursal, datos)
            ticket, device_id = _asegurar_edicion_ticket(request, ticket, datos)
            ticket = completar_ticket_sucursal(ticket)
        return JsonResponse({"ticket": _ticket_payload(_ticket(ticket.id), device_id)})
    except ErrorVenta as exc:
        return _respuesta_error_venta(exc, device_id)


@require_POST
def api_cancelar(request, ticket_id):
    device_id = ""
    try:
        datos = _json(request)
        with transaction.atomic():
            ticket = _ticket(ticket_id)
            ticket, device_id = _asegurar_edicion_ticket(request, ticket, datos)
            operador = _operador_actual_pos(request, ticket.sucursal)
            if ticket.estado == Ticket.Estado.ABIERTO:
                ticket = cancelar_ticket(
                    ticket,
                    cancelado_por=operador,
                    cancelado_por_nombre=(
                        "" if operador is not None else "Operador no identificado"
                    ),
                )
            elif (
                ticket.estado == Ticket.Estado.PROCESADO
                and ticket.comanda_en_edicion
                and ticket.comanda_actual > 1
            ):
                ticket = cancelar_comanda_adicional(ticket)
            else:
                _, perfil_elevado = autenticar_acceso_administrador(
                    ticket.sucursal,
                    datos.get("clave_administrador"),
                )
                ticket = cancelar_ticket_administrador(
                    ticket,
                    cancelado_por=perfil_elevado or operador,
                )
        return JsonResponse(
            {"ticket": _ticket_payload(_ticket(ticket.id), device_id)}
        )
    except ErrorVenta as exc:
        return _respuesta_error_venta(exc, device_id)

@require_POST
def api_imprimir(request, ticket_id):
    try:
        ticket = _ticket(ticket_id)
        formato = _json(request).get("formato", "cuenta")
        formatos_operativos = {
            TrabajoImpresion.Formato.COMANDA,
            TrabajoImpresion.Formato.CUENTA,
            TrabajoImpresion.Formato.DOMICILIO,
            TrabajoImpresion.Formato.SUCURSAL,
        }
        if formato not in formatos_operativos:
            raise ErrorVenta("Formato de impresión inválido.")
        if ticket.canal == Mesa.Canal.SUCURSALES and formato != TrabajoImpresion.Formato.SUCURSAL:
            raise ErrorVenta("Los pedidos de sucursal sólo usan el ticket total de sucursal.")
        if ticket.canal != Mesa.Canal.SUCURSALES and formato == TrabajoImpresion.Formato.SUCURSAL:
            raise ErrorVenta("Este pedido no usa el ticket de sucursal.")
        if formato == TrabajoImpresion.Formato.DOMICILIO and ticket.canal != Mesa.Canal.DOMICILIO:
            raise ErrorVenta("Este pedido no usa el ticket de domicilio.")
        if formato == TrabajoImpresion.Formato.CUENTA and ticket.canal not in {
            Mesa.Canal.COMEDOR,
            Mesa.Canal.LLEVAR,
            Mesa.Canal.RECOGER,
            Mesa.Canal.DOMICILIO,
        }:
            raise ErrorVenta("Este pedido no admite ticket total de caja.")
        if ticket.estado not in {
            Ticket.Estado.PROCESADO,
            Ticket.Estado.COBRAR,
            Ticket.Estado.PAGADO,
        } or ticket.comanda_en_edicion:
            raise ErrorVenta("Procesa la comanda actual antes de imprimir.")
        trabajos = encolar_impresiones(ticket, formato)
        return JsonResponse({"impresiones": _trabajos_payload(trabajos)})
    except ErrorVenta as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
def api_admin_acceso(request):
    sucursal = _sucursal()
    clave_limite = clave_intentos(request, "administrador", sucursal)
    if limite_agotado(clave_limite):
        return respuesta_limite()
    try:
        datos = _json(request)
        nivel, perfil = autenticar_acceso_administrador(
            sucursal,
            datos.get("clave_administrador"),
        )
    except ErrorVenta as exc:
        registrar_fallo(clave_limite)
        if limite_agotado(clave_limite):
            return respuesta_limite()
        return JsonResponse({"error": str(exc)}, status=400)

    limpiar_fallos(clave_limite)
    request.session["admin_autorizado_hasta"] = (
        timezone.now() + timedelta(minutes=30)
    ).timestamp()
    request.session["admin_nivel"] = nivel
    if perfil is None:
        request.session.pop("admin_perfil_id", None)
        permisos = PERMISOS_ADMINISTRADOR
    else:
        request.session["admin_perfil_id"] = str(perfil.id)
        permisos = PERMISOS_ELEVADO
    return JsonResponse(
        {
            "ok": True,
            "destino": reverse("ventas:administrador"),
            "nivel": nivel,
            "permisos": dict(permisos),
        }
    )


@require_GET
@acceso_administrador
def api_admin_resumen(request):
    sucursal = _sucursal()
    resumen = resumen_administrador(sucursal)
    sucursales_por_posicion = {
        str(mesa.id): mesa.cliente_sucursal
        for mesa in Mesa.objects.select_related("cliente_sucursal").filter(
            sucursal=sucursal,
            activa=True,
            canal=Mesa.Canal.SUCURSALES,
            cliente_sucursal__isnull=False,
        )
    }
    for posicion in resumen.get("posiciones", []):
        cliente_sucursal = sucursales_por_posicion.get(str(posicion.get("id", "")))
        posicion["cliente_sucursal_id"] = (
            str(cliente_sucursal.id) if cliente_sucursal else ""
        )
        posicion["cliente_sucursal"] = (
            cliente_sucursal.nombre if cliente_sucursal else ""
        )
    resumen["acceso"] = {
        "nivel": request.acceso_administrador["nivel"],
        "permisos": dict(request.acceso_administrador["permisos"]),
    }
    return JsonResponse({"administrador": resumen})


@require_POST
@acceso_administrador_maestro
def api_admin_usuarios(request):
    try:
        datos = _json(request)
        sucursal = _sucursal()
        usuario = guardar_usuario(sucursal, datos)
        return JsonResponse({"usuario": usuario_payload(usuario)}, status=201)
    except ErrorVenta as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_http_methods(["PATCH"])
@acceso_administrador_maestro
def api_admin_usuario(request, usuario_id):
    try:
        datos = _json(request)
        sucursal = _sucursal()
        usuario = UsuarioPOS.objects.get(pk=usuario_id, sucursal=sucursal)
        usuario = guardar_usuario(sucursal, datos, usuario=usuario)
        return JsonResponse({"usuario": usuario_payload(usuario)})
    except UsuarioPOS.DoesNotExist:
        return JsonResponse({"error": "La persona ya no existe."}, status=404)
    except ErrorVenta as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
@acceso_administrador_maestro
def api_admin_cambiar_clave(request):
    try:
        datos = _json(request)
        cambiar_clave_administrador(
            _sucursal(),
            datos.get("clave_administrador"),
            datos.get("nueva_clave"),
        )
        return JsonResponse({"ok": True})
    except ErrorVenta as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
@requiere_modulo("reparto")
@acceso_administrador
def api_admin_asignar_repartidor(request, ticket_id):
    try:
        datos = _json(request)
        ticket = _ticket(ticket_id)
        repartidor = UsuarioPOS.objects.get(pk=datos.get("repartidor_id"), sucursal=ticket.sucursal)
        ticket = asignar_repartidor(ticket, repartidor)
        return JsonResponse({"ticket": _ticket_payload(ticket)})
    except UsuarioPOS.DoesNotExist:
        return JsonResponse({"error": "El repartidor ya no está disponible."}, status=400)
    except ErrorVenta as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
@requiere_modulo("programados")
@acceso_administrador
def api_admin_editar_programado(request, ticket_id):
    device_id = ""
    try:
        datos = _json(request)
        device_id = _device_id(request, datos)
        if not device_id:
            raise ErrorSolicitudJSON(
                "El identificador del dispositivo es obligatorio para editar un pedido programado."
            )
        with transaction.atomic():
            ticket = _ticket(ticket_id)
            if ticket.estado != Ticket.Estado.PROGRAMADO:
                raise ErrorVenta("Este pedido ya no está programado.")
            operador = request.acceso_administrador["perfil"] or _operador_actual_pos(
                request,
                ticket.sucursal,
            )
            ticket = asegurar_bloqueo_ticket(ticket, device_id, operador=operador)
            if ticket.estado != Ticket.Estado.PROGRAMADO:
                raise ErrorVenta("Este pedido ya no está programado.")
        return JsonResponse(
            {
                "ticket": _ticket_payload(ticket, device_id),
                "edicion_programada": True,
            }
        )
    except ErrorVenta as exc:
        return _respuesta_error_venta(exc, device_id)


@require_http_methods(["POST", "PATCH", "DELETE"])
@requiere_modulo("programados")
@acceso_administrador
def api_admin_programar_ticket(request, ticket_id):
    try:
        ticket = _ticket(ticket_id)
        if request.method == "DELETE":
            eliminar_ticket_programado(ticket)
            return JsonResponse(
                {"eliminado": True, "ticket_id": str(ticket_id)}
            )
        datos = _json(request)
        fecha_programada = date.fromisoformat(
            str(datos.get("fecha_programada", ""))
        )
        hora_programada = datetime.strptime(
            str(datos.get("hora_programada", "")),
            "%H:%M",
        ).time()
        ticket = programar_ticket(
            ticket,
            fecha_programada,
            hora_programada,
        )
        return JsonResponse({"ticket": _ticket_payload(ticket)})
    except ValueError:
        return JsonResponse(
            {"error": "La fecha u hora programada no es válida."},
            status=400,
        )
    except ErrorVenta as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
@requiere_modulo("programados")
@acceso_administrador
def api_admin_desprogramar_ticket(request, ticket_id):
    try:
        _json(request)
        ticket = desprogramar_ticket(_ticket(ticket_id))
        return JsonResponse({"ticket": _ticket_payload(ticket)})
    except ErrorVenta as exc:
        return JsonResponse({"error": str(exc)}, status=400)

@require_POST
@acceso_administrador
def api_admin_reasignar_ticket(request, ticket_id):
    try:
        datos = _json(request)
        ticket = _ticket(ticket_id)
        mesa = Mesa.objects.get(pk=datos.get("mesa_id"), sucursal=ticket.sucursal, activa=True)
        ticket = reasignar_ticket(ticket, mesa)
        return JsonResponse({"ticket": _ticket_payload(ticket)})
    except Mesa.DoesNotExist:
        return JsonResponse({"error": "La posición de destino ya no está disponible."}, status=400)
    except ErrorVenta as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
@acceso_administrador
def api_admin_descuento_ticket(request, ticket_id):
    try:
        datos = _json(request)
        ticket = _ticket(ticket_id)
        ticket = aplicar_descuento(ticket, datos.get("porcentaje"))
        return JsonResponse({"ticket": _ticket_payload(ticket)})
    except ErrorVenta as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
@acceso_administrador
def api_admin_cancelar_ticket(request, ticket_id):
    try:
        _json(request)
        ticket = _ticket(ticket_id)
        actor = request.acceso_administrador.get("perfil")
        if actor is None:
            actor = _operador_actual_pos(request, ticket.sucursal)
        ticket = cancelar_ticket_administrador(
            ticket,
            cancelado_por=actor,
        )
        return JsonResponse({"ticket": _ticket_payload(ticket)})
    except ErrorVenta as exc:
        return JsonResponse({"error": str(exc)}, status=400)


def _respuesta_reporte(reporte, extra=None):
    trabajos = encolar_reporte(reporte)
    respuesta = {"reporte_id": str(reporte.id), "impresiones": _trabajos_payload(trabajos)}
    if extra:
        respuesta.update(extra)
    return JsonResponse(respuesta, status=201)


def _movimiento_payload(movimiento):
    return {
        "id": str(movimiento.id),
        "tipo": movimiento.tipo,
        "concepto": movimiento.concepto,
        "importe": str(movimiento.importe),
        "creado_en": movimiento.creado_en.isoformat(),
    }


@require_POST
@requiere_modulo("reparto")
@acceso_administrador
def api_admin_liquidacion_repartidor(request):
    try:
        datos = _json(request)
        sucursal = _sucursal()
        repartidor = UsuarioPOS.objects.get(pk=datos.get("repartidor_id"), sucursal=sucursal)
        liquidacion, reporte = crear_liquidacion_repartidor(
            sucursal,
            repartidor,
            datos.get("fondo", "0"),
        )
        return _respuesta_reporte(
            reporte,
            {"liquidacion_id": str(liquidacion.id), "total_a_entregar": str(liquidacion.total_a_entregar)},
        )
    except UsuarioPOS.DoesNotExist:
        return JsonResponse({"error": "El repartidor ya no está disponible."}, status=400)
    except ErrorVenta as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
@acceso_administrador
def api_admin_reporte_parcial(request):
    try:
        datos = _json(request)
        sucursal = _sucursal()
        return _respuesta_reporte(crear_reporte_parcial(sucursal))
    except ErrorVenta as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
@acceso_administrador
def api_admin_movimientos(request):
    try:
        datos = _json(request)
        sucursal = _sucursal()
        movimiento = agregar_movimiento(
            sucursal,
            datos.get("tipo"),
            datos.get("concepto"),
            datos.get("importe"),
        )
        return JsonResponse(
            {"movimiento": _movimiento_payload(movimiento)},
            status=201,
        )
    except ErrorVenta as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_http_methods(["PUT"])
@acceso_administrador
def api_admin_control_efectivo(request):
    try:
        datos = _json(request)
        sucursal = _sucursal()
        control = guardar_control_efectivo(sucursal, datos)
        resumen = resumen_administrador(sucursal)
        return JsonResponse(
            {"control_efectivo": resumen["control_efectivo"]}
        )
    except ErrorVenta as exc:
        return JsonResponse({"error": str(exc)}, status=400)

@require_http_methods(["PATCH", "DELETE"])
@acceso_administrador
def api_admin_movimiento(request, movimiento_id):
    try:
        sucursal = _sucursal()
        movimiento = MovimientoCaja.objects.get(pk=movimiento_id, sucursal=sucursal)
        if request.method == "DELETE":
            eliminar_movimiento(sucursal, movimiento)
            return JsonResponse({"eliminado": True, "movimiento_id": str(movimiento_id)})
        datos = _json(request)
        movimiento = actualizar_movimiento(
            sucursal,
            movimiento,
            datos.get("tipo"),
            datos.get("concepto"),
            datos.get("importe"),
        )
        return JsonResponse({"movimiento": _movimiento_payload(movimiento)})
    except MovimientoCaja.DoesNotExist:
        return JsonResponse({"error": "El movimiento ya no existe."}, status=404)
    except ErrorVenta as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
@acceso_administrador
def api_admin_acciones_tickets_lote(request):
    try:
        datos = _json(request)
        ticket_ids_crudos = datos.get("ticket_ids")
        if not isinstance(ticket_ids_crudos, list):
            raise ErrorSolicitudJSON("La selección de pedidos no es válida.")
        ticket_ids = [UUID(str(ticket_id)) for ticket_id in ticket_ids_crudos]
        if len(set(ticket_ids)) != len(ticket_ids):
            raise ErrorSolicitudJSON("La selección contiene pedidos repetidos.")
        sucursal = _sucursal()
        repartidor = None
        if datos.get("accion") == "asignar_repartidor":
            repartidor = UsuarioPOS.objects.get(
                pk=datos.get("repartidor_id"),
                sucursal=sucursal,
            )
        tickets = aplicar_accion_tickets_lote(
            sucursal,
            datos.get("accion"),
            ticket_ids,
            forma_pago=datos.get("forma_pago"),
            repartidor=repartidor,
        )
        return JsonResponse(
            {
                "accion": datos.get("accion"),
                "tickets": [
                    _ticket_payload(_ticket(ticket.id))
                    for ticket in tickets
                ],
            }
        )
    except (ValueError, ValidationError, ErrorSolicitudJSON) as exc:
        return JsonResponse({"error": str(exc) or "La selección de pedidos no es válida."}, status=400)
    except UsuarioPOS.DoesNotExist:
        return JsonResponse({"error": "El repartidor ya no está disponible."}, status=400)
    except ErrorVenta as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
@acceso_administrador_maestro
def api_admin_reiniciar_folios(request):
    try:
        _json(request)
        reiniciar_folios(_sucursal())
        return JsonResponse({"ok": True, "siguiente_folio": 1})
    except ErrorVenta as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
@acceso_administrador
def api_admin_reimprimir_reporte(request, reporte_id):
    try:
        _json(request)
        reporte = ReporteAdministrativo.objects.get(
            pk=reporte_id,
            sucursal=_sucursal(),
        )
        return _respuesta_reporte(reporte)
    except ReporteAdministrativo.DoesNotExist:
        return JsonResponse(
            {"error": "El reporte solicitado ya no está disponible."},
            status=404,
        )


@require_POST
@acceso_administrador
def api_admin_consolidacion_mensual(request):
    try:
        datos = _json(request)
        consolidacion = consolidar_periodo(
            _sucursal(),
            periodo=datos.get("periodo") or None,
        )
        return JsonResponse(
            {
                "ok": True,
                "periodo": consolidacion.periodo.isoformat(),
                "estado": consolidacion.estado,
                "acuse_vps": consolidacion.acuse_vps,
                "siguiente_folio": 1,
            }
        )
    except ErrorVenta as exc:
        return JsonResponse({"error": str(exc)}, status=400)

@require_POST
@acceso_administrador
def api_admin_corte_caja(request):
    try:
        datos = _json(request)
        sucursal = _sucursal()
        corte, reporte = crear_corte_caja(sucursal)
        return _respuesta_reporte(
            reporte,
            {"corte_id": str(corte.id), "total_caja": str(corte.total_caja)},
        )
    except ErrorVenta as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
@requiere_modulo("pedidos_sucursales")
@acceso_administrador
def api_admin_corte_sucursal(request):
    try:
        datos = _json(request)
        sucursal = _sucursal()
        cliente = SucursalPedido.objects.get(pk=datos.get("cliente_sucursal_id"), sucursal=sucursal)
        corte, reporte = crear_corte_sucursal(sucursal, cliente)
        return _respuesta_reporte(
            reporte,
            {"corte_id": str(corte.id), "total": str(corte.total)},
        )
    except SucursalPedido.DoesNotExist:
        return JsonResponse({"error": "La sucursal ya no está disponible."}, status=400)
    except ErrorVenta as exc:
        return JsonResponse({"error": str(exc)}, status=400)


def manifest(request):
    modo_tableta = request.GET.get("modo") == "tableta"
    inicio = "/tabletas/" if modo_tableta else "/"
    return JsonResponse(
        {
            "name": "Los Tocayos Comedor" if modo_tableta else "Los Tocayos POS",
            "short_name": "Tocayos Comedor" if modo_tableta else "Tocayos POS",
            "id": inicio,
            "start_url": inicio,
            "scope": "/",
            "display": "standalone",
            "display_override": ["fullscreen", "standalone"],
            "background_color": "#ffed00",
            "theme_color": "#ffed00",
            "icons": [
                {"src": "/static/ventas/brand/logoactual.jpeg", "sizes": "1181x1181", "type": "image/jpeg", "purpose": "any"}
            ],
        },
        content_type="application/manifest+json",
    )


def service_worker(request):
    recursos_menu = {
        static(f"{RUTA_MENU_ESTATICO}/{archivo}")
        for archivo in (*IMAGEN_MENU_POR_CODIGO.values(), IMAGEN_MENU_PREDETERMINADA)
    }
    precache = [
        f"{static('ventas/app.css')}?v={ASSET_VERSION}",
        f"{static('ventas/brand-pos.css')}?v={ASSET_VERSION}",
        f"{static('ventas/app.js')}?v={ASSET_VERSION}",
        f"{static('ventas/admin.css')}?v={ASSET_VERSION}",
        f"{static('ventas/admin.js')}?v={ASSET_VERSION}",
        static("ventas/brand/logoactual.jpeg"),
        static("ventas/fonts/Montserrat-Variable.woff2"),
        static("ventas/fonts/BebasNeue-Regular.woff2"),
        *sorted(recursos_menu),
    ]
    codigo = """const CACHE='__CACHE__';
const PRECACHE=__PRECACHE__;
self.addEventListener('install', e => e.waitUntil(caches.open(CACHE).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting())));
self.addEventListener('activate', e => e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim())));
self.addEventListener('fetch', e => { if (e.request.method === 'GET') e.respondWith(fetch(e.request).catch(() => caches.match(e.request))); });
""".replace("__CACHE__", PWA_CACHE).replace("__PRECACHE__", json.dumps(precache))
    return HttpResponse(codigo, content_type="application/javascript", headers={"Cache-Control": "no-cache"})
