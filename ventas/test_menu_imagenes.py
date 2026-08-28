from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase

from catalogo.models import Producto
from personas.models import Sucursal

from .menu_imagenes import (
    IMAGEN_MENU_PREDETERMINADA,
    IMAGEN_MENU_POR_CODIGO,
    RUTA_MENU_ESTATICO,
    archivo_imagen_menu,
    imagen_producto_url,
)


class ImagenesMenuTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("cargar_datos_iniciales", verbosity=0)
        cls.sucursal = Sucursal.objects.get(clave="ARBOLEDAS")

    def producto(self, codigo):
        return Producto.objects.get(sucursal=self.sucursal, codigo=codigo)

    def test_familias_comparten_la_imagen_solicitada(self):
        grupos = (
            ("LB", "LOBQ"),
            ("LOKSQ", "LBIQ"),
            ("LOCSQ", "LCQ"),
            ("HR05", "HR1", "HB05", "HB1", "JAM05", "JAM1"),
            ("CC", "CCL", "CCSA", "FANTA", "SPRITE", "SPRITESA", "SIDRAL"),
        )
        for codigos in grupos:
            archivos = {archivo_imagen_menu(codigo) for codigo in codigos}
            self.assertEqual(len(archivos), 1, codigos)

        self.assertEqual(archivo_imagen_menu("CC"), "refresco.webp")
        self.assertEqual(archivo_imagen_menu("HR05"), "aguasfrescas.webp")

    def test_articulo_sin_foto_usa_mainlogo_y_carga_posterior_tiene_prioridad(self):
        quesadilla = self.producto("QKH")
        self.assertNotIn(quesadilla.codigo, IMAGEN_MENU_POR_CODIGO)
        self.assertEqual(archivo_imagen_menu(quesadilla.codigo), IMAGEN_MENU_PREDETERMINADA)
        self.assertEqual(
            imagen_producto_url(quesadilla),
            f"/static/{RUTA_MENU_ESTATICO}/{IMAGEN_MENU_PREDETERMINADA}",
        )

        quesadilla.imagen = "productos/quesadilla.webp"
        self.assertEqual(
            imagen_producto_url(quesadilla),
            f"/catalogo/productos/{quesadilla.id}/imagen.webp",
        )

    def test_todo_el_catalogo_publica_una_imagen_local_existente(self):
        respuesta = self.client.get("/")
        self.assertEqual(respuesta.status_code, 200)
        productos = respuesta.context["productos"]
        self.assertEqual(len(productos), 47)

        raiz_estatica = Path(settings.BASE_DIR) / "ventas" / "static"
        for producto in productos:
            self.assertTrue(producto["imagen_url"].startswith("/static/ventas/menu/"))
            relativa = producto["imagen_url"].removeprefix("/static/")
            self.assertTrue((raiz_estatica / relativa).is_file(), producto["codigo"])

    def test_service_worker_precarga_catalogo_visual_para_operacion_offline(self):
        respuesta = self.client.get("/service-worker.js")
        self.assertEqual(respuesta.status_code, 200)
        codigo = respuesta.content.decode()
        archivos = set(IMAGEN_MENU_POR_CODIGO.values()) | {IMAGEN_MENU_PREDETERMINADA}
        for archivo in archivos:
            self.assertIn(f"/static/{RUTA_MENU_ESTATICO}/{archivo}", codigo)
