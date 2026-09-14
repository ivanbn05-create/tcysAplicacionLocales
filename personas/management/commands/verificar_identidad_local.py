from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from personas.models import Sucursal


class Command(BaseCommand):
    help = (
        "Comprueba sin escribir que una base existente pertenece a SUCURSAL_CLAVE. "
        "Admite una base nueva sin tabla o sin identidad."
    )

    def handle(self, *args, **options):
        tabla = Sucursal._meta.db_table
        with connection.cursor() as cursor:
            if tabla not in connection.introspection.table_names(cursor):
                self.stdout.write("Base nueva: la tabla de sucursal aún no existe.")
                return
            cursor.execute(
                f"SELECT clave, activa FROM {connection.ops.quote_name(tabla)}"
            )
            identidades = cursor.fetchall()

        if not identidades:
            self.stdout.write("Base sin identidad: puede aprovisionarse después de migrar.")
            return
        if len(identidades) != 1:
            raise CommandError(
                "La base contiene más de una identidad de sucursal; no se migrará."
            )
        clave, activa = identidades[0]
        if clave != settings.SUCURSAL_CLAVE or not activa:
            raise CommandError(
                "La identidad activa de la base no coincide con SUCURSAL_CLAVE; "
                "no se migrará ni se convertirá a otra sucursal."
            )
        self.stdout.write(
            self.style.SUCCESS(f"Identidad local verificada: {settings.SUCURSAL_CLAVE}.")
        )
