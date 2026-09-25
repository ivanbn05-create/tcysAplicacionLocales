"""Validación de destinos TCP de impresoras locales."""

import ipaddress
import re
import socket


_HOSTNAME = re.compile(
    r"(?=.{1,253}\Z)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\Z"
)
_RED_LOCAL = tuple(
    ipaddress.ip_network(red)
    for red in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "fc00::/7",
    )
)


def ip_lan(valor):
    direccion = ipaddress.ip_address(valor)
    if getattr(direccion, "ipv4_mapped", None):
        direccion = direccion.ipv4_mapped
    return any(direccion in red for red in _RED_LOCAL)


def normalizar_host_impresora(valor):
    if not isinstance(valor, str):
        raise ValueError("La dirección de la impresora debe ser texto.")
    host = valor.strip()
    if not host or host != valor or len(host) > 253:
        raise ValueError("Indica una IP LAN o un hostname de impresora válido.")
    try:
        direccion = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        if not _HOSTNAME.fullmatch(host) or host.casefold() == "localhost":
            raise ValueError("Indica una IP LAN o un hostname de impresora válido.")
        return host.casefold()
    if not ip_lan(direccion):
        raise ValueError("La IP de la impresora debe pertenecer a la red local.")
    return direccion.compressed


def resolver_ip_impresora(host, puerto):
    """Resuelve primero y sólo permite conexiones a IP locales."""
    host = normalizar_host_impresora(host)
    try:
        direcciones = socket.getaddrinfo(host, puerto, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise OSError("No se pudo resolver la impresora en la red local.") from exc
    ips = {entrada[4][0] for entrada in direcciones}
    if not ips or any(not ip_lan(ip) for ip in ips):
        raise OSError("La impresora no resuelve exclusivamente a direcciones LAN.")
    return sorted(ips)[0]


def sondear_recurso_impresora(impresora, *, timeout=2):
    """Verifica TCP sin enviar bytes a la impresora."""
    try:
        ip = resolver_ip_impresora(impresora.host, impresora.puerto)
        with socket.create_connection((ip, impresora.puerto), timeout=timeout):
            pass
    except OSError:
        return {
            "alcanzable": False,
            "mensaje": "Sin conexión TCP. Verifica red, IP y puerto; no se imprimió.",
        }
    return {
        "alcanzable": True,
        "mensaje": "Conexión TCP disponible; no se enviaron datos.",
    }
