"""Aplica únicamente un recibo de Pedidos firmado y enlazado al ACK."""
from pathlib import Path
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from ventas.models import RecuperacionPedidosV2
from ventas.recuperacion_pedidos_v2 import (
    RecuperacionLocalError,
    aplicar_recibo,
    cargar_pins_pedidos,
)


class Command(BaseCommand):
    help = "Verifica recibo r7 y, si completado, cierra checkpoint por CAS."

    def add_arguments(self, parser):
        parser.add_argument("--recovery-id", required=True)
        parser.add_argument("--receipt-file", required=True)
        parser.add_argument("--pins-file", required=True)

    def handle(self, *args, **options):
        try:
            recovery_id = UUID(options["recovery_id"])
            acta = RecuperacionPedidosV2.objects.get(pk=recovery_id)
            pins = cargar_pins_pedidos(Path(options["pins_file"]))
            completed = aplicar_recibo(
                acta,
                receipt_path=Path(options["receipt_file"]),
                pedidos_keys=pins,
            )
        except (ValueError, RecuperacionPedidosV2.DoesNotExist) as exc:
            raise CommandError("Acta recovery desconocida.") from exc
        except RecuperacionLocalError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            "completada" if completed else "intervencion_manual; checkpoint intacto"
        )
