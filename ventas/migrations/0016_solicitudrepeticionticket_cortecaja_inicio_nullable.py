import django.db.models.deletion
import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("personas", "0004_modulos_sucursal"),
        ("ventas", "0015_consecutivofolio_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="cortecaja",
            name="inicio",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="SolicitudRepeticionTicket",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("clave_idempotencia", models.CharField(max_length=128)),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                (
                    "sucursal",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="solicitudes_repeticion_ticket",
                        to="personas.sucursal",
                    ),
                ),
                (
                    "ticket_nuevo",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="solicitud_repeticion_origen",
                        to="ventas.ticket",
                    ),
                ),
                (
                    "ticket_origen",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="solicitudes_repeticion",
                        to="ventas.ticket",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="solicitudrepeticionticket",
            constraint=models.UniqueConstraint(
                fields=("ticket_origen", "clave_idempotencia"),
                name="repeticion_ticket_clave_unica",
            ),
        ),
    ]
