# Generated manually: central release and branch publication are distinct IDs.

import uuid

from django.db import migrations, models


def asignar_publicaciones(apps, schema_editor):
    Publicacion = apps.get_model("catalogo", "PublicacionCatalogoCentral")
    for publicacion in Publicacion.objects.filter(publicacion_id__isnull=True).iterator():
        publicacion.publicacion_id = uuid.uuid4()
        publicacion.save(update_fields=["publicacion_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("catalogo", "0007_precio_origen_precio_publicacion_central_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="publicacioncatalogocentral",
            name="publicacion_anterior_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="publicacioncatalogocentral",
            name="publicacion_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.RunPython(asignar_publicaciones, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="publicacioncatalogocentral",
            name="publicacion_id",
            field=models.UUIDField(),
        ),
        migrations.AddConstraint(
            model_name="publicacioncatalogocentral",
            constraint=models.UniqueConstraint(
                fields=("sucursal", "publicacion_id"),
                name="catalogo_publicacion_unica_sucursal",
            ),
        ),
    ]
