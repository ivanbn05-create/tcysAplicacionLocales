import uuid
import warnings

from PIL import Image, UnidentifiedImageError
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone

from personas.models import Sucursal


TAMANO_MAXIMO_IMAGEN_PRODUCTO = 1 * 1024 * 1024
DIMENSION_MAXIMA_IMAGEN_PRODUCTO = 4096
PIXELES_MAXIMOS_IMAGEN_PRODUCTO = 8_000_000
FORMATOS_IMAGEN_PRODUCTO = {"JPEG", "PNG", "WEBP"}


def validar_imagen_producto(archivo):
    """Valida el contenido de una imagen y deja el archivo listo para reutilizarse."""

    try:
        try:
            tamano = archivo.size
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            raise ValidationError("No fue posible validar la imagen del producto.") from exc

        if tamano > TAMANO_MAXIMO_IMAGEN_PRODUCTO:
            raise ValidationError("La imagen del producto no puede superar 1 MB.")

        archivo.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(archivo) as imagen:
                if imagen.format not in FORMATOS_IMAGEN_PRODUCTO:
                    raise ValidationError("La imagen del producto debe ser un JPG, PNG o WebP válido.")

                # El POS sólo necesita una miniatura estática. Rechazar animaciones
                # evita forzar al decodificador a recorrer un número no acotado de
                # cuadros dentro de un archivo comprimido pequeño.
                if getattr(imagen, "n_frames", 1) != 1:
                    raise ValidationError("La imagen del producto debe tener un solo cuadro.")

                ancho, alto = imagen.size
                if (
                    ancho > DIMENSION_MAXIMA_IMAGEN_PRODUCTO
                    or alto > DIMENSION_MAXIMA_IMAGEN_PRODUCTO
                    or ancho * alto > PIXELES_MAXIMOS_IMAGEN_PRODUCTO
                ):
                    raise ValidationError(
                        "La imagen del producto supera las dimensiones máximas permitidas."
                    )
                imagen.verify()
    except ValidationError:
        raise
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ValidationError(
            "La imagen del producto supera las dimensiones seguras permitidas."
        ) from exc
    except (OSError, SyntaxError, TypeError, ValueError, UnidentifiedImageError) as exc:
        raise ValidationError("La imagen del producto debe ser un JPG, PNG o WebP válido.") from exc
    finally:
        try:
            archivo.seek(0)
        except (AttributeError, OSError, ValueError):
            pass


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
    class Termino(models.TextChoices):
        DORADO = "dorado", "Dorado"
        MEDIO = "medio", "Medio"
        BLANDO = "blando", "Blando"

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
    orden = models.PositiveSmallIntegerField(default=0)
    permite_termino = models.BooleanField(default=False)
    termino_predeterminado = models.CharField(max_length=8, choices=Termino.choices, blank=True)
    abreviaturas_termino = models.JSONField(default=dict, blank=True)
    imagen = models.ImageField(
        upload_to="productos/%Y/%m/",
        blank=True,
        validators=[
            FileExtensionValidator(["jpg", "jpeg", "png", "webp"]),
            validar_imagen_producto,
        ],
        help_text="JPG, PNG o WebP estático de hasta 1 MB. El POS genera una miniatura 4:3 protegida.",
    )
    destino_impresion = models.CharField(max_length=10, choices=Destino.choices, default=Destino.COCINA)
    activo = models.BooleanField(default=True)
    origen = models.CharField(max_length=30, default="menu_2026")
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["categoria__orden", "orden", "nombre"]
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
