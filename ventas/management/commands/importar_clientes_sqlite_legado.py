import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from personas.identidad import normalizar_clave_sucursal
from personas.models import Sucursal
from ventas.models import (
    Cliente,
    ConsecutivoCliente,
    DomicilioCliente,
    TelefonoCliente,
)


TABLAS_REQUERIDAS = {
    "personas_sucursal": {"id", "clave"},
    "ventas_cliente": {
        "id",
        "sucursal_id",
        "clave_corta",
        "nombre",
        "nombre_normalizado",
        "notas",
        "comentarios_multiples",
        "activo",
        "creado_en",
        "actualizado_en",
    },
    "ventas_telefonocliente": {
        "id",
        "sucursal_id",
        "cliente_id",
        "numero",
        "normalizado",
        "etiqueta",
        "principal",
        "activo",
        "creado_en",
    },
    "ventas_domiciliocliente": {
        "id",
        "sucursal_id",
        "cliente_id",
        "etiqueta",
        "calle",
        "numero_exterior",
        "numero_interior",
        "colonia",
        "codigo_postal",
        "municipio",
        "referencia",
        "normalizado",
        "principal",
        "activo",
        "creado_en",
    },
    "ventas_consecutivocliente": {"sucursal_id", "ultimo"},
}


@dataclass(frozen=True)
class DatosLegado:
    clientes: tuple[dict, ...]
    telefonos: tuple[dict, ...]
    domicilios: tuple[dict, ...]
    ultimo: int


def _uuid(valor, descripcion):
    try:
        return uuid.UUID(str(valor))
    except (AttributeError, TypeError, ValueError) as exc:
        raise CommandError(f"{descripcion} no contiene un UUID valido.") from exc


def _booleano(valor, descripcion):
    if valor not in (0, 1, False, True):
        raise CommandError(f"{descripcion} no contiene un booleano valido.")
    return bool(valor)


def _fecha_hora(valor, descripcion):
    if isinstance(valor, datetime):
        resultado = valor
    else:
        resultado = parse_datetime(str(valor or ""))
    if resultado is None:
        raise CommandError(f"{descripcion} no contiene una fecha valida.")
    # Django guarda datetimes SQLite sin zona en UTC. Interpretarlos como hora
    # local alteraria silenciosamente el instante al llevarlos al destino.
    if timezone.is_naive(resultado):
        resultado = timezone.make_aware(resultado, datetime_timezone.utc)
    return resultado.astimezone(datetime_timezone.utc)


def _texto(fila, campo, descripcion):
    valor = fila[campo]
    if valor is None:
        raise CommandError(f"{descripcion} contiene un valor nulo en {campo}.")
    return str(valor)


def _columnas(conexion, tabla):
    return {fila[1] for fila in conexion.execute(f'PRAGMA table_info("{tabla}")')}


