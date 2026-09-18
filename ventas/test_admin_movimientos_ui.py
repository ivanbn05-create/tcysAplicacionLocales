from html.parser import HTMLParser
from pathlib import Path

from django.test import SimpleTestCase


RAIZ_VENTAS = Path(__file__).resolve().parent
PLANTILLA_ADMIN = RAIZ_VENTAS / "templates" / "ventas" / "administrador.html"
CSS_ADMIN = RAIZ_VENTAS / "static" / "ventas" / "admin.css"
JS_ADMIN = RAIZ_VENTAS / "static" / "ventas" / "admin.js"


class _RecolectorInputsNumericosMovimientos(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs = []
        self._en_movimientos = False
        self._profundidad_seccion = 0

    def handle_starttag(self, tag, attrs):
        atributos = dict(attrs)
        if tag == "section":
            if self._en_movimientos:
                self._profundidad_seccion += 1
            elif atributos.get("data-admin-panel") == "movimientos":
                self._en_movimientos = True
                self._profundidad_seccion = 1
        if (
            self._en_movimientos
            and tag == "input"
            and atributos.get("type") == "number"
        ):
            self.inputs.append(atributos)

    def handle_endtag(self, tag):
        if not self._en_movimientos or tag != "section":
            return
        self._profundidad_seccion -= 1
        if self._profundidad_seccion == 0:
            self._en_movimientos = False


class ContratoMovimientosAdministradorTests(SimpleTestCase):
    def test_inputs_numericos_de_movimientos_no_obligan_a_borrar_un_cero(self):
        plantilla = PLANTILLA_ADMIN.read_text(encoding="utf-8")
        recolector = _RecolectorInputsNumericosMovimientos()
        recolector.feed(plantilla)

        ids_movimientos = {
            "movimiento-importe",
            "app-rappi",
            "app-didi",
            "app-uber-eats",
        }
        entradas_movimientos = [
            entrada
            for entrada in recolector.inputs
            if entrada.get("id") in ids_movimientos
        ]

        self.assertEqual(
            {entrada.get("id") for entrada in entradas_movimientos},
            ids_movimientos,
        )
        self.assertTrue(
            all(entrada.get("value") is None for entrada in entradas_movimientos)
        )
        self.assertIn(
            "Los campos vacíos cuentan como cero y puedes guardarlos así.",
            plantilla,
        )

        javascript = JS_ADMIN.read_text(encoding="utf-8")
        self.assertIn("function valorNumericoEditable(valor)", javascript)
        self.assertNotIn("escapar(valores[denominacion] || 0)", javascript)
        for campo in ("app-rappi", "app-didi", "app-uber-eats"):
            self.assertIn(
                f'$("#{campo}").value = valorNumericoEditable(',
                javascript,
            )

    def test_movimientos_responde_al_ancho_real_de_su_bandeja(self):
        plantilla = PLANTILLA_ADMIN.read_text(encoding="utf-8")
        estilos = CSS_ADMIN.read_text(encoding="utf-8")

        self.assertIn(
            'class="admin-panel panel-movimientos" data-admin-panel="movimientos"',
            plantilla,
        )
        self.assertIn('class="movimientos-bandeja"', plantilla)
        self.assertIn('id="conteo-movimientos" aria-label="0 movimientos"', plantilla)
        self.assertIn("container-name: movimientos;", estilos)
        self.assertIn("container-name: lista-movimientos;", estilos)
        self.assertIn("@container movimientos (max-width: 780px)", estilos)
        self.assertIn("@container lista-movimientos (max-width: 560px)", estilos)
        self.assertIn(".movimiento-acciones .boton { min-height: 44px; }", estilos)