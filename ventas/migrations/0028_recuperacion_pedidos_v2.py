"""Acta durable de recovery-v2 y vínculo del checkpoint confirmado."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ventas", "0027_pedido_v2_sender_hash")]

    operations = [
        migrations.AddField(
            model_name="estadosincronizacionpedidos",
            name="ultimo_recovery_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="estadosincronizacionpedidos",
            name="ultimo_recovery_snapshot_sha256",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.CreateModel(
            name="RecuperacionPedidosV2",
            fields=[
                ("id", models.UUIDField(editable=False, primary_key=True, serialize=False)),
                ("edge_id", models.UUIDField()),
                ("pos_branch_id", models.UUIDField()),
                ("sender_ids", models.JSONField(default=list)),
                ("prestate", models.JSONField(default=dict)),
                ("prestate_sha256", models.CharField(max_length=64)),
                ("snapshot_sha256", models.CharField(max_length=64)),
                ("manifest_sha256", models.CharField(max_length=64)),
                ("orders_sha256", models.CharField(max_length=64)),
                ("recovered_orders_sha256", models.CharField(max_length=64)),
                ("tombstones_sha256", models.CharField(max_length=64)),
                ("snapshot_path", models.CharField(max_length=300)),
                ("hasta", models.DateTimeField()),
                ("pedidos_key_id", models.CharField(max_length=80)),
                ("edge_key_id", models.CharField(max_length=80)),
                ("ack_nonce", models.UUIDField()),
                ("ack_json", models.TextField()),
                ("ack_sha256", models.CharField(max_length=64)),
                ("receipt_json", models.TextField(blank=True)),
                ("receipt_sha256", models.CharField(blank=True, max_length=64)),
                (
                    "estado",
                    models.CharField(
                        choices=[
                            ("acuse_pendiente", "Acuse pendiente"),
                            ("intervencion_manual", "Intervención manual"),
                            ("completada", "Completada"),
                        ],
                        default="acuse_pendiente",
                        max_length=24,
                    ),
                ),
                ("detalle_seguro", models.CharField(blank=True, max_length=240)),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("actualizado_en", models.DateTimeField(auto_now=True)),
                (
                    "sucursal",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="recuperaciones_pedidos_v2",
                        to="personas.sucursal",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="recuperacionpedidosv2",
            constraint=models.UniqueConstraint(
                condition=models.Q(estado="acuse_pendiente"),
                fields=("sucursal",),
                name="recuperacion_pedidos_v2_activa_unica",
            ),
        ),
    ]