def _validar_esquema(conexion):
    tablas = {
        fila[0]
        for fila in conexion.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    for tabla, requeridas in TABLAS_REQUERIDAS.items():
        if tabla not in tablas:
            raise CommandError(f"La SQLite legado no contiene la tabla requerida {tabla}.")
        faltantes = requeridas - _columnas(conexion, tabla)
        if faltantes:
            detalle = ", ".join(sorted(faltantes))
            raise CommandError(
                f"La tabla {tabla} no contiene las columnas requeridas: {detalle}."
            )


def _comprobar_unicidad(filas, clave, descripcion):
    valores = [clave(fila) for fila in filas]
    if len(valores) != len(set(valores)):
        raise CommandError(f"La SQLite legado contiene {descripcion} duplicados.")


def _leer_legado(ruta, clave_sucursal):
    uri = ruta.resolve(strict=True).as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=10)) as origen:
            origen.row_factory = sqlite3.Row
            origen.execute("PRAGMA query_only = ON")
            integridad = [fila[0] for fila in origen.execute("PRAGMA integrity_check")]
            if integridad != ["ok"]:
                raise CommandError("La SQLite legado no supera PRAGMA integrity_check.")
            _validar_esquema(origen)

            sucursales = list(
                origen.execute(
                    "SELECT id, clave FROM personas_sucursal WHERE clave = ?",
                    (clave_sucursal,),
                )
            )
            if len(sucursales) != 1:
                raise CommandError(
                    f"La SQLite legado debe contener exactamente una sucursal {clave_sucursal}."
                )
            sucursal_origen = str(sucursales[0]["id"])

            clientes_crudos = list(
                origen.execute(
                    """
                    SELECT id, sucursal_id, clave_corta, nombre, nombre_normalizado,
                           notas, comentarios_multiples, activo, creado_en, actualizado_en
                    FROM ventas_cliente
                    WHERE sucursal_id = ?
                    ORDER BY id
                    """,
                    (sucursal_origen,),
                )
            )
            telefonos_crudos = list(
                origen.execute(
                    """
                    SELECT t.id, t.sucursal_id, t.cliente_id, t.numero, t.normalizado,
                           t.etiqueta, t.principal, t.activo, t.creado_en
                    FROM ventas_telefonocliente AS t
                    LEFT JOIN ventas_cliente AS c ON c.id = t.cliente_id
                    WHERE t.sucursal_id = ? OR c.sucursal_id = ?
                    ORDER BY t.id
                    """,
                    (sucursal_origen, sucursal_origen),
                )
            )
            domicilios_crudos = list(
                origen.execute(
                    """
                    SELECT d.id, d.sucursal_id, d.cliente_id, d.etiqueta, d.calle,
                           d.numero_exterior, d.numero_interior, d.colonia,
                           d.codigo_postal, d.municipio, d.referencia, d.normalizado,
                           d.principal, d.activo, d.creado_en
                    FROM ventas_domiciliocliente AS d
                    LEFT JOIN ventas_cliente AS c ON c.id = d.cliente_id
                    WHERE d.sucursal_id = ? OR c.sucursal_id = ?
                    ORDER BY d.id
                    """,
                    (sucursal_origen, sucursal_origen),
                )
            )
            consecutivos = list(
                origen.execute(
                    "SELECT ultimo FROM ventas_consecutivocliente WHERE sucursal_id = ?",
                    (sucursal_origen,),
                )
            )
    except CommandError:
        raise
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise CommandError(
            "No fue posible abrir y validar la SQLite legado en modo de solo lectura."
        ) from exc

    clientes = []
    for fila in clientes_crudos:
        clave_corta = _texto(fila, "clave_corta", "Un cliente")
        if len(clave_corta) != 6 or not clave_corta.isdigit():
            raise CommandError("Un cliente legado contiene una clave corta invalida.")
        clientes.append(
            {
                "id": _uuid(fila["id"], "Un cliente"),
                "clave_corta": clave_corta,
                "nombre": _texto(fila, "nombre", "Un cliente"),
                "nombre_normalizado": _texto(
                    fila, "nombre_normalizado", "Un cliente"
                ),
                "notas": _texto(fila, "notas", "Un cliente"),
                "comentarios_multiples": _booleano(
                    fila["comentarios_multiples"], "Un cliente"
                ),
                "activo": _booleano(fila["activo"], "Un cliente"),
                "creado_en": _fecha_hora(fila["creado_en"], "Un cliente"),
                "actualizado_en": _fecha_hora(
                    fila["actualizado_en"], "Un cliente"
                ),
            }
        )
    ids_clientes = {item["id"] for item in clientes}
    _comprobar_unicidad(clientes, lambda item: item["id"], "UUID de cliente")
    _comprobar_unicidad(
        clientes, lambda item: item["clave_corta"], "claves cortas de cliente"
    )

    telefonos = []
    for fila in telefonos_crudos:
        cliente_id = _uuid(fila["cliente_id"], "Un telefono")
        if str(fila["sucursal_id"]) != sucursal_origen or cliente_id not in ids_clientes:
            raise CommandError(
                "La SQLite legado contiene un telefono fuera de la sucursal o sin cliente valido."
            )
        telefonos.append(
            {
                "id": _uuid(fila["id"], "Un telefono"),
                "cliente_id": cliente_id,
                "numero": _texto(fila, "numero", "Un telefono"),
                "normalizado": _texto(fila, "normalizado", "Un telefono"),
                "etiqueta": _texto(fila, "etiqueta", "Un telefono"),
                "principal": _booleano(fila["principal"], "Un telefono"),
                "activo": _booleano(fila["activo"], "Un telefono"),
                "creado_en": _fecha_hora(fila["creado_en"], "Un telefono"),
            }
        )
    _comprobar_unicidad(telefonos, lambda item: item["id"], "UUID de telefono")
    _comprobar_unicidad(
        telefonos,
        lambda item: (item["cliente_id"], item["normalizado"]),
        "telefonos normalizados por cliente",
    )

    domicilios = []
    campos_domicilio = (
        "etiqueta",
        "calle",
        "numero_exterior",
        "numero_interior",
        "colonia",
        "codigo_postal",
        "municipio",
        "referencia",
        "normalizado",
    )
    for fila in domicilios_crudos:
        cliente_id = _uuid(fila["cliente_id"], "Un domicilio")
        if str(fila["sucursal_id"]) != sucursal_origen or cliente_id not in ids_clientes:
            raise CommandError(
                "La SQLite legado contiene un domicilio fuera de la sucursal o sin cliente valido."
            )
        domicilio = {
            "id": _uuid(fila["id"], "Un domicilio"),
            "cliente_id": cliente_id,
            "principal": _booleano(fila["principal"], "Un domicilio"),
            "activo": _booleano(fila["activo"], "Un domicilio"),
            "creado_en": _fecha_hora(fila["creado_en"], "Un domicilio"),
        }
        domicilio.update(
            {
                campo: _texto(fila, campo, "Un domicilio")
                for campo in campos_domicilio
            }
        )
        domicilios.append(domicilio)
    _comprobar_unicidad(domicilios, lambda item: item["id"], "UUID de domicilio")

    if len(consecutivos) != 1:
        raise CommandError(
            "La SQLite legado debe contener un consecutivo de clientes para la sucursal."
        )
    ultimo = consecutivos[0]["ultimo"]
    if isinstance(ultimo, bool) or not isinstance(ultimo, int) or ultimo < 100000:
        raise CommandError("El consecutivo de clientes legado no es valido.")
    maximo_clave = max((int(item["clave_corta"]) for item in clientes), default=100000)
    if ultimo < maximo_clave:
        raise CommandError(
            "El consecutivo legado es menor que una clave corta existente."
        )
    return DatosLegado(tuple(clientes), tuple(telefonos), tuple(domicilios), ultimo)


