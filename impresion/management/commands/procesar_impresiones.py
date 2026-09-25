import time

from django.core.management.base import BaseCommand
from django.db import OperationalError, connection
from django.utils import timezone

from impresion.models import TrabajoImpresion
from impresion.services import procesar_trabajo, reclamar_siguiente
from soporte.models import LatidoServicio


class Command(BaseCommand):
    help = "Consume la cola local y genera/envía impresiones térmicas."

    def add_arguments(self, parser):
        parser.add_argument("--una-vez", action="store_true")

    def handle(self, *args, **options):
        self.stdout.write("Servicio de impresión listo.")
        ultimo_latido = 0.0
        while True:
            try:
                ahora = time.monotonic()
                if ahora - ultimo_latido >= 10:
                    LatidoServicio.objects.update_or_create(
                        nombre="impresion", defaults={"ultimo_en": timezone.now()}
                    )
                    ultimo_latido = ahora
                trabajo = reclamar_siguiente()
                if trabajo:
                    procesar_trabajo(trabajo)
                    self.stdout.write(f"{trabajo}: {trabajo.estado}")
                elif options["una_vez"]:
                    return
                else:
                    time.sleep(1)
            except OperationalError:
                connection.close()
                if options["una_vez"]:
                    raise
                time.sleep(2)
