from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalogo.configuracion_menu import configuracion_producto
from catalogo.models import Categoria, Precio, Producto
from personas.models import Rol, Sucursal
from ventas.catalogo_sucursales import (
    PRODUCTOS_SUCURSALES,
    SUCURSALES_PEDIDO,
    configuracion_precio,
)
from ventas.models import (
    Mesa,
    Partida,
    PrecioProductoSucursal,
    ProductoSucursal,
    SucursalPedido,
)


MENU = [
    ("TB", "Taco de barbacoa", "Taco", "TB", "25.00", "cocina"),
    ("TBQ", "Taco de barbacoa c/queso", "Taco", "TBQ", "31.00", "cocina"),
    ("TPLB", "Taco de barbacoa planchado", "Taco", "TPLB", "30.00", "cocina"),
    ("TPLBQ", "Taco de barbacoa planchado c/queso", "Taco", "TPLBQ", "36.00", "cocina"),
    ("TBI", "Taco de bistek", "Taco", "TBI", "32.00", "cocina"),
    ("TBIQ", "Taco de bistek c/queso", "Taco", "TBIQ", "38.00", "cocina"),
    ("TC", "Taco de chorizo", "Taco", "TC", "32.00", "cocina"),
    ("TCQ", "Taco de chorizo c/queso", "Taco", "TCQ", "38.00", "cocina"),
    ("PB", "Taco y bebida", "Promoción", "PB", "95.00", "cocina"),
    ("PL", "Lonche y taco", "Promoción", "PL", "90.00", "cocina"),
    ("P4", "Cuatro tacos", "Promoción", "P4", "90.00", "cocina"),
    ("PK", "Bistec", "Promoción", "PK", "90.00", "cocina"),
    ("BBQ05", "Barbacoa 1/2 litro", "Consomé y Barbacoa", "1/2BBQ", "200.00", "cocina"),
    ("BBQ1", "Barbacoa 1 litro", "Consomé y Barbacoa", "1LBBQ", "400.00", "cocina"),
    ("CO8", "Consomé vaso 8 oz", "Consomé y Barbacoa", "CO8", "14.00", "cocina"),
    ("CO05", "Consomé 1/2 litro", "Consomé y Barbacoa", "CO1/2", "22.00", "cocina"),
    ("CO1", "Consomé 1 litro", "Consomé y Barbacoa", "CO1L", "35.00", "cocina"),
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
    ("FLCAJ", "Flan de cajeta", "Postre", "FL CAJ", "40.00", "barra"),
    ("FLCAR", "Flan de caramelo", "Postre", "FL CAR", "40.00", "barra"),
    ("JERICALLA", "Jericalla", "Postre", "JER", "40.00", "barra"),
    ("ARROZL", "Arroz con leche", "Postre", "ARROZ", "40.00", "barra"),
    ("GELATINA", "Gelatina", "Postre", "GEL", "40.00", "barra"),
]

