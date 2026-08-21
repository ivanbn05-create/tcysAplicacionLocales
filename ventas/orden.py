ORDEN_TERMINOS = {"dorado": 0, "medio": 1, "blando": 2, "": 3}
PRODUCTOS_SIEMPRE_AL_FINAL = ("BBQ05", "BBQ1", "CO8", "CO05", "CO1")


def clave_orden_partida(partida):
    codigo = partida.producto.codigo.upper()
    if codigo in PRODUCTOS_SIEMPRE_AL_FINAL:
        return (1, PRODUCTOS_SIEMPRE_AL_FINAL.index(codigo), ORDEN_TERMINOS.get(partida.termino, 9))
    return (0, partida.producto.orden, ORDEN_TERMINOS.get(partida.termino, 9), partida.nombre_corto)


def ordenar_partidas(partidas):
    return sorted(partidas, key=clave_orden_partida)
