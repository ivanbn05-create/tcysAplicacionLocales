import re
import warnings
from collections import OrderedDict
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from openpyxl import load_workbook

from personas.models import Sucursal
from ventas.clientes import guardar_cliente
from ventas.models import Cliente
from ventas.normalizacion import normalizar_texto, normalizar_telefono


MARCADOR_ORIGEN = "Importado desde Clientes-Domicilio-Completo.xlsx"
PATRON_CONTACTO = re.compile(r"(?<!\d)(33\d{8}|\d{8})(?!\d)")
PATRON_INTERIOR = re.compile(
    r"\b(INT(?:ERIOR)?|DEP(?:ARTAMENTO)?|DEPTO|CASA|LOCAL)\.?\s*#?\s*([A-Z0-9-]+)",
    re.IGNORECASE,
)


def texto(valor):
    return str(valor or "").replace("�", "Ñ").strip()


def separar_domicilio(valor):
    domicilio = re.sub(r"\s+", " ", texto(valor))
    interior = ""
    coincidencia_interior = PATRON_INTERIOR.search(domicilio)
    base = domicilio
    if coincidencia_interior:
        interior = f"{coincidencia_interior.group(1).upper()} {coincidencia_interior.group(2)}"
        base = f"{domicilio[:coincidencia_interior.start()]} {domicilio[coincidencia_interior.end():]}".strip()
    numeros = list(re.finditer(r"(?<![A-Z0-9])#?(\d+[A-Z]?)(?![A-Z0-9])", base, re.IGNORECASE))
    if not numeros:
        return domicilio, "", interior
    exterior = numeros[-1]
    calle = f"{base[:exterior.start()]} {base[exterior.end():]}".strip(" ,-#")
    calle = re.sub(r"\s+", " ", calle) or domicilio
    return calle, exterior.group(1), interior


class Command(BaseCommand):
    help = "Importa clientes completos del directorio XLSX legado, agrupados por nombre."

    def add_arguments(self, parser):
        parser.add_argument("archivo", type=Path)
        parser.add_argument("--sucursal", default="ARBOLEDAS")
        parser.add_argument("--dry-run", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        archivo = options["archivo"]
        if not archivo.is_file():
            raise CommandError(f"No existe el archivo: {archivo}")
        try:
            sucursal = Sucursal.objects.get(clave=options["sucursal"])
        except Sucursal.DoesNotExist as exc:
            raise CommandError("Primero ejecuta cargar_datos_iniciales.") from exc

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            libro = load_workbook(archivo, read_only=True, data_only=True)
        if "Clientes" not in libro.sheetnames:
            raise CommandError("El archivo no contiene la hoja Clientes.")

        grupos = OrderedDict()
        omitidos = 0
        for fila in libro["Clientes"].iter_rows(min_row=2, values_only=True):
            nombre, domicilio, colonia, ciudad, _, telefono, referencia, *_ = (list(fila) + [None] * 9)[:9]
            nombre, domicilio = texto(nombre), texto(domicilio)
            telefono = texto(telefono)
            referencia = texto(referencia)
            telefono_normalizado = normalizar_telefono(telefono)
            contacto_rotativo = PATRON_CONTACTO.search(referencia) if len(telefono_normalizado) < 7 else None
            if not nombre or not domicilio or (len(telefono_normalizado) < 7 and not contacto_rotativo):
                omitidos += 1
                continue
            clave = normalizar_texto(nombre)
            grupo = grupos.setdefault(
                clave,
                {
                    "nombre": nombre,
                    "telefonos": OrderedDict(),
                    "domicilios": OrderedDict(),
                    "comentarios_multiples": False,
                },
            )
            if len(telefono_normalizado) >= 7:
                grupo["telefonos"].setdefault(
                    telefono_normalizado,
                    {"etiqueta": "Principal", "numero": telefono},
                )
            if contacto_rotativo:
                grupo["comentarios_multiples"] = True
            calle, exterior, interior = separar_domicilio(domicilio)
            domicilio_clave = normalizar_texto(" ".join([domicilio, texto(colonia), texto(ciudad)]))
            grupo["domicilios"].setdefault(
                domicilio_clave,
                {
                    "etiqueta": "Principal",
                    "calle": calle,
                    "numero_exterior": exterior,
                    "numero_interior": interior,
                    "colonia": texto(colonia),
                    "codigo_postal": "",
                    "municipio": texto(ciudad),
                    "referencia": referencia,
                },
            )

        creados = actualizados = 0
        for grupo in grupos.values():
            cliente = Cliente.objects.filter(
                sucursal=sucursal,
                nombre_normalizado=normalizar_texto(grupo["nombre"]),
                notas__contains=MARCADOR_ORIGEN,
            ).first()
            datos = {
                "nombre": grupo["nombre"],
                "notas": MARCADOR_ORIGEN,
                "comentarios_multiples": grupo["comentarios_multiples"],
                "telefonos": list(grupo["telefonos"].values()),
                "domicilios": list(grupo["domicilios"].values()),
            }
            guardar_cliente(sucursal, datos, cliente=cliente)
            if cliente:
                actualizados += 1
            else:
                creados += 1

        if options["dry_run"]:
            transaction.set_rollback(True)
        modo = "simulados" if options["dry_run"] else "importados"
        self.stdout.write(
            self.style.SUCCESS(
                f"{creados} clientes nuevos y {actualizados} actualizados {modo}; "
                f"{omitidos} filas incompletas omitidas."
            )
        )
