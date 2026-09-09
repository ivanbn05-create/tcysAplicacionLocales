import os
import socket
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from PIL import Image, ImageChops, ImageDraw, ImageFont

from ventas.orden import PRODUCTOS_SIEMPRE_AL_FINAL, ordenar_partidas
from ventas.promociones import configuracion_promocion


ANCHO = 576
MARGEN = 22


def _ruta_fuente(negrita=False, cursiva=False):
    personalizada = os.getenv("THERMAL_FONT_PATH")
    candidatos = []
    if personalizada:
        candidatos.append(personalizada)
    if os.name == "nt":
        nombre = "arialbi.ttf" if negrita and cursiva else "arialbd.ttf" if negrita else "ariali.ttf" if cursiva else "arial.ttf"
        candidatos.append(str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / nombre))
    else:
        nombre = "DejaVuSans-BoldOblique.ttf" if negrita and cursiva else "DejaVuSans-Bold.ttf" if negrita else "DejaVuSans-Oblique.ttf" if cursiva else "DejaVuSans.ttf"
        candidatos.append(f"/usr/share/fonts/truetype/dejavu/{nombre}")
    return next((ruta for ruta in candidatos if Path(ruta).is_file()), None)


def fuente(tamano, negrita=False, cursiva=False):
    ruta = _ruta_fuente(negrita, cursiva)
    return ImageFont.truetype(ruta, tamano) if ruta else ImageFont.load_default()


def _ancho(draw, texto, font):
    caja = draw.textbbox((0, 0), str(texto), font=font)
    return caja[2] - caja[0]


def _centrado(draw, y, texto, font, fill=0):
    draw.text(((ANCHO - _ancho(draw, texto, font)) / 2, y), str(texto), font=font, fill=fill)


def _derecha(draw, y, texto, font, margen=MARGEN):
    draw.text((ANCHO - margen - _ancho(draw, texto, font), y), str(texto), font=font, fill=0)


def _ajustar(draw, texto, font, ancho):
    palabras = str(texto).split()
    lineas, actual = [], ""
    for palabra in palabras:
        prueba = f"{actual} {palabra}".strip()
        if actual and _ancho(draw, prueba, font) > ancho:
            lineas.append(actual)
            actual = palabra
        else:
            actual = prueba
    if actual:
        lineas.append(actual)
    return lineas or [""]


def _cantidad_matriz(valor):
    if valor == valor.to_integral_value():
        return str(int(valor))
    return format(valor.normalize(), "f")


def _segmentos_bebidas(bebidas_agrupadas, fuente_normal, fuente_negrita):
    grupos = []
    for indice, (nombre_corto, cantidad_bebida) in enumerate(bebidas_agrupadas.items()):
        segmentos = []
        if indice:
            segmentos.append(("* ", fuente_normal))
        if cantidad_bebida >= 2:
            segmentos.append((f"( {_cantidad_matriz(cantidad_bebida)} ) ", fuente_negrita))
        segmentos.append((nombre_corto.upper(), fuente_normal))
        if indice < len(bebidas_agrupadas) - 1:
            segmentos.append((" ", fuente_normal))
        grupos.append(segmentos)
    return grupos


def _dibujar_segmentos(draw, y, grupos, ancho, alto_linea=34):
    x = MARGEN
    limite = MARGEN + ancho
    for segmentos in grupos:
        ancho_grupo = sum(_ancho(draw, texto, font) for texto, font in segmentos)
        if x > MARGEN and x + ancho_grupo > limite:
            y += alto_linea
            x = MARGEN
        for texto, font in segmentos:
            draw.text((x, y), texto, font=font, fill=0)
            x += _ancho(draw, texto, font)
    return y + alto_linea


def _logo_actual():
    ruta = Path(settings.BASE_DIR) / "ventas" / "static" / "ventas" / "brand" / "logoactual.jpeg"
    if not ruta.is_file():
        return None
    original = Image.open(ruta).convert("RGB")
    fondo = Image.new("RGB", original.size, original.getpixel((0, 0)))
    mascara = ImageChops.difference(original, fondo).convert("L").point(lambda pixel: 255 if pixel > 14 else 0)
    caja = mascara.getbbox()
    if caja:
        mascara = mascara.crop(caja)
    logo = Image.new("1", mascara.size, 1)
    logo.paste(0, mask=mascara)
    logo.thumbnail((300, 108), Image.Resampling.LANCZOS)
    return logo


def _partidas_destino(ticket, destino, comanda_numero=None, canal=None):
    consulta = ticket.partidas.select_related("producto__categoria").all()
    if comanda_numero is not None:
        consulta = consulta.filter(comanda_numero=comanda_numero)
    partidas = list(consulta)
    if destino == "cocina":
        return [
            p
            for p in partidas
            if p.producto.categoria.nombre.lower() not in {"extras", "bebidas"}
            and (
                (canal or ticket.canal) in {"comedor", "llevar", "domicilio", "recoger"}
                or p.producto.destino_impresion == "cocina"
            )
        ]
    if destino == "barra":
        return [p for p in partidas if p.producto.destino_impresion == "barra"]
    return partidas


def _hora_corta(valor):
    return valor.strftime("%I:%M %p").lstrip("0").lower()


def _contexto_comanda(ticket, comanda_numero):
    contexto = (ticket.contextos_comandas or {}).get(str(comanda_numero), {})
    return contexto if isinstance(contexto, dict) else {}


def _hora_contexto(contexto, campo, respaldo):
    valor = contexto.get(campo) if contexto else None
    if valor:
        try:
            return datetime.strptime(str(valor), "%H:%M").time()
        except ValueError:
            pass
    return respaldo


