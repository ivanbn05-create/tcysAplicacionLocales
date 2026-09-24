import json
from pathlib import Path

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from impresion.models import ConfiguracionImpresionTerminal
from personas.models import Rol, Sucursal, UsuarioPOS


RUTA_COLECCION = "/api/administrador/configuracion-tecnica/impresion/"
RAIZ_VENTAS = Path(__file__).resolve().parent
PLANTILLA_ADMIN = RAIZ_VENTAS / "templates" / "ventas" / "administrador.html"
JS_ADMIN = RAIZ_VENTAS / "static" / "ventas" / "admin.js"
JS_POS = RAIZ_VENTAS / "static" / "ventas" / "app.js"
CSS_ADMIN = RAIZ_VENTAS / "static" / "ventas" / "admin.css"


@override_settings(SUCURSAL_CLAVE="ARBOLEDAS", POS_REQUIRE_AUTH=False)
class ConfiguracionTecnicaImpresionAPITests(TestCase):
    def setUp(self):
        call_command(
            "aprovisionar_sucursal",
            clave="ARBOLEDAS",
            nombre="Arboledas",
            verbosity=0,
        )
        self.sucursal = Sucursal.objects.get(clave="ARBOLEDAS")
        self.payload = {
            "nombre": "Caja principal",
            "device_id": "terminal-caja-01",
            "host_caja": "192.0.2.20",
            "host_cocina": "192.0.2.21",
            "host_barra": "2001:db8::22",
            "puerto": 9100,
            "activa": True,
        }

    def acceder(self, pin="0000"):
        return self.client.post(
            "/api/administrador/acceso/",
            data=json.dumps({"clave_administrador": pin}),
            content_type="application/json",
        )

    def crear(self, payload=None):
        return self.client.post(
            RUTA_COLECCION,
            data=json.dumps(payload or self.payload),
            content_type="application/json",
        )

    def test_maestro_lista_crea_edita_y_desactiva_sin_exponer_campos_ajenos(self):
        self.assertEqual(self.client.get(RUTA_COLECCION).status_code, 401)
        acceso = self.acceder()
        self.assertEqual(acceso.status_code, 200)
        self.assertTrue(
            acceso.json()["permisos"]["gestionar_configuracion_tecnica"]
        )

        creada = self.crear()
        self.assertEqual(creada.status_code, 201, creada.content)
        self.assertIn("no-store", creada.headers.get("Cache-Control", ""))
        self.assertIn("private", creada.headers.get("Cache-Control", ""))
        configuracion = creada.json()["configuracion"]
        self.assertEqual(
            set(configuracion),
            {
                "id",
                "nombre",
                "device_id",
                "host_caja",
                "host_cocina",
                "host_barra",
                "puerto",
                "activa",
                "actualizado_en",
            },
        )
        self.assertEqual(configuracion["host_barra"], "2001:db8::22")
        self.assertNotIn("token", json.dumps(creada.json()).lower())
        self.assertNotIn("clave", json.dumps(creada.json()).lower())

        listado = self.client.get(RUTA_COLECCION)
        self.assertEqual(listado.status_code, 200)
        self.assertEqual(
            [item["device_id"] for item in listado.json()["configuraciones"]],
            ["terminal-caja-01"],
        )

        detalle = RUTA_COLECCION + configuracion["id"] + "/"
        editada = self.client.patch(
            detalle,
            data=json.dumps(
                {
                    "nombre": "Caja mostrador",
                    "host_caja": "198.51.100.30",
                    "puerto": "9200",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(editada.status_code, 200, editada.content)
        self.assertEqual(editada.json()["configuracion"]["nombre"], "Caja mostrador")
        self.assertEqual(editada.json()["configuracion"]["puerto"], 9200)

        desactivada = self.client.patch(
            detalle,
            data=json.dumps({"activa": False}),
            content_type="application/json",
        )
        self.assertEqual(desactivada.status_code, 200)
        self.assertFalse(desactivada.json()["configuracion"]["activa"])
        self.assertFalse(
            ConfiguracionImpresionTerminal.objects.get(
                pk=configuracion["id"]
            ).activa
        )

    def test_payload_estricto_rechaza_campos_hosts_puertos_y_device_id_invalidos(self):
        self.assertEqual(self.acceder().status_code, 200)
        casos = (
            ({**self.payload, "token": "no-debe-aceptarse"}, "campos no permitidos"),
            ({**self.payload, "device_id": "terminal con espacios"}, "identificador"),
            ({**self.payload, "host_caja": "http://192.0.2.20:9100"}, "IPv4 o IPv6"),
            ({**self.payload, "host_caja": "224.0.0.1"}, "multicast"),
            ({**self.payload, "puerto": 0}, "entre 1 y 65535"),
            ({**self.payload, "puerto": 65536}, "entre 1 y 65535"),
            ({**self.payload, "puerto": 9100.5}, "entero"),
            ({**self.payload, "activa": "true"}, "verdadero o falso"),
        )
        for payload, mensaje in casos:
            with self.subTest(payload=payload):
                respuesta = self.crear(payload)
                self.assertEqual(respuesta.status_code, 400, respuesta.content)
                self.assertIn(mensaje, respuesta.json()["error"])
                self.assertNotIn("no-debe-aceptarse", json.dumps(respuesta.json()))
        self.assertFalse(
            ConfiguracionImpresionTerminal.objects.filter(
                sucursal=self.sucursal
            ).exists()
        )

    def test_device_id_es_unico_por_sucursal_y_otras_sucursales_no_se_filtran(self):
        self.assertEqual(self.acceder().status_code, 200)
        creada = self.crear()
        self.assertEqual(creada.status_code, 201)

        duplicada = self.crear({**self.payload, "nombre": "Duplicada"})
        self.assertEqual(duplicada.status_code, 409)
        self.assertEqual(duplicada.json()["codigo"], "device_id_duplicado")

        otra_sucursal = Sucursal.objects.create(
            clave="CENTRO",
            nombre="Centro",
        )
        ajena = ConfiguracionImpresionTerminal.objects.create(
            sucursal=otra_sucursal,
            nombre="Terminal ajena",
            device_id="terminal-ajena",
            host_caja="203.0.113.9",
            puerto=9100,
        )
        listado = self.client.get(RUTA_COLECCION)
        self.assertNotIn("terminal-ajena", json.dumps(listado.json()))

        intento_ajeno = self.client.patch(
            RUTA_COLECCION + str(ajena.id) + "/",
            data=json.dumps({"activa": False}),
            content_type="application/json",
        )
        self.assertEqual(intento_ajeno.status_code, 404)
        ajena.refresh_from_db()
        self.assertTrue(ajena.activa)

    def test_rol_elevado_no_puede_ver_ni_modificar_configuracion_tecnica(self):
        rol = Rol.objects.create(
            sucursal=self.sucursal,
            nombre="Supervisión",
            tipo=Rol.Tipo.ELEVADO,
        )
        perfil = UsuarioPOS(
            sucursal=self.sucursal,
            rol=rol,
            nombre="Supervisora",
            clave="",
        )
        perfil.set_clave("2468")
        perfil.save()

        acceso = self.acceder("2468")
        self.assertEqual(acceso.status_code, 200)
        self.assertFalse(
            acceso.json()["permisos"]["gestionar_configuracion_tecnica"]
        )
        self.assertEqual(self.client.get(RUTA_COLECCION).status_code, 403)
        self.assertEqual(self.crear().status_code, 403)

        configuracion = ConfiguracionImpresionTerminal.objects.create(
            sucursal=self.sucursal,
            nombre="Caja",
            device_id="terminal-caja-01",
            host_caja="192.0.2.20",
        )
        respuesta = self.client.patch(
            RUTA_COLECCION + str(configuracion.id) + "/",
            data=json.dumps({"activa": False}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(
            respuesta.json()["codigo"],
            "administrador_maestro_requerido",
        )
        configuracion.refresh_from_db()
        self.assertTrue(configuracion.activa)


class ConfiguracionTecnicaImpresionFrontendTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.html = PLANTILLA_ADMIN.read_text(encoding="utf-8")
        cls.javascript = JS_ADMIN.read_text(encoding="utf-8")
        cls.javascript_pos = JS_POS.read_text(encoding="utf-8")
        cls.css = CSS_ADMIN.read_text(encoding="utf-8")

    def test_panel_y_navegacion_nacen_ocultos_para_perfiles_sin_permiso(self):
        permiso = 'data-permiso-admin="gestionar_configuracion_tecnica"'
        self.assertIn(
            'data-panel="configuracion-tecnica" ' + permiso + ' type="button" hidden',
            self.html,
        )
        self.assertIn(
            'data-admin-panel="configuracion-tecnica" ' + permiso + ' hidden',
            self.html,
        )
        for identificador in (
            "lista-configuraciones-impresion",
            "form-configuracion-impresion",
            "configuracion-impresion-device-id",
            "configuracion-impresion-host-caja",
            "configuracion-impresion-host-cocina",
            "configuracion-impresion-host-barra",
            "configuracion-impresion-puerto",
            "configuracion-impresion-activa",
        ):
            self.assertIn('id="' + identificador + '"', self.html)

    def test_admin_reutiliza_device_id_del_pos_y_lo_fuerza_en_toda_api(self):
        contrato = 'const DEVICE_ID_KEY = "tocayos_pos_device_id";'
        self.assertIn(contrato, self.javascript)
        self.assertIn(contrato, self.javascript_pos)
        inicio_headers = self.javascript.index("      headers: {")
        fin_headers = self.javascript.index("      },", inicio_headers)
        headers = self.javascript[inicio_headers:fin_headers]
        self.assertIn('...(opciones.headers || {}),', headers)
        self.assertIn('"X-POS-Device-ID": POS_DEVICE_ID,', headers)
        self.assertLess(
            headers.index("...(opciones.headers || {}),"),
            headers.index('"X-POS-Device-ID": POS_DEVICE_ID,'),
        )
        self.assertIn("localStorage.getItem(DEVICE_ID_KEY)", self.javascript)
        self.assertIn("localStorage.setItem(DEVICE_ID_KEY, nuevo)", self.javascript)

    def test_ui_permita_crear_editar_desactivar_y_se_adapta_sin_overflow(self):
        for contrato in (
            "function cargarConfiguracionesImpresion(control = null)",
            "function guardarConfiguracionImpresion(evento)",
            "function editarConfiguracionImpresion(id)",
            "async function desactivarConfiguracionImpresion()",
            'exigirPermisoAdministrador("gestionar_configuracion_tecnica")',
            'method: id ? "PATCH" : "POST"',
            'JSON.stringify({ activa: false })',
        ):
            self.assertIn(contrato, self.javascript)
        for contrato in (
            ".configuracion-tecnica-layout {",
            "grid-template-columns: minmax(310px, .9fr) minmax(420px, 1.35fr);",
            "@media (max-width: 860px)",
            "grid-template-columns: minmax(0, 1fr);",
            "overflow-wrap: anywhere;",
            "grid-template-columns: repeat(9, minmax(70px, 1fr));",
        ):
            self.assertIn(contrato, self.css)
