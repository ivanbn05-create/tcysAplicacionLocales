"""Imágenes locales de respaldo para el catálogo operativo.

Las fotografías provienen de los recursos autorizados del sitio de Los Tocayos.
Una imagen cargada en ``Producto.imagen`` siempre tiene prioridad sobre este mapa.
"""

from types import MappingProxyType

from django.templatetags.static import static
from django.urls import reverse


RUTA_MENU_ESTATICO = "ventas/menu"
IMAGEN_MENU_PREDETERMINADA = "mainlogo.webp"

# El mapa usa el código estable del catálogo, no el nombre visible. Varias claves
# comparten deliberadamente un único archivo para evitar duplicados binarios.
IMAGEN_MENU_POR_CODIGO = MappingProxyType(
    {
        "TB": "tacosdorados.webp",
        "TBQ": "tacoconqueso.webp",
        "TPLB": "tacoplanchado.webp",
        "TPLBQ": "tacoplanchadoconqueso.webp",
        "TBI": "bistek.webp",
        "TBIQ": "tacobistecconqueso.webp",
        "TC": "tacochorizo.webp",
        "TCQ": "tacochorizoconqueso.webp",
        "PB": "tacosyagua.webp",
        "PL": "loncheytaco.webp",
        "P4": "tacospreparados.webp",
        "PK": "bistek.webp",
        "BBQ05": "barbacoaservida.webp",
        "BBQ1": "barbacoaservida.webp",
        "CO8": "consomevaso.webp",
        "CO05": "consomemediolitro.webp",
        "CO1": "consomelitro.webp",
        # Los lonches sin queso usan la foto de su variante normal.
        "LB": "lonche4.webp",
        "LOBQ": "lonche4.webp",
        "LOKSQ": "lonche-bistec.webp",
        "LBIQ": "lonche-bistec.webp",
        "LOCSQ": "lonchechorizo.webp",
        "LCQ": "lonchechorizo.webp",
        "GRB": "gringa.webp",
        "GRBI": "gringabistec.webp",
        "GRC": "gringachorizo.webp",
        # Todas las aguas frescas comparten la fotografía aprobada del sitio.
        "HR05": "aguasfrescas.webp",
        "HR1": "aguasfrescas.webp",
        "HB05": "aguasfrescas.webp",
        "HB1": "aguasfrescas.webp",
        "JAM05": "aguasfrescas.webp",
        "JAM1": "aguasfrescas.webp",
        # Por indicación operativa, todos los refrescos usan Coca-Cola.
        "CC": "refresco.webp",
        "CCL": "refresco.webp",
        "CCSA": "refresco.webp",
        "FANTA": "refresco.webp",
        "SPRITE": "refresco.webp",
        "SPRITESA": "refresco.webp",
        "SIDRAL": "refresco.webp",
        "AM": "agua-mineral.webp",
    }
)


def archivo_imagen_menu(codigo):
    """Devuelve el archivo compartido asignado o el logotipo de respaldo."""

    return IMAGEN_MENU_POR_CODIGO.get(str(codigo).strip().upper(), IMAGEN_MENU_PREDETERMINADA)


def imagen_producto_url(producto):
    """Resuelve la miniatura privada o una imagen estática disponible sin internet."""

    if producto.imagen:
        return reverse("ventas:imagen_producto", args=[producto.id])
    return static(f"{RUTA_MENU_ESTATICO}/{archivo_imagen_menu(producto.codigo)}")
