"""Validacion y aplicacion atomica del contrato candidato de catalogo Central v2."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from catalogo.models import (
    Categoria,
    IdentidadCategoriaCentral,
    IdentidadProductoCentral,
    Precio,
    Producto,
    PublicacionCatalogoCentral,
)
from ventas.models import EventoOutbox


CHECKSUM_RE = re.compile(r"^[0-9a-f]{64}$")
DECIMAL_RE = re.compile(r"^[0-9]{1,12}\.[0-9]{2}$")
CODIGOS_FIJOS_LEGACY_V2 = {"PB", "PL", "P4", "PK", "TB", "TBI", "LB", "LOBQ"}
MAX_CATEGORIAS = 500
MAX_PRODUCTOS = 5000
MAX_SNAPSHOT_BYTES = 1024 * 1024
# Namespace versionado: altas v3 reproducen el mismo UUID tras restaurar SQLite.
# No se aplica a mappings existentes ni a publicaciones v2.
MAPPING_V3_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, "los-tocayos-pos/catalogo-v3/mapping/v1"
)


def _id_local_v3(sucursal_id, tipo, central_id):
    if tipo not in {"categoria", "producto"}:
        raise ValueError("Tipo de identidad de catálogo desconocido.")
    return uuid.uuid5(
        MAPPING_V3_NAMESPACE, f"{sucursal_id}:{tipo}:{central_id}"
    )

CODIGOS_RECHAZO = {
    "checksum_invalido",
    "schema_no_soportado",
    "sucursal_incorrecta",
    "version_fuera_de_orden",
    "conflicto_local",
    "payload_excede_limite",
}


class ErrorCatalogoCentral(ValueError):
    def __init__(self, mensaje, *, codigo):
        super().__init__(mensaje)
        self.codigo = codigo


def _error(codigo, mensaje):
    raise ErrorCatalogoCentral(mensaje, codigo=codigo)


def _uuid(valor, ruta, *, nulo=False):
    if nulo and valor is None:
        return None
    if type(valor) is not str or len(valor) != 36:
        _error("schema_no_soportado", f"UUID invalido en {ruta}.")
    try:
        resultado = uuid.UUID(valor)
    except (ValueError, TypeError, AttributeError):
        _error("schema_no_soportado", f"UUID invalido en {ruta}.")
    if str(resultado) != valor:
        _error("schema_no_soportado", f"UUID no canonico en {ruta}.")
    return resultado


def _texto(valor, ruta, maximo, *, vacio=False):
    if type(valor) is not str or len(valor) > maximo or (not vacio and not valor.strip()):
        _error("schema_no_soportado", f"Texto invalido en {ruta}.")
    if any(ord(c) < 32 for c in valor):
        _error("schema_no_soportado", f"Texto invalido en {ruta}.")
    return valor.strip()


def _entero(valor, ruta, minimo=0, maximo=65535):
    if type(valor) is not int or not minimo <= valor <= maximo:
        _error("schema_no_soportado", f"Entero invalido en {ruta}.")
    return valor


def _booleano(valor, ruta):
    if type(valor) is not bool:
        _error("schema_no_soportado", f"Booleano invalido en {ruta}.")
    return valor


def _objeto(valor, campos, ruta):
    if type(valor) is not dict or set(valor) != set(campos):
        _error("schema_no_soportado", f"Esquema invalido en {ruta}.")
    return valor


def _fecha(valor, ruta, *, nula=False):
    if nula and valor is None:
        return None
    if type(valor) is not str or len(valor) != 10:
        _error("schema_no_soportado", f"Fecha invalida en {ruta}.")
    try:
        return date.fromisoformat(valor)
    except ValueError:
        _error("schema_no_soportado", f"Fecha invalida en {ruta}.")


def _timestamp(valor, ruta):
    if type(valor) is not str:
        _error("schema_no_soportado", f"Timestamp invalido en {ruta}.")
    try:
        instante = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        _error("schema_no_soportado", f"Timestamp invalido en {ruta}.")
    if instante.tzinfo is None or instante.utcoffset() is None:
        _error("schema_no_soportado", f"Timestamp sin zona en {ruta}.")
    return instante


def _decimal(valor, ruta):
    if type(valor) is not str or not DECIMAL_RE.fullmatch(valor):
        _error("schema_no_soportado", f"Importe invalido en {ruta}.")
    try:
        numero = Decimal(valor)
    except InvalidOperation:
        _error("schema_no_soportado", f"Importe invalido en {ruta}.")
    if numero <= 0:
        _error("schema_no_soportado", f"Importe no positivo en {ruta}.")
    return numero


def _json_canonico(datos):
    try:
        return json.dumps(
            datos,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ErrorCatalogoCentral(
            "El snapshot no puede canonizarse.",
            codigo="schema_no_soportado",
        ) from exc


def bytes_canonicos_snapshot(datos, *, excluir_checksum=True):
    """Compatibilidad para pruebas: el hash normativo cubre solo contenido."""

    if excluir_checksum and type(datos) is dict and "contenido" in datos:
        return _json_canonico(datos["contenido"])
    if excluir_checksum and type(datos) is dict:
        copia = dict(datos)
        copia.pop("contenido_sha256", None)
        copia.pop("checksum", None)
        return _json_canonico(copia)
    return _json_canonico(datos)


def checksum_snapshot(datos):
    return hashlib.sha256(bytes_canonicos_snapshot(datos)).hexdigest()


def validar_publicacion_catalogo(datos, sucursal):
    if len(_json_canonico(datos)) > MAX_SNAPSHOT_BYTES:
        _error("payload_excede_limite", "La publicacion supera 1 MiB.")
    raiz = _objeto(
        datos,
        {
            "version_contrato",
            "tipo",
            "release_id",
            "publicacion_id",
            "publicacion_anterior_id",
            "version_sucursal",
            "sucursal",
            "publicada_en",
            "aplicar_desde",
            "moneda",
            "conteos",
            "contenido",
            "contenido_sha256",
        },
        "publicacion",
    )
    if type(raiz["version_contrato"]) is not int or raiz["version_contrato"] not in {2, 3} or raiz["tipo"] != "snapshot_completo":
        _error("schema_no_soportado", "Solo se admiten snapshot_completo v2/v3.")
    contrato_v3 = raiz["version_contrato"] == 3
    release_id = _uuid(raiz["release_id"], "release_id")
    publicacion_id = _uuid(raiz["publicacion_id"], "publicacion_id")
    anterior_id = _uuid(
        raiz["publicacion_anterior_id"], "publicacion_anterior_id", nulo=True
    )
    if anterior_id == publicacion_id:
        _error("schema_no_soportado", "La publicacion anterior no puede ser la actual.")
    version = _entero(
        raiz["version_sucursal"], "version_sucursal", 1, 2_147_483_647
    )
    if (version == 1 and anterior_id is not None) or (
        version > 1 and anterior_id is None
    ):
        _error(
            "version_fuera_de_orden",
            "La cadena de publicaciones no declara un predecesor válido.",
        )
    identidad = _objeto(raiz["sucursal"], {"id", "clave"}, "sucursal")
    if _uuid(identidad["id"], "sucursal.id") != sucursal.id:
        _error("sucursal_incorrecta", "La publicacion pertenece a otra sucursal.")
    if _texto(identidad["clave"], "sucursal.clave", 30) != sucursal.clave:
        _error("sucursal_incorrecta", "La clave de sucursal no coincide.")
    _timestamp(raiz["publicada_en"], "publicada_en")
    aplicar_desde = _fecha(raiz["aplicar_desde"], "aplicar_desde")
    if raiz["moneda"] != "MXN":
        _error("schema_no_soportado", "La moneda del catalogo debe ser MXN.")

    contenido = _objeto(
        raiz["contenido"],
        {"categorias", "productos", "promociones"} if contrato_v3 else {"categorias", "productos"},
        "contenido",
    )
    categorias = contenido["categorias"]
    productos = contenido["productos"]
    promociones = contenido["promociones"] if contrato_v3 else []
    if type(categorias) is not list or not 1 <= len(categorias) <= MAX_CATEGORIAS:
        _error("schema_no_soportado", "Cantidad de categorias invalida.")
    if type(productos) is not list or not 1 <= len(productos) <= MAX_PRODUCTOS:
        _error("schema_no_soportado", "Cantidad de productos invalida.")
    conteos = _objeto(
        raiz["conteos"],
        {"categorias", "productos", "promociones"} if contrato_v3 else {"categorias", "productos"},
        "conteos",
    )
    if (
        _entero(conteos["categorias"], "conteos.categorias", 1, MAX_CATEGORIAS)
        != len(categorias)
        or _entero(conteos["productos"], "conteos.productos", 1, MAX_PRODUCTOS)
        != len(productos)
        or (contrato_v3 and _entero(conteos["promociones"], "conteos.promociones", 0, 500) != len(promociones))
    ):
        _error("schema_no_soportado", "Los conteos no coinciden con el snapshot.")

    checksum = _texto(raiz["contenido_sha256"], "contenido_sha256", 64)
    if not CHECKSUM_RE.fullmatch(checksum) or checksum_snapshot(raiz) != checksum:
        _error("checksum_invalido", "El checksum de la publicacion no coincide.")

    categorias_ids = set()
    categorias_por_central = {}
    for indice, categoria in enumerate(categorias):
        categoria = _objeto(
            categoria,
            {"categoria_central_id", "codigo", "nombre", "orden", "activa"},
            f"contenido.categorias[{indice}]",
        )
        central_id = _uuid(
            categoria["categoria_central_id"],
            f"contenido.categorias[{indice}].categoria_central_id",
        )
        if central_id in categorias_ids:
            _error("schema_no_soportado", "Categoria central duplicada.")
        categorias_ids.add(central_id)
        categorias_por_central[central_id] = categoria
        _texto(categoria["codigo"], f"contenido.categorias[{indice}].codigo", 30)
        _texto(categoria["nombre"], f"contenido.categorias[{indice}].nombre", 100)
        _entero(categoria["orden"], f"contenido.categorias[{indice}].orden")
        _booleano(categoria["activa"], f"contenido.categorias[{indice}].activa")

    productos_ids = set()
    productos_por_central = {}
    productos_vendibles = 0
    codigos = set()
    for indice, producto in enumerate(productos):
        campos_producto = {
                "producto_central_id",
                "categoria_central_id",
                "codigo",
                "nombre",
                "nombre_corto",
                "orden",
                "permite_termino",
                "termino_predeterminado",
                "abreviaturas_termino",
                "destino_impresion",
                "activo",
                "imagen",
                "precio",
            }
        if contrato_v3:
            campos_producto.add("disponible_sucursal")
        producto = _objeto(producto, campos_producto, f"contenido.productos[{indice}]")
        central_id = _uuid(
            producto["producto_central_id"],
            f"contenido.productos[{indice}].producto_central_id",
        )
        if central_id in productos_ids:
            _error("schema_no_soportado", "Producto central duplicado.")
        productos_ids.add(central_id)
        productos_por_central[central_id] = producto
        categoria_id = _uuid(
            producto["categoria_central_id"],
            f"contenido.productos[{indice}].categoria_central_id",
        )
        if categoria_id not in categorias_ids:
            _error("schema_no_soportado", "Producto con categoria inexistente.")
        codigo = _texto(producto["codigo"], f"contenido.productos[{indice}].codigo", 30)
        if codigo in codigos:
            _error("conflicto_local", "Codigo de producto duplicado en la publicacion.")
        codigos.add(codigo)
        _texto(producto["nombre"], f"contenido.productos[{indice}].nombre", 180)
        _texto(
            producto["nombre_corto"],
            f"contenido.productos[{indice}].nombre_corto",
            24,
        )
        _entero(producto["orden"], f"contenido.productos[{indice}].orden")
        permite = _booleano(
            producto["permite_termino"],
            f"contenido.productos[{indice}].permite_termino",
        )
        termino = producto["termino_predeterminado"]
        abreviaturas = producto["abreviaturas_termino"]
        if (
            type(abreviaturas) is not dict
            or len(abreviaturas) > 3
            or any(
                clave not in Producto.Termino.values
                or type(valor) is not str
                or len(valor) > 8
                or not valor
                for clave, valor in abreviaturas.items()
            )
        ):
            _error("schema_no_soportado", "Abreviaturas de termino invalidas.")
        if permite:
            if termino not in Producto.Termino.values or termino not in abreviaturas:
                _error("schema_no_soportado", "Configuracion de termino incompleta.")
        elif termino != "" or abreviaturas:
            _error("schema_no_soportado", "Producto sin termino contiene opciones.")
        if producto["destino_impresion"] not in Producto.Destino.values:
            _error("schema_no_soportado", "Destino de impresion invalido.")
        activo = _booleano(producto["activo"], f"contenido.productos[{indice}].activo")
        if contrato_v3:
            disponible = _booleano(
                producto["disponible_sucursal"],
                f"contenido.productos[{indice}].disponible_sucursal",
            )
            productos_vendibles += bool(activo and disponible)
            if activo and disponible and not categorias_por_central[categoria_id]["activa"]:
                _error("schema_no_soportado", "Un producto vendible pertenece a una categoría inactiva.")
        imagen = _objeto(
            producto["imagen"], {"politica", "asset"}, f"contenido.productos[{indice}].imagen"
        )
        if (
            imagen["politica"] != "conservar_local_o_placeholder"
            or imagen["asset"] is not None
        ):
            _error("schema_no_soportado", "Imagen no soportada en el contrato inicial.")
        precio = _objeto(
            producto["precio"],
            {"importe", "origen", "vigente_desde", "vigente_hasta"},
            f"contenido.productos[{indice}].precio",
        )
        _decimal(precio["importe"], f"contenido.productos[{indice}].precio.importe")
        if precio["origen"] not in {"global", "excepcion_sucursal"}:
            _error("schema_no_soportado", "Origen de precio invalido.")
        desde = _fecha(precio["vigente_desde"], "precio.vigente_desde")
        hasta = _fecha(precio["vigente_hasta"], "precio.vigente_hasta", nula=True)
        if (
            desde > aplicar_desde
            or (hasta is not None and hasta < desde)
            or (hasta is not None and hasta < aplicar_desde)
        ):
            _error("schema_no_soportado", "Vigencia de precio invalida al aplicar.")
    if contrato_v3:
        if productos_vendibles == 0:
            _error("schema_no_soportado", "La publicación no contiene productos vendibles para esta sucursal.")
        from .promociones import validar_promociones_publicadas

        try:
            validar_promociones_publicadas(promociones, productos_por_central)
        except ValueError as exc:
            raise ErrorCatalogoCentral(str(exc), codigo="schema_no_soportado") from exc
    return raiz, release_id, publicacion_id, anterior_id, version, checksum


def _categoria_local(sucursal, datos, existentes, *, alta_v3=False):
    central_id = uuid.UUID(datos["categoria_central_id"])
    mapeo = existentes.get(central_id)
    if mapeo:
        return mapeo.categoria
    if Categoria.objects.filter(sucursal=sucursal, nombre=datos["nombre"]).exists():
        _error(
            "conflicto_local",
            "Categoria local con el mismo nombre requiere mapeo explicito.",
        )
    identidad_local = {}
    if alta_v3:
        local_id = _id_local_v3(sucursal.id, "categoria", central_id)
        if Categoria.objects.filter(pk=local_id).exists():
            _error("conflicto_local", "UUID local de categoria v3 ya ocupado.")
        identidad_local["id"] = local_id
    categoria = Categoria.objects.create(
        **identidad_local,
        sucursal=sucursal,
        nombre=datos["nombre"],
        orden=datos["orden"],
        activa=datos["activa"],
    )
    mapeo = IdentidadCategoriaCentral.objects.create(
        sucursal=sucursal,
        central_id=central_id,
        categoria=categoria,
    )
    existentes[central_id] = mapeo
    return categoria


def _producto_local(
    sucursal, datos, categoria, existentes, *, compatibilidad_legacy_v2, alta_v3=False
):
    central_id = uuid.UUID(datos["producto_central_id"])
    mapeo = existentes.get(central_id)
    if mapeo:
        producto = mapeo.producto
        if (
            compatibilidad_legacy_v2
            and producto.codigo != datos["codigo"]
            and (
                producto.codigo in CODIGOS_FIJOS_LEGACY_V2
                or datos["codigo"] in CODIGOS_FIJOS_LEGACY_V2
            )
        ):
            _error(
                "conflicto_local",
                "Un codigo usado por promociones no puede cambiarse.",
            )
        if Producto.objects.filter(
            sucursal=sucursal,
            codigo=datos["codigo"],
        ).exclude(pk=producto.pk).exists():
            _error("conflicto_local", "El nuevo codigo ya pertenece a otro producto local.")
        return producto
    if Producto.objects.filter(sucursal=sucursal, codigo=datos["codigo"]).exists():
        _error(
            "conflicto_local",
            "Codigo local existente requiere un mapeo explicito previo.",
        )
    identidad_local = {}
    if alta_v3:
        local_id = _id_local_v3(sucursal.id, "producto", central_id)
        if Producto.objects.filter(pk=local_id).exists():
            _error("conflicto_local", "UUID local de producto v3 ya ocupado.")
        identidad_local["id"] = local_id
    producto = Producto.objects.create(
        **identidad_local,
        sucursal=sucursal,
        categoria=categoria,
        codigo=datos["codigo"],
        nombre=datos["nombre"],
        nombre_corto=datos["nombre_corto"],
        orden=datos["orden"],
        permite_termino=datos["permite_termino"],
        termino_predeterminado=datos["termino_predeterminado"],
        abreviaturas_termino=datos["abreviaturas_termino"],
        destino_impresion=datos["destino_impresion"],
        activo=datos["activo"],
        origen="central_v2",
    )
    mapeo = IdentidadProductoCentral.objects.create(
        sucursal=sucursal,
        central_id=central_id,
        producto=producto,
    )
    existentes[central_id] = mapeo
    return producto


def _mapeos_ack(sucursal, publicacion):
    contenido = publicacion.snapshot["contenido"]
    categorias_ids = {
        uuid.UUID(item["categoria_central_id"]) for item in contenido["categorias"]
    }
    productos_ids = {
        uuid.UUID(item["producto_central_id"]) for item in contenido["productos"]
    }
    categorias = [
        {
            "categoria_central_id": str(item.central_id),
            "categoria_local_id": str(item.categoria_id),
        }
        for item in IdentidadCategoriaCentral.objects.filter(
            sucursal=sucursal,
            central_id__in=categorias_ids,
        ).order_by("central_id")
    ]
    productos = [
        {
            "producto_central_id": str(item.central_id),
            "producto_local_id": str(item.producto_id),
        }
        for item in IdentidadProductoCentral.objects.filter(
            sucursal=sucursal,
            central_id__in=productos_ids,
        ).order_by("central_id")
    ]
    if (
        len(categorias) != len(categorias_ids)
        or len(productos) != len(productos_ids)
        or {uuid.UUID(item["categoria_central_id"]) for item in categorias} != categorias_ids
        or {uuid.UUID(item["producto_central_id"]) for item in productos} != productos_ids
    ):
        _error("conflicto_local", "El ACK no contiene todos los mapeos de la publicacion.")
    return categorias, productos


def _crear_ack(sucursal, publicacion):
    from .sincronizacion_central import hash_payload, obtener_pos_instance_id

    ack_id = uuid.uuid4()
    categorias, productos = _mapeos_ack(sucursal, publicacion)
    payload = {
        "version_contrato": publicacion.version_contrato,
        "ack_id": str(ack_id),
        "pos_instance_id": obtener_pos_instance_id(sucursal),
        "sucursal": {"id": str(sucursal.id), "clave": sucursal.clave},
        "release_id": str(publicacion.release_id),
        "publicacion_id": str(publicacion.publicacion_id),
        "version_sucursal": publicacion.version,
        "contenido_sha256": publicacion.checksum,
        "estado": "aplicado",
        "registrado_en": publicacion.aplicado_en.isoformat(),
        "resultado": {"codigo": "ok", "detalle": ""},
        "mapeos_categoria": categorias,
        "mapeos_producto": productos,
    }
    return EventoOutbox.objects.create(
        id=ack_id,
        sucursal=sucursal,
        agregado="catalogo_publicacion",
        agregado_id=publicacion.publicacion_id,
        tipo="catalogo.aplicado",
        datos=payload,
        destino=EventoOutbox.Destino.CENTRAL_CATALOGO_ACK,
        estado_entrega=EventoOutbox.EstadoEntrega.PENDIENTE,
        version_contrato=publicacion.version_contrato,
        version_origen=publicacion.version,
        payload_hash=hash_payload(payload),
    )


def encolar_ack_catalogo_rechazado(
    sucursal, datos, error, *, version_contrato=None
):
    """Persiste un rechazo seguro si la publicacion puede identificarse sin ambiguedad."""

    if type(datos) is not dict:
        return None
    if version_contrato is None:
        version_contrato = datos.get("version_contrato")
    if type(version_contrato) is not int or version_contrato not in {2, 3}:
        return None
    if error.codigo == "sucursal_incorrecta":
        return None
    try:
        release_id = _uuid(datos.get("release_id"), "release_id")
        publicacion_id = _uuid(datos.get("publicacion_id"), "publicacion_id")
        version = _entero(
            datos.get("version_sucursal"),
            "version_sucursal",
            1,
            2_147_483_647,
        )
        checksum = _texto(datos.get("contenido_sha256"), "contenido_sha256", 64)
        if not CHECKSUM_RE.fullmatch(checksum):
            checksum = "0" * 64
    except ErrorCatalogoCentral:
        return None

    from .sincronizacion_central import hash_payload, obtener_pos_instance_id

    codigo = error.codigo if error.codigo in CODIGOS_RECHAZO else "schema_no_soportado"
    pos_instance_id = obtener_pos_instance_id(sucursal)
    # El mismo rechazo publicado en cada poll usa la misma identidad durable.
    # Un cambio de version, release, checksum o codigo genera otro ACK.
    ack_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        (
            f"lostocayos:catalogo:rechazado:{sucursal.id}:{pos_instance_id}:"
            f"{version_contrato}:{release_id}:{publicacion_id}:{version}:"
            f"{checksum}:{codigo}"
        ),
    )
    payload = {
        "version_contrato": version_contrato,
        "ack_id": str(ack_id),
        "pos_instance_id": pos_instance_id,
        "sucursal": {"id": str(sucursal.id), "clave": sucursal.clave},
        "release_id": str(release_id),
        "publicacion_id": str(publicacion_id),
        "version_sucursal": version,
        "contenido_sha256": checksum,
        "estado": "rechazado",
        "registrado_en": timezone.now().isoformat(),
        "resultado": {"codigo": codigo, "detalle": str(error)[:200]},
        "mapeos_categoria": [],
        "mapeos_producto": [],
    }
    evento, creada = EventoOutbox.objects.get_or_create(
        id=ack_id,
        defaults={
            "sucursal": sucursal,
            "agregado": "catalogo_publicacion",
            "agregado_id": publicacion_id,
            "tipo": "catalogo.rechazado",
            "datos": payload,
            "destino": EventoOutbox.Destino.CENTRAL_CATALOGO_ACK,
            "estado_entrega": EventoOutbox.EstadoEntrega.PENDIENTE,
            "version_contrato": version_contrato,
            "version_origen": version,
            "payload_hash": hash_payload(payload),
            "ultimo_error": codigo,
        },
    )
    if not creada and (
        evento.sucursal_id != sucursal.id
        or evento.tipo != "catalogo.rechazado"
        or evento.version_contrato != version_contrato
        or evento.datos.get("publicacion_id") != str(publicacion_id)
        or evento.datos.get("contenido_sha256") != checksum
        or evento.payload_hash != hash_payload(evento.datos)
    ):
        return None
    return evento


def _validar_raices_promocionales_historicas(sucursal, raiz):
    """Una raíz retirada no vuelve a venderse como artículo simple por omisión."""

    if raiz["version_contrato"] < 3:
        return
    from ventas.models import DefinicionPromocion

    productos_historicos = set(
        DefinicionPromocion.objects.filter(sucursal=sucursal).values_list(
            "producto_id", flat=True
        )
    )
    if not productos_historicos:
        return
    principales_historicos = set(
        IdentidadProductoCentral.objects.filter(
            sucursal=sucursal,
            producto_id__in=productos_historicos,
        ).values_list("central_id", flat=True)
    )
    principales_actuales = {
        uuid.UUID(item["producto_central_id"])
        for item in raiz["contenido"]["promociones"]
    }
    for producto in raiz["contenido"]["productos"]:
        central_id = uuid.UUID(producto["producto_central_id"])
        if (
            central_id in principales_historicos
            and central_id not in principales_actuales
            and producto["activo"]
            and producto["disponible_sucursal"]
        ):
            _error(
                "schema_no_soportado",
                "Una raíz promocional retirada debe quedar no disponible en esta sucursal.",
            )


@transaction.atomic
def aplicar_publicacion_catalogo(sucursal, datos):
    (
        raiz,
        release_id,
        publicacion_id,
        anterior_id,
        version,
        checksum,
    ) = validar_publicacion_catalogo(datos, sucursal)
    type(sucursal).objects.select_for_update().get(pk=sucursal.pk)

    publicaciones = PublicacionCatalogoCentral.objects.select_for_update().filter(
        sucursal=sucursal,
        estado=PublicacionCatalogoCentral.Estado.APLICADA,
    )
    ultima_activa = publicaciones.order_by("-version_contrato", "-version").first()
    if (
        ultima_activa is not None
        and ultima_activa.version_contrato >= 3
        and raiz["version_contrato"] < 3
    ):
        _error("schema_no_soportado", "No se admite bajar el contrato de catálogo v3 a v2.")
    # Central inicia v3 en 1 aunque exista una cadena v2. Cada contrato tiene
    # su propia secuencia; las publicaciones y ACK v2 permanecen intactos.
    ultima = publicaciones.filter(
        version_contrato=raiz["version_contrato"]
    ).order_by("-version").first()
    _validar_raices_promocionales_historicas(sucursal, raiz)
    existente = PublicacionCatalogoCentral.objects.filter(
        sucursal=sucursal,
        publicacion_id=publicacion_id,
    ).first()
    if existente:
        mismo_replay = (
            existente.estado == PublicacionCatalogoCentral.Estado.APLICADA
            and existente.release_id == release_id
            and existente.publicacion_anterior_id == anterior_id
            and existente.checksum == checksum
            and existente.version == version
            and existente.snapshot == raiz
        )
        if mismo_replay and ultima and existente.pk == ultima.pk:
            return existente, False
        if mismo_replay:
            _error(
                "version_fuera_de_orden",
                "La publicacion ya no es la ultima version activa.",
            )
        _error("conflicto_local", "publicacion_id reutilizado con contenido distinto.")
    if PublicacionCatalogoCentral.objects.filter(
        sucursal=sucursal,
        release_id=release_id,
    ).exists():
        _error("conflicto_local", "release_id reutilizado por otra publicacion.")

    if ultima is None:
        if version != 1 or anterior_id is not None:
            _error(
                "version_fuera_de_orden",
                "La primera publicacion local debe ser version 1 sin predecesora.",
            )
    elif version != ultima.version + 1:
        _error(
            "version_fuera_de_orden",
            "La publicacion debe ser exactamente la siguiente version.",
        )
    elif anterior_id != ultima.publicacion_id:
        _error(
            "version_fuera_de_orden",
            "La predecesora no coincide con la publicacion local activa.",
        )

    categorias_existentes = {
        item.central_id: item
        for item in IdentidadCategoriaCentral.objects.select_for_update()
        .select_related("categoria")
        .filter(sucursal=sucursal)
    }
    productos_existentes = {
        item.central_id: item
        for item in IdentidadProductoCentral.objects.select_for_update()
        .select_related("producto")
        .filter(sucursal=sucursal)
    }
    contenido = raiz["contenido"]
    categorias_por_central = {}
    categorias_publicadas = set()
    for datos_categoria in contenido["categorias"]:
        categoria = _categoria_local(
            sucursal,
            datos_categoria,
            categorias_existentes,
            alta_v3=raiz["version_contrato"] == 3,
        )
        if Categoria.objects.filter(
            sucursal=sucursal,
            nombre=datos_categoria["nombre"],
        ).exclude(pk=categoria.pk).exists():
            _error("conflicto_local", "Nombre de categoria local duplicado.")
        categoria.nombre = datos_categoria["nombre"]
        categoria.orden = datos_categoria["orden"]
        categoria.activa = datos_categoria["activa"]
        categoria.save(update_fields=["nombre", "orden", "activa"])
        central_id = uuid.UUID(datos_categoria["categoria_central_id"])
        categorias_por_central[central_id] = categoria
        categorias_publicadas.add(central_id)

    productos_publicados = set()
    for datos_producto in contenido["productos"]:
        central_id = uuid.UUID(datos_producto["producto_central_id"])
        categoria_id = uuid.UUID(datos_producto["categoria_central_id"])
        producto = _producto_local(
            sucursal,
            datos_producto,
            categorias_por_central[categoria_id],
            productos_existentes,
            compatibilidad_legacy_v2=raiz["version_contrato"] == 2,
            alta_v3=raiz["version_contrato"] == 3,
        )
        vendible = datos_producto["activo"] and datos_producto.get("disponible_sucursal", True)
        producto.categoria = categorias_por_central[categoria_id]
        producto.codigo = datos_producto["codigo"]
        producto.nombre = datos_producto["nombre"]
        producto.nombre_corto = datos_producto["nombre_corto"]
        producto.orden = datos_producto["orden"]
        producto.permite_termino = datos_producto["permite_termino"]
        producto.termino_predeterminado = datos_producto["termino_predeterminado"]
        producto.abreviaturas_termino = datos_producto["abreviaturas_termino"]
        producto.destino_impresion = datos_producto["destino_impresion"]
        producto.activo = vendible
        producto.disponible_sucursal = datos_producto.get("disponible_sucursal", True)
        producto.origen = "central_v2"
        producto.save(
            update_fields=[
                "categoria",
                "codigo",
                "nombre",
                "nombre_corto",
                "orden",
                "permite_termino",
                "termino_predeterminado",
                "abreviaturas_termino",
                "destino_impresion",
                "activo",
                "disponible_sucursal",
                "origen",
                "actualizado_en",
            ]
        )
        precio_datos = datos_producto["precio"]
        # Cada snapshot completo sustituye toda la secuencia central del producto.
        # Desactivar primero también neutraliza precios futuros de publicaciones
        # anteriores, que de otro modo podrían reaparecer después de la activación.
        Precio.objects.select_for_update().filter(
            sucursal=sucursal,
            producto=producto,
            origen="central_v2",
        ).update(activo=False)
        Precio.objects.update_or_create(
            sucursal=sucursal,
            producto=producto,
            vigente_desde=date.fromisoformat(precio_datos["vigente_desde"]),
            defaults={
                "importe": Decimal(precio_datos["importe"]),
                "vigente_hasta": (
                    date.fromisoformat(precio_datos["vigente_hasta"])
                    if precio_datos["vigente_hasta"]
                    else None
                ),
                "activo": vendible,
                "origen": "central_v2",
                "publicacion_central_id": publicacion_id,
            },
        )
        productos_publicados.add(central_id)

    for central_id, mapeo in productos_existentes.items():
        if central_id not in productos_publicados:
            Producto.objects.filter(pk=mapeo.producto_id).update(activo=False)
            Precio.objects.filter(
                producto_id=mapeo.producto_id,
                origen="central_v2",
            ).update(activo=False)
    for central_id, mapeo in categorias_existentes.items():
        if central_id not in categorias_publicadas:
            Categoria.objects.filter(pk=mapeo.categoria_id).update(activa=False)

    publicacion = PublicacionCatalogoCentral.objects.create(
        sucursal=sucursal,
        release_id=release_id,
        publicacion_id=publicacion_id,
        publicacion_anterior_id=anterior_id,
        version=version,
        version_contrato=raiz["version_contrato"],
        checksum=checksum,
        estado=PublicacionCatalogoCentral.Estado.APLICADA,
        snapshot=raiz,
        aplicado_en=timezone.now(),
    )
    if raiz["version_contrato"] >= 3:
        from .promociones import aplicar_promociones_publicadas

        try:
            aplicar_promociones_publicadas(
                sucursal,
                publicacion,
                contenido["promociones"],
                productos_existentes,
            )
        except ValueError as exc:
            raise ErrorCatalogoCentral(str(exc), codigo="conflicto_local") from exc
    from .aprovisionamiento import registrar_catalogo_aplicado

    registrar_catalogo_aplicado(sucursal, publicacion)
    _crear_ack(sucursal, publicacion)
    return publicacion, True
