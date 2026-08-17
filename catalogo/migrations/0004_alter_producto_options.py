from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("catalogo", "0003_producto_orden")]

    operations = [
        migrations.AlterModelOptions(
            name="producto",
            options={"ordering": ["categoria__orden", "orden", "nombre"]},
        ),
    ]
