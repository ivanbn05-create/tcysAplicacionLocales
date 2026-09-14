import re


_CLAVE_SUCURSAL = re.compile(r"^[A-Z0-9](?:[A-Z0-9_-]{0,28}[A-Z0-9])?$")
_CARACTERES_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def normalizar_clave_sucursal(valor):
    """Devuelve una clave canónica apta para identificar una sucursal."""

    clave = str(valor or "").strip().upper()
    if not _CLAVE_SUCURSAL.fullmatch(clave):
        raise ValueError(
            "La clave de sucursal debe tener entre 1 y 30 caracteres; sólo "
            "puede contener letras A-Z, números, guion y guion bajo, y debe "
            "comenzar y terminar con una letra o un número."
        )
    return clave


def normalizar_nombre_sucursal(valor):
    """Devuelve un nombre visible válido sin alterar su capitalización."""

    nombre = str(valor or "").strip()
    if not nombre:
        raise ValueError("El nombre de sucursal es obligatorio.")
    if len(nombre) > 120:
        raise ValueError("El nombre de sucursal no puede exceder 120 caracteres.")
    if _CARACTERES_CONTROL.search(nombre):
        raise ValueError("El nombre de sucursal no puede contener caracteres de control.")
    return nombre
