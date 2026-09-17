"""Consolidación mensual del edge local y purga autorizada por el VPS."""

import json
import threading
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    ConsolidacionMensual,
    ControlEfectivoDia,
    CorteCaja,
    CorteSucursal,
    EventoOutbox,
    LiquidacionRepartidor,
    MovimientoCaja,
    ReporteAdministrativo,
    SolicitudRepeticionTicket,
    Ticket,
)
from .purgas_fisicas import (
    REFERENCIA_CONSOLIDACION,
    crear_solicitud_archivos,
    crear_solicitud_respaldos,
    procesar_solicitud_archivos,
    retirar_solicitudes_absorbidas,
    solicitudes_archivos_cortes,
)
from .services import ErrorVenta


_CONSOLIDACION_LOCAL_LOCK = threading.RLock()


def _inicio_mes(valor):
    return date(valor.year, valor.month, 1)


def _mes_siguiente(periodo):
    if periodo.month == 12:
        return date(periodo.year + 1, 1, 1)
    return date(periodo.year, periodo.month + 1, 1)


def _fecha_local(valor):
    if timezone.is_aware(valor):
        return timezone.localtime(valor).date()
    return valor.date()


def _periodo_corte(corte):
    return _inicio_mes(_fecha_local(corte.inicio or corte.fin))


def _filtro_cortes_periodo(periodo, siguiente):
    return (
        Q(inicio__date__gte=periodo, inicio__date__lt=siguiente)
        | Q(
            inicio__isnull=True,
            fin__date__gte=periodo,
            fin__date__lt=siguiente,
        )
    )


def _filtro_tickets_periodo(periodo, siguiente):
    """Asigna cada ticket al mes en que realmente entró a operación."""

    return (
        Q(
            activado_programado_en__date__gte=periodo,
            activado_programado_en__date__lt=siguiente,
        )
        | Q(
            activado_programado_en__isnull=True,
            creado_en__date__gte=periodo,
            creado_en__date__lt=siguiente,
        )
    )


def _periodo_ticket(creado_en, activado_programado_en):
    return _inicio_mes(_fecha_local(activado_programado_en or creado_en))


def _control_tiene_actividad(control):
    """Distingue un control abierto por la UI de un conteo realmente capturado."""

    for grupo in (
        control.fondo_anterior,
        control.fondo_siguiente,
        control.ventas_apps,
    ):
        for valor in (grupo or {}).values():
            try:
                if Decimal(str(valor or 0)) != 0:
                    return True
            except (InvalidOperation, TypeError, ValueError):
                # Un valor no numérico no debe ocultar un control que requiere revisión.
                if str(valor or "").strip():
                    return True
    return False


def periodos_pendientes(sucursal, hoy=None):
    hoy = hoy or timezone.localdate()
    mes_actual = _inicio_mes(hoy)
    periodos = {
        _periodo_corte(corte)
        for corte in CorteCaja.objects.filter(sucursal=sucursal).only("inicio", "fin")
        if _periodo_corte(corte) < mes_actual
    }
    periodos.update(
        _periodo_ticket(creado_en, activado_programado_en)
        for creado_en, activado_programado_en in Ticket.objects.filter(
            Q(activado_programado_en__date__lt=mes_actual)
            | Q(
                activado_programado_en__isnull=True,
                creado_en__date__lt=mes_actual,
            ),
            sucursal=sucursal,
        )
        .exclude(estado=Ticket.Estado.PROGRAMADO)
        .values_list("creado_en", "activado_programado_en")
    )
    periodos.update(
        _inicio_mes(_fecha_local(creado_en))
        for creado_en in MovimientoCaja.objects.filter(
            sucursal=sucursal,
            creado_en__date__lt=mes_actual,
        ).values_list("creado_en", flat=True)
    )
    periodos.update(
        _inicio_mes(control.fecha)
        for control in ControlEfectivoDia.objects.filter(
            sucursal=sucursal,
            fecha__lt=mes_actual,
        ).only("fecha", "fondo_anterior", "fondo_siguiente", "ventas_apps")
        if _control_tiene_actividad(control)
    )
    # Una consolidación confirmada conserva el bloqueo aunque su detalle lógico
    # ya haya sido eliminado: sólo SYSTEM puede cerrar su purga física.
    periodos.update(
        ConsolidacionMensual.objects.filter(
            sucursal=sucursal,
            periodo__lt=mes_actual,
        )
        .exclude(estado=ConsolidacionMensual.Estado.PURGADA)
        .values_list("periodo", flat=True)
    )
    purgados = set(
        ConsolidacionMensual.objects.filter(
            sucursal=sucursal,
            estado=ConsolidacionMensual.Estado.PURGADA,
        ).values_list("periodo", flat=True)
    )
    return sorted(periodos - purgados)

