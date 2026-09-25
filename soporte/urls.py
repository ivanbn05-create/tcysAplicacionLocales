from django.urls import path

from . import views

app_name = "soporte"

urlpatterns = [
    path("", views.panel, name="panel"),
    path("api/resumen/", views.resumen, name="resumen"),
    path("api/edge/", views.acceso_edge, name="edge"),
    path("api/terminales/", views.terminales, name="terminales"),
    path("api/terminales/<uuid:terminal_id>/", views.terminal_detalle, name="terminal"),
    path("api/terminales/<uuid:terminal_id>/rutas/", views.rutas_terminal, name="rutas"),
    path("api/impresoras/", views.impresoras, name="impresoras"),
    path("api/impresoras/<uuid:impresora_id>/", views.impresora_detalle, name="impresora"),
    path("api/impresoras/<uuid:impresora_id>/sondeo/", views.sondear_impresora, name="sondeo"),
    path("api/cola/", views.cola, name="cola"),
    path("api/impresion/confirmar-prueba-fisica/", views.confirmar_impresion_fisica, name="confirmar_impresion_fisica"),
    path("api/cola/<uuid:trabajo_id>/reintentar/", views.reintentar, name="reintentar"),
]
