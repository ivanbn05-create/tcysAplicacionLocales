from django.conf import settings
from django.core.management.base import BaseCommand

from personas.models import Sucursal
from personas.modulos import modulo_habilitado
from ventas.integracion_sucursales import sincronizar_pedidos_confirmados


class Command(BaseCommand):
    help = "Importa pedidos confirmados de hoy o ayer desde Supabase o la SQLite de respaldo."

    def handle(self, *args, **options):
        sucursal = Sucursal.objects.get(clave=settings.SUCURSAL_CLAVE, activa=True)
        if not modulo_habilitado(sucursal, "pedidos_sucursales"):
            self.stdout.write(self.style.WARNING(
                "Módulo pedidos_sucursales deshabilitado; no se ejecutó la sincronización."
            ))
            return
        resultado = sincronizar_pedidos_confirmados(sucursal, forzar=True)
        estilo = self.style.SUCCESS if resultado["activa"] else self.style.WARNING
        self.stdout.write(estilo(resultado["mensaje"]))
