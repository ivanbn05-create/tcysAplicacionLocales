"""Separa las cadenas de publicaciones v2 y v3 sin renumerar el historial."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalogo", "0009_producto_disponible_sucursal_and_more"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="publicacioncatalogocentral",
            options={"ordering": ["-version_contrato", "-version"]},
        ),
        migrations.RemoveConstraint(
            model_name="publicacioncatalogocentral",
            name="catalogo_version_unica_sucursal",
        ),
        migrations.AddConstraint(
            model_name="publicacioncatalogocentral",
            constraint=models.UniqueConstraint(
                fields=("sucursal", "version_contrato", "version"),
                name="catalogo_version_unica_por_contrato",
            ),
        ),
    ]
