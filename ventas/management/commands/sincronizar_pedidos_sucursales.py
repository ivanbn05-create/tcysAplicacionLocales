from django.conf import settings
from django.core.management.base import BaseCommand

from personas.models import Sucursal
from ventas.integracion_sucursales import sincronizar_pedidos_confirmados


class Command(BaseCommand):
    help = "Importa pedidos confirmados del día desde Supabase o la SQLite de respaldo."

    def handle(self, *args, **options):
        sucursal = Sucursal.objects.get(clave=settings.SUCURSAL_CLAVE, activa=True)
        resultado = sincronizar_pedidos_confirmados(sucursal, forzar=True)
        estilo = self.style.SUCCESS if resultado["activa"] else self.style.WARNING
        self.stdout.write(estilo(resultado["mensaje"]))
