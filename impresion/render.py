import os
import socket
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from PIL import Image, ImageChops, ImageDraw, ImageFont

from ventas.orden import ordenar_partidas
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


def _partidas_destino(ticket, destino):
    partidas = list(ticket.partidas.select_related("producto__categoria").all())
    if destino == "cocina":
        return [
            p
            for p in partidas
            if p.producto.categoria.nombre.lower() not in {"extras", "bebidas"}
            and (ticket.canal in {"comedor", "domicilio"} or p.producto.destino_impresion == "cocina")
        ]
    if destino == "barra":
        return [p for p in partidas if p.producto.destino_impresion == "barra"]
    return partidas


def _hora_corta(valor):
    return valor.strftime("%I:%M %p").lstrip("0").lower()


def _texto_entrega(ticket, incluir_toma=True):
    if not ticket.entrega_aproximada:
        return "Sin hora de entrega"
    entrega = _hora_corta(ticket.entrega_aproximada)
    if ticket.tipo_entrega == "programada":
        return f"Programado: {entrega}"
    if not incluir_toma:
        return f"Entrega aprox: {entrega}"
    tomada = _hora_corta(timezone.localtime(ticket.creado_en))
    return f"{tomada} - {entrega}"


def _texto_salsas(ticket):
    grupos = []
    for grupo in ticket.salsas_verduras or []:
        prefijo = str(grupo.get("prefijo", "")).strip()
        elementos = ", ".join(str(item) for item in grupo.get("elementos", []))
        if elementos:
            grupos.append(f"{prefijo} {elementos}".strip())
    return " * ".join(grupos)


