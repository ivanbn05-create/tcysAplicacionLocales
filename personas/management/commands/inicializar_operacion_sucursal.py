from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from personas.models import Sucursal
from ventas.bootstrap import inicializar_posiciones_operativas


class Command(BaseCommand):
    help = (
        "Inicializa las posiciones operativas estándar de la sucursal ya "
        "aprovisionada, sin cargar catálogo histórico ni crear sucursales."
    )

    def handle(self, *args, **options):
        try:
            sucursal = Sucursal.objects.get(
                clave=settings.SUCURSAL_CLAVE,
                activa=True,
            )
        except Sucursal.DoesNotExist as exc:
            raise CommandError("La sucursal configurada no está aprovisionada.") from exc
        except Sucursal.MultipleObjectsReturned as exc:
            raise CommandError("La base contiene una identidad de sucursal ambigua.") from exc

        total = inicializar_posiciones_operativas(sucursal)
        self.stdout.write(
            self.style.SUCCESS(
                f"Posiciones operativas listas para {sucursal.clave}: {total}."
            )
        )