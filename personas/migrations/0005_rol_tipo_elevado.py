from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("personas", "0004_modulos_sucursal"),
    ]

    operations = [
        migrations.AlterField(
            model_name="rol",
            name="tipo",
            field=models.CharField(
                choices=[
                    ("encargado", "Encargado"),
                    ("elevado", "Elevado"),
                    ("mesero", "Mesero"),
                    ("repartidor", "Repartidor"),
                ],
                default="mesero",
                max_length=16,
            ),
        ),
    ]
