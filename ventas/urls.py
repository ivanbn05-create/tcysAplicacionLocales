from django.urls import path

from . import views
from .auth_views import login_view, logout_view

app_name = "ventas"

urlpatterns = [
    path("acceso/", login_view, name="login"),
    path("salir/", logout_view, name="logout"),
    path("salud/", views.salud, name="salud"),
    path("", views.inicio, name="inicio"),
    path("tabletas/", views.tabletas, name="tabletas"),
    path("manifest.webmanifest", views.manifest, name="manifest"),
    path("service-worker.js", views.service_worker, name="service_worker"),
    path("catalogo/productos/<uuid:producto_id>/imagen.webp", views.imagen_producto, name="imagen_producto"),
    path("api/estado/", views.api_estado, name="api_estado"),
    path(
        "api/sincronizacion/sucursales/",
        views.api_sincronizar_sucursales,
        name="api_sincronizar_sucursales",
    ),
    path("api/clientes/buscar/", views.api_buscar_clientes, name="api_buscar_clientes"),
    path("api/clientes/", views.api_clientes, name="api_clientes"),
    path("api/clientes/<uuid:cliente_id>/", views.api_cliente, name="api_cliente"),
    path("api/impresion/estado/", views.api_estado_impresion, name="api_estado_impresion"),
    path(
        "api/impresiones/<uuid:trabajo_id>/archivo/",
        views.api_archivo_impresion,
        name="api_archivo_impresion",
    ),
    path("api/tickets/abrir/", views.api_abrir_ticket, name="api_abrir_ticket"),
    path("api/tickets/<uuid:ticket_id>/", views.api_ticket, name="api_ticket"),
    path("api/tickets/<uuid:ticket_id>/cliente/", views.api_ticket_cliente, name="api_ticket_cliente"),
    path("api/tickets/<uuid:ticket_id>/convertir/", views.api_convertir_ticket, name="api_convertir_ticket"),
    path("api/tickets/<uuid:ticket_id>/partidas/", views.api_agregar_partida, name="api_agregar_partida"),
    path("api/tickets/<uuid:ticket_id>/partidas/ajustar/", views.api_ajustar_partidas, name="api_ajustar_partidas"),
    path("api/partidas/<uuid:partida_id>/", views.api_partida, name="api_partida"),
    path("api/tickets/<uuid:ticket_id>/modificadores/", views.api_modificador, name="api_modificador"),
    path("api/tickets/<uuid:ticket_id>/procesar/", views.api_procesar, name="api_procesar"),
    path("api/tickets/<uuid:ticket_id>/cobrar/", views.api_cobrar, name="api_cobrar"),
    path(
        "api/tickets/<uuid:ticket_id>/completar-sucursal/",
        views.api_completar_sucursal,
        name="api_completar_sucursal",
    ),
    path("api/tickets/<uuid:ticket_id>/cancelar/", views.api_cancelar, name="api_cancelar"),
    path("api/tickets/<uuid:ticket_id>/imprimir/", views.api_imprimir, name="api_imprimir"),
]
