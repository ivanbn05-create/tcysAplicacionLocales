from django.db import migrations, models
import django.db.models.deletion


def migrar_comentario_general(apps, schema_editor):
    Ticket = apps.get_model("ventas", "Ticket")
    for ticket in Ticket.objects.exclude(comentario_general="").iterator():
        texto = ticket.comentario_general.strip()
        if texto:
            ticket.comentarios_generales = [{"codigo": "LEGADO", "nombre": texto}]
            ticket.save(update_fields=["comentarios_generales"])


class Migration(migrations.Migration):
    dependencies = [("ventas", "0006_cliente_comentarios_multiples_y_cancelacion")]

    operations = [
        migrations.AddField(
            model_name="ticket",
            name="comentarios_generales",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="ticket",
            name="salsas_verduras",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="ticket",
            name="tipo_entrega",
            field=models.CharField(
                choices=[("aproximada", "Aproximada"), ("programada", "Programada")],
                default="aproximada",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="ticket",
            name="terminal",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="ticket",
            name="paga_con",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name="partida",
            name="promocion_aplicada",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="componentes_promocion",
                to="ventas.partida",
            ),
        ),
        migrations.RunPython(migrar_comentario_general, migrations.RunPython.noop),
    ]
