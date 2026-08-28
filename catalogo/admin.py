from django.contrib import admin

from .models import Categoria, Precio, Producto


class PrecioInline(admin.TabularInline):
    model = Precio
    extra = 0


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "categoria", "tiene_imagen", "destino_impresion", "activo")
    list_filter = ("sucursal", "categoria", "destino_impresion", "activo")
    search_fields = ("codigo", "nombre", "nombre_corto")
    inlines = [PrecioInline]

    @admin.display(boolean=True, description="Imagen")
    def tiene_imagen(self, producto):
        return bool(producto.imagen)


admin.site.register(Categoria)
