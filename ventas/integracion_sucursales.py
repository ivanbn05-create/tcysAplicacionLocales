"""Puente idempotente de sólo lectura con ``tcysPedidosSucursales``.

Supabase es la fuente operativa; la SQLite hermana se conserva como respaldo de
desarrollo. Únicamente se incorporan pedidos confirmados del día local. Cada
pedido aparece abierto en el primer espacio libre de su sucursal para que el
operador pueda revisarlo e imprimirlo.
"""

import sqlite3
import threading
import time
from datetime import UTC, datetime, time as hora, timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone
import psycopg
from psycopg.rows import dict_row

from .models import Mesa, Partida, PedidoSucursalImportado, ProductoSucursal, SucursalPedido, Ticket
from .normalizacion import normalizar_texto
from .services import abrir_ticket


_bloqueo = threading.Lock()
_ultima_revision = 0.0


ORIGEN_SUPABASE = "pedidos_sucursales_supabase"
ORIGEN_SQLITE = "pedidos_sucursales_sqlite"


def _fecha_local(valor):
    if not valor:
        return None
    fecha = valor if isinstance(valor, datetime) else datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=UTC)
    return timezone.localtime(fecha).date()


def _leer_confirmados_sqlite(ruta):
    conexion = sqlite3.connect(f"file:{ruta.as_posix()}?mode=ro", uri=True, timeout=2)
    conexion.row_factory = sqlite3.Row
    try:
        pedidos = list(
            conexion.execute(
                """
                SELECT id, sucursal_cliente_id, codigo_publico, estado,
                       fecha_confirmacion
                  FROM pedidos_pedido
                 WHERE estado = 'confirmado' AND eliminado = 0
                 ORDER BY fecha_confirmacion, id
                """
            )
        )
        resultado = []
        for pedido in pedidos:
            items = list(
                conexion.execute(
                    """
                    SELECT producto_id, cantidad, precio_unitario
                      FROM pedidos_itempedido
                     WHERE pedido_id = ?
                     ORDER BY id
                    """,
                    (pedido["id"],),
                )
            )
            resultado.append((dict(pedido), [dict(item) for item in items]))
        return resultado
    finally:
        conexion.close()


def _leer_confirmados_postgres(desde, hasta):
    parametros = dict(settings.PEDIDOS_SUCURSALES_POSTGRES)
    url = settings.PEDIDOS_SUCURSALES_DATABASE_URL
    if url:
        conexion = psycopg.connect(url, connect_timeout=parametros["connect_timeout"], row_factory=dict_row)
    else:
        conexion = psycopg.connect(**parametros, row_factory=dict_row)
    try:
        conexion.execute("SET TRANSACTION READ ONLY")
        pedidos = list(
            conexion.execute(
                """
                SELECT pedido.id, pedido.sucursal_cliente_id, pedido.codigo_publico,
                       pedido.estado, pedido.fecha_confirmacion,
                       cliente.nombre AS sucursal_nombre
                  FROM pedidos_pedido AS pedido
                 JOIN pedidos_sucursalcliente AS cliente
                    ON cliente.id = pedido.sucursal_cliente_id
                 WHERE pedido.estado = 'confirmado' AND pedido.eliminado = FALSE
                   AND pedido.fecha_confirmacion >= %s
                   AND pedido.fecha_confirmacion < %s
                 ORDER BY pedido.fecha_confirmacion, pedido.id
                """,
                (desde, hasta),
            )
        )
        resultado = []
        for pedido in pedidos:
            items = list(
                conexion.execute(
                    """
                    SELECT item.producto_id, item.cantidad, item.precio_unitario,
                           producto.nombre AS producto_nombre,
                           producto.nombre_ticket AS producto_nombre_ticket
                      FROM pedidos_itempedido AS item
                      JOIN pedidos_producto AS producto
                        ON producto.id = item.producto_id
                     WHERE item.pedido_id = %s
                     ORDER BY item.id
                    """,
                    (pedido["id"],),
                )
            )
            resultado.append((dict(pedido), [dict(item) for item in items]))
        return resultado
    finally:
        conexion.close()


