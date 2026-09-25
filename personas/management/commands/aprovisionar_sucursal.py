import uuid

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from personas.identidad import normalizar_clave_sucursal, normalizar_nombre_sucursal
from personas.models import Sucursal
from ventas.models import ConfiguracionSucursal


_BLOQUEO_APROVISIONAMIENTO = 2026091101


def bloquear_aprovisionamiento():
    """Serializa incluso el primer alta, cuando select_for_update no tiene filas."""

    with connection.cursor() as cursor:
        if connection.vendor == "postgresql":
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s)", [_BLOQUEO_APROVISIONAMIENTO]
            )
        elif connection.vendor == "sqlite":
            tabla = connection.ops.quote_name(Sucursal._meta.db_table)
            cursor.execute(f"UPDATE {tabla} SET clave = clave WHERE 0 = 1")
        else:
            raise CommandError(
                "El aprovisionamiento sólo admite las bases SQLite y PostgreSQL validadas."
            )


class Command(BaseCommand):
    help = (
        "Aprovisiona explícitamente la identidad local de una sucursal y su "
        "clave maestra inicial. No crea catálogos, posiciones ni usuarios."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--clave",
            required=True,
            help="Clave estable de la sucursal; debe coincidir con SUCURSAL_CLAVE.",
        )
        parser.add_argument(
            "--nombre",
            required=True,
            help="Nombre visible de la sucursal.",
        )
        parser.add_argument(
            "--sucursal-id",
            help="UUID central opcional. Si se omite, se genera una identidad local estable.",
        )
        parser.add_argument(
            "--edge-id",
            help="UUID Edge autorizado por Central; debe coincidir con CENTRAL_POS_INSTANCE_ID.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        bloquear_aprovisionamiento()
        try:
            clave = normalizar_clave_sucursal(options["clave"])
            nombre = normalizar_nombre_sucursal(options["nombre"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        sucursal_id = None
        if options.get("sucursal_id"):
            try:
                sucursal_id = uuid.UUID(str(options["sucursal_id"]).strip())
            except (ValueError, AttributeError) as exc:
                raise CommandError("SucursalId debe ser un UUID válido.") from exc
            if sucursal_id.int == 0:
                raise CommandError("SucursalId no puede ser el UUID vacío.")

        edge_configurado = str(getattr(settings, "CENTRAL_POS_INSTANCE_ID", "") or "").strip()
        edge_solicitado = str(options.get("edge_id") or "").strip()
        if edge_configurado and edge_solicitado and edge_configurado != edge_solicitado:
            raise CommandError("EdgeId no coincide con CENTRAL_POS_INSTANCE_ID.")
        edge_texto = edge_solicitado or edge_configurado
        edge_id = None
        if edge_texto:
            try:
                edge_id = uuid.UUID(edge_texto)
            except (ValueError, AttributeError) as exc:
                raise CommandError("EdgeId debe ser un UUID válido.") from exc
            if edge_id.int == 0 or (sucursal_id is not None and edge_id == sucursal_id):
                raise CommandError("EdgeId no puede ser vacío ni igual al UUID de sucursal.")

        if clave != settings.SUCURSAL_CLAVE:
            raise CommandError(
                f"La clave solicitada ({clave}) no coincide con SUCURSAL_CLAVE "
                f"({settings.SUCURSAL_CLAVE}). Corrige la configuración antes de aprovisionar."
            )

        otra_sucursal = (
            Sucursal.objects.select_for_update()
            .exclude(clave=clave)
            .order_by("clave")
            .first()
        )
        if otra_sucursal is not None:
            raise CommandError(
                "La base ya contiene otra identidad de sucursal "
                f"({otra_sucursal.clave}). No se puede cambiar de sucursal mediante "
                "este comando; usa un flujo controlado de reinstalación."
            )

        defaults = {"nombre": nombre, "activa": True}
        if sucursal_id is not None:
            defaults["id"] = sucursal_id
        sucursal, creada = Sucursal.objects.select_for_update().get_or_create(
            clave=clave,
            defaults=defaults,
        )
        if creada:
            valores_configuracion = {
                "sucursal": sucursal,
                "clave_administrador": make_password("0000"),
            }
            if edge_id is not None:
                valores_configuracion["instalacion_id"] = edge_id
            ConfiguracionSucursal.objects.create(**valores_configuracion)
            self.stdout.write(
                self.style.SUCCESS(f"Sucursal aprovisionada: {nombre} ({clave}).")
            )
            return

        if sucursal.nombre != nombre:
            raise CommandError(
                f"La sucursal {clave} ya existe con el nombre '{sucursal.nombre}'. "
                "El aprovisionamiento no modifica una identidad existente."
            )
        if sucursal_id is not None and sucursal.id != sucursal_id:
            raise CommandError(
                f"La sucursal {clave} ya existe con UUID {sucursal.id}. "
                "El aprovisionamiento no reemplaza una identidad existente."
            )
        if not sucursal.activa:
            raise CommandError(
                f"La sucursal {clave} ya existe, pero está inactiva. "
                "Reactívala mediante un procedimiento administrativo explícito."
            )
        if edge_id is not None:
            configuracion = ConfiguracionSucursal.objects.select_for_update().filter(
                sucursal=sucursal
            ).first()
            if configuracion is None or configuracion.instalacion_id != edge_id:
                raise CommandError(
                    "El Edge solicitado no coincide con la identidad durable local. "
                    "No se reemplaza una instalación mediante aprovisionamiento."
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Sucursal ya aprovisionada sin cambios: {nombre} ({clave})."
            )
        )
