from django.contrib import admin

from .models import Categoria, Precio, Producto


class PrecioInline(admin.TabularInline):
    model = Precio
    extra = 0


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "categoria", "destino_impresion", "activo")
    list_filter = ("sucursal", "categoria", "destino_impresion", "activo")
    search_fields = ("codigo", "nombre", "nombre_corto")
    inlines = [PrecioInline]


admin.site.register(Categoria)
