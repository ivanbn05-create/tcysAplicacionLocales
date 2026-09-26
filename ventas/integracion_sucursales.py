"""Puente idempotente de sólo lectura con ``tcysPedidosSucursales``.

Supabase es la fuente operativa; la SQLite hermana se conserva como respaldo de
desarrollo. Únicamente se incorporan pedidos confirmados de hoy o ayer. Cada
pedido aparece abierto en el primer espacio libre de su sucursal para que el
operador pueda revisarlo e imprimirlo.
"""

import logging
import sqlite3
import threading
import time
import uuid
from datetime import UTC, datetime, time as hora, timedelta
from decimal import Decimal, DecimalException, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone
import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from .models import (
    EstadoSincronizacionPedidos,
    Mesa,
    Partida,
    PedidoSucursalImportado,
    ProductoSucursal,
    SucursalPedido,
    Ticket,
)
from .normalizacion import normalizar_texto
from .pedidos_api_v2 import (
    ClientePedidosV2,
    ErrorAlcancePedidos,
    ErrorAutenticacionPedidos,
    ErrorBrechaRetencion,
    ErrorContratoPedidos,
    ErrorEndpointPedidos,
    ErrorLimitePedidos,
    ErrorTLSPedidos,
    ErrorTemporalPedidos,
    ErrorTransportePedidos,
)
from .services import abrir_ticket


logger = logging.getLogger(__name__)
_bloqueo = threading.Lock()
_ultima_revision = 0.0


ORIGEN_SUPABASE = "pedidos_sucursales_supabase"
ORIGEN_SQLITE = "pedidos_sucursales_sqlite"
ORIGEN_API_V2 = "pedidos_sucursales_api_v2"

CANTIDAD_MINIMA = Decimal("0.001")
CANTIDAD_MAXIMA = Decimal("999.999")
PRECIO_MINIMO = Decimal("0.00")
PRECIO_MAXIMO = Decimal("99999999.99")
MAX_ITEMS_POR_PEDIDO = 100
ROL_POSTGRES_PERMITIDO = "pos_local_reader"
TABLAS_SUPABASE_PERMITIDAS = {
    "pedidos_pedido": {
        "id", "sucursal_cliente_id", "codigo_publico", "estado", "fecha_confirmacion", "eliminado"
    },
    "pedidos_sucursalcliente": {"id", "nombre"},
    "pedidos_itempedido": {"id", "pedido_id", "producto_id", "cantidad", "precio_unitario"},
    "pedidos_producto": {"id", "nombre", "nombre_ticket"},
}


class PedidoRemotoInvalido(ValueError):
    """El pedido remoto no cumple las reglas de integridad del POS."""


class PedidoRemotoSinCapacidad(RuntimeError):
    """La pagina valida no puede confirmarse hasta liberar una posicion local."""


class PedidoRemotoRequiereConciliacion(PedidoRemotoInvalido):
    """Una discontinuidad de identidad/checkpoint exige revision supervisada."""


def _nombre_normalizado(valor):
    texto = str(valor or "").strip()
    if not texto or len(texto) > 200:
        return ""
    return normalizar_texto(texto)


def _resolver_sucursal_remota(clientes, origen_id, nombre_remoto, *, identidad_estricta=False):
    """Resuelve por identidad estable aunque el catálogo remoto reordene sus IDs."""

    nombre = _nombre_normalizado(nombre_remoto)
    candidato = next((cliente for cliente in clientes if cliente.origen_id == origen_id), None)
    if identidad_estricta:
        if candidato and candidato.identidad_confirmada_en is not None:
            return candidato
        raise PedidoRemotoInvalido(
            "La identidad remota no tiene una correspondencia POS confirmada."
        )
    if candidato and (not nombre or _nombre_normalizado(candidato.nombre) == nombre):
        return candidato
    if nombre:
        coincidencias = [cliente for cliente in clientes if _nombre_normalizado(cliente.nombre) == nombre]
        if len(coincidencias) == 1:
            return coincidencias[0]
    raise PedidoRemotoInvalido("La sucursal remota no está autorizada en este POS.")


def _resolver_producto_remoto(productos, origen_id, nombre_remoto, nombre_ticket_remoto, *, identidad_estricta=False):
    """Prefiere el ID, pero verifica y recupera por nombre ante un reordenamiento."""

    nombre = _nombre_normalizado(nombre_remoto)
    nombre_ticket = _nombre_normalizado(nombre_ticket_remoto)
    def identidades_locales(producto):
        return {
            valor
            for valor in (
                _nombre_normalizado(producto.nombre),
                _nombre_normalizado(producto.nombre_ticket),
            )
            if valor
        }

    candidato = next((producto for producto in productos if producto.origen_id == origen_id), None)
    if identidad_estricta:
        if candidato:
            return candidato
        raise PedidoRemotoInvalido("El producto remoto no tiene un mapeo local explicito.")
    if candidato:
        if nombre and _nombre_normalizado(candidato.nombre) == nombre:
            return candidato
        if not nombre and (
            not nombre_ticket
            or nombre_ticket in identidades_locales(candidato)
        ):
            return candidato

    # El nombre completo es la identidad más segura. El nombre corto del ticket
    # sólo se usa como respaldo cuando la fuente no entrega el nombre completo.
    if nombre:
        coincidencias = [
            producto for producto in productos
            if _nombre_normalizado(producto.nombre) == nombre
        ]
    elif nombre_ticket:
        coincidencias = [
            producto for producto in productos
            if nombre_ticket in identidades_locales(producto)
        ]
    else:
        coincidencias = []
    if len(coincidencias) == 1:
        return coincidencias[0]
    raise PedidoRemotoInvalido("El pedido contiene un producto no autorizado.")


def _fecha_local(valor):
    if not valor:
        return None
    fecha = valor if isinstance(valor, datetime) else datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=UTC)
    return timezone.localtime(fecha).date()


def _decimal_remoto(valor, *, nombre, minimo, maximo, decimales):
    try:
        numero = Decimal(str(valor))
        cuantizado = numero.quantize(Decimal(1).scaleb(-decimales))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PedidoRemotoInvalido(f"{nombre} no es un decimal válido.") from exc
    if not numero.is_finite():
        raise PedidoRemotoInvalido(f"{nombre} debe ser finito.")
    if numero < minimo or numero > maximo:
        raise PedidoRemotoInvalido(f"{nombre} está fuera del rango permitido.")
    if numero != cuantizado:
        raise PedidoRemotoInvalido(f"{nombre} tiene demasiados decimales.")
    return cuantizado