def _texto_entrega(ticket, incluir_toma=True, contexto=None):
    entrega = _hora_contexto(contexto, "entrega_aproximada", ticket.entrega_aproximada)
    if not entrega:
        return "Sin hora de entrega"
    entrega_texto = _hora_corta(entrega)
    tipo_entrega = (contexto or {}).get("tipo_entrega", ticket.tipo_entrega)
    if ticket.fecha_programada and ticket.hora_programada:
        return f"Programado: {ticket.fecha_programada.strftime('%d/%m/%Y')} {_hora_corta(ticket.hora_programada)}"
    if tipo_entrega == "programada":
        return f"Programado: {entrega_texto}"
    if not incluir_toma:
        return f"Entrega aprox: {entrega_texto}"
    tomada = _hora_corta(timezone.localtime(ticket.creado_en))
    return f"{tomada} - {entrega_texto}"


def _texto_salsas(ticket, contexto=None):
    grupos = []
    for grupo in (contexto or {}).get("salsas_verduras", ticket.salsas_verduras) or []:
        prefijo = str(grupo.get("prefijo", "")).strip()
        elementos = ", ".join(str(item) for item in grupo.get("elementos", []))
        if elementos:
            grupos.append(f"{prefijo} {elementos}".strip())
    return " * ".join(grupos)


def _identificador_ticket(ticket, contexto=None):
    canal = (contexto or {}).get("canal", ticket.canal)
    posicion = (contexto or {}).get("posicion_numero", ticket.mesa.orden)
    if canal == "domicilio":
        return f"Ticket: {ticket.folio}, {posicion}"
    if canal == "recoger":
        return f"Ticket: {ticket.folio}, R{posicion}"
    return f"Ticket: {ticket.folio}"


def _contacto_pedido(ticket, contexto=None):
    if contexto:
        return (
            str(contexto.get("contacto_pedido_nombre", "")),
            str(contexto.get("contacto_pedido_telefono", "")),
        )
    if (
        ticket.canal != "domicilio"
        or not ticket.cliente_id
        or not ticket.cliente.comentarios_multiples
    ):
        return "", ""
    return ticket.contacto_pedido_nombre, ticket.contacto_pedido_telefono


def _agrupar_partidas_total(ticket):
    """Agrupa conceptos idénticos para los tickets de cuenta y domicilio."""
    partidas = [
        partida
        for partida in ordenar_partidas(ticket.partidas.select_related("producto__categoria").all())
        if not partida.promocion_aplicada_id
    ]
    agrupadas = {}
    for partida in partidas:
        clave = (partida.producto_id, partida.termino, partida.nombre_producto)
        if clave not in agrupadas:
            agrupadas[clave] = {
                "nombre": partida.nombre_producto,
                "cantidad": Decimal("0"),
                "precio_unitario": Decimal("0"),
                "importe": Decimal("0"),
                "comensales": set(),
            }
        fila = agrupadas[clave]
        fila["cantidad"] += partida.cantidad
        fila["importe"] += partida.importe
        fila["comensales"].add(partida.comensal)
    for fila in agrupadas.values():
        # Si el mismo concepto conserva precios históricos distintos, el ticket
        # sigue mostrando una sola cantidad y un solo importe. La columna de
        # precio unitario refleja el promedio efectivo de ese renglón.
        if fila["cantidad"]:
            fila["precio_unitario"] = fila["importe"] / fila["cantidad"]
    return list(agrupadas.values())


def _datos_comanda_por_nombres(ticket, comanda_numero=None):
    consulta = ticket.partidas.select_related("producto__categoria").all()
    modificadores = ticket.modificadores.all()
    if comanda_numero is not None:
        consulta = consulta.filter(comanda_numero=comanda_numero)
        modificadores = modificadores.filter(comanda_numero=comanda_numero)
    partidas = [
        partida
        for partida in ordenar_partidas(consulta)
        if not configuracion_promocion(partida.producto)
    ]
    principales, complementos = [], []
    for partida in partidas:
        if (
            partida.producto.codigo.upper() in PRODUCTOS_SIEMPRE_AL_FINAL
            or partida.producto.categoria.nombre.lower() == "bebidas"
        ):
            complementos.append(partida)
        else:
            principales.append(partida)

    columnas = []
    for partida in principales:
        clave = (partida.producto_id, partida.termino)
        if not any(columna and columna["clave"] == clave for columna in columnas):
            columnas.append({"clave": clave, "nombre": partida.nombre_corto})
    columnas = columnas[:4]
    while len(columnas) < 4:
        columnas.append(None)

    cantidades = defaultdict(lambda: defaultdict(Decimal))
    for partida in principales:
        cantidades[partida.comensal][(partida.producto_id, partida.termino)] += partida.cantidad
    modificaciones = defaultdict(list)
    for modificador in modificadores:
        modificaciones[modificador.comensal].append(modificador.codigo)

    contexto = _contexto_comanda(ticket, comanda_numero) if comanda_numero is not None else {}
    nombres = contexto.get("nombres_comensales", ticket.nombres_comensales) or {}
    personas = sorted({partida.comensal for partida in partidas})
    filas = [
        {
            "persona": persona,
            "nombre": str(nombres.get(str(persona), "")).strip() or f"PERSONA {persona}",
            "modificadores": " ".join(modificaciones.get(persona, [])),
            "cantidades": [cantidades[persona].get(columna["clave"]) if columna else None for columna in columnas],
        }
        for persona in personas
    ]

    extras_por_persona = defaultdict(lambda: defaultdict(Decimal))
    for partida in complementos:
        extras_por_persona[partida.comensal][partida.nombre_corto] += partida.cantidad
    extras = [
        {
            "persona": persona,
            "nombre": str(nombres.get(str(persona), "")).strip() or f"PERSONA {persona}",
            "conceptos": conceptos,
        }
        for persona, conceptos in sorted(extras_por_persona.items())
    ]
    return {"columnas": columnas, "filas": filas, "extras": extras}


