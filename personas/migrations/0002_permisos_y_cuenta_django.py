import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def habilitar_encargados_existentes(apps, schema_editor):
    Rol = apps.get_model("personas", "Rol")
    Rol.objects.filter(puede_cobrar=True).update(
        puede_cancelar=True,
        puede_sincronizar=True,
    )


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("personas", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="rol",
            name="puede_cancelar",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="rol",
            name="puede_sincronizar",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="usuariopos",
            name="cuenta",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="perfil_pos",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(habilitar_encargados_existentes, migrations.RunPython.noop),
    ]
