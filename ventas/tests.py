import json
import tempfile
from collections import OrderedDict
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from catalogo.models import Producto
from impresion.models import TrabajoImpresion
from catalogo.configuracion_menu import configuracion_producto
from impresion.render import (
    _segmentos_bebidas,
    _texto_entrega,
    _texto_salsas,
    escpos_raster,
    render_comanda,
    render_cuenta,
    render_domicilio,
)
from impresion.services import encolar_impresiones
from personas.models import Sucursal

from .models import Cliente, EventoOutbox, Mesa, ModificadorTicket, Ticket
from .orden import ordenar_partidas
from .services import (
    ErrorVenta,
    abrir_ticket,
    agregar_partida,
    alternar_comentario_general,
    alternar_modificador,
    cobrar_ticket,
    procesar_ticket,
)


class FlujoPOSTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("cargar_datos_iniciales", verbosity=0)

    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory()
        self.ajustes = override_settings(MEDIA_ROOT=self.temporal.name, PRINT_SYNC=True, PRINT_BACKEND="archivo")
        self.ajustes.enable()
        self.sucursal = Sucursal.objects.get(clave="ARBOLEDAS")
        self.producto = Producto.objects.get(sucursal=self.sucursal, codigo="TB")

    def tearDown(self):
        self.ajustes.disable()
        self.temporal.cleanup()

    def datos_cliente(self, nombre="María López", telefono="3312345678", exterior="5860"):
        return {
            "nombre": nombre,
            "notas": "Cliente de prueba",
            "telefonos": [{"etiqueta": "Celular", "numero": telefono}],
            "domicilios": [
                {
                    "etiqueta": "Empresa",
                    "calle": "Avenida Patria",
                    "numero_exterior": exterior,
                    "numero_interior": "3",
                    "colonia": "Jardines",
                    "codigo_postal": "45000",
                    "municipio": "Zapopan",
                    "referencia": "Recepción principal",
                }
            ],
        }

    def test_catalogo_confirmado_del_menu(self):
        self.assertEqual(Producto.objects.filter(sucursal=self.sucursal, activo=True).count(), 47)
        self.assertEqual(self.producto.precio_actual().importe, Decimal("25.00"))
        self.assertEqual(Mesa.objects.filter(sucursal=self.sucursal, canal="comedor").count(), 24)

        precios = {
            producto.codigo: producto.precio_actual().importe
            for producto in Producto.objects.filter(
                sucursal=self.sucursal,
                codigo__in=["QKH", "QKM", "LOBQ", "LOKSQ", "LOCSQ", "HR05", "HR1", "CC"],
            )
        }
        self.assertEqual(precios["QKH"], Decimal("25.00"))
        self.assertEqual(precios["QKM"], Decimal("25.00"))
        self.assertEqual(precios["LOBQ"], Decimal("75.00"))
        self.assertEqual(precios["LOKSQ"], Decimal("85.00"))
        self.assertEqual(precios["LOCSQ"], Decimal("85.00"))
        self.assertEqual(precios["HR05"], Decimal("30.00"))
        self.assertEqual(precios["HR1"], Decimal("50.00"))
        self.assertEqual(precios["CC"], Decimal("30.00"))
        self.assertEqual(Producto.objects.get(sucursal=self.sucursal, codigo="PB").precio_actual().importe, Decimal("95.00"))
        self.assertEqual(
            Producto.objects.filter(sucursal=self.sucursal, categoria__nombre="Postre", activo=True).count(),
            5,
        )

    def test_terminos_predeterminados_y_abreviaturas_del_menu(self):
        bistec = Producto.objects.get(sucursal=self.sucursal, codigo="TBI")
        consome = Producto.objects.get(sucursal=self.sucursal, codigo="CO05")
        self.assertTrue(self.producto.permite_termino)
        self.assertEqual(self.producto.termino_predeterminado, Producto.Termino.DORADO)
        self.assertEqual(self.producto.nombre_corto, "TD")
        self.assertEqual(self.producto.abreviaturas_termino["medio"], "TM")
        self.assertEqual(bistec.termino_predeterminado, Producto.Termino.BLANDO)
        self.assertEqual(bistec.nombre_corto, "TKB")
        self.assertEqual(consome.nombre_corto, "CO 1/2")
        self.assertEqual(
            configuracion_producto("P63", "Agua de horchata rosa de medio litro")["nombre_corto"],
            "HR 1/2",
        )

    def test_teclado_actualiza_cantidad_termino_y_elimina_grupo(self):
        mesa = Mesa.objects.get(sucursal=self.sucursal, clave="MESA-6")
        bistec = Producto.objects.get(sucursal=self.sucursal, codigo="TBI")
        ticket, _ = abrir_ticket(mesa)
        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/partidas/",
            data=json.dumps({"producto_id": str(bistec.id), "comensal": 3, "cantidad": 1}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        partida = respuesta.json()["ticket"]["partidas"][0]
        self.assertEqual(partida["termino"], "blando")
        self.assertEqual(partida["nombre_corto"], "TKB")

        url = f"/api/tickets/{ticket.id}/partidas/ajustar/"
        respuesta = self.client.post(
            url,
            data=json.dumps({"partida_ids": [partida["id"]], "cantidad": 12, "termino": "dorado"}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        partida = respuesta.json()["ticket"]["partidas"][0]
        self.assertEqual(partida["cantidad"], "12.000")
        self.assertEqual(partida["termino"], "dorado")
        self.assertEqual(partida["nombre_corto"], "TKD")
        self.assertIn("dorado", partida["nombre"].lower())

        for cantidad_invalida in [0, 100, 1.5]:
            respuesta = self.client.post(
                url,
                data=json.dumps({"partida_ids": [partida["id"]], "cantidad": cantidad_invalida}),
                content_type="application/json",
            )
            self.assertEqual(respuesta.status_code, 400)

        respuesta = self.client.post(
            url,
            data=json.dumps({"partida_ids": [partida["id"]], "cantidad": 1, "eliminar": True}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["ticket"]["partidas"], [])

    def test_cambiar_termino_acumula_una_fila_ya_existente(self):
        mesa = Mesa.objects.get(sucursal=self.sucursal, clave="MESA-7")
        bistec = Producto.objects.get(sucursal=self.sucursal, codigo="TBI")
        ticket, _ = abrir_ticket(mesa)
        blando = agregar_partida(ticket, bistec, comensal=2, termino="blando")
        agregar_partida(ticket, bistec, comensal=2, cantidad=Decimal("2"), termino="dorado")
        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/partidas/ajustar/",
            data=json.dumps({"partida_ids": [str(blando.id)], "cantidad": 1, "termino": "dorado"}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        partidas = respuesta.json()["ticket"]["partidas"]
        self.assertEqual(len(partidas), 1)
        self.assertEqual(partidas[0]["cantidad"], "3.000")
        self.assertEqual(partidas[0]["nombre_corto"], "TKD")

    def test_bebidas_forman_una_sola_cadena_y_omiten_cantidad_uno(self):
        grupos = _segmentos_bebidas(
            OrderedDict((("AM", Decimal("1")), ("HR 1/2", Decimal("2")))),
            "normal",
            "negrita",
        )
        texto = "".join(fragmento for grupo in grupos for fragmento, _ in grupo).strip()
        self.assertEqual(texto, "AM * ( 2 ) HR 1/2")
        self.assertNotIn("( 1 )", texto)
        self.assertIn(("( 2 ) ", "negrita"), grupos[1])

    def test_flujo_completo_genera_outbox_y_dos_formatos(self):
        mesa = Mesa.objects.get(sucursal=self.sucursal, clave="MESA-2")
        ticket, creado = abrir_ticket(mesa)
        self.assertTrue(creado)
        agregar_partida(ticket, self.producto, comensal=1, cantidad=Decimal("2"))
        agregar_partida(ticket, self.producto, comensal=2, cantidad=Decimal("1"))
        alternar_modificador(ticket, 1, "CEB", "CEBOLLA")
        ticket.refresh_from_db()
        self.assertEqual(ticket.total, Decimal("75.00"))

        procesar_ticket(ticket)
        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/imprimir/",
            data=json.dumps({"formato": "comanda"}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        trabajo = TrabajoImpresion.objects.get(formato="comanda", ticket=ticket)
        self.assertEqual(trabajo.estado, TrabajoImpresion.Estado.GENERADO)
        self.assertTrue((Path(self.temporal.name) / trabajo.archivo).is_file())

        cobrar_ticket(ticket, Ticket.FormaPago.EFECTIVO, Decimal("100"))
        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/imprimir/",
            data=json.dumps({"formato": "cuenta"}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        cuenta = TrabajoImpresion.objects.get(formato="cuenta", ticket=ticket)
        self.assertTrue((Path(self.temporal.name) / cuenta.archivo).is_file())
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, Ticket.Estado.PAGADO)
        self.assertGreaterEqual(EventoOutbox.objects.filter(agregado_id=ticket.id).count(), 6)

    def test_apertura_disponible_en_los_tres_canales(self):
        for canal in [Mesa.Canal.COMEDOR, Mesa.Canal.DOMICILIO, Mesa.Canal.SUCURSALES]:
            mesa = Mesa.objects.filter(sucursal=self.sucursal, canal=canal).first()
            respuesta = self.client.post(
                "/api/tickets/abrir/",
                data=json.dumps({"mesa_id": str(mesa.id)}),
                content_type="application/json",
            )
            self.assertEqual(respuesta.status_code, 200)
            self.assertEqual(respuesta.json()["ticket"]["canal"], canal)

    def test_clientes_se_buscan_por_cualquier_dato_y_pueden_compartir_contacto(self):
        primero = self.client.post(
            "/api/clientes/",
            data=json.dumps(self.datos_cliente()),
            content_type="application/json",
        )
        self.assertEqual(primero.status_code, 201)
        cliente = primero.json()["cliente"]
        self.assertEqual(len(cliente["clave_corta"]), 6)

        segundo = self.client.post(
            "/api/clientes/",
            data=json.dumps(self.datos_cliente(nombre="Carlos Pérez")),
            content_type="application/json",
        )
        self.assertEqual(segundo.status_code, 201)
        self.assertEqual(Cliente.objects.count(), 2)

        for consulta in ["5860", "3312345678", "María", cliente["clave_corta"], "recepcion"]:
            respuesta = self.client.get("/api/clientes/buscar/", {"q": consulta})
            self.assertEqual(respuesta.status_code, 200)
            nombres = [resultado["nombre"] for resultado in respuesta.json()["resultados"]]
            self.assertIn("María López", nombres)

    def test_duplicado_solo_se_advierte_por_nombre_y_puede_confirmarse(self):
        datos = self.datos_cliente(nombre="José Álvarez")
        self.client.post("/api/clientes/", data=json.dumps(datos), content_type="application/json")
        repetido = self.datos_cliente(nombre="Jose Alvarez", telefono="3399999999", exterior="100")
        respuesta = self.client.post("/api/clientes/", data=json.dumps(repetido), content_type="application/json")
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(respuesta.json()["duplicados"][0]["nombre"], "José Álvarez")
        repetido["confirmar_duplicado"] = True
        respuesta = self.client.post("/api/clientes/", data=json.dumps(repetido), content_type="application/json")
        self.assertEqual(respuesta.status_code, 201)

    def test_domicilio_asigna_cliente_y_conserva_snapshot_historico(self):
        creado = self.client.post(
            "/api/clientes/", data=json.dumps(self.datos_cliente()), content_type="application/json"
        ).json()["cliente"]
        mesa = Mesa.objects.filter(sucursal=self.sucursal, canal=Mesa.Canal.DOMICILIO).first()
        ticket, _ = abrir_ticket(mesa)
        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/cliente/",
            data=json.dumps(
                {
                    "cliente_id": creado["id"],
                    "telefono_id": creado["telefonos"][0]["id"],
                    "domicilio_id": creado["domicilios"][0]["id"],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.cliente_telefono, "3312345678")
        self.assertIn("5860", ticket.cliente_domicilio)

        editados = self.datos_cliente(telefono="3388888888", exterior="700")
        editados["telefonos"][0]["id"] = creado["telefonos"][0]["id"]
        editados["domicilios"][0]["id"] = creado["domicilios"][0]["id"]
        respuesta = self.client.patch(
            f"/api/clientes/{creado['id']}/", data=json.dumps(editados), content_type="application/json"
        )
        self.assertEqual(respuesta.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.cliente_telefono, "3312345678")
        self.assertIn("5860", ticket.cliente_domicilio)

    def test_domicilio_exige_cliente_completo_antes_de_procesar(self):
        mesa = Mesa.objects.filter(sucursal=self.sucursal, canal=Mesa.Canal.DOMICILIO).first()
        ticket, _ = abrir_ticket(mesa)
        agregar_partida(ticket, self.producto)
        respuesta = self.client.post(f"/api/tickets/{ticket.id}/procesar/", data="{}", content_type="application/json")
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("cliente", respuesta.json()["error"].lower())

    def test_empresa_solicita_contacto_nuevo_en_cada_pedido(self):
        datos_cliente = {
            "nombre": "LIVERPOOL LA PERLA",
            "comentarios_multiples": True,
            "telefonos": [],
            "domicilios": [{"etiqueta": "Empresa", "calle": "PLAZA LA PERLA", "numero_exterior": ""}],
        }
        cliente = self.client.post(
            "/api/clientes/", data=json.dumps(datos_cliente), content_type="application/json"
        ).json()["cliente"]
        self.assertTrue(cliente["comentarios_multiples"])
        self.assertEqual(cliente["telefonos"], [])

        mesa = Mesa.objects.get(sucursal=self.sucursal, clave="DOM-3")
        ticket, _ = abrir_ticket(mesa)
        agregar_partida(ticket, self.producto)
        agregar_partida(ticket, Producto.objects.get(sucursal=self.sucursal, codigo="CC"), comensal=2)
        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/cliente/",
            data=json.dumps(
                {
                    "cliente_id": cliente["id"],
                    "telefono_id": "",
                    "domicilio_id": cliente["domicilios"][0]["id"],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/procesar/", data="{}", content_type="application/json"
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("contacto", respuesta.json()["error"].lower())

        respuesta = self.client.patch(
            f"/api/tickets/{ticket.id}/",
            data=json.dumps(
                {"contacto_pedido_nombre": "Moisés", "contacto_pedido_telefono": "3334670249"}
            ),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/procesar/", data="{}", content_type="application/json"
        )
        self.assertEqual(respuesta.status_code, 200)
        impresiones = respuesta.json()["impresiones"]
        self.assertEqual(len(impresiones), 2)
        self.assertEqual(
            {impresion["formato"] for impresion in impresiones},
            {TrabajoImpresion.Formato.COMANDA, TrabajoImpresion.Formato.DOMICILIO},
        )
        comanda = next(impresion for impresion in impresiones if impresion["formato"] == "comanda")
        self.assertEqual(comanda["destino"], TrabajoImpresion.Destino.COCINA)
        ticket.refresh_from_db()
        domicilio = render_domicilio(ticket)
        self.assertEqual(domicilio.width, 576)
        self.assertGreater(domicilio.height, 700)

    def test_tabletas_solo_presenta_el_acceso_de_comedor(self):
        respuesta = self.client.get("/tabletas/")
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'data-modo-tableta="true"')
        self.assertNotContains(respuesta, 'class="tableta-identidad"')
        self.assertContains(respuesta, 'id="pantalla-completa"')
        self.assertContains(respuesta, 'id="salir-tableta"')
        self.assertContains(respuesta, "manifest.webmanifest?modo=tableta")
        self.assertNotContains(respuesta, 'class="topbar"')
        self.assertNotContains(respuesta, 'data-canal="domicilio"')
        self.assertNotContains(respuesta, 'data-canal="sucursales"')
        manifest = self.client.get("/manifest.webmanifest?modo=tableta").json()
        self.assertEqual(manifest["id"], "/tabletas/")
        self.assertEqual(manifest["start_url"], "/tabletas/")
        self.assertEqual(manifest["display_override"], ["fullscreen", "standalone"])

    def test_pc_inicia_en_selector_de_perfil_y_admin_esta_deshabilitado(self):
        respuesta = self.client.get("/")
        self.assertContains(respuesta, 'id="pantalla-acceso"')
        self.assertContains(respuesta, 'id="entrar-mesero"')
        self.assertContains(respuesta, 'class="perfil-acceso administrador" type="button" disabled')
        self.assertNotContains(respuesta, "¿Cómo deseas entrar?")
        self.assertEqual(respuesta.content.decode().count('class="perfil-icono"'), 2)
        self.assertEqual(respuesta.content.decode().count('data-salir-mesero'), 2)

    def test_comensal_24_bebidas_y_preparacion_global(self):
        mesa = Mesa.objects.get(sucursal=self.sucursal, clave="MESA-20")
        bebida = Producto.objects.get(sucursal=self.sucursal, codigo="AM")
        ticket, _ = abrir_ticket(mesa)
        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/partidas/",
            data=json.dumps({"producto_id": str(bebida.id), "comensal": 24, "cantidad": 2}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["ticket"]["partidas"][0]["categoria"], "Bebidas")
        agregar_partida(ticket, self.producto, comensal=24)
        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/modificadores/",
            data=json.dumps({"comensales": [19, 20, 21, 22, 23, 24], "codigo": "C/T", "nombre": "Con todo"}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(ModificadorTicket.objects.filter(ticket=ticket, codigo="C/T").count(), 1)
        self.assertEqual(ModificadorTicket.objects.get(ticket=ticket, codigo="C/T").comensal, 24)
        imagen = render_comanda(ticket, "cocina")
        self.assertGreater(imagen.height, 650)
        self.assertLess(imagen.height, 850)
        trabajos = encolar_impresiones(ticket, TrabajoImpresion.Formato.COMANDA)
        self.assertEqual(len(trabajos), 1)
        self.assertEqual(trabajos[0].destino, TrabajoImpresion.Destino.COCINA)

    def test_promocion_cobra_una_vez_y_exige_sus_componentes(self):
        mesa = Mesa.objects.get(sucursal=self.sucursal, clave="MESA-10")
        promocion = Producto.objects.get(sucursal=self.sucursal, codigo="PK")
        bistec = Producto.objects.get(sucursal=self.sucursal, codigo="TBI")
        ticket, _ = abrir_ticket(mesa)
        with patch("ventas.services.timezone.localdate", return_value=date(2026, 8, 17)):
            partida_promocion = agregar_partida(ticket, promocion)
        componente = agregar_partida(
            ticket,
            bistec,
            comensal=4,
            cantidad=Decimal("3"),
            promocion_aplicada=partida_promocion,
        )
        ticket.refresh_from_db()
        self.assertEqual(componente.precio_unitario, Decimal("0.00"))
        self.assertEqual(ticket.total, Decimal("90.00"))
        procesar_ticket(ticket)

        incompleto, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="MESA-11"))
        with patch("ventas.services.timezone.localdate", return_value=date(2026, 8, 17)):
            agregar_partida(incompleto, promocion)
        with self.assertRaisesMessage(ErrorVenta, "Completa la promoción PK"):
            procesar_ticket(incompleto)

    def test_promocion_rechaza_componentes_ajenos(self):
        ticket, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="MESA-12"))
        promocion = Producto.objects.get(sucursal=self.sucursal, codigo="PK")
        consome = Producto.objects.get(sucursal=self.sucursal, codigo="CO8")
        with patch("ventas.services.timezone.localdate", return_value=date(2026, 8, 17)):
            partida_promocion = agregar_partida(ticket, promocion)
        with self.assertRaisesMessage(ErrorVenta, "no forma parte"):
            agregar_partida(ticket, consome, promocion_aplicada=partida_promocion)

    def test_comentarios_generales_e_individuales_son_excluyentes(self):
        ticket, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="MESA-13"))
        agregar_partida(ticket, self.producto, comensal=1)
        alternar_modificador(ticket, 1, "CEB", "CEBOLLA")
        alternar_comentario_general(ticket, "TODO_PLATO", "TODO POR PLATO")
        alternar_comentario_general(ticket, "MAS_CEB", "MÁS CEBOLLA")
        ticket.refresh_from_db()
        self.assertFalse(ticket.modificadores.exists())
        self.assertEqual([item["codigo"] for item in ticket.comentarios_generales], ["TODO_PLATO", "MAS_CEB"])

        alternar_modificador(ticket, 1, "CH V", "CHILE VERDE")
        alternar_modificador(ticket, 1, "CEB", "CEBOLLA")
        ticket.refresh_from_db()
        self.assertEqual(ticket.comentarios_generales, [])
        self.assertEqual(list(ticket.modificadores.values_list("codigo", flat=True)), ["CEB"])

    def test_orden_de_comanda_respeta_menu_y_deja_litros_y_consomes_al_final(self):
        ticket, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="MESA-14"))
        for codigo in ["CO1", "CO8", "BBQ05", "TB", "CO05", "LB"]:
            agregar_partida(ticket, Producto.objects.get(sucursal=self.sucursal, codigo=codigo))
        codigos = [partida.producto.codigo for partida in ordenar_partidas(ticket.partidas.select_related("producto"))]
        self.assertEqual(codigos, ["TB", "LB", "BBQ05", "CO8", "CO05", "CO1"])

    def test_entrega_programada_terminal_paga_con_y_salsas_se_persisten(self):
        mesa = Mesa.objects.get(sucursal=self.sucursal, clave="DOM-4")
        ticket, _ = abrir_ticket(mesa)
        agregar_partida(ticket, self.producto)
        respuesta = self.client.patch(
            f"/api/tickets/{ticket.id}/",
            data=json.dumps(
                {
                    "tipo_entrega": "programada",
                    "entrega_aproximada": "13:30",
                    "terminal": True,
                    "salsas_verduras": [
                        {"prefijo": "+ Más", "elementos": ["Verde", "Roja", "Pepino"]},
                        {"prefijo": "Nada más", "elementos": ["Chipotle", "Mexicana"]},
                    ],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        ticket.refresh_from_db()
        self.assertTrue(ticket.terminal)
        self.assertIn("Programado: 1:30 pm", _texto_entrega(ticket))
        self.assertEqual(_texto_salsas(ticket), "+ Más Verde, Roja, Pepino * Nada más Chipotle, Mexicana")

        respuesta = self.client.patch(
            f"/api/tickets/{ticket.id}/",
            data=json.dumps({"paga_con": 20}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 400)
        respuesta = self.client.patch(
            f"/api/tickets/{ticket.id}/",
            data=json.dumps({"paga_con": 100}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        ticket.refresh_from_db()
        self.assertFalse(ticket.terminal)
        self.assertEqual(ticket.paga_con, Decimal("100.00"))

    def test_render_termico_es_raster_escpos(self):
        mesa = Mesa.objects.get(sucursal=self.sucursal, clave="MESA-3")
        ticket, _ = abrir_ticket(mesa)
        agregar_partida(ticket, self.producto, comensal=1)
        ticket.refresh_from_db()
        comanda = render_comanda(ticket, "cocina")
        cuenta = render_cuenta(ticket)
        self.assertEqual(comanda.width, 576)
        self.assertEqual(cuenta.width, 576)
        self.assertGreater(comanda.height, 300)
        self.assertLess(comanda.height, 650)
        self.assertGreater(cuenta.height, 600)
        self.assertLess(cuenta.height, 800)
        contenido_cuenta = cuenta.convert("L").point(lambda valor: 255 - valor).getbbox()
        self.assertLessEqual(contenido_cuenta[1], 2)
        self.assertGreaterEqual(cuenta.height - contenido_cuenta[3], 50)
        datos = escpos_raster(comanda)
        self.assertTrue(datos.startswith(b"\x1b@\x1dv0\x00"))

    def test_tcp_marca_impreso_solo_despues_de_enviar(self):
        mesa = Mesa.objects.get(sucursal=self.sucursal, clave="MESA-4")
        ticket, _ = abrir_ticket(mesa)
        agregar_partida(ticket, self.producto, comensal=1)
        with override_settings(PRINT_BACKEND="tcp"), patch("impresion.services.enviar_tcp") as enviar:
            trabajo = encolar_impresiones(ticket, TrabajoImpresion.Formato.COMANDA)[0]
        enviar.assert_called_once()
        trabajo.refresh_from_db()
        self.assertEqual(trabajo.estado, TrabajoImpresion.Estado.IMPRESO)

    def test_error_tcp_conserva_png_y_no_reporta_impreso(self):
        mesa = Mesa.objects.get(sucursal=self.sucursal, clave="MESA-5")
        ticket, _ = abrir_ticket(mesa)
        agregar_partida(ticket, self.producto, comensal=1)
        with override_settings(PRINT_BACKEND="tcp"), patch(
            "impresion.services.enviar_tcp", side_effect=OSError("impresora no disponible")
        ):
            trabajo = encolar_impresiones(ticket, TrabajoImpresion.Formato.COMANDA)[0]
        trabajo.refresh_from_db()
        self.assertEqual(trabajo.estado, TrabajoImpresion.Estado.ERROR)
        self.assertIn("impresora no disponible", trabajo.error)
        self.assertTrue((Path(self.temporal.name) / trabajo.archivo).is_file())

    def test_cancelar_borra_la_orden_y_libera_la_posicion(self):
        mesa = Mesa.objects.get(sucursal=self.sucursal, clave="MESA-8")
        ticket, _ = abrir_ticket(mesa)
        agregar_partida(ticket, self.producto)
        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/cancelar/", data="{}", content_type="application/json"
        )
        self.assertEqual(respuesta.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, Ticket.Estado.CANCELADO)
        self.assertFalse(ticket.partidas.exists())
        self.assertNotIn(str(mesa.id), self.client.get("/api/estado/").json()["tickets"])
        nuevo, creado = abrir_ticket(mesa)
        self.assertTrue(creado)
        self.assertNotEqual(nuevo.id, ticket.id)

    def test_cobro_puede_omitir_ticket_de_cuenta(self):
        mesa = Mesa.objects.get(sucursal=self.sucursal, clave="MESA-9")
        ticket, _ = abrir_ticket(mesa)
        agregar_partida(ticket, self.producto)
        procesar_ticket(ticket)
        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/cobrar/",
            data=json.dumps({"forma_pago": "efectivo", "importe_recibido": "25", "imprimir_ticket": False}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["impresiones"], [])
        self.assertFalse(TrabajoImpresion.objects.filter(ticket=ticket, formato="cuenta").exists())
