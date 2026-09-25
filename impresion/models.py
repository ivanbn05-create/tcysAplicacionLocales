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
    comanda_numero = models.PositiveIntegerField(null=True, blank=True)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.PENDIENTE)
    archivo = models.CharField(max_length=300, blank=True)
    intentos = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    device_id = models.CharField(max_length=128, blank=True)
    printer_host = models.CharField(max_length=253, null=True, blank=True)
    printer_port = models.PositiveIntegerField(null=True, blank=True)
    printer_name = models.CharField(max_length=100, blank=True)
    origen_ruta = models.CharField(max_length=20, blank=True)
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


class ConfiguracionImpresionTerminal(models.Model):
    """Rutas por terminal. El device_id selecciona configuracion, no autoriza."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(
        Sucursal,
        on_delete=models.CASCADE,
        related_name="configuraciones_impresion_terminal",
    )
    device_id = models.CharField(max_length=128)
    nombre = models.CharField(max_length=100)
    activa = models.BooleanField(default=True)
    host_caja = models.GenericIPAddressField(null=True, blank=True)
    host_cocina = models.GenericIPAddressField(null=True, blank=True)
    host_barra = models.GenericIPAddressField(null=True, blank=True)
    puerto = models.PositiveIntegerField(default=9100)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nombre", "device_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["sucursal", "device_id"],
                name="impresion_terminal_unica_sucursal",
            ),
            models.CheckConstraint(
                condition=models.Q(puerto__gte=1, puerto__lte=65535),
                name="impresion_terminal_puerto_valido",
            ),
        ]

    def host_para(self, destino):
        return getattr(self, f"host_{destino}", None)

    def __str__(self):
        return f"{self.nombre} - {self.sucursal.clave}"


class Impresora(models.Model):
    """Recurso físico de impresión, independiente de la función del trabajo."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(
        Sucursal, on_delete=models.PROTECT, related_name="impresoras"
    )
    nombre = models.CharField(max_length=100)
    host = models.CharField(max_length=253)
    puerto = models.PositiveIntegerField(default=9100)
    descripcion = models.CharField(max_length=240, blank=True)
    activa = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=["sucursal", "nombre"], name="impresora_nombre_unico_sucursal"
            ),
            models.CheckConstraint(
                condition=models.Q(puerto__gte=1, puerto__lte=65535),
                name="impresora_puerto_valido",
            ),
        ]

    def __str__(self):
        return f"{self.nombre} - {self.sucursal.clave}"


class AsignacionImpresoraTerminal(models.Model):
    """Una terminal puede elegir un recurso general y excepciones por destino."""

    class Destino(models.TextChoices):
        TODOS = "todos", "Todos los trabajos"
        CAJA = TrabajoImpresion.Destino.CAJA, "Caja"
        COCINA = TrabajoImpresion.Destino.COCINA, "Cocina"
        BARRA = TrabajoImpresion.Destino.BARRA, "Barra"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    terminal = models.ForeignKey(
        ConfiguracionImpresionTerminal,
        on_delete=models.CASCADE,
        related_name="asignaciones_impresora",
    )
    destino = models.CharField(max_length=10, choices=Destino.choices)
    impresora = models.ForeignKey(
        Impresora, on_delete=models.PROTECT, related_name="asignaciones_terminal"
    )
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["destino"]
        constraints = [
            models.UniqueConstraint(
                fields=["terminal", "destino"], name="impresora_ruta_unica_terminal"
            ),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        if (
            self.terminal_id
            and self.impresora_id
            and self.terminal.sucursal_id != self.impresora.sucursal_id
        ):
            raise ValidationError(
                "La terminal y la impresora deben pertenecer a la misma sucursal."
            )