@transaction.atomic
def _importar_pedido(sucursal_local, pedido, items, origen):
    if PedidoSucursalImportado.objects.filter(
        sucursal=sucursal_local,
        origen__in=[ORIGEN_SUPABASE, ORIGEN_SQLITE],
        origen_id=pedido["id"],
    ).exists():
        return None
    clientes = SucursalPedido.objects.select_for_update().filter(sucursal=sucursal_local, activa=True)
    if pedido.get("sucursal_nombre"):
        cliente_sucursal = clientes.filter(nombre__iexact=pedido["sucursal_nombre"].strip()).first()
    else:
        cliente_sucursal = clientes.filter(origen_id=pedido["sucursal_cliente_id"]).first()
    if not cliente_sucursal or not items:
        return None
    posiciones = list(
        Mesa.objects.select_for_update()
        .filter(
            sucursal=sucursal_local,
            canal=Mesa.Canal.SUCURSALES,
            cliente_sucursal=cliente_sucursal,
            activa=True,
        )
        .order_by("orden")
    )
    ocupadas = set(
        Ticket.objects.filter(
            mesa__in=posiciones,
            estado__in=[Ticket.Estado.ABIERTO, Ticket.Estado.PROCESADO, Ticket.Estado.COBRAR],
        ).values_list("mesa_id", flat=True)
    )
    posicion = next((mesa for mesa in posiciones if mesa.id not in ocupadas), None)
    if not posicion:
        return None
    ticket, _ = abrir_ticket(posicion)
    productos_disponibles = list(ProductoSucursal.objects.filter(sucursal=sucursal_local, activo=True))
    productos_por_id = {producto.origen_id: producto for producto in productos_disponibles}
    productos_por_nombre = {}
    for producto in productos_disponibles:
        for nombre in [producto.nombre, producto.nombre_ticket]:
            if nombre:
                productos_por_nombre.setdefault(normalizar_texto(nombre), producto)
    for item in items:
        producto = None
        for nombre in [item.get("producto_nombre"), item.get("producto_nombre_ticket")]:
            if nombre:
                producto = productos_por_nombre.get(normalizar_texto(nombre))
                if producto:
                    break
        if not producto:
            producto = productos_por_id.get(item["producto_id"])
        if not producto:
            continue
        precio_configurado = producto.precio_actual(cliente_sucursal)
        nombre_ticket = (
            precio_configurado.nombre_ticket if precio_configurado else producto.nombre_ticket
        ) or producto.nombre
        Partida.objects.create(
            sucursal=sucursal_local,
            ticket=ticket,
            producto_sucursal=producto,
            comensal=1,
            cantidad=Decimal(str(item["cantidad"])),
            precio_unitario=Decimal(str(item["precio_unitario"])),
            cantidad_por_precio=producto.cantidad_por_precio,
            unidad=producto.unidad,
            nombre_producto=nombre_ticket,
            nombre_corto=nombre_ticket[:24],
        )
    if not ticket.partidas.exists():
        ticket.delete()
        return None
    PedidoSucursalImportado.objects.create(
        sucursal=sucursal_local,
        ticket=ticket,
        origen=origen,
        origen_id=pedido["id"],
        codigo_publico=pedido["codigo_publico"],
        estado_origen=pedido["estado"],
    )
    return ticket


def sincronizar_pedidos_confirmados(sucursal_local, forzar=False):
    global _ultima_revision
    ruta = Path(settings.PEDIDOS_SUCURSALES_DB)
    postgres_configurado = bool(
        settings.PEDIDOS_SUCURSALES_DATABASE_URL
        or settings.PEDIDOS_SUCURSALES_POSTGRES.get("password")
    )
    fuente = "Supabase" if postgres_configurado else "SQLite local"
    if not settings.PEDIDOS_SUCURSALES_AUTO_SYNC or (not postgres_configurado and not ruta.is_file()):
        return {"activa": False, "importados": 0, "mensaje": "Fuente externa no disponible."}
    ahora_local = timezone.localtime()
    if not forzar:
        inicio = hora.fromisoformat(settings.PEDIDOS_SUCURSALES_HORA_INICIO)
        fin = hora.fromisoformat(settings.PEDIDOS_SUCURSALES_HORA_FIN)
        if not inicio <= ahora_local.time().replace(tzinfo=None) <= fin:
            return {
                "activa": True,
                "importados": 0,
                "fuente": fuente,
                "mensaje": (
                    "Sincronización en pausa fuera del horario "
                    f"{settings.PEDIDOS_SUCURSALES_HORA_INICIO}–{settings.PEDIDOS_SUCURSALES_HORA_FIN}."
                ),
            }
    ahora = time.monotonic()
    if not forzar and ahora - _ultima_revision < settings.PEDIDOS_SUCURSALES_SYNC_SECONDS:
        return {"activa": True, "importados": 0, "mensaje": "Sincronización al día."}
    if not _bloqueo.acquire(blocking=False):
        return {"activa": True, "importados": 0, "mensaje": "Sincronización en curso."}
    try:
        _ultima_revision = ahora
        hoy = ahora_local.date()
        if postgres_configurado:
            origen = ORIGEN_SUPABASE
            inicio_local = timezone.make_aware(datetime.combine(hoy, hora.min))
            pedidos_confirmados = _leer_confirmados_postgres(inicio_local, inicio_local + timedelta(days=1))
        else:
            origen = ORIGEN_SQLITE
            pedidos_confirmados = _leer_confirmados_sqlite(ruta)
        importados = 0
        for pedido, items in pedidos_confirmados:
            if _fecha_local(pedido["fecha_confirmacion"]) != hoy:
                continue
            if _importar_pedido(sucursal_local, pedido, items, origen):
                importados += 1
        return {
            "activa": True,
            "importados": importados,
            "fuente": fuente,
            "mensaje": (
                f"{importados} pedido(s) nuevo(s) importado(s) desde {fuente}."
                if importados
                else f"Sin pedidos nuevos en {fuente}."
            ),
        }
    except (OSError, sqlite3.Error, psycopg.Error, ValueError) as exc:
        return {"activa": False, "importados": 0, "mensaje": f"No fue posible leer pedidos externos: {exc}"}
    finally:
        _bloqueo.release()