def estado_cierre_mensual(sucursal, hoy=None):
    pendientes = periodos_pendientes(sucursal, hoy=hoy)
    registro = (
        ConsolidacionMensual.objects.filter(
            sucursal=sucursal,
            periodo=pendientes[0],
        ).first()
        if pendientes
        else None
    )
    return {
        "requerido": bool(pendientes),
        "periodo": pendientes[0].isoformat() if pendientes else "",
        "estado": registro.estado if registro else "",
        "ultimo_error": registro.ultimo_error if registro else "",
        "vps_configurado": bool(settings.VPS_CONSOLIDACION_URL),
        "purga_fisica_pendiente": bool(
            registro
            and registro.estado == ConsolidacionMensual.Estado.CONFIRMADA
        ),
        "intervalo_purga_segundos": 300,
    }


def exigir_mes_operativo(sucursal, hoy=None):
    estado = estado_cierre_mensual(sucursal, hoy=hoy)
    if estado["requerido"]:
        raise ErrorVenta(
            "Debes consolidar el mes "
            f"{estado['periodo'][:7]} con el VPS y reiniciar folios antes de iniciar ventas."
        )


def _sumar_mapas(filas, campo):
    acumulado = {}
    for fila in filas:
        for clave, valor in (getattr(fila, campo) or {}).items():
            acumulado[clave] = acumulado.get(clave, Decimal("0.00")) + Decimal(str(valor or 0))
    return {clave: str(valor.quantize(Decimal("0.01"))) for clave, valor in acumulado.items()}


def construir_totales(sucursal, periodo):
    siguiente = _mes_siguiente(periodo)
    cortes = list(
        CorteCaja.objects.filter(
            _filtro_cortes_periodo(periodo, siguiente),
            sucursal=sucursal,
        ).order_by("fin")
    )
    if not cortes:
        raise ErrorVenta("No hay cortes de caja que consolidar en ese mes.")
    tickets_abiertos = Ticket.objects.filter(
        _filtro_tickets_periodo(periodo, siguiente),
        sucursal=sucursal,
    ).exclude(estado__in=[Ticket.Estado.CANCELADO, Ticket.Estado.PROGRAMADO])
    if tickets_abiertos.exists():
        raise ErrorVenta(
            "Aún hay detalle de ventas del mes. Completa el corte de caja pendiente antes de consolidar."
        )
    return {
        "periodo": periodo.isoformat(),
        "cortes": len(cortes),
        "ventas": str(
            sum((corte.total_ventas for corte in cortes), Decimal("0.00")).quantize(
                Decimal("0.01")
            )
        ),
        "resultado_caja": str(
            sum((corte.total_caja for corte in cortes), Decimal("0.00")).quantize(
                Decimal("0.01")
            )
        ),
        "ingresos": str(
            sum((corte.total_entradas for corte in cortes), Decimal("0.00")).quantize(
                Decimal("0.01")
            )
        ),
        "gastos": str(
            sum((corte.total_salidas for corte in cortes), Decimal("0.00")).quantize(
                Decimal("0.01")
            )
        ),
        "terminales": str(
            sum((corte.total_terminales for corte in cortes), Decimal("0.00")).quantize(
                Decimal("0.01")
            )
        ),
        "canales": _sumar_mapas(cortes, "totales_canales"),
        "apps": _sumar_mapas(cortes, "ventas_apps"),
        "sucursales": _sumar_mapas(cortes, "totales_sucursales"),
    }


