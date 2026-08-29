import uuid

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models


class Sucursal(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clave = models.CharField(max_length=30, unique=True)
    nombre = models.CharField(max_length=120)
    activa = models.BooleanField(default=True)
    creada_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name_plural = "Sucursales"

    def __str__(self):
        return self.nombre


class Rol(models.Model):
    class Tipo(models.TextChoices):
        ENCARGADO = "encargado", "Encargado"
        MESERO = "mesero", "Mesero"
        REPARTIDOR = "repartidor", "Repartidor"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE, related_name="roles")
    nombre = models.CharField(max_length=60)
    tipo = models.CharField(max_length=16, choices=Tipo.choices, default=Tipo.MESERO)
    puede_cobrar = models.BooleanField(default=False)
    puede_reimprimir = models.BooleanField(default=False)
    puede_cancelar = models.BooleanField(default=False)
    puede_sincronizar = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["sucursal", "nombre"], name="rol_unico_sucursal")]

    def __str__(self):
        return self.nombre


class UsuarioPOS(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE, related_name="usuarios_pos")
    rol = models.ForeignKey(Rol, on_delete=models.PROTECT, related_name="usuarios")
    cuenta = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="perfil_pos",
    )
    nombre = models.CharField(max_length=100)
    clave = models.CharField(max_length=128)
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Usuario POS"
        verbose_name_plural = "Usuarios POS"

    def __str__(self):
        return self.nombre

    def set_clave(self, clave):
        self.clave = make_password(str(clave))

    def check_clave(self, clave):
        return check_password(str(clave), self.clave)
