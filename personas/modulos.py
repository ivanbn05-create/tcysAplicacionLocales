from collections import OrderedDict

from django.db import transaction

from .models import Modulo, ModuloSucursal


VERSION_MODULOS = "0.4.0-dev.3"

CATALOGO_MODULOS = OrderedDict(
    (
        ("pos", {"nombre": "Punto de venta", "descripcion": "Comedor, mostrador, cobro, seguridad y operación local.", "nucleo": True, "dependencias": ()}),
        ("catalogo", {"nombre": "Catálogo local", "descripcion": "Productos, precios y disponibilidad de la sucursal.", "nucleo": True, "dependencias": ("pos",)}),
        ("impresion", {"nombre": "Impresión", "descripcion": "Comandas, cuentas y reportes en archivo o impresora térmica.", "nucleo": True, "dependencias": ("pos",)}),
        ("respaldos", {"nombre": "Respaldos locales", "descripcion": "Respaldo verificable y recuperación de la base local.", "nucleo": True, "dependencias": ("pos",)}),
        ("domicilios", {"nombre": "Domicilios", "descripcion": "Clientes, direcciones y pedidos entregados a domicilio.", "nucleo": False, "dependencias": ("pos",)}),
        ("programados", {"nombre": "Pedidos programados", "descripcion": "Pedidos con fecha y hora de activación futura.", "nucleo": False, "dependencias": ("domicilios",)}),
        ("reparto", {"nombre": "Reparto", "descripcion": "Asignación y liquidación de repartidores.", "nucleo": False, "dependencias": ("domicilios",)}),
        ("pedidos_sucursales", {"nombre": "Pedidos entre sucursales", "descripcion": "Recepción, preparación y corte de pedidos de otras sucursales.", "nucleo": False, "dependencias": ("pos",)}),
    )
)

MODULOS_NUCLEO = tuple(
    clave for clave, datos in CATALOGO_MODULOS.items() if datos["nucleo"]
)
MODULOS_OPCIONALES = tuple(
    clave for clave, datos in CATALOGO_MODULOS.items() if not datos["nucleo"]
)


def resolver_seleccion(seleccion):
    solicitados = {
        str(clave).strip().lower() for clave in seleccion if str(clave).strip()
    }
    desconocidos = sorted(solicitados - set(MODULOS_OPCIONALES))
    if desconocidos:
        raise ValueError("Módulos opcionales desconocidos: " + ", ".join(desconocidos))

    efectivos = set(MODULOS_NUCLEO) | solicitados
    pendientes = list(efectivos)
    while pendientes:
        clave = pendientes.pop()
        for dependencia in CATALOGO_MODULOS[clave]["dependencias"]:
            if dependencia not in efectivos:
                efectivos.add(dependencia)
                pendientes.append(dependencia)
    return efectivos


@transaction.atomic
def configurar_modulos(sucursal, seleccion):
    efectivos = resolver_seleccion(seleccion)
    objetos = {}
    for clave, datos in CATALOGO_MODULOS.items():
        modulo, _ = Modulo.objects.update_or_create(
            clave=clave,
            defaults={
                "nombre": datos["nombre"],
                "descripcion": datos["descripcion"],
                "nucleo": datos["nucleo"],
                "version_minima": VERSION_MODULOS,
            },
        )
        objetos[clave] = modulo

    for clave, datos in CATALOGO_MODULOS.items():
        objetos[clave].dependencias.set(
            objetos[item] for item in datos["dependencias"]
        )
        ModuloSucursal.objects.update_or_create(
            sucursal=sucursal,
            modulo=objetos[clave],
            defaults={"habilitado": clave in efectivos},
        )
    return efectivos


def modulos_efectivos(sucursal):
    asignaciones = {
        asignacion.modulo.clave: asignacion.habilitado
        for asignacion in ModuloSucursal.objects.select_related("modulo").filter(
            sucursal=sucursal
        )
    }
    if not asignaciones:
        # Compatibilidad para bases de desarrollo y fixtures creados sin instalador.
        return {clave: True for clave in CATALOGO_MODULOS}
    return {
        clave: bool(datos["nucleo"] or asignaciones.get(clave, False))
        for clave, datos in CATALOGO_MODULOS.items()
    }


def modulo_habilitado(sucursal, clave):
    return bool(modulos_efectivos(sucursal).get(clave, False))
