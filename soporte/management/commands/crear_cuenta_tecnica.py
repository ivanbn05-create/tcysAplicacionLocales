"""Aprovisiona una cuenta Django separada para soporte local."""

import getpass
import sys

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from personas.capacidades import usuario_es_soporte_tecnico


class Command(BaseCommand):
    help = "Crea o rota una cuenta técnica local sin recibir la clave por argumentos."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument(
            "--password-stdin", action="store_true",
            help="Lee la contraseña por stdin; nunca por un argumento o variable de entorno.",
        )

    def handle(self, *args, **options):
        username = options["username"].strip()
        if not username or len(username) > 150:
            raise CommandError("El nombre de cuenta técnica no es válido.")
        User = get_user_model()
        existente = User.objects.filter(username=username).first()
        if existente and not usuario_es_soporte_tecnico(existente):
            raise CommandError("La cuenta ya existe y no es una cuenta técnica.")

        if options["password_stdin"]:
            password = sys.stdin.readline().rstrip("\r\n")
        else:
            password = getpass.getpass("Nueva contraseña técnica: ")
            confirmacion = getpass.getpass("Confirma contraseña: ")
            if password != confirmacion:
                raise CommandError("Las contraseñas no coinciden.")
        if not password:
            raise CommandError("La contraseña técnica no puede quedar vacía.")
        try:
            validate_password(password, user=existente)
        except Exception as exc:
            raise CommandError("La contraseña no cumple la política local.") from exc

        with transaction.atomic():
            grupo, _ = Group.objects.get_or_create(name="soporte_tecnico_edge")
            usuario = existente or User(username=username)
            usuario.is_staff = True
            usuario.is_superuser = False
            usuario.is_active = True
            usuario.set_password(password)
            usuario.full_clean()
            usuario.save()
            usuario.groups.add(grupo)
        self.stdout.write(self.style.SUCCESS(
            "Cuenta técnica local preparada. Conserva la contraseña fuera del repositorio."
        ))