def _dibujar_comanda_por_nombres(draw, y, ticket, comanda_numero=None):
    datos = _datos_comanda_por_nombres(ticket, comanda_numero)
    contexto = _contexto_comanda(ticket, comanda_numero) if comanda_numero is not None else {}
    f_encabezado = fuente(18, negrita=True)
    f_nombre = fuente(21, negrita=True)
    f_modificador = fuente(15)
    f_cantidad = fuente(28, negrita=True)
    f_extras = fuente(23, negrita=True)
    ancho_nombre = 190
    ancho_producto = (ANCHO - MARGEN * 2 - ancho_nombre) / 4

    alto_encabezado = 62
    draw.rectangle((MARGEN, y, ANCHO - MARGEN, y + alto_encabezado), outline=0, width=2)
    draw.text((MARGEN + 7, y + 19), "NOMBRE", font=f_encabezado, fill=0)
    for indice, columna in enumerate(datos["columnas"]):
        x = MARGEN + ancho_nombre + ancho_producto * indice
        draw.line((x, y, x, y + alto_encabezado), fill=0, width=2)
        texto = columna["nombre"] if columna else ""
        lineas = _ajustar(draw, texto, f_encabezado, ancho_producto - 8)[:2]
        linea_y = y + max(5, (alto_encabezado - len(lineas) * 22) // 2)
        for linea in lineas:
            draw.text((x + (ancho_producto - _ancho(draw, linea, f_encabezado)) / 2, linea_y), linea, font=f_encabezado, fill=0)
            linea_y += 22
    y += alto_encabezado

    generales = " · ".join(
        item.get("nombre", "")
        for item in contexto.get("comentarios_generales", ticket.comentarios_generales) or []
    ).upper()
    if generales:
        lineas = _ajustar(draw, generales, f_encabezado, ANCHO - MARGEN * 2 - 14)
        alto = max(44, len(lineas) * 22 + 12)
        draw.rectangle((MARGEN, y, ANCHO - MARGEN, y + alto), outline=0, width=2)
        linea_y = y + 6
        for linea in lineas:
            _centrado(draw, linea_y, linea, f_encabezado)
            linea_y += 22
        y += alto

    for fila in datos["filas"]:
        alto_fila = 60
        draw.rectangle((MARGEN, y, ANCHO - MARGEN, y + alto_fila), outline=0, width=1)
        draw.text((MARGEN + 6, y + 6), fila["nombre"].upper()[:18], font=f_nombre, fill=0)
        if fila["modificadores"]:
            draw.text((MARGEN + 7, y + 35), fila["modificadores"][:24], font=f_modificador, fill=0)
        for indice, valor in enumerate(fila["cantidades"]):
            x = MARGEN + ancho_nombre + ancho_producto * indice
            draw.line((x, y, x, y + alto_fila), fill=0, width=1)
            if valor:
                texto = _cantidad_matriz(valor)
                draw.text((x + (ancho_producto - _ancho(draw, texto, f_cantidad)) / 2, y + 13), texto, font=f_cantidad, fill=0)
        y += alto_fila

    if datos["extras"]:
        draw.rectangle((MARGEN, y, ANCHO - MARGEN, y + 38), outline=0, width=2)
        _centrado(draw, y + 7, "CONSOMÉS Y BEBIDAS", f_encabezado)
        y += 38
        for extra in datos["extras"]:
            segmentos = " * ".join(
                f"{_cantidad_matriz(cantidad)} {nombre.upper()}"
                for nombre, cantidad in extra["conceptos"].items()
            )
            lineas = _ajustar(draw, segmentos, f_extras, ANCHO - MARGEN * 2 - ancho_nombre - 14)
            alto = max(52, len(lineas) * 28 + 12)
            draw.rectangle((MARGEN, y, ANCHO - MARGEN, y + alto), outline=0, width=1)
            draw.line((MARGEN + ancho_nombre, y, MARGEN + ancho_nombre, y + alto), fill=0, width=1)
            draw.text((MARGEN + 6, y + 12), extra["nombre"].upper()[:18], font=f_nombre, fill=0)
            linea_y = y + 6
            for linea in lineas:
                draw.text((MARGEN + ancho_nombre + 7, linea_y), linea, font=f_extras, fill=0)
                linea_y += 28
            y += alto
    return y + 24


def render_comanda(ticket, destino, comanda_numero=None):
    comanda_numero = int(comanda_numero or ticket.comanda_actual or 1)
    contexto = _contexto_comanda(ticket, comanda_numero)
    canal = contexto.get("canal", ticket.canal)
    try:
        total_comanda = Decimal(str(contexto.get("total", ticket.total)))
    except (ValueError, TypeError, ArithmeticError):
        total_comanda = ticket.total
    captura_por_nombres = bool(
        contexto.get("captura_por_nombres", ticket.captura_por_nombres)
    )
    partidas_destino = ordenar_partidas(
        _partidas_destino(ticket, destino, comanda_numero, canal)
    )
    # La fila de promociones sólo es una ayuda de captura en la comanda virtual.
    # En cocina se imprimen exclusivamente los productos que la componen.
    partidas = [partida for partida in partidas_destino if not configuracion_promocion(partida.producto)]
    bebidas = (
        list(
            ticket.partidas.select_related("producto__categoria").filter(
                comanda_numero=comanda_numero,
                producto__categoria__nombre__iexact="Bebidas",
            )
        )
        if destino == "cocina" and canal in {"comedor", "llevar", "domicilio", "recoger"}
        else []
    )
    modificadores = list(
        ticket.modificadores.filter(comanda_numero=comanda_numero)
    )
    ultimo_comensal = max([p.comensal for p in partidas] + [m.comensal for m in modificadores] + [1])
    bloques = max(1, min(4, (ultimo_comensal + 5) // 6))
    bebidas_agrupadas = defaultdict(Decimal)
    for bebida in ordenar_partidas(bebidas):
        bebidas_agrupadas[bebida.nombre_corto] += bebida.cantidad
    contacto_nombre, contacto_telefono = _contacto_pedido(ticket, contexto)
    contacto_pedido = " ".join(parte for parte in [contacto_nombre, contacto_telefono] if parte)
    imagen = Image.new("L", (ANCHO, 6000), 255)
    draw = ImageDraw.Draw(imagen)
    f_titulo = fuente(30, cursiva=True)
    f_normal = fuente(26)
    f_bold = fuente(28, negrita=True)
    f_total = f_bold
    f_tabla = fuente(25)
    f_tabla_bold = fuente(25, negrita=True)
    f_matriz_numero = fuente(30, negrita=True)
    f_chico = fuente(21)
    f_bebidas = fuente(29, negrita=True)

    y = 24
    titulo = "Los Tocayos Tacos de Barbacoa"
    _centrado(draw, y, titulo, f_titulo)
    y += 39
    ancho_titulo = _ancho(draw, titulo, f_titulo)
    draw.line(((ANCHO - ancho_titulo) / 2, y, (ANCHO + ancho_titulo) / 2, y), fill=0, width=2)
    local = timezone.localtime(ticket.creado_en)
    y += 12
    if canal in {"domicilio", "recoger"}:
        draw.text((MARGEN, y), local.strftime("%d/%m/%Y"), font=f_normal, fill=0)
        _derecha(draw, y, _identificador_ticket(ticket, contexto), f_bold)
        y += 35
        draw.text((MARGEN, y), _texto_entrega(ticket, contexto=contexto), font=f_normal, fill=0)
        _derecha(draw, y, f"${total_comanda:,.2f}", f_total)
        y += 43
        if canal == "recoger":
            contacto = " · ".join(
                parte
                for parte in [
                    contexto.get("cliente_nombre", ticket.cliente_nombre),
                    contexto.get("cliente_telefono", ticket.cliente_telefono),
                ]
                if parte
            )
            for linea in _ajustar(draw, f"RECOGER: {contacto}".upper(), f_bold, ANCHO - MARGEN * 2):
                _centrado(draw, y, linea, f_bold)
                y += 34
            y += 5
    elif canal in {"comedor", "llevar"}:
        draw.text((MARGEN, y), f"{local.strftime('%d/%m/%Y')} {_hora_corta(local)}", font=f_normal, fill=0)
        _derecha(draw, y, contexto.get("mesa", ticket.mesa.nombre), f_bold)
        y += 38
        draw.text((MARGEN, y), _identificador_ticket(ticket, contexto), font=f_bold, fill=0)
        _derecha(draw, y, f"${total_comanda:,.2f}", f_total)
        y += 46
        if canal == "llevar":
            cliente_nombre = contexto.get("cliente_nombre", ticket.cliente_nombre)
            for linea in _ajustar(draw, f"LLEVAR: {cliente_nombre}".upper(), f_bold, ANCHO - MARGEN * 2):
                _centrado(draw, y, linea, f_bold)
                y += 34
            y += 5
    else:
        draw.text((MARGEN, y), f"{local.strftime('%d/%m/%Y')} {_hora_corta(local)}", font=f_normal, fill=0)
        _derecha(draw, y, f"Ticket: {ticket.folio}", f_bold)
        y += 36
        _derecha(draw, y, f"${total_comanda:,.2f}", f_total)
        y += 42
    if destino != "cocina":
        _centrado(draw, y, destino.upper(), f_chico)
        y += 32
    if comanda_numero > 1:
        _centrado(draw, y, f"COMANDA {comanda_numero}", f_chico)
        y += 30

    if captura_por_nombres:
        y = _dibujar_comanda_por_nombres(
            draw,
            y,
            ticket,
            comanda_numero,
        )
    else:
        agrupadas = {}
        for partida in partidas:
            clave = (partida.producto_id, partida.termino)
            if clave not in agrupadas:
                agrupadas[clave] = {"nombre": partida.nombre_corto, "cantidades": defaultdict(Decimal)}
            agrupadas[clave]["cantidades"][partida.comensal] += partida.cantidad
        mods_por_persona = defaultdict(list)
        for modificador in modificadores:
            mods_por_persona[modificador.comensal].append(modificador.codigo)

        for bloque in range(bloques):
            inicio = bloque * 6 + 1
            personas = list(range(inicio, inicio + 6))
            etiqueta = 132
            celda = (ANCHO - MARGEN * 2 - etiqueta) / 6
            arriba = y
            draw.line((MARGEN, arriba, ANCHO - MARGEN, arriba), fill=0, width=3)
            x0 = MARGEN + etiqueta
            for indice, persona in enumerate(personas):
                x = x0 + celda * indice
                draw.rectangle((x, arriba, x + celda, arriba + 48), outline=0, width=2)
                texto = str(persona)
                draw.text((x + (celda - _ancho(draw, texto, f_matriz_numero)) / 2, arriba + 6), texto, font=f_matriz_numero, fill=0)
            y = arriba + 48
            comentarios_generales = contexto.get(
                "comentarios_generales", ticket.comentarios_generales
            ) or []
            if comentarios_generales:
                texto_general = " · ".join(item.get("nombre", "") for item in comentarios_generales).upper()
                lineas_general = _ajustar(draw, texto_general, f_tabla_bold, ANCHO - MARGEN * 2 - 12)
                alto_comentario = max(78, len(lineas_general) * 30 + 14)
                draw.rectangle((MARGEN, y, ANCHO - MARGEN, y + alto_comentario), outline=0, width=2)
                linea_y = y + max(7, (alto_comentario - len(lineas_general) * 30) // 2)
                for linea in lineas_general:
                    _centrado(draw, linea_y, linea, f_tabla_bold)
                    linea_y += 30
                y += alto_comentario
            else:
                alto_comentario = 78
                draw.line((MARGEN, y + alto_comentario, ANCHO - MARGEN, y + alto_comentario), fill=0, width=2)
                for indice, persona in enumerate(personas):
                    x = x0 + celda * indice
                    draw.line((x, y, x, y + alto_comentario), fill=0, width=1)
                    mods = " ".join(mods_por_persona.get(persona, []))
                    lineas = _ajustar(draw, mods, f_chico, celda - 8)[:2]
                    linea_y = y + 9
                    for linea in lineas:
                        draw.text((x + 4, linea_y), linea, font=f_chico, fill=0)
                        linea_y += 28
                draw.line((ANCHO - MARGEN, y, ANCHO - MARGEN, y + alto_comentario), fill=0, width=1)
                y += alto_comentario

            for fila in agrupadas.values():
                draw.text((MARGEN, y + 6), fila["nombre"], font=f_tabla, fill=0)
                for indice, persona in enumerate(personas):
                    x = x0 + celda * indice
                    draw.line((x, y, x, y + 46), fill=0, width=1)
                    valor = fila["cantidades"].get(persona)
                    if valor:
                        texto = _cantidad_matriz(valor)
                        draw.text((x + (celda - _ancho(draw, texto, f_matriz_numero)) / 2, y + 5), texto, font=f_matriz_numero, fill=0)
                draw.line((ANCHO - MARGEN, y, ANCHO - MARGEN, y + 46), fill=0, width=1)
                draw.line((MARGEN, y + 46, ANCHO - MARGEN, y + 46), fill=145, width=1)
                y += 48
            y += 26

    if contacto_pedido:
        draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=2)
        y += 14
        for linea in _ajustar(draw, f"CONTACTO: {contacto_pedido}".upper(), f_bold, ANCHO - MARGEN * 2):
            _centrado(draw, y, linea, f_bold)
            y += 36

    comentario_general = contexto.get("comentario_general", ticket.comentario_general)
    if comentario_general:
        draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=2)
        y += 16
        for linea in _ajustar(draw, str(comentario_general).upper(), f_bold, ANCHO - MARGEN * 2):
            _centrado(draw, y, linea, f_bold)
            y += 36

    if bebidas_agrupadas and not captura_por_nombres:
        draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=2)
        y += 12
        segmentos = _segmentos_bebidas(bebidas_agrupadas, f_bebidas, f_bebidas)
        y = _dibujar_segmentos(draw, y, segmentos, ANCHO - MARGEN * 2, alto_linea=38)
        y += 8

    salsas = _texto_salsas(ticket, contexto)
    if salsas:
        draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=2)
        y += 12
        for linea in _ajustar(draw, salsas.upper(), f_bold, ANCHO - MARGEN * 2):
            _centrado(draw, y, linea, f_bold)
            y += 36
    if contexto.get("terminal", ticket.terminal) and canal in {"domicilio", "recoger"}:
        draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=3)
        y += 14
        _centrado(draw, y, "PAGO: T E R M I N A L", f_bold)
        y += 42
    return imagen.crop((0, 0, ANCHO, min(y + 38, imagen.height))).convert("1")


