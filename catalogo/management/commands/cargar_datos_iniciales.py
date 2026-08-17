from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand

from catalogo.configuracion_menu import configuracion_producto
from catalogo.models import Categoria, Precio, Producto
from personas.models import Rol, Sucursal, UsuarioPOS
from ventas.models import Mesa, Partida


MENU = [
    ("TB", "Taco de barbacoa", "Tacos", "TB", "25.00", "cocina"),
    ("TBQ", "Taco de barbacoa c/queso", "Tacos", "TBQ", "31.00", "cocina"),
    ("TPLB", "Taco de barbacoa planchado", "Tacos", "TPLB", "30.00", "cocina"),
    ("TPLBQ", "Taco de barbacoa planchado c/queso", "Tacos", "TPLBQ", "36.00", "cocina"),
    ("TBI", "Taco de bistek", "Tacos", "TBI", "32.00", "cocina"),
    ("TBIQ", "Taco de bistek c/queso", "Tacos", "TBIQ", "38.00", "cocina"),
    ("TC", "Taco de chorizo", "Tacos", "TC", "32.00", "cocina"),
    ("TCQ", "Taco de chorizo c/queso", "Tacos", "TCQ", "38.00", "cocina"),
    ("BBQ05", "Barbacoa 1/2 litro", "Barbacoa Litros", "1/2BBQ", "200.00", "cocina"),
    ("BBQ1", "Barbacoa 1 litro", "Barbacoa Litros", "1LBBQ", "400.00", "cocina"),
    ("CO8", "Consomé vaso 8 oz", "Consomés", "CO8", "14.00", "cocina"),
    ("CO05", "Consomé 1/2 litro", "Consomés", "CO1/2", "22.00", "cocina"),
    ("CO1", "Consomé 1 litro", "Consomés", "CO1L", "35.00", "cocina"),
    ("LB", "Lonche de barbacoa sin queso", "Lonches", "LB", "75.00", "cocina"),
    ("LOBQ", "Lonche de barbacoa con queso", "Lonches", "LOBQ", "75.00", "cocina"),
    ("LOKSQ", "Lonche de bistek sin queso", "Lonches", "LOK/SQ", "85.00", "cocina"),
    ("LBIQ", "Lonche de bistek c/queso", "Lonches", "LBIQ", "85.00", "cocina"),
    ("LOCSQ", "Lonche de chorizo sin queso", "Lonches", "LOC/SQ", "85.00", "cocina"),
    ("LCQ", "Lonche de chorizo c/queso", "Lonches", "LCQ", "85.00", "cocina"),
    ("QKH", "Quesadilla de harina", "Gringas y Quesadillas", "QKH", "25.00", "cocina"),
    ("QKM", "Quesadilla de maíz", "Gringas y Quesadillas", "QKM", "25.00", "cocina"),
    ("GRB", "Gringa de barbacoa", "Gringas y Quesadillas", "GRB", "36.00", "cocina"),
    ("GRBI", "Gringa de bistek", "Gringas y Quesadillas", "GRBI", "50.00", "cocina"),
    ("GRC", "Gringa de chorizo", "Gringas y Quesadillas", "GRC", "50.00", "cocina"),
    ("HR05", "Agua de horchata rosa de medio litro", "Bebidas", "HR 1/2", "30.00", "barra"),
    ("HR1", "Agua de horchata rosa de litro", "Bebidas", "HR L", "50.00", "barra"),
    ("HB05", "Agua de horchata blanca de medio litro", "Bebidas", "HB 1/2", "30.00", "barra"),
    ("HB1", "Agua de horchata blanca de litro", "Bebidas", "HB L", "50.00", "barra"),
    ("JAM05", "Agua de jamaica de medio litro", "Bebidas", "JAM 1/2", "30.00", "barra"),
    ("JAM1", "Agua de jamaica de litro", "Bebidas", "JAM L", "50.00", "barra"),
    ("CC", "Coca Cola", "Bebidas", "CC", "30.00", "barra"),
    ("CCL", "Coca Cola Light", "Bebidas", "CC/L", "30.00", "barra"),
    ("CCSA", "Coca Cola sin azúcar", "Bebidas", "CC/SA", "30.00", "barra"),
    ("FANTA", "Fanta", "Bebidas", "FNTA", "30.00", "barra"),
    ("SPRITE", "Sprite", "Bebidas", "SPRT", "30.00", "barra"),
    ("SPRITESA", "Sprite sin azúcar", "Bebidas", "SPRT/SA", "30.00", "barra"),
    ("SIDRAL", "Sidral", "Bebidas", "SDRL", "30.00", "barra"),
    ("AM", "Agua mineral", "Bebidas", "AM", "30.00", "barra"),
    ("POSTRE", "Postre", "Postres", "POSTRE", "40.00", "barra"),
]

