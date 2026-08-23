from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ventas", "0007_entrega_promociones_y_preparacion")]

    operations = [
        migrations.AlterField(
            model_name="partida",
            name="promocion_aplicada",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="componentes_promocion",
                to="ventas.partida",
            ),
        ),
    ]
