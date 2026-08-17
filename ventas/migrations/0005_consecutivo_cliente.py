import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Max


def inicializar_consecutivos(apps, schema_editor):
    Cliente = apps.get_model("ventas", "Cliente")
    Consecutivo = apps.get_model("ventas", "ConsecutivoCliente")
    Sucursal = apps.get_model("personas", "Sucursal")
    for sucursal in Sucursal.objects.all():
        ultima = Cliente.objects.filter(sucursal_id=sucursal.id).aggregate(Max("clave_corta"))["clave_corta__max"]
        Consecutivo.objects.create(sucursal_id=sucursal.id, ultimo=max(100000, int(ultima or 100000)))


class Migration(migrations.Migration):

    dependencies = [
        ("ventas", "0004_clientes_domicilio_profesional"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConsecutivoCliente",
            fields=[
                ("sucursal", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True, related_name="consecutivo_clientes", serialize=False, to="personas.sucursal")),
                ("ultimo", models.PositiveIntegerField(default=100000)),
            ],
        ),
        migrations.RunPython(inicializar_consecutivos, migrations.RunPython.noop),
    ]
