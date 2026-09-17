"""Solicitudes persistentes y acotadas para purgas físicas reintentables."""

from __future__ import annotations

import json
import os
import stat
import uuid
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.utils import timezone


VERSION_SOLICITUD = 1
TIPO_ARCHIVOS = "archivos_impresion"
TIPO_RESPALDOS = "respaldos_periodo"
REFERENCIA_CORTE = "corte"
REFERENCIA_CONSOLIDACION = "consolidacion"


class ErrorPurgaFisica(RuntimeError):
    """La solicitud no es válida o no pudo completarse físicamente."""


def raiz_solicitudes():
    return Path(
        getattr(
            settings,
            "PURGAS_PENDIENTES_ROOT",
            Path(settings.BASE_DIR) / "runtime" / "purgas-pendientes",
        )
    )


def _es_reparse(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & reparse
    )


def _preparar_raiz():
    root = raiz_solicitudes()
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir() or _es_reparse(root):
        raise ErrorPurgaFisica("La raíz de solicitudes no es una carpeta física.")
    return root.resolve()


def _escribir_json_atomico(path, payload, *, exclusivo=False):
    temporal = path.parent / f".{path.name}.{os.getpid()}-{uuid.uuid4().hex}.tmp"
    modo = "x" if exclusivo else "w"
    try:
        with temporal.open(modo, encoding="utf-8", newline="") as archivo:
            json.dump(payload, archivo, ensure_ascii=False, sort_keys=True)
            archivo.write("\n")
            archivo.flush()
            os.fsync(archivo.fileno())
        if exclusivo and path.exists():
            raise FileExistsError(path)
        os.replace(temporal, path)
    finally:
        temporal.unlink(missing_ok=True)


def _normalizar_rutas(rutas):
    resultado = []
    vistos = set()
    for valor in rutas:
        texto = str(valor or "").strip().replace("\\", "/")
        if not texto:
            continue
        relativa = PurePosixPath(texto)
        if relativa.is_absolute() or ".." in relativa.parts or "." in relativa.parts:
            continue
        normalizada = "/".join(relativa.parts)
        if ":" in relativa.parts[0] or normalizada in vistos:
            continue
        vistos.add(normalizada)
        resultado.append(normalizada)
    return sorted(resultado)


def _crear_solicitud(payload):
    root = _preparar_raiz()
    solicitud_id = uuid.uuid4()
    completo = {
        "version": VERSION_SOLICITUD,
        "id": str(solicitud_id),
        "creada_en": timezone.now().isoformat(),
        "intentos": 0,
        "ultimo_error": "",
        **payload,
    }
    path = root / f"purga-{solicitud_id}.json"
    _escribir_json_atomico(path, completo, exclusivo=True)
    return path


def crear_solicitud_archivos(rutas, *, referencia_tipo, referencia_id):
    rutas = _normalizar_rutas(rutas)
    if not rutas:
        return None
    if referencia_tipo not in {REFERENCIA_CORTE, REFERENCIA_CONSOLIDACION}:
        raise ErrorPurgaFisica("La referencia de la purga de archivos no es válida.")
    return _crear_solicitud(
        {
            "tipo": TIPO_ARCHIVOS,
            "referencia_tipo": referencia_tipo,
            "referencia_id": str(referencia_id),
            "rutas": rutas,
        }
    )


def crear_solicitud_respaldos(consolidacion, *, rutas_archivos=()):
    rutas_archivos = _normalizar_rutas(rutas_archivos)
    return _crear_solicitud(
        {
            "tipo": TIPO_RESPALDOS,
            "referencia_tipo": REFERENCIA_CONSOLIDACION,
            "referencia_id": str(consolidacion.pk),
            "sucursal_id": str(consolidacion.sucursal_id),
            "periodo": consolidacion.periodo.isoformat(),
            # Acreditación durable aunque la solicitud hermana se pierda.
            "rutas_archivos": rutas_archivos,
        }
    )


