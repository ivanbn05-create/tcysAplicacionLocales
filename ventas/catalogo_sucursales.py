"""Catálogo mayorista vigente exportado el 24/08/2026.

La fuente operativa es ``productos_sucursales_precios_vigentes.csv``.  Se
conservan los identificadores del sistema de pedidos de sucursales para que la
integración posterior no dependa de nombres que pueden cambiar.
"""

from decimal import Decimal


SUCURSALES_PEDIDO = (
    (1, "Aguilas", "sucursal"),
    (2, "Fortin", "sucursal"),
    (3, "Estancia", "sucursal"),
    (4, "Eventos MO", "sucursal"),
    (5, "Plaza del Sol", "sucursal"),
    (6, "Santa Anita", "sucursal"),
    (7, "Brot Nueva Galicia", "cliente_mayorista"),
    (8, "Brot CAT", "cliente_mayorista"),
    (9, "Rakebela", "cliente_mayorista"),
    (10, "Eventos Edgar", "cliente_mayorista"),
)


# origen_id, nombre, nombre ticket base, unidad, cantidad por precio
PRODUCTOS_SUCURSALES = (
    (1, "LITRO DE BARBACOA", "BARBACOA", "LT", "1.000"),
    (2, "TORTILLA ESPECIAL", "TORTILLA", "KG", "1.000"),
    (3, "BOLILLO", "BOLILLO", "PZA", "1.000"),
    (4, "QUESO", "QUESO", "KG", "1.000"),
    (5, "CEBOLLA BLANCA", "C. PICADA", "KG", "1.000"),
    (6, "CEBOLLA GUISADA", "C. GUISADA", "KG", "1.000"),
    (7, "CHILE GÜERO", "CHILE", "KG", "1.000"),
    (8, "SALSA DE TOMATE", "S. ROJA", "LT", "1.000"),
    (9, "SALSA DE AGUACATE", "S. AGUACATE", "LT", "1.000"),
    (10, "SALSA DE CHIPOTLE", "S. CHIPOTLE", "LT", "1.000"),
    (11, "SALSA DE SERRANO", "S. SERRANO", "LT", "1.000"),
    (12, "SALSA VERDE SIN CHILE", "S. VERDE S/CH", "LT", "1.000"),
    (13, "SALSA MEXICANA", "S. MEXICANA", "KG", "1.000"),
    (14, "SALSA HABANERO TATEMADO", "S. HABANERO", "LT", "1.000"),
    (15, "SALSA DE CACAHUATE", "S. CACAHUATE", "LT", "1.000"),
    (16, "CEBOLLA MORADA RAYADA", "C. MORADA RYD", "KG", "1.000"),
    (17, "PREPARADO CEBOLLA MORADA", "PREP. C. MORADA", "LT", "1.000"),
    (18, "PEPINO", "PEPINO", "KG", "1.000"),
    (19, "RÁBANO", "RÁBANO", "KG", "1.000"),
    (20, "LIMÓN", "LIMÓN", "KG", "1.000"),
    (21, "CILANTRO", "CILANTRO", "KG", "1.000"),
    (22, "GRASA", "GRASA", "LT", "1.000"),
    (23, "CONSOMÉ", "CONSOMÉ", "LT", "1.000"),
    (24, "BISTEK", "BISTEK", "KG", "1.000"),
    (25, "ARRACHERA", "ARRACHERA", "KG", "1.000"),
    (26, "CHORIZO", "CHORIZO", "KG", "1.000"),
    (27, "AGUA HORCHATA BLANCA 1/2", "HB 1/2", "PZA", "1.000"),
    (28, "AGUA HORCHATA BLANCA LT", "HB LT", "PZA", "1.000"),
    (29, "AGUA HORCHATA ROSA 1/2", "HR 1/2", "PZA", "1.000"),
    (30, "AGUA HORCHATA ROSA LT", "HR LT", "PZA", "1.000"),
    (31, "AGUA JAMAICA 1/2", "JAM 1/2", "PZA", "1.000"),
    (32, "AGUA JAMAICA LT", "JAM LT", "PZA", "1.000"),
    (33, "SERVILLETAS", "SERVILLETA", "PZA", "1.000"),
    (34, "VASO 8 TÉRMICO DART", "VASO 8 oz", "PZA", "1.000"),
    (35, "CUCHARA CHICA ECONÓMICA", "CUCHARA", "PZA", "1.000"),
    (36, "6x6 NEVADO 125 PZAS", "6x6", "PZA", "1.000"),
    (37, "7x7 LISO NEVADO 100 PZAS", "7x7", "PZA", "1.000"),
    (38, "HOAGIE REYMA", "HOAGIE", "PZA", "1.000"),
)


PRECIOS_BASE = {
    1: "193.00", 2: "25.50", 3: "9.00", 4: "160.00", 5: "52.00",
    6: "60.00", 7: "64.00", 8: "60.00", 9: "60.00", 10: "56.00",
    11: "56.00", 12: "55.00", 13: "45.00", 14: "55.00", 15: "70.00",
    16: "43.00", 17: "27.00", 18: "30.00", 19: "30.00", 20: "13.00",
    21: "200.00", 22: "20.00", 23: "0.00", 24: "220.00", 25: "220.00",
    26: "125.00", 27: "19.00", 28: "32.00", 29: "19.00", 30: "32.00",
    31: "19.00", 32: "32.00", 33: "39.00", 34: "16.50", 35: "9.50",
    36: "108.00", 37: "170.00", 38: "151.00",
}


def configuracion_precio(tipo, producto_id, nombre_ticket, cliente_origen_id=None):
    """Devuelve disponibilidad, alias y precio para el grupo del CSV."""
    if tipo == "cliente_mayorista":
        if producto_id in {25, 26}:
            return None
        importe = {1: "203.00", 2: "26.50", 22: "30.00", 23: "15.00"}.get(
            producto_id,
            PRECIOS_BASE[producto_id],
        )
        alias = {
            1: "BARBACOA .M", 2: "TORTILLA .M", 22: "GRASA .M", 23: "CONSOMÉ .M",
            27: "HB 1/2 .M", 28: "HB LT .M", 29: "HR 1/2 .M", 30: "HR LT .M",
            31: "JAM 1/2 .M", 32: "JAM LT .M",
        }.get(producto_id, nombre_ticket)
        return Decimal(importe), alias
    importe = (
        "200.00"
        if cliente_origen_id == 1 and producto_id == 24
        else PRECIOS_BASE[producto_id]
    )
    return Decimal(importe), nombre_ticket
