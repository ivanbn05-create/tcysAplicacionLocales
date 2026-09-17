import json
from pathlib import Path

from django.contrib.auth.hashers import check_password, make_password
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from personas.models import Rol, Sucursal, UsuarioPOS
from ventas.admin_services import validar_clave_administrativa
from ventas.models import ConfiguracionSucursal, MovimientoCaja


@override_settings(SUCURSAL_CLAVE="ARBOLEDAS", POS_REQUIRE_AUTH=False)
class AprovisionamientoClaveMaestraTests(TestCase):
    def _aprovisionar(self):
        call_command(
            "aprovisionar_sucursal",
            clave="ARBOLEDAS",
            nombre="Arboledas",
            verbosity=0,
        )

    def test_solo_un_aprovisionamiento_nuevo_crea_la_clave_maestra_0000_hasheada(self):
        self._aprovisionar()

        configuracion = ConfiguracionSucursal.objects.get(
            sucursal__clave="ARBOLEDAS"
        )
        self.assertNotEqual(configuracion.clave_administrador, "0000")
        self.assertTrue(check_password("0000", configuracion.clave_administrador))

        configuracion.clave_administrador = make_password("8642")
        configuracion.save(update_fields=["clave_administrador", "actualizado_en"])
        self._aprovisionar()
        configuracion.refresh_from_db()

        self.assertTrue(check_password("8642", configuracion.clave_administrador))
        self.assertFalse(check_password("0000", configuracion.clave_administrador))


