"""Capacidades de negocio del POS y plano de soporte técnico local."""

from django.conf import settings


class Capacidad:
    VENTAS = "ventas"
    COMANDAS = "comandas"
    MODIFICAR_PARTIDAS = "modificar_partidas"
    COBRAR = "cobrar"
    REIMPRIMIR = "reimprimir"
    CANCELAR = "cancelar"
    ADMINISTRAR_NEGOCIO = "administrar_negocio"
    GESTIONAR_USUARIOS = "gestionar_usuarios"
    CAMBIAR_PINES_AJENOS = "cambiar_pines_ajenos"
    REINICIAR_FOLIOS = "reiniciar_folios"
    SINCRONIZAR_PEDIDOS = "sincronizar_pedidos"
    SOPORTE_TECNICO = "soporte_tecnico"


CAPACIDADES_POR_TIPO = {
    "dueno": (
        Capacidad.VENTAS, Capacidad.COMANDAS, Capacidad.MODIFICAR_PARTIDAS,
        Capacidad.COBRAR, Capacidad.REIMPRIMIR, Capacidad.CANCELAR,
        Capacidad.ADMINISTRAR_NEGOCIO, Capacidad.GESTIONAR_USUARIOS,
        Capacidad.CAMBIAR_PINES_AJENOS, Capacidad.REINICIAR_FOLIOS,
        Capacidad.SINCRONIZAR_PEDIDOS,
    ),
    "encargado": (
        Capacidad.VENTAS, Capacidad.COMANDAS, Capacidad.MODIFICAR_PARTIDAS,
    ),
    "elevado": (
        Capacidad.VENTAS, Capacidad.COMANDAS, Capacidad.MODIFICAR_PARTIDAS,
        Capacidad.COBRAR, Capacidad.REIMPRIMIR, Capacidad.CANCELAR,
        Capacidad.ADMINISTRAR_NEGOCIO, Capacidad.SINCRONIZAR_PEDIDOS,
    ),
    "mesero": (
        Capacidad.VENTAS, Capacidad.COMANDAS, Capacidad.MODIFICAR_PARTIDAS,
    ),
    "repartidor": (),
}

CAPACIDADES_CONOCIDAS = frozenset(
    capacidad
    for capacidades in CAPACIDADES_POR_TIPO.values()
    for capacidad in capacidades
)


def capacidades_iniciales(tipo, *, puede_cobrar=False, puede_reimprimir=False,
                          puede_cancelar=False, puede_sincronizar=False):
    """Inicializa roles nuevos y conserva permisos explícitos legados."""
    capacidades = set(CAPACIDADES_POR_TIPO.get(tipo, ()))
    if tipo in {"dueno", "encargado", "elevado"}:
        for permitido, capacidad in (
            (puede_cobrar, Capacidad.COBRAR),
            (puede_reimprimir, Capacidad.REIMPRIMIR),
            (puede_cancelar, Capacidad.CANCELAR),
            (puede_sincronizar, Capacidad.SINCRONIZAR_PEDIDOS),
        ):
            if permitido:
                capacidades.add(capacidad)
    return sorted(capacidades)


def perfil_tiene_capacidad(perfil, capacidad):
    if perfil is None or not perfil.activo or perfil.es_sistema:
        return False
    return capacidad in (perfil.rol.capacidades or ())


def usuario_es_soporte_tecnico(usuario):
    return bool(
        usuario and usuario.is_authenticated and usuario.is_active
        and usuario.is_staff
        and usuario.groups.filter(name="soporte_tecnico_edge").exists()
    )


def tiene_capacidad(request, capacidad):
    """Evalúa la sesión actual; soporte nunca hereda capacidades de negocio."""
    if capacidad == Capacidad.SOPORTE_TECNICO:
        return usuario_es_soporte_tecnico(getattr(request, "user", None))
    if not getattr(settings, "POS_REQUIRE_AUTH", True):
        return True
    return perfil_tiene_capacidad(getattr(request, "pos_user", None), capacidad)
