from django.conf import settings
from django.core.management.base import BaseCommand

from ventas.sincronizacion_central import sincronizar_outbox_central


class Command(BaseCommand):
    help = "Procesa el outbox Central candidato; no hace red si sus flags estan apagados."

    def add_arguments(self, parser):
        parser.add_argument("--limite", type=int, default=50)

    def handle(self, *args, **options):
        if not any(
            (
                settings.CENTRAL_ENABLE_SALES_V2,
                settings.CENTRAL_ENABLE_CUSTOMERS_V2,
                settings.CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2,
            )
        ):
            self.stdout.write(
                self.style.WARNING(
                    "Todos los flujos Central v2 estan apagados; no se realizo ninguna solicitud."
                )
            )
            return
        resultado = sincronizar_outbox_central(limite=options["limite"])
        self.stdout.write(
            self.style.SUCCESS(
                "Central: "
                f"{resultado['entregados']} entregado(s), "
                f"{resultado['pendientes']} pendiente(s), "
                f"{resultado['suspendidos']} suspendido(s), "
                f"{resultado['conciliacion']} en conciliacion."
            )
        )
