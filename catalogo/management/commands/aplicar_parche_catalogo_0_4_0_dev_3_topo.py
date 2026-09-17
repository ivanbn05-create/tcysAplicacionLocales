"""Publica el cambio de Topo Chico sin volver a cargar la semilla histórica."""

import json
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from catalogo.models import Precio, Producto
from personas.models import Sucursal


PARCHE_ID = "catalogo-0.4.0-dev.3-topo-am-arboledas"
SUCURSAL_OBJETIVO = "ARBOLEDAS"
PRODUCTO_CODIGO = "AM"
NOMBRE_CORTO_ANTERIOR = "AM"
NOMBRE_CORTO_NUEVO = "Topo"
PRECIO_ANTERIOR = Decimal("30.00")
PRECIO_NUEVO = Decimal("32.00")
VIGENTE_DESDE = date(2026, 9, 17)


def _precio_a_dict(precio):
    if precio is None:
        return None
    return {
        "importe": format(precio.importe, ".2f"),
        "vigente_desde": precio.vigente_desde.isoformat(),
    }


class Command(BaseCommand):
    help = (
        "Aplica el parche versionado de Topo Chico al producto AM de ARBOLEDAS. "
        "No carga la semilla ni modifica otros productos."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--sucursal",
            required=True,
            help="Clave de sucursal que recibirá la publicación (debe ser ARBOLEDAS).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Valida y muestra el antes/después sin escribir en la base.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        clave = options["sucursal"].strip().upper()
        if clave != SUCURSAL_OBJETIVO:
            raise CommandError(
                f"El parche {PARCHE_ID} sólo está publicado para {SUCURSAL_OBJETIVO}."
            )
        if settings.SUCURSAL_CLAVE != clave:
            raise CommandError(
                "La sucursal solicitada no coincide con SUCURSAL_CLAVE de esta instalación."
            )

        try:
            sucursal = Sucursal.objects.select_for_update().get(clave=clave, activa=True)
        except Sucursal.DoesNotExist as exc:
            raise CommandError(f"No existe la sucursal activa {clave}.") from exc

        try:
            producto = Producto.objects.select_for_update().get(
                sucursal=sucursal,
                codigo=PRODUCTO_CODIGO,
                activo=True,
            )
        except Producto.DoesNotExist as exc:
            raise CommandError(
                f"No existe el producto activo {PRODUCTO_CODIGO} en {clave}."
            ) from exc

        precio_vigente = producto.precio_actual()
        precio_actual = (
            Precio.objects.select_for_update().filter(pk=precio_vigente.pk).first()
            if precio_vigente is not None
            else None
        )
        if precio_actual is None:
            raise CommandError(
                f"El producto {PRODUCTO_CODIGO} no tiene un precio vigente que actualizar."
            )

        if producto.nombre_corto not in {NOMBRE_CORTO_ANTERIOR, NOMBRE_CORTO_NUEVO}:
            raise CommandError(
                "El nombre corto actual no coincide con el valor legado ni con el valor "
                "objetivo; se rechazó sobrescribir una personalización."
            )
        if precio_actual.importe not in {PRECIO_ANTERIOR, PRECIO_NUEVO}:
            raise CommandError(
                "El precio vigente no coincide con el valor legado ni con el valor "
                "objetivo; se rechazó sobrescribir una personalización."
            )

        cambiar_nombre = producto.nombre_corto != NOMBRE_CORTO_NUEVO
        cambiar_precio = precio_actual.importe != PRECIO_NUEVO
        if cambiar_precio and Precio.objects.filter(
            sucursal=sucursal,
            producto=producto,
            vigente_desde__gt=VIGENTE_DESDE,
            activo=True,
        ).exists():
            raise CommandError(
                "Existen precios activos posteriores a la vigencia del parche; "
                "se rechazó alterar la secuencia de precios."
            )

        antes = {
            "nombre_corto": producto.nombre_corto,
            "precio": _precio_a_dict(precio_actual),
        }
        precio_destino = (
            {
                "importe": format(PRECIO_NUEVO, ".2f"),
                "vigente_desde": VIGENTE_DESDE.isoformat(),
            }
            if cambiar_precio
            else _precio_a_dict(precio_actual)
        )
        despues = {
            "nombre_corto": NOMBRE_CORTO_NUEVO,
            "precio": precio_destino,
        }
        cambios = [
            nombre
            for nombre, requerido in (
                ("nombre_corto", cambiar_nombre),
                ("precio", cambiar_precio),
            )
            if requerido
        ]

        if options["dry_run"]:
            estado = "dry-run"
        elif not cambios:
            estado = "ya_aplicado"
        else:
            if cambiar_nombre:
                producto.nombre_corto = NOMBRE_CORTO_NUEVO
                producto.save(update_fields=["nombre_corto", "actualizado_en"])
            if cambiar_precio:
                Precio.objects.filter(
                    Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gte=VIGENTE_DESDE),
                    sucursal=sucursal,
                    producto=producto,
                    vigente_desde__lt=VIGENTE_DESDE,
                    activo=True,
                ).update(vigente_hasta=VIGENTE_DESDE - timedelta(days=1))
                Precio.objects.update_or_create(
                    sucursal=sucursal,
                    producto=producto,
                    vigente_desde=VIGENTE_DESDE,
                    defaults={
                        "importe": PRECIO_NUEVO,
                        "vigente_hasta": None,
                        "activo": True,
                    },
                )
            estado = "aplicado"

        resultado = {
            "antes": antes,
            "cambios": cambios,
            "despues": despues,
            "estado": estado,
            "parche": PARCHE_ID,
            "producto_codigo": PRODUCTO_CODIGO,
            "sucursal": clave,
        }
        self.stdout.write(
            "PARCHE_CATALOGO="
            + json.dumps(resultado, ensure_ascii=False, sort_keys=True)
        )