def render_domicilio(ticket):
    """Ticket para el repartidor con cliente, entrega, conceptos e importe a cobrar."""
    partidas = _agrupar_partidas_total(ticket)
    imagen = Image.new("L", (ANCHO, max(3000, 1100 + len(partidas) * 115)), 255)
    draw = ImageDraw.Draw(imagen)
    f_logo = fuente(54, negrita=True)
    f_titulo = fuente(29, negrita=True)
    f_normal = fuente(23)
    f_bold = fuente(26, negrita=True)
    f_cursiva = fuente(28, cursiva=True)
    f_total = fuente(38, negrita=True)
    f_cant = fuente(23, cursiva=True)

    def lineas_centradas(texto, font, separacion=34):
        nonlocal y
        for linea in _ajustar(draw, texto, font, ANCHO - MARGEN * 2):
            _centrado(draw, y, linea, font)
            y += separacion

    y = 0
    logo = _logo_actual()
    if logo:
        imagen.paste(logo, ((ANCHO - logo.width) // 2, y))
        y += logo.height + 18
    else:
        _centrado(draw, y, "LOS TOCAYOS", f_logo)
        y += 66
        _centrado(draw, y, "TACOS DE BARBACOA", f_titulo)
        y += 52
    version = Image.new("L", (110, 24), 255)
    ImageDraw.Draw(version).text((0, 0), "EBF3.2.0", font=fuente(18, negrita=True), fill=0)
    version = version.rotate(90, expand=True)
    imagen.paste(version, (2, max(35, y - 105)))
    for linea in ["BAVJ051126EI9", "Tel: 3631-6834 / 1542-1635"]:
        _centrado(draw, y, linea, f_normal)
        y += 30

    y += 14
    draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=3)
    y += 16
    lineas_centradas((ticket.cliente_nombre or "CLIENTE").upper(), f_cursiva, 38)
    lineas_centradas(f"DOM: {ticket.cliente_domicilio}".upper(), f_bold, 34)
    if ticket.cliente_referencia:
        lineas_centradas(f"REFERENCIA: {ticket.cliente_referencia}".upper(), f_normal, 32)
    contacto_nombre, contacto_telefono = _contacto_pedido(ticket)
    telefono = contacto_telefono or ticket.cliente_telefono
    if telefono:
        lineas_centradas(f"TEL: {telefono}", f_normal, 32)
    if contacto_nombre:
        lineas_centradas(f"CONTACTO: {contacto_nombre}".upper(), f_normal, 32)
    y += 16
    lineas_centradas(_texto_entrega(ticket, incluir_toma=False).upper(), f_bold, 36)

    y += 8
    draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=3)
    y += 16
    local = timezone.localtime(ticket.creado_en)
    draw.text((MARGEN, y), _identificador_ticket(ticket), font=f_bold, fill=0)
    _derecha(draw, y, local.strftime("%d/%m/%Y %I:%M %p").lower(), f_bold)
    y += 54
    draw.text((MARGEN, y), "Concepto", font=f_titulo, fill=0)
    draw.text((365, y), "Cant", font=f_titulo, fill=0)
    _derecha(draw, y, "Importe", f_titulo)
    y += 38
    draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=2)
    y += 18

    for partida in partidas:
        lineas = _ajustar(draw, partida["nombre"].upper(), f_bold, 325)
        draw.text((MARGEN, y), lineas[0], font=f_bold, fill=0)
        draw.text((372, y), _cantidad_matriz(partida["cantidad"]), font=f_cant, fill=0)
        _derecha(draw, y, f"${partida['precio_unitario']:,.2f}", f_normal)
        y += 31
        if lineas[1:]:
            draw.text((MARGEN, y), " ".join(lineas[1:])[:38], font=f_bold, fill=0)
        _derecha(draw, y, f"${partida['importe']:,.2f}", f_bold)
        y += 36

    draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=3)
    y += 18
    draw.text((210, y), "Total:", font=f_total, fill=0)
    _derecha(draw, y, f"${ticket.total:,.2f}", f_total)
    y += 58
    if ticket.terminal:
        _centrado(draw, y, "T E R M I N A L", f_bold)
        y += 42
    elif ticket.paga_con is not None:
        cambio = ticket.paga_con - ticket.total
        _derecha(draw, y, f"Cambio de ${ticket.paga_con:,.0f}: ${cambio:,.2f}", f_bold)
        y += 42
    draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=3)
    y += 24
    mensaje_footer = "¡Gracias Por Su Preferencia!"
    _centrado(draw, y, mensaje_footer, f_titulo)
    y = draw.textbbox((0, y), mensaje_footer, font=f_titulo)[3] + 58
    return imagen.crop((0, 0, ANCHO, min(y, imagen.height))).convert("1")


