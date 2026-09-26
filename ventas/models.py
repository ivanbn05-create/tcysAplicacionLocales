import uuid
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from catalogo.models import Producto
from personas.models import Sucursal, UsuarioPOS

from .normalizacion import normalizar_texto, normalizar_telefono


class SucursalPedido(models.Model):
    """Sucursal o cliente mayorista que envía pedidos a este local."""

    class Tipo(models.TextChoices):
        SUCURSAL = "sucursal", "Sucursal"
        CLIENTE_MAYORISTA = "cliente_mayorista", "Cliente mayorista"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE, related_name="clientes_sucursales")
    origen_id = models.PositiveIntegerField()
    nombre = models.CharField(max_length=120)
    tipo = models.CharField(max_length=24, choices=Tipo.choices)
    activa = models.BooleanField(default=True)
    identidad_confirmada_en = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Confirmacion manual del vinculo con SucursalCliente. "
            "El nombre remoto nunca confirma la identidad."
        ),
    )

    class Meta:
        ordering = ["tipo", "nombre"]
        constraints = [
            models.UniqueConstraint(fields=["sucursal", "origen_id"], name="sucursal_pedido_origen_unico")
        ]

    def __str__(self):
        return self.nombre


class ProductoSucursal(models.Model):
    """Catálogo mayorista separado del menú de comedor."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE, related_name="productos_sucursales")
    origen_id = models.PositiveIntegerField()
    nombre = models.CharField(max_length=120)
    nombre_ticket = models.CharField(max_length=40)
    unidad = models.CharField(max_length=8, default="PZA")
    cantidad_por_precio = models.DecimalField(max_digits=8, decimal_places=3, default=Decimal("1.000"))
    orden = models.PositiveSmallIntegerField(default=0)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["orden", "nombre"]
        constraints = [
            models.UniqueConstraint(fields=["sucursal", "origen_id"], name="producto_sucursal_origen_unico")
        ]

    def __str__(self):
        return self.nombre

    def precio_actual(self, cliente_sucursal, fecha=None):
        fecha = fecha or timezone.localdate()
        return (
            self.precios.filter(cliente_sucursal=cliente_sucursal, vigente_desde__lte=fecha)
            .order_by("-vigente_desde")
            .first()
        )


class PrecioProductoSucursal(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE, related_name="precios_productos_sucursales")
    cliente_sucursal = models.ForeignKey(SucursalPedido, on_delete=models.CASCADE, related_name="precios")
    producto = models.ForeignKey(ProductoSucursal, on_delete=models.CASCADE, related_name="precios")
    importe = models.DecimalField(max_digits=10, decimal_places=2)
    nombre_ticket = models.CharField(max_length=40)
    vigente_desde = models.DateField()

    class Meta:
        ordering = ["producto__orden", "-vigente_desde"]
        constraints = [
            models.UniqueConstraint(
                fields=["cliente_sucursal", "producto", "vigente_desde"],
                name="precio_producto_sucursal_fecha_unico",
            )
        ]


class Mesa(models.Model):
    class Canal(models.TextChoices):
        COMEDOR = "comedor", "Comedor"
        LLEVAR = "llevar", "Llevar"
        DOMICILIO = "domicilio", "Domicilio"
        RECOGER = "recoger", "Recoger"
        SUCURSALES = "sucursales", "Sucursales"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE, related_name="mesas")
    canal = models.CharField(max_length=15, choices=Canal.choices)
    clave = models.CharField(max_length=40)
    nombre = models.CharField(max_length=80)
    orden = models.PositiveSmallIntegerField(default=0)
    cliente_sucursal = models.ForeignKey(
        SucursalPedido,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="posiciones",
    )
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
    version_entidad = models.PositiveIntegerField(default=1)
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
    actualizado_en = models.DateTimeField(auto_now=True)

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
    actualizado_en = models.DateTimeField(auto_now=True)

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


class ConfiguracionSucursal(models.Model):
    sucursal = models.OneToOneField(
        Sucursal,
        primary_key=True,
        on_delete=models.CASCADE,
        related_name="configuracion_pos",
    )
    instalacion_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        help_text="Identidad durable de esta instalacion Edge; no cambia en updates.",
    )
    clave_administrador = models.CharField(max_length=128)
    actor_administrador = models.OneToOneField(
        UsuarioPOS,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="configuracion_como_administrador",
    )
    actualizado_en = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Configuración · {self.sucursal.nombre}"


class ConsecutivoFolio(models.Model):
    """Consecutivo vigente de tickets, separado por una serie reiniciable."""

    sucursal = models.OneToOneField(
        Sucursal,
        primary_key=True,
        on_delete=models.CASCADE,
        related_name="consecutivo_folios",
    )
    serie = models.PositiveIntegerField(default=1)
    ultimo = models.PositiveIntegerField(default=0)
    actualizado_en = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.sucursal.nombre} · serie {self.serie} · {self.ultimo}"


class Ticket(models.Model):
    class Estado(models.TextChoices):
        ABIERTO = "abierto", "Abierto"
        PROCESADO = "procesado", "Procesado"
        COBRAR = "cobrar", "Por cobrar"
        PAGADO = "pagado", "Pagado"
        PROGRAMADO = "programado", "Programado"
        CANCELADO = "cancelado", "Cancelado"

    class FormaPago(models.TextChoices):
        EFECTIVO = "efectivo", "Efectivo"
        TARJETA = "tarjeta", "Tarjeta"

    class TipoEntrega(models.TextChoices):
        APROXIMADA = "aproximada", "Aproximada"
        PROGRAMADA = "programada", "Programada"

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
    repartidor = models.ForeignKey(
        UsuarioPOS,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="tickets_repartidos",
    )
    folio = models.PositiveIntegerField()
    serie_folio = models.PositiveIntegerField(default=1)
    canal = models.CharField(max_length=15, choices=Mesa.Canal.choices)
    estado = models.CharField(max_length=15, choices=Estado.choices, default=Estado.ABIERTO)
    comentario_general = models.TextField(blank=True)
    comentarios_generales = models.JSONField(default=list, blank=True)
    salsas_verduras = models.JSONField(default=list, blank=True)
    tipo_entrega = models.CharField(
        max_length=12,
        choices=TipoEntrega.choices,
        default=TipoEntrega.APROXIMADA,
    )
    entrega_aproximada = models.TimeField(null=True, blank=True)
    terminal = models.BooleanField(default=False)
    paga_con = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    fecha_programada = models.DateField(null=True, blank=True)
    hora_programada = models.TimeField(null=True, blank=True)
    activado_programado_en = models.DateTimeField(null=True, blank=True)
    comanda_actual = models.PositiveIntegerField(default=1)
    comanda_en_edicion = models.BooleanField(default=True)
    contextos_comandas = models.JSONField(default=dict, blank=True)
    version_entidad = models.PositiveIntegerField(default=1)
    bloqueo_device_id = models.CharField(max_length=128, blank=True, db_index=True)
    bloqueo_operador = models.ForeignKey(
        UsuarioPOS,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tickets_en_edicion",
    )
    bloqueo_tomado_en = models.DateTimeField(null=True, blank=True)
    bloqueo_heartbeat_en = models.DateTimeField(null=True, blank=True)
    bloqueo_expira_en = models.DateTimeField(null=True, blank=True, db_index=True)
    descuento_porcentaje = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("100.00"))],
    )
    captura_por_nombres = models.BooleanField(default=False)
    nombres_comensales = models.JSONField(default=dict, blank=True)
    forma_pago = models.CharField(max_length=12, choices=FormaPago.choices, blank=True)
    importe_recibido = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)
    procesado_en = models.DateTimeField(null=True, blank=True)
    pagado_en = models.DateTimeField(null=True, blank=True)
    cancelado_en = models.DateTimeField(null=True, blank=True)
    cancelado_por = models.ForeignKey(
        UsuarioPOS,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tickets_cancelados",
    )
    cancelado_por_nombre = models.CharField(max_length=180, blank=True)

    class Meta:
        ordering = ["-creado_en"]
        constraints = [
            models.UniqueConstraint(
                fields=["sucursal", "serie_folio", "folio"],
                name="ticket_folio_serie_sucursal",
            ),
            models.UniqueConstraint(
                fields=["mesa"],
                condition=Q(estado__in=["abierto", "procesado", "cobrar"]),
                name="ticket_activo_unico_mesa",
            ),
            models.CheckConstraint(
                condition=Q(descuento_porcentaje__gte=0, descuento_porcentaje__lte=100),
                name="ticket_descuento_valido",
            ),
        ]
        indexes = [
            models.Index(fields=["estado", "bloqueo_expira_en"], name="ventas_t_estado_bloq_idx"),
        ]

    def __str__(self):
        return f"Ticket {self.folio} - {self.mesa.nombre}"

    @property
    def subtotal(self):
        return sum((partida.importe for partida in self.partidas.all()), Decimal("0.00"))

    @property
    def total(self):
        descuento = (self.subtotal * self.descuento_porcentaje / Decimal("100")).quantize(
            Decimal("0.01")
        )
        return self.subtotal - descuento


class SolicitudRepeticionTicket(models.Model):
    """Resultado idempotente de ``Agregar`` para domicilio y recoger."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(
        Sucursal,
        on_delete=models.PROTECT,
        related_name="solicitudes_repeticion_ticket",
    )
    ticket_origen = models.ForeignKey(
        Ticket,
        on_delete=models.PROTECT,
        related_name="solicitudes_repeticion",
    )
    ticket_nuevo = models.OneToOneField(
        Ticket,
        on_delete=models.PROTECT,
        related_name="solicitud_repeticion_origen",
    )
    clave_idempotencia = models.CharField(max_length=128)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["ticket_origen", "clave_idempotencia"],
                name="repeticion_ticket_clave_unica",
            )
        ]


