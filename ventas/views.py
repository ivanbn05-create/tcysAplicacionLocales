import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from catalogo.models import Producto
from impresion.models import TrabajoImpresion
from impresion.services import encolar_impresiones, estado_impresora
from personas.models import Sucursal

from .clientes import (
    ErrorCliente,
    buscar_clientes,
    cliente_payload,
    duplicados_por_nombre,
    guardar_cliente,
)
from .models import Cliente, DomicilioCliente, Mesa, Partida, TelefonoCliente, Ticket
from .normalizacion import normalizar_telefono
from .orden import ordenar_partidas
from .promociones import configuracion_promocion, promocion_disponible, promociones_pendientes
from .services import (
    ErrorVenta,
    abrir_ticket,
    asegurar_modificador,
    ajustar_grupo_partidas,
    actualizar_partida,
    agregar_partida,
    alternar_comentario_general,
    alternar_modificador,
    cobrar_ticket,
    cancelar_ticket,
    convertir_tipo_ticket,
    procesar_ticket,
    registrar_evento,
    validar_limite_productos_por_nombre,
)


PREFIJOS_SALSA = {"", "+ Más", "Nada más"}
OPCIONES_SALSA = {
    "Con Todo", "Sin Nada", "Sólo Salsas", "Verde", "Roja", "Pepino", "Rábano", "Cebolla",
    "Limón", "Morada", "Serrano", "Cilantro", "Cacahuate", "Chipotle", "Mexicana",
    "Verde Tomate", "Habanero", "Roja Taquera",
}

# Cambiar este valor obliga a las terminales y tabletas instaladas a descargar
# los recursos de interfaz de esta entrega, incluso si conservan una caché PWA.
ASSET_VERSION = "20260823-1"


def _sucursal():
    try:
        return Sucursal.objects.get(clave=settings.SUCURSAL_CLAVE, activa=True)
    except Sucursal.DoesNotExist as exc:
        raise Http404("Ejecuta: python manage.py cargar_datos_iniciales") from exc


def _json(request):
    try:
        return json.loads(request.body or b"{}")
    except json.JSONDecodeError as exc:
        raise ErrorVenta("El cuerpo JSON no es válido.") from exc


def _ticket(ticket_id):
    try:
        return Ticket.objects.select_related(
            "mesa", "cliente", "telefono_cliente", "domicilio_cliente", "atendio", "sucursal"
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
                "error": trabajo.error,
                "url": f"{settings.MEDIA_URL}{trabajo.archivo}" if trabajo.archivo else "",
            }
        )
    return resultado


def _ticket_payload(ticket):
    partidas = []
    consulta_partidas = ticket.partidas.select_related("producto__categoria", "promocion_aplicada__producto").all()
    for partida in ordenar_partidas(consulta_partidas):
        promocion = configuracion_promocion(partida.producto)
        partidas.append(
            {
                "id": str(partida.id),
                "producto_id": str(partida.producto_id),
                "codigo": partida.producto.codigo,
                "nombre": partida.nombre_producto,
                "nombre_corto": partida.nombre_corto,
                "comensal": partida.comensal,
                "cantidad": str(partida.cantidad),
                "precio": str(partida.precio_unitario),
                "importe": str(partida.importe),
                "categoria": partida.producto.categoria.nombre,
                "destino": partida.producto.destino_impresion,
                "termino": partida.termino,
                "orden": partida.producto.orden,
                "es_promocion": bool(promocion),
                "promocion_id": str(partida.promocion_aplicada_id) if partida.promocion_aplicada_id else "",
            }
        )
    modificadores = [
        {"id": str(mod.id), "comensal": mod.comensal, "codigo": mod.codigo, "nombre": mod.nombre}
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
    pendientes = promociones_pendientes(ticket)
    return {
        "id": str(ticket.id),
        "folio": ticket.folio,
        "estado": ticket.estado,
        "canal": ticket.canal,
        "mesa_id": str(ticket.mesa_id),
        "mesa": ticket.mesa.nombre,
        "posicion_numero": ticket.mesa.orden,
        "total": str(ticket.total),
        "creado_en": ticket.creado_en.isoformat(),
        "comentario_general": ticket.comentario_general,
        "comentarios_generales": ticket.comentarios_generales,
        "salsas_verduras": ticket.salsas_verduras,
        "tipo_entrega": ticket.tipo_entrega,
        "entrega_aproximada": ticket.entrega_aproximada.strftime("%H:%M") if ticket.entrega_aproximada else "",
        "terminal": ticket.terminal,
        "paga_con": str(ticket.paga_con) if ticket.paga_con is not None else "",
        "captura_por_nombres": ticket.captura_por_nombres,
        "nombres_comensales": ticket.nombres_comensales,
        "promocion_pendiente_id": pendientes[0] if pendientes else "",
        "promociones_pendientes": pendientes,
        "cliente": cliente,
        "partidas": partidas,
        "modificadores": modificadores,
    }


def _inicio(request, modo_tableta=False):
    sucursal = _sucursal()
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
                    "orden": producto.orden,
                    "es_promocion": bool(promocion),
                    "promocion_dias": promocion["dias_texto"] if promocion else "",
                    "disponible_hoy": promocion_disponible(producto, timezone.localdate()) if promocion else True,
                }
            )
    posiciones = [
        {"id": str(mesa.id), "canal": mesa.canal, "clave": mesa.clave, "nombre": mesa.nombre, "orden": mesa.orden}
        for mesa in Mesa.objects.filter(sucursal=sucursal, activa=True)
    ]
    return render(
        request,
        "ventas/inicio.html",
        {
            "sucursal": sucursal,
            "productos": productos,
            "posiciones": posiciones,
            "modo_tableta": modo_tableta,
            "asset_version": ASSET_VERSION,
        },
    )


