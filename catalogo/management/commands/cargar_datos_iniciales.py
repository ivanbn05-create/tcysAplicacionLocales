from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand

from catalogo.models import Categoria, Precio, Producto
from personas.models import Rol, Sucursal, UsuarioPOS
from ventas.models import Mesa


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
    ("LB", "Lonche de barbacoa", "Lonches", "LB", "75.00", "cocina"),
    ("LBIQ", "Lonche de bistek c/queso", "Lonches", "LBIQ", "85.00", "cocina"),
    ("LCQ", "Lonche de chorizo c/queso", "Lonches", "LCQ", "85.00", "cocina"),
    ("GRB", "Gringa de barbacoa", "Gringas y Quesadillas", "GRB", "36.00", "cocina"),
    ("GRBI", "Gringa de bistek", "Gringas y Quesadillas", "GRBI", "50.00", "cocina"),
    ("GRC", "Gringa de chorizo", "Gringas y Quesadillas", "GRC", "50.00", "cocina"),
    ("REF05", "Refresco o agua 1/2 litro", "Bebidas", "REF", "30.00", "barra"),
    ("AM", "Agua mineral", "Bebidas", "AM", "32.00", "barra"),
    ("AF1", "Agua fresca 1 litro", "Bebidas", "AF1L", "50.00", "barra"),
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

        for codigo, nombre, categoria, corto, importe, destino in MENU:
            producto, _ = Producto.objects.update_or_create(
                sucursal=sucursal,
                codigo=codigo,
                defaults={
                    "categoria": categorias[categoria],
                    "nombre": nombre,
                    "nombre_corto": corto,
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

        self.stdout.write(self.style.SUCCESS("Datos iniciales listos: 23 productos activos y 45 posiciones."))
