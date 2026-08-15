import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from catalogo.models import Producto
from impresion.models import TrabajoImpresion
from impresion.services import encolar_impresiones, estado_impresora
from personas.models import Sucursal

from .models import Cliente, Mesa, Partida, Ticket
from .services import (
    ErrorVenta,
    abrir_ticket,
    asegurar_modificador,
    actualizar_partida,
    agregar_partida,
    alternar_modificador,
    cobrar_ticket,
    procesar_ticket,
    registrar_evento,
)


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
        return Ticket.objects.select_related("mesa", "cliente", "atendio", "sucursal").get(pk=ticket_id, sucursal=_sucursal())
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
    for partida in ticket.partidas.select_related("producto__categoria").all():
        partidas.append(
            {
                "id": str(partida.id),
                "producto_id": str(partida.producto_id),
                "nombre": partida.nombre_producto,
                "nombre_corto": partida.nombre_corto,
                "comensal": partida.comensal,
                "cantidad": str(partida.cantidad),
                "precio": str(partida.precio_unitario),
                "importe": str(partida.importe),
                "categoria": partida.producto.categoria.nombre,
                "destino": partida.producto.destino_impresion,
            }
        )
    modificadores = [
        {"id": str(mod.id), "comensal": mod.comensal, "codigo": mod.codigo, "nombre": mod.nombre}
        for mod in ticket.modificadores.all()
    ]
    return {
        "id": str(ticket.id),
        "folio": ticket.folio,
        "estado": ticket.estado,
        "canal": ticket.canal,
        "mesa_id": str(ticket.mesa_id),
        "mesa": ticket.mesa.nombre,
        "total": str(ticket.total),
        "creado_en": ticket.creado_en.isoformat(),
        "comentario_general": ticket.comentario_general,
        "entrega_aproximada": ticket.entrega_aproximada.strftime("%H:%M") if ticket.entrega_aproximada else "",
        "cliente": {
            "nombre": ticket.cliente.nombre if ticket.cliente else "",
            "telefono": ticket.cliente.telefono if ticket.cliente else "",
            "domicilio": ticket.cliente.domicilio if ticket.cliente else "",
            "referencia": ticket.cliente.referencia if ticket.cliente else "",
        },
        "partidas": partidas,
        "modificadores": modificadores,
    }


def _inicio(request, modo_tableta=False):
    sucursal = _sucursal()
    productos = []
    for producto in Producto.objects.select_related("categoria").filter(sucursal=sucursal, activo=True):
        precio = producto.precio_actual()
        if precio:
            productos.append(
                {
                    "id": str(producto.id),
                    "codigo": producto.codigo,
                    "nombre": producto.nombre,
                    "corto": producto.nombre_corto,
                    "categoria": producto.categoria.nombre,
                    "precio": str(precio.importe),
                    "destino": producto.destino_impresion,
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
            ticket.comentario_general = str(datos.get("comentario_general", ticket.comentario_general)).strip()
            entrega = datos.get("entrega_aproximada")
            ticket.entrega_aproximada = datetime.strptime(entrega, "%H:%M").time() if entrega else None
            if ticket.canal == Mesa.Canal.DOMICILIO and datos.get("cliente_nombre"):
                if not ticket.cliente:
                    ticket.cliente = Cliente.objects.create(sucursal=ticket.sucursal, nombre=datos["cliente_nombre"].strip())
                ticket.cliente.nombre = datos["cliente_nombre"].strip()
                ticket.cliente.telefono = str(datos.get("cliente_telefono", "")).strip()
                ticket.cliente.domicilio = str(datos.get("cliente_domicilio", "")).strip()
                ticket.cliente.referencia = str(datos.get("cliente_referencia", "")).strip()
                ticket.cliente.save()
            ticket.save()
            registrar_evento(ticket, "ticket.datos_actualizados", {"canal": ticket.canal})
        except (ErrorVenta, ValueError) as exc:
            return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"ticket": _ticket_payload(ticket)})


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
        agregar_partida(ticket, producto, comensal, cantidad)
        ticket.refresh_from_db()
        return JsonResponse({"ticket": _ticket_payload(ticket)})
    except (Producto.DoesNotExist, ErrorVenta, InvalidOperation, ValueError) as exc:
        return JsonResponse({"error": str(exc) or "Producto no encontrado."}, status=400)


@require_http_methods(["PATCH", "DELETE"])
def api_partida(request, partida_id):
    try:
        partida = Partida.objects.select_related("ticket").get(pk=partida_id, sucursal=_sucursal())
        ticket = partida.ticket
        cantidad = Decimal("0") if request.method == "DELETE" else Decimal(str(_json(request).get("cantidad", "1")))
        actualizar_partida(partida, cantidad)
        ticket.refresh_from_db()
        return JsonResponse({"ticket": _ticket_payload(ticket)})
    except (Partida.DoesNotExist, ErrorVenta, InvalidOperation) as exc:
        return JsonResponse({"error": str(exc) or "Partida no encontrada."}, status=400)


@require_POST
def api_modificador(request, ticket_id):
    try:
        ticket = _ticket(ticket_id)
        datos = _json(request)
        codigo = str(datos.get("codigo", "")).strip()[:20]
        nombre = str(datos.get("nombre", codigo)).strip()[:60]
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
        trabajos = encolar_impresiones(ticket, TrabajoImpresion.Formato.COMANDA)
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
        trabajos = encolar_impresiones(ticket, TrabajoImpresion.Formato.CUENTA)
        return JsonResponse({"ticket": _ticket_payload(ticket), "impresiones": _trabajos_payload(trabajos)})
    except (ErrorVenta, InvalidOperation) as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
def api_imprimir(request, ticket_id):
    try:
        ticket = _ticket(ticket_id)
        formato = _json(request).get("formato", "cuenta")
        if formato not in TrabajoImpresion.Formato.values:
            raise ErrorVenta("Formato de impresión inválido.")
        trabajos = encolar_impresiones(ticket, formato)
        return JsonResponse({"impresiones": _trabajos_payload(trabajos)})
    except ErrorVenta as exc:
        return JsonResponse({"error": str(exc)}, status=400)


def manifest(request):
    return JsonResponse(
        {
            "name": "Los Tocayos POS",
            "short_name": "Tocayos POS",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#ffed00",
            "theme_color": "#ffed00",
            "icons": [
                {"src": "/static/ventas/brand/logoactual.jpeg", "sizes": "1181x1181", "type": "image/jpeg", "purpose": "any maskable"}
            ],
        },
        content_type="application/manifest+json",
    )


def service_worker(request):
    codigo = """const CACHE='tocayos-pos-v2';
self.addEventListener('install', e => e.waitUntil(caches.open(CACHE).then(c => c.addAll(['/static/ventas/app.css','/static/ventas/app.js','/static/ventas/brand/logoactual.jpeg','/static/ventas/fonts/Montserrat-Medium.ttf','/static/ventas/fonts/Montserrat-SemiBold.ttf']))));
self.addEventListener('fetch', e => { if (e.request.method === 'GET') e.respondWith(fetch(e.request).catch(() => caches.match(e.request))); });
"""
    return HttpResponse(codigo, content_type="application/javascript")
