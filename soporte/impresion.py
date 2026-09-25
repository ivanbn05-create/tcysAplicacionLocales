"""Estado auditable de la validación física de impresión."""

import hashlib
import json

from django.conf import settings

from impresion.models import (
    AsignacionImpresoraTerminal, ConfiguracionImpresionTerminal, Impresora,
)

from .models import ValidacionImpresionFisica


def estado_validacion_impresion(sucursal):
    recursos = list(
        Impresora.objects.filter(sucursal=sucursal).order_by("id")
        .values("id", "host", "puerto", "activa")
    )
    terminales = list(
        ConfiguracionImpresionTerminal.objects.filter(sucursal=sucursal)
        .order_by("id").values("id", "device_id", "activa")
    )
    rutas = list(
        AsignacionImpresoraTerminal.objects.filter(
            terminal__sucursal=sucursal
        ).order_by("terminal_id", "destino").values(
            "terminal_id", "destino", "impresora_id"
        )
    )
    material = {
        "recursos": [
            [str(i["id"]), i["host"], i["puerto"], i["activa"]]
            for i in recursos
        ],
        "terminales": [
            [str(t["id"]), t["device_id"], t["activa"]]
            for t in terminales
        ],
        "rutas": [
            [str(r["terminal_id"]), r["destino"], str(r["impresora_id"])]
            for r in rutas
        ],
    }
    huella = hashlib.sha256(
        json.dumps(material, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()
    activos = {recurso["id"] for recurso in recursos if recurso["activa"]}
    terminales_activas = {terminal["id"] for terminal in terminales if terminal["activa"]}
    rutas_activas = [
        ruta for ruta in rutas if ruta["terminal_id"] in terminales_activas
    ]
    por_terminal = {
        terminal_id: set() for terminal_id in terminales_activas
    }
    for ruta in rutas_activas:
        por_terminal[ruta["terminal_id"]].add(ruta["destino"])
    usados = {ruta["impresora_id"] for ruta in rutas_activas}
    ultima = ValidacionImpresionFisica.objects.filter(sucursal=sucursal).first()

    if settings.PRINT_BACKEND != "tcp":
        codigo = "backend_archivo"
        mensaje = "Impresión física desactivada; el Edge sólo genera vista previa."
    elif not activos:
        codigo = "sin_impresoras"
        mensaje = "Registra y activa una impresora LAN."
    elif not terminales_activas:
        codigo = "sin_terminales"
        mensaje = "Registra una terminal activa."
    elif any(
        "todos" not in destinos and not {"caja", "cocina", "barra"}.issubset(destinos)
        for destinos in por_terminal.values()
    ):
        codigo = "rutas_incompletas"
        mensaje = "Cada terminal activa necesita una ruta general o los tres destinos."
    elif not usados.issubset(activos):
        codigo = "recurso_inactivo"
        mensaje = "Una ruta activa apunta a una impresora desactivada."
    elif not activos.issubset(usados):
        codigo = "recurso_sin_ruta"
        mensaje = "Cada impresora activa debe estar asignada a una terminal."
    elif ultima is None:
        codigo = "prueba_pendiente"
        mensaje = "Falta confirmar una impresión observada en el hardware real."
    elif ultima.huella_topologia != huella:
        codigo = "topologia_cambiada"
        mensaje = "Cambió la configuración; repite la prueba física."
    else:
        codigo = "validada"
        mensaje = "Impresión física confirmada para la configuración actual."

    return {
        "codigo": codigo,
        "lista": codigo == "validada",
        "mensaje": mensaje,
        "recursos_activos": len(activos),
        "terminales_activas": len(terminales_activas),
        "huella_topologia": huella,
        "confirmado_en": ultima.confirmado_en.isoformat() if ultima else "",
        "confirmado_por": ultima.confirmado_por.get_username() if ultima else "",
        "nota": ultima.nota if ultima else "",
    }
