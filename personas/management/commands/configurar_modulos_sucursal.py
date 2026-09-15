from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from personas.models import Sucursal
from personas.modulos import MODULOS_OPCIONALES, configurar_modulos


class Command(BaseCommand):
    help = "Configura los módulos iniciales de la sucursal sin instalar código diferente."

    def add_arguments(self, parser):
        grupo = parser.add_mutually_exclusive_group(required=True)
        grupo.add_argument("--modulos", help="Claves opcionales separadas por comas.")
        grupo.add_argument("--sin-opcionales", action="store_true")

    def handle(self, *args, **options):
        try:
            sucursal = Sucursal.objects.get(
                clave=settings.SUCURSAL_CLAVE, activa=True
            )
        except Sucursal.DoesNotExist as exc:
            raise CommandError("La sucursal configurada no está aprovisionada.") from exc

        seleccion = []
        if not options["sin_opcionales"]:
            seleccion = [
                item.strip() for item in (options["modulos"] or "").split(",")
            ]
        try:
            efectivos = configurar_modulos(sucursal, seleccion)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        opcionales = [
            clave for clave in MODULOS_OPCIONALES if clave in efectivos
        ]
        texto = ", ".join(opcionales) if opcionales else "ninguno"
        self.stdout.write(
            self.style.SUCCESS(f"Módulos opcionales habilitados: {texto}.")
        )