def _canonico_cliente(item):
    return (
        str(item["id"]),
        item["clave_corta"],
        item["nombre"],
        item["nombre_normalizado"],
        item["notas"],
        bool(item["comentarios_multiples"]),
        bool(item["activo"]),
        item["creado_en"].astimezone(datetime_timezone.utc),
        item["actualizado_en"].astimezone(datetime_timezone.utc),
    )


def _canonico_telefono(item):
    return (
        str(item["id"]),
        str(item["cliente_id"]),
        item["numero"],
        item["normalizado"],
        item["etiqueta"],
        bool(item["principal"]),
        bool(item["activo"]),
        item["creado_en"].astimezone(datetime_timezone.utc),
    )


def _canonico_domicilio(item):
    return (
        str(item["id"]),
        str(item["cliente_id"]),
        item["etiqueta"],
        item["calle"],
        item["numero_exterior"],
        item["numero_interior"],
        item["colonia"],
        item["codigo_postal"],
        item["municipio"],
        item["referencia"],
        item["normalizado"],
        bool(item["principal"]),
        bool(item["activo"]),
        item["creado_en"].astimezone(datetime_timezone.utc),
    )


def _estado_destino(sucursal):
    clientes = tuple(
        {
            "id": item.id,
            "clave_corta": item.clave_corta,
            "nombre": item.nombre,
            "nombre_normalizado": item.nombre_normalizado,
            "notas": item.notas,
            "comentarios_multiples": item.comentarios_multiples,
            "activo": item.activo,
            "creado_en": item.creado_en,
            "actualizado_en": item.actualizado_en,
        }
        for item in Cliente.objects.filter(sucursal=sucursal).order_by("id")
    )
    telefonos = tuple(
        {
            "id": item.id,
            "cliente_id": item.cliente_id,
            "numero": item.numero,
            "normalizado": item.normalizado,
            "etiqueta": item.etiqueta,
            "principal": item.principal,
            "activo": item.activo,
            "creado_en": item.creado_en,
        }
        for item in TelefonoCliente.objects.filter(sucursal=sucursal).order_by("id")
    )
    domicilios = tuple(
        {
            "id": item.id,
            "cliente_id": item.cliente_id,
            "etiqueta": item.etiqueta,
            "calle": item.calle,
            "numero_exterior": item.numero_exterior,
            "numero_interior": item.numero_interior,
            "colonia": item.colonia,
            "codigo_postal": item.codigo_postal,
            "municipio": item.municipio,
            "referencia": item.referencia,
            "normalizado": item.normalizado,
            "principal": item.principal,
            "activo": item.activo,
            "creado_en": item.creado_en,
        }
        for item in DomicilioCliente.objects.filter(sucursal=sucursal).order_by("id")
    )
    return clientes, telefonos, domicilios


