"""Identidad compuesta y cuerpo canónico para reconciliación Pedidos v2.

Las filas previas quedan sin sender/hash: no se inventa prueba histórica.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ventas", "0026_backfill_promociones_legado")]

    operations = [
        migrations.AddField(
            model_name="pedidosucursalimportado",
            name="sender_id",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pedidosucursalimportado",
            name="order_canonical_json",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="pedidosucursalimportado",
            name="order_sha256",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.RemoveConstraint(
            model_name="pedidosucursalimportado",
            name="pedido_api_v2_codigo_publico_unico",
        ),
        migrations.AddConstraint(
            model_name="pedidosucursalimportado",
            constraint=models.UniqueConstraint(
                fields=("sucursal", "codigo_publico"),
                condition=models.Q(
                    origen="pedidos_sucursales_api_v2",
                    codigo_publico__gt="",
                    sender_id__isnull=True,
                ),
                name="pedido_api_v2_codigo_legacy_unico",
            ),
        ),
        migrations.AddConstraint(
            model_name="pedidosucursalimportado",
            constraint=models.UniqueConstraint(
                fields=("sucursal", "sender_id", "codigo_publico"),
                condition=models.Q(
                    origen="pedidos_sucursales_api_v2",
                    codigo_publico__gt="",
                    sender_id__isnull=False,
                ),
                name="pedido_api_v2_sender_codigo_unico",
            ),
        ),
    ]
