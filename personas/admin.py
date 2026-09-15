from django.contrib import admin

from .models import Modulo, ModuloSucursal, Rol, Sucursal, UsuarioPOS


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


@admin.register(Modulo)
class ModuloAdmin(admin.ModelAdmin):
    list_display = ("clave", "nombre", "nucleo", "version_minima")
    list_filter = ("nucleo",)
    search_fields = ("clave", "nombre")
    filter_horizontal = ("dependencias",)


@admin.register(ModuloSucursal)
class ModuloSucursalAdmin(admin.ModelAdmin):
    list_display = ("sucursal", "modulo", "habilitado", "actualizado_en")
    list_filter = ("habilitado", "sucursal", "modulo")
    search_fields = ("sucursal__clave", "sucursal__nombre", "modulo__clave")
    readonly_fields = ("sucursal", "modulo", "habilitado", "configuracion", "actualizado_en")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