def _parametros_conexion_postgres():
    """Normaliza el DSN y bloquea credenciales privilegiadas o TLS débil."""

    url = settings.PEDIDOS_SUCURSALES_DATABASE_URL
    try:
        parametros = conninfo_to_dict(url) if url else dict(settings.PEDIDOS_SUCURSALES_POSTGRES)
    except psycopg.Error as exc:
        raise ValueError("La configuración de Supabase no es válida.") from exc

    usuario = str(parametros.get("user", "")).strip()
    password = str(parametros.get("password", ""))
    host = str(parametros.get("host", "")).strip().rstrip(".").lower()
    dbname = str(parametros.get("dbname", "")).strip()
    rol = usuario.split(".", 1)[0].lower()
    if not host or not dbname or not usuario or not password:
        raise ValueError("Supabase requiere host, base, usuario y contraseña dedicados.")
    if not (host.endswith(".supabase.com") or host.endswith(".supabase.co")):
        raise ValueError("El host configurado no pertenece a Supabase.")
    if rol != ROL_POSTGRES_PERMITIDO:
        raise ValueError(
            "La integración de Supabase debe usar exclusivamente el rol lector "
            "dedicado pos_local_reader."
        )

    sslmode = str(parametros.get("sslmode", "prefer")).lower()
    if sslmode != "verify-full":
        raise ValueError("Supabase requiere sslmode=verify-full.")
    sslrootcert = str(parametros.get("sslrootcert", "")).strip()
    if not sslrootcert:
        raise ValueError("Supabase requiere el certificado CA en sslrootcert.")
    ruta_ca = Path(sslrootcert)
    if not ruta_ca.is_absolute():
        ruta_ca = Path(settings.BASE_DIR) / ruta_ca
    try:
        ruta_ca = ruta_ca.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("No se encontró el certificado CA configurado para Supabase.") from exc
    if not ruta_ca.is_file():
        raise ValueError("El certificado CA configurado para Supabase no es un archivo.")
    for directorio_mutable in ("runtime", "media", "logs"):
        if ruta_ca.is_relative_to((Path(settings.BASE_DIR) / directorio_mutable).resolve()):
            raise ValueError("El certificado CA debe estar fuera de directorios modificables por el servicio.")
    parametros["sslrootcert"] = str(ruta_ca)

    try:
        connect_timeout = int(parametros.get("connect_timeout", 8))
        port = int(parametros.get("port", 5432))
    except (TypeError, ValueError) as exc:
        raise ValueError("El puerto o tiempo de conexión de Supabase no es válido.") from exc
    if not 1 <= connect_timeout <= 30 or not 1 <= port <= 65535:
        raise ValueError("El puerto o tiempo de conexión de Supabase está fuera de rango.")
    parametros["connect_timeout"] = connect_timeout
    parametros["port"] = port
    parametros["application_name"] = "tocayos_pos_sync"
    return parametros


