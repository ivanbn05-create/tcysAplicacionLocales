import uuid
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from catalogo.models import Producto
from personas.models import Sucursal, UsuarioPOS


class Mesa(models.Model):
    class Canal(models.TextChoices):
        COMEDOR = "comedor", "Comedor"
        DOMICILIO = "domicilio", "Domicilio"
        SUCURSALES = "sucursales", "Sucursales"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE, related_name="mesas")
    canal = models.CharField(max_length=15, choices=Canal.choices)
    clave = models.CharField(max_length=40)
    nombre = models.CharField(max_length=80)
    orden = models.PositiveSmallIntegerField(default=0)
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ["canal", "orden", "nombre"]
        constraints = [models.UniqueConstraint(fields=["sucursal", "clave"], name="mesa_clave_sucursal")]

    def __str__(self):
        return self.nombre


class Cliente(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE, related_name="clientes")
    nombre = models.CharField(max_length=180)
    telefono = models.CharField(max_length=30, blank=True)
    domicilio = models.TextField(blank=True)
    colonia = models.CharField(max_length=120, blank=True)
    referencia = models.TextField(blank=True)
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nombre


class Ticket(models.Model):
    class Estado(models.TextChoices):
        ABIERTO = "abierto", "Abierto"
        PROCESADO = "procesado", "Procesado"
        COBRAR = "cobrar", "Por cobrar"
        PAGADO = "pagado", "Pagado"
        CANCELADO = "cancelado", "Cancelado"

    class FormaPago(models.TextChoices):
        EFECTIVO = "efectivo", "Efectivo"
        TARJETA = "tarjeta", "Tarjeta"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.PROTECT, related_name="tickets")
    mesa = models.ForeignKey(Mesa, on_delete=models.PROTECT, related_name="tickets")
    cliente = models.ForeignKey(Cliente, null=True, blank=True, on_delete=models.PROTECT, related_name="tickets")
    atendio = models.ForeignKey(UsuarioPOS, null=True, blank=True, on_delete=models.PROTECT, related_name="tickets")
    folio = models.PositiveIntegerField()
    canal = models.CharField(max_length=15, choices=Mesa.Canal.choices)
    estado = models.CharField(max_length=15, choices=Estado.choices, default=Estado.ABIERTO)
    comentario_general = models.TextField(blank=True)
    entrega_aproximada = models.TimeField(null=True, blank=True)
    forma_pago = models.CharField(max_length=12, choices=FormaPago.choices, blank=True)
    importe_recibido = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)
    procesado_en = models.DateTimeField(null=True, blank=True)
    pagado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-creado_en"]
        constraints = [models.UniqueConstraint(fields=["sucursal", "folio"], name="ticket_folio_sucursal")]

    def __str__(self):
        return f"Ticket {self.folio} - {self.mesa.nombre}"

    @property
    def total(self):
        return sum((partida.importe for partida in self.partidas.all()), Decimal("0.00"))


class Partida(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.PROTECT, related_name="partidas")
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="partidas")
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name="partidas")
    comensal = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(24)])
    cantidad = models.DecimalField(max_digits=8, decimal_places=3, default=Decimal("1.000"))
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2)
    nombre_producto = models.CharField(max_length=180)
    nombre_corto = models.CharField(max_length=24)
    comentario = models.CharField(max_length=220, blank=True)
    procesada = models.BooleanField(default=False)
    creada_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["creada_en"]

    @property
    def importe(self):
        return self.cantidad * self.precio_unitario


class ModificadorTicket(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.PROTECT, related_name="modificadores_ticket")
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="modificadores")
    comensal = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(24)])
    codigo = models.CharField(max_length=20)
    nombre = models.CharField(max_length=60)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["ticket", "comensal", "codigo"], name="modificador_unico_comensal")
        ]


class EventoOutbox(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.PROTECT, related_name="eventos_outbox")
    agregado = models.CharField(max_length=40)
    agregado_id = models.UUIDField()
    tipo = models.CharField(max_length=80)
    datos = models.JSONField(default=dict)
    creado_en = models.DateTimeField(auto_now_add=True)
    publicado_en = models.DateTimeField(null=True, blank=True)
    intentos = models.PositiveIntegerField(default=0)
    ultimo_error = models.TextField(blank=True)

    class Meta:
        ordering = ["creado_en"]
        indexes = [models.Index(fields=["publicado_en", "creado_en"])]

    def __str__(self):
        return f"{self.tipo} · {self.agregado_id}"
