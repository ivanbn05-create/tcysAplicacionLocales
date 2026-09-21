from datetime import date
from decimal import Decimal

from django.db import migrations


def corregir_unidades_y_precio(apps, schema_editor):
    ProductoSucursal = apps.get_model("ventas", "ProductoSucursal")
    PrecioProductoSucursal = apps.get_model("ventas", "PrecioProductoSucursal")

    ProductoSucursal.objects.filter(origen_id=1).update(
        unidad="LT",
        cantidad_por_precio=Decimal("1.000"),
    )
    ProductoSucursal.objects.filter(origen_id=7).update(
        unidad="KG",
        cantidad_por_precio=Decimal("1.000"),
    )
    # Dev.9 corrige el precio que estaba vigente al preparar la entrega sin
    # reescribir el historial ni una tarifa futura ya programada por sucursal.
    fecha_entrega = date(2026, 9, 21)
    for producto_id in ProductoSucursal.objects.filter(
        origen_id=7
    ).values_list("id", flat=True):
        clientes = (
            PrecioProductoSucursal.objects.filter(producto_id=producto_id)
            .values_list("cliente_sucursal_id", flat=True)
            .distinct()
        )
        for cliente_id in clientes:
            precio_vigente = (
                PrecioProductoSucursal.objects.filter(
                    producto_id=producto_id,
                    cliente_sucursal_id=cliente_id,
                    vigente_desde__lte=fecha_entrega,
                )
                .order_by("-vigente_desde")
                .first()
            )
            if precio_vigente is not None:
                PrecioProductoSucursal.objects.filter(pk=precio_vigente.pk).update(
                    importe=Decimal("64.00")
                )


class Migration(migrations.Migration):
    dependencies = [
        ("ventas", "0018_clave_administrador_instalaciones_existentes"),
    ]

    operations = [
        migrations.RunPython(
            corregir_unidades_y_precio,
            migrations.RunPython.noop,
        ),
    ]