def _coincide(datos, estado):
    comparaciones = (
        (datos.clientes, estado[0], _canonico_cliente),
        (datos.telefonos, estado[1], _canonico_telefono),
        (datos.domicilios, estado[2], _canonico_domicilio),
    )
    return all(
        sorted(canonico(item) for item in esperado)
        == sorted(canonico(item) for item in actual)
        for esperado, actual, canonico in comparaciones
    )


def _restaurar_fechas(modelo, filas, campos):
    if not filas:
        return
    tabla = connection.ops.quote_name(modelo._meta.db_table)
    pk = modelo._meta.pk
    asignaciones = ", ".join(
        f"{connection.ops.quote_name(modelo._meta.get_field(campo).column)} = %s"
        for campo in campos
    )
    sql = (
        f"UPDATE {tabla} SET {asignaciones} "
        f"WHERE {connection.ops.quote_name(pk.column)} = %s"
    )
    parametros = []
    for fila in filas:
        valores = [
            modelo._meta.get_field(campo).get_db_prep_save(
                fila[campo], connection=connection
            )
            for campo in campos
        ]
        valores.append(pk.get_db_prep_save(fila["id"], connection=connection))
        parametros.append(valores)
    with connection.cursor() as cursor:
        cursor.executemany(sql, parametros)


