from collections import defaultdict
from decimal import Decimal


PROMOCIONES = {
    "PB": {
        "abreviatura": "PB",
        "nombre": "Taco y bebida",
        "dias": {0, 3},
        "dias_texto": "Lunes y jueves",
        "requisitos": (("taco_barbacoa", 3), ("bebida_500", 1)),
    },
    "PL": {
        "abreviatura": "PL",
        "nombre": "Lonche y taco",
        "dias": {1, 2},
        "dias_texto": "Martes y miércoles",
        "requisitos": (("lonche_barbacoa", 1), ("taco_barbacoa", 1)),
    },
    "P4": {
        "abreviatura": "P4",
        "nombre": "Cuatro tacos",
        "dias": {1, 2},
        "dias_texto": "Martes y miércoles",
        "requisitos": (("taco_barbacoa", 4),),
    },
    "PK": {
        "abreviatura": "PK",
        "nombre": "Bistec",
        "dias": {0, 1, 2, 3, 4},
        "dias_texto": "Lunes a viernes",
        "requisitos": (("taco_bistec", 3),),
    },
}

BEBIDAS_DE_LITRO = {"HR1", "HB1", "JAM1"}


def configuracion_promocion(producto_o_codigo):
    codigo = getattr(producto_o_codigo, "codigo", producto_o_codigo)
    return PROMOCIONES.get(str(codigo or "").upper())


def promocion_disponible(producto_o_codigo, fecha):
    configuracion = configuracion_promocion(producto_o_codigo)
    return bool(configuracion and fecha.weekday() in configuracion["dias"])


def tipo_componente(producto):
    codigo = producto.codigo.upper()
    if codigo == "TB":
        return "taco_barbacoa"
    if codigo == "TBI":
        return "taco_bistec"
    if codigo in {"LB", "LOBQ"}:
        return "lonche_barbacoa"
    if producto.categoria.nombre.casefold() == "bebidas" and codigo not in BEBIDAS_DE_LITRO:
        return "bebida_500"
    return ""


def capacidad_componentes(partida_promocion):
    configuracion = configuracion_promocion(partida_promocion.producto)
    if not configuracion:
        return {}
    return {
        tipo: Decimal(str(cantidad)) * partida_promocion.cantidad
        for tipo, cantidad in configuracion["requisitos"]
    }


def cantidades_componentes(partida_promocion, excluir_ids=()):
    cantidades = defaultdict(Decimal)
    componentes = partida_promocion.componentes_promocion.select_related("producto__categoria")
    if excluir_ids:
        componentes = componentes.exclude(id__in=excluir_ids)
    for componente in componentes:
        cantidades[tipo_componente(componente.producto)] += componente.cantidad
    return cantidades


def validar_cupo_componente(partida_promocion, producto, cantidad, excluir_ids=()):
    tipo = tipo_componente(producto)
    capacidad = capacidad_componentes(partida_promocion)
    if not tipo or tipo not in capacidad:
        raise ValueError(f"{producto.nombre} no forma parte de la promoción {partida_promocion.producto.codigo}.")
    acumuladas = cantidades_componentes(partida_promocion, excluir_ids)
    if acumuladas[tipo] + cantidad > capacidad[tipo]:
        faltante = max(Decimal("0"), capacidad[tipo] - acumuladas[tipo])
        raise ValueError(
            f"La promoción {partida_promocion.producto.codigo} sólo admite "
            f"{int(faltante)} unidad(es) adicionales de este componente."
        )


def validar_promociones(ticket):
    promociones = ticket.partidas.select_related("producto").filter(
        producto__codigo__in=PROMOCIONES,
        promocion_aplicada__isnull=True,
    )
    for partida_promocion in promociones:
        capacidad = capacidad_componentes(partida_promocion)
        cantidades = cantidades_componentes(partida_promocion)
        faltantes = []
        for tipo, esperado in capacidad.items():
            actual = cantidades[tipo]
            if actual != esperado:
                nombres = {
                    "taco_barbacoa": "taco(s) de barbacoa sin queso",
                    "taco_bistec": "taco(s) de bistec sin queso",
                    "lonche_barbacoa": "lonche(s) de barbacoa",
                    "bebida_500": "bebida(s) de 500 ml",
                }
                faltantes.append(f"{int(actual)}/{int(esperado)} {nombres[tipo]}")
        if faltantes:
            raise ValueError(
                f"Completa la promoción {partida_promocion.producto.codigo}: " + ", ".join(faltantes) + "."
            )


def promocion_pendiente(ticket):
    promociones = ticket.partidas.select_related("producto").filter(
        producto__codigo__in=PROMOCIONES,
        promocion_aplicada__isnull=True,
    ).order_by("creada_en")
    for partida in promociones:
        capacidad = capacidad_componentes(partida)
        cantidades = cantidades_componentes(partida)
        if any(cantidades[tipo] < esperado for tipo, esperado in capacidad.items()):
            return partida
    return None


def promociones_pendientes(ticket):
    pendientes = []
    promociones = ticket.partidas.select_related("producto").filter(
        producto__codigo__in=PROMOCIONES,
        promocion_aplicada__isnull=True,
    ).order_by("creada_en")
    for partida in promociones:
        capacidad = capacidad_componentes(partida)
        cantidades = cantidades_componentes(partida)
        if any(cantidades[tipo] < esperado for tipo, esperado in capacidad.items()):
            pendientes.append(str(partida.id))
    return pendientes
