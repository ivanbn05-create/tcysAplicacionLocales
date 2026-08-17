from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ventas", "0005_consecutivo_cliente")]

    operations = [
        migrations.AddField(
            model_name="cliente",
            name="comentarios_multiples",
            field=models.BooleanField(
                default=False,
                help_text="Solicita el nombre y teléfono del contacto en cada pedido.",
            ),
        ),
        migrations.AlterField(
            model_name="domiciliocliente",
            name="numero_exterior",
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name="ticket",
            name="contacto_pedido_nombre",
            field=models.CharField(blank=True, max_length=180),
        ),
        migrations.AddField(
            model_name="ticket",
            name="contacto_pedido_telefono",
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name="ticket",
            name="cancelado_en",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