def render_cuenta(ticket):
    partidas = _agrupar_partidas_total(ticket)
    alto = 650 + len(partidas) * 96
    imagen = Image.new("L", (ANCHO, max(alto, 1050)), 255)
    draw = ImageDraw.Draw(imagen)
    f_logo = fuente(54, negrita=True)
    f_titulo = fuente(29, negrita=True)
    f_normal = fuente(23)
    f_bold = fuente(25, negrita=True)
    f_total = fuente(39, negrita=True)
    f_cant = fuente(23, cursiva=True)

    y = 0
    logo = _logo_actual()
    if logo:
        imagen.paste(logo, ((ANCHO - logo.width) // 2, y))
        y += logo.height + 18
    else:
        _centrado(draw, y, "LOS TOCAYOS", f_logo)
        y += 66
        _centrado(draw, y, "TACOS DE BARBACOA", f_titulo)
        y += 52
    version = Image.new("L", (110, 24), 255)
    ImageDraw.Draw(version).text((0, 0), "EBF3.2.0", font=fuente(18, negrita=True), fill=0)
    version = version.rotate(90, expand=True)
    imagen.paste(version, (2, max(35, y - 105)))
    for linea in ["BAVJ051126EI9", "Tel: 3631-6834 / 1542-1635"]:
        _centrado(draw, y, linea, f_normal)
        y += 30
    y += 14
    draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=3)
    y += 14
    local = timezone.localtime(ticket.creado_en)
    draw.text((MARGEN, y), _identificador_ticket(ticket), font=f_bold, fill=0)
    _derecha(draw, y, local.strftime("%d/%m/%Y %I:%M %p").lower(), f_bold)
    y += 38
    if ticket.canal == "domicilio":
        posicion = f"Domicilio: {ticket.mesa.orden}"
    elif ticket.canal == "llevar":
        posicion = f"Llevar: {ticket.mesa.orden} · {ticket.cliente_nombre}"
    elif ticket.canal == "recoger":
        posicion = f"Recoger: {ticket.mesa.orden} · {ticket.cliente_nombre}"
    else:
        posicion = f"Mesa: {ticket.mesa.nombre}"
    draw.text((MARGEN, y), posicion, font=f_bold, fill=0)
    _derecha(draw, y, f"Le atendió: {ticket.atendio.nombre if ticket.atendio else ''}", f_normal)
    y += 70
    draw.text((MARGEN, y), "Concepto", font=f_titulo, fill=0)
    draw.text((365, y), "Cant", font=f_titulo, fill=0)
    _derecha(draw, y, "Importe", f_titulo)
    y += 38
    draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=2)
    y += 18
    mods_por_persona = defaultdict(list)
    for modificador in ticket.modificadores.all():
        mods_por_persona[modificador.comensal].append(modificador.nombre.upper())
    for partida in partidas:
        lineas = _ajustar(draw, partida["nombre"].upper(), f_bold, 325)
        draw.text((MARGEN, y), lineas[0], font=f_bold, fill=0)
        draw.text((372, y), _cantidad_matriz(partida["cantidad"]), font=f_cant, fill=0)
        _derecha(draw, y, f"${partida['precio_unitario']:,.2f}", f_normal)
        y += 31
        modificadores_fila = []
        for comensal in sorted(partida["comensales"]):
            modificadores_fila.extend(mods_por_persona[comensal])
        extras = lineas[1:] + ([" - ".join(dict.fromkeys(modificadores_fila))] if modificadores_fila else [])
        if extras:
            draw.text((MARGEN, y), " ".join(extras)[:38], font=f_bold, fill=0)
        _derecha(draw, y, f"${partida['importe']:,.2f}", f_bold)
        y += 36
    draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=3)
    y += 18
    draw.text((210, y), "Total:", font=f_total, fill=0)
    _derecha(draw, y, f"${ticket.total:,.2f}", f_total)
    y += 58
    draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=3)
    y += 24
    mensaje_footer = "¡Gracias Por Su Preferencia!"
    _centrado(draw, y, mensaje_footer, f_titulo)
    y = draw.textbbox((0, y), mensaje_footer, font=f_titulo)[3] + 58
    return imagen.crop((0, 0, ANCHO, min(y, imagen.height))).convert("1")