class Partida(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.PROTECT, related_name="partidas")
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="partidas")
    producto = models.ForeignKey(Producto, null=True, blank=True, on_delete=models.PROTECT, related_name="partidas")
    producto_sucursal = models.ForeignKey(
        ProductoSucursal,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="partidas",
    )
    personalizada = models.BooleanField(default=False)
    promocion_aplicada = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="componentes_promocion",
    )
    # La raíz conserva la definición exacta con que se capturó, aun tras otra publicación.
    promocion_definicion = models.ForeignKey(
        "DefinicionPromocion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="partidas_raiz",
    )
    # Cada componente señala el grupo explícito; evita ambigüedad si un producto
    # está permitido en varios grupos o promociones.
    promocion_grupo = models.ForeignKey(
        "GrupoPromocion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="partidas_componente",
    )
    comensal = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(24)])
    comanda_numero = models.PositiveIntegerField(default=1)
    cantidad = models.DecimalField(max_digits=8, decimal_places=3, default=Decimal("1.000"))
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2)
    # Congela el precio de lista al capturar la línea, incluso si luego se bonifica por promoción.
    precio_lista_capturado = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    cantidad_por_precio = models.DecimalField(max_digits=8, decimal_places=3, default=Decimal("1.000"))
    unidad = models.CharField(max_length=8, blank=True)
    nombre_producto = models.CharField(max_length=180)
    nombre_corto = models.CharField(max_length=24)
    termino = models.CharField(max_length=8, choices=Producto.Termino.choices, blank=True)
    comentario = models.CharField(max_length=220, blank=True)
    procesada = models.BooleanField(default=False)
    creada_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["creada_en"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(personalizada=True, producto__isnull=True, producto_sucursal__isnull=True)
                    | Q(personalizada=False, producto__isnull=False, producto_sucursal__isnull=True)
                    | Q(personalizada=False, producto__isnull=True, producto_sucursal__isnull=False)
                ),
                name="partida_origen_valido",
            )
        ]

    @property
    def importe(self):
        divisor = self.cantidad_por_precio or Decimal("1.000")
        return ((self.cantidad / divisor) * self.precio_unitario).quantize(Decimal("0.01"))


