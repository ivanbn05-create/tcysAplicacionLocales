"""Acciones permitidas del panel técnico local del Edge."""

import json
import re
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.core.exceptions import RequestDataTooBig, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from impresion.models import (
    AsignacionImpresoraTerminal,
    ConfiguracionImpresionTerminal,
    Impresora,
    TrabajoImpresion,
)
from impresion.network import normalizar_host_impresora, sondear_recurso_impresora
from impresion.services import resolver_ruta_impresion
from personas.models import Sucursal
from pos.version import APP_VERSION
from ventas.models import EstadoSincronizacionPedidos, EventoOutbox

from .access import acceso_soporte_tecnico
from .impresion import estado_validacion_impresion
from .models import ConfiguracionAccesoEdge, LatidoServicio, ValidacionImpresionFisica


_DEVICE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_DESTINOS = frozenset(AsignacionImpresoraTerminal.Destino.values)


class DatosInvalidos(ValueError):
    pass


def _sucursal():
    return get_object_or_404(
        Sucursal, clave=settings.SUCURSAL_CLAVE, activa=True
    )


def _json(request):
    try:
        cuerpo = request.body
    except RequestDataTooBig as exc:
        raise DatosInvalidos("La solicitud supera el tamaño permitido.") from exc
    if len(cuerpo) > 65536:
        raise DatosInvalidos("La solicitud supera el tamaño permitido.")
    try:
        datos = json.loads(cuerpo or b"{}")
    except (ValueError, UnicodeDecodeError) as exc:
        raise DatosInvalidos("El JSON no es válido.") from exc
    if not isinstance(datos, dict):
        raise DatosInvalidos("Se espera un objeto JSON.")
    return datos


def _campos(datos, permitidos, obligatorios=()):
    desconocidos = set(datos) - set(permitidos)
    if desconocidos:
        raise DatosInvalidos("La solicitud contiene campos no permitidos.")
    if not datos:
        raise DatosInvalidos("Indica al menos un dato.")
    if not set(obligatorios).issubset(datos):
        raise DatosInvalidos("Faltan datos obligatorios.")


def _texto(valor, etiqueta, maximo):
    if not isinstance(valor, str):
        raise DatosInvalidos(f"{etiqueta} debe ser texto.")
    texto = valor.strip()
    if not texto or len(texto) > maximo or any(ord(c) < 32 for c in texto):
        raise DatosInvalidos(f"{etiqueta} debe tener entre 1 y {maximo} caracteres.")
    return texto


def _puerto(valor):
    if isinstance(valor, bool):
        raise DatosInvalidos("El puerto debe estar entre 1 y 65535.")
    if isinstance(valor, str) and valor.isascii() and valor.isdigit():
        valor = int(valor)
    if not isinstance(valor, int) or not 1 <= valor <= 65535:
        raise DatosInvalidos("El puerto debe estar entre 1 y 65535.")
    return valor


def _booleano(valor, etiqueta):
    if not isinstance(valor, bool):
        raise DatosInvalidos(f"{etiqueta} debe ser verdadero o falso.")
    return valor


def _respuesta(datos, status=200):
    return JsonResponse(datos, status=status)


def _error(exc, status=400):
    return _respuesta({"error": str(exc)}, status=status)


def _impresora_payload(impresora):
    return {
        "id": str(impresora.pk),
        "nombre": impresora.nombre,
        "host": impresora.host,
        "puerto": impresora.puerto,
        "descripcion": impresora.descripcion,
        "activa": impresora.activa,
        "actualizado_en": impresora.actualizado_en.isoformat(),
    }


def _terminal_payload(terminal):
    asignaciones = terminal.asignaciones_impresora.select_related("impresora")
    return {
        "id": str(terminal.pk),
        "device_id": terminal.device_id,
        "nombre": terminal.nombre,
        "activa": terminal.activa,
        "rutas": {
            item.destino: str(item.impresora_id) for item in asignaciones
        },
        "heredada": {
            "caja": terminal.host_caja or "",
            "cocina": terminal.host_cocina or "",
            "barra": terminal.host_barra or "",
        },
        "actualizado_en": terminal.actualizado_en.isoformat(),
    }


def _datos_impresora(datos, parcial=False):
    _campos(
        datos, {"nombre", "host", "puerto", "descripcion", "activa"},
        () if parcial else {"nombre", "host"},
    )
    salida = {}
    if "nombre" in datos:
        salida["nombre"] = _texto(datos["nombre"], "El nombre", 100)
    if "host" in datos:
        try:
            salida["host"] = normalizar_host_impresora(datos["host"])
        except ValueError as exc:
            raise DatosInvalidos(str(exc)) from exc
    if "puerto" in datos:
        salida["puerto"] = _puerto(datos["puerto"])
    if "descripcion" in datos:
        descripcion = datos["descripcion"]
        if not isinstance(descripcion, str) or len(descripcion.strip()) > 240:
            raise DatosInvalidos("La descripción debe tener máximo 240 caracteres.")
        salida["descripcion"] = descripcion.strip()
    if "activa" in datos:
        salida["activa"] = _booleano(datos["activa"], "Activa")
    return salida


