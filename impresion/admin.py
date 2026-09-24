from django.contrib import admin

from .models import ConfiguracionImpresionTerminal, TrabajoImpresion


@admin.register(TrabajoImpresion)
class TrabajoImpresionAdmin(admin.ModelAdmin):
    list_display = ("ticket", "formato", "destino", "device_id", "origen_ruta", "estado", "intentos", "creado_en")
    list_filter = ("sucursal", "formato", "destino", "estado")
    readonly_fields = ("creado_en", "procesado_en", "error", "archivo", "device_id", "printer_host", "printer_port", "origen_ruta")



@admin.register(ConfiguracionImpresionTerminal)
class ConfiguracionImpresionTerminalAdmin(admin.ModelAdmin):
    list_display = ("nombre", "sucursal", "device_id", "activa", "puerto", "actualizado_en")
    list_filter = ("sucursal", "activa")
    search_fields = ("nombre", "device_id", "host_caja", "host_cocina", "host_barra")
    readonly_fields = ("creado_en", "actualizado_en")
