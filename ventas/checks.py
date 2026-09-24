from django.conf import settings
from django.core.checks import Error, Warning, register


@register()
def comprobar_integracion_externa(app_configs, **kwargs):
    resultados = []
    fuente = getattr(settings, "PEDIDOS_SUCURSALES_FUENTE", "desactivada")
    activa = getattr(settings, "PEDIDOS_SUCURSALES_AUTO_SYNC", False)
    if fuente == "supabase":
        try:
            from .integracion_sucursales import _parametros_conexion_postgres

            _parametros_conexion_postgres()
        except (TypeError, ValueError) as exc:
            resultados.append(
                Error(
                    f"La integración Supabase no está endurecida: {exc}",
                    id="ventas.E001",
                )
            )
        if not activa:
            resultados.append(
                Warning(
                    "Supabase está configurado, pero la sincronización automática está desactivada.",
                    id="ventas.W001",
                )
            )
    elif fuente == "api_v2":
        if not activa:
            resultados.append(
                Warning(
                    "Pedidos API v2 esta configurada, pero la sincronizacion automatica esta desactivada.",
                    id="ventas.W003",
                )
            )
        if not getattr(settings, "PEDIDOS_API_SUCURSAL_IDS", ()):
            resultados.append(
                Error(
                    "Pedidos API v2 requiere un alcance explicito de SucursalCliente.",
                    id="ventas.E003",
                )
            )
    elif fuente == "sqlite" and not settings.DEBUG:
        resultados.append(
            Error(
                "La fuente SQLite externa sólo se permite en el modo de prueba explícito.",
                id="ventas.E002",
            )
        )
    elif fuente == "desactivada" and activa:
        resultados.append(
            Warning(
                "La sincronización está activa, pero la fuente externa está desactivada.",
                id="ventas.W002",
            )
        )
    return resultados