def _datos_terminal(datos, parcial=False):
    _campos(
        datos, {"nombre", "device_id", "activa"},
        () if parcial else {"nombre", "device_id"},
    )
    salida = {}
    if "nombre" in datos:
        salida["nombre"] = _texto(datos["nombre"], "El nombre", 100)
    if "device_id" in datos:
        valor = datos["device_id"]
        if not isinstance(valor, str) or not _DEVICE_ID.fullmatch(valor):
            raise DatosInvalidos("El identificador de terminal no es válido.")
        salida["device_id"] = valor
    if "activa" in datos:
        salida["activa"] = _booleano(datos["activa"], "Activa")
    return salida


@require_GET
@acceso_soporte_tecnico
@ensure_csrf_cookie
def panel(request):
    return render(request, "soporte/panel.html", {
        "sucursal": _sucursal(), "version": APP_VERSION,
    })


@require_GET
@acceso_soporte_tecnico
def resumen(request):
    sucursal = _sucursal()
    latido = LatidoServicio.objects.filter(nombre="impresion").first()
    worker_activo = bool(
        latido and latido.ultimo_en >= timezone.now() - timedelta(seconds=30)
    )
    cola = {
        fila["estado"]: fila["total"]
        for fila in TrabajoImpresion.objects.filter(sucursal=sucursal)
        .values("estado").annotate(total=Count("id"))
    }
    outbox = {
        fila["estado_entrega"]: fila["total"]
        for fila in EventoOutbox.objects.filter(sucursal=sucursal)
        .values("estado_entrega").annotate(total=Count("id"))
    }
    pedidos = EstadoSincronizacionPedidos.objects.filter(sucursal=sucursal).first()
    return _respuesta({
        "version": APP_VERSION,
        "sucursal": sucursal.clave,
        "edge": {
            "escucha": settings.WAITRESS_HOST,
            "https": settings.HTTPS_ENABLED,
            "backend_impresion": settings.PRINT_BACKEND,
        },
        "servicios": {
            "edge": "activo",
            "base_local": "activa",
            "impresion": "activo" if worker_activo else "sin_actividad",
            "ultimo_latido_impresion": (
                latido.ultimo_en.isoformat() if latido else ""
            ),
        },
        "sincronizacion": {
            "pedidos": pedidos.estado if pedidos else "sin_configurar",
            "pedidos_ultima": (
                pedidos.ultima_sincronizacion_en.isoformat()
                if pedidos and pedidos.ultima_sincronizacion_en else ""
            ),
            "outbox": outbox,
        },
        "cola": cola,
        "validacion_impresion": estado_validacion_impresion(sucursal),
    })


@require_http_methods(["GET", "PUT"])
@acceso_soporte_tecnico
def acceso_edge(request):
    sucursal = _sucursal()
    configuracion = ConfiguracionAccesoEdge.objects.filter(sucursal=sucursal).first()
    if request.method == "PUT":
        try:
            datos = _json(request)
            _campos(datos, {"host", "puerto", "usar_https"}, {"host", "puerto", "usar_https"})
            host = normalizar_host_impresora(datos["host"])
            permitido = host if ":" not in host else f"[{host}]"
            if permitido not in settings.ALLOWED_HOSTS:
                raise DatosInvalidos(
                    "Añade primero esta dirección a DJANGO_ALLOWED_HOSTS en la instalación."
                )
            usar_https = _booleano(datos["usar_https"], "HTTPS")
            if usar_https != settings.HTTPS_ENABLED:
                raise DatosInvalidos(
                    "El esquema anunciado debe coincidir con el HTTPS configurado en el Edge."
                )
            configuracion, _ = ConfiguracionAccesoEdge.objects.update_or_create(
                sucursal=sucursal,
                defaults={
                    "host": host,
                    "puerto": _puerto(datos["puerto"]),
                    "usar_https": usar_https,
                },
            )
        except (DatosInvalidos, ValueError) as exc:
            return _error(exc)
    return _respuesta({
        "host": configuracion.host if configuracion else "",
        "puerto": configuracion.puerto if configuracion else 8000,
        "usar_https": configuracion.usar_https if configuracion else settings.HTTPS_ENABLED,
        "url_anunciada": configuracion.url_anunciada if configuracion else "",
        "escucha": settings.WAITRESS_HOST,
        "actualizado_en": (
            configuracion.actualizado_en.isoformat() if configuracion else ""
        ),
    })


