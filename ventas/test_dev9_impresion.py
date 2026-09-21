from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase
from PIL import ImageDraw

from catalogo.models import Categoria, Producto
from impresion.render import (
    ALTO_SEPARADOR_COMENSALES,
    TAMANO_NOMBRE_LLEVAR,
    _agrupar_complementos_globales,
    _centrado,
    _datos_comanda_por_nombres,
    _dibujar_complementos_globales,
    _dibujar_separador_comensales,
    render_comanda,
)
from personas.models import Sucursal
from ventas.models import Mesa, Partida, Ticket


class ImpresionDev9Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sucursal = Sucursal.objects.create(clave="DEV9", nombre="Pruebas dev.9")
        comida = Categoria.objects.create(
            sucursal=cls.sucursal,
            nombre="Taco",
            orden=1,
        )
        complementos = Categoria.objects.create(
            sucursal=cls.sucursal,
            nombre="Consome y Barbacoa",
            orden=2,
        )
        bebidas = Categoria.objects.create(
            sucursal=cls.sucursal,
            nombre="Bebidas",
            orden=3,
        )
        cls.taco = Producto.objects.create(
            sucursal=cls.sucursal,
            categoria=comida,
            codigo="TB",
            nombre="Taco de barbacoa",
            nombre_corto="TB",
            orden=1,
            destino_impresion=Producto.Destino.COCINA,
        )
        cls.consome = Producto.objects.create(
            sucursal=cls.sucursal,
            categoria=complementos,
            codigo="CO05",
            nombre="Consome medio litro",
            nombre_corto="CO 1/2",
            orden=2,
            destino_impresion=Producto.Destino.COCINA,
        )
        cls.barbacoa = Producto.objects.create(
            sucursal=cls.sucursal,
            categoria=complementos,
            codigo="BBQ05",
            nombre="Barbacoa medio litro",
            nombre_corto="BBQ 1/2",
            orden=3,
            destino_impresion=Producto.Destino.COCINA,
        )
        cls.bebida = Producto.objects.create(
            sucursal=cls.sucursal,
            categoria=bebidas,
            codigo="CC",
            nombre="Coca Cola",
            nombre_corto="CC",
            orden=4,
            destino_impresion=Producto.Destino.BARRA,
        )

    def setUp(self):
        self.secuencia = 0

    def _ticket(self, canal=Mesa.Canal.COMEDOR, *, por_nombres=False, cliente=""):
        self.secuencia += 1
        mesa = Mesa.objects.create(
            sucursal=self.sucursal,
            canal=canal,
            clave=f"{canal}-{self.secuencia}",
            nombre=f"Posicion {self.secuencia}",
            orden=self.secuencia,
        )
        return Ticket.objects.create(
            sucursal=self.sucursal,
            mesa=mesa,
            folio=9000 + self.secuencia,
            canal=canal,
            captura_por_nombres=por_nombres,
            cliente_nombre=cliente,
        )

    def _partida(self, ticket, producto, comensal, cantidad=1):
        return Partida.objects.create(
            sucursal=self.sucursal,
            ticket=ticket,
            producto=producto,
            comensal=comensal,
            cantidad=Decimal(str(cantidad)),
            precio_unitario=Decimal("25.00"),
            nombre_producto=producto.nombre,
            nombre_corto=producto.nombre_corto,
        )

    def _comanda_con_personas(self, cantidad, *, por_nombres=False):
        ticket = self._ticket(por_nombres=por_nombres)
        if por_nombres:
            ticket.nombres_comensales = {
                str(numero): f"Persona {numero}"
                for numero in range(1, cantidad + 1)
            }
            ticket.save(update_fields=["nombres_comensales"])
        for numero in range(1, cantidad + 1):
            self._partida(ticket, self.taco, numero)
        return ticket

    def test_separador_punteado_ocupa_un_renglon(self):
        draw = MagicMock()

        siguiente_y = _dibujar_separador_comensales(draw, 120)

        self.assertEqual(siguiente_y, 120 + ALTO_SEPARADOR_COMENSALES)
        self.assertGreater(draw.line.call_count, 10)
        segmentos = [llamada.args[0] for llamada in draw.line.call_args_list]
        self.assertTrue(all(segmento[1] == 144 and segmento[3] == 144 for segmento in segmentos))
        self.assertTrue(
            all(actual[2] < siguiente[0] for actual, siguiente in zip(segmentos, segmentos[1:]))
        )

    def test_matriz_separa_solo_si_hay_otro_grupo_de_seis(self):
        for comensales, separadores in ((6, 0), (7, 1), (13, 2)):
            with self.subTest(comensales=comensales):
                ticket = self._comanda_con_personas(comensales)
                with patch(
                    "impresion.render._dibujar_separador_comensales",
                    wraps=_dibujar_separador_comensales,
                ) as separar:
                    render_comanda(ticket, "cocina")
                self.assertEqual(separar.call_count, separadores)

    def test_captura_por_nombres_separa_cada_seis_filas(self):
        for comensales, separadores in ((6, 0), (7, 1), (13, 2)):
            with self.subTest(comensales=comensales):
                ticket = self._comanda_con_personas(comensales, por_nombres=True)
                with patch(
                    "impresion.render._dibujar_separador_comensales",
                    wraps=_dibujar_separador_comensales,
                ) as separar:
                    render_comanda(ticket, "cocina")
                self.assertEqual(separar.call_count, separadores)

    def test_complementos_se_agrupan_sin_comensal_y_barbacoa_se_conserva(self):
        ticket = self._ticket(por_nombres=True)
        ticket.nombres_comensales = {"1": "Amanda", "2": "Cecy"}
        ticket.save(update_fields=["nombres_comensales"])
        self._partida(ticket, self.consome, 1, 1)
        self._partida(ticket, self.consome, 2, 2)
        self._partida(ticket, self.bebida, 1, 1)
        self._partida(ticket, self.bebida, 2, 3)
        self._partida(ticket, self.barbacoa, 2, 1)

        agrupados = _agrupar_complementos_globales(
            ticket.partidas.select_related("producto__categoria").all()
        )
        cantidades = {item["nombre"]: item["cantidad"] for item in agrupados}
        self.assertEqual(cantidades, {"CO 1/2": Decimal("3.000"), "CC": Decimal("4.000")})

        datos = _datos_comanda_por_nombres(ticket)
        self.assertEqual(
            {item["nombre"]: item["cantidad"] for item in datos["complementos_globales"]},
            cantidades,
        )
        self.assertEqual([fila["nombre"] for fila in datos["filas"]], ["Cecy"])
        self.assertEqual([extra["nombre"] for extra in datos["extras_barbacoa"]], ["Cecy"])
        self.assertEqual(
            dict(datos["extras_barbacoa"][0]["conceptos"]),
            {"BBQ 1/2": Decimal("1.000")},
        )

    def test_dibujo_suma_complementos_con_la_misma_abreviatura(self):
        draw = MagicMock()
        complementos = [
            {"nombre": "CC", "cantidad": Decimal("1.000")},
            {"nombre": "CC", "cantidad": Decimal("2.000")},
        ]
        with patch("impresion.render._centrado"), patch(
            "impresion.render._segmentos_bebidas",
            return_value=[],
        ) as segmentos, patch(
            "impresion.render._dibujar_segmentos",
            return_value=80,
        ):
            _dibujar_complementos_globales(
                draw,
                10,
                complementos,
                MagicMock(),
                MagicMock(),
            )

        self.assertEqual(segmentos.call_args.args[0]["CC"], Decimal("3.000"))

    def test_modo_normal_dibuja_una_sola_seccion_global(self):
        ticket = self._ticket()
        self._partida(ticket, self.consome, 1, 1)
        self._partida(ticket, self.consome, 2, 2)
        self._partida(ticket, self.bebida, 1, 1)
        self._partida(ticket, self.bebida, 2, 3)
        self._partida(ticket, self.barbacoa, 2, 1)

        with patch(
            "impresion.render._dibujar_complementos_globales",
            wraps=_dibujar_complementos_globales,
        ) as dibujar, patch.object(
            ImageDraw.ImageDraw,
            "text",
            autospec=True,
        ) as escribir:
            render_comanda(ticket, "cocina")

        self.assertEqual(dibujar.call_count, 1)
        cantidades = {
            item["nombre"]: item["cantidad"]
            for item in dibujar.call_args.args[2]
        }
        self.assertEqual(cantidades, {"CO 1/2": Decimal("3.000"), "CC": Decimal("4.000")})
        textos = [str(llamada.args[2]) for llamada in escribir.call_args_list]
        self.assertEqual(textos.count("CO 1/2"), 1)
        self.assertEqual(textos.count("CC"), 1)
        self.assertIn("BBQ 1/2", textos)

    def test_modo_por_nombres_no_imprime_nombres_para_complementos_globales(self):
        ticket = self._ticket(por_nombres=True)
        ticket.nombres_comensales = {"1": "Amanda", "2": "Cecy"}
        ticket.save(update_fields=["nombres_comensales"])
        self._partida(ticket, self.consome, 1, 1)
        self._partida(ticket, self.bebida, 2, 2)

        with patch(
            "impresion.render._dibujar_complementos_globales",
            wraps=_dibujar_complementos_globales,
        ) as dibujar, patch.object(
            ImageDraw.ImageDraw,
            "text",
            autospec=True,
        ) as escribir:
            render_comanda(ticket, "cocina")

        self.assertEqual(dibujar.call_count, 1)
        textos = [str(llamada.args[2]) for llamada in escribir.call_args_list]
        self.assertNotIn("AMANDA", textos)
        self.assertNotIn("CECY", textos)
        self.assertEqual(textos.count("CO 1/2"), 1)
        self.assertEqual(textos.count("CC"), 1)

    def test_nombre_llevar_usa_fuente_dedicada_mayor_solo_en_impresion(self):
        llevar = self._ticket(Mesa.Canal.LLEVAR, cliente="Maria Fernanda")
        self._partida(llevar, self.taco, 1)

        with patch("impresion.render._centrado", wraps=_centrado) as centrar:
            render_comanda(llevar, "cocina")

        llamadas_llevar = [
            llamada
            for llamada in centrar.call_args_list
            if str(llamada.args[2]).startswith("LLEVAR:")
        ]
        self.assertEqual(len(llamadas_llevar), 1)
        self.assertEqual(llamadas_llevar[0].args[3].size, TAMANO_NOMBRE_LLEVAR)
        self.assertGreater(llamadas_llevar[0].args[3].size, 28)

        comedor = self._ticket(Mesa.Canal.COMEDOR, cliente="No debe aparecer")
        self._partida(comedor, self.taco, 1)
        with patch("impresion.render._centrado", wraps=_centrado) as centrar_comedor:
            render_comanda(comedor, "cocina")
        self.assertFalse(
            any(
                str(llamada.args[2]).startswith("LLEVAR:")
                for llamada in centrar_comedor.call_args_list
            )
        )