def _comprobar_rol_y_tls(conexion, usuario_configurado):
    # Psycopg 3.3 expone el estado TLS en PGconn; versiones anteriores lo
    # publicaban también a través de ConnectionInfo. Se conserva el fallback
    # para que instalaciones ya desplegadas no dependan de una versión exacta.
    pgconn = getattr(conexion, "pgconn", None)
    ssl_in_use = getattr(pgconn, "ssl_in_use", None)
    if ssl_in_use is None:
        ssl_in_use = getattr(conexion.info, "ssl_in_use", False)
    if not ssl_in_use:
        raise ValueError("La conexión de Supabase no negoció TLS.")
    rol_esperado = usuario_configurado.split(".", 1)[0].lower()
    privilegios = conexion.execute(
        """
        SELECT rolname, rolcanlogin, rolinherit, rolconnlimit, rolconfig,
               rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls
          FROM pg_roles
         WHERE rolname = current_user
        """
    ).fetchone()
    if not privilegios or privilegios["rolname"].lower() != rol_esperado:
        raise ValueError("El rol efectivo de Supabase no coincide con el rol configurado.")
    if not privilegios["rolcanlogin"] or privilegios["rolinherit"] or any(
        privilegios[campo]
        for campo in ("rolsuper", "rolcreaterole", "rolcreatedb", "rolreplication", "rolbypassrls")
    ):
        raise ValueError("El rol de Supabase tiene privilegios incompatibles con sólo lectura.")
    configuracion_rol = set(privilegios["rolconfig"] or [])
    requeridas = {
        "default_transaction_read_only=on",
        "statement_timeout=10s",
        "lock_timeout=3s",
        "idle_in_transaction_session_timeout=10s",
    }
    if not 1 <= privilegios["rolconnlimit"] <= 5 or not requeridas.issubset(configuracion_rol):
        raise ValueError("El rol de Supabase no conserva los límites operativos requeridos.")
    if conexion.execute(
        """
        SELECT EXISTS (
            SELECT 1
              FROM pg_auth_members membresia
             WHERE membresia.member = (SELECT oid FROM pg_roles WHERE rolname = current_user)
        ) AS tiene_membresias
        """
    ).fetchone()["tiene_membresias"]:
        raise ValueError("El rol de Supabase no debe heredar ni poder asumir otros roles.")
    if conexion.execute(
        """
        SELECT bool_or(
            has_table_privilege(current_user, tabla, 'INSERT')
            OR has_table_privilege(current_user, tabla, 'UPDATE')
            OR has_table_privilege(current_user, tabla, 'DELETE')
            OR has_table_privilege(current_user, tabla, 'TRUNCATE')
        ) AS puede_escribir
        FROM unnest(ARRAY[
            'public.pedidos_pedido',
            'public.pedidos_sucursalcliente',
            'public.pedidos_itempedido',
            'public.pedidos_producto'
        ]) AS tabla
        """
    ).fetchone()["puede_escribir"]:
        raise ValueError("El rol de Supabase conserva permisos de escritura.")

    if conexion.execute(
        """
        SELECT EXISTS (
            SELECT 1
              FROM pg_class objeto
              JOIN pg_namespace esquema ON esquema.oid = objeto.relnamespace
             WHERE esquema.nspname = 'public'
               AND (
                   (objeto.relkind IN ('r', 'p', 'v', 'm', 'f') AND (
                       has_table_privilege(current_user, objeto.oid, 'INSERT')
                       OR has_table_privilege(current_user, objeto.oid, 'UPDATE')
                       OR has_table_privilege(current_user, objeto.oid, 'DELETE')
                       OR has_table_privilege(current_user, objeto.oid, 'TRUNCATE')
                       OR has_table_privilege(current_user, objeto.oid, 'REFERENCES')
                       OR has_table_privilege(current_user, objeto.oid, 'TRIGGER')
                   ))
                   OR (objeto.relkind = 'S' AND (
                       has_sequence_privilege(current_user, objeto.oid, 'USAGE')
                       OR has_sequence_privilege(current_user, objeto.oid, 'UPDATE')
                   ))
               )
        ) AS puede_escribir
        """
    ).fetchone()["puede_escribir"]:
        raise ValueError("El rol de Supabase puede modificar otros objetos públicos.")

    if conexion.execute(
        "SELECT has_schema_privilege(current_user, 'public', 'CREATE') AS puede_crear"
    ).fetchone()["puede_crear"]:
        raise ValueError("El rol de Supabase puede crear objetos en el esquema público.")

    if conexion.execute(
        """
        SELECT EXISTS (
            SELECT 1
              FROM pg_class objeto
              JOIN pg_namespace esquema ON esquema.oid = objeto.relnamespace
             WHERE esquema.nspname = 'public'
               AND objeto.relkind IN ('r', 'p', 'v', 'm', 'S')
               AND objeto.relowner = (SELECT oid FROM pg_roles WHERE rolname = current_user)
        ) AS propietario
        """
    ).fetchone()["propietario"]:
        raise ValueError("El rol de Supabase es propietario de objetos públicos.")

    tablas_esperadas = {f"public.{tabla}" for tabla in TABLAS_SUPABASE_PERMITIDAS}
    accesos_tabla = conexion.execute(
        """
        SELECT format('%I.%I', esquema.nspname, objeto.relname) AS tabla
          FROM pg_class objeto
          JOIN pg_namespace esquema ON esquema.oid = objeto.relnamespace
         WHERE esquema.nspname = 'public'
           AND objeto.relkind IN ('r', 'p', 'v', 'm')
           AND (
               has_table_privilege(current_user, objeto.oid, 'SELECT')
               OR has_any_column_privilege(current_user, objeto.oid, 'SELECT')
           )
        """
    ).fetchall()
    if {fila["tabla"] for fila in accesos_tabla} != tablas_esperadas:
        raise ValueError("El rol de Supabase tiene acceso de lectura fuera del conjunto permitido.")

    politicas = conexion.execute(
        """
        SELECT objeto.relname AS tabla, objeto.relrowsecurity,
               count(politica.policyname) FILTER (
                   WHERE politica.policyname = 'pos_local_reader_select'
                     AND 'pos_local_reader' = ANY(politica.roles)
               ) AS politicas_esperadas,
               count(politica.policyname) FILTER (
                   WHERE 'public' = ANY(politica.roles)
                      OR 'pos_local_reader' = ANY(politica.roles)
               ) AS politicas_aplicables
          FROM pg_class objeto
          JOIN pg_namespace esquema ON esquema.oid = objeto.relnamespace
          LEFT JOIN pg_policies politica
            ON politica.schemaname = esquema.nspname
           AND politica.tablename = objeto.relname
         WHERE esquema.nspname = 'public'
           AND objeto.relname = ANY(%s)
         GROUP BY objeto.relname, objeto.relrowsecurity
        """,
        (list(TABLAS_SUPABASE_PERMITIDAS),),
    ).fetchall()
    if len(politicas) != len(TABLAS_SUPABASE_PERMITIDAS) or any(
        not fila["relrowsecurity"]
        or fila["politicas_esperadas"] != 1
        or fila["politicas_aplicables"] != 1
        for fila in politicas
    ):
        raise ValueError("Las políticas RLS de Supabase no coinciden con el perfil esperado.")

    columnas = conexion.execute(
        """
        SELECT tabla.table_name, tabla.column_name
          FROM information_schema.columns AS tabla
         WHERE tabla.table_schema = 'public'
           AND has_column_privilege(
               current_user,
               format('%I.%I', tabla.table_schema, tabla.table_name),
               tabla.column_name,
               'SELECT'
           )
        """
    ).fetchall()
    columnas_efectivas = {
        (fila["table_name"], fila["column_name"])
        for fila in columnas
    }
    columnas_esperadas = {
        (tabla, columna)
        for tabla, columnas_tabla in TABLAS_SUPABASE_PERMITIDAS.items()
        for columna in columnas_tabla
    }
    if columnas_efectivas != columnas_esperadas:
        raise ValueError("El rol de Supabase no tiene exactamente los permisos de columna esperados.")

    if conexion.execute(
        """
        SELECT EXISTS (
            SELECT 1
              FROM pg_proc funcion
              JOIN pg_namespace esquema ON esquema.oid = funcion.pronamespace
             WHERE esquema.nspname = 'public'
               AND funcion.prosecdef
               AND has_function_privilege(current_user, funcion.oid, 'EXECUTE')
        ) AS ejecuta_security_definer
        """
    ).fetchone()["ejecuta_security_definer"]:
        raise ValueError("El rol de Supabase puede ejecutar funciones SECURITY DEFINER.")


