from django.db import migrations, models


def crear_posiciones_auxiliares(apps, schema_editor):
    Sucursal = apps.get_model("personas", "Sucursal")
    Mesa = apps.get_model("ventas", "Mesa")
    for sucursal in Sucursal.objects.all():
        for canal, prefijo, etiqueta in (
            ("recoger", "REC", "Recoger"),
            ("llevar", "LLEV", "Llevar"),
        ):
            for numero in range(1, 13):
                Mesa.objects.get_or_create(
                    sucursal=sucursal,
                    clave=f"{prefijo}-{numero}",
                    defaults={
                        "canal": canal,
                        "nombre": f"{etiqueta} {numero}",
                        "orden": numero,
                        "activa": True,
                    },
                )


class Migration(migrations.Migration):
    dependencies = [("ventas", "0008_componentes_promocion_set_null")]

    operations = [
        migrations.AlterField(
            model_name="mesa",
            name="canal",
            field=models.CharField(
                choices=[
                    ("comedor", "Comedor"),
                    ("llevar", "Llevar"),
                    ("domicilio", "Domicilio"),
                    ("recoger", "Recoger"),
                    ("sucursales", "Sucursales"),
                ],
                max_length=15,
            ),
        ),
        migrations.AlterField(
            model_name="ticket",
            name="canal",
            field=models.CharField(
                choices=[
                    ("comedor", "Comedor"),
                    ("llevar", "Llevar"),
                    ("domicilio", "Domicilio"),
                    ("recoger", "Recoger"),
                    ("sucursales", "Sucursales"),
                ],
                max_length=15,
            ),
        ),
        migrations.AddField(
            model_name="ticket",
            name="captura_por_nombres",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="ticket",
            name="nombres_comensales",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddConstraint(
            model_name="ticket",
            constraint=models.UniqueConstraint(
                condition=models.Q(estado__in=["abierto", "procesado", "cobrar"]),
                fields=("mesa",),
                name="ticket_activo_unico_mesa",
            ),
        ),
        migrations.RunPython(crear_posiciones_auxiliares, migrations.RunPython.noop),
    ]