def render_sucursal(ticket):
    """Ticket total mayorista, sin la cuadrícula de comensales."""
    partidas = list(
        ticket.partidas.select_related("producto_sucursal")
        .filter(producto_sucursal__isnull=False)
        .order_by("producto_sucursal__orden", "creada_en")
    )
    alto = 610 + len(partidas) * 82
    imagen = Image.new("L", (ANCHO, max(alto, 1050)), 255)
    draw = ImageDraw.Draw(imagen)
    f_logo = fuente(54, negrita=True)
    f_titulo = fuente(28, negrita=True)
    f_normal = fuente(23)
    f_bold = fuente(25, negrita=True)
    f_total = fuente(38, negrita=True)
    f_cantidad = fuente(23, cursiva=True)

    y = 0
    logo = _logo_actual()
    if logo:
        imagen.paste(logo, ((ANCHO - logo.width) // 2, y))
        y += logo.height + 18
    else:
        _centrado(draw, y, "LOS TOCAYOS", f_logo)
        y += 66
        _centrado(draw, y, "TACOS DE BARBACOA", f_titulo)
        y += 52
    version = Image.new("L", (110, 24), 255)
    ImageDraw.Draw(version).text((0, 0), "EBF3.2.0", font=fuente(18, negrita=True), fill=0)
    version = version.rotate(90, expand=True)
    imagen.paste(version, (2, max(35, y - 105)))
    for linea in ["BAVJ051126EI9", "Tel: 3631-6834 / 1542-1635"]:
        _centrado(draw, y, linea, f_normal)
        y += 30

    y += 14
    draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=3)
    y += 14
    local = timezone.localtime(ticket.creado_en)
    draw.text((MARGEN, y), f"Ticket {ticket.folio}", font=f_bold, fill=0)
    _derecha(draw, y, local.strftime("%d/%m/%Y %I:%M %p").lower(), f_normal)
    y += 43
    cliente_sucursal = getattr(ticket.mesa, "cliente_sucursal", None)
    nombre_sucursal = cliente_sucursal.nombre if cliente_sucursal else ticket.mesa.nombre
    draw.text((MARGEN, y), f"Sucursal: {nombre_sucursal}", font=f_bold, fill=0)
    y += 66

    draw.text((MARGEN, y), "Concepto", font=f_titulo, fill=0)
    draw.text((340, y), "Cant", font=f_titulo, fill=0)
    _derecha(draw, y, "Importe", f_titulo)
    y += 38
    draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=2)
    y += 18

    for partida in partidas:
        concepto = partida.nombre_producto.upper()
        if partida.unidad and partida.unidad != "PZA":
            concepto = f"{concepto} ({partida.unidad})"
        lineas = _ajustar(draw, concepto, f_bold, 305)
        draw.text((MARGEN, y), lineas[0], font=f_bold, fill=0)
        draw.text((340, y), f"{partida.cantidad:.3f}", font=f_cantidad, fill=0)
        _derecha(draw, y, f"${partida.precio_unitario:,.2f}", f_normal)
        y += 32
        if lineas[1:]:
            draw.text((MARGEN, y), " ".join(lineas[1:]), font=f_bold, fill=0)
        _derecha(draw, y, f"${partida.importe:,.2f}", f_bold)
        y += 39

    draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=3)
    y += 17
    draw.text((210, y), "Total:", font=f_total, fill=0)
    _derecha(draw, y, f"${ticket.total:,.2f}", f_total)
    y += 58
    draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=3)
    y += 22
    _centrado(draw, y, "¡Gracias Por Su Preferencia!", f_titulo)
    y += 62
    return imagen.crop((0, 0, ANCHO, min(y, imagen.height))).convert("1")


