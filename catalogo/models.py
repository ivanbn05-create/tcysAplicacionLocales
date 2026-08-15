import uuid

from django.db import models
from django.utils import timezone

from personas.models import Sucursal


class Categoria(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE, related_name="categorias")
    nombre = models.CharField(max_length=100)
    orden = models.PositiveSmallIntegerField(default=0)
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ["orden", "nombre"]
        constraints = [models.UniqueConstraint(fields=["sucursal", "nombre"], name="categoria_unica_sucursal")]

    def __str__(self):
        return self.nombre


class Producto(models.Model):
    class Destino(models.TextChoices):
        COCINA = "cocina", "Cocina"
        BARRA = "barra", "Barra"
        CAJA = "caja", "Caja"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE, related_name="productos")
    categoria = models.ForeignKey(Categoria, on_delete=models.PROTECT, related_name="productos")
    codigo = models.CharField(max_length=30)
    nombre = models.CharField(max_length=180)
    nombre_corto = models.CharField(max_length=24)
    destino_impresion = models.CharField(max_length=10, choices=Destino.choices, default=Destino.COCINA)
    activo = models.BooleanField(default=True)
    origen = models.CharField(max_length=30, default="menu_2026")
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["categoria__orden", "nombre"]
        constraints = [models.UniqueConstraint(fields=["sucursal", "codigo"], name="producto_codigo_sucursal")]

    def __str__(self):
        return self.nombre

    def precio_actual(self):
        hoy = timezone.localdate()
        return self.precios.filter(activo=True, vigente_desde__lte=hoy).filter(
            models.Q(vigente_hasta__isnull=True) | models.Q(vigente_hasta__gte=hoy)
        ).order_by("-vigente_desde").first()


class Precio(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE, related_name="precios")
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name="precios")
    importe = models.DecimalField(max_digits=10, decimal_places=2)
    vigente_desde = models.DateField(default=timezone.localdate)
    vigente_hasta = models.DateField(null=True, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["-vigente_desde"]
        constraints = [models.UniqueConstraint(fields=["sucursal", "producto", "vigente_desde"], name="precio_unico_vigencia")]

    def __str__(self):
        return f"{self.producto}: ${self.importe}"
