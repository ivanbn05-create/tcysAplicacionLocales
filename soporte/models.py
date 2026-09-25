import uuid

from django.conf import settings
from django.db import models

from personas.models import Sucursal


class ConfiguracionAccesoEdge(models.Model):
    """Dirección LAN anunciada a terminales; no cambia la red de Windows."""

    sucursal = models.OneToOneField(
        Sucursal, primary_key=True, on_delete=models.CASCADE,
        related_name="configuracion_acceso_edge",
    )
    host = models.CharField(max_length=253, blank=True)
    puerto = models.PositiveIntegerField(default=8000)
    usar_https = models.BooleanField(default=False)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(puerto__gte=1, puerto__lte=65535),
                name="soporte_acceso_edge_puerto_valido",
            ),
        ]

    @property
    def url_anunciada(self):
        if not self.host:
            return ""
        host = f"[{self.host}]" if ":" in self.host else self.host
        esquema = "https" if self.usar_https else "http"
        return f"{esquema}://{host}:{self.puerto}/"



class LatidoServicio(models.Model):
    """Última actividad observada de un proceso local, sin acceso a Windows."""

    nombre = models.CharField(max_length=40, primary_key=True)
    ultimo_en = models.DateTimeField()


class ValidacionImpresionFisica(models.Model):
    """Acta inmutable de papel observado en la topología actual."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(
        Sucursal, on_delete=models.PROTECT, related_name="validaciones_impresion_fisica"
    )
    confirmado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="validaciones_impresion_fisica",
    )
    huella_topologia = models.CharField(max_length=64)
    nota = models.CharField(max_length=240)
    confirmado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-confirmado_en"]
