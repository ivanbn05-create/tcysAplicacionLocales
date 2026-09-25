import hashlib
from ipaddress import ip_address

from django.conf import settings
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods, require_POST

from personas.capacidades import Capacidad, perfil_tiene_capacidad, usuario_es_soporte_tecnico
from personas.models import UsuarioPOS


def _rate_limit_keys(request, username):
    # REMOTE_ADDR proviene del socket. No se confía en X-Forwarded-For porque un
    # cliente LAN podría falsificarlo si no existe un proxy confiable delante.
    remote_addr = str(request.META.get("REMOTE_ADDR") or "").strip()
    if remote_addr.startswith("[") and remote_addr.endswith("]"):
        remote_addr = remote_addr[1:-1]
    try:
        direccion = ip_address(remote_addr)
        # Evita dos contadores para la misma IPv4 cuando un proxy la expresa
        # como IPv6 mapeada (::ffff:192.0.2.1).
        remote_addr = str(getattr(direccion, "ipv4_mapped", None) or direccion)
    except ValueError:
        # Un REMOTE_ADDR inválido nunca debe generar claves de caché ilimitadas
        # controladas por el cliente ni permitir evadir el contador agregado.
        remote_addr = "desconocida"
    remote_digest = hashlib.sha256(remote_addr.encode("utf-8", errors="replace")).hexdigest()
    combined_material = f"{remote_addr}\0{username.casefold()}".encode("utf-8", errors="replace")
    combined_digest = hashlib.sha256(combined_material).hexdigest()
    return f"pos-login-ip:{remote_digest}", f"pos-login-user:{combined_digest}"


def _login_limits():
    user_attempts = max(1, int(getattr(settings, "POS_LOGIN_MAX_ATTEMPTS", 5)))
    ip_attempts = max(1, int(getattr(settings, "POS_LOGIN_MAX_IP_ATTEMPTS", 20)))
    lockout_seconds = max(1, int(getattr(settings, "POS_LOGIN_LOCKOUT_SECONDS", 900)))
    return user_attempts, ip_attempts, lockout_seconds


def _record_failed_login(key, timeout):
    if cache.add(key, 1, timeout=timeout):
        return 1
    try:
        return cache.incr(key)
    except ValueError:
        # Tolera backends que hayan expulsado la clave entre ``add`` e ``incr``.
        cache.set(key, 1, timeout=timeout)
        return 1


def _safe_next_url(request):
    target = request.POST.get("next") or request.GET.get("next") or reverse("ventas:inicio")
    if url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return target
    return reverse("ventas:inicio")


@never_cache
@csrf_protect
@require_http_methods(["GET", "POST"])
def login_view(request):
    next_url = _safe_next_url(request)
    if request.user.is_authenticated:
        return redirect(next_url)

    context = {"next": next_url, "username": "", "error": ""}
    if request.method == "GET":
        return render(request, "ventas/login.html", context)

    username = str(request.POST.get("username", "")).strip()[:150]
    password = str(request.POST.get("password", ""))
    context["username"] = username
    ip_key, user_key = _rate_limit_keys(request, username)
    max_user_attempts, max_ip_attempts, lockout_seconds = _login_limits()
    user_failed_attempts = int(cache.get(user_key, 0) or 0)
    ip_failed_attempts = int(cache.get(ip_key, 0) or 0)
    if user_failed_attempts >= max_user_attempts or ip_failed_attempts >= max_ip_attempts:
        context["error"] = "Demasiados intentos. Espera antes de volver a intentar."
        response = render(request, "ventas/login.html", context, status=429)
        response["Retry-After"] = str(lockout_seconds)
        return response

    user = authenticate(request, username=username, password=password)
    if user is None:
        user_failed_attempts = _record_failed_login(user_key, lockout_seconds)
        ip_failed_attempts = _record_failed_login(ip_key, lockout_seconds)
        context["error"] = "Usuario o contraseña incorrectos."
        status = (
            429
            if user_failed_attempts >= max_user_attempts or ip_failed_attempts >= max_ip_attempts
            else 200
        )
        if status == 429:
            context["error"] = "Demasiados intentos. Espera antes de volver a intentar."
        response = render(request, "ventas/login.html", context, status=status)
        if status == 429:
            response["Retry-After"] = str(lockout_seconds)
        return response

    perfil = (
        UsuarioPOS.objects.select_related("rol")
        .filter(
            cuenta=user,
            activo=True,
            sucursal__activa=True,
            sucursal__clave=settings.SUCURSAL_CLAVE,
        )
        .first()
    )
    tiene_perfil = perfil_tiene_capacidad(perfil, Capacidad.VENTAS)
    es_soporte = usuario_es_soporte_tecnico(user)
    if not tiene_perfil and not es_soporte:
        context["error"] = "La cuenta no tiene acceso a Ventas en esta sucursal."
        return render(request, "ventas/login.html", context, status=403)

    # Una autenticación válida rehabilita esa cuenta/dispositivo, pero no borra
    # el contador agregado de la IP (evita que una cuenta conocida permita
    # reiniciar un ataque de pulverización de usuarios).
    cache.delete(user_key)
    auth_login(request, user)
    if es_soporte and not tiene_perfil and next_url == reverse("ventas:inicio"):
        return redirect("/soporte/")
    return redirect(next_url)


@never_cache
@csrf_protect
@require_POST
@login_required(login_url="ventas:login")
def logout_view(request):
    auth_logout(request)
    return redirect("ventas:login")
