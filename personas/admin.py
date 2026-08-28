from django.contrib import admin

from .models import Rol, Sucursal, UsuarioPOS


@admin.register(Sucursal)
class SucursalAdmin(admin.ModelAdmin):
    list_display = ("clave", "nombre", "activa")
    search_fields = ("clave", "nombre")


@admin.register(Rol)
class RolAdmin(admin.ModelAdmin):
    list_display = (
        "nombre",
        "sucursal",
        "puede_cobrar",
        "puede_reimprimir",
        "puede_cancelar",
        "puede_sincronizar",
    )


@admin.register(UsuarioPOS)
class UsuarioPOSAdmin(admin.ModelAdmin):
    list_display = ("nombre", "clave", "cuenta", "rol", "sucursal", "activo")
    list_filter = ("activo", "sucursal", "rol")
    search_fields = ("nombre", "clave", "cuenta__username")