def render_reporte_administrativo(reporte):
    datos = reporte.datos or {}
    imagen = Image.new("L", (ANCHO, 5000), 255)
    draw = ImageDraw.Draw(imagen)
    f_titulo = fuente(31, negrita=True)
    f_subtitulo = fuente(23, negrita=True)
    f_normal = fuente(20)
    f_bold = fuente(20, negrita=True)
    f_total = fuente(28, negrita=True)
    y = 22
    logo = _logo_actual()
    if logo:
        imagen.paste(logo, ((ANCHO - logo.width) // 2, y))
        y += logo.height + 10
    _centrado(draw, y, datos.get("titulo", reporte.get_tipo_display()).upper(), f_titulo)
    y += 42
    _centrado(draw, y, reporte.sucursal.nombre.upper(), f_bold)
    y += 32
    _centrado(draw, y, timezone.localtime(reporte.creado_en).strftime("%d/%m/%Y %H:%M"), f_normal)
    y += 36
    draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=3)
    y += 18

    def fila(etiqueta, valor, *, total=False):
        nonlocal y
        font = f_total if total else f_bold
        draw.text((MARGEN, y), str(etiqueta), font=font, fill=0)
        _derecha(draw, y, str(valor), font)
        y += 43 if total else 32

    tipo = reporte.tipo
    if tipo == "liquidacion":
        _centrado(draw, y, str(datos.get("repartidor", "")).upper(), f_subtitulo)
        y += 38
        for pedido in datos.get("pedidos", []):
            draw.text((MARGEN, y), f"TICKET {pedido.get('folio')}", font=f_bold, fill=0)
            _derecha(draw, y, f"${Decimal(str(pedido.get('total', 0))):,.2f}", f_bold)
            y += 29
            forma = "TERMINAL" if pedido.get("terminal") else "EFECTIVO"
            draw.text((MARGEN, y), forma, font=f_normal, fill=0)
            y += 27
            for linea in _ajustar(draw, pedido.get("domicilio", ""), f_normal, ANCHO - 2 * MARGEN):
                draw.text((MARGEN, y), linea, font=f_normal, fill=0)
                y += 25
            draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=1)
            y += 12
        fila("PEDIDOS", datos.get("cantidad_pedidos", 0))
        fila("EFECTIVO", f"${Decimal(str(datos.get('total_efectivo', 0))):,.2f}")
        fila("TERMINAL", f"${Decimal(str(datos.get('total_terminal', 0))):,.2f}")
        fila("FONDO", f"${Decimal(str(datos.get('fondo', 0))):,.2f}")
        fila("A ENTREGAR", f"${Decimal(str(datos.get('total_a_entregar', 0))):,.2f}", total=True)
    elif tipo in {"parcial", "corte_caja"}:
        etiquetas = {
            "comedor": "COMEDOR",
            "llevar": "LLEVAR",
            "domicilio": "DOMICILIO",
            "recoger": "RECOGER",
            "sucursales": "SUCURSALES",
        }
        for canal, etiqueta in etiquetas.items():
            valor = Decimal(str(datos.get("canales", {}).get(canal, 0)))
            fila(etiqueta, f"${valor:,.2f}")
        if tipo == "corte_caja":
            y += 5
            draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=2)
            y += 15
            fila("VENTAS", f"${Decimal(str(datos.get('total_ventas', 0))):,.2f}")
            fila("ENTRADAS", f"${Decimal(str(datos.get('entradas', 0))):,.2f}")
            fila("SALIDAS", f"${Decimal(str(datos.get('salidas', 0))):,.2f}")
            movimientos = datos.get("movimientos", [])
            if movimientos:
                y += 8
                draw.text((MARGEN, y), "MOVIMIENTOS", font=f_subtitulo, fill=0)
                y += 34
                for movimiento in movimientos:
                    signo = "+" if movimiento.get("tipo") == "entrada" else "-"
                    for linea in _ajustar(draw, movimiento.get("concepto", ""), f_normal, 360):
                        draw.text((MARGEN, y), linea, font=f_normal, fill=0)
                        y += 25
                    _derecha(
                        draw,
                        y - 25,
                        f"{signo}${Decimal(str(movimiento.get('importe', 0))):,.2f}",
                        f_bold,
                    )
            fila("TOTAL CAJA", f"${Decimal(str(datos.get('total_caja', 0))):,.2f}", total=True)
        else:
            fila("TOTAL", f"${Decimal(str(datos.get('total', 0))):,.2f}", total=True)
    elif tipo == "corte_sucursal":
        _centrado(draw, y, str(datos.get("sucursal_cliente", "")).upper(), f_subtitulo)
        y += 39
        for partida in datos.get("partidas", []):
            cantidad = Decimal(str(partida.get("cantidad", 0)))
            concepto = f"{_cantidad_matriz(cantidad)}  {partida.get('nombre', '')}"
            for linea in _ajustar(draw, concepto, f_normal, 365):
                draw.text((MARGEN, y), linea, font=f_normal, fill=0)
                y += 25
            _derecha(draw, y - 25, f"${Decimal(str(partida.get('importe', 0))):,.2f}", f_bold)
            y += 6
        fila("TOTAL", f"${Decimal(str(datos.get('total', 0))):,.2f}", total=True)

    y += 8
    draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=3)
    y += 24
    _centrado(draw, y, "LOS TOCAYOS", f_bold)
    y += 46
    return imagen.crop((0, 0, ANCHO, min(y, imagen.height))).convert("1")


