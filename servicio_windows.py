"""Servicio de Windows que supervisa el proceso Waitress del POS."""

from __future__ import annotations

import os
import subprocess
from ipaddress import ip_address
from pathlib import Path

import servicemanager
import win32event
import win32service
import win32serviceutil


BASE_DIR = Path(__file__).resolve().parent
PYTHON = BASE_DIR / ".venv" / "Scripts" / "python.exe"
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "waitress.log"


def _cargar_entorno() -> None:
    ruta = BASE_DIR / ".env"
    if not ruta.is_file():
        return
    for linea in ruta.read_text(encoding="utf-8-sig").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        nombre, valor = linea.split("=", 1)
        nombre = nombre.strip()
        valor = valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in {'"', "'"}:
            valor = valor[1:-1]
        if nombre:
            os.environ.setdefault(nombre, valor)


def _entero_entorno(nombre: str, predeterminado: int, minimo: int, maximo: int) -> int:
    try:
        valor = int(os.getenv(nombre, str(predeterminado)))
    except ValueError as exc:
        raise RuntimeError(f"{nombre} debe ser un número entero.") from exc
    if not minimo <= valor <= maximo:
        raise RuntimeError(f"{nombre} debe estar entre {minimo} y {maximo}.")
    return valor


def _rotar_log() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    if not LOG_FILE.exists() or LOG_FILE.stat().st_size < 5 * 1024 * 1024:
        return
    anterior = LOG_FILE.with_suffix(".log.1")
    anterior.unlink(missing_ok=True)
    LOG_FILE.replace(anterior)


