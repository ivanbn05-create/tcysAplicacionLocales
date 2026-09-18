from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class PosFrontendContractTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        static_dir = Path(settings.BASE_DIR) / "ventas" / "static" / "ventas"
        cls.javascript = (static_dir / "app.js").read_text(encoding="utf-8")
        cls.estilos = (static_dir / "app.css").read_text(encoding="utf-8")

    def test_comanda_virtual_prioriza_abreviatura_y_conserva_nombre_personalizado(self):
        inicio = self.javascript.index("  function nombrePartida(partida) {")
        fin = self.javascript.index("  function clavePartidaEdicion(partida) {", inicio)
        funcion = self.javascript[inicio:fin]

        self.assertIn("if (esPartidaPersonalizada(partida))", funcion)
        self.assertIn(
            'return String(partida?.nombre || partida?.nombre_corto || "Producto personalizado");',
            funcion,
        )
        self.assertIn(
            'return String(partida?.nombre_corto || partida?.nombre || "Producto");',
            funcion,
        )

    def test_producto_personalizable_cierra_catalogos_con_geometria_normal(self):
        inicio = self.javascript.index("    const accesoPersonalizado = soloBebidas")
        fin = self.javascript.index(
            "    configurarAtajosMenu(soloBebidas ? [] : secciones.map", inicio
        )
        catalogo = self.javascript[inicio:fin]

        self.assertIn(
            '<button class="producto producto-menu" data-producto-personalizado',
            catalogo,
        )
        self.assertIn('class="producto-imagen producto-imagen-vacia"', catalogo)
        self.assertIn('class="producto-copy"', catalogo)
        self.assertIn(
            '$("#productos").innerHTML = (seccionesHtml + accesoPersonalizado)',
            catalogo,
        )
        self.assertNotIn("producto producto-menu producto-personalizado", catalogo)
        self.assertNotIn(
            ".segmento-personalizado .segmento-productos", self.estilos
        )
        self.assertNotIn(".producto.producto-personalizado", self.estilos)

        inicio_sucursal = self.javascript.index(
            "      const catalogoSucursalHtml = "
        )
        fin_sucursal = self.javascript.index("      return;", inicio_sucursal)
        catalogo_sucursal = self.javascript[inicio_sucursal:fin_sucursal]
        self.assertIn(
            "${catalogoSucursalHtml}${accesoPersonalizadoSucursal}",
            catalogo_sucursal,
        )
        self.assertIn(
            '<button class="producto producto-sucursal" data-producto-personalizado',
            catalogo_sucursal,
        )
        self.assertNotIn(
            "producto producto-sucursal producto-personalizado",
            catalogo_sucursal,
        )

    def test_tableta_muestra_solo_mesas_y_autoenvia_el_cuarto_digito(self):
        render_inicio = self.javascript.index("  function renderPosiciones() {")
        normalizacion = self.javascript.index(
            '    if (modoTableta && estado.canal !== "comedor") {',
            render_inicio,
        )
        sucursales = self.javascript.index(
            '    if (estado.canal === "sucursales") {',
            render_inicio,
        )
        self.assertLess(normalizacion, sucursales)
        self.assertIn(
            'estado.canal = "comedor";',
            self.javascript[normalizacion:sucursales],
        )

        inicio = self.javascript.index(
            '    if (modoTableta) {\n      const tarjetasComedor',
            sucursales,
        )
        fin = self.javascript.index(
            '    const secundario = estado.canal === "comedor"', inicio
        )
        tableta = self.javascript[inicio:fin]
        teclado_inicio = self.javascript.index(
            "  function manejarTeclaClaveTableta(tecla) {"
        )
        teclado_fin = self.javascript.index(
            "  function pedirFormaPago() {", teclado_inicio
        )
        teclado = self.javascript[teclado_inicio:teclado_fin]

        self.assertIn('contenedor.innerHTML = tarjetasComedor ||', tableta)
        self.assertNotIn('renderTarjetas("llevar")', tableta)
        self.assertIn('if (!estado.pinTabletaActivo)', tableta)
        self.assertIn(
            '$("#rejilla-posiciones [data-tecla-pin-tableta]")?.focus()',
            self.javascript,
        )
        self.assertNotIn('data-tecla-pin-tableta="confirmar"', tableta)
        self.assertNotIn(">OK</button>", tableta)
        self.assertIn('aria-busy="${String(estado.pinTabletaEnviando)}"', tableta)
        self.assertIn('role="status" aria-live="polite"', tableta)
        self.assertIn("if (estado.claveTableta.length === 4)", teclado)
        self.assertIn("resolverClavePos(estado.claveTableta)", teclado)
        self.assertIn("|| estado.pinTabletaEnviando", teclado)
