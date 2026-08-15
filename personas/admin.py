from django.contrib import admin

from .models import Rol, Sucursal, UsuarioPOS


@admin.register(Sucursal)
class SucursalAdmin(admin.ModelAdmin):
    list_display = ("clave", "nombre", "activa")
    search_fields = ("clave", "nombre")


admin.site.register(Rol)
admin.site.register(UsuarioPOS)
