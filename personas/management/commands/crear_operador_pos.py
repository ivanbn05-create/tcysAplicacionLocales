from getpass import getpass

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from personas.models import Rol, Sucursal, UsuarioPOS


class Command(BaseCommand):
    help = "Crea una cuenta operativa y, si se solicita, su primer perfil POS."

    def add_arguments(self, parser):
        parser.add_argument("--username")
        parser.add_argument("--nombre")
        parser.add_argument("--pin")
        parser.add_argument("--crear-perfil-inicial", action="store_true")

    def _crear_perfil_inicial(self, options):
        try:
            sucursal = Sucursal.objects.select_for_update().get(
                clave=settings.SUCURSAL_CLAVE,
                activa=True,
            )
        except Sucursal.DoesNotExist as exc:
            raise CommandError("La sucursal configurada no está aprovisionada.") from exc

        rol, _ = Rol.objects.get_or_create(
            sucursal=sucursal,
            tipo=Rol.Tipo.ENCARGADO,
            defaults={
                "nombre": "Encargado",
                "puede_cobrar": True,
                "puede_reimprimir": True,
                "puede_cancelar": True,
                "puede_sincronizar": True,
            },
        )
        nombre = (options.get("nombre") or "").strip()
        if not nombre:
            nombre = input("Nombre del primer operador [Encargado]: ").strip() or "Encargado"
        nombre = nombre[:100]

        pin = (options.get("pin") or "").strip()
        while len(pin) != 4 or not pin.isdigit():
            if pin:
                self.stderr.write("El PIN debe contener exactamente cuatro dígitos.")
            pin = getpass("PIN operativo de cuatro dígitos: ").strip()
        if any(
            perfil.check_clave(pin)
            for perfil in UsuarioPOS.objects.filter(sucursal=sucursal, activo=True)
        ):
            raise CommandError("El PIN operativo ya está asignado en esta sucursal.")

        perfil = UsuarioPOS(sucursal=sucursal, rol=rol, nombre=nombre)
        perfil.set_clave(pin)
        perfil.save()
        return perfil

    @transaction.atomic
    def handle(self, *args, **options):
        perfil = (
            UsuarioPOS.objects.select_for_update()
            .select_related("rol", "sucursal")
            .filter(
                sucursal__clave=settings.SUCURSAL_CLAVE,
                sucursal__activa=True,
                activo=True,
                cuenta__isnull=True,
            )
            .order_by("creado_en")
            .first()
        )
        if perfil is None:
            if options["crear_perfil_inicial"]:
                perfil = self._crear_perfil_inicial(options)
            else:
                raise CommandError(
                    "No hay un perfil POS libre. Crea otro en /admin/ o usa "
                    "--crear-perfil-inicial durante el primer alta."
                )

        username = (options.get("username") or "").strip()
        if not username:
            username = input("Usuario operativo [operador]: ").strip() or "operador"
        User = get_user_model()
        if User.objects.filter(username__iexact=username).exists():
            raise CommandError("Ya existe una cuenta con ese nombre de usuario.")

        user = User(username=username, is_active=True, is_staff=False, is_superuser=False)
        while True:
            password = getpass("Contraseña operativa: ")
            confirmacion = getpass("Confirma la contraseña: ")
            if password != confirmacion:
                self.stderr.write("Las contraseñas no coinciden.")
                continue
            try:
                validate_password(password, user=user)
            except ValidationError as exc:
                self.stderr.write(" ".join(exc.messages))
                continue
            break

        user.set_password(password)
        user.save()
        perfil.cuenta = user
        perfil.save(update_fields=["cuenta"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Cuenta operativa '{username}' vinculada al perfil '{perfil.nombre}'."
            )
        )
