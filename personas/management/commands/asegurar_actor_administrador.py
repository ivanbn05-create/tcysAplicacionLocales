from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from personas.models import Sucursal
from ventas.admin_services import asegurar_actor_administrador


class Command(BaseCommand):
    help = "Crea o repara el actor POS protegido que representa al administrador."

    def handle(self, *args, **options):
        try:
            sucursal = Sucursal.objects.get(
                clave=settings.SUCURSAL_CLAVE,
                activa=True,
            )
        except Sucursal.DoesNotExist as exc:
            raise CommandError("La sucursal configurada no esta aprovisionada.") from exc
        actor = asegurar_actor_administrador(sucursal)
        self.stdout.write(
            self.style.SUCCESS(
                f"Actor administrador listo: {actor.id} ({sucursal.clave})."
            )
        )
