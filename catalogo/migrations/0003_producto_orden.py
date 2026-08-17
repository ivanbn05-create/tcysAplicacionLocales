from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("catalogo", "0002_producto_abreviaturas_termino_and_more")]

    operations = [
        migrations.AddField(
            model_name="producto",
            name="orden",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
