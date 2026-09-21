from django.test import TestCase

from personas.models import Sucursal

from .clientes import buscar_clientes_directorio, guardar_cliente


class DirectorioClientesDev9Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sucursal = Sucursal.objects.create(clave="DIR_DEV9", nombre="Directorio dev.9")
        cls.otra = Sucursal.objects.create(clave="DIR_OTRA", nombre="Otra sucursal")
        for indice in range(35):
            guardar_cliente(
                cls.sucursal,
                {
                    "nombre": f"Cliente {indice:02d}",
                    "notas": "Prefiere llamada vespertina" if indice == 7 else "",
                    "telefonos": [
                        {
                            "numero": f"331234{indice:04d}",
                            "etiqueta": "WhatsApp ventas" if indice == 8 else "Principal",
                        }
                    ],
                    "domicilios": [
                        {
                            "etiqueta": "Bodega norte" if indice == 9 else "Principal",
                            "calle": f"Avenida Operación {indice}",
                            "numero_exterior": str(100 + indice),
                            "numero_interior": f"Local {indice}" if indice == 10 else "",
                            "colonia": "Arboledas",
                            "codigo_postal": "45070" if indice == 11 else "",
                            "municipio": "Zapopan" if indice == 12 else "",
                            "referencia": "Puerta amarilla" if indice == 13 else "",
                        }
                    ],
                },
            )
        guardar_cliente(
            cls.otra,
            {
                "nombre": "Cliente ajeno",
                "notas": "SECRETO OTRA SUCURSAL",
            },
        )

    def test_directorio_pagina_todos_los_clientes_sin_duplicarlos(self):
        primera = buscar_clientes_directorio(self.sucursal, pagina=1, limite=30)
        segunda = buscar_clientes_directorio(self.sucursal, pagina=2, limite=30)

        self.assertEqual(primera["total"], 35)
        self.assertEqual(len(primera["resultados"]), 30)
        self.assertTrue(primera["hay_mas"])
        self.assertEqual(len(segunda["resultados"]), 5)
        self.assertFalse(segunda["hay_mas"])
        ids = [item["cliente_id"] for item in primera["resultados"] + segunda["resultados"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_directorio_busca_por_campos_del_registro_y_aisla_sucursal(self):
        casos = {
            "vespertina": "Cliente 07",
            "WhatsApp ventas": "Cliente 08",
            "Bodega norte": "Cliente 09",
            "Local 10": "Cliente 10",
            "45070": "Cliente 11",
            "Zapopan": "Cliente 12",
            "Puerta amarilla": "Cliente 13",
            "3312340006": "Cliente 06",
        }
        for consulta, esperado in casos.items():
            with self.subTest(consulta=consulta):
                resultado = buscar_clientes_directorio(self.sucursal, consulta, limite=30)
                self.assertIn(esperado, [item["nombre"] for item in resultado["resultados"]])

        ajeno = buscar_clientes_directorio(self.sucursal, "SECRETO OTRA SUCURSAL")
        self.assertEqual(ajeno["total"], 0)
        self.assertEqual(ajeno["resultados"], [])

import json

from django.test import override_settings


@override_settings(POS_REQUIRE_AUTH=False, SUCURSAL_CLAVE="DIR_API")
class DirectorioClientesApiDev9Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sucursal = Sucursal.objects.create(clave="DIR_API", nombre="Directorio API")
        Sucursal.objects.create(clave="DIR_API_OTRA", nombre="Directorio API otra")
        for indice in range(3):
            guardar_cliente(
                cls.sucursal,
                {
                    "nombre": f"Persona API {indice}",
                    "notas": "dato buscable" if indice == 2 else "",
                },
            )

    def test_api_pagina_directorio_y_permita_editar_sin_clave_administrativa(self):
        primera = self.client.post(
            "/api/clientes/buscar/",
            data=json.dumps({"q": "", "limite": 2, "pagina": 1}),
            content_type="application/json",
        )
        self.assertEqual(primera.status_code, 200)
        self.assertEqual(primera.json()["total"], 3)
        self.assertEqual(len(primera.json()["resultados"]), 2)
        self.assertTrue(primera.json()["hay_mas"])

        buscado = self.client.post(
            "/api/clientes/buscar/",
            data=json.dumps({"q": "dato buscable", "limite": 30, "pagina": 1}),
            content_type="application/json",
        )
        self.assertEqual([item["nombre"] for item in buscado.json()["resultados"]], ["Persona API 2"])

        cliente_id = buscado.json()["resultados"][0]["cliente_id"]
        editado = self.client.patch(
            f"/api/clientes/{cliente_id}/",
            data=json.dumps(
                {
                    "nombre": "Persona API editada",
                    "notas": "dato buscable",
                    "telefonos": [],
                    "domicilios": [],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(editado.status_code, 200)
        self.assertEqual(editado.json()["cliente"]["nombre"], "Persona API editada")