@override_settings(SUCURSAL_CLAVE="ARBOLEDAS", POS_REQUIRE_AUTH=False)
class AccesoElevadoTests(TestCase):
    def setUp(self):
        call_command(
            "aprovisionar_sucursal",
            clave="ARBOLEDAS",
            nombre="Arboledas",
            verbosity=0,
        )
        self.sucursal = Sucursal.objects.get(clave="ARBOLEDAS")
        self.rol_administrador = Rol.objects.create(
            sucursal=self.sucursal,
            nombre="Encargado",
            tipo=Rol.Tipo.ENCARGADO,
        )
        self.administrador = self._crear_usuario(
            "Administrador de prueba",
            self.rol_administrador,
            "9876",
        )
        self.rol_mesero = Rol.objects.create(
            sucursal=self.sucursal,
            nombre="Mesero",
            tipo=Rol.Tipo.MESERO,
        )
        self.operador = self._crear_usuario(
            "Operador de prueba",
            self.rol_mesero,
            "1111",
        )
        self.rol_repartidor = Rol.objects.create(
            sucursal=self.sucursal,
            nombre="Repartidor",
            tipo=Rol.Tipo.REPARTIDOR,
        )
        self.repartidor = self._crear_usuario(
            "Repartidor de prueba",
            self.rol_repartidor,
            "2222",
        )

    def _crear_usuario(self, nombre, rol, pin, activo=True):
        perfil = UsuarioPOS(
            sucursal=self.sucursal,
            rol=rol,
            nombre=nombre,
            activo=activo,
            clave="",
        )
        perfil.set_clave(pin)
        perfil.save()
        return perfil

    def _acceder_admin(self, pin):
        return self.client.post(
            "/api/administrador/acceso/",
            data=json.dumps({"clave_administrador": pin}),
            content_type="application/json",
        )

    def _crear_elevado(self, pin="2468", nombre="Supervisora"):
        self.assertEqual(self._acceder_admin("0000").status_code, 200)
        return self.client.post(
            "/api/administrador/usuarios/",
            data=json.dumps(
                {
                    "nombre": nombre,
                    "tipo": Rol.Tipo.ELEVADO,
                    "clave": pin,
                    "activo": True,
                }
            ),
            content_type="application/json",
        )

    def test_maestro_crea_varios_elevados_sin_exponer_sus_pines(self):
        primera = self._crear_elevado()
        segunda = self.client.post(
            "/api/administrador/usuarios/",
            data=json.dumps(
                {
                    "nombre": "Supervisor",
                    "tipo": Rol.Tipo.ELEVADO,
                    "clave": "1357",
                    "activo": True,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(primera.status_code, 201)
        self.assertEqual(segunda.status_code, 201)
        for respuesta, pin in ((primera, "2468"), (segunda, "1357")):
            payload = respuesta.json()["usuario"]
            self.assertNotIn("clave", payload)
            perfil = UsuarioPOS.objects.get(pk=payload["id"])
            self.assertNotEqual(perfil.clave, pin)
            self.assertTrue(perfil.check_clave(pin))

        edicion = self.client.patch(
            f"/api/administrador/usuarios/{primera.json()['usuario']['id']}/",
            data=json.dumps(
                {
                    "nombre": "Supervisora de turno",
                    "tipo": Rol.Tipo.ELEVADO,
                    "activo": True,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(edicion.status_code, 200)
        self.assertNotIn("clave", edicion.json()["usuario"])
        perfil_editado = UsuarioPOS.objects.get(
            pk=primera.json()["usuario"]["id"]
        )
        self.assertEqual(perfil_editado.nombre, "Supervisora de turno")
        self.assertEqual(perfil_editado.rol.tipo, Rol.Tipo.ELEVADO)
        self.assertTrue(perfil_editado.check_clave("2468"))

        resumen = self.client.get("/api/administrador/resumen/")
        self.assertEqual(resumen.status_code, 200)
        for usuario in resumen.json()["administrador"]["usuarios"]:
            self.assertNotIn("clave", usuario)

    def test_operador_principal_se_edita_desactiva_y_rota_pin_sin_ser_elevado(self):
        self.assertEqual(self._acceder_admin("0000").status_code, 200)
        resumen = self.client.get("/api/administrador/resumen/")
        self.assertEqual(resumen.status_code, 200)
        operador = next(
            usuario
            for usuario in resumen.json()["administrador"]["usuarios"]
            if usuario["id"] == str(self.administrador.id)
        )
        self.assertEqual(operador["tipo"], Rol.Tipo.ENCARGADO)
        self.assertEqual(operador["tipo_etiqueta"], "Operador principal")
        self.assertNotIn("clave", operador)

        edicion = self.client.patch(
            f"/api/administrador/usuarios/{self.administrador.id}/",
            data=json.dumps(
                {
                    "nombre": "Caja principal editada",
                    "tipo": Rol.Tipo.ENCARGADO,
                    "clave": "8642",
                    "activo": False,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(edicion.status_code, 200)
        self.assertNotIn("clave", edicion.json()["usuario"])
        self.administrador.refresh_from_db()
        self.assertEqual(self.administrador.nombre, "Caja principal editada")
        self.assertEqual(self.administrador.rol.tipo, Rol.Tipo.ENCARGADO)
        self.assertFalse(self.administrador.activo)
        self.assertTrue(self.administrador.check_clave("8642"))
        self.assertFalse(self.administrador.check_clave("9876"))

        conversion = self.client.patch(
            f"/api/administrador/usuarios/{self.administrador.id}/",
            data=json.dumps(
                {
                    "nombre": self.administrador.nombre,
                    "tipo": Rol.Tipo.ELEVADO,
                    "activo": False,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(conversion.status_code, 400)
        self.administrador.refresh_from_db()
        self.assertEqual(self.administrador.rol.tipo, Rol.Tipo.ENCARGADO)
        self.assertEqual(self._acceder_admin("8642").status_code, 400)

    def test_elevado_opera_el_panel_pero_no_acciones_reservadas(self):
        alta = self._crear_elevado()
        elevado_id = alta.json()["usuario"]["id"]

        acceso = self._acceder_admin("2468")
        self.assertEqual(acceso.status_code, 200)
        self.assertEqual(acceso.json()["nivel"], "elevado")
        self.assertNotIn("2468", json.dumps(acceso.json()))
        self.assertTrue(validar_clave_administrativa(self.sucursal, "2468"))
        self.assertEqual(
            acceso.json()["permisos"],
            {
                "gestionar_usuarios": False,
                "reiniciar_folios": False,
                "cambiar_clave_maestra": False,
            },
        )

        resumen = self.client.get("/api/administrador/resumen/")
        self.assertEqual(resumen.status_code, 200)
        movimiento = self.client.post(
            "/api/administrador/movimientos/",
            data=json.dumps(
                {"tipo": "ingreso", "concepto": "Cambio", "importe": "100"}
            ),
            content_type="application/json",
        )
        self.assertEqual(movimiento.status_code, 201)
        self.assertTrue(MovimientoCaja.objects.filter(sucursal=self.sucursal).exists())

        intentos_reservados = (
            self.client.post(
                "/api/administrador/usuarios/",
                data=json.dumps(
                    {
                        "nombre": "No permitido",
                        "tipo": Rol.Tipo.MESERO,
                        "clave": "4444",
                    }
                ),
                content_type="application/json",
            ),
            self.client.patch(
                f"/api/administrador/usuarios/{elevado_id}/",
                data=json.dumps({"nombre": "Cambio no permitido", "tipo": Rol.Tipo.ELEVADO}),
                content_type="application/json",
            ),
            self.client.post(
                "/api/administrador/clave/",
                data=json.dumps(
                    {"clave_administrador": "0000", "nueva_clave": "5555"}
                ),
                content_type="application/json",
            ),
            self.client.post(
                "/api/administrador/folios/reiniciar/",
                data="{}",
                content_type="application/json",
            ),
        )
        for respuesta in intentos_reservados:
            self.assertEqual(respuesta.status_code, 403)
            self.assertEqual(
                respuesta.json()["codigo"],
                "administrador_maestro_requerido",
            )
        self.assertFalse(
            UsuarioPOS.objects.filter(sucursal=self.sucursal, nombre="No permitido").exists()
        )
        self.assertTrue(
            check_password(
                "0000",
                ConfiguracionSucursal.objects.get(
                    sucursal=self.sucursal
                ).clave_administrador,
            )
        )

        UsuarioPOS.objects.filter(pk=elevado_id).update(activo=False)
        sesion_revocada = self.client.get("/api/administrador/resumen/")
        self.assertEqual(sesion_revocada.status_code, 401)

    def test_todos_los_pines_activos_y_la_clave_maestra_identifican_ventas(self):
        self._crear_elevado()
        casos = (
            ("0000", "Administrador de prueba"),
            ("9876", "Administrador de prueba"),
            ("2468", "Supervisora"),
            ("1111", "Operador de prueba"),
            ("2222", "Repartidor de prueba"),
        )
        for pin, nombre in casos:
            with self.subTest(pin=pin):
                respuesta = self.client.post(
                    "/api/operador/identificar/",
                    data=json.dumps({"clave": pin}),
                    content_type="application/json",
                )
                self.assertEqual(respuesta.status_code, 200)
                self.assertEqual(respuesta.json()["operador"]["nombre"], nombre)
                self.assertNotIn("clave", respuesta.json()["operador"])

        inactivo = self._crear_usuario(
            "Usuario inactivo",
            self.rol_mesero,
            "3333",
            activo=False,
        )
        respuesta = self.client.post(
            "/api/operador/identificar/",
            data=json.dumps({"clave": "3333"}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertFalse(inactivo.check_clave("0000"))

    def test_no_permite_colisiones_entre_clave_maestra_y_pines(self):
        self.assertEqual(self._acceder_admin("0000").status_code, 200)
        duplicado_maestro = self.client.post(
            "/api/administrador/usuarios/",
            data=json.dumps(
                {
                    "nombre": "Duplicado",
                    "tipo": Rol.Tipo.ELEVADO,
                    "clave": "0000",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(duplicado_maestro.status_code, 400)

        duplicado_usuario = self.client.post(
            "/api/administrador/clave/",
            data=json.dumps(
                {"clave_administrador": "0000", "nueva_clave": "1111"}
            ),
            content_type="application/json",
        )
        self.assertEqual(duplicado_usuario.status_code, 400)
        self.assertTrue(validar_clave_administrativa(self.sucursal, "0000"))

class InterfazPermisosAdministrativosTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        base = Path(__file__).resolve().parent
        cls.html = (base / "templates" / "ventas" / "administrador.html").read_text(
            encoding="utf-8"
        )
        cls.javascript = (base / "static" / "ventas" / "admin.js").read_text(
            encoding="utf-8"
        )

    def test_controles_exclusivos_del_maestro_nacen_ocultos_y_marcados(self):
        for contrato in (
            'data-panel="personal" data-permiso-admin="gestionar_usuarios" type="button" hidden',
            'data-admin-panel="personal" data-permiso-admin="gestionar_usuarios" hidden',
            'id="form-clave" class="formulario-admin formulario-clave" data-permiso-admin="cambiar_clave_maestra" hidden',
            'class="hoja zona-peligro" data-permiso-admin="reiniciar_folios"',
        ):
            with self.subTest(contrato=contrato):
                self.assertIn(contrato, self.html)

        for panel in (
            "inicio",
            "pedidos",
            "domicilios",
            "programados",
            "movimientos",
            "reportes",
            "sucursales",
        ):
            with self.subTest(panel=panel):
                linea = next(
                    linea
                    for linea in self.html.splitlines()
                    if f'data-panel="{panel}"' in linea
                )
                self.assertNotIn("data-permiso-admin", linea)

    def test_formulario_personal_crea_elevados_y_edita_el_operador_sin_precargar_pin(self):
        self.assertIn('<option value="elevado">Elevado</option>', self.html)
        self.assertIn(
            '<option id="usuario-tipo-encargado" value="encargado" hidden disabled>Operador principal</option>',
            self.html,
        )
        for contrato in (
            'tipo: $("#usuario-tipo").value',
            'configurarTipoUsuario(datos.tipo);',
            'const esOperadorPrincipal = tipo === "encargado";',
            'selector.disabled = esOperadorPrincipal;',
            '$("#usuario-clave").value = "";',
            '$("#usuario-clave").required = false;',
        ):
            with self.subTest(contrato=contrato):
                self.assertIn(contrato, self.javascript)
        self.assertNotIn('$("#usuario-clave").value = datos.clave', self.javascript)
        self.assertNotIn('usuario.clave', self.javascript)

    def test_javascript_aplica_visibilidad_y_bloquea_eventos_reservados(self):
        for contrato in (
            'estado.administrador?.acceso?.permisos?.[nombre] === true',
            'if (!destino || !elementoPermitido(destino)) nombre = "inicio";',
            'estado.administrador.usuarios = [];',
            'if (!exigirPermisoAdministrador("gestionar_usuarios")) return;',
            'if (!exigirPermisoAdministrador("reiniciar_folios")) return;',
            'if (!exigirPermisoAdministrador("cambiar_clave_maestra")) return;',
        ):
            with self.subTest(contrato=contrato):
                self.assertIn(contrato, self.javascript)