def _leer_confirmados_sqlite(ruta):
    conexion = sqlite3.connect(f"file:{ruta.as_posix()}?mode=ro", uri=True, timeout=2)
    conexion.row_factory = sqlite3.Row
    try:
        max_pedidos = min(max(int(getattr(settings, "PEDIDOS_SUCURSALES_MAX_PEDIDOS", 500)), 1), 2000)
        pedidos = list(
            conexion.execute(
                """
                SELECT id, sucursal_cliente_id, codigo_publico, estado,
                       fecha_confirmacion
                 FROM pedidos_pedido
                 WHERE estado = 'confirmado' AND eliminado = 0
                 ORDER BY fecha_confirmacion, id
                 LIMIT ?
                """,
                (max_pedidos,),
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
                     LIMIT ?
                    """,
                    (pedido["id"], MAX_ITEMS_POR_PEDIDO + 1),
                )
            )
            resultado.append((dict(pedido), [dict(item) for item in items]))
        return resultado
    finally:
        conexion.close()


def _leer_confirmados_postgres(desde, hasta):
    parametros = _parametros_conexion_postgres()
    conexion = psycopg.connect(**parametros, row_factory=dict_row)
    try:
        conexion.execute("SET TRANSACTION READ ONLY")
        conexion.execute("SET LOCAL statement_timeout = '10s'")
        conexion.execute("SET LOCAL lock_timeout = '3s'")
        conexion.execute("SET LOCAL idle_in_transaction_session_timeout = '10s'")
        _comprobar_rol_y_tls(conexion, str(parametros["user"]))
        max_pedidos = min(max(int(getattr(settings, "PEDIDOS_SUCURSALES_MAX_PEDIDOS", 500)), 1), 2000)
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
                 LIMIT %s
                """,
                (desde, hasta, max_pedidos),
            )
        )
        if not pedidos:
            return []
        pedidos_ids = [pedido["id"] for pedido in pedidos]
        items = list(
            conexion.execute(
                """
                SELECT item.pedido_id, item.producto_id, item.cantidad, item.precio_unitario,
                       producto.nombre AS producto_nombre,
                       producto.nombre_ticket AS producto_nombre_ticket
                  FROM (
                      SELECT pedido_id, producto_id, cantidad, precio_unitario, id,
                             row_number() OVER (PARTITION BY pedido_id ORDER BY id) AS posicion
                        FROM pedidos_itempedido
                       WHERE pedido_id = ANY(%s)
                  ) AS item
                  JOIN pedidos_producto AS producto
                    ON producto.id = item.producto_id
                 WHERE item.posicion <= %s
                 ORDER BY item.pedido_id, item.id
                """,
                (pedidos_ids, MAX_ITEMS_POR_PEDIDO + 1),
            )
        )
        items_por_pedido = {pedido_id: [] for pedido_id in pedidos_ids}
        for item in items:
            items_por_pedido.setdefault(item["pedido_id"], []).append(dict(item))
        return [
            (dict(pedido), items_por_pedido.get(pedido["id"], []))
            for pedido in pedidos
        ]
    finally:
        conexion.close()


