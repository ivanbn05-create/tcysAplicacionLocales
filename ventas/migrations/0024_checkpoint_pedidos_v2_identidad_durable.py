# Generated for the 0.4.0-dev.10 Pedidos v2 candidate.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ventas", "0023_partida_precio_lista_capturado"),
    ]

    operations = [
        migrations.AddField(
            model_name="estadosincronizacionpedidos",
            name="agua_alta_hasta",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="pedidosucursalimportado",
            constraint=models.UniqueConstraint(
                fields=("sucursal", "codigo_publico"),
                condition=models.Q(
                    origen="pedidos_sucursales_api_v2",
                    codigo_publico__gt="",
                ),
                name="pedido_api_v2_codigo_publico_unico",
            ),
        ),
    ]