def _cargar_solicitud(path):
    root = _preparar_raiz()
    path = Path(path)
    try:
        path = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ErrorPurgaFisica("La solicitud pendiente ya no existe.") from exc
    if path.parent != root or not path.name.startswith("purga-") or path.suffix != ".json":
        raise ErrorPurgaFisica("La solicitud queda fuera de la raíz admitida.")
    if _es_reparse(path) or not path.is_file():
        raise ErrorPurgaFisica("La solicitud no es un archivo físico directo.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ErrorPurgaFisica("La solicitud pendiente no contiene JSON válido.") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("version") != VERSION_SOLICITUD
        or payload.get("id") != path.stem.removeprefix("purga-")
    ):
        raise ErrorPurgaFisica("La solicitud pendiente no cumple el contrato.")
    try:
        uuid.UUID(str(payload["id"]))
        uuid.UUID(str(payload["referencia_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ErrorPurgaFisica("La solicitud contiene identificadores inválidos.") from exc
    return path, payload


def _validar_referencia(payload):
    from .models import ConsolidacionMensual, CorteCaja

    tipo = payload.get("referencia_tipo")
    referencia_id = payload["referencia_id"]
    if tipo == REFERENCIA_CORTE:
        return CorteCaja.objects.filter(
            pk=referencia_id,
            detalle_eliminado_en__isnull=False,
        ).exists()
    if tipo == REFERENCIA_CONSOLIDACION:
        return ConsolidacionMensual.objects.filter(
            pk=referencia_id,
            estado__in=[
                ConsolidacionMensual.Estado.CONFIRMADA,
                ConsolidacionMensual.Estado.PURGADA,
            ],
            acuse_vps__gt="",
            confirmado_en__isnull=False,
            purgado_en__isnull=False,
        ).exists()
    return False


def _ruta_media_segura(relativa):
    raiz = Path(settings.MEDIA_ROOT).resolve()
    partes = PurePosixPath(relativa).parts
    if not partes or ".." in partes or "." in partes or ":" in partes[0]:
        raise ErrorPurgaFisica("Una ruta de impresión no es relativa y acotada.")
    ruta = raiz.joinpath(*partes)
    resuelta = ruta.resolve(strict=False)
    try:
        dentro = resuelta.is_relative_to(raiz)
    except AttributeError:
        dentro = resuelta == raiz or raiz in resuelta.parents
    if not dentro:
        raise ErrorPurgaFisica("Una ruta de impresión sale de MEDIA_ROOT.")
    actual = raiz
    for parte in partes:
        actual = actual / parte
        if actual.exists() and _es_reparse(actual):
            raise ErrorPurgaFisica("No se purgan enlaces ni puntos de reanálisis.")
    if ruta.exists() and not ruta.is_file():
        raise ErrorPurgaFisica("La ruta de impresión no es un archivo físico.")
    return ruta


def _registrar_error(path, payload, exc):
    payload = dict(payload)
    payload["intentos"] = int(payload.get("intentos") or 0) + 1
    payload["ultimo_error"] = f"{type(exc).__name__}: {exc}"[:500]
    payload["ultimo_intento_en"] = timezone.now().isoformat()
    try:
        _escribir_json_atomico(path, payload)
    except OSError:
        pass


def solicitudes_archivos_cortes(corte_ids):
    """Devuelve rutas/solicitudes diarias que una purga mensual debe absorber."""

    ids = {str(valor) for valor in corte_ids}
    rutas = []
    solicitudes = []
    root = _preparar_raiz()
    for path in sorted(root.glob("purga-*.json")):
        path, payload = _cargar_solicitud(path)
        if (
            payload.get("tipo") == TIPO_ARCHIVOS
            and payload.get("referencia_tipo") == REFERENCIA_CORTE
            and payload.get("referencia_id") in ids
        ):
            candidatas = payload.get("rutas")
            if (
                not isinstance(candidatas, list)
                or candidatas != _normalizar_rutas(candidatas)
            ):
                raise ErrorPurgaFisica(
                    "Una solicitud diaria que debe absorberse no es canónica."
                )
            rutas.extend(candidatas)
            solicitudes.append(path)
    return _normalizar_rutas(rutas), solicitudes


def retirar_solicitudes_absorbidas(paths):
    root = _preparar_raiz()
    for value in paths:
        path, payload = _cargar_solicitud(value)
        if (
            path.parent != root
            or payload.get("tipo") != TIPO_ARCHIVOS
            or payload.get("referencia_tipo") != REFERENCIA_CORTE
        ):
            raise ErrorPurgaFisica("La solicitud absorbida cambió durante la purga.")
        path.unlink()


def procesar_solicitud_archivos(path, *, propagar=False):
    """Borra rutas seguras; ante fallo conserva la solicitud para reintentar."""

    try:
        path, payload = _cargar_solicitud(path)
        if payload.get("tipo") != TIPO_ARCHIVOS:
            raise ErrorPurgaFisica("La solicitud no corresponde a archivos de impresión.")
        rutas = payload.get("rutas")
        if not isinstance(rutas, list) or rutas != _normalizar_rutas(rutas):
            raise ErrorPurgaFisica("Las rutas de la solicitud no cumplen el contrato.")
        if not _validar_referencia(payload):
            raise ErrorPurgaFisica("La referencia aún no acredita la purga lógica.")
        for relativa in rutas:
            ruta = _ruta_media_segura(relativa)
            if ruta.exists():
                ruta.unlink()
        path.unlink()
        return True
    except (ErrorPurgaFisica, OSError) as exc:
        if "path" in locals() and "payload" in locals() and isinstance(payload, dict):
            _registrar_error(path, payload, exc)
        if propagar:
            raise
        return False