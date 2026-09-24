"""Consolidación mensual del edge local y purga autorizada por el VPS."""

import hashlib
import json
import ssl
import threading
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

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


class ErrorConsolidacionHTTP(ErrorVenta):
    def __init__(self, mensaje, *, status):
        super().__init__(mensaje)
        self.status = int(status)


class _BloquearRedirecciones(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _origen_https(url):
    try:
        partes = urlsplit(str(url))
        puerto = partes.port
    except ValueError as exc:
        raise ErrorVenta("La URL de consolidacion no es valida.") from exc
    if (
        partes.scheme.lower() != "https"
        or not partes.hostname
        or partes.username is not None
        or partes.password is not None
    ):
        raise ErrorVenta("La consolidacion exige HTTPS sin credenciales en la URL.")
    return partes.hostname.lower(), puerto or 443


def _urlopen_sin_redireccion(solicitud, *, timeout):
    contexto = ssl.create_default_context()
    if contexto.verify_mode != ssl.CERT_REQUIRED or not contexto.check_hostname:
        raise ErrorVenta("La verificacion TLS de consolidacion debe permanecer activa.")
    opener = build_opener(
        HTTPSHandler(context=contexto),
        _BloquearRedirecciones(),
    )
    return opener.open(solicitud, timeout=timeout)


# Punto de inyeccion conservado para las pruebas; la implementacion real bloquea 30x.
urlopen = _urlopen_sin_redireccion


def _json_sin_duplicados(pares):
    resultado = {}
    for clave, valor in pares:
        if clave in resultado:
            raise ValueError("clave duplicada")
        resultado[clave] = valor
    return resultado


def _payload_bytes(payload):
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _payload_hash(payload):
    return hashlib.sha256(_payload_bytes(payload)).hexdigest()


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
    # Una consolidación confirmada conserva la alerta administrativa aunque su detalle
    # lógico ya haya sido eliminado: sólo SYSTEM puede cerrar su purga física.
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
            "La consolidacion mensual esta pendiente: configura VPS_CONSOLIDACION_URL."
        )
    contenido = _payload_bytes(payload)
    if len(contenido) > 256 * 1024:
        raise ErrorVenta("La consolidacion supera el limite contractual de 256 KiB.")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Idempotency-Key": payload["idempotencia"],
    }
    if settings.VPS_CONSOLIDACION_TOKEN:
        headers["Authorization"] = f"Bearer {settings.VPS_CONSOLIDACION_TOKEN}"
    origen_esperado = _origen_https(settings.VPS_CONSOLIDACION_URL)
    solicitud = Request(
        settings.VPS_CONSOLIDACION_URL,
        data=contenido,
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(solicitud, timeout=settings.VPS_CONSOLIDACION_TIMEOUT) as respuesta:
            if _origen_https(respuesta.geturl()) != origen_esperado:
                raise ErrorVenta("El VPS intento cambiar el origen de la consolidacion.")
            codigo = int(getattr(respuesta, "status", respuesta.getcode()))
            content_type = str(respuesta.headers.get("Content-Type", "")).split(";", 1)[0].lower()
            cuerpo = respuesta.read(64 * 1024 + 1)
    except HTTPError as exc:
        raise ErrorConsolidacionHTTP(
            f"El VPS rechazo la consolidacion (HTTP {exc.code}).",
            status=exc.code,
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ErrorVenta("No fue posible conectar con el VPS para consolidar el mes.") from exc
    if len(cuerpo) > 64 * 1024:
        raise ErrorVenta("El VPS respondio un acuse demasiado grande.")
    if content_type != "application/json":
        raise ErrorVenta("El VPS no respondio application/json.")
    try:
        datos = json.loads(
            cuerpo,
            object_pairs_hook=_json_sin_duplicados,
            parse_constant=lambda _valor: (_ for _ in ()).throw(ValueError("constante")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ErrorVenta("El VPS respondio sin un acuse JSON valido.") from exc
    if not 200 <= codigo < 300:
        raise ErrorConsolidacionHTTP(
            f"El VPS rechazo la consolidacion (HTTP {codigo}).",
            status=codigo,
        )
    if type(datos) is not dict or set(datos) != {"recibido", "acuse", "estado"}:
        raise ErrorVenta("El VPS respondio un esquema de acuse desconocido.")
    acuse = str(datos.get("acuse") or "").strip()
    estado = str(datos.get("estado") or "").strip()
    if datos.get("recibido") is not True or not acuse or len(acuse) > 160:
        raise ErrorVenta("El VPS no confirmo explicitamente la recepcion de los datos.")
    if estado not in {"recibido", "purgado"}:
        raise ErrorVenta("El VPS respondio un estado de consolidacion desconocido.")
    return acuse, estado


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
            destino=EventoOutbox.Destino.LOCAL,
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
        raise ErrorVenta("No hay un mes anterior pendiente de consolidacion.")
    if isinstance(periodo, str):
        try:
            periodo = date.fromisoformat(f"{periodo[:7]}-01")
        except ValueError as exc:
            raise ErrorVenta("El periodo mensual no es valido.") from exc
    periodo = _inicio_mes(periodo)
    if periodo >= _inicio_mes(timezone.localdate()):
        raise ErrorVenta("Solo se pueden consolidar meses ya terminados.")

    # El lock evita envios simultaneos dentro de Waitress. La idempotencia
    # remota cubre procesos distintos; la red ocurre fuera de transaction.atomic.
    with _CONSOLIDACION_LOCAL_LOCK:
        with transaction.atomic():
            type(sucursal).objects.select_for_update().get(pk=sucursal.pk)
            consolidacion = _obtener_consolidacion_bloqueada(sucursal, periodo)
            consolidacion.refresh_from_db()

            if consolidacion.estado == ConsolidacionMensual.Estado.PURGADA:
                return consolidacion
            if consolidacion.estado == ConsolidacionMensual.Estado.CONCILIACION:
                raise ErrorVenta(
                    "La consolidacion requiere conciliacion; no se genero otro identificador."
                )
            if consolidacion.estado == ConsolidacionMensual.Estado.CONFIRMADA:
                if consolidacion.purgado_en is None:
                    _purgar_periodo(sucursal, consolidacion)
                consolidacion.refresh_from_db()
                return consolidacion

            if not consolidacion.payload_inmutable:
                totales = construir_totales(sucursal, periodo)
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
                consolidacion.totales = totales
                consolidacion.payload_inmutable = payload
                consolidacion.payload_hash = _payload_hash(payload)
            else:
                payload = consolidacion.payload_inmutable
                if _payload_hash(payload) != consolidacion.payload_hash:
                    consolidacion.estado = ConsolidacionMensual.Estado.CONCILIACION
                    consolidacion.ultimo_error = (
                        "El payload mensual local no coincide con su hash inmutable."
                    )
                    consolidacion.save(update_fields=["estado", "ultimo_error"])
                    raise ErrorVenta(
                        "La consolidacion requiere conciliacion por integridad local."
                    )
            consolidacion.intentos += 1
            consolidacion.estado = ConsolidacionMensual.Estado.PENDIENTE
            consolidacion.ultimo_error = ""
            consolidacion.save(
                update_fields=[
                    "totales",
                    "payload_inmutable",
                    "payload_hash",
                    "intentos",
                    "estado",
                    "ultimo_error",
                ]
            )
            payload = dict(consolidacion.payload_inmutable)
            hash_esperado = consolidacion.payload_hash

        try:
            acuse, estado_vps = _enviar_vps(payload)
        except ErrorConsolidacionHTTP as exc:
            with transaction.atomic():
                actual = ConsolidacionMensual.objects.select_for_update().get(
                    pk=consolidacion.pk
                )
                if exc.status in {409, 422}:
                    actual.estado = ConsolidacionMensual.Estado.CONCILIACION
                else:
                    actual.estado = ConsolidacionMensual.Estado.ERROR
                actual.ultimo_error = str(exc)
                actual.save(update_fields=["estado", "ultimo_error"])
            raise ErrorVenta(str(exc)) from exc
        except ErrorVenta as exc:
            with transaction.atomic():
                actual = ConsolidacionMensual.objects.select_for_update().get(
                    pk=consolidacion.pk
                )
                actual.estado = ConsolidacionMensual.Estado.ERROR
                actual.ultimo_error = str(exc)
                actual.save(update_fields=["estado", "ultimo_error"])
            raise

        with transaction.atomic():
            actual = ConsolidacionMensual.objects.select_for_update().get(
                pk=consolidacion.pk
            )
            if actual.estado == ConsolidacionMensual.Estado.CONFIRMADA and actual.purgado_en:
                return actual
            if (
                actual.payload_hash != hash_esperado
                or actual.payload_inmutable != payload
                or _payload_hash(actual.payload_inmutable) != hash_esperado
            ):
                actual.estado = ConsolidacionMensual.Estado.CONCILIACION
                actual.ultimo_error = "El payload cambio mientras se esperaba el ACK."
                actual.save(update_fields=["estado", "ultimo_error"])
                raise ErrorVenta(
                    "La consolidacion requiere conciliacion por una carrera de integridad."
                )
            actual.estado = ConsolidacionMensual.Estado.CONFIRMADA
            actual.acuse_vps = acuse
            actual.estado_vps = estado_vps
            actual.confirmado_en = timezone.now()
            actual.ultimo_error = ""
            actual.save(
                update_fields=[
                    "estado",
                    "acuse_vps",
                    "estado_vps",
                    "confirmado_en",
                    "ultimo_error",
                ]
            )
            _purgar_periodo(sucursal, actual)
        actual.refresh_from_db()
        return actual
