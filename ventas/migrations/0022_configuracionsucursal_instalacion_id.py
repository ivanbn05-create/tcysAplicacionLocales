# Generated manually for a unique UUID on existing installations.

import uuid

from django.db import migrations, models


def asignar_identidades(apps, schema_editor):
    ConfiguracionSucursal = apps.get_model("ventas", "ConfiguracionSucursal")
    for configuracion in ConfiguracionSucursal.objects.filter(instalacion_id__isnull=True).iterator():
        configuracion.instalacion_id = uuid.uuid4()
        configuracion.save(update_fields=["instalacion_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("ventas", "0021_cliente_version_entidad"),
    ]

    operations = [
        migrations.AddField(
            model_name="configuracionsucursal",
            name="instalacion_id",
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
        migrations.RunPython(asignar_identidades, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="configuracionsucursal",
            name="instalacion_id",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                help_text="Identidad durable de esta instalacion Edge; no cambia en updates.",
                unique=True,
            ),
        ),
    ]