def inicio(request):
    return _inicio(request)


def tabletas(request):
    return _inicio(request, modo_tableta=True)


@require_GET
def api_estado(request):
    sucursal = _sucursal()
    activos = Ticket.objects.filter(
        sucursal=sucursal,
        estado__in=[Ticket.Estado.ABIERTO, Ticket.Estado.PROCESADO, Ticket.Estado.COBRAR],
    ).select_related("mesa")
    tickets = {
        str(ticket.mesa_id): {
            "ticket_id": str(ticket.id),
            "folio": ticket.folio,
            "estado": ticket.estado,
            "total": str(ticket.total),
        }
        for ticket in activos
    }
    return JsonResponse({"tickets": tickets})


@require_GET
def api_estado_impresion(request):
    return JsonResponse(estado_impresora())


@require_POST
def api_abrir_ticket(request):
    try:
        datos = _json(request)
        mesa = Mesa.objects.get(pk=datos.get("mesa_id"), sucursal=_sucursal(), activa=True)
        ticket, creado = abrir_ticket(mesa)
        return JsonResponse({"creado": creado, "ticket": _ticket_payload(ticket)})
    except (Mesa.DoesNotExist, ErrorVenta) as exc:
        return JsonResponse({"error": str(exc) or "Posición no encontrada."}, status=400)


@require_http_methods(["GET", "PATCH"])
def api_ticket(request, ticket_id):
    ticket = _ticket(ticket_id)
    if request.method == "PATCH":
        try:
            datos = _json(request)
            if ticket.estado != Ticket.Estado.ABIERTO:
                raise ErrorVenta("La orden ya fue procesada.")
            if "comentario_general" in datos:
                ticket.comentario_general = str(datos["comentario_general"]).strip()
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
                normalizados = []
                for grupo in grupos:
                    if not isinstance(grupo, dict):
                        raise ErrorVenta("La selección de salsas y verduras no es válida.")
                    prefijo = str(grupo.get("prefijo", ""))
                    elementos = list(dict.fromkeys(str(item) for item in grupo.get("elementos", [])))
                    if prefijo not in PREFIJOS_SALSA or not elementos or any(item not in OPCIONES_SALSA for item in elementos):
                        raise ErrorVenta("La selección de salsas y verduras contiene una opción no válida.")
                    normalizados.append({"prefijo": prefijo, "elementos": elementos})
                ticket.salsas_verduras = normalizados
            ticket.save()
            registrar_evento(ticket, "ticket.datos_actualizados", {"canal": ticket.canal})
        except (ErrorVenta, InvalidOperation, ValueError) as exc:
            return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"ticket": _ticket_payload(ticket)})