def _enviar_vps(payload):
    if not settings.VPS_CONSOLIDACION_URL:
        raise ErrorVenta(
            "La consolidación mensual está pendiente: configura VPS_CONSOLIDACION_URL."
        )
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Idempotency-Key": payload["idempotencia"],
    }
    if settings.VPS_CONSOLIDACION_TOKEN:
        headers["Authorization"] = f"Bearer {settings.VPS_CONSOLIDACION_TOKEN}"
    solicitud = Request(
        settings.VPS_CONSOLIDACION_URL,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(solicitud, timeout=settings.VPS_CONSOLIDACION_TIMEOUT) as respuesta:
            codigo = getattr(respuesta, "status", respuesta.getcode())
            datos = json.loads(respuesta.read().decode("utf-8"))
    except HTTPError as exc:
        raise ErrorVenta(f"El VPS rechazó la consolidación (HTTP {exc.code}).") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ErrorVenta("No fue posible conectar con el VPS para consolidar el mes.") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ErrorVenta("El VPS respondió sin un acuse JSON válido.") from exc
    if not 200 <= int(codigo) < 300:
        raise ErrorVenta(f"El VPS rechazó la consolidación (HTTP {codigo}).")
    if datos.get("recibido") is not True or not str(datos.get("acuse", "")).strip():
        raise ErrorVenta("El VPS no confirmó explícitamente la recepción de los datos.")
    return str(datos["acuse"]).strip()[:160]


@transaction.atomic
def _purgar_periodo(sucursal, consolidacion):
    from impresion.models import TrabajoImpresion

    periodo = consolidacion.periodo
    siguiente = _mes_siguiente(periodo)

    ids_tickets = list(
        Ticket.objects.filter(
            _filtro_tickets_periodo(periodo, siguiente),
            sucursal=sucursal,
        )
        .exclude(estado=Ticket.Estado.PROGRAMADO)
        .values_list("id", flat=True)
    )
    cortes = list(
        CorteCaja.objects.select_for_update().filter(
            _filtro_cortes_periodo(periodo, siguiente),
            sucursal=sucursal,
        )
    )
    rutas_absorbidas, solicitudes_absorbidas = solicitudes_archivos_cortes(
        corte.id for corte in cortes
    )
    reportes = {corte.reporte_id for corte in cortes}
    liquidaciones = list(
        LiquidacionRepartidor.objects.filter(
            sucursal=sucursal,
            creado_en__date__gte=periodo,
            creado_en__date__lt=siguiente,
        )
    )
    reportes.update(item.reporte_id for item in liquidaciones)
    cortes_sucursal = list(
        CorteSucursal.objects.filter(
            sucursal=sucursal,
            creado_en__date__gte=periodo,
            creado_en__date__lt=siguiente,
        )
    )
    reportes.update(item.reporte_id for item in cortes_sucursal)
    reportes.update(
        ReporteAdministrativo.objects.filter(
            sucursal=sucursal,
            creado_en__date__gte=periodo,
            creado_en__date__lt=siguiente,
        ).values_list("id", flat=True)
    )
    rutas = list(
        TrabajoImpresion.objects.filter(
            Q(ticket_id__in=ids_tickets) | Q(reporte_id__in=reportes)
        )
        .exclude(archivo="")
        .values_list("archivo", flat=True)
    )
    rutas = tuple(sorted(set([*rutas, *rutas_absorbidas])))

    if ids_tickets:
        SolicitudRepeticionTicket.objects.filter(
            ticket_origen_id__in=ids_tickets
        ).delete()
        SolicitudRepeticionTicket.objects.filter(
            ticket_nuevo_id__in=ids_tickets
        ).delete()
        EventoOutbox.objects.filter(
            agregado="ticket",
            agregado_id__in=ids_tickets,
        ).delete()
        Ticket.objects.filter(id__in=ids_tickets).delete()

    CorteCaja.objects.filter(id__in=[corte.id for corte in cortes]).delete()
    LiquidacionRepartidor.objects.filter(
        id__in=[item.id for item in liquidaciones]
    ).delete()
    CorteSucursal.objects.filter(
        id__in=[item.id for item in cortes_sucursal]
    ).delete()
    ReporteAdministrativo.objects.filter(id__in=reportes).delete()
    MovimientoCaja.objects.filter(
        sucursal=sucursal,
        creado_en__date__gte=periodo,
        creado_en__date__lt=siguiente,
    ).delete()
    ControlEfectivoDia.objects.filter(
        sucursal=sucursal,
        fecha__gte=periodo,
        fecha__lt=siguiente,
    ).delete()

    from .services import reiniciar_folios

    reiniciar_folios(sucursal)
    # El acuse ya permitió purgar el detalle lógico. El estado permanece
    # CONFIRMADA hasta que la tarea SYSTEM cree un respaldo post-purga,
    # depure los respaldos previos y complete los archivos físicos.
    consolidacion.estado = ConsolidacionMensual.Estado.CONFIRMADA
    consolidacion.totales = {}
    # Marca sólo la eliminación lógica; CONFIRMADA sigue indicando que la
    # limpieza física privilegiada aún no ha concluido.
    consolidacion.purgado_en = timezone.now()
    consolidacion.ultimo_error = ""
    consolidacion.save(
        update_fields=[
            "estado",
            "totales",
            "purgado_en",
            "ultimo_error",
        ]
    )
    solicitud_archivos = crear_solicitud_archivos(
        rutas,
        referencia_tipo=REFERENCIA_CONSOLIDACION,
        referencia_id=consolidacion.pk,
    )
    crear_solicitud_respaldos(consolidacion, rutas_archivos=rutas)
    if solicitudes_absorbidas:
        if solicitud_archivos is None:
            raise RuntimeError("No se creó la solicitud mensual que absorbe los archivos.")
        retirar_solicitudes_absorbidas(solicitudes_absorbidas)
    if solicitud_archivos is not None:
        transaction.on_commit(
            lambda path=solicitud_archivos: procesar_solicitud_archivos(path)
        )

def _obtener_consolidacion_bloqueada(sucursal, periodo):
    consulta = ConsolidacionMensual.objects.select_for_update()
    consolidacion = consulta.filter(sucursal=sucursal, periodo=periodo).first()
    if consolidacion is not None:
        return consolidacion

    try:
        # El savepoint permite recuperarse de la restricción única si otro
        # proceso creó el mismo mes después de nuestra primera lectura.
        with transaction.atomic():
            ConsolidacionMensual.objects.create(
                sucursal=sucursal,
                periodo=periodo,
                totales={},
            )
    except IntegrityError:
        pass
    return consulta.get(sucursal=sucursal, periodo=periodo)


def consolidar_periodo(sucursal, periodo=None):
    periodo = periodo or (periodos_pendientes(sucursal) or [None])[0]
    if periodo is None:
        raise ErrorVenta("No hay un mes anterior pendiente de consolidación.")
    if isinstance(periodo, str):
        try:
            periodo = date.fromisoformat(f"{periodo[:7]}-01")
        except ValueError as exc:
            raise ErrorVenta("El periodo mensual no es válido.") from exc
    periodo = _inicio_mes(periodo)
    if periodo >= _inicio_mes(timezone.localdate()):
        raise ErrorVenta("Sólo se pueden consolidar meses ya terminados.")

    error = None
    resultado = None
    # Waitress atiende varias terminales con hilos. Este bloqueo evita la
    # carrera de creación propia de SQLite; select_for_update mantiene la
    # misma garantía entre procesos cuando se usa PostgreSQL.
    with _CONSOLIDACION_LOCAL_LOCK:
        with transaction.atomic():
            type(sucursal).objects.select_for_update().get(pk=sucursal.pk)
            consolidacion = _obtener_consolidacion_bloqueada(sucursal, periodo)
            consolidacion.refresh_from_db()

            if consolidacion.estado == ConsolidacionMensual.Estado.PURGADA:
                resultado = consolidacion
            elif consolidacion.estado == ConsolidacionMensual.Estado.CONFIRMADA:
                # Si el proceso cayó justo después del acuse, completa una sola
                # vez la purga lógica. Después sólo espera a la tarea SYSTEM.
                if consolidacion.purgado_en is None:
                    _purgar_periodo(sucursal, consolidacion)
                resultado = consolidacion
            else:
                try:
                    totales = construir_totales(sucursal, periodo)
                    consolidacion.totales = totales
                    consolidacion.intentos += 1
                    consolidacion.estado = ConsolidacionMensual.Estado.PENDIENTE
                    consolidacion.ultimo_error = ""
                    consolidacion.save(
                        update_fields=["totales", "intentos", "estado", "ultimo_error"]
                    )
                    payload = {
                        "version_contrato": 1,
                        "idempotencia": str(consolidacion.idempotencia),
                        "sucursal": {
                            "id": str(sucursal.id),
                            "clave": sucursal.clave,
                            "nombre": sucursal.nombre,
                        },
                        "periodo": periodo.isoformat(),
                        "totales": totales,
                    }
                    acuse = _enviar_vps(payload)
                except ErrorVenta as exc:
                    consolidacion.estado = ConsolidacionMensual.Estado.ERROR
                    consolidacion.ultimo_error = str(exc)
                    consolidacion.save(update_fields=["estado", "ultimo_error"])
                    error = ErrorVenta(str(exc))
                else:
                    consolidacion.estado = ConsolidacionMensual.Estado.CONFIRMADA
                    consolidacion.acuse_vps = acuse
                    consolidacion.confirmado_en = timezone.now()
                    consolidacion.save(
                        update_fields=["estado", "acuse_vps", "confirmado_en"]
                    )
                    _purgar_periodo(sucursal, consolidacion)
                    resultado = consolidacion

        if error is not None:
            raise error
        resultado.refresh_from_db()
        return resultado
