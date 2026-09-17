"""Límite local de intentos para accesos por PIN en la API POS."""

import hashlib
from ipaddress import ip_address

from django.core.cache import cache
from django.http import JsonResponse


MAX_FALLOS = 5
VENTANA_SEGUNDOS = 60


def _direccion_remota_canonica(request):
    """Normaliza la IP del socket sin confiar en encabezados reenviados."""

    valor = str(request.META.get("REMOTE_ADDR") or "").strip()
    if valor.startswith("[") and valor.endswith("]"):
        valor = valor[1:-1]
    try:
        direccion = ip_address(valor)
    except ValueError:
        return "desconocida"
    return str(getattr(direccion, "ipv4_mapped", None) or direccion)


def clave_intentos(request, proposito, sucursal):
    material = (
        f"{str(proposito).strip().casefold()}\0{sucursal.pk}\0"
        f"{_direccion_remota_canonica(request)}"
    ).encode("utf-8", errors="replace")
    digest = hashlib.sha256(material).hexdigest()
    return f"pos-api-pin:{digest}"


def limite_agotado(clave):
    try:
        return int(cache.get(clave, 0) or 0) >= MAX_FALLOS
    except (TypeError, ValueError):
        # Un valor corrupto debe cerrarse de forma segura y expirar pronto.
        cache.set(clave, MAX_FALLOS, timeout=VENTANA_SEGUNDOS)
        return True


def registrar_fallo(clave):
    if cache.add(clave, 1, timeout=VENTANA_SEGUNDOS):
        return 1
    try:
        return int(cache.incr(clave))
    except (TypeError, ValueError):
        # Tolera una expulsión entre add/incr y backends sin valor entero.
        cache.set(clave, 1, timeout=VENTANA_SEGUNDOS)
        return 1


def limpiar_fallos(clave):
    cache.delete(clave)


def respuesta_limite():
    respuesta = JsonResponse(
        {"error": "Demasiados intentos. Espera antes de volver a intentar."},
        status=429,
    )
    respuesta["Retry-After"] = str(VENTANA_SEGUNDOS)
    return respuesta
