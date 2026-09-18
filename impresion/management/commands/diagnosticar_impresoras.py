import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from impresion.services import sondear_impresora


class Command(BaseCommand):
    help = (
        "Comprueba la configuración y conectividad TCP de las impresoras sin "
        "transmitir datos ni generar papel."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--destino",
            action="append",
            choices=("caja", "cocina", "barra"),
            help="Destino a comprobar. Puede repetirse; por defecto comprueba los tres.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emite un resultado JSON apto para evidencia automatizada.",
        )
        parser.add_argument(
            "--exigir-tcp",
            action="store_true",
            help="Falla si la impresión física no está activa o un destino no responde.",
        )

    def handle(self, *args, **options):
        destinos = options["destino"] or ["caja", "cocina", "barra"]
        resultados = [sondear_impresora(destino) for destino in destinos]
        salida = {
            "backend": settings.PRINT_BACKEND,
            "impresion_fisica_activa": settings.PRINT_BACKEND == "tcp",
            "envio_de_datos": False,
            "destinos": resultados,
        }

        if options["json"]:
            self.stdout.write(json.dumps(salida, ensure_ascii=False, sort_keys=True))
        else:
            estado = "activa" if salida["impresion_fisica_activa"] else "desactivada"
            self.stdout.write(
                f"Impresión física: {estado} (backend={settings.PRINT_BACKEND})."
            )
            self.stdout.write(
                "El diagnóstico sólo abre y cierra el socket; no envía datos."
            )
            for resultado in resultados:
                host = resultado["host"] or "sin configurar"
                conectividad = (
                    "disponible" if resultado["alcanzable"] else "no disponible"
                )
                self.stdout.write(
                    f"{resultado['destino']}: "
                    f"{host}:{resultado['puerto']} - {conectividad}."
                )

        if options["exigir_tcp"]:
            if settings.PRINT_BACKEND != "tcp":
                raise CommandError(
                    "La conectividad puede existir, pero PRINT_BACKEND no es tcp; "
                    "la aplicación no enviará papel."
                )
            fallidos = [
                resultado["destino"]
                for resultado in resultados
                if not resultado["alcanzable"]
            ]
            if fallidos:
                raise CommandError(
                    "No hay conectividad TCP para: " + ", ".join(fallidos) + "."
                )