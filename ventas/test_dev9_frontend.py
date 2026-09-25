import json
import subprocess
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.test import SimpleTestCase, TestCase, override_settings

from personas.models import Rol, Sucursal, UsuarioPOS
from ventas.models import ConfiguracionSucursal


RAIZ_VENTAS = Path(__file__).resolve().parent
PLANTILLA_POS = RAIZ_VENTAS / "templates" / "ventas" / "inicio.html"
PLANTILLA_ADMIN = RAIZ_VENTAS / "templates" / "ventas" / "administrador.html"
JS_POS = RAIZ_VENTAS / "static" / "ventas" / "app.js"
CSS_POS = RAIZ_VENTAS / "static" / "ventas" / "app.css"
JS_ADMIN = RAIZ_VENTAS / "static" / "ventas" / "admin.js"
CSS_ADMIN = RAIZ_VENTAS / "static" / "ventas" / "admin.css"


class ContratoFrontendDev9Tests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pos_html = PLANTILLA_POS.read_text(encoding="utf-8")
        cls.admin_html = PLANTILLA_ADMIN.read_text(encoding="utf-8")
        cls.pos_js = JS_POS.read_text(encoding="utf-8")
        cls.pos_css = CSS_POS.read_text(encoding="utf-8")
        cls.admin_js = JS_ADMIN.read_text(encoding="utf-8")
        cls.admin_css = CSS_ADMIN.read_text(encoding="utf-8")

    def ejecutar_javascript(self, codigo):
        resultado = subprocess.run(
            ["node", "-e", codigo],
            cwd=RAIZ_VENTAS.parent,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(
            resultado.returncode,
            0,
            msg=f"Node falló.\\nSTDOUT:\\n{resultado.stdout}\\nSTDERR:\\n{resultado.stderr}",
        )

    def test_node_abrir_llega_a_fetch_y_uuid_recibe_contrato(self):
        ruta_js = json.dumps(str(JS_POS))
        self.ejecutar_javascript(
            f"""
const assert = require("node:assert/strict");
const core = require({ruta_js});
const ticketId = "11111111-1111-4111-8111-111111111111";
const llamadas = [];
const fetchFalso = async (url, opciones) => {{
  llamadas.push({{ url, opciones }});
  return {{ ok: true, status: 200, json: async () => ({{ ok: true }}) }};
}};
(async () => {{
  const urlAbrir = "/api/tickets/abrir/";
  assert.equal(core.ticketIdDeMutacion(urlAbrir, ticketId), "");
  await core.solicitarJson(
    fetchFalso,
    urlAbrir,
    {{ method: "POST", body: JSON.stringify({{ mesa_id: "mesa-1" }}) }},
  );
  assert.equal(llamadas.length, 1, "abrir mesa debe llegar a fetch");
  assert.deepEqual(JSON.parse(llamadas[0].opciones.body), {{ mesa_id: "mesa-1" }});

  const urlMutacion = "/api/tickets/" + ticketId + "/partidas/";
  const idDetectado = core.ticketIdDeMutacion(urlMutacion, "");
  assert.equal(idDetectado, ticketId);
  assert.equal(core.requiereContratoTicket(urlMutacion, "POST", idDetectado), true);
  await core.solicitarJson(
    fetchFalso,
    urlMutacion,
    {{ method: "POST", body: JSON.stringify({{ producto_id: "producto-1" }}) }},
    {{
      ticketId,
      ticket: {{ id: ticketId, version_entidad: 7 }},
      deviceId: "terminal-prueba",
      edicionProgramada: false,
    }},
  );
  assert.equal(llamadas.length, 2);
  const cuerpo = JSON.parse(llamadas[1].opciones.body);
  assert.equal(cuerpo.producto_id, "producto-1");
  assert.equal(cuerpo.version_entidad, 7);
  assert.equal(cuerpo.device_id, "terminal-prueba");
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
        )

    def test_node_conflicto_adopta_payload_sin_reenviar_cuerpo(self):
        ruta_js = json.dumps(str(JS_POS))
        self.ejecutar_javascript(
            f"""
const assert = require("node:assert/strict");
const core = require({ruta_js});
const ticketId = "22222222-2222-4222-8222-222222222222";
let llamadas = 0;
const ticketServidor = {{ id: ticketId, version_entidad: 12, total: "240.00" }};
const fetchFalso = async () => {{
  llamadas += 1;
  return {{
    ok: false,
    status: 409,
    json: async () => ({{
      codigo: "version_entidad_desactualizada",
      error: "Versión anterior",
      ticket: ticketServidor,
    }}),
  }};
}};
(async () => {{
  const resultado = await core.solicitarJson(
    fetchFalso,
    "/api/tickets/" + ticketId + "/",
    {{ method: "PATCH", body: JSON.stringify({{ nombres_comensales: {{ 1: "Ana" }} }}) }},
    {{
      ticketId,
      ticket: {{ id: ticketId, version_entidad: 11 }},
      deviceId: "terminal-prueba",
    }},
  );
  assert.equal(llamadas, 1, "un 409 externo no debe reenviar el reemplazo");
  assert.equal(resultado.respuesta.status, 409);
  const estado = {{ ticket: {{ id: ticketId, version_entidad: 11 }} }};
  assert.equal(core.adoptarTicketRespuesta(estado, resultado.datos, ticketId), true);
  assert.equal(estado.ticket.version_entidad, 12);
  assert.match(core.mensajeConflictoVersion(), /revisa la comanda/i);
  assert.match(core.mensajeConflictoVersion(), /vuelve a confirmar/i);
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
        )
        self.assertNotIn(
            "return ejecutarApi(url, opciones, { ...contrato, reintento: 1 });",
            self.pos_js,
        )
        self.assertIn(
            "mostrarTicket({ descartarBorradorClienteLlevar: true });",
            self.pos_js,
        )
        self.assertIn("if (error.requiereConfirmacion) {", self.pos_js)

    def test_node_nombre_llevar_conserva_edicion_hecha_durante_el_patch(self):
        ruta_js = json.dumps(str(JS_POS))
        self.ejecutar_javascript(
            f"""
const assert = require("node:assert/strict");
const core = require({ruta_js});
const sinCambio = core.resolverGuardadoNombreClienteLlevar({{
  nombreEnviado: "Ana",
  nombreConfirmado: "ANA",
  nombreActual: "Ana",
  revisionEnviada: 4,
  revisionActual: 4,
}});
assert.deepEqual(sinCambio, {{
  edicionPosterior: false,
  nombreVisible: "ANA",
}});

const conCambio = core.resolverGuardadoNombreClienteLlevar({{
  nombreEnviado: "Ana",
  nombreConfirmado: "Ana",
  nombreActual: "Beatriz",
  revisionEnviada: 4,
  revisionActual: 5,
}});
assert.deepEqual(conCambio, {{
  edicionPosterior: true,
  nombreVisible: "Beatriz",
}});
"""
        )
        inicio = self.pos_js.index("  function guardarNombreClienteLlevar()")
        fin = self.pos_js.index("  async function guardarDatos()", inicio)
        guardado = self.pos_js[inicio:fin]
        self.assertIn("const revisionEnviada = estado.revisionClienteLlevar;", guardado)
        self.assertIn("const nombreActual = ticketId === estado.clienteLlevarTicketId", guardado)
        self.assertIn("? estado.clienteLlevarBorrador", guardado)
        self.assertIn("estado.clienteLlevarBorrador = resolucion.nombreVisible;", guardado)
        self.assertIn("if (resolucion.edicionPosterior)", guardado)
        self.assertIn("programarGuardadoNombreClienteLlevar();\n          return false;", guardado)
        self.assertIn("escapar(nombreClienteLlevar)", self.pos_js)

    def test_mutaciones_se_encolan_y_validan_el_mismo_ticket(self):
        inicio = self.pos_js.index("  async function api(")
        fin = self.pos_js.index("  function ticketActivo(", inicio)
        contrato = self.pos_js[inicio:fin]

        self.assertIn("colasMutacionesTicket: new Map()", self.pos_js)
        self.assertIn(
            "estado.colasMutacionesTicket.get(contrato.ticketId)",
            contrato,
        )
        self.assertIn("colaAnterior.then(ejecutar, ejecutar)", contrato)
        self.assertGreaterEqual(
            contrato.count("ticketParaContrato(contrato.ticketId)"),
            2,
        )
        self.assertIn('error.codigo = "contexto_ticket_cambio";', self.pos_js)
        self.assertIn(
            "adoptarTicketRespuesta(estado, datos, contrato.ticketId)",
            contrato,
        )

    def test_directorio_es_una_vista_paginable_y_vuelve_al_pos(self):
        for identificador in (
            "abrir-directorio",
            "vista-directorio",
            "cerrar-directorio",
            "directorio-buscar",
            "directorio-lista",
            "directorio-cargar-mas",
            "directorio-nuevo-cliente",
        ):
            self.assertIn(f'id="{identificador}"', self.pos_html)

        self.assertIn('api("/api/clientes/buscar/"', self.pos_js)
        self.assertIn("limite: 20", self.pos_js)
        self.assertIn("pagina,", self.pos_js)
        self.assertIn("datos.hay_mas", self.pos_js)
        self.assertIn("function cerrarDirectorio()", self.pos_js)
        self.assertIn(
            '$("#vista-posiciones").classList.remove("oculto");',
            self.pos_js,
        )

    def test_modal_de_directorio_guarda_sin_asignar_a_un_ticket(self):
        self.assertIn(
            'function abrirFormularioCliente(cliente = null, contexto = "pedido")',
            self.pos_js,
        )
        self.assertIn("estado.clienteFormularioContexto = contexto;", self.pos_js)
        inicio = self.pos_js.index("  async function guardarFormularioCliente(")
        fin = self.pos_js.index("  async function editarClienteSeleccionado(", inicio)
        guardado = self.pos_js[inicio:fin]
        directorio = guardado.index(
            'if (estado.clienteFormularioContexto === "directorio")'
        )
        asignacion = guardado.index("await asignarCliente(")
        self.assertLess(directorio, asignacion)
        self.assertIn("await cargarDirectorio({ reiniciar: true });", guardado)
        self.assertIn("return;", guardado[directorio:asignacion])

    def test_movimientos_usa_capacidad_del_servidor_y_revalida_clave(self):
        self.assertIn('id="abrir-movimientos"', self.pos_html)
        self.assertIn(
            "Boolean(estado.operador?.puede_acceder_movimientos)",
            self.pos_js,
        )
        self.assertNotIn(
            '["elevado", "encargado"].includes(estado.operador?.tipo)',
            self.pos_js,
        )
        self.assertIn(
            '$("#abrir-movimientos")?.addEventListener("click", () => abrirAdministrador("movimientos"))',
            self.pos_js,
        )
        inicio = self.pos_js.index("  async function abrirAdministrador(")
        fin = self.pos_js.index("  function bloquear(", inicio)
        acceso = self.pos_js[inicio:fin]
        self.assertIn('pedirClavePos("Clave de administrador"', acceso)
        self.assertIn('+ "#" + panel', acceso)

    def test_fullscreen_es_solo_icono_sin_aviso_persistente(self):
        self.assertNotIn('id="estado-pantalla-completa"', self.pos_html)
        self.assertNotIn("<span>Pantalla completa</span>", self.pos_html)
        self.assertNotIn("<span>Pantalla completa</span>", self.admin_html)
        self.assertNotIn('querySelector("span")', self.admin_js)
        self.assertNotIn(".estado-pantalla-completa", self.pos_css)
        self.assertIn('boton.setAttribute("aria-label", etiqueta);', self.pos_js)
        self.assertIn('boton.setAttribute("aria-label", etiqueta);', self.admin_js)

    def test_arranque_reanuda_operador_de_escritorio_sin_alterar_tableta(self):
        inicio = self.pos_js.index("  async function reanudarSesionOperador()")
        fin = self.pos_js.index("  async function entrarComoMesero(", inicio)
        reanudacion = self.pos_js[inicio:fin]

        self.assertIn("if (modoTableta || idProgramadoSolicitado()) return false;", reanudacion)
        self.assertIn('api("/api/operador/actual/")', reanudacion)
        self.assertIn("if (!datos.operador) return false;", reanudacion)
        self.assertIn('$("#pantalla-acceso")?.classList.add("oculto");', reanudacion)
        self.assertIn('$("main").classList.remove("oculto");', reanudacion)
        self.assertIn('document.body.classList.add("en-operacion");', reanudacion)
        self.assertIn("renderOperadorActual();", reanudacion)
        self.assertIn("await cargarEstado(estado.canal === \"sucursales\");", reanudacion)
        self.assertIn("programarSincronizacionSucursales();", reanudacion)
        self.assertIn("async function iniciarAplicacion()", self.pos_js)
        self.assertIn("iniciarAplicacion();", self.pos_js)
        self.assertNotIn("reanudarSesionOperador();\n  abrirProgramadoDesdeEnlace();", self.pos_js)
    def test_node_comanda_separa_barbacoa_por_persona_y_complementos_globales(self):
        ruta_js = json.dumps(str(JS_POS))
        self.ejecutar_javascript(
            f"""
const assert = require("node:assert/strict");
const core = require({ruta_js});
const partidas = [
  {{ codigo: "T1", producto_id: "p1", comensal: 1 }},
  {{ codigo: "T2", producto_id: "p2", comensal: 1 }},
  {{ codigo: "T3", producto_id: "p3", comensal: 2 }},
  {{ codigo: "T4", producto_id: "p4", comensal: 2 }},
  {{ codigo: "BBQ05", producto_id: "bbq", comensal: 2 }},
  {{ codigo: "CO8", producto_id: "consome", comensal: 1 }},
  {{ codigo: "AGUA", producto_id: "bebida", comensal: 2, bebida: true }},
];
const grupos = core.clasificarPartidasPorNombres(partidas, item => Boolean(item.bebida));
assert.deepEqual(grupos.principales.map(item => item.codigo), ["T1", "T2", "T3", "T4"]);
assert.deepEqual(grupos.barbacoa.map(item => [item.codigo, item.comensal]), [["BBQ05", 2]]);
assert.deepEqual(
  grupos.complementosGlobales.map(item => item.codigo),
  ["CO8", "AGUA"],
);
"""
        )
        inicio = self.pos_js.index("  function renderComandaPorNombres(")
        fin = self.pos_js.index("  function renderPedidoSucursal(", inicio)
        comanda = self.pos_js[inicio:fin]
        self.assertIn("...principales, ...barbacoa", comanda)
        self.assertIn('class="comanda-barbacoa-nombres"', comanda)
        self.assertIn('data-celda-producto=', comanda)
        self.assertIn('data-seleccionar-persona=', comanda)
        self.assertIn("for (const partida of complementosGlobales)", comanda)
        self.assertIn(".comanda-barbacoa-concepto {", self.pos_css)
        self.assertIn("min-height: 44px;", self.pos_css)

    def test_sucursal_puede_reactivarse_con_clave_desde_el_ticket(self):
        self.assertIn('id="reactivar-sucursal"', self.pos_html)
        self.assertIn(
            '"/api/tickets/" + estado.ticket.id + "/reactivar-sucursal/"',
            self.pos_js,
        )
        self.assertIn('"Reactivar pedido de sucursal"', self.pos_js)
        self.assertIn("clave_administrador: claveAdministrador", self.pos_js)
        self.assertIn(
            '$("#reactivar-sucursal").classList.toggle("oculto", !muestraReactivarSucursal);',
            self.pos_js,
        )

    def test_vista_previa_de_corte_imprime_sin_usar_el_cierre(self):
        self.assertIn('id="vista-previa-corte"', self.admin_html)
        self.assertIn(
            'url: "/api/administrador/corte-caja/previa/"',
            self.admin_js,
        )
        self.assertIn("El turno sigue abierto.", self.admin_js)
        self.assertIn("vistaPrevia.disabled = bloqueos.length > 0;", self.admin_js)

    def test_movimientos_y_totales_conservan_lectura_y_objetivos_tactiles(self):
        self.assertIn(".comanda-total {", self.pos_css)
        self.assertIn("font-size: 1.28rem !important;", self.pos_css)
        self.assertIn("font-weight: 900 !important;", self.pos_css)
        self.assertIn(".vista-directorio {", self.pos_css)
        self.assertIn("min-height: 44px;", self.pos_css)
        self.assertIn(
            "grid-template-columns: repeat(auto-fit, minmax(126px, 1fr));",
            self.admin_css,
        )
        self.assertIn("@container movimientos (max-width: 780px)", self.admin_css)
        self.assertIn("@container movimientos (max-width: 520px)", self.admin_css)
        self.assertIn(".hoja-captura-movimiento {", self.admin_css)

@override_settings(SUCURSAL_CLAVE="ARBOLEDAS", POS_REQUIRE_AUTH=False, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CapacidadMovimientosOperadorDev9Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sucursal = Sucursal.objects.create(
            clave="ARBOLEDAS",
            nombre="Arboledas",
        )
        ConfiguracionSucursal.objects.create(
            sucursal=cls.sucursal,
            clave_administrador=make_password("0000"),
        )
        rol_encargado = Rol.objects.create(
            sucursal=cls.sucursal,
            nombre="Encargado",
            tipo=Rol.Tipo.ENCARGADO,
        )
        rol_elevado = Rol.objects.create(
            sucursal=cls.sucursal,
            nombre="Elevado",
            tipo=Rol.Tipo.ELEVADO,
        )
        cls.encargado = cls._crear_usuario(
            cls.sucursal,
            rol_encargado,
            "Encargado con PIN",
            "1111",
        )
        cls.elevado = cls._crear_usuario(
            cls.sucursal,
            rol_elevado,
            "Supervisión",
            "2222",
        )
        cls.cuenta = get_user_model().objects.create_user(
            username="encargado-dev9",
            password="clave-django-pruebas",
        )
        cls.encargado.cuenta = cls.cuenta
        cls.encargado.save(update_fields=["cuenta"])

    @staticmethod
    def _crear_usuario(sucursal, rol, nombre, clave):
        perfil = UsuarioPOS(
            sucursal=sucursal,
            rol=rol,
            nombre=nombre,
        )
        perfil.set_clave(clave)
        perfil.save()
        return perfil

    def _identificar(self, clave):
        return self.client.post(
            "/api/operador/identificar/",
            data=json.dumps({"clave": clave}),
            content_type="application/json",
        )

    def _actual(self):
        return self.client.get("/api/operador/actual/")

    def test_pin_normal_de_encargado_no_expone_movimientos(self):
        respuesta = self._identificar("1111")

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["operador"]["tipo"], Rol.Tipo.ENCARGADO)
        self.assertIs(
            respuesta.json()["operador"]["puede_acceder_movimientos"],
            False,
        )
        self.assertIs(
            self._actual().json()["operador"]["puede_acceder_movimientos"],
            False,
        )

    def test_pin_elevado_expone_movimientos(self):
        respuesta = self._identificar("2222")

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["operador"]["tipo"], Rol.Tipo.ELEVADO)
        self.assertIs(
            respuesta.json()["operador"]["puede_acceder_movimientos"],
            True,
        )
        self.assertIs(
            self._actual().json()["operador"]["puede_acceder_movimientos"],
            True,
        )

    @override_settings(POS_REQUIRE_AUTH=True)
    def test_pin_ajeno_no_eleva_la_sesion_y_salir_limpia_el_operador(self):
        self.client.force_login(self.cuenta)
        identificacion = self._identificar("2222")
        self.assertEqual(identificacion.status_code, 200)

        actual = self._actual()
        self.assertEqual(actual.status_code, 200)
        self.assertEqual(actual.json()["operador"]["id"], str(self.elevado.id))
        self.assertIs(
            actual.json()["operador"]["puede_acceder_movimientos"],
            False,
        )
        self.assertEqual(
            self.client.post(
                "/api/administrador/acceso/",
                data=json.dumps({"clave_administrador": "2222"}),
                content_type="application/json",
            ).status_code,
            403,
        )

        salida = self.client.post(
            "/api/operador/salir/",
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(salida.status_code, 200)
        self.assertEqual(self._actual().json(), {"operador": None})

    def test_clave_maestra_expone_movimientos_aunque_payload_sea_encargado(self):
        respuesta = self._identificar("0000")

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["operador"]["tipo"], Rol.Tipo.ENCARGADO)
        self.assertIs(
            respuesta.json()["operador"]["puede_acceder_movimientos"],
            True,
        )
        self.assertIs(
            self._actual().json()["operador"]["puede_acceder_movimientos"],
            True,
        )