@require_http_methods(["GET", "POST"])
@acceso_soporte_tecnico
def impresoras(request):
    sucursal = _sucursal()
    if request.method == "GET":
        return _respuesta({
            "impresoras": [
                _impresora_payload(item)
                for item in Impresora.objects.filter(sucursal=sucursal)
            ]
        })
    try:
        datos = _datos_impresora(_json(request))
        impresora = Impresora(sucursal=sucursal, **datos)
        impresora.full_clean()
        impresora.save()
    except (DatosInvalidos, ValidationError) as exc:
        return _error(exc if isinstance(exc, DatosInvalidos) else
                      DatosInvalidos("Los datos de la impresora no son válidos."))
    except IntegrityError:
        return _error("Ya existe una impresora con ese nombre.", 409)
    return _respuesta({"impresora": _impresora_payload(impresora)}, 201)


@require_http_methods(["PATCH"])
@acceso_soporte_tecnico
def impresora_detalle(request, impresora_id):
    sucursal = _sucursal()
    try:
        datos = _datos_impresora(_json(request), parcial=True)
        with transaction.atomic():
            impresora = get_object_or_404(
                Impresora.objects.select_for_update(),
                pk=impresora_id, sucursal=sucursal,
            )
            for campo, valor in datos.items():
                setattr(impresora, campo, valor)
            impresora.full_clean()
            impresora.save()
    except (DatosInvalidos, ValidationError) as exc:
        return _error(exc if isinstance(exc, DatosInvalidos) else
                      DatosInvalidos("Los datos de la impresora no son válidos."))
    except IntegrityError:
        return _error("Ya existe una impresora con ese nombre.", 409)
    return _respuesta({"impresora": _impresora_payload(impresora)})


@require_POST
@acceso_soporte_tecnico
def sondear_impresora(request, impresora_id):
    impresora = get_object_or_404(
        Impresora, pk=impresora_id, sucursal=_sucursal()
    )
    return _respuesta({
        "impresora": _impresora_payload(impresora),
        "sondeo": sondear_recurso_impresora(
            impresora, timeout=min(float(settings.PRINTER_TIMEOUT), 2.0)
        ),
    })


@require_http_methods(["GET", "POST"])
@acceso_soporte_tecnico
def terminales(request):
    sucursal = _sucursal()
    if request.method == "GET":
        return _respuesta({
            "terminales": [
                _terminal_payload(item)
                for item in ConfiguracionImpresionTerminal.objects
                .filter(sucursal=sucursal).prefetch_related(
                    "asignaciones_impresora__impresora"
                )
            ]
        })
    try:
        datos = _datos_terminal(_json(request))
        terminal = ConfiguracionImpresionTerminal(sucursal=sucursal, **datos)
        terminal.full_clean()
        terminal.save()
    except (DatosInvalidos, ValidationError) as exc:
        return _error(exc if isinstance(exc, DatosInvalidos) else
                      DatosInvalidos("Los datos de la terminal no son válidos."))
    except IntegrityError:
        return _error("Ya existe una terminal con ese identificador.", 409)
    return _respuesta({"terminal": _terminal_payload(terminal)}, 201)


@require_http_methods(["PATCH"])
@acceso_soporte_tecnico
def terminal_detalle(request, terminal_id):
    try:
        datos = _datos_terminal(_json(request), parcial=True)
        with transaction.atomic():
            terminal = get_object_or_404(
                ConfiguracionImpresionTerminal.objects.select_for_update(),
                pk=terminal_id, sucursal=_sucursal(),
            )
            for campo, valor in datos.items():
                setattr(terminal, campo, valor)
            terminal.full_clean()
            terminal.save()
    except (DatosInvalidos, ValidationError) as exc:
        return _error(exc if isinstance(exc, DatosInvalidos) else
                      DatosInvalidos("Los datos de la terminal no son válidos."))
    except IntegrityError:
        return _error("Ya existe una terminal con ese identificador.", 409)
    return _respuesta({"terminal": _terminal_payload(terminal)})


