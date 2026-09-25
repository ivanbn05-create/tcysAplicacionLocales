"""Convertir reglas PB/PL/P4/PK a datos una sola vez.

Los códigos aparecen aquí por migración histórica, no en la ejecución de ventas.
La primera publicación Central reemplazará estas definiciones para nuevas líneas,
pero las referencias de tickets abiertos/cerrados conservan esta revisión.
"""

from decimal import Decimal

from django.db import migrations
from django.db.models import Q
from django.utils import timezone


LEGADO = {
    "PB": ("Taco y bebida", (0, 3), (("Tacos de barbacoa sin queso", 3, "taco_barbacoa"), ("Bebidas de 500 ml", 1, "bebida_500"))),
    "PL": ("Lonche y taco", (1, 2), (("Lonche de barbacoa", 1, "lonche_barbacoa"), ("Taco de barbacoa sin queso", 1, "taco_barbacoa"))),
    "P4": ("Cuatro tacos", (1, 2), (("Tacos de barbacoa sin queso", 4, "taco_barbacoa"),)),
    "PK": ("Bistec", (0, 1, 2, 3, 4), (("Tacos de bistec sin queso", 3, "taco_bistec"),)),
}
LITRO = {"HR1", "HB1", "JAM1"}


def _tipo(producto, categoria):
    codigo = producto.codigo.upper()
    if codigo == "TB":
        return "taco_barbacoa"
    if codigo == "TBI":
        return "taco_bistec"
    if codigo in {"LB", "LOBQ"}:
        return "lonche_barbacoa"
    if categoria.nombre.casefold() == "bebidas" and codigo not in LITRO:
        return "bebida_500"
    return None


def migrar_legado(apps, schema_editor):
    Producto = apps.get_model("catalogo", "Producto")
    Precio = apps.get_model("catalogo", "Precio")
    Identidad = apps.get_model("catalogo", "IdentidadProductoCentral")
    Partida = apps.get_model("ventas", "Partida")
    Definicion = apps.get_model("ventas", "DefinicionPromocion")
    Grupo = apps.get_model("ventas", "GrupoPromocion")
    Permitido = apps.get_model("ventas", "ProductoPermitidoPromocion")
    Sucursal = apps.get_model("personas", "Sucursal")
    db = schema_editor.connection.alias
    hoy = timezone.localdate()
    for sucursal in Sucursal.objects.using(db).all().iterator():
        productos = list(Producto.objects.using(db).filter(sucursal_id=sucursal.id).select_related("categoria"))
        por_codigo = {producto.codigo.upper(): producto for producto in productos}
        por_tipo = {}
        for producto in productos:
            tipo = _tipo(producto, producto.categoria)
            if tipo:
                por_tipo.setdefault(tipo, []).append(producto)
        identidades = {
            item.producto_id: item
            for item in Identidad.objects.using(db).filter(sucursal_id=sucursal.id)
        }
        for codigo, (nombre, dias, requisitos) in LEGADO.items():
            principal = por_codigo.get(codigo)
            if principal is None or Definicion.objects.using(db).filter(
                sucursal_id=sucursal.id, producto_id=principal.id, version_publicacion=0,
            ).exists():
                continue
            precio = (
                Precio.objects.using(db).filter(
                    producto_id=principal.id, activo=True, vigente_desde__lte=hoy
                ).filter(Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gte=hoy))
                .order_by("-vigente_desde").first()
            )
            raiz_anterior = Partida.objects.using(db).filter(
                sucursal_id=sucursal.id,
                producto_id=principal.id,
                promocion_aplicada_id__isnull=True,
            ).first()
            importe = (precio.importe if precio else raiz_anterior.precio_unitario if raiz_anterior else Decimal("0.00"))
            definicion = Definicion.objects.using(db).create(
                sucursal_id=sucursal.id,
                producto_id=principal.id,
                producto_identidad_id=(identidades[principal.id].id if principal.id in identidades else None),
                version_publicacion=0,
                codigo=principal.codigo,
                nombre=nombre,
                precio=importe,
                dias_semana=list(dias),
                activo=principal.activo and all(por_tipo.get(tipo) for _, _, tipo in requisitos),
                origen="legado",
            )
            grupos = {}
            for orden, (etiqueta, cantidad, tipo) in enumerate(requisitos):
                grupo = Grupo.objects.using(db).create(
                    definicion_id=definicion.id,
                    nombre=etiqueta,
                    orden=orden,
                    cantidad=cantidad,
                )
                grupos[tipo] = grupo
                for producto in por_tipo.get(tipo, []):
                    Permitido.objects.using(db).create(
                        grupo_id=grupo.id,
                        producto_id=producto.id,
                        identidad_id=(identidades[producto.id].id if producto.id in identidades else None),
                    )
            raices = Partida.objects.using(db).filter(
                sucursal_id=sucursal.id,
                producto_id=principal.id,
                promocion_aplicada_id__isnull=True,
            )
            for raiz in raices.iterator():
                Partida.objects.using(db).filter(pk=raiz.pk).update(promocion_definicion_id=definicion.id)
                componentes = Partida.objects.using(db).filter(promocion_aplicada_id=raiz.id).select_related("producto__categoria")
                for componente in componentes.iterator():
                    if componente.producto_id is None:
                        continue
                    tipo = _tipo(componente.producto, componente.producto.categoria)
                    grupo = grupos.get(tipo)
                    if grupo:
                        Partida.objects.using(db).filter(pk=componente.pk).update(promocion_grupo_id=grupo.id)


class Migration(migrations.Migration):
    dependencies = [("ventas", "0025_promociones_dinamicas")]
    operations = [migrations.RunPython(migrar_legado, migrations.RunPython.noop)]