@transaction.atomic
def _importar_pedido(sucursal_local, pedido, items, origen, *, identidad_estricta=False):
    try:
        origen_id = int(pedido["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PedidoRemotoInvalido("El pedido no tiene un identificador válido.") from exc
    if not 1 <= origen_id <= 2_147_483_647:
        raise PedidoRemotoInvalido("El identificador del pedido está fuera de rango.")
    if pedido.get("estado") != "confirmado":
        raise PedidoRemotoInvalido("El pedido remoto no está confirmado.")
    codigo_publico = str(pedido.get("codigo_publico", "")).strip()
    if not codigo_publico or len(codigo_publico) > 40:
        raise PedidoRemotoInvalido("El código público del pedido no es válido.")

    origenes_pedidos = [ORIGEN_SUPABASE, ORIGEN_SQLITE, ORIGEN_API_V2]
    if origen == ORIGEN_API_V2:
        try:
            codigo_publico = str(uuid.UUID(codigo_publico))
        except (ValueError, AttributeError, TypeError) as exc:
            raise PedidoRemotoInvalido(
                "El codigo_publico v2 no es un UUID canonico valido."
            ) from exc

        # codigo_publico es la identidad durable. Si Pedidos reasigna su ID
        # interno, el mismo UUID sigue representando el pedido ya importado.
        if PedidoSucursalImportado.objects.filter(
            sucursal=sucursal_local,
            origen__in=origenes_pedidos,
            codigo_publico=codigo_publico,
        ).exists():
            return None

        existente_id = (
            PedidoSucursalImportado.objects.select_for_update()
            .filter(
                sucursal=sucursal_local,
                origen__in=origenes_pedidos,
                origen_id=origen_id,
            )
            .order_by("importado_en")
            .first()
        )
        if existente_id is not None:
            if not existente_id.codigo_publico:
                # Registro legacy sin UUID: se conserva la deduplicacion por ID
                # y no se inventa una identidad retroactiva.
                return None
            raise PedidoRemotoRequiereConciliacion(
                "Pedidos reutilizo un ID con un codigo_publico distinto."
            )
    elif PedidoSucursalImportado.objects.filter(
        sucursal=sucursal_local,
        origen__in=origenes_pedidos,
        origen_id=origen_id,
    ).exists():
        return None

    if not items or len(items) > MAX_ITEMS_POR_PEDIDO:
        raise PedidoRemotoInvalido("El pedido no tiene una cantidad válida de conceptos.")

    clientes = list(
        SucursalPedido.objects.select_for_update().filter(sucursal=sucursal_local, activa=True)
    )
    try:
        sucursal_origen_id = int(pedido["sucursal_cliente_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PedidoRemotoInvalido("La sucursal remota no tiene un identificador válido.") from exc
    sucursal_nombre = str(pedido.get("sucursal_nombre") or "").strip()
    cliente_sucursal = _resolver_sucursal_remota(
        clientes,
        sucursal_origen_id,
        sucursal_nombre,
        identidad_estricta=identidad_estricta,
    )

    productos_disponibles = list(ProductoSucursal.objects.filter(sucursal=sucursal_local, activo=True))

    items_validados = []
    productos_usados = set()
    for item in items:
        try:
            producto_origen_id = int(item["producto_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PedidoRemotoInvalido("El producto remoto no tiene un identificador válido.") from exc
        producto = _resolver_producto_remoto(
            productos_disponibles,
            producto_origen_id,
            item.get("producto_nombre"),
            item.get("producto_nombre_ticket"),
            identidad_estricta=identidad_estricta,
        )
        if identidad_estricta:
            divisor_remoto = _decimal_remoto(
                item.get("cantidad_por_precio"),
                nombre="La cantidad por precio remota",
                minimo=CANTIDAD_MINIMA,
                maximo=CANTIDAD_MAXIMA,
                decimales=3,
            )
            if divisor_remoto != producto.cantidad_por_precio:
                raise PedidoRemotoInvalido(
                    "La unidad comercial remota no coincide con el catalogo local."
                )
            unidad_remota = str(item.get("unidad") or "").strip().casefold()
            if unidad_remota != str(producto.unidad).strip().casefold():
                raise PedidoRemotoInvalido(
                    "La unidad remota no coincide con el catalogo local."
                )
        if producto.id in productos_usados:
            raise PedidoRemotoInvalido("El pedido contiene productos duplicados.")
        productos_usados.add(producto.id)

        cantidad = _decimal_remoto(
            item.get("cantidad"),
            nombre="La cantidad remota",
            minimo=CANTIDAD_MINIMA,
            maximo=CANTIDAD_MAXIMA,
            decimales=3,
        )
        precio_remoto = _decimal_remoto(
            item.get("precio_unitario"),
            nombre="El precio remoto",
            minimo=PRECIO_MINIMO,
            maximo=PRECIO_MAXIMO,
            decimales=2,
        )
        precio_configurado = producto.precio_actual(cliente_sucursal)
        if not precio_configurado:
            raise PedidoRemotoInvalido("El producto no tiene un precio local vigente.")
        precio_local = _decimal_remoto(
            precio_configurado.importe,
            nombre="El precio local",
            minimo=PRECIO_MINIMO,
            maximo=PRECIO_MAXIMO,
            decimales=2,
        )
        if precio_remoto != precio_local:
            raise PedidoRemotoInvalido("El precio remoto no coincide con el catálogo local vigente.")
        nombre_ticket = precio_configurado.nombre_ticket or producto.nombre_ticket or producto.nombre
        items_validados.append((producto, cantidad, precio_local, nombre_ticket))

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
        if identidad_estricta:
            raise PedidoRemotoSinCapacidad(
                "No hay una posicion libre para confirmar esta pagina de Pedidos."
            )
        return None
    ticket, _ = abrir_ticket(posicion)
    for producto, cantidad, precio_local, nombre_ticket in items_validados:
        Partida.objects.create(
            sucursal=sucursal_local,
            ticket=ticket,
            producto_sucursal=producto,
            comensal=1,
            cantidad=cantidad,
            precio_unitario=precio_local,
            precio_lista_capturado=precio_local,
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
        origen_id=origen_id,
        codigo_publico=codigo_publico,
        estado_origen=pedido["estado"],
    )
    return ticket



def _marcar_estado_api_v2(
    sucursal_local,
    estado_nuevo,
    *,
    detalle,
    error=None,
    conciliacion=False,
):
    with transaction.atomic():
        estado, _ = EstadoSincronizacionPedidos.objects.select_for_update().get_or_create(
            sucursal=sucursal_local,
            defaults={"version_api": "v2"},
        )
        estado.estado = estado_nuevo
        estado.detalle_seguro = str(detalle)[:240]
        estado.ultimo_codigo_http = getattr(error, "status", None)
        estado.ultimo_request_id = str(getattr(error, "request_id", "") or "")[:64]
        if conciliacion and estado.conciliacion_requerida_en is None:
            estado.conciliacion_requerida_en = timezone.now()
        estado.save(
            update_fields=[
                "estado",
                "detalle_seguro",
                "ultimo_codigo_http",
                "ultimo_request_id",
                "conciliacion_requerida_en",
                "actualizado_en",
            ]
        )
    return estado


def _adaptar_pedido_api_v2(pedido):
    items = []
    total_calculado = Decimal("0.00")
    for item in pedido.items:
        try:
            subtotal = (
                (item.cantidad / item.producto.cantidad_por_precio)
                * item.precio_unitario
            ).quantize(Decimal("0.01"))
            total_calculado += subtotal
        except (DecimalException, OverflowError, ZeroDivisionError) as exc:
            raise PedidoRemotoInvalido(
                "Los importes remotos exceden los limites decimales del POS."
            ) from exc
        if subtotal != item.subtotal:
            raise PedidoRemotoInvalido("El subtotal remoto no coincide con cantidad y precio.")
        items.append(
            {
                "id": item.id,
                "pedido_id": item.pedido_id,
                "producto_id": item.producto.id,
                "producto_nombre": item.producto.nombre,
                "producto_nombre_ticket": item.producto.nombre_ticket,
                "unidad": item.producto.unidad_abreviatura,
                "cantidad_por_precio": item.producto.cantidad_por_precio,
                "cantidad": item.cantidad,
                "precio_unitario": item.precio_unitario,
            }
        )
    try:
        total_calculado = total_calculado.quantize(Decimal("0.01"))
        total_remoto = pedido.total.quantize(Decimal("0.01"))
    except (DecimalException, OverflowError) as exc:
        raise PedidoRemotoInvalido(
            "El total remoto excede los limites decimales del POS."
        ) from exc
    if total_calculado != total_remoto:
        raise PedidoRemotoInvalido("El total remoto no coincide con sus conceptos.")
    return (
        {
            "id": pedido.id,
            "codigo_publico": str(pedido.codigo_publico),
            "estado": "confirmado",
            "fecha_confirmacion": pedido.fecha_confirmacion,
            "sucursal_cliente_id": pedido.sucursal.id,
            "sucursal_nombre": pedido.sucursal.nombre,
            "total": pedido.total,
        },
        items,
    )

def _sincronizar_pedidos_api_v2(sucursal_local):
    ids_configurados = tuple(settings.PEDIDOS_API_SUCURSAL_IDS)
    ids_confirmados = tuple(
        SucursalPedido.objects.filter(
            sucursal=sucursal_local,
            activa=True,
            tipo=SucursalPedido.Tipo.SUCURSAL,
            identidad_confirmada_en__isnull=False,
            origen_id__in=ids_configurados,
        )
        .order_by("origen_id")
        .values_list("origen_id", flat=True)
    )
    if ids_confirmados != ids_configurados:
        _marcar_estado_api_v2(
            sucursal_local,
            EstadoSincronizacionPedidos.Estado.PAUSADO,
            detalle="Falta aprobar el mapa POS a SucursalCliente para todo el alcance configurado.",
        )
        return {
            "activa": False,
            "importados": 0,
            "requiere_identidad": True,
            "fuente": "API Pedidos v2",
            "mensaje": (
                "API v2 pausada: confirma explicitamente cada identidad "
                "POS a SucursalCliente antes de sincronizar."
            ),
        }

    horizonte = timezone.now()
    ahora_local = timezone.localtime(horizonte)
    inicio_inicial = timezone.make_aware(
        datetime.combine(ahora_local.date() - timedelta(days=1), hora.min),
        timezone.get_current_timezone(),
    )
    url = f"{settings.PEDIDOS_API_BASE_URL}{settings.PEDIDOS_API_ENDPOINT}"
    cliente = ClientePedidosV2(
        url=url,
        token=settings.PEDIDOS_API_TOKEN,
        sucursal_ids=ids_configurados,
        edge_id=settings.PEDIDOS_API_EDGE_ID if settings.PEDIDOS_API_BOUND_IDENTITY else None,
        pos_branch_id=settings.PEDIDOS_API_POS_BRANCH_ID if settings.PEDIDOS_API_BOUND_IDENTITY else None,
        ca_bundle=settings.PEDIDOS_API_CA_BUNDLE or None,
        timeout=max(
            settings.PEDIDOS_API_CONNECT_TIMEOUT_SECONDS,
            settings.PEDIDOS_API_READ_TIMEOUT_SECONDS,
        ),
        max_response_bytes=settings.PEDIDOS_API_MAX_RESPONSE_BYTES,
        max_reintentos=settings.PEDIDOS_API_MAX_RETRIES,
    )
    importados = 0
    paginas_totales = 0
    ventanas_completadas = 0
    max_ventanas = 31

    def respuesta_conciliacion(mensaje):
        return {
            "activa": False,
            "importados": importados,
            "requiere_conciliacion": True,
            "fuente": "API Pedidos v2",
            "mensaje": mensaje,
        }

    try:
        while ventanas_completadas < max_ventanas:
            with transaction.atomic():
                estado, _ = EstadoSincronizacionPedidos.objects.select_for_update().get_or_create(
                    sucursal=sucursal_local,
                    defaults={"version_api": "v2"},
                )
                if estado.estado == EstadoSincronizacionPedidos.Estado.RECONCILIACION:
                    return respuesta_conciliacion(
                        "Pedidos requiere conciliacion supervisada; el checkpoint permanece sin cambios."
                    )

                try:
                    alcance_guardado = tuple(int(valor) for valor in estado.sucursales_origen)
                except (TypeError, ValueError):
                    alcance_guardado = ()
                if estado.sucursales_origen and alcance_guardado != ids_configurados:
                    estado.estado = EstadoSincronizacionPedidos.Estado.RECONCILIACION
                    estado.detalle_seguro = "El alcance cambió respecto del checkpoint confirmado."
                    estado.conciliacion_requerida_en = timezone.now()
                    estado.save(
                        update_fields=[
                            "estado",
                            "detalle_seguro",
                            "conciliacion_requerida_en",
                            "actualizado_en",
                        ]
                    )
                    return respuesta_conciliacion(
                        "El alcance de Pedidos cambió y requiere conciliacion antes de continuar."
                    )

                cursor_inicial = estado.cursor or None
                if cursor_inicial:
                    desde = estado.ventana_desde
                    hasta = estado.ventana_hasta
                    if desde is None or hasta is None or desde >= hasta:
                        estado.estado = EstadoSincronizacionPedidos.Estado.RECONCILIACION
                        estado.detalle_seguro = "El cursor no tiene una ventana válida asociada."
                        estado.conciliacion_requerida_en = timezone.now()
                        estado.save(
                            update_fields=[
                                "estado",
                                "detalle_seguro",
                                "conciliacion_requerida_en",
                                "actualizado_en",
                            ]
                        )
                        return respuesta_conciliacion(
                            "El checkpoint de Pedidos es discontinuo y requiere conciliacion."
                        )
                    if estado.agua_alta_hasta is not None and (
                        desde > estado.agua_alta_hasta
                        or hasta <= estado.agua_alta_hasta
                    ):
                        estado.estado = EstadoSincronizacionPedidos.Estado.RECONCILIACION
                        estado.detalle_seguro = "La ventana activa no continúa el high-water mark."
                        estado.conciliacion_requerida_en = timezone.now()
                        estado.save(
                            update_fields=[
                                "estado",
                                "detalle_seguro",
                                "conciliacion_requerida_en",
                                "actualizado_en",
                            ]
                        )
                        return respuesta_conciliacion(
                            "Se detectó una discontinuidad temporal; no se avanzó el checkpoint."
                        )
                else:
                    desde = estado.agua_alta_hasta or inicio_inicial
                    if desde > horizonte:
                        estado.estado = EstadoSincronizacionPedidos.Estado.RECONCILIACION
                        estado.detalle_seguro = "El high-water mark está en el futuro."
                        estado.conciliacion_requerida_en = timezone.now()
                        estado.save(
                            update_fields=[
                                "estado",
                                "detalle_seguro",
                                "conciliacion_requerida_en",
                                "actualizado_en",
                            ]
                        )
                        return respuesta_conciliacion(
                            "El checkpoint temporal está en el futuro y requiere conciliacion."
                        )
                    if desde == horizonte:
                        break
                    hasta = min(desde + timedelta(days=1), horizonte)
                    estado.ventana_desde = desde
                    estado.ventana_hasta = hasta
                    estado.sucursales_origen = list(ids_configurados)
                    estado.ultimo_cursor_confirmado = ""

                avance_hasta = min(hasta, horizonte)
                estado.estado = EstadoSincronizacionPedidos.Estado.LISTO
                estado.detalle_seguro = ""
                estado.intentos += 1
                estado.save(
                    update_fields=[
                        "ventana_desde",
                        "ventana_hasta",
                        "sucursales_origen",
                        "ultimo_cursor_confirmado",
                        "estado",
                        "detalle_seguro",
                        "intentos",
                        "actualizado_en",
                    ]
                )

            def aplicar_pagina(pagina, checkpoint):
                nonlocal importados
                with transaction.atomic():
                    estado_pagina = EstadoSincronizacionPedidos.objects.select_for_update().get(
                        sucursal=sucursal_local
                    )
                    cursor_esperado = estado_pagina.cursor or None
                    try:
                        alcance_pagina = tuple(
                            int(valor) for valor in estado_pagina.sucursales_origen
                        )
                    except (TypeError, ValueError) as exc:
                        raise PedidoRemotoRequiereConciliacion(
                            "El alcance persistido dejó de ser válido."
                        ) from exc
                    if (
                        cursor_esperado != checkpoint.cursor_entrada
                        or estado_pagina.ventana_desde != desde
                        or estado_pagina.ventana_hasta != hasta
                        or alcance_pagina != ids_configurados
                    ):
                        raise PedidoRemotoRequiereConciliacion(
                            "El checkpoint local cambió durante la sincronizacion de Pedidos."
                        )

                    importados_pagina = 0
                    for pedido_remoto in pagina.pedidos:
                        pedido, items = _adaptar_pedido_api_v2(pedido_remoto)
                        if _importar_pedido(
                            sucursal_local,
                            pedido,
                            items,
                            ORIGEN_API_V2,
                            identidad_estricta=True,
                        ):
                            importados_pagina += 1

                    if checkpoint.cursor_siguiente:
                        estado_pagina.cursor = checkpoint.cursor_siguiente
                        estado_pagina.ultimo_cursor_confirmado = checkpoint.cursor_siguiente
                    elif checkpoint.ventana_completa:
                        estado_pagina.cursor = ""
                        if (
                            estado_pagina.agua_alta_hasta is None
                            or avance_hasta > estado_pagina.agua_alta_hasta
                        ):
                            estado_pagina.agua_alta_hasta = avance_hasta
                    else:
                        raise PedidoRemotoRequiereConciliacion(
                            "La API terminó una página sin cursor ni cierre de ventana."
                        )
                    estado_pagina.estado = EstadoSincronizacionPedidos.Estado.LISTO
                    estado_pagina.ultimo_request_id = pagina.request_id
                    estado_pagina.ultimo_codigo_http = 200
                    estado_pagina.detalle_seguro = ""
                    estado_pagina.ultima_sincronizacion_en = timezone.now()
                    estado_pagina.conciliacion_requerida_en = None
                    estado_pagina.save(
                        update_fields=[
                            "cursor",
                            "ultimo_cursor_confirmado",
                            "agua_alta_hasta",
                            "estado",
                            "ultimo_request_id",
                            "ultimo_codigo_http",
                            "detalle_seguro",
                            "ultima_sincronizacion_en",
                            "conciliacion_requerida_en",
                            "actualizado_en",
                        ]
                    )
                    importados += importados_pagina

            resultado = cliente.sincronizar_ventana(
                desde=desde,
                hasta=hasta,
                aplicar_pagina=aplicar_pagina,
                limite=settings.PEDIDOS_API_PAGE_SIZE,
                cursor_inicial=cursor_inicial,
            )
            paginas_totales += resultado.paginas

            with transaction.atomic():
                estado_confirmado = EstadoSincronizacionPedidos.objects.select_for_update().get(
                    sucursal=sucursal_local
                )
                if (
                    estado_confirmado.cursor
                    or estado_confirmado.agua_alta_hasta is None
                    or estado_confirmado.agua_alta_hasta < avance_hasta
                ):
                    raise PedidoRemotoRequiereConciliacion(
                        "La API declaró completa una ventana sin confirmar su high-water mark."
                    )
            ventanas_completadas += 1
            if estado_confirmado.agua_alta_hasta >= horizonte:
                break
    except ErrorBrechaRetencion as exc:
        _marcar_estado_api_v2(
            sucursal_local,
            EstadoSincronizacionPedidos.Estado.RECONCILIACION,
            detalle="retention_gap: se conserva el ultimo checkpoint confirmado.",
            error=exc,
            conciliacion=True,
        )
        return respuesta_conciliacion(
            "Pedidos reporto una brecha de retencion. No se avanzo el cursor; "
            "se requiere conciliacion supervisada."
        )
    except (ErrorAutenticacionPedidos, ErrorAlcancePedidos) as exc:
        _marcar_estado_api_v2(
            sucursal_local,
            EstadoSincronizacionPedidos.Estado.AUTENTICACION,
            detalle="La credencial de Pedidos fue rechazada o no cubre la sucursal.",
            error=exc,
        )
        return {
            "activa": False,
            "importados": importados,
            "fuente": "API Pedidos v2",
            "mensaje": "Sincronizacion de Pedidos suspendida: revisa su credencial y alcance.",
        }
    except (ErrorEndpointPedidos, ErrorTLSPedidos) as exc:
        _marcar_estado_api_v2(
            sucursal_local,
            EstadoSincronizacionPedidos.Estado.PAUSADO,
            detalle="Endpoint v2 no disponible o verificacion TLS fallida.",
            error=exc,
        )
        return {
            "activa": False,
            "importados": importados,
            "fuente": "API Pedidos v2",
            "mensaje": "API Pedidos v2 pausada; no se degrado a HTTP ni a otra fuente.",
        }
    except ErrorContratoPedidos as exc:
        with transaction.atomic():
            estado_actual = EstadoSincronizacionPedidos.objects.select_for_update().get(
                sucursal=sucursal_local
            )
            cursor_existente = bool(estado_actual.cursor)
        requiere_conciliacion = exc.status == 400 and cursor_existente
        _marcar_estado_api_v2(
            sucursal_local,
            (
                EstadoSincronizacionPedidos.Estado.RECONCILIACION
                if requiere_conciliacion
                else EstadoSincronizacionPedidos.Estado.CONTRATO_RECHAZADO
            ),
            detalle=(
                "Cursor antes valido rechazado; posible rotacion de firma."
                if requiere_conciliacion
                else "La pagina fue rechazada por esquema, identidad o contrato."
            ),
            error=exc,
            conciliacion=requiere_conciliacion,
        )
        return {
            "activa": False,
            "importados": importados,
            "requiere_conciliacion": requiere_conciliacion,
            "fuente": "API Pedidos v2",
            "mensaje": (
                "La respuesta de Pedidos requiere conciliacion."
                if requiere_conciliacion
                else "La respuesta de Pedidos fue rechazada sin avanzar el cursor."
            ),
        }
    except PedidoRemotoRequiereConciliacion:
        _marcar_estado_api_v2(
            sucursal_local,
            EstadoSincronizacionPedidos.Estado.RECONCILIACION,
            detalle="Discontinuidad de identidad o checkpoint; no se avanzó silenciosamente.",
            conciliacion=True,
        )
        return respuesta_conciliacion(
            "Pedidos presentó una discontinuidad de identidad o checkpoint; "
            "se requiere conciliacion supervisada."
        )
    except PedidoRemotoSinCapacidad:
        _marcar_estado_api_v2(
            sucursal_local,
            EstadoSincronizacionPedidos.Estado.ERROR_TRANSITORIO,
            detalle="Pagina valida pendiente por falta de una posicion local libre.",
        )
        return {
            "activa": True,
            "importados": importados,
            "fuente": "API Pedidos v2",
            "mensaje": "Libera una posicion de sucursal para continuar sin perder el pedido.",
        }
    except PedidoRemotoInvalido:
        _marcar_estado_api_v2(
            sucursal_local,
            EstadoSincronizacionPedidos.Estado.CONTRATO_RECHAZADO,
            detalle="La pagina no pudo aplicarse de forma integra.",
        )
        return {
            "activa": False,
            "importados": importados,
            "fuente": "API Pedidos v2",
            "mensaje": "Pagina de Pedidos rechazada; el cursor permanece sin cambios.",
        }
    except (ErrorLimitePedidos, ErrorTemporalPedidos, ErrorTransportePedidos) as exc:
        _marcar_estado_api_v2(
            sucursal_local,
            EstadoSincronizacionPedidos.Estado.ERROR_TRANSITORIO,
            detalle="Fallo transitorio de Pedidos; se reintentara con el mismo checkpoint.",
            error=exc,
        )
        return {
            "activa": True,
            "importados": importados,
            "fuente": "API Pedidos v2",
            "mensaje": "Pedidos no esta disponible; el POS continua operando localmente.",
        }

    estado_final = EstadoSincronizacionPedidos.objects.get(sucursal=sucursal_local)
    pendiente = bool(
        estado_final.agua_alta_hasta
        and estado_final.agua_alta_hasta < horizonte
    )
    return {
        "activa": True,
        "importados": importados,
        "paginas": paginas_totales,
        "ventanas": ventanas_completadas,
        "pendiente": pendiente,
        "fuente": "API Pedidos v2",
        "mensaje": (
            "Avance parcial guardado; quedan ventanas consecutivas por sincronizar."
            if pendiente
            else f"{importados} pedido(s) nuevo(s) importado(s) desde API Pedidos v2."
            if importados
            else "Sin pedidos nuevos en API Pedidos v2."
        ),
    }

def sincronizar_pedidos_confirmados(sucursal_local, forzar=False):
    global _ultima_revision
    ruta = Path(settings.PEDIDOS_SUCURSALES_DB)
    fuente_configurada = getattr(settings, "PEDIDOS_SUCURSALES_FUENTE", "desactivada")
    if not settings.PEDIDOS_SUCURSALES_AUTO_SYNC or fuente_configurada == "desactivada":
        return {"activa": False, "importados": 0, "mensaje": "Fuente externa no disponible."}
    if fuente_configurada == "supabase":
        postgres_configurado = bool(
            settings.PEDIDOS_SUCURSALES_DATABASE_URL
            or settings.PEDIDOS_SUCURSALES_POSTGRES.get("password")
        )
        if not postgres_configurado:
            return {
                "activa": False,
                "importados": 0,
                "mensaje": "La fuente Supabase no tiene credenciales dedicadas configuradas.",
            }
        fuente = "Supabase"
    elif fuente_configurada == "api_v2":
        postgres_configurado = False
        fuente = "API Pedidos v2"
    elif fuente_configurada == "sqlite" and settings.DEBUG:
        postgres_configurado = False
        fuente = "SQLite local de prueba"
        if not ruta.is_file():
            return {"activa": False, "importados": 0, "mensaje": "Fuente externa no disponible."}
    else:
        logger.warning("Se rechazó una fuente SQLite externa fuera del modo de prueba.")
        return {
            "activa": False,
            "importados": 0,
            "mensaje": "La fuente configurada no está permitida en producción.",
        }
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
        if fuente_configurada == "api_v2":
            return _sincronizar_pedidos_api_v2(sucursal_local)
        hoy = ahora_local.date()
        fechas_permitidas = {hoy - timedelta(days=1), hoy}
        if postgres_configurado:
            origen = ORIGEN_SUPABASE
            inicio_local = timezone.make_aware(datetime.combine(hoy - timedelta(days=1), hora.min))
            pedidos_confirmados = _leer_confirmados_postgres(inicio_local, inicio_local + timedelta(days=2))
        else:
            origen = ORIGEN_SQLITE
            pedidos_confirmados = _leer_confirmados_sqlite(ruta)
        importados = 0
        rechazados = 0
        for pedido, items in pedidos_confirmados:
            try:
                if _fecha_local(pedido.get("fecha_confirmacion")) not in fechas_permitidas:
                    continue
                if _importar_pedido(sucursal_local, pedido, items, origen):
                    importados += 1
            except (PedidoRemotoInvalido, InvalidOperation, KeyError, TypeError, ValueError, OverflowError) as exc:
                rechazados += 1
                logger.warning(
                    "Pedido remoto rechazado por integridad (tipo=%s).",
                    type(exc).__name__,
                )
        return {
            "activa": True,
            "importados": importados,
            "rechazados": rechazados,
            "fuente": fuente,
            "mensaje": (
                f"{importados} pedido(s) importado(s) y {rechazados} rechazado(s) desde {fuente}."
                if rechazados
                else f"{importados} pedido(s) nuevo(s) importado(s) desde {fuente}."
                if importados
                else f"Sin pedidos nuevos en {fuente}."
            ),
        }
    except (OSError, sqlite3.Error, psycopg.Error, ValueError) as exc:
        logger.warning("Fallo de lectura de la fuente externa (tipo=%s).", type(exc).__name__)
        return {
            "activa": False,
            "importados": 0,
            "rechazados": 0,
            "mensaje": "No fue posible leer pedidos externos; revisa el registro seguro del servidor.",
        }
    finally:
        _bloqueo.release()
