from django.db import migrations, models
import django.db.models.deletion


CATALOGO = (
    ("pos", "Punto de venta", True, ()),
    ("catalogo", "Catálogo local", True, ("pos",)),
    ("impresion", "Impresión", True, ("pos",)),
    ("respaldos", "Respaldos locales", True, ("pos",)),
    ("domicilios", "Domicilios", False, ("pos",)),
    ("programados", "Pedidos programados", False, ("domicilios",)),
    ("reparto", "Reparto", False, ("domicilios",)),
    ("pedidos_sucursales", "Pedidos entre sucursales", False, ("pos",)),
)


def crear_catalogo_y_preservar_capacidades(apps, schema_editor):
    Modulo = apps.get_model("personas", "Modulo")
    ModuloSucursal = apps.get_model("personas", "ModuloSucursal")
    Sucursal = apps.get_model("personas", "Sucursal")
    objetos = {}
    for clave, nombre, nucleo, _dependencias in CATALOGO:
        objetos[clave] = Modulo.objects.create(
            clave=clave,
            nombre=nombre,
            descripcion="",
            nucleo=nucleo,
            version_minima="0.4.0-dev.2",
        )
    for clave, _nombre, _nucleo, dependencias in CATALOGO:
        objetos[clave].dependencias.set(objetos[item] for item in dependencias)
    for sucursal in Sucursal.objects.all():
        for modulo in objetos.values():
            ModuloSucursal.objects.create(
                sucursal=sucursal, modulo=modulo, habilitado=True
            )


class Migration(migrations.Migration):
    dependencies = [
        ("personas", "0003_remove_usuariopos_usuario_clave_sucursal_rol_tipo_and_more")
    ]

    operations = [
        migrations.CreateModel(
            name="Modulo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("clave", models.SlugField(max_length=40, unique=True)),
                ("nombre", models.CharField(max_length=100)),
                ("descripcion", models.CharField(blank=True, max_length=240)),
                ("nucleo", models.BooleanField(default=False)),
                ("version_minima", models.CharField(max_length=32)),
                ("dependencias", models.ManyToManyField(blank=True, related_name="requerido_por", to="personas.modulo")),
            ],
            options={"verbose_name": "Módulo", "verbose_name_plural": "Módulos", "ordering": ["-nucleo", "nombre"]},
        ),
        migrations.CreateModel(
            name="ModuloSucursal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("habilitado", models.BooleanField(default=False)),
                ("configuracion", models.JSONField(blank=True, default=dict)),
                ("actualizado_en", models.DateTimeField(auto_now=True)),
                ("modulo", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="asignaciones", to="personas.modulo")),
                ("sucursal", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="modulos", to="personas.sucursal")),
            ],
            options={"verbose_name": "Módulo por sucursal", "verbose_name_plural": "Módulos por sucursal", "ordering": ["modulo__nombre"]},
        ),
        migrations.AddConstraint(
            model_name="modulosucursal",
            constraint=models.UniqueConstraint(fields=("sucursal", "modulo"), name="modulo_unico_sucursal"),
        ),
        migrations.RunPython(
            crear_catalogo_y_preservar_capacidades, migrations.RunPython.noop
        ),
    ]
