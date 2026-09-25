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


class Modulo(models.Model):
    clave = models.SlugField(max_length=40, unique=True)
    nombre = models.CharField(max_length=100)
    descripcion = models.CharField(max_length=240, blank=True)
    nucleo = models.BooleanField(default=False)
    version_minima = models.CharField(max_length=32)
    dependencias = models.ManyToManyField(
        "self", symmetrical=False, blank=True, related_name="requerido_por"
    )

    class Meta:
        ordering = ["-nucleo", "nombre"]
        verbose_name = "Módulo"
        verbose_name_plural = "Módulos"

    def __str__(self):
        return self.nombre


class ModuloSucursal(models.Model):
    sucursal = models.ForeignKey(
        Sucursal, on_delete=models.CASCADE, related_name="modulos"
    )
    modulo = models.ForeignKey(
        Modulo, on_delete=models.PROTECT, related_name="asignaciones"
    )
    habilitado = models.BooleanField(default=False)
    configuracion = models.JSONField(default=dict, blank=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["modulo__nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=["sucursal", "modulo"],
                name="modulo_unico_sucursal",
            )
        ]
        verbose_name = "Módulo por sucursal"
        verbose_name_plural = "Módulos por sucursal"

    def __str__(self):
        estado = "habilitado" if self.habilitado else "deshabilitado"
        return f"{self.sucursal}: {self.modulo} ({estado})"


class Rol(models.Model):
    class Tipo(models.TextChoices):
        DUENO = "dueno", "Dueño de sucursal"
        ENCARGADO = "encargado", "Encargado legado"
        ELEVADO = "elevado", "Elevado"
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
    capacidades = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["sucursal", "nombre"], name="rol_unico_sucursal")]

    def __str__(self):
        return self.nombre

    def save(self, *args, **kwargs):
        if self._state.adding and not self.capacidades:
            from .capacidades import capacidades_iniciales

            self.capacidades = capacidades_iniciales(
                self.tipo,
                puede_cobrar=self.puede_cobrar,
                puede_reimprimir=self.puede_reimprimir,
                puede_cancelar=self.puede_cancelar,
                puede_sincronizar=self.puede_sincronizar,
            )
        super().save(*args, **kwargs)


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
    es_sistema = models.BooleanField(
        default=False,
        help_text="Actor tecnico protegido; no se administra como personal operativo.",
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Usuario POS"
        verbose_name_plural = "Usuarios POS"
        constraints = [
            models.UniqueConstraint(
                fields=["sucursal"],
                condition=models.Q(es_sistema=True),
                name="usuario_sistema_unico_sucursal",
            )
        ]

    def __str__(self):
        return self.nombre

    def set_clave(self, clave):
        self.clave = make_password(str(clave))

    def check_clave(self, clave):
        return check_password(str(clave), self.clave)
