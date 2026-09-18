"""Identidad única de versión para runtime, módulos y caché PWA."""

from pathlib import Path


_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"
APP_VERSION = _VERSION_FILE.read_text(encoding="utf-8-sig").strip()
if not APP_VERSION:
    raise RuntimeError("VERSION no puede estar vacío.")