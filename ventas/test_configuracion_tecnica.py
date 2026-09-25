"""Contrato de separación: soporte local ya no vive en Administración de negocio."""

from pathlib import Path
from uuid import uuid4

from django.test import SimpleTestCase
from django.urls import Resolver404, resolve


RAIZ = Path(__file__).resolve().parent
PLANTILLA_ADMIN = RAIZ / "templates" / "ventas" / "administrador.html"
JS_ADMIN = RAIZ / "static" / "ventas" / "admin.js"
JS_POS = RAIZ / "static" / "ventas" / "app.js"


class SeparacionPanelTecnicoTests(SimpleTestCase):
    def test_rutas_legacy_de_impresion_no_se_publican(self):
        for ruta in (
            "/api/administrador/configuracion-tecnica/impresion/",
            "/api/administrador/configuracion-tecnica/impresion/" + str(uuid4()) + "/",
        ):
            with self.subTest(ruta=ruta), self.assertRaises(Resolver404):
                resolve(ruta)

    def test_administrador_de_negocio_no_ofrece_panel_tecnico(self):
        html = PLANTILLA_ADMIN.read_text(encoding="utf-8")
        self.assertNotIn('data-panel="configuracion-tecnica"', html)
        self.assertNotIn('data-admin-panel="configuracion-tecnica"', html)

    def test_id_terminal_se_comparte_con_cliente_pos(self):
        admin = JS_ADMIN.read_text(encoding="utf-8")
        pos = JS_POS.read_text(encoding="utf-8")
        contrato = 'const DEVICE_ID_KEY = "tocayos_pos_device_id";'
        self.assertIn(contrato, admin)
        self.assertIn(contrato, pos)
        self.assertIn('"X-POS-Device-ID": POS_DEVICE_ID', admin)