def render_comanda(ticket, destino):
    partidas_destino = ordenar_partidas(_partidas_destino(ticket, destino))
    promociones = [
        partida for partida in partidas_destino
        if configuracion_promocion(partida.producto) and not partida.promocion_aplicada_id
    ]
    partidas = [partida for partida in partidas_destino if not configuracion_promocion(partida.producto)]
    bebidas = (
        list(ticket.partidas.select_related("producto__categoria").filter(producto__categoria__nombre__iexact="Bebidas"))
        if destino == "cocina" and ticket.canal in {"comedor", "domicilio"}
        else []
    )
    modificadores = list(ticket.modificadores.all())
    ultimo_comensal = max(
        [p.comensal for p in partidas + promociones] + [m.comensal for m in modificadores] + [1]
    )
    bloques = max(1, min(4, (ultimo_comensal + 5) // 6))
    bebidas_agrupadas = defaultdict(Decimal)
    for bebida in ordenar_partidas(bebidas):
        bebidas_agrupadas[bebida.nombre_corto] += bebida.cantidad
    contacto_pedido = " ".join(
        parte for parte in [ticket.contacto_pedido_nombre, ticket.contacto_pedido_telefono] if parte
    )
    imagen = Image.new("L", (ANCHO, 6000), 255)
    draw = ImageDraw.Draw(imagen)
    f_titulo = fuente(30, cursiva=True)
    f_normal = fuente(26)
    f_bold = fuente(28, negrita=True)
    f_total = f_bold
    f_tabla = fuente(25)
    f_tabla_bold = fuente(25, negrita=True)
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
    if ticket.canal == "domicilio":
        draw.text((MARGEN, y), local.strftime("%d/%m/%Y"), font=f_normal, fill=0)
        _derecha(draw, y, f"Ticket: {ticket.folio}", f_bold)
        y += 35
        draw.text((MARGEN, y), _texto_entrega(ticket), font=f_normal, fill=0)
        _derecha(draw, y, f"${ticket.total:,.2f}", f_total)
        y += 43
    elif ticket.canal == "comedor":
        draw.text((MARGEN, y), f"{local.strftime('%d/%m/%Y')} {_hora_corta(local)}", font=f_normal, fill=0)
        y += 34
        draw.text((MARGEN, y), f"Ticket: {ticket.folio}", font=f_bold, fill=0)
        _derecha(draw, y, ticket.mesa.nombre, f_bold)
        y += 36
        _derecha(draw, y, f"${ticket.total:,.2f}", f_total)
        y += 42
    else:
        draw.text((MARGEN, y), f"{local.strftime('%d/%m/%Y')} {_hora_corta(local)}", font=f_normal, fill=0)
        _derecha(draw, y, f"Ticket: {ticket.folio}", f_bold)
        y += 36
        _derecha(draw, y, f"${ticket.total:,.2f}", f_total)
        y += 42
    if destino != "cocina":
        _centrado(draw, y, destino.upper(), f_chico)
        y += 32

    agrupadas = {}
    for partida in partidas:
        clave = (partida.producto_id, partida.termino)
        if clave not in agrupadas:
            agrupadas[clave] = {"nombre": partida.nombre_corto, "cantidades": defaultdict(Decimal), "promocion": False}
        agrupadas[clave]["cantidades"][partida.comensal] += partida.cantidad
    promociones_agrupadas = {}
    for promocion in promociones:
        if promocion.producto_id not in promociones_agrupadas:
            promociones_agrupadas[promocion.producto_id] = {
                "nombre": promocion.producto.codigo,
                "cantidades": defaultdict(Decimal),
                "promocion": True,
            }
        promociones_agrupadas[promocion.producto_id]["cantidades"][promocion.comensal] += promocion.cantidad
    mods_por_persona = defaultdict(list)
    for modificador in modificadores:
        mods_por_persona[modificador.comensal].append(modificador.codigo)

    for bloque in range(bloques):
        inicio = bloque * 6 + 1
        personas = list(range(inicio, inicio + 6))
        etiqueta = 132
        celda = (ANCHO - MARGEN * 2 - etiqueta) / 6
        arriba = y
        draw.line((MARGEN, arriba, ANCHO - MARGEN, arriba), fill=0, width=4)
        x0 = MARGEN + etiqueta
        for indice, persona in enumerate(personas):
            x = x0 + celda * indice
            draw.rectangle((x, arriba, x + celda, arriba + 44), outline=0, width=3)
            texto = str(persona)
            draw.text((x + (celda - _ancho(draw, texto, f_tabla_bold)) / 2, arriba + 8), texto, font=f_tabla_bold, fill=0)
        y = arriba + 44
        comentarios_generales = ticket.comentarios_generales or []
        if comentarios_generales:
            texto_general = " · ".join(item.get("nombre", "") for item in comentarios_generales).upper()
            lineas_general = _ajustar(draw, texto_general, f_tabla_bold, ANCHO - MARGEN * 2 - 12)
            alto_comentario = max(42, len(lineas_general) * 30 + 8)
            draw.rectangle((MARGEN, y, ANCHO - MARGEN, y + alto_comentario), outline=0, width=3)
            linea_y = y + 6
            for linea in lineas_general:
                _centrado(draw, linea_y, linea, f_tabla_bold)
                linea_y += 30
            y += alto_comentario
        else:
            hay_comentarios = any(mods_por_persona.get(persona) for persona in personas)
            if hay_comentarios:
                alto_comentario = 34
                draw.line((MARGEN, y + alto_comentario, ANCHO - MARGEN, y + alto_comentario), fill=0, width=3)
                for indice, persona in enumerate(personas):
                    x = x0 + celda * indice
                    draw.line((x, y, x, y + alto_comentario), fill=0, width=3)
                    mods = " ".join(mods_por_persona.get(persona, []))
                    for linea in _ajustar(draw, mods, f_chico, celda - 8)[:1]:
                        draw.text((x + 4, y + 4), linea, font=f_chico, fill=0)
                draw.line((ANCHO - MARGEN, y, ANCHO - MARGEN, y + alto_comentario), fill=0, width=3)
                y += alto_comentario
            else:
                draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=3)

        for fila in [*promociones_agrupadas.values(), *agrupadas.values()]:
            draw.text((MARGEN, y + 6), fila["nombre"], font=f_tabla, fill=0)
            for indice, persona in enumerate(personas):
                x = x0 + celda * indice
                draw.line((x, y, x, y + 42), fill=0, width=3)
                valor = fila["cantidades"].get(persona)
                if valor:
                    texto = "P" if fila["promocion"] and valor == 1 else (
                        f"{_cantidad_matriz(valor)}P" if fila["promocion"] else _cantidad_matriz(valor)
                    )
                    draw.text((x + (celda - _ancho(draw, texto, f_tabla_bold)) / 2, y + 6), texto, font=f_tabla_bold, fill=0)
            draw.line((ANCHO - MARGEN, y, ANCHO - MARGEN, y + 42), fill=0, width=3)
            draw.line((MARGEN, y + 42, ANCHO - MARGEN, y + 42), fill=0, width=3)
            y += 42
        y += 16

    if contacto_pedido:
        draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=2)
        y += 14
        for linea in _ajustar(draw, f"CONTACTO: {contacto_pedido}".upper(), f_bold, ANCHO - MARGEN * 2):
            _centrado(draw, y, linea, f_bold)
            y += 36

    if ticket.comentario_general and not ticket.comentarios_generales:
        draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=2)
        y += 16
        for linea in _ajustar(draw, ticket.comentario_general.upper(), f_bold, ANCHO - MARGEN * 2):
            _centrado(draw, y, linea, f_bold)
            y += 36

    if bebidas_agrupadas:
        draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=2)
        y += 12
        segmentos = _segmentos_bebidas(bebidas_agrupadas, f_bebidas, f_bebidas)
        y = _dibujar_segmentos(draw, y, segmentos, ANCHO - MARGEN * 2, alto_linea=38)
        y += 8

    salsas = _texto_salsas(ticket)
    if salsas:
        draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=2)
        y += 12
        for linea in _ajustar(draw, salsas.upper(), f_bold, ANCHO - MARGEN * 2):
            _centrado(draw, y, linea, f_bold)
            y += 36
    return imagen.crop((0, 0, ANCHO, min(y + 38, imagen.height))).convert("1")


