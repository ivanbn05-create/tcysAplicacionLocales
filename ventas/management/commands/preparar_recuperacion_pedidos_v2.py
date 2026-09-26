"""Prepara ACK Ed25519 local sin transmitirlo a Pedidos."""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from personas.models import Sucursal
from ventas.recuperacion_pedidos_v2 import (
    RecuperacionLocalError,
    acuse_persistido,
    cargar_pins_pedidos,
    preparar_recuperacion,
)


class Command(BaseCommand):
    help = "Verifica baseline/archivos, importa lo probado y persiste un ACK r7."

    def add_arguments(self, parser):
        parser.add_argument("--sucursal", required=True)
        parser.add_argument("--snapshot-file", required=True)
        parser.add_argument("--snapshot-sha256", required=True)
        parser.add_argument("--pins-file", required=True)
        parser.add_argument("--edge-id", required=True)
        parser.add_argument("--pos-branch-id", required=True)
        parser.add_argument("--sender-ids", required=True)
        parser.add_argument("--edge-key-id", required=True)
        parser.add_argument("--edge-private-key-file", required=True)
        parser.add_argument(
            "--archivo-custodia", action="append", default=[],
            metavar="EXPORTACION_UUID=ZIP", help="Repetir para recuperados archivados."
        )

    def handle(self, *args, **options):
        try:
            sucursal = Sucursal.objects.get(clave=options["sucursal"])
            try:
                sender_ids = [
                    int(value) for value in options["sender_ids"].split(",")
                ]
            except ValueError as exc:
                raise RecuperacionLocalError(
                    "La tupla de remitentes debe contener enteros."
                ) from exc
            if not sender_ids or sender_ids != sorted(set(sender_ids)):
                raise RecuperacionLocalError(
                    "La tupla de remitentes debe ser exacta y ordenada."
                )
            archives = {}
            for item in options["archivo_custodia"]:
                export_id, separator, path = item.partition("=")
                if not separator or not export_id or not path or export_id in archives:
                    raise RecuperacionLocalError(
                        "Cada archivo custodio debe ser UUID=ZIP único."
                    )
                archives[export_id] = Path(path)
            pins = cargar_pins_pedidos(Path(options["pins_file"]))
            acta = preparar_recuperacion(
                sucursal,
                snapshot_path=Path(options["snapshot_file"]),
                snapshot_sha256=options["snapshot_sha256"],
                pedidos_keys=pins,
                edge_id=options["edge_id"],
                pos_branch_id=options["pos_branch_id"],
                sender_ids=sender_ids,
                edge_key_id=options["edge_key_id"],
                edge_private_key_path=Path(options["edge_private_key_file"]),
                archivos_custodia=archives,
            )
            ack = acuse_persistido(acta)
        except Sucursal.DoesNotExist as exc:
            raise CommandError("Sucursal POS desconocida.") from exc
        except RecuperacionLocalError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"recovery_id={acta.id} estado={acta.estado} ack_sha256={acta.ack_sha256}"
        )
        self.stdout.write(str(ack))
