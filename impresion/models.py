import uuid

from django.db import models

from personas.models import Sucursal
from ventas.models import Ticket


class TrabajoImpresion(models.Model):
    class Formato(models.TextChoices):
        COMANDA = "comanda", "Comanda"
        CUENTA = "cuenta", "Cuenta"

    class Destino(models.TextChoices):
        COCINA = "cocina", "Cocina"
        BARRA = "barra", "Barra"
        CAJA = "caja", "Caja"

    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        PROCESANDO = "procesando", "Procesando"
        GENERADO = "generado", "Vista previa generada"
        IMPRESO = "impreso", "Impreso"
        ERROR = "error", "Error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.PROTECT, related_name="trabajos_impresion")
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="trabajos_impresion")
    formato = models.CharField(max_length=12, choices=Formato.choices)
    destino = models.CharField(max_length=10, choices=Destino.choices)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.PENDIENTE)
    archivo = models.CharField(max_length=300, blank=True)
    intentos = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    procesado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["creado_en"]
        indexes = [models.Index(fields=["estado", "creado_en"])]
        verbose_name_plural = "Trabajos de impresión"

    def __str__(self):
        return f"{self.formato} {self.ticket.folio} → {self.destino}"
