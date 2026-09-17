"""Bootstrap neutral de las posiciones operativas de una sucursal."""

from django.db import transaction

from .models import Mesa


POSICIONES_OPERATIVAS = (
    (Mesa.Canal.COMEDOR, "MESA", "Mesa", 24),
    (Mesa.Canal.DOMICILIO, "DOM", "Dom. #", 100),
    (Mesa.Canal.LLEVAR, "LLEV", "Llevar", 40),
    (Mesa.Canal.RECOGER, "REC", "Recoger", 40),
)


@transaction.atomic
def inicializar_posiciones_operativas(sucursal):
    """Crea o normaliza las casillas base sin sembrar catálogo ni sucursales."""

    total = 0
    for canal, prefijo, etiqueta, cantidad in POSICIONES_OPERATIVAS:
        for numero in range(1, cantidad + 1):
            Mesa.objects.update_or_create(
                sucursal=sucursal,
                clave=f"{prefijo}-{numero}",
                defaults={
                    "canal": canal,
                    "nombre": f"{etiqueta} {numero}",
                    "orden": numero,
                    "cliente_sucursal": None,
                    "activa": True,
                },
            )
            total += 1
    return total