class ModificadorTicket(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.PROTECT, related_name="modificadores_ticket")
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="modificadores")
    comensal = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(24)])
    comanda_numero = models.PositiveIntegerField(default=1)
    codigo = models.CharField(max_length=20)
    nombre = models.CharField(max_length=60)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["ticket", "comanda_numero", "comensal", "codigo"],
                name="modificador_unico_comanda_comensal",
            )
        ]


class EventoOutbox(models.Model):
    class Destino(models.TextChoices):
        LOCAL = "local", "Auditoria local"
        CENTRAL_VENTAS = "central_ventas_v2", "Central: ventas v2"
        CENTRAL_CLIENTES = "central_clientes_v2", "Central: clientes v2"
        CENTRAL_CATALOGO_ACK = "central_catalogo_ack_v2", "Central: ACK catalogo v2"

    class EstadoEntrega(models.TextChoices):
        LOCAL = "local", "Solo local"
        PENDIENTE = "pendiente", "Pendiente"
        ENTREGADO = "entregado", "Entregado"
        CONCILIACION = "conciliacion", "Requiere conciliacion"
        CUARENTENA = "cuarentena", "Cuarentena"
        SUSPENDIDO = "suspendido", "Suspendido"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.PROTECT, related_name="eventos_outbox")
    agregado = models.CharField(max_length=40)
    agregado_id = models.UUIDField()
    tipo = models.CharField(max_length=80)
    datos = models.JSONField(default=dict)
    destino = models.CharField(
        max_length=32,
        choices=Destino.choices,
        default=Destino.LOCAL,
    )
    estado_entrega = models.CharField(
        max_length=16,
        choices=EstadoEntrega.choices,
        default=EstadoEntrega.LOCAL,
    )
    version_contrato = models.PositiveSmallIntegerField(default=1)
    version_origen = models.PositiveIntegerField(default=1)
    payload_hash = models.CharField(max_length=64, blank=True)
    acuse_remoto = models.CharField(max_length=160, blank=True)
    estado_remoto = models.CharField(max_length=32, blank=True)
    ultima_respuesta_http = models.PositiveSmallIntegerField(null=True, blank=True)
    proximo_intento_en = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    publicado_en = models.DateTimeField(null=True, blank=True)
    intentos = models.PositiveIntegerField(default=0)
    ultimo_error = models.TextField(blank=True)

    class Meta:
        ordering = ["creado_en"]
        indexes = [
            models.Index(fields=["publicado_en", "creado_en"]),
            models.Index(
                fields=["destino", "estado_entrega", "proximo_intento_en", "creado_en"],
                name="ventas_outbox_entrega_idx",
            ),
        ]

    def __str__(self):
        return f"{self.tipo} · {self.agregado_id}"


