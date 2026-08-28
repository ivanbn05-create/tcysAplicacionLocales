from io import BytesIO

from PIL import Image
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from .models import TAMANO_MAXIMO_IMAGEN_PRODUCTO, validar_imagen_producto


class SeguridadImagenProductoTests(SimpleTestCase):
    def _archivo(self, nombre, contenido):
        return SimpleUploadedFile(nombre, contenido, content_type="application/octet-stream")

    def test_rechaza_archivo_que_supera_el_limite_antes_de_decodificar(self):
        archivo = self._archivo(
            "demasiado-grande.webp",
            b"x" * (TAMANO_MAXIMO_IMAGEN_PRODUCTO + 1),
        )

        with self.assertRaisesMessage(ValidationError, "no puede superar 1 MB"):
            validar_imagen_producto(archivo)
        self.assertEqual(archivo.tell(), 0)

    def test_rechaza_dimension_extrema_aunque_el_png_sea_pequeno(self):
        contenido = BytesIO()
        Image.new("1", (4097, 1), 0).save(contenido, format="PNG")
        archivo = self._archivo("panoramica.png", contenido.getvalue())

        with self.assertRaisesMessage(ValidationError, "dimensiones máximas"):
            validar_imagen_producto(archivo)

    def test_rechaza_presupuesto_de_pixeles_excesivo(self):
        contenido = BytesIO()
        Image.new("1", (3000, 3000), 0).save(contenido, format="PNG")
        archivo = self._archivo("comprimida.png", contenido.getvalue())

        with self.assertRaisesMessage(ValidationError, "dimensiones máximas"):
            validar_imagen_producto(archivo)

    def test_rechaza_webp_animado(self):
        contenido = BytesIO()
        primero = Image.new("RGB", (16, 16), "red")
        segundo = Image.new("RGB", (16, 16), "green")
        primero.save(
            contenido,
            format="WEBP",
            save_all=True,
            append_images=[segundo],
            duration=100,
            loop=0,
        )
        archivo = self._archivo("animado.webp", contenido.getvalue())

        with self.assertRaisesMessage(ValidationError, "un solo cuadro"):
            validar_imagen_producto(archivo)

    def test_acepta_webp_estatico_pequeno(self):
        contenido = BytesIO()
        Image.new("RGB", (320, 240), "white").save(contenido, format="WEBP")
        archivo = self._archivo("producto.webp", contenido.getvalue())

        validar_imagen_producto(archivo)
        self.assertEqual(archivo.tell(), 0)
