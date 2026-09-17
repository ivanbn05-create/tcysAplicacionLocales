import json
from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from catalogo.models import Precio, Producto
from personas.models import Sucursal


class ParcheCatalogoTopoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command(
            "aprovisionar_sucursal",
            clave="ARBOLEDAS",
            nombre="Arboledas",
            verbosity=0,
        )
        call_command("cargar_datos_iniciales", verbosity=0)

    def setUp(self):
        self.sucursal = Sucursal.objects.get(clave="ARBOLEDAS")
        self.topo = Producto.objects.get(sucursal=self.sucursal, codigo="AM")
        self.otro = Producto.objects.get(sucursal=self.sucursal, codigo="CC")

    def _restaurar_estado_legado(self):
        self.topo.nombre_corto = "AM"
        self.topo.save(update_fields=["nombre_corto", "actualizado_en"])
        Precio.objects.filter(producto=self.topo).delete()
        Precio.objects.create(
            sucursal=self.sucursal,
            producto=self.topo,
            importe=Decimal("30.00"),
            vigente_desde=date(2026, 8, 14),
            activo=True,
        )

    def _ejecutar(self, *, dry_run=False):
        salida = StringIO()
        call_command(
            "aplicar_parche_catalogo_0_4_0_dev_3_topo",
            sucursal="ARBOLEDAS",
            dry_run=dry_run,
            stdout=salida,
        )
        linea = next(
            item
            for item in salida.getvalue().splitlines()
            if item.startswith("PARCHE_CATALOGO=")
        )
        return json.loads(linea.split("=", 1)[1])

    def test_semilla_limpia_publica_topo_a_32(self):
        self.assertEqual(self.topo.nombre_corto, "Topo")
        self.assertEqual(self.topo.precio_actual().importe, Decimal("32.00"))

    def test_dry_run_informa_antes_y_despues_sin_escribir(self):
        self._restaurar_estado_legado()

        resultado = self._ejecutar(dry_run=True)

        self.topo.refresh_from_db()
        self.assertEqual(resultado["estado"], "dry-run")
        self.assertEqual(resultado["antes"]["nombre_corto"], "AM")
        self.assertEqual(resultado["antes"]["precio"]["importe"], "30.00")
        self.assertEqual(resultado["despues"]["nombre_corto"], "Topo")
        self.assertEqual(resultado["despues"]["precio"]["importe"], "32.00")
        self.assertEqual(self.topo.nombre_corto, "AM")
        self.assertEqual(self.topo.precio_actual().importe, Decimal("30.00"))

    def test_aplica_una_vez_preserva_historia_y_no_toca_otro_producto(self):
        self._restaurar_estado_legado()
        otro_antes = (
            self.otro.nombre,
            self.otro.nombre_corto,
            self.otro.precio_actual().importe,
            Precio.objects.filter(producto=self.otro).count(),
        )

        resultado = self._ejecutar()

        self.topo.refresh_from_db()
        precio_anterior = Precio.objects.get(
            producto=self.topo,
            vigente_desde=date(2026, 8, 14),
        )
        precio_nuevo = Precio.objects.get(
            producto=self.topo,
            vigente_desde=date(2026, 9, 17),
        )
        self.otro.refresh_from_db()
        self.assertEqual(resultado["estado"], "aplicado")
        self.assertEqual(self.topo.nombre_corto, "Topo")
        self.assertEqual(precio_anterior.importe, Decimal("30.00"))
        self.assertEqual(precio_anterior.vigente_hasta, date(2026, 9, 16))
        self.assertEqual(precio_nuevo.importe, Decimal("32.00"))
        self.assertEqual(
            (
                self.otro.nombre,
                self.otro.nombre_corto,
                self.otro.precio_actual().importe,
                Precio.objects.filter(producto=self.otro).count(),
            ),
            otro_antes,
        )

        segundo = self._ejecutar()
        self.assertEqual(segundo["estado"], "ya_aplicado")
        self.assertEqual(Precio.objects.filter(producto=self.topo).count(), 2)

    def test_rechaza_otra_sucursal_y_una_identidad_local_distinta(self):
        with self.assertRaisesRegex(CommandError, "sólo está publicado"):
            call_command(
                "aplicar_parche_catalogo_0_4_0_dev_3_topo",
                sucursal="CENTRO",
            )
        with override_settings(SUCURSAL_CLAVE="CENTRO"):
            with self.assertRaisesRegex(CommandError, "no coincide"):
                call_command(
                    "aplicar_parche_catalogo_0_4_0_dev_3_topo",
                    sucursal="ARBOLEDAS",
                )

    def test_rechaza_sobrescribir_una_personalizacion(self):
        self.topo.nombre_corto = "Mineral especial"
        self.topo.save(update_fields=["nombre_corto", "actualizado_en"])

        with self.assertRaisesRegex(CommandError, "personalización"):
            self._ejecutar()

        self.topo.refresh_from_db()
        self.assertEqual(self.topo.nombre_corto, "Mineral especial")
        self.assertEqual(self.topo.precio_actual().importe, Decimal("32.00"))