class PedidoSucursalImportado(models.Model):
    """Marca idempotente para pedidos confirmados en el sistema web."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE, related_name="pedidos_sucursal_importados")
    ticket = models.OneToOneField(Ticket, on_delete=models.CASCADE, related_name="importacion_sucursal")
    origen = models.CharField(max_length=40, default="pedidos_sucursales_sqlite")
    origen_id = models.PositiveIntegerField()
    codigo_publico = models.CharField(max_length=40, blank=True)
    # Sólo v2: identidad de SucursalCliente y cuerpo exacto para verificar ACK r7.
    sender_id = models.PositiveIntegerField(null=True, blank=True)
    order_canonical_json = models.TextField(blank=True)
    order_sha256 = models.CharField(max_length=64, blank=True)
    estado_origen = models.CharField(max_length=16, blank=True)
    importado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["sucursal", "origen", "origen_id"],
                name="pedido_sucursal_importado_unico",
            ),
            models.UniqueConstraint(
                fields=["sucursal", "codigo_publico"],
                condition=models.Q(
                    origen="pedidos_sucursales_api_v2",
                    codigo_publico__gt="",
                    sender_id__isnull=True,
                ),
                name="pedido_api_v2_codigo_legacy_unico",
            ),
            models.UniqueConstraint(
                fields=["sucursal", "sender_id", "codigo_publico"],
                condition=models.Q(
                    origen="pedidos_sucursales_api_v2",
                    codigo_publico__gt="",
                    sender_id__isnull=False,
                ),
                name="pedido_api_v2_sender_codigo_unico",
            ),
        ]

    def __str__(self):
        return f"{self.origen} #{self.origen_id} → {self.ticket.folio}"


class EstadoSincronizacionPedidos(models.Model):
    """Checkpoint durable de Pedidos v2; un gap nunca se interpreta como vacio."""

    class Estado(models.TextChoices):
        LISTO = "listo", "Listo"
        RECONCILIACION = "reconciliacion", "Requiere conciliacion"
        CONTRATO_RECHAZADO = "contrato_rechazado", "Contrato rechazado"
        AUTENTICACION = "autenticacion", "Requiere credencial"
        ERROR_TRANSITORIO = "error_transitorio", "Error transitorio"
        PAUSADO = "pausado", "Pausado"

    sucursal = models.OneToOneField(
        Sucursal,
        primary_key=True,
        on_delete=models.CASCADE,
        related_name="estado_sincronizacion_pedidos",
    )
    version_api = models.CharField(max_length=8, default="v2")
    estado = models.CharField(max_length=24, choices=Estado.choices, default=Estado.LISTO)
    ventana_desde = models.DateTimeField(null=True, blank=True)
    ventana_hasta = models.DateTimeField(null=True, blank=True)
    agua_alta_hasta = models.DateTimeField(null=True, blank=True)
    sucursales_origen = models.JSONField(default=list, blank=True)
    cursor = models.TextField(blank=True)
    ultimo_cursor_confirmado = models.TextField(blank=True)
    ultimo_request_id = models.CharField(max_length=64, blank=True)
    ultimo_codigo_http = models.PositiveSmallIntegerField(null=True, blank=True)
    detalle_seguro = models.CharField(max_length=240, blank=True)
    intentos = models.PositiveIntegerField(default=0)
    ultima_sincronizacion_en = models.DateTimeField(null=True, blank=True)
    conciliacion_requerida_en = models.DateTimeField(null=True, blank=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Pedidos {self.version_api} - {self.sucursal.clave} - {self.estado}"


class MovimientoCaja(models.Model):
    class Tipo(models.TextChoices):
        INGRESO = "ingreso", "Ingreso"
        GASTO = "gasto", "Gasto"
        TERMINAL = "terminal", "Terminal"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.PROTECT, related_name="movimientos_caja")
    tipo = models.CharField(max_length=8, choices=Tipo.choices)
    concepto = models.CharField(max_length=180)
    importe = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en"]


class ControlEfectivoDia(models.Model):
    """Conteos editables que alimentan el corte de una fecha local."""

    DENOMINACIONES = ("0.5", "1", "2", "5", "10", "20", "50", "100", "200", "500", "1000")
    APPS = ("rappi", "didi", "uber_eats")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.PROTECT, related_name="controles_efectivo")
    fecha = models.DateField()
    fondo_anterior = models.JSONField(default=dict, blank=True)
    fondo_siguiente = models.JSONField(default=dict, blank=True)
    ventas_apps = models.JSONField(default=dict, blank=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-fecha"]
        constraints = [
            models.UniqueConstraint(
                fields=["sucursal", "fecha"],
                name="control_efectivo_fecha_sucursal",
            )
        ]

class ReporteAdministrativo(models.Model):
    class Tipo(models.TextChoices):
        LIQUIDACION_REPARTIDOR = "liquidacion", "Total de repartidor"
        PARCIAL = "parcial", "Reporte parcial"
        CORTE_CAJA = "corte_caja", "Corte de caja"
        CORTE_SUCURSAL = "corte_sucursal", "Corte de sucursal"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.PROTECT, related_name="reportes_administrativos")
    tipo = models.CharField(max_length=16, choices=Tipo.choices)
    datos = models.JSONField(default=dict)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en"]


class LiquidacionRepartidor(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.PROTECT, related_name="liquidaciones_repartidor")
    repartidor = models.ForeignKey(
        UsuarioPOS,
        on_delete=models.PROTECT,
        related_name="liquidaciones_repartidor",
    )
    tickets = models.ManyToManyField(Ticket, related_name="liquidaciones_repartidor")
    reporte = models.OneToOneField(
        ReporteAdministrativo,
        on_delete=models.PROTECT,
        related_name="liquidacion_repartidor",
    )
    fondo = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    total_efectivo = models.DecimalField(max_digits=12, decimal_places=2)
    total_terminal = models.DecimalField(max_digits=12, decimal_places=2)
    total_pedidos = models.DecimalField(max_digits=12, decimal_places=2)
    total_a_entregar = models.DecimalField(max_digits=12, decimal_places=2)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en"]


class CorteCaja(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.PROTECT, related_name="cortes_caja")
    inicio = models.DateTimeField(null=True, blank=True)
    fin = models.DateTimeField()
    tickets = models.ManyToManyField(Ticket, related_name="cortes_caja")
    movimientos = models.ManyToManyField(MovimientoCaja, related_name="cortes_caja")
    reporte = models.OneToOneField(
        ReporteAdministrativo,
        on_delete=models.PROTECT,
        related_name="corte_caja",
    )
    totales_canales = models.JSONField(default=dict)
    total_ventas = models.DecimalField(max_digits=12, decimal_places=2)
    total_entradas = models.DecimalField(max_digits=12, decimal_places=2)
    total_salidas = models.DecimalField(max_digits=12, decimal_places=2)
    total_fondo_anterior = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_terminales = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_fondo_siguiente = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    ventas_apps = models.JSONField(default=dict, blank=True)
    totales_sucursales = models.JSONField(default=dict, blank=True)
    total_caja = models.DecimalField(max_digits=12, decimal_places=2)
    detalle_eliminado_en = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fin"]


class ConsolidacionMensual(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        CONFIRMADA = "confirmada", "Confirmada por VPS"
        PURGADA = "purgada", "Datos locales eliminados"
        ERROR = "error", "Error"
        CONCILIACION = "conciliacion", "Requiere conciliacion"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.PROTECT, related_name="consolidaciones_mensuales")
    periodo = models.DateField(help_text="Primer día del mes consolidado.")
    idempotencia = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.PENDIENTE)
    totales = models.JSONField(default=dict)
    payload_inmutable = models.JSONField(default=dict, blank=True)
    payload_hash = models.CharField(max_length=64, blank=True)
    intentos = models.PositiveIntegerField(default=0)
    acuse_vps = models.CharField(max_length=160, blank=True)
    estado_vps = models.CharField(max_length=16, blank=True)
    ultimo_error = models.TextField(blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    confirmado_en = models.DateTimeField(null=True, blank=True)
    purgado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-periodo"]
        constraints = [
            models.UniqueConstraint(
                fields=["sucursal", "periodo"],
                name="consolidacion_periodo_sucursal",
            )
        ]

class CorteSucursal(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.PROTECT, related_name="cortes_sucursal")
    cliente_sucursal = models.ForeignKey(
        SucursalPedido,
        on_delete=models.PROTECT,
        related_name="cortes",
    )
    tickets = models.ManyToManyField(Ticket, related_name="cortes_sucursal")
    reporte = models.OneToOneField(
        ReporteAdministrativo,
        on_delete=models.PROTECT,
        related_name="corte_sucursal",
    )
    partidas = models.JSONField(default=list)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en"]


class DefinicionPromocion(models.Model):
    """Regla inmutable por publicación; una nueva versión crea otra fila."""

    class Origen(models.TextChoices):
        LEGADO = "legado", "Migración local"
        CENTRAL = "central", "Publicación Central"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.PROTECT, related_name="definiciones_promocion")
    central_id = models.UUIDField(null=True, blank=True)
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name="definiciones_promocion")
    producto_identidad = models.ForeignKey(
        "catalogo.IdentidadProductoCentral",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="definiciones_promocion",
    )
    publicacion = models.ForeignKey(
        "catalogo.PublicacionCatalogoCentral",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="definiciones_promocion",
    )
    version_publicacion = models.PositiveIntegerField(default=0)
    codigo = models.CharField(max_length=30)
    nombre = models.CharField(max_length=180)
    precio = models.DecimalField(max_digits=10, decimal_places=2)
    dias_semana = models.JSONField(default=list)
    fecha_desde = models.DateField(null=True, blank=True)
    fecha_hasta = models.DateField(null=True, blank=True)
    activo = models.BooleanField(default=True)
    origen = models.CharField(max_length=8, choices=Origen.choices, default=Origen.CENTRAL)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["sucursal", "central_id", "version_publicacion"],
                condition=Q(origen="central"),
                name="promo_central_version_unica",
            ),
            models.UniqueConstraint(
                fields=["sucursal", "producto", "version_publicacion"],
                name="promo_producto_version_unica",
            ),
        ]

    def __str__(self):
        return f"{self.codigo} · publicación {self.version_publicacion}"


class GrupoPromocion(models.Model):
    """Cantidad exacta que debe elegirse entre productos explícitamente permitidos."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    definicion = models.ForeignKey(
        DefinicionPromocion,
        on_delete=models.PROTECT,
        related_name="grupos",
    )
    central_id = models.UUIDField(null=True, blank=True)
    nombre = models.CharField(max_length=100)
    orden = models.PositiveSmallIntegerField()
    cantidad = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])

    class Meta:
        ordering = ["orden", "id"]
        constraints = [
            models.UniqueConstraint(fields=["definicion", "orden"], name="promo_grupo_orden_unico"),
            models.UniqueConstraint(
                fields=["definicion", "central_id"],
                condition=Q(central_id__isnull=False),
                name="promo_grupo_central_unico",
            ),
        ]


class ProductoPermitidoPromocion(models.Model):
    """Referencia por UUID local + mapping Central; el nombre nunca define membresía."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    grupo = models.ForeignKey(GrupoPromocion, on_delete=models.PROTECT, related_name="permitidos")
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name="permisos_promocion")
    identidad = models.ForeignKey(
        "catalogo.IdentidadProductoCentral",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="permisos_promocion",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["grupo", "producto"], name="promo_grupo_producto_unico"),
            models.UniqueConstraint(
                fields=["grupo", "identidad"],
                condition=Q(identidad__isnull=False),
                name="promo_grupo_identidad_unica",
            ),
        ]
