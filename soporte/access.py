"""Puerta del plano técnico: sesión de soporte y origen LAN."""

from functools import wraps
from ipaddress import ip_address, ip_network

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden, JsonResponse
from django.utils.cache import patch_cache_control, patch_vary_headers

from personas.capacidades import Capacidad, tiene_capacidad


_REDES_LAN = tuple(ip_network(red) for red in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "::1/128", "fc00::/7",
))


def origen_lan(request):
    valor = str(request.META.get("REMOTE_ADDR") or "").strip().strip("[]")
    try:
        direccion = ip_address(valor)
        if getattr(direccion, "ipv4_mapped", None):
            direccion = direccion.ipv4_mapped
    except ValueError:
        return False
    if not any(direccion in red for red in _REDES_LAN):
        return False
    # Waitress sólo confía en el proxy loopback configurado y reescribe
    # REMOTE_ADDR desde el último X-Forwarded-For. Si el proxy omite la IP
    # original, fallamos cerrado en vez de aceptar su dirección loopback.
    if getattr(settings, "WAITRESS_TRUSTED_PROXY", ""):
        cadena = str(request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
        if not cadena:
            return False
        try:
            saltos = [ip_address(valor.strip()) for valor in cadena.split(",")]
        except ValueError:
            return False
        if not all(any(salto in red for red in _REDES_LAN) for salto in saltos):
            return False
        if saltos[-1] != direccion:
            return False
    return True


def acceso_soporte_tecnico(vista):
    @wraps(vista)
    def protegido(request, *args, **kwargs):
        es_api = request.path_info.startswith("/soporte/api/")
        if not origen_lan(request):
            respuesta = HttpResponseForbidden("Soporte sólo está disponible en la red local.")
        elif not getattr(request, "user", None) or not request.user.is_authenticated:
            if es_api:
                respuesta = JsonResponse({"error": "Autenticación técnica requerida."}, status=401)
            else:
                respuesta = redirect_to_login(
                    request.get_full_path(), settings.LOGIN_URL
                )
        elif not tiene_capacidad(request, Capacidad.SOPORTE_TECNICO):
            if es_api:
                respuesta = JsonResponse({"error": "Cuenta técnica requerida."}, status=403)
            else:
                respuesta = HttpResponseForbidden("Cuenta técnica requerida.")
        else:
            respuesta = vista(request, *args, **kwargs)
        patch_cache_control(respuesta, no_store=True, private=True)
        patch_vary_headers(respuesta, ["Cookie"])
        return respuesta

    return protegido
