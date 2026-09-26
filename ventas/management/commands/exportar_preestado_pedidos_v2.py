"""Exporta el checkpoint real en conciliación para recovery-v2 de laboratorio."""
from django.core.management.base import BaseCommand, CommandError
from personas.models import Sucursal
from ventas.recuperacion_pedidos_v2 import (
    RecuperacionLocalError,
    preestado_persistido,
)


class Command(BaseCommand):
    help = "Guarda los ocho campos r7 del preestado Pedidos en runtime privado."

    def add_arguments(self, parser):
        parser.add_argument("--sucursal", required=True, help="Clave local POS.")

    def handle(self, *args, **options):
        try:
            sucursal = Sucursal.objects.get(clave=options["sucursal"])
            path = preestado_persistido(sucursal)
        except Sucursal.DoesNotExist as exc:
            raise CommandError("Sucursal POS desconocida.") from exc
        except RecuperacionLocalError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(str(path))