CATEGORIAS = ["Tacos", "Barbacoa Litros", "Consomés", "Lonches", "Gringas y Quesadillas", "Bebidas", "Postres"]
SUCURSALES_DESTINO = [
    "Centro Médico",
    "RAKEBELA",
    "Las Águilas",
    "La Estancia",
    "Fortín",
    "Plaza del Sol",
    "Santa Anita",
    "Edgar",
    "Brot",
]


class Command(BaseCommand):
    help = "Crea la sucursal, posiciones y productos confirmados por el menú fotografiado."

    def handle(self, *args, **options):
        sucursal, _ = Sucursal.objects.get_or_create(clave="ARBOLEDAS", defaults={"nombre": "Arboledas"})
        rol, _ = Rol.objects.get_or_create(
            sucursal=sucursal,
            nombre="Encargado",
            defaults={"puede_cobrar": True, "puede_reimprimir": True},
        )
        UsuarioPOS.objects.get_or_create(sucursal=sucursal, clave="CAJA", defaults={"nombre": "Caja", "rol": rol})

        categorias = {}
        for orden, nombre in enumerate(CATEGORIAS, 1):
            categoria, _ = Categoria.objects.update_or_create(
                sucursal=sucursal,
                nombre=nombre,
                defaults={"orden": orden, "activa": True},
            )
            categorias[nombre] = categoria

        Producto.objects.filter(sucursal=sucursal, codigo__in=["REF05", "AF1"]).update(activo=False)

        for orden_producto, (codigo, nombre, categoria, corto, importe, destino) in enumerate(MENU, 1):
            configuracion = configuracion_producto(codigo, nombre, corto)
            producto, _ = Producto.objects.update_or_create(
                sucursal=sucursal,
                codigo=codigo,
                defaults={
                    "categoria": categorias[categoria],
                    "nombre": nombre,
                    "orden": orden_producto,
                    **configuracion,
                    "destino_impresion": destino,
                    "activo": True,
                    "origen": "menu_2026",
                },
            )
            Precio.objects.update_or_create(
                sucursal=sucursal,
                producto=producto,
                vigente_desde="2026-08-14",
                defaults={"importe": Decimal(importe), "activo": True},
            )

        for numero in range(1, 25):
            Mesa.objects.get_or_create(
                sucursal=sucursal,
                canal=Mesa.Canal.COMEDOR,
                clave=f"MESA-{numero}",
                defaults={"nombre": f"Mesa {numero}", "orden": numero},
            )
        for numero in range(1, 13):
            Mesa.objects.get_or_create(
                sucursal=sucursal,
                canal=Mesa.Canal.DOMICILIO,
                clave=f"DOM-{numero}",
                defaults={"nombre": f"Dom. # {numero}", "orden": numero},
            )
        for orden, nombre in enumerate(SUCURSALES_DESTINO, 1):
            Mesa.objects.get_or_create(
                sucursal=sucursal,
                canal=Mesa.Canal.SUCURSALES,
                clave=f"SUC-{orden}",
                defaults={"nombre": nombre, "orden": orden},
            )

        legado = Path(settings.BASE_DIR) / "datos" / "Listado-Productos.xlsx"
        if legado.is_file():
            call_command("importar_catalogo_legado", str(legado), sucursal=sucursal.clave, verbosity=0)

        # También actualiza productos legados que ya existían antes de incorporar
        # las abreviaturas y los términos. Nunca los activa ni inventa precios.
        for producto in Producto.objects.filter(sucursal=sucursal).iterator():
            configuracion = configuracion_producto(producto.codigo, producto.nombre, producto.nombre_corto)
            cambios = {
                campo: valor
                for campo, valor in configuracion.items()
                if getattr(producto, campo) != valor
            }
            if cambios:
                Producto.objects.filter(pk=producto.pk).update(**cambios)

        # Normaliza únicamente las órdenes todavía abiertas. Las partidas de
        # tickets históricos conservan el texto exacto con el que se imprimieron.
        partidas_abiertas = Partida.objects.select_related("producto").filter(
            sucursal=sucursal,
            ticket__estado="abierto",
            termino="",
            producto__permite_termino=True,
        )
        for partida in partidas_abiertas.iterator():
            termino = partida.producto.termino_predeterminado
            abreviatura = partida.producto.abreviaturas_termino.get(termino)
            Partida.objects.filter(pk=partida.pk).update(
                termino=termino,
                nombre_corto=abreviatura or partida.nombre_corto,
                nombre_producto=f"{partida.producto.nombre} {termino}",
            )

        activos = Producto.objects.filter(sucursal=sucursal, activo=True).count()
        self.stdout.write(self.style.SUCCESS(f"Datos iniciales listos: {activos} productos activos y 45 posiciones."))
