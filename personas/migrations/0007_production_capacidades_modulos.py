from django.db import migrations, models


CAPACIDADES_POR_TIPO = {
    "dueno": (
        "ventas", "comandas", "modificar_partidas", "cobrar", "reimprimir",
        "cancelar", "administrar_negocio", "gestionar_usuarios",
        "cambiar_pines_ajenos", "reiniciar_folios", "sincronizar_pedidos",
    ),
    "encargado": ("ventas", "comandas", "modificar_partidas"),
    "elevado": (
        "ventas", "comandas", "modificar_partidas", "cobrar", "reimprimir",
        "cancelar", "administrar_negocio", "sincronizar_pedidos",
    ),
    "mesero": ("ventas", "comandas", "modificar_partidas"),
    "repartidor": (),
}

LEGADAS = (
    ("puede_cobrar", "cobrar"),
    ("puede_reimprimir", "reimprimir"),
    ("puede_cancelar", "cancelar"),
    ("puede_sincronizar", "sincronizar_pedidos"),
)
NUCLEO = (
    "pos", "catalogo", "impresion", "respaldos", "domicilios",
    "programados", "reparto",
)


def actualizar_capacidades_y_modulos(apps, schema_editor):
    Rol = apps.get_model("personas", "Rol")
    Sucursal = apps.get_model("personas", "Sucursal")
    Modulo = apps.get_model("personas", "Modulo")
    ModuloSucursal = apps.get_model("personas", "ModuloSucursal")
    alias = schema_editor.connection.alias

    for rol in Rol.objects.using(alias).all().iterator():
        capacidades = set(CAPACIDADES_POR_TIPO.get(rol.tipo, ()))
        if rol.tipo in {"dueno", "encargado", "elevado"}:
            capacidades.update(
                capacidad for campo, capacidad in LEGADAS if getattr(rol, campo)
            )
        rol.capacidades = sorted(capacidades)
        rol.save(update_fields=["capacidades"])

    Modulo.objects.using(alias).filter(clave__in=NUCLEO).update(nucleo=True)
    modulos = dict(
        Modulo.objects.using(alias).filter(clave__in=(*NUCLEO, "pedidos_sucursales"))
        .values_list("clave", "id")
    )
    for sucursal in Sucursal.objects.using(alias).all().iterator():
        for clave in NUCLEO:
            if clave in modulos:
                ModuloSucursal.objects.using(alias).update_or_create(
                    sucursal_id=sucursal.id,
                    modulo_id=modulos[clave],
                    defaults={"habilitado": True},
                )
        if "pedidos_sucursales" in modulos:
            ModuloSucursal.objects.using(alias).update_or_create(
                sucursal_id=sucursal.id,
                modulo_id=modulos["pedidos_sucursales"],
                defaults={"habilitado": sucursal.clave == "ARBOLEDAS"},
            )


class Migration(migrations.Migration):
    dependencies = [("personas", "0006_usuariopos_es_sistema_and_more")]

    operations = [
        migrations.AlterField(
            model_name="rol",
            name="tipo",
            field=models.CharField(
                choices=[
                    ("dueno", "Dueño de sucursal"),
                    ("encargado", "Encargado legado"),
                    ("elevado", "Elevado"),
                    ("mesero", "Mesero"),
                    ("repartidor", "Repartidor"),
                ],
                default="mesero",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="rol",
            name="capacidades",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(
            actualizar_capacidades_y_modulos,
            migrations.RunPython.noop,
        ),
    ]
