import uuid
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from catalogo.models import Producto
from personas.models import Sucursal, UsuarioPOS

from .normalizacion import normalizar_texto, normalizar_telefono


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
    clave_corta = models.CharField(max_length=6)
    nombre = models.CharField(max_length=180)
    nombre_normalizado = models.CharField(max_length=180, blank=True, db_index=True)
    notas = models.TextField(blank=True)
    comentarios_multiples = models.BooleanField(
        default=False,
        help_text="Solicita el nombre y teléfono del contacto en cada pedido.",
    )
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nombre", "clave_corta"]
        constraints = [
            models.UniqueConstraint(fields=["sucursal", "clave_corta"], name="cliente_clave_sucursal")
        ]

    def save(self, *args, **kwargs):
        self.nombre_normalizado = normalizar_texto(self.nombre)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.nombre} · {self.clave_corta}"


class ConsecutivoCliente(models.Model):
    sucursal = models.OneToOneField(
        Sucursal, primary_key=True, on_delete=models.CASCADE, related_name="consecutivo_clientes"
    )
    ultimo = models.PositiveIntegerField(default=100000)

    def __str__(self):
        return f"{self.sucursal.nombre} · {self.ultimo}"


class TelefonoCliente(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE, related_name="telefonos_cliente")
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="telefonos")
    numero = models.CharField(max_length=30)
    normalizado = models.CharField(max_length=15, db_index=True)
    etiqueta = models.CharField(max_length=30, default="Principal")
    principal = models.BooleanField(default=False)
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-principal", "creado_en"]
        constraints = [
            models.UniqueConstraint(fields=["cliente", "normalizado"], name="cliente_telefono_unico")
        ]
        indexes = [models.Index(fields=["sucursal", "normalizado"], name="ventas_tel_suc_norm_idx")]

    def save(self, *args, **kwargs):
        self.normalizado = normalizar_telefono(self.numero)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.cliente.nombre} · {self.numero}"


class DomicilioCliente(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE, related_name="domicilios_cliente")
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="domicilios")
    etiqueta = models.CharField(max_length=30, default="Principal")
    calle = models.CharField(max_length=180)
    numero_exterior = models.CharField(max_length=30, blank=True)
    numero_interior = models.CharField(max_length=30, blank=True)
    colonia = models.CharField(max_length=120, blank=True)
    codigo_postal = models.CharField(max_length=10, blank=True)
    municipio = models.CharField(max_length=120, blank=True)
    referencia = models.TextField(blank=True)
    normalizado = models.TextField(blank=True)
    principal = models.BooleanField(default=False)
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-principal", "creado_en"]
        indexes = [models.Index(fields=["sucursal", "numero_exterior"], name="ventas_dom_suc_num_idx")]

    @property
    def texto_completo(self):
        numero = f"#{self.numero_exterior}" if self.numero_exterior else ""
        if self.numero_interior:
            interior = self.numero_interior.strip()
            if interior.upper().startswith(("INT ", "INTERIOR ", "DEP ", "DEPTO ", "DEPARTAMENTO ", "CASA ", "LOCAL ")):
                numero = f"{numero} {interior}".strip()
            else:
                numero = f"{numero} Int. {interior}".strip()
        principal = f"{self.calle} {numero}".strip()
        ubicacion = ", ".join(parte for parte in [self.colonia, self.municipio, self.codigo_postal] if parte)
        return ", ".join(parte for parte in [principal, ubicacion] if parte)

    def save(self, *args, **kwargs):
        self.normalizado = normalizar_texto(
            " ".join(
                [
                    self.calle,
                    self.numero_exterior,
                    self.numero_interior,
                    self.colonia,
                    self.codigo_postal,
                    self.municipio,
                    self.referencia,
                ]
            )
        )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.cliente.nombre} · {self.texto_completo}"


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
    telefono_cliente = models.ForeignKey(
        TelefonoCliente, null=True, blank=True, on_delete=models.SET_NULL, related_name="tickets"
    )
    domicilio_cliente = models.ForeignKey(
        DomicilioCliente, null=True, blank=True, on_delete=models.SET_NULL, related_name="tickets"
    )
    cliente_nombre = models.CharField(max_length=180, blank=True)
    cliente_telefono = models.CharField(max_length=30, blank=True)
    cliente_domicilio = models.TextField(blank=True)
    cliente_referencia = models.TextField(blank=True)
    contacto_pedido_nombre = models.CharField(max_length=180, blank=True)
    contacto_pedido_telefono = models.CharField(max_length=30, blank=True)
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
    cancelado_en = models.DateTimeField(null=True, blank=True)

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
    termino = models.CharField(max_length=8, choices=Producto.Termino.choices, blank=True)
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
