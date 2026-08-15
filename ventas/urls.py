from django.urls import path

from . import views

app_name = "ventas"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("tabletas/", views.tabletas, name="tabletas"),
    path("manifest.webmanifest", views.manifest, name="manifest"),
    path("service-worker.js", views.service_worker, name="service_worker"),
    path("api/estado/", views.api_estado, name="api_estado"),
    path("api/impresion/estado/", views.api_estado_impresion, name="api_estado_impresion"),
    path("api/tickets/abrir/", views.api_abrir_ticket, name="api_abrir_ticket"),
    path("api/tickets/<uuid:ticket_id>/", views.api_ticket, name="api_ticket"),
    path("api/tickets/<uuid:ticket_id>/partidas/", views.api_agregar_partida, name="api_agregar_partida"),
    path("api/partidas/<uuid:partida_id>/", views.api_partida, name="api_partida"),
    path("api/tickets/<uuid:ticket_id>/modificadores/", views.api_modificador, name="api_modificador"),
    path("api/tickets/<uuid:ticket_id>/procesar/", views.api_procesar, name="api_procesar"),
    path("api/tickets/<uuid:ticket_id>/cobrar/", views.api_cobrar, name="api_cobrar"),
    path("api/tickets/<uuid:ticket_id>/imprimir/", views.api_imprimir, name="api_imprimir"),
]
