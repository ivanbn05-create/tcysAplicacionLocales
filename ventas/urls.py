from django.urls import path

from . import views

app_name = "ventas"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("tabletas/", views.tabletas, name="tabletas"),
    path("manifest.webmanifest", views.manifest, name="manifest"),
    path("service-worker.js", views.service_worker, name="service_worker"),
    path("api/estado/", views.api_estado, name="api_estado"),
    path("api/clientes/buscar/", views.api_buscar_clientes, name="api_buscar_clientes"),
    path("api/clientes/", views.api_clientes, name="api_clientes"),
    path("api/clientes/<uuid:cliente_id>/", views.api_cliente, name="api_cliente"),
    path("api/impresion/estado/", views.api_estado_impresion, name="api_estado_impresion"),
    path("api/tickets/abrir/", views.api_abrir_ticket, name="api_abrir_ticket"),
    path("api/tickets/<uuid:ticket_id>/", views.api_ticket, name="api_ticket"),
    path("api/tickets/<uuid:ticket_id>/cliente/", views.api_ticket_cliente, name="api_ticket_cliente"),
    path("api/tickets/<uuid:ticket_id>/partidas/", views.api_agregar_partida, name="api_agregar_partida"),
    path("api/tickets/<uuid:ticket_id>/partidas/ajustar/", views.api_ajustar_partidas, name="api_ajustar_partidas"),
    path("api/partidas/<uuid:partida_id>/", views.api_partida, name="api_partida"),
    path("api/tickets/<uuid:ticket_id>/modificadores/", views.api_modificador, name="api_modificador"),
    path("api/tickets/<uuid:ticket_id>/procesar/", views.api_procesar, name="api_procesar"),
    path("api/tickets/<uuid:ticket_id>/cobrar/", views.api_cobrar, name="api_cobrar"),
    path("api/tickets/<uuid:ticket_id>/cancelar/", views.api_cancelar, name="api_cancelar"),
    path("api/tickets/<uuid:ticket_id>/imprimir/", views.api_imprimir, name="api_imprimir"),
]
