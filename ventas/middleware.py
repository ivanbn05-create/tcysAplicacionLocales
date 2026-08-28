from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponseForbidden, JsonResponse
from django.urls import reverse
from django.utils.cache import patch_cache_control, patch_vary_headers

from personas.models import UsuarioPOS


class POSSessionAuthenticationMiddleware:
    """Exige una sesión Django en la UI y las API operativas del POS.

    Debe instalarse después de ``AuthenticationMiddleware``. El interruptor se
    conserva para pruebas controladas; al no declararlo, la opción segura es
    exigir autenticación.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _static_prefix():
        prefix = urlsplit(str(settings.STATIC_URL)).path or "/static/"
        if not prefix.startswith("/"):
            prefix = f"/{prefix}"
        return prefix

    @staticmethod
    def _is_api(path):
        return path == "/api" or path.startswith("/api/")

    @staticmethod
    def _secure_response(response, incluir_csp=True):
        # Política compatible con la PWA: todo el código, fuentes, imágenes y
        # conexiones deben provenir del mismo servidor local.
        if incluir_csp:
            response.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self'; font-src 'self'; connect-src 'self'; "
                "worker-src 'self'; manifest-src 'self'; object-src 'none'; "
                "base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
            )
        response.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
            "serial=(), bluetooth=()",
        )
        response.setdefault("X-Content-Type-Options", "nosniff")
        response.setdefault("Referrer-Policy", "same-origin")
        return response

    def __call__(self, request):
        path = request.path_info
        login_path = reverse("ventas:login")
        public_paths = {
            login_path,
            reverse("ventas:manifest"),
            reverse("ventas:service_worker"),
            reverse("ventas:salud"),
        }

        require_auth = getattr(settings, "POS_REQUIRE_AUTH", True)
        is_admin = path == "/admin" or path.startswith("/admin/")
        is_public = path in public_paths or path.startswith(self._static_prefix()) or is_admin
        if require_auth and not is_public:
            if not hasattr(request, "user"):
                raise ImproperlyConfigured(
                    "POSSessionAuthenticationMiddleware debe ir después de "
                    "django.contrib.auth.middleware.AuthenticationMiddleware."
                )
            if not request.user.is_authenticated:
                if self._is_api(path):
                    response = JsonResponse({"error": "Autenticación requerida."}, status=401)
                    response["WWW-Authenticate"] = "Session"
                else:
                    response = redirect_to_login(request.get_full_path(), login_path)
                patch_cache_control(response, no_store=True, private=True)
                patch_vary_headers(response, ["Cookie"])
                return self._secure_response(response)

            perfil = None
            if request.user.is_superuser:
                perfil = (
                    UsuarioPOS.objects.select_related("rol", "sucursal")
                    .filter(cuenta=request.user, activo=True, sucursal__activa=True)
                    .first()
                )
            else:
                perfil = (
                    UsuarioPOS.objects.select_related("rol", "sucursal")
                    .filter(
                        cuenta=request.user,
                        activo=True,
                        sucursal__activa=True,
                        sucursal__clave=settings.SUCURSAL_CLAVE,
                    )
                    .first()
                )
                if perfil is None:
                    if self._is_api(path):
                        response = JsonResponse(
                            {"error": "La cuenta no tiene un perfil POS activo en esta sucursal."},
                            status=403,
                        )
                    else:
                        response = HttpResponseForbidden(
                            "La cuenta no tiene un perfil POS activo en esta sucursal."
                        )
                    patch_cache_control(response, no_store=True, private=True)
                    patch_vary_headers(response, ["Cookie"])
                    return self._secure_response(response)
            request.pos_user = perfil

        response = self.get_response(request)
        if self._is_api(path) or path in {
            login_path,
            reverse("ventas:logout"),
            reverse("ventas:inicio"),
            reverse("ventas:tabletas"),
        }:
            # Las API contienen pedidos y datos personales; no deben quedar en
            # cachés compartidas, historial intermedio ni el almacenamiento PWA.
            patch_cache_control(response, no_store=True, private=True)
            patch_vary_headers(response, ["Cookie"])
        return self._secure_response(response, incluir_csp=not is_admin)