@require_GET
def api_buscar_clientes(request):
    try:
        limite = int(request.GET.get("limite", 10))
        return JsonResponse({"resultados": buscar_clientes(_sucursal(), request.GET.get("q", ""), limite)})
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
    try:
        ticket = _ticket(ticket_id)
        if ticket.estado != Ticket.Estado.ABIERTO:
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
            datos = _json(request)
            cliente = Cliente.objects.get(pk=datos.get("cliente_id"), sucursal=ticket.sucursal, activo=True)
            telefono = None
            if datos.get("telefono_id"):
                telefono = TelefonoCliente.objects.get(
                    pk=datos.get("telefono_id"), cliente=cliente, sucursal=ticket.sucursal, activo=True
                )
            if not telefono and not cliente.comentarios_multiples:
                raise ErrorVenta("Selecciona un teléfono para el cliente.")
            domicilio = DomicilioCliente.objects.get(
                pk=datos.get("domicilio_id"), cliente=cliente, sucursal=ticket.sucursal, activo=True
            )
            ticket.cliente = cliente
            ticket.telefono_cliente = telefono
            ticket.domicilio_cliente = domicilio
            ticket.cliente_nombre = cliente.nombre
            ticket.cliente_telefono = telefono.numero if telefono else ""
            ticket.cliente_domicilio = domicilio.texto_completo
            ticket.cliente_referencia = domicilio.referencia
            # El contacto pertenece exclusivamente al pedido actual. Nunca debe
            # sobrevivir al cambio o a la nueva selección de una empresa.
            ticket.contacto_pedido_nombre = ""
            ticket.contacto_pedido_telefono = ""
        ticket.save()
        registrar_evento(
            ticket,
            "ticket.cliente_asignado",
            {"cliente_id": str(ticket.cliente_id) if ticket.cliente_id else None},
        )
        return JsonResponse({"ticket": _ticket_payload(ticket)})
    except (Cliente.DoesNotExist, TelefonoCliente.DoesNotExist, DomicilioCliente.DoesNotExist):
        return JsonResponse({"error": "El cliente, teléfono o domicilio seleccionado ya no está disponible."}, status=400)
    except ErrorVenta as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
def api_convertir_ticket(request, ticket_id):
    try:
        ticket = _ticket(ticket_id)
        canal_destino = str(_json(request).get("canal", ""))
        ticket = convertir_tipo_ticket(ticket, canal_destino)
        return JsonResponse({"ticket": _ticket_payload(ticket)})
    except ErrorVenta as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