@require_http_methods(["PUT"])
@acceso_soporte_tecnico
def rutas_terminal(request, terminal_id):
    sucursal = _sucursal()
    try:
        datos = _json(request)
        _campos(datos, {"rutas"}, {"rutas"})
        rutas = datos["rutas"]
        if not isinstance(rutas, dict) or set(rutas) - _DESTINOS:
            raise DatosInvalidos("Los destinos de impresión no son válidos.")
        ids = {}
        for destino, valor in rutas.items():
            try:
                ids[destino] = UUID(valor)
            except (ValueError, TypeError, AttributeError) as exc:
                raise DatosInvalidos("Selecciona una impresora válida por destino.") from exc
        with transaction.atomic():
            terminal = get_object_or_404(
                ConfiguracionImpresionTerminal.objects.select_for_update(),
                pk=terminal_id, sucursal=sucursal,
            )
            impresoras = {
                item.pk: item for item in Impresora.objects.filter(
                    sucursal=sucursal, activa=True, pk__in=ids.values()
                )
            }
            if len(impresoras) != len(set(ids.values())):
                raise DatosInvalidos("Una impresora seleccionada no existe o está inactiva.")
            terminal.asignaciones_impresora.all().delete()
            for destino, impresora_id in ids.items():
                asignacion = AsignacionImpresoraTerminal(
                    terminal=terminal, destino=destino,
                    impresora=impresoras[impresora_id],
                )
                asignacion.full_clean()
                asignacion.save()
    except DatosInvalidos as exc:
        return _error(exc)
    return _respuesta({"terminal": _terminal_payload(terminal)})


@require_GET
@acceso_soporte_tecnico
def cola(request):
    sucursal = _sucursal()
    trabajos = (
        TrabajoImpresion.objects.filter(sucursal=sucursal)
        .order_by("-creado_en")[:40]
    )
    return _respuesta({"trabajos": [{
        "id": str(t.pk),
        "creado_en": t.creado_en.isoformat(),
        "formato": t.formato,
        "destino": t.destino,
        "estado": t.estado,
        "intentos": t.intentos,
        "device_id": t.device_id,
        "impresora": t.printer_name,
        "host": t.printer_host or "",
        "puerto": t.printer_port,
        "error": t.error,
    } for t in trabajos]})


@require_POST
@acceso_soporte_tecnico
def reintentar(request, trabajo_id):
    try:
        datos = _json(request)
        _campos(datos, {"actualizar_ruta"}, {"actualizar_ruta"})
        actualizar = _booleano(datos["actualizar_ruta"], "Actualizar ruta")
        with transaction.atomic():
            trabajo = get_object_or_404(
                TrabajoImpresion.objects.select_for_update(),
                pk=trabajo_id, sucursal=_sucursal(),
            )
            if trabajo.estado != TrabajoImpresion.Estado.ERROR:
                return _error("Sólo se puede reintentar un trabajo en error.", 409)
            if trabajo.intentos >= 5:
                return _error("El trabajo alcanzó cinco intentos; requiere revisión.", 409)
            if actualizar:
                ruta = resolver_ruta_impresion(
                    trabajo.sucursal, trabajo.device_id, trabajo.destino
                )
                for campo in ("printer_host", "printer_port", "printer_name", "origen_ruta"):
                    setattr(trabajo, campo, ruta[campo])
            if settings.PRINT_BACKEND == "tcp" and (
                not trabajo.printer_host or
                trabajo.origen_ruta == "recurso_inactivo"
            ):
                return _error("No hay una impresora activa para este trabajo.", 409)
            trabajo.estado = TrabajoImpresion.Estado.PENDIENTE
            trabajo.procesado_en = None
            trabajo.error = ""
            trabajo.save(update_fields=[
                "estado", "procesado_en", "error",
                "printer_host", "printer_port", "printer_name", "origen_ruta",
            ])
    except DatosInvalidos as exc:
        return _error(exc)
    return _respuesta({"estado": "pendiente", "id": str(trabajo.pk)})


@require_POST
@acceso_soporte_tecnico
def confirmar_impresion_fisica(request):
    try:
        datos = _json(request)
        _campos(datos, {"confirmada", "nota"}, {"confirmada", "nota"})
        if datos["confirmada"] is not True:
            raise DatosInvalidos("Confirma que observaste papel físico.")
        nota = _texto(datos["nota"], "La referencia de la prueba", 240)
        if len(nota) < 8:
            raise DatosInvalidos("La referencia de la prueba requiere al menos 8 caracteres.")
        sucursal = _sucursal()
        estado = estado_validacion_impresion(sucursal)
        if estado["codigo"] not in {"prueba_pendiente", "topologia_cambiada", "validada"}:
            return _error(estado["mensaje"], 409)
        ValidacionImpresionFisica.objects.create(
            sucursal=sucursal,
            confirmado_por=request.user,
            huella_topologia=estado["huella_topologia"],
            nota=nota,
        )
    except DatosInvalidos as exc:
        return _error(exc)
    return _respuesta(
        {"validacion_impresion": estado_validacion_impresion(sucursal)}, 201
    )