def guardar_png(imagen, ticket, formato, destino):
    fecha = timezone.localdate().isoformat()
    relativo = Path("impresiones") / fecha / f"ticket-{ticket.folio}-{formato}-{destino}-{datetime.now().strftime('%H%M%S%f')}.png"
    absoluto = Path(settings.MEDIA_ROOT) / relativo
    absoluto.parent.mkdir(parents=True, exist_ok=True)
    imagen.save(absoluto, format="PNG", optimize=True)
    return relativo.as_posix(), absoluto


def guardar_png_reporte(imagen, reporte, formato, destino):
    fecha = timezone.localdate().isoformat()
    relativo = Path("impresiones") / fecha / f"reporte-{reporte.id}-{formato}-{destino}-{datetime.now().strftime('%H%M%S%f')}.png"
    absoluto = Path(settings.MEDIA_ROOT) / relativo
    absoluto.parent.mkdir(parents=True, exist_ok=True)
    imagen.save(absoluto, format="PNG", optimize=True)
    return relativo.as_posix(), absoluto


def escpos_raster(imagen):
    monocromo = imagen.convert("1")
    ancho_bytes = (monocromo.width + 7) // 8
    datos = bytearray()
    pixeles = monocromo.load()
    for y in range(monocromo.height):
        for byte_x in range(ancho_bytes):
            valor = 0
            for bit in range(8):
                x = byte_x * 8 + bit
                if x < monocromo.width and pixeles[x, y] == 0:
                    valor |= 1 << (7 - bit)
            datos.append(valor)
    cabecera = b"\x1d\x76\x30\x00" + bytes(
        [ancho_bytes & 0xFF, (ancho_bytes >> 8) & 0xFF, monocromo.height & 0xFF, (monocromo.height >> 8) & 0xFF]
    )
    return b"\x1b@" + cabecera + bytes(datos) + b"\n\n\n\x1dV\x00"


def enviar_tcp(imagen, destino):
    host = settings.PRINTER_HOSTS[destino]
    with socket.create_connection((host, settings.PRINTER_PORT), timeout=settings.PRINTER_TIMEOUT) as conexion:
        conexion.sendall(escpos_raster(imagen))
