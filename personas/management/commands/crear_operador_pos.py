from getpass import getpass

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from personas.models import UsuarioPOS


class Command(BaseCommand):
    help = "Crea una cuenta Django no administrativa y la vincula con un perfil POS libre."

    def add_arguments(self, parser):
        parser.add_argument("--username")

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
            raise CommandError(
                "No hay un perfil POS libre. Crea otro en /admin/ o libera uno existente."
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