class Command(BaseCommand):
    help = (
        "Migra fielmente el directorio de clientes desde otra SQLite del POS, "
        "sin copiar tickets ni ventas."
    )

    def add_arguments(self, parser):
        parser.add_argument("origen", type=Path, help="Ruta explicita de la SQLite legado.")
        parser.add_argument("--sucursal", required=True)
        parser.add_argument("--dry-run", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            ruta = options["origen"].expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise CommandError("No existe o no se puede resolver la SQLite legado.") from exc
        if not ruta.is_file():
            raise CommandError("La ruta de la SQLite legado no es un archivo.")
        if connection.vendor == "sqlite":
            destino = Path(connection.settings_dict["NAME"]).resolve()
            if ruta == destino:
                raise CommandError("La SQLite legado no puede ser la base de destino.")

        try:
            clave_sucursal = normalizar_clave_sucursal(options["sucursal"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        try:
            sucursal = Sucursal.objects.select_for_update().get(
                clave=clave_sucursal, activa=True
            )
        except Sucursal.DoesNotExist as exc:
            raise CommandError(
                "La sucursal de destino no esta aprovisionada y activa."
            ) from exc

        datos = _leer_legado(ruta, clave_sucursal)
        estado = _estado_destino(sucursal)
        contador = ConsecutivoCliente.objects.filter(sucursal=sucursal).first()
        ultimo_actual = contador.ultimo if contador else 100000

        if _coincide(datos, estado):
            if ultimo_actual < datos.ultimo:
                raise CommandError(
                    "El directorio coincide, pero su consecutivo de destino esta incompleto."
                )
            self.stdout.write(
                self.style.SUCCESS(
                    f"El destino ya coincide: {len(datos.clientes)} clientes, "
                    f"{len(datos.telefonos)} telefonos y {len(datos.domicilios)} domicilios; sin cambios."
                )
            )
            return

        if any(estado):
            raise CommandError(
                "El destino contiene un directorio parcial o distinto; no se modifico nada."
            )

        ids_clientes = [item["id"] for item in datos.clientes]
        ids_telefonos = [item["id"] for item in datos.telefonos]
        ids_domicilios = [item["id"] for item in datos.domicilios]
        if (
            Cliente.objects.filter(pk__in=ids_clientes).exclude(sucursal=sucursal).exists()
            or TelefonoCliente.objects.filter(pk__in=ids_telefonos).exclude(sucursal=sucursal).exists()
            or DomicilioCliente.objects.filter(pk__in=ids_domicilios).exclude(sucursal=sucursal).exists()
        ):
            raise CommandError(
                "Uno o mas UUID legado ya pertenecen a otra sucursal; no se modifico nada."
            )

        if options["dry_run"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Validacion correcta: se importarian {len(datos.clientes)} clientes, "
                    f"{len(datos.telefonos)} telefonos y {len(datos.domicilios)} domicilios."
                )
            )
            return

        Cliente.objects.bulk_create(
            [
                Cliente(
                    id=item["id"],
                    sucursal=sucursal,
                    clave_corta=item["clave_corta"],
                    nombre=item["nombre"],
                    nombre_normalizado=item["nombre_normalizado"],
                    notas=item["notas"],
                    comentarios_multiples=item["comentarios_multiples"],
                    activo=item["activo"],
                    creado_en=item["creado_en"],
                    actualizado_en=item["actualizado_en"],
                )
                for item in datos.clientes
            ],
            batch_size=500,
        )
        TelefonoCliente.objects.bulk_create(
            [
                TelefonoCliente(
                    id=item["id"],
                    sucursal=sucursal,
                    cliente_id=item["cliente_id"],
                    numero=item["numero"],
                    normalizado=item["normalizado"],
                    etiqueta=item["etiqueta"],
                    principal=item["principal"],
                    activo=item["activo"],
                    creado_en=item["creado_en"],
                )
                for item in datos.telefonos
            ],
            batch_size=500,
        )
        DomicilioCliente.objects.bulk_create(
            [
                DomicilioCliente(
                    id=item["id"],
                    sucursal=sucursal,
                    cliente_id=item["cliente_id"],
                    etiqueta=item["etiqueta"],
                    calle=item["calle"],
                    numero_exterior=item["numero_exterior"],
                    numero_interior=item["numero_interior"],
                    colonia=item["colonia"],
                    codigo_postal=item["codigo_postal"],
                    municipio=item["municipio"],
                    referencia=item["referencia"],
                    normalizado=item["normalizado"],
                    principal=item["principal"],
                    activo=item["activo"],
                    creado_en=item["creado_en"],
                )
                for item in datos.domicilios
            ],
            batch_size=500,
        )
        _restaurar_fechas(Cliente, datos.clientes, ("creado_en", "actualizado_en"))
        _restaurar_fechas(TelefonoCliente, datos.telefonos, ("creado_en",))
        _restaurar_fechas(DomicilioCliente, datos.domicilios, ("creado_en",))
        ConsecutivoCliente.objects.update_or_create(
            sucursal=sucursal,
            defaults={"ultimo": max(ultimo_actual, datos.ultimo)},
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Importados {len(datos.clientes)} clientes, {len(datos.telefonos)} telefonos "
                f"y {len(datos.domicilios)} domicilios; no se copiaron tickets ni ventas."
            )
        )
