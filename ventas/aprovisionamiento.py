"""Alistamiento durable del Edge y puerta segura del catálogo inicial.

La salud técnica del proceso no implica que el POS esté listo para vender.
Las transiciones de catálogo se confirman junto con la publicación SQLite.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from catalogo.models import (
    EstadoAprovisionamientoCatalogo,
    IdentidadProductoCentral,
    Precio,
    Producto,
    PublicacionCatalogoCentral,
)
from ventas.models import ConfiguracionSucursal


class CatalogoInicialPendiente(ValueError):
    """La identidad técnica existe, pero falta el menú publicado/alistado."""


def requiere_catalogo_inicial(sucursal=None):
    """Activa el gate en Edge enrolado; no altera la operación legacy sin identidad Central."""

    branch_id = getattr(settings, "CENTRAL_BRANCH_ID", "")
    edge_id = getattr(settings, "CENTRAL_POS_INSTANCE_ID", "")
    if branch_id or edge_id:
        return True
    # Una identidad ya iniciada en SQLite sigue exigiendo catálogo si falta la
    # configuración de entorno al reiniciar; no se convierte en legacy por accidente.
    return bool(
        sucursal is not None
        and EstadoAprovisionamientoCatalogo.objects.filter(sucursal=sucursal).exists()
    )


def _ultima_publicacion(sucursal):
    return (
        PublicacionCatalogoCentral.objects.filter(
            sucursal=sucursal,
            estado=PublicacionCatalogoCentral.Estado.APLICADA,
        )
        .order_by("-version")
        .first()
    )


def _identidad_enrolada(sucursal):
    configuracion = ConfiguracionSucursal.objects.filter(sucursal=sucursal).first()
    if not configuracion or not configuracion.instalacion_id:
        return False
    branch_id = getattr(settings, "CENTRAL_BRANCH_ID", "")
    edge_id = getattr(settings, "CENTRAL_POS_INSTANCE_ID", "")
    if branch_id and str(sucursal.id) != branch_id:
        return False
    if edge_id and str(configuracion.instalacion_id) != edge_id:
        return False
    return True


def _productos_snapshot_vendibles(publicacion):
    if publicacion is None or publicacion.version_contrato < 3:
        return {}
    from .catalogo_central import ErrorCatalogoCentral, checksum_snapshot

    try:
        if checksum_snapshot(publicacion.snapshot) != publicacion.checksum:
            return {}
        productos = publicacion.snapshot["contenido"]["productos"]
        return {
            uuid.UUID(item["producto_central_id"]): item
            for item in productos
            if item["activo"] is True and item["disponible_sucursal"] is True
        }
    except (ErrorCatalogoCentral, KeyError, TypeError, ValueError, AttributeError):
        # Una copia dañada no habilita ventas; la publicación previa permanece en DB.
        return {}


def ids_productos_vendibles(sucursal, *, exigir_listo=True):
    """IDs autorizados por snapshot v3, mapping y precio local exacto.

    Una edición local posterior de precio/código nunca suplanta la autoridad
    Central; la siguiente publicación válida puede reparar la divergencia.
    """

    if not requiere_catalogo_inicial(sucursal):
        return set(
            Producto.objects.filter(sucursal=sucursal, activo=True)
            .values_list("id", flat=True)
        )
    estado = estado_aprovisionamiento(sucursal, comprobar_menu=False)
    if exigir_listo and not estado["listo"]:
        return set()
    publicacion = _ultima_publicacion(sucursal)
    esperados = _productos_snapshot_vendibles(publicacion)
    if not esperados:
        return set()
    mapeos = list(
        IdentidadProductoCentral.objects.select_related("producto__categoria").filter(
            sucursal=sucursal,
            central_id__in=esperados,
            producto__sucursal=sucursal,
            producto__activo=True,
            producto__disponible_sucursal=True,
        )
    )
    if not mapeos:
        return set()
    hoy = timezone.localdate()
    precios = {}
    for precio in (
        Precio.objects.filter(
            sucursal=sucursal,
            producto_id__in=[mapeo.producto_id for mapeo in mapeos],
            activo=True,
            vigente_desde__lte=hoy,
        )
        .filter(Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gte=hoy))
        .order_by("producto_id", "-vigente_desde")
    ):
        precios.setdefault(precio.producto_id, precio)
    vendibles = set()
    for mapeo in mapeos:
        esperado = esperados[mapeo.central_id]
        producto = mapeo.producto
        precio = precios.get(producto.id)
        precio_central = esperado["precio"]
        if (
            producto.codigo == esperado["codigo"]
            and producto.categoria.activa
            and producto.nombre == esperado["nombre"]
            and producto.nombre_corto == esperado["nombre_corto"]
            and precio is not None
            and precio.origen == "central_v2"
            and precio.publicacion_central_id == publicacion.publicacion_id
            and precio.importe == Decimal(precio_central["importe"])
            and precio.vigente_desde.isoformat() == precio_central["vigente_desde"]
            and (
                precio.vigente_hasta.isoformat() if precio.vigente_hasta else None
            ) == precio_central["vigente_hasta"]
        ):
            vendibles.add(producto.id)
    return vendibles

def estado_aprovisionamiento(sucursal, *, comprobar_menu=True):
    """Estado público persistido y comprobado contra la última publicación válida."""

    Estado = EstadoAprovisionamientoCatalogo.Estado
    ultima = _ultima_publicacion(sucursal)
    if not requiere_catalogo_inicial(sucursal):
        # Las bases legacy no enroladas conservan su menú local hasta el corte autorizado.
        return {
            "estado": Estado.LISTO,
            "catalogo_aplicado": ultima is not None,
            "listo": True,
            "requerido": False,
            "mensaje": "",
            "publicacion_id": str(ultima.publicacion_id) if ultima else "",
            "version": ultima.version if ultima else None,
            "version_contrato": ultima.version_contrato if ultima else None,
        }
    registro = EstadoAprovisionamientoCatalogo.objects.filter(sucursal=sucursal).first()
    identidad = _identidad_enrolada(sucursal)
    if not identidad:
        estado = Estado.ENROLADO
        mensaje = "Falta confirmar la identidad local del Edge."
    elif ultima is None:
        estado = (
            Estado.ESPERANDO_CATALOGO_INICIAL
            if registro and registro.estado == Estado.ESPERANDO_CATALOGO_INICIAL
            else Estado.ENROLADO
        )
        mensaje = "Enrolamiento registrado; esperando catálogo inicial de Central."
    elif ultima.version_contrato < 3:
        estado = Estado.CATALOGO_APLICADO
        mensaje = "El catálogo anterior requiere una publicación v3 con disponibilidad y promociones."
    elif registro and registro.estado == Estado.LISTO and registro.publicacion_id == ultima.pk:
        estado = Estado.LISTO
        mensaje = "Catálogo inicial aplicado y Edge listo para operar."
    else:
        estado = Estado.CATALOGO_APLICADO
        mensaje = "Catálogo aplicado; faltan las verificaciones operativas de alistamiento."
    if estado == Estado.LISTO and comprobar_menu and not ids_productos_vendibles(
        sucursal, exigir_listo=False
    ):
        estado = Estado.CATALOGO_APLICADO
        mensaje = "El catálogo no contiene productos vendibles con precio vigente."
    return {
        "estado": estado,
        "catalogo_aplicado": ultima is not None,
        "listo": estado == Estado.LISTO,
        "requerido": True,
        "mensaje": mensaje,
        "publicacion_id": str(ultima.publicacion_id) if ultima else "",
        "version": ultima.version if ultima else None,
        "version_contrato": ultima.version_contrato if ultima else None,
    }


@transaction.atomic
def iniciar_aprovisionamiento(sucursal):
    type(sucursal).objects.select_for_update().get(pk=sucursal.pk)
    registro, _ = EstadoAprovisionamientoCatalogo.objects.get_or_create(sucursal=sucursal)
    return registro


@transaction.atomic
def marcar_esperando_catalogo(sucursal):
    type(sucursal).objects.select_for_update().get(pk=sucursal.pk)
    registro, _ = EstadoAprovisionamientoCatalogo.objects.get_or_create(sucursal=sucursal)
    if _ultima_publicacion(sucursal) is None:
        registro.estado = EstadoAprovisionamientoCatalogo.Estado.ESPERANDO_CATALOGO_INICIAL
        registro.publicacion = None
        registro.save(update_fields=["estado", "publicacion", "actualizado_en"])
    return registro


def registrar_catalogo_aplicado(sucursal, publicacion):
    """Debe llamarse dentro de la misma transacción que aplica el snapshot."""

    if publicacion.sucursal_id != sucursal.id:
        raise ValueError("La publicación no pertenece a este Edge.")
    if publicacion.version_contrato < 3 and not requiere_catalogo_inicial(sucursal):
        # Los ensayos v2 previos al enrolamiento no cambian el gate legacy.
        return None
    registro, _ = EstadoAprovisionamientoCatalogo.objects.select_for_update().get_or_create(
        sucursal=sucursal
    )
    ya_listo = registro.estado == EstadoAprovisionamientoCatalogo.Estado.LISTO
    registro.publicacion = publicacion
    registro.estado = (
        EstadoAprovisionamientoCatalogo.Estado.LISTO
        if ya_listo and publicacion.version_contrato >= 3
        else EstadoAprovisionamientoCatalogo.Estado.CATALOGO_APLICADO
    )
    registro.save(update_fields=["estado", "publicacion", "actualizado_en"])
    return registro


@transaction.atomic
def marcar_listo(sucursal):
    """La capa instalador/soporte llama esto tras validar dueño, impresión y backup."""

    type(sucursal).objects.select_for_update().get(pk=sucursal.pk)
    ultima = _ultima_publicacion(sucursal)
    if not _identidad_enrolada(sucursal) or ultima is None or ultima.version_contrato < 3:
        raise CatalogoInicialPendiente("Falta una publicación inicial v3 del Edge enrolado.")
    if not ids_productos_vendibles(sucursal, exigir_listo=False):
        raise CatalogoInicialPendiente("No hay productos vendibles con precio vigente.")
    registro, _ = EstadoAprovisionamientoCatalogo.objects.select_for_update().get_or_create(
        sucursal=sucursal
    )
    registro.publicacion = ultima
    registro.estado = EstadoAprovisionamientoCatalogo.Estado.LISTO
    registro.save(update_fields=["estado", "publicacion", "actualizado_en"])
    return registro


def exigir_catalogo_operativo(sucursal):
    if not requiere_catalogo_inicial(sucursal):
        return
    estado = estado_aprovisionamiento(sucursal)
    if not estado["listo"]:
        raise CatalogoInicialPendiente(estado["mensaje"])


def producto_vendible(sucursal, producto):
    if producto is None or producto.sucursal_id != sucursal.id:
        return False
    if not requiere_catalogo_inicial(sucursal):
        return bool(producto.activo and producto.precio_actual())
    return producto.pk in ids_productos_vendibles(sucursal)
