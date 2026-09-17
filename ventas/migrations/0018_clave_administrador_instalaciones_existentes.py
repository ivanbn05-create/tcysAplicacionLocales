from django.db import migrations


def configurar_instalaciones_existentes(apps, schema_editor):
    from django.contrib.auth.hashers import make_password

    Sucursal = apps.get_model("personas", "Sucursal")
    ConfiguracionSucursal = apps.get_model("ventas", "ConfiguracionSucursal")
    for sucursal_id in Sucursal.objects.values_list("id", flat=True).iterator():
        ConfiguracionSucursal.objects.get_or_create(
            sucursal_id=sucursal_id,
            defaults={"clave_administrador": make_password("1212")},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("ventas", "0017_controles_caja_y_consolidacion"),
    ]

    operations = [
        migrations.RunPython(
            configurar_instalaciones_existentes,
            migrations.RunPython.noop,
        ),
    ]
