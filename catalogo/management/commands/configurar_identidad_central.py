import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalogo.models import (
    Categoria,
    IdentidadCategoriaCentral,
    IdentidadProductoCentral,
    Producto,
)
from personas.models import Sucursal
from ventas.models import ConfiguracionSucursal


def _par(valor, etiqueta):
    try:
        central, local = str(valor).split("=", 1)
        central_id = uuid.UUID(central)
        local_id = uuid.UUID(local)
    except (ValueError, TypeError, AttributeError) as exc:
        raise CommandError(
            f"{etiqueta} debe usar CENTRAL_UUID=LOCAL_UUID con UUID canonicos."
        ) from exc
    if str(central_id) != central or str(local_id) != local:
        raise CommandError(f"{etiqueta} contiene un UUID no canonico.")
    return central_id, local_id


class Command(BaseCommand):
    help = (
        "Configura mapeos explicitos del catalogo Central; nunca enlaza por nombre o codigo."
    )

    def add_arguments(self, parser):
        parser.add_argument("--sucursal", required=True)
        parser.add_argument("--categoria", action="append", default=[])
        parser.add_argument("--producto", action="append", default=[])
        parser.add_argument("--mostrar-identidad", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            sucursal = Sucursal.objects.get(clave=options["sucursal"], activa=True)
        except Sucursal.DoesNotExist as exc:
            raise CommandError("La sucursal activa no existe.") from exc
        try:
            configuracion = ConfiguracionSucursal.objects.get(sucursal=sucursal)
        except ConfiguracionSucursal.DoesNotExist as exc:
            raise CommandError("La sucursal no esta aprovisionada.") from exc

        if options["mostrar_identidad"]:
            self.stdout.write(
                f"sucursal_id={sucursal.id}\n"
                f"sucursal_clave={sucursal.clave}\n"
                f"pos_instance_id={configuracion.instalacion_id}"
            )

        cambios = 0
        for valor in options["categoria"]:
            central_id, local_id = _par(valor, "--categoria")
            try:
                local = Categoria.objects.select_for_update().get(
                    pk=local_id, sucursal=sucursal
                )
            except Categoria.DoesNotExist as exc:
                raise CommandError("La categoria local indicada no existe en la sucursal.") from exc
            por_central = IdentidadCategoriaCentral.objects.filter(
                sucursal=sucursal, central_id=central_id
            ).first()
            por_local = IdentidadCategoriaCentral.objects.filter(categoria=local).first()
            if por_central and por_central.categoria_id != local.id:
                raise CommandError("La categoria Central ya apunta a otra identidad local.")
            if por_local and por_local.central_id != central_id:
                raise CommandError("La categoria local ya apunta a otra identidad Central.")
            if not por_central:
                IdentidadCategoriaCentral.objects.create(
                    sucursal=sucursal,
                    central_id=central_id,
                    categoria=local,
                )
                cambios += 1

        for valor in options["producto"]:
            central_id, local_id = _par(valor, "--producto")
            try:
                local = Producto.objects.select_for_update().get(
                    pk=local_id, sucursal=sucursal
                )
            except Producto.DoesNotExist as exc:
                raise CommandError("El producto local indicado no existe en la sucursal.") from exc
            por_central = IdentidadProductoCentral.objects.filter(
                sucursal=sucursal, central_id=central_id
            ).first()
            por_local = IdentidadProductoCentral.objects.filter(producto=local).first()
            if por_central and por_central.producto_id != local.id:
                raise CommandError("El producto Central ya apunta a otra identidad local.")
            if por_local and por_local.central_id != central_id:
                raise CommandError("El producto local ya apunta a otra identidad Central.")
            if not por_central:
                IdentidadProductoCentral.objects.create(
                    sucursal=sucursal,
                    central_id=central_id,
                    producto=local,
                )
                cambios += 1

        if not options["mostrar_identidad"] and not options["categoria"] and not options["producto"]:
            raise CommandError(
                "Indica --mostrar-identidad o al menos un mapeo explicito."
            )
        if options["dry_run"]:
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING(f"Dry-run correcto: {cambios} alta(s)."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Mapeos confirmados: {cambios}."))