class ServicioTocayosPOS(win32serviceutil.ServiceFramework):
    _svc_name_ = "LosTocayosPOS"
    _svc_display_name_ = "Los Tocayos POS"
    _svc_description_ = "Servidor local Waitress del punto de venta Los Tocayos."

    def __init__(self, args):
        super().__init__(args)
        self._detener = win32event.CreateEvent(None, True, False, None)
        self._procesos: list[tuple[str, subprocess.Popen[bytes]]] = []
        self._log = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING, waitHint=15000)
        win32event.SetEvent(self._detener)

    def SvcShutdown(self):
        self.SvcStop()

    def SvcDoRun(self):
        servicemanager.LogInfoMsg("Los Tocayos POS inició Waitress.")
        try:
            self._ejecutar()
        except Exception as exc:
            # El detalle completo queda en un archivo con ACL restringida. El Visor de
            # eventos sólo recibe el tipo de fallo, nunca variables de entorno.
            servicemanager.LogErrorMsg(
                f"Los Tocayos POS se detuvo por {type(exc).__name__}. Consulte logs\\waitress.log."
            )
            raise
        finally:
            self._cerrar_proceso()

    def _ejecutar(self):
        if not PYTHON.is_file():
            raise FileNotFoundError("No se encontró el Python virtual del servicio.")

        _cargar_entorno()
        host = os.getenv("WAITRESS_HOST", "0.0.0.0").strip() or "0.0.0.0"
        try:
            direccion_host = ip_address(host)
        except ValueError as exc:
            raise RuntimeError("WAITRESS_HOST debe ser una dirección IP local concreta.") from exc
        if direccion_host.is_multicast:
            raise RuntimeError("WAITRESS_HOST no puede ser una dirección multicast.")
        https_enabled = os.getenv("DJANGO_HTTPS", "false").strip().lower() in {
            "1", "true", "si", "sí", "yes"
        }
        allow_insecure_http_lan = os.getenv(
            "ALLOW_INSECURE_HTTP_LAN", "false"
        ).strip().lower() in {"1", "true", "si", "sí", "yes"}
        if not https_enabled and not direccion_host.is_loopback and not allow_insecure_http_lan:
            raise RuntimeError(
                "HTTP fuera de loopback requiere ALLOW_INSECURE_HTTP_LAN=true."
            )
        port = _entero_entorno("WAITRESS_PORT", 8000, 1, 65535)
        threads = _entero_entorno("WAITRESS_THREADS", 8, 2, 64)
        connection_limit = _entero_entorno("WAITRESS_CONNECTION_LIMIT", 100, 10, 500)
        channel_timeout = _entero_entorno("WAITRESS_CHANNEL_TIMEOUT", 30, 5, 300)
        cleanup_interval = _entero_entorno("WAITRESS_CLEANUP_INTERVAL", 10, 1, 60)
        _rotar_log()
        self._log = LOG_FILE.open("ab", buffering=0)

        entorno = os.environ.copy()
        entorno["DJANGO_SETTINGS_MODULE"] = "pos.settings"
        entorno["PYTHONUNBUFFERED"] = "1"
        entorno["PYTHONDONTWRITEBYTECODE"] = "1"
        argumentos = [
            str(PYTHON),
            "-m",
            "waitress",
            f"--host={host}",
            f"--port={port}",
            f"--threads={threads}",
            "--ident=LosTocayosPOS",
            "--max-request-header-size=32768",
            "--max-request-body-size=1048576",
            f"--connection-limit={connection_limit}",
            f"--channel-timeout={channel_timeout}",
            f"--cleanup-interval={cleanup_interval}",
            "pos.wsgi:application",
        ]
        proxy_confiable = os.getenv("WAITRESS_TRUSTED_PROXY", "").strip()
        if proxy_confiable:
            try:
                direccion_proxy = ip_address(proxy_confiable)
            except ValueError as exc:
                raise RuntimeError("WAITRESS_TRUSTED_PROXY debe ser una dirección IP concreta.") from exc
            if not direccion_proxy.is_loopback:
                raise RuntimeError("WAITRESS_TRUSTED_PROXY debe ser una dirección loopback.")
            if not direccion_host.is_loopback:
                raise RuntimeError("Waitress debe escuchar en loopback al confiar en un proxy.")
            argumentos[-1:-1] = [
                f"--trusted-proxy={proxy_confiable}",
                "--trusted-proxy-count=1",
                "--trusted-proxy-headers=x-forwarded-for x-forwarded-proto",
                "--log-untrusted-proxy-headers",
            ]
        if https_enabled and (not direccion_host.is_loopback or not proxy_confiable):
            raise RuntimeError(
                "Con DJANGO_HTTPS activo, Waitress debe escuchar en loopback detrás del proxy."
            )
        waitress = subprocess.Popen(
            argumentos,
            cwd=BASE_DIR,
            env=entorno,
            stdin=subprocess.DEVNULL,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self._procesos.append(("Waitress", waitress))

        print_sync = os.getenv("PRINT_SYNC", "false").strip().lower() in {
            "1", "true", "si", "sí", "yes"
        }
        if not print_sync:
            worker = subprocess.Popen(
                [str(PYTHON), "manage.py", "procesar_impresiones"],
                cwd=BASE_DIR,
                env=entorno,
                stdin=subprocess.DEVNULL,
                stdout=self._log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self._procesos.append(("Worker de impresión", worker))

        while True:
            if win32event.WaitForSingleObject(self._detener, 1000) == win32event.WAIT_OBJECT_0:
                return
            for nombre, proceso in self._procesos:
                codigo = proceso.poll()
                if codigo is not None:
                    raise RuntimeError(f"{nombre} terminó inesperadamente con código {codigo}.")

    def _cerrar_proceso(self):
        for _, proceso in reversed(self._procesos):
            if proceso.poll() is None:
                proceso.terminate()
        for _, proceso in reversed(self._procesos):
            if proceso.poll() is None:
                try:
                    proceso.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    proceso.kill()
                    proceso.wait(timeout=3)
        self._procesos.clear()
        if self._log is not None:
            self._log.close()


if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(ServicioTocayosPOS)
