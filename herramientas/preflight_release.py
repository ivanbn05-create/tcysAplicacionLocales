"""Preflight de solo lectura para construir una release del servidor Edge.

Este comando no ejecuta pruebas ni construye artefactos. Congela la identidad que
deben reutilizar los dos builds y comprueba los contratos que fallaron entre
0.4.0-dev.4 y 0.4.0-dev.6: VERSION como fuente unica, propagacion a la PWA y
presencia de VERSION en el snapshot efimero de despliegue.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import tokenize
from pathlib import Path, PurePosixPath


PATRON_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")
PATRON_COMMIT = re.compile(r"^[0-9a-f]{40}$")

CONTRATOS_TEXTO = {
    "pos/version.py": (
        '_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"',
        "APP_VERSION = _VERSION_FILE.read_text",
    ),
    "ventas/views.py": (
        "from pos.version import APP_VERSION",
        "ASSET_VERSION = APP_VERSION",
        'PWA_CACHE = f"tocayos-pos-{ASSET_VERSION}"',
        "self.skipWaiting()",
        "self.clients.claim()",
        '"Cache-Control": "no-cache"',
    ),
    "personas/modulos.py": (
        "from pos.version import APP_VERSION",
        "VERSION_MODULOS = APP_VERSION",
    ),
    "herramientas/validar_despliegue.py": (
        'shutil.copyfile(source / "VERSION", snapshot / "VERSION")',
    ),
    "actualizar-laboratorio-desde-release.ps1": (
        "$stagedVersionPath = Join-Path $staging 'VERSION'",
        "$stagedVersion -cne $ExpectedVersion",
        "foreach ($relative in @('.env', '.venv', 'runtime', 'media', 'logs', 'backups'))",
    ),
    "instalar-servicio-lan.ps1": ("Assert-EnvironmentUnchanged",),
}


class ErrorPreflight(RuntimeError):
    pass


def _git(fuente: Path, *argumentos: str) -> str:
    resultado = subprocess.run(
        ["git", *argumentos],
        cwd=fuente,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if resultado.returncode:
        diagnostico = (resultado.stderr or resultado.stdout).strip()
        raise ErrorPreflight(
            f"Git no pudo ejecutar {' '.join(argumentos)}"
            + (f": {diagnostico}" if diagnostico else ".")
        )
    return resultado.stdout


def _leer_version(fuente: Path) -> str:
    ruta = fuente / "VERSION"
    try:
        contenido = ruta.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ErrorPreflight("VERSION no existe o no es UTF-8 legible.") from exc
    lineas = contenido.splitlines()
    if len(lineas) != 1 or lineas[0] != lineas[0].strip():
        raise ErrorPreflight("VERSION debe contener una sola linea, sin espacios laterales.")
    version = lineas[0]
    if not PATRON_VERSION.fullmatch(version):
        raise ErrorPreflight("VERSION no cumple el formato permitido para una release.")
    return version


def _validar_fuente_unica_version(fuente: Path, version: str) -> None:
    archivos = [linea for linea in _git(fuente, "ls-files").splitlines() if linea]
    versiones = sorted(
        ruta
        for ruta in archivos
        if PurePosixPath(ruta).name.casefold() == "version"
    )
    if versiones != ["VERSION"]:
        raise ErrorPreflight(
            "El arbol debe tener un unico archivo VERSION rastreado en la raiz."
        )
    version_commit = _git(fuente, "show", "HEAD:VERSION").strip()
    if version_commit != version:
        raise ErrorPreflight("VERSION del checkout no coincide con VERSION en HEAD.")


def _sin_comentarios_python(contenido: str, relativa: str) -> str:
    try:
        tokens = tokenize.generate_tokens(io.StringIO(contenido).readline)
        return tokenize.untokenize(
            token for token in tokens if token.type != tokenize.COMMENT
        )
    except (tokenize.TokenError, IndentationError) as exc:
        raise ErrorPreflight(f"{relativa} no contiene Python valido.") from exc


def _validar_contratos(fuente: Path) -> None:
    for relativa, fragmentos in CONTRATOS_TEXTO.items():
        ruta = fuente / relativa
        try:
            contenido = ruta.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ErrorPreflight(f"Falta el contrato requerido {relativa}.") from exc
        contenido_validable = (
            _sin_comentarios_python(contenido, relativa)
            if ruta.suffix == ".py"
            else contenido
        )
        faltantes = [
            fragmento
            for fragmento in fragmentos
            if fragmento not in contenido_validable
        ]
        if faltantes:
            raise ErrorPreflight(
                f"{relativa} ya no satisface el contrato de "
                "VERSION/PWA/despliegue/preservacion."
            )


def evaluar_preflight(
    fuente: Path,
    *,
    version_esperada: str | None = None,
    commit_esperado: str | None = None,
) -> dict[str, object]:
    try:
        fuente = fuente.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ErrorPreflight("La raiz fuente no existe o no se puede resolver.") from exc
    if not fuente.is_dir():
        raise ErrorPreflight("La raiz fuente no es un directorio.")

    version = _leer_version(fuente)
    if version_esperada is not None and version != version_esperada:
        raise ErrorPreflight(
            f"VERSION declara {version}, no la version esperada {version_esperada}."
        )

    raiz_git = Path(_git(fuente, "rev-parse", "--show-toplevel").strip()).resolve()
    if raiz_git != fuente:
        raise ErrorPreflight("--source debe apuntar exactamente a la raiz del repositorio.")
    estado = _git(fuente, "status", "--porcelain=v1", "--untracked-files=all")
    if estado:
        raise ErrorPreflight("El arbol Git contiene cambios o archivos sin rastrear.")

    commit = _git(fuente, "rev-parse", "--verify", "HEAD").strip().lower()
    if not PATRON_COMMIT.fullmatch(commit):
        raise ErrorPreflight("HEAD no produjo un commit SHA-1 completo.")
    if commit_esperado is not None:
        esperado = _git(
            fuente, "rev-parse", "--verify", f"{commit_esperado}^{{commit}}"
        ).strip().lower()
        if commit != esperado:
            raise ErrorPreflight(f"HEAD {commit} no coincide con {esperado}.")

    _validar_fuente_unica_version(fuente, version)
    _validar_contratos(fuente)
    epoch_texto = _git(fuente, "show", "-s", "--format=%ct", "HEAD").strip()
    try:
        epoch = int(epoch_texto)
    except ValueError as exc:
        raise ErrorPreflight("Git no produjo un SOURCE_DATE_EPOCH valido.") from exc
    if epoch <= 0:
        raise ErrorPreflight("SOURCE_DATE_EPOCH debe ser positivo.")
    rama = _git(fuente, "branch", "--show-current").strip() or "(detached)"
    return {
        "branch": rama,
        "commit": commit,
        "source": str(fuente),
        "source_date_epoch": epoch,
        "status": "ok",
        "version": version,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Valida identidad y limpieza antes de construir dos releases."
    )
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("--expected-version")
    parser.add_argument("--expected-commit")
    return parser


def main(argumentos: list[str] | None = None) -> int:
    opciones = _parser().parse_args(argumentos)
    try:
        resultado = evaluar_preflight(
            opciones.source,
            version_esperada=opciones.expected_version,
            commit_esperado=opciones.expected_commit,
        )
    except (ErrorPreflight, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(resultado, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
