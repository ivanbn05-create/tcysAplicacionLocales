import uuid

from django.db import models

from personas.models import Sucursal
from ventas.models import ReporteAdministrativo, Ticket


class TrabajoImpresion(models.Model):
    class Formato(models.TextChoices):
        COMANDA = "comanda", "Comanda"
        CUENTA = "cuenta", "Cuenta"
        DOMICILIO = "domicilio", "Domicilio"
        SUCURSAL = "sucursal", "Pedido de sucursal"
        LIQUIDACION = "liquidacion", "Total de repartidor"
        PARCIAL = "parcial", "Reporte parcial"
        CORTE_CAJA = "corte_caja", "Corte de caja"
        CORTE_SUCURSAL = "corte_sucursal", "Corte de sucursal"

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
    ticket = models.ForeignKey(
        Ticket,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="trabajos_impresion",
    )
    reporte = models.ForeignKey(
        ReporteAdministrativo,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="trabajos_impresion",
    )
    formato = models.CharField(max_length=20, choices=Formato.choices)
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
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(ticket__isnull=False, reporte__isnull=True)
                    | models.Q(ticket__isnull=True, reporte__isnull=False)
                ),
                name="impresion_un_documento",
            )
        ]
        verbose_name_plural = "Trabajos de impresión"

    def __str__(self):
        # Mantener la salida del worker compatible con la consola cp1252 de Windows.
        referencia = self.ticket.folio if self.ticket_id else str(self.reporte_id)[:8]
        return f"{self.formato} {referencia} -> {self.destino}"
