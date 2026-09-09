"""Prepara un host pywin32 que no depende del PATH heredado por services.exe."""

from __future__ import annotations

import ctypes
from importlib.metadata import distribution
import os
from pathlib import Path
import shutil
import subprocess
import sys


class ServiceHostError(RuntimeError):
    pass


def _archivos_del_host(root: Path) -> list[tuple[Path, Path]]:
    installed = distribution("pywin32")
    packaged_host = Path(installed.locate_file("win32/pythonservice.exe"))
    # Python 3.13 calcula la raiz del venv como el padre del directorio del EXE.
    host = root / "Scripts" / "pythonservice.exe"
    # pywin32 anterior pudo mover su ejecutable fuera de site-packages.
    host_source = next(
        (path for path in (packaged_host, host, root / "pythonservice.exe") if path.is_file()),
        packaged_host,
    )
    buffer = ctypes.create_unicode_buffer(32768)
    kernel = ctypes.windll.kernel32
    kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
    kernel.GetModuleFileNameW.restype = ctypes.c_uint32
    if not kernel.GetModuleFileNameW(sys.dllhandle, buffer, len(buffer)):
        raise ServiceHostError("No se pudo localizar la DLL de Python cargada.")
    python_dll = Path(buffer.value)
    pywintypes_name = python_dll.name.replace("python", "pywintypes", 1)
    pywintypes_dll = Path(installed.locate_file("pywin32_system32/" + pywintypes_name))
    files = [(host_source, host), (python_dll, host.parent / python_dll.name),
             (pywintypes_dll, host.parent / pywintypes_name)]
    files.extend((path, host.parent / path.name) for path in Path(sys.base_prefix).glob("vcruntime*.dll"))
    return files


def comprobar_host(host: Path) -> None:
    windows = os.environ["SystemRoot"]
    environment = {
        "SystemRoot": windows,
        "WINDIR": windows,
        "PATH": windows + r"\System32;" + windows,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    kernel = ctypes.windll.kernel32
    previous = kernel.SetErrorMode(0x0001 | 0x0002 | 0x8000)
    try:
        try:
            result = subprocess.run(
                [str(host), "-?"], cwd=windows, env=environment, capture_output=True,
                timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            raise ServiceHostError("El host de servicio no respondio al preflight en 15 segundos.") from exc
    finally:
        kernel.SetErrorMode(previous)
    # Fuera del SCM, pywin32 muestra su ayuda y termina con codigo 2.
    markers = (b"Python Service Manager", "Python Service Manager".encode("utf-16-le"))
    if result.returncode != 2 or not any(marker in result.stdout for marker in markers):
        code = f"0x{result.returncode & 0xffffffff:08X}"
        raise ServiceHostError(
            f"El host no cargo Python/pywin32 (codigo {code}). "
            "Comprueba las DLL junto a .venv/Scripts/pythonservice.exe; no se inicio el servicio."
        )


def preparar_host() -> Path:
    if os.name != "nt" or sys.prefix == sys.base_prefix:
        raise ServiceHostError("Ejecuta este preflight con el Python virtual de Windows.")
    root = Path(sys.prefix).resolve()
    files = _archivos_del_host(root)
    missing = [source.name for source, _ in files if not source.is_file()]
    if missing:
        raise ServiceHostError("Faltan archivos del runtime: " + ", ".join(missing))
    for source, target in files:
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
    host = root / "Scripts" / "pythonservice.exe"
    comprobar_host(host)
    return host
