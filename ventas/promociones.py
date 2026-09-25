"""Promociones elegidas explícitamente, respaldadas por una publicación local inmutable.

Las reglas de operación se leen de datos. Los códigos heredados aparecen sólo en el
backfill de la migración, nunca gobiernan la venta normal.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import date
from decimal import Decimal

from catalogo.models import IdentidadProductoCentral, Producto, PublicacionCatalogoCentral

from .models import (
    DefinicionPromocion,
    GrupoPromocion,
    ProductoPermitidoPromocion,
)

MAX_PROMOCIONES = 500
MAX_GRUPOS = 16
MAX_PERMITIDOS = 500
MAX_REFERENCIAS_PERMITIDAS = 10000
_PRECIO = re.compile(r"^[0-9]{1,8}\.[0-9]{2}$")
_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def _publicacion_activa(sucursal_id):
    return (
        PublicacionCatalogoCentral.objects.filter(
            sucursal_id=sucursal_id,
            estado=PublicacionCatalogoCentral.Estado.APLICADA,
        )
        .order_by("-version")
        .first()
    )


def configuracion_promocion(producto_o_codigo, *, partida=None, sucursal=None):
    """Devuelve la definición actual o la capturada en una raíz histórica.

    Una publicación aplicada sustituye por completo al backfill legado; no se
    recurre a PB/PL/P4/PK si Central retiró una promoción.
    """
    if partida is not None and getattr(partida, "promocion_definicion_id", None):
        return partida.promocion_definicion
    if isinstance(producto_o_codigo, Producto):
        producto = producto_o_codigo
    else:
        if sucursal is None:
            return None
        producto = Producto.objects.filter(sucursal=sucursal, codigo=producto_o_codigo).first()
    if producto is None:
        return None
    publicacion = _publicacion_activa(producto.sucursal_id)
    consulta = DefinicionPromocion.objects.filter(sucursal_id=producto.sucursal_id, producto=producto)
    if publicacion and publicacion.version_contrato >= 3:
        return consulta.filter(publicacion=publicacion, origen=DefinicionPromocion.Origen.CENTRAL).first()
    # v2 carecía de promociones publicadas: conserva temporalmente el backfill
    # hasta recibir la primera publicación v3 de la misma sucursal.
    return consulta.filter(origen=DefinicionPromocion.Origen.LEGADO).first()


def promocion_disponible(producto_o_codigo, fecha, *, partida=None, sucursal=None):
    definicion = configuracion_promocion(producto_o_codigo, partida=partida, sucursal=sucursal)
    return bool(
        definicion
        and definicion.activo
        and fecha.weekday() in definicion.dias_semana
        and (definicion.fecha_desde is None or fecha >= definicion.fecha_desde)
        and (definicion.fecha_hasta is None or fecha <= definicion.fecha_hasta)
    )


def etiqueta_dias_promocion(definicion):
    dias = sorted(set(definicion.dias_semana))
    if dias == [0, 1, 2, 3, 4]:
        return "Lunes a viernes"
    nombres = [_DIAS[d] for d in dias]
    if len(nombres) == 1:
        return nombres[0].capitalize()
    return (", ".join(nombres[:-1]) + " y " + nombres[-1]).capitalize()


def grupos_promocion(partida_raiz):
    definicion = configuracion_promocion(partida_raiz.producto, partida=partida_raiz)
    if definicion is None:
        return []
    return list(definicion.grupos.prefetch_related("permitidos__producto").order_by("orden", "id"))


def capacidad_componentes(partida_promocion):
    return {
        grupo.id: Decimal(grupo.cantidad) * partida_promocion.cantidad
        for grupo in grupos_promocion(partida_promocion)
    }


def cantidades_componentes(partida_promocion, excluir_ids=()):
    cantidades = defaultdict(Decimal)
    componentes = partida_promocion.componentes_promocion.all()
    if excluir_ids:
        componentes = componentes.exclude(id__in=excluir_ids)
    for componente in componentes:
        cantidades[componente.promocion_grupo_id] += componente.cantidad
    return cantidades


def validar_cupo_componente(partida_promocion, producto, cantidad, grupo_id=None, excluir_ids=()):
    """Valida elección manual; retorna el grupo para grabarlo en Partida."""
    grupos = grupos_promocion(partida_promocion)
    candidatos = [
        grupo for grupo in grupos
        if any(permitido.producto_id == producto.id for permitido in grupo.permitidos.all())
    ]
    if grupo_id is not None:
        candidatos = [grupo for grupo in candidatos if str(grupo.id) == str(grupo_id)]
    if not candidatos:
        raise ValueError(
            f"{producto.nombre} no forma parte del grupo de la promoción {partida_promocion.producto.codigo}."
        )
    if len(candidatos) != 1:
        raise ValueError("Elige el grupo de la promoción para este producto.")
    grupo = candidatos[0]
    cantidades = cantidades_componentes(partida_promocion, excluir_ids)
    cupo = Decimal(grupo.cantidad) * partida_promocion.cantidad
    if cantidad <= 0 or cantidades[grupo.id] + cantidad > cupo:
        disponible = max(Decimal("0"), cupo - cantidades[grupo.id])
        raise ValueError(
            f"La promoción {partida_promocion.producto.codigo} sólo admite "
            f"{int(disponible)} unidad(es) adicionales en {grupo.nombre}."
        )
    return grupo


def _raices(ticket):
    return ticket.partidas.select_related("producto", "promocion_definicion").filter(
        promocion_definicion__isnull=False,
        promocion_aplicada__isnull=True,
    ).order_by("creada_en", "id")


def validar_promociones(ticket):
    for raiz in _raices(ticket):
        capacidad = capacidad_componentes(raiz)
        cantidades = defaultdict(Decimal)
        grupos = {grupo.id: grupo for grupo in grupos_promocion(raiz)}
        for componente in raiz.componentes_promocion.select_related("producto", "promocion_grupo"):
            grupo = grupos.get(componente.promocion_grupo_id)
            if (
                grupo is None
                or componente.ticket_id != raiz.ticket_id
                or componente.comanda_numero != raiz.comanda_numero
                or componente.sucursal_id != raiz.sucursal_id
                or componente.promocion_definicion_id is not None
                or not grupo.permitidos.filter(producto_id=componente.producto_id).exists()
            ):
                raise ValueError(f"La promoción {raiz.producto.codigo} contiene un componente inválido.")
            cantidades[grupo.id] += componente.cantidad
        faltantes = [
            f"{int(cantidades[grupo_id])}/{int(esperado)} {grupos[grupo_id].nombre}"
            for grupo_id, esperado in capacidad.items()
            if cantidades[grupo_id] != esperado
        ]
        if faltantes:
            raise ValueError(f"Completa la promoción {raiz.producto.codigo}: " + ", ".join(faltantes) + ".")


def promocion_pendiente(ticket):
    for raiz in _raices(ticket):
        capacidad = capacidad_componentes(raiz)
        cantidades = cantidades_componentes(raiz)
        if any(cantidades[grupo_id] < esperado for grupo_id, esperado in capacidad.items()):
            return raiz
    return None


def promociones_pendientes(ticket):
    pendientes = []
    for raiz in _raices(ticket):
        capacidad = capacidad_componentes(raiz)
        cantidades = cantidades_componentes(raiz)
        if any(cantidades[grupo_id] < esperado for grupo_id, esperado in capacidad.items()):
            pendientes.append(str(raiz.id))
    return pendientes


def _uuid(valor, ruta):
    if type(valor) is not str or len(valor) != 36:
        raise ValueError(f"UUID inválido en {ruta}.")
    try:
        resultado = uuid.UUID(valor)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"UUID inválido en {ruta}.") from exc
    if str(resultado) != valor or resultado.int == 0:
        raise ValueError(f"UUID inválido en {ruta}.")
    return resultado


def _fecha(valor, ruta):
    if valor is None:
        return None
    if type(valor) is not str or len(valor) != 10:
        raise ValueError(f"Fecha inválida en {ruta}.")
    try:
        return date.fromisoformat(valor)
    except ValueError as exc:
        raise ValueError(f"Fecha inválida en {ruta}.") from exc


def validar_promociones_publicadas(promociones, productos_por_central):
    """Rechaza reglas ambiguas antes de alterar el catálogo local."""
    if type(promociones) is not list or len(promociones) > MAX_PROMOCIONES:
        raise ValueError("Lista de promociones inválida.")
    ids = set()
    codigos = set()
    principales = set()
    referencias_permitidas = 0
    for indice, promocion in enumerate(promociones):
        ruta = f"promociones[{indice}]"
        if type(promocion) is not dict or set(promocion) != {
            "id", "codigo", "nombre", "producto_central_id", "precio", "activo",
            "dias_semana", "fecha_desde", "fecha_hasta", "grupos",
        }:
            raise ValueError(f"Esquema inválido en {ruta}.")
        identidad = _uuid(promocion["id"], f"{ruta}.id")
        principal = _uuid(promocion["producto_central_id"], f"{ruta}.producto_central_id")
        codigo = promocion["codigo"]
        nombre = promocion["nombre"]
        if (
            identidad in ids or principal in principales or type(codigo) is not str
            or not codigo or len(codigo) > 30 or any(ord(c) < 32 for c in codigo)
            or codigo.casefold() in codigos or type(nombre) is not str or not nombre.strip()
            or len(nombre) > 180 or any(ord(c) < 32 for c in nombre)
        ):
            raise ValueError(f"Identidad o texto inválido en {ruta}.")
        ids.add(identidad)
        principales.add(principal)
        codigos.add(codigo.casefold())
        if type(promocion["activo"]) is not bool:
            raise ValueError(f"Estado inválido en {ruta}.")
        dias = promocion["dias_semana"]
        if type(dias) is not list or not dias or len(dias) > 7 or any(type(d) is not int or d not in range(7) for d in dias) or len(set(dias)) != len(dias):
            raise ValueError(f"Días inválidos en {ruta}.")
        desde = _fecha(promocion["fecha_desde"], f"{ruta}.fecha_desde")
        hasta = _fecha(promocion["fecha_hasta"], f"{ruta}.fecha_hasta")
        if desde and hasta and desde > hasta:
            raise ValueError(f"Fechas invertidas en {ruta}.")
        precio = promocion["precio"]
        if type(precio) is not str or not _PRECIO.fullmatch(precio) or Decimal(precio) <= 0:
            raise ValueError(f"Precio inválido en {ruta}.")
        producto = productos_por_central.get(principal)
        if (
            not producto or not producto["activo"] or not producto["disponible_sucursal"]
            or producto["precio"]["importe"] != precio or producto["codigo"] != codigo
        ):
            raise ValueError(f"Producto principal no disponible o precio/código divergente en {ruta}.")
        grupos = promocion["grupos"]
        if type(grupos) is not list or not 1 <= len(grupos) <= MAX_GRUPOS:
            raise ValueError(f"Grupos inválidos en {ruta}.")
        ids_grupo = set()
        ordenes = set()
        for numero, grupo in enumerate(grupos):
            gruta = f"{ruta}.grupos[{numero}]"
            if type(grupo) is not dict or set(grupo) != {"id", "nombre", "orden", "cantidad", "productos_permitidos"}:
                raise ValueError(f"Esquema inválido en {gruta}.")
            gid = _uuid(grupo["id"], f"{gruta}.id")
            if gid in ids_grupo or type(grupo["orden"]) is not int or not 0 <= grupo["orden"] <= 65535 or grupo["orden"] in ordenes:
                raise ValueError(f"Identidad u orden inválido en {gruta}.")
            ids_grupo.add(gid)
            ordenes.add(grupo["orden"])
            if type(grupo["nombre"]) is not str or not grupo["nombre"].strip() or len(grupo["nombre"]) > 100 or any(ord(c) < 32 for c in grupo["nombre"]):
                raise ValueError(f"Nombre inválido en {gruta}.")
            if type(grupo["cantidad"]) is not int or not 1 <= grupo["cantidad"] <= 999:
                raise ValueError(f"Cantidad inválida en {gruta}.")
            permitidos = grupo["productos_permitidos"]
            if type(permitidos) is not list or not 1 <= len(permitidos) <= MAX_PERMITIDOS:
                raise ValueError(f"Productos permitidos inválidos en {gruta}.")
            referencias_permitidas += len(permitidos)
            if referencias_permitidas > MAX_REFERENCIAS_PERMITIDAS:
                raise ValueError("La publicación excede el límite total de productos permitidos.")
            ids_permitidos = [_uuid(v, f"{gruta}.productos_permitidos") for v in permitidos]
            if len(set(ids_permitidos)) != len(ids_permitidos) or principal in ids_permitidos:
                raise ValueError(f"Productos permitidos repetidos o recursivos en {gruta}.")
            for pid in ids_permitidos:
                candidato = productos_por_central.get(pid)
                if not candidato or not candidato["activo"] or not candidato["disponible_sucursal"]:
                    raise ValueError(f"Producto permitido no disponible en {gruta}.")
    for promocion in promociones:
        for grupo in promocion["grupos"]:
            if any(uuid.UUID(pid) in principales for pid in grupo["productos_permitidos"]):
                raise ValueError("Una promoción no puede ser componente de otra.")


def aplicar_promociones_publicadas(sucursal, publicacion, promociones, mapeos_productos):
    """Aplicar después del upsert de productos y antes del commit de publicación.

    El llamador mantiene transaction.atomic. Una falla revierte categorías, productos,
    precios, reglas y ACK en conjunto.
    """
    if publicacion.sucursal_id != sucursal.id:
        raise ValueError("La publicación no pertenece a esta sucursal.")
    if DefinicionPromocion.objects.filter(publicacion=publicacion).exists():
        raise ValueError("La publicación ya tiene definiciones de promoción.")
    for promocion in promociones:
        identidad = mapeos_productos[uuid.UUID(promocion["producto_central_id"])]
        if identidad.sucursal_id != sucursal.id:
            raise ValueError("Mapping de promoción de otra sucursal.")
        definicion = DefinicionPromocion.objects.create(
            sucursal=sucursal,
            central_id=uuid.UUID(promocion["id"]),
            producto=identidad.producto,
            producto_identidad=identidad,
            publicacion=publicacion,
            version_publicacion=publicacion.version,
            codigo=promocion["codigo"],
            nombre=promocion["nombre"],
            precio=Decimal(promocion["precio"]),
            dias_semana=sorted(promocion["dias_semana"]),
            fecha_desde=_fecha(promocion["fecha_desde"], "fecha_desde"),
            fecha_hasta=_fecha(promocion["fecha_hasta"], "fecha_hasta"),
            activo=promocion["activo"],
            origen=DefinicionPromocion.Origen.CENTRAL,
        )
        for grupo_dato in promocion["grupos"]:
            grupo = GrupoPromocion.objects.create(
                definicion=definicion,
                central_id=uuid.UUID(grupo_dato["id"]),
                nombre=grupo_dato["nombre"],
                orden=grupo_dato["orden"],
                cantidad=grupo_dato["cantidad"],
            )
            for permitido_id in grupo_dato["productos_permitidos"]:
                mapeo = mapeos_productos[uuid.UUID(permitido_id)]
                if mapeo.sucursal_id != sucursal.id:
                    raise ValueError("Mapping de componente de otra sucursal.")
                ProductoPermitidoPromocion.objects.create(
                    grupo=grupo,
                    producto=mapeo.producto,
                    identidad=mapeo,
                )