def render_domicilio(ticket):
    """Ticket para el repartidor con cliente, entrega, conceptos e importe a cobrar."""
    partidas = [
        partida
        for partida in ordenar_partidas(ticket.partidas.select_related("producto__categoria").all())
        if not partida.promocion_aplicada_id
    ]
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
    contacto = " ".join(
        parte for parte in [ticket.contacto_pedido_nombre, ticket.contacto_pedido_telefono] if parte
    )
    telefono = ticket.contacto_pedido_telefono or ticket.cliente_telefono
    if telefono:
        lineas_centradas(f"TEL: {telefono}", f_normal, 32)
    if ticket.contacto_pedido_nombre:
        lineas_centradas(f"CONTACTO: {ticket.contacto_pedido_nombre}".upper(), f_normal, 32)
    y += 16
    lineas_centradas(_texto_entrega(ticket, incluir_toma=False).upper(), f_bold, 36)

    y += 8
    draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=3)
    y += 16
    local = timezone.localtime(ticket.creado_en)
    draw.text((MARGEN, y), f"Ticket: {ticket.folio}", font=f_bold, fill=0)
    _derecha(draw, y, local.strftime("%d/%m/%Y %I:%M %p").lower(), f_bold)
    y += 54
    draw.text((MARGEN, y), "Concepto", font=f_titulo, fill=0)
    draw.text((365, y), "Cant", font=f_titulo, fill=0)
    _derecha(draw, y, "Importe", f_titulo)
    y += 38
    draw.line((MARGEN, y, ANCHO - MARGEN, y), fill=0, width=2)
    y += 18

    for partida in partidas:
        lineas = _ajustar(draw, partida.nombre_producto.upper(), f_bold, 325)
        draw.text((MARGEN, y), lineas[0], font=f_bold, fill=0)
        draw.text((372, y), _cantidad_matriz(partida.cantidad), font=f_cant, fill=0)
        _derecha(draw, y, f"${partida.precio_unitario:,.2f}", f_normal)
        y += 34
        if lineas[1:]:
            draw.text((MARGEN, y), " ".join(lineas[1:])[:38], font=f_bold, fill=0)
        _derecha(draw, y, f"${partida.importe:,.2f}", f_bold)
        y += 52

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
    partidas = [
        partida
        for partida in ordenar_partidas(ticket.partidas.select_related("producto__categoria").all())
        if not partida.promocion_aplicada_id
    ]
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
    draw.text((MARGEN, y), f"Ticket: {ticket.folio}", font=f_bold, fill=0)
    _derecha(draw, y, local.strftime("%d/%m/%Y %I:%M %p").lower(), f_bold)
    y += 38
    draw.text((MARGEN, y), f"Mesa: {ticket.mesa.nombre}", font=f_bold, fill=0)
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
        lineas = _ajustar(draw, partida.nombre_producto.upper(), f_bold, 325)
        draw.text((MARGEN, y), lineas[0], font=f_bold, fill=0)
        draw.text((372, y), f"{partida.cantidad:.3f}", font=f_cant, fill=0)
        _derecha(draw, y, f"${partida.precio_unitario:,.2f}", f_normal)
        y += 34
        extras = lineas[1:] + ([" - ".join(mods_por_persona[partida.comensal])] if mods_por_persona[partida.comensal] else [])
        if extras:
            draw.text((MARGEN, y), " ".join(extras)[:38], font=f_bold, fill=0)
        _derecha(draw, y, f"${partida.importe:,.2f}", f_bold)
        y += 52
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


def guardar_png(imagen, ticket, formato, destino):
    fecha = timezone.localdate().isoformat()
    relativo = Path("impresiones") / fecha / f"ticket-{ticket.folio}-{formato}-{destino}-{datetime.now().strftime('%H%M%S%f')}.png"
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
