import time

from django.core.management.base import BaseCommand
from django.db import OperationalError, connection

from impresion.models import TrabajoImpresion
from impresion.services import procesar_trabajo, reclamar_siguiente


class Command(BaseCommand):
    help = "Consume la cola local y genera/envía impresiones térmicas."

    def add_arguments(self, parser):
        parser.add_argument("--una-vez", action="store_true")

    def handle(self, *args, **options):
        self.stdout.write("Servicio de impresión listo.")
        while True:
            try:
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
