from django.contrib import admin

from .models import TrabajoImpresion


@admin.register(TrabajoImpresion)
class TrabajoImpresionAdmin(admin.ModelAdmin):
    list_display = ("ticket", "formato", "destino", "estado", "intentos", "creado_en")
    list_filter = ("sucursal", "formato", "destino", "estado")
    readonly_fields = ("creado_en", "procesado_en", "error", "archivo")
