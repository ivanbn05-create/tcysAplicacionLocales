from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from personas.models import Sucursal
from ventas.models import SucursalPedido


class Command(BaseCommand):
    help = (
        "Confirma o revoca de forma explicita el vinculo local con un "
        "SucursalCliente de Pedidos. Nunca empata por nombre."
    )

    def add_arguments(self, parser):
        parser.add_argument("--origen-id", type=int, required=True)
        grupo = parser.add_mutually_exclusive_group(required=True)
        grupo.add_argument("--confirmar", action="store_true")
        grupo.add_argument("--revocar", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            sucursal = Sucursal.objects.select_for_update().get(
                clave=settings.SUCURSAL_CLAVE,
                activa=True,
            )
        except Sucursal.DoesNotExist as exc:
            raise CommandError("La sucursal POS configurada no existe.") from exc
        try:
            identidad = SucursalPedido.objects.select_for_update().get(
                sucursal=sucursal,
                origen_id=options["origen_id"],
                tipo=SucursalPedido.Tipo.SUCURSAL,
            )
        except SucursalPedido.DoesNotExist as exc:
            raise CommandError(
                "No existe un mapeo local con ese origen_id y tipo sucursal."
            ) from exc

        if options["confirmar"]:
            identidad.identidad_confirmada_en = timezone.now()
            accion = "confirmada"
        else:
            identidad.identidad_confirmada_en = None
            accion = "revocada"
        identidad.save(update_fields=["identidad_confirmada_en"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Identidad {accion}: POS {sucursal.id}/{sucursal.clave} -> "
                f"SucursalCliente.id={identidad.origen_id}."
            )
        )
