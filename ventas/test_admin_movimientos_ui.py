import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path

from django.test import SimpleTestCase


RAIZ_VENTAS = Path(__file__).resolve().parent
PLANTILLA_ADMIN = RAIZ_VENTAS / "templates" / "ventas" / "administrador.html"
CSS_ADMIN = RAIZ_VENTAS / "static" / "ventas" / "admin.css"
JS_ADMIN = RAIZ_VENTAS / "static" / "ventas" / "admin.js"
JS_POS = RAIZ_VENTAS / "static" / "ventas" / "app.js"


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

    def test_cascada_final_de_movimientos_preserva_una_columna(self):
        estilos = CSS_ADMIN.read_text(encoding="utf-8")
        reticula_escritorio = (
            "grid-template-columns: minmax(250px, .68fr) "
            "minmax(0, 1.5fr);"
        )
        self.assertEqual(estilos.count(reticula_escritorio), 1)
        indice_escritorio = estilos.index(reticula_escritorio)
        indice_contenedor = estilos.rindex(
            "@container movimientos (max-width: 780px)"
        )
        indice_viewport = estilos.rindex("@media (max-width: 760px)")
        self.assertLess(indice_escritorio, indice_contenedor)
        self.assertLess(indice_escritorio, indice_viewport)

        cascada_final = estilos[indice_contenedor:]
        self.assertIn(
            ".movimientos-bandeja,\n"
            "  .control-efectivo-grid { "
            "grid-template-columns: minmax(0, 1fr); }",
            cascada_final,
        )
        for selector in (
            ".movimientos-bandeja > *",
            ".control-efectivo-grid > *",
            ".movimiento-campos > *",
            ".denominaciones-grid > *",
            ".ventas-apps > *",
        ):
            self.assertIn(selector, estilos)
        self.assertIn(
            "repeat(auto-fit, minmax(min(120px, 100%), 1fr))",
            estilos,
        )

    def test_origen_administrador_es_por_pestana_y_solo_movimientos_vuelve_a_ventas(
        self,
    ):
        javascript_pos = JS_POS.read_text(encoding="utf-8")
        javascript_admin = JS_ADMIN.read_text(encoding="utf-8")
        plantilla = PLANTILLA_ADMIN.read_text(encoding="utf-8")
        ruta_js = json.dumps(str(JS_POS))
        resultado = subprocess.run(
            [
                "node",
                "-e",
                (
                    'const assert=require("node:assert/strict");'
                    f"const core=require({ruta_js});"
                    'assert.equal(core.origenAdministradorDesdePanel("movimientos"),'
                    '"ventas");'
                    'assert.equal(core.origenAdministradorDesdePanel(""),"inicio");'
                    'assert.equal(core.origenAdministradorDesdePanel("pedidos"),'
                    '"inicio");'
                ),
            ],
            cwd=RAIZ_VENTAS.parent,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertIn(
            "sessionStorage.setItem("
            "CLAVE_ORIGEN_ADMIN, origenAdministradorDesdePanel(panel)",
            javascript_pos,
        )
        self.assertIn(
            'sessionStorage.getItem(CLAVE_ORIGEN_ADMIN) === "ventas"',
            javascript_admin,
        )
        self.assertIn('await api("/api/operador/salir/"', javascript_admin)
        self.assertIn("if (!vuelveAVentas)", javascript_admin)
        self.assertGreaterEqual(plantilla.count("data-regreso-admin"), 2)
        self.assertIn("data-regreso-admin-texto", plantilla)

    def test_personal_revalida_sesion_y_no_cambia_de_panel_silenciosamente(self):
        javascript = JS_ADMIN.read_text(encoding="utf-8")
        inicio_mostrar = javascript.index("  function mostrarPanel(nombre)")
        fin_mostrar = javascript.index(
            "  async function navegarPanel(nombre)", inicio_mostrar
        )
        mostrar = javascript[inicio_mostrar:fin_mostrar]
        inicio_navegar = fin_mostrar
        fin_navegar = javascript.index(
            "  function aplicarPermisosAdministrativos()", inicio_navegar
        )
        navegar = javascript[inicio_navegar:fin_navegar]

        self.assertNotIn('nombre = "inicio"', mostrar)
        self.assertIn(
            'toast("Tu acceso administrativo no permite abrir esta sección.", true)',
            mostrar,
        )
        self.assertIn("const actualizado = await cargarResumen(false);", navegar)
        self.assertIn("if (!actualizado) return false;", navegar)
        self.assertIn("if (!elementoPermitido(destino))", navegar)
        self.assertIn(
            "if (error.status !== 401 || !(await autorizarEntrada()))",
            javascript,
        )
        self.assertIn("await regresarDesdeAdministrador();", javascript)

    def test_admin_intenta_pantalla_completa_en_arranque_y_primer_gesto(self):
        javascript = JS_ADMIN.read_text(encoding="utf-8")
        for contrato in (
            'window.matchMedia?.("(display-mode: fullscreen)")?.matches',
            'window.matchMedia?.("(display-mode: standalone)")?.matches',
            "window.navigator.standalone",
            'boton.setAttribute("aria-pressed", String(activo));',
            'document.addEventListener("pointerdown", '
            "activarPantallaCompletaAdminConPrimerToque, true);",
            "iniciarPantallaCompletaAdmin();",
        ):
            self.assertIn(contrato, javascript)

    def test_seleccion_masiva_solo_abarca_cobrables_y_envia_forma_de_pago(self):
        plantilla = PLANTILLA_ADMIN.read_text(encoding="utf-8")
        javascript = JS_ADMIN.read_text(encoding="utf-8")
        estilos = CSS_ADMIN.read_text(encoding="utf-8")

        self.assertIn('id="lote-forma-pago"', plantilla)
        self.assertIn('<option value="efectivo">Efectivo</option>', plantilla)
        self.assertIn('<option value="tarjeta">Terminal</option>', plantilla)
        self.assertIn('data-seleccionar-canal="', javascript)
        self.assertIn(
            '.filter(ticket => tipoLote(ticket) === "cobrar")',
            javascript,
        )
        self.assertIn("function ticketsCobrablesCanal(canal)", javascript)
        self.assertIn(
            'if (tipoLote(buscarTicket(id)) !== "cobrar")',
            javascript,
        )
        self.assertIn(
            'cuerpo.forma_pago = $("#lote-forma-pago").value;',
            javascript,
        )
        self.assertIn(
            'if (!["efectivo", "tarjeta"].includes(cuerpo.forma_pago))',
            javascript,
        )
        self.assertIn(".mapa-grupo-encabezado {", estilos)
        self.assertIn(".mapa-grupo-seleccion {", estilos)
        self.assertIn("bottom: max(8px, env(safe-area-inset-bottom));", estilos)
