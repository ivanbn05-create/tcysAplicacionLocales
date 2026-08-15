import uuid

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
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE, related_name="roles")
    nombre = models.CharField(max_length=60)
    puede_cobrar = models.BooleanField(default=False)
    puede_reimprimir = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["sucursal", "nombre"], name="rol_unico_sucursal")]

    def __str__(self):
        return self.nombre


class UsuarioPOS(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE, related_name="usuarios_pos")
    rol = models.ForeignKey(Rol, on_delete=models.PROTECT, related_name="usuarios")
    nombre = models.CharField(max_length=100)
    clave = models.CharField(max_length=20)
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["sucursal", "clave"], name="usuario_clave_sucursal")]
        verbose_name = "Usuario POS"
        verbose_name_plural = "Usuarios POS"

    def __str__(self):
        return self.nombre