CATEGORIAS = ["Taco", "Promoción", "Consomé y Barbacoa", "Lonches", "Gringas y Quesadillas", "Bebidas", "Postre"]
class Command(BaseCommand):
    help = "Carga en una sucursal ARBOLEDAS ya aprovisionada el catálogo histórico y sus posiciones."

    @transaction.atomic
    def handle(self, *args, **options):
        if settings.SUCURSAL_CLAVE != "ARBOLEDAS":
            raise CommandError(
                "La carga histórica sólo se admite cuando SUCURSAL_CLAVE es ARBOLEDAS."
            )
        sucursal = Sucursal.objects.filter(clave="ARBOLEDAS", activa=True).first()
        if sucursal is None or Sucursal.objects.count() != 1:
            raise CommandError(
                "La base debe contener únicamente la sucursal ARBOLEDAS activa y ya "
                "aprovisionada antes de cargar datos históricos."
            )
        rol, _ = Rol.objects.update_or_create(
            sucursal=sucursal,
            nombre="Encargado",
            defaults={
                "tipo": Rol.Tipo.ENCARGADO,
                "puede_cobrar": True,
                "puede_reimprimir": True,
                "puede_cancelar": True,
                "puede_sincronizar": True,
            },
        )
        Rol.objects.get_or_create(
            sucursal=sucursal,
            tipo=Rol.Tipo.MESERO,
            defaults={"nombre": "Mesero"},
        )
        Rol.objects.get_or_create(
            sucursal=sucursal,
            tipo=Rol.Tipo.REPARTIDOR,
            defaults={"nombre": "Repartidor"},
        )

        categorias = {}
        for orden, nombre in enumerate(CATEGORIAS, 1):
            categoria, _ = Categoria.objects.update_or_create(
                sucursal=sucursal,
                nombre=nombre,
                defaults={"orden": orden, "activa": True},
            )
            categorias[nombre] = categoria

        Categoria.objects.filter(sucursal=sucursal).exclude(nombre__in=CATEGORIAS).update(activa=False)

        Producto.objects.filter(sucursal=sucursal, codigo__in=["REF05", "AF1", "POSTRE"]).update(activo=False)

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
        for numero in range(1, 101):
            Mesa.objects.get_or_create(
                sucursal=sucursal,
                canal=Mesa.Canal.DOMICILIO,
                clave=f"DOM-{numero}",
                defaults={"nombre": f"Dom. # {numero}", "orden": numero},
            )
        for canal, prefijo, etiqueta in (
            (Mesa.Canal.RECOGER, "REC", "Recoger"),
            (Mesa.Canal.LLEVAR, "LLEV", "Llevar"),
        ):
            for numero in range(1, 13):
                Mesa.objects.get_or_create(
                    sucursal=sucursal,
                    canal=canal,
                    clave=f"{prefijo}-{numero}",
                    defaults={"nombre": f"{etiqueta} {numero}", "orden": numero},
                )
        # El catálogo de sucursales conserva los ids del sistema web externo.
        # Cada cliente dispone de una pantalla propia con holgura para todo el turno.
        clientes_sucursal = {}
        for origen_id, nombre, tipo in SUCURSALES_PEDIDO:
            cliente_sucursal, _ = SucursalPedido.objects.update_or_create(
                sucursal=sucursal,
                origen_id=origen_id,
                defaults={"nombre": nombre, "tipo": tipo, "activa": True},
            )
            clientes_sucursal[origen_id] = cliente_sucursal

        SucursalPedido.objects.filter(sucursal=sucursal).exclude(
            origen_id__in=clientes_sucursal
        ).update(activa=False)
        Mesa.objects.filter(
            sucursal=sucursal,
            canal=Mesa.Canal.SUCURSALES,
            cliente_sucursal__isnull=True,
        ).update(activa=False)
        for origen_id, cliente_sucursal in clientes_sucursal.items():
            for numero in range(1, 25):
                Mesa.objects.update_or_create(
                    sucursal=sucursal,
                    clave=f"SUC-{origen_id}-{numero}",
                    defaults={
                        "canal": Mesa.Canal.SUCURSALES,
                        "nombre": f"{cliente_sucursal.nombre} {numero}",
                        "orden": numero,
                        "cliente_sucursal": cliente_sucursal,
                        "activa": True,
                    },
                )

        productos_sucursal = {}
        for orden, (origen_id, nombre, nombre_ticket, unidad, divisor) in enumerate(PRODUCTOS_SUCURSALES, 1):
            producto_sucursal, _ = ProductoSucursal.objects.update_or_create(
                sucursal=sucursal,
                origen_id=origen_id,
                defaults={
                    "nombre": nombre,
                    "nombre_ticket": nombre_ticket,
                    "unidad": unidad,
                    "cantidad_por_precio": Decimal(divisor),
                    "orden": orden,
                    "activo": True,
                },
            )
            productos_sucursal[origen_id] = producto_sucursal

        ProductoSucursal.objects.filter(sucursal=sucursal).exclude(
            origen_id__in=productos_sucursal
        ).update(activo=False)
        for origen_sucursal, cliente_sucursal in clientes_sucursal.items():
            for origen_producto, producto_sucursal in productos_sucursal.items():
                configuracion = configuracion_precio(
                    cliente_sucursal.tipo,
                    origen_producto,
                    producto_sucursal.nombre_ticket,
                    origen_sucursal,
                )
                if configuracion is None:
                    continue
                importe, nombre_ticket = configuracion
                PrecioProductoSucursal.objects.update_or_create(
                    sucursal=sucursal,
                    cliente_sucursal=cliente_sucursal,
                    producto=producto_sucursal,
                    vigente_desde="2026-08-15",
                    defaults={"importe": importe, "nombre_ticket": nombre_ticket},
                )

        legado = Path(settings.BASE_DIR) / "datos" / "Listado-Productos.xlsx"
        if legado.is_file():
            try:
                call_command(
                    "importar_catalogo_legado",
                    str(legado),
                    sucursal=sucursal.clave,
                    verbosity=0,
                )
            except Exception as exc:
                raise CommandError(
                    "No se pudo importar el XLSX histórico; se revirtió toda la carga."
                ) from exc

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
        posiciones = Mesa.objects.filter(sucursal=sucursal, activa=True).count()
        self.stdout.write(
            self.style.SUCCESS(f"Datos iniciales listos: {activos} productos activos y {posiciones} posiciones.")
        )