def api_agregar_partida(request, ticket_id):
    try:
        ticket = _ticket(ticket_id)
        datos = _json(request)
        producto = Producto.objects.get(pk=datos.get("producto_id"), sucursal=ticket.sucursal)
        comensal = int(datos.get("comensal", 1))
        cantidad = Decimal(str(datos.get("cantidad", "1")))
        if not 1 <= comensal <= 24 or cantidad <= 0:
            raise ErrorVenta("Comensal o cantidad fuera de rango.")
        promocion_aplicada = None
        if datos.get("promocion_id"):
            promocion_aplicada = Partida.objects.select_related("producto").get(
                pk=datos["promocion_id"], ticket=ticket, promocion_aplicada__isnull=True
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
        return JsonResponse({"ticket": _ticket_payload(ticket)})
    except (Producto.DoesNotExist, Partida.DoesNotExist, ErrorVenta, InvalidOperation, ValueError) as exc:
        return JsonResponse({"error": str(exc) or "Producto no encontrado."}, status=400)


@require_http_methods(["PATCH", "DELETE"])
def api_partida(request, partida_id):
    try:
        partida = Partida.objects.select_related("ticket").get(pk=partida_id, sucursal=_sucursal())
        ticket = partida.ticket
        datos = {} if request.method == "DELETE" else _json(request)
        cantidad = Decimal("0") if request.method == "DELETE" else Decimal(str(datos.get("cantidad", partida.cantidad)))
        termino = datos.get("termino") if "termino" in datos else None
        actualizar_partida(partida, cantidad, termino)
        ticket.refresh_from_db()
        return JsonResponse({"ticket": _ticket_payload(ticket)})
    except (Partida.DoesNotExist, ErrorVenta, InvalidOperation) as exc:
        return JsonResponse({"error": str(exc) or "Partida no encontrada."}, status=400)


@require_POST
def api_ajustar_partidas(request, ticket_id):
    try:
        ticket = _ticket(ticket_id)
        datos = _json(request)
        partida_ids = [str(valor) for valor in datos.get("partida_ids", [])]
        eliminar = bool(datos.get("eliminar", False))
        cantidad = Decimal(str(datos.get("cantidad", "1")))
        ajustar_grupo_partidas(ticket, partida_ids, cantidad, datos.get("termino"), eliminar)
        ticket.refresh_from_db()
        return JsonResponse({"ticket": _ticket_payload(ticket)})
    except (ErrorVenta, InvalidOperation, ValidationError, ValueError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
def api_modificador(request, ticket_id):
    try:
        ticket = _ticket(ticket_id)
        datos = _json(request)
        codigo = str(datos.get("codigo", "")).strip()[:20]
        nombre = str(datos.get("nombre", codigo)).strip()[:60]
        if datos.get("tipo") == "general":
            if not codigo:
                raise ErrorVenta("Comentario general inválido.")
            alternar_comentario_general(ticket, codigo, nombre)
        else:
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
        return JsonResponse({"ticket": _ticket_payload(ticket)})
    except (ErrorVenta, ValueError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
def api_procesar(request, ticket_id):
    try:
        ticket = procesar_ticket(_ticket(ticket_id))
        trabajos = list(encolar_impresiones(ticket, TrabajoImpresion.Formato.COMANDA))
        if ticket.canal == Mesa.Canal.DOMICILIO:
            trabajos.extend(encolar_impresiones(ticket, TrabajoImpresion.Formato.DOMICILIO))
        return JsonResponse({"ticket": _ticket_payload(ticket), "impresiones": _trabajos_payload(trabajos)})
    except ErrorVenta as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
def api_cobrar(request, ticket_id):
    try:
        datos = _json(request)
        forma = datos.get("forma_pago", Ticket.FormaPago.EFECTIVO)
        if forma not in Ticket.FormaPago.values:
            raise ErrorVenta("Forma de pago inválida.")
        recibido = datos.get("importe_recibido")
        ticket = cobrar_ticket(_ticket(ticket_id), forma, Decimal(str(recibido)) if recibido not in (None, "") else None)
        imprimir_ticket = bool(datos.get("imprimir_ticket", True)) and ticket.canal != Mesa.Canal.RECOGER
        trabajos = encolar_impresiones(ticket, TrabajoImpresion.Formato.CUENTA) if imprimir_ticket else []
        return JsonResponse({"ticket": _ticket_payload(ticket), "impresiones": _trabajos_payload(trabajos)})
    except (ErrorVenta, InvalidOperation) as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
def api_cancelar(request, ticket_id):
    try:
        ticket = cancelar_ticket(_ticket(ticket_id))
        return JsonResponse({"ticket": _ticket_payload(ticket)})
    except ErrorVenta as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
def api_imprimir(request, ticket_id):
    try:
        ticket = _ticket(ticket_id)
        formato = _json(request).get("formato", "cuenta")
        if formato not in TrabajoImpresion.Formato.values:
            raise ErrorVenta("Formato de impresión inválido.")
        if ticket.canal == Mesa.Canal.RECOGER and formato != TrabajoImpresion.Formato.COMANDA:
            raise ErrorVenta("Los pedidos para recoger sólo imprimen comanda.")
        trabajos = encolar_impresiones(ticket, formato)
        return JsonResponse({"impresiones": _trabajos_payload(trabajos)})
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
                {"src": "/static/ventas/brand/logoactual.jpeg", "sizes": "1181x1181", "type": "image/jpeg", "purpose": "any maskable"}
            ],
        },
        content_type="application/manifest+json",
    )


def service_worker(request):
    codigo = """const CACHE='tocayos-pos-v9';
self.addEventListener('install', e => e.waitUntil(caches.open(CACHE).then(c => c.addAll(['/static/ventas/app.css?v=20260823-1','/static/ventas/app.js?v=20260823-1','/static/ventas/brand/logoactual.jpeg','/static/ventas/fonts/Montserrat-Medium.ttf','/static/ventas/fonts/Montserrat-SemiBold.ttf']))));
self.addEventListener('activate', e => e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))));
self.addEventListener('fetch', e => { if (e.request.method === 'GET') e.respondWith(fetch(e.request).catch(() => caches.match(e.request))); });
"""
    return HttpResponse(codigo, content_type="application/javascript", headers={"Cache-Control": "no-cache"})
