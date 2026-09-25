import json
from io import StringIO
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from impresion.network import normalizar_host_impresora, resolver_ip_impresora
from impresion.models import (
    AsignacionImpresoraTerminal, ConfiguracionImpresionTerminal,
    Impresora, TrabajoImpresion,
)
from impresion.services import encolar_impresiones, reclamar_siguiente, resolver_ruta_impresion
from personas.models import Sucursal
from ventas.models import Mesa, Ticket
from soporte.models import ValidacionImpresionFisica


@override_settings(
    SUCURSAL_CLAVE="ARBOLEDAS",
    POS_REQUIRE_AUTH=True,
    PRINT_SYNC=False,
    PRINT_BACKEND="archivo",
    PRINTER_HOSTS={"caja": "192.168.1.9", "cocina": "192.168.1.9", "barra": "192.168.1.9"},
    PRINTER_PORT=9100,
)
class SoporteProductionTests(TestCase):
    def setUp(self):
        self.sucursal = Sucursal.objects.create(clave="ARBOLEDAS", nombre="Arboledas")
        User = get_user_model()
        self.tecnico = User.objects.create_user(
            username="tecnico_edge", password="TecnicoLocal!2468", is_staff=True
        )
        grupo = Group.objects.create(name="soporte_tecnico_edge")
        self.tecnico.groups.add(grupo)
        self.dueno = User.objects.create_superuser(
            username="dueno", password="DuenoLocal!2468"
        )
        self.client.force_login(self.tecnico)

    def enviar(self, ruta, datos, metodo="post", **extra):
        return getattr(self.client, metodo)(
            ruta, data=json.dumps(datos), content_type="application/json", **extra
        )

    def test_panel_exige_cuenta_tecnica_y_origen_lan(self):
        panel = self.client.get("/soporte/")
        self.assertEqual(panel.status_code, 200)
        self.assertContains(panel, "Soporte local")
        self.assertIn("no-store", panel.headers["Cache-Control"])
        self.assertEqual(
            self.client.get("/soporte/", REMOTE_ADDR="8.8.8.8").status_code, 403
        )
        self.assertEqual(
            self.client.get("/soporte/api/impresoras/", REMOTE_ADDR="8.8.8.8").status_code,
            403,
        )
        self.client.force_login(self.dueno)
        self.assertEqual(self.client.get("/soporte/").status_code, 403)
        self.assertEqual(self.client.get("/soporte/api/impresoras/").status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get("/soporte/api/impresoras/").status_code, 401)

    def test_cuatro_terminales_usan_dos_recursos_y_destinos_reconfigurables(self):
        a = self.enviar("/soporte/api/impresoras/", {
            "nombre": "IMPRESORA-A", "host": "192.168.1.20", "puerto": 9100,
            "descripcion": "Mesa uno",
        }).json()["impresora"]
        b = self.enviar("/soporte/api/impresoras/", {
            "nombre": "IMPRESORA-B", "host": "192.168.1.21", "puerto": 9200,
            "descripcion": "Mesa dos",
        }).json()["impresora"]
        for device_id, impresora in (
            ("pc-a", a), ("pc-b", b), ("tab-a", a), ("tab-b", b)
        ):
            creada = self.enviar("/soporte/api/terminales/", {
                "nombre": device_id.upper(), "device_id": device_id
            })
            self.assertEqual(creada.status_code, 201, creada.content)
            terminal_id = creada.json()["terminal"]["id"]
            ruta = self.enviar(
                "/soporte/api/terminales/" + terminal_id + "/rutas/",
                {"rutas": {"todos": impresora["id"]}},
                metodo="put",
            )
            self.assertEqual(ruta.status_code, 200, ruta.content)
            for destino in ("caja", "cocina", "barra"):
                snapshot = resolver_ruta_impresion(self.sucursal, device_id, destino)
                self.assertEqual(snapshot["printer_host"], impresora["host"])
                self.assertEqual(snapshot["printer_port"], impresora["puerto"])
                self.assertEqual(snapshot["origen_ruta"], "recurso")

        terminal_a = ConfiguracionImpresionTerminal.objects.get(
            sucursal=self.sucursal, device_id="pc-a"
        )
        ruta = self.enviar(
            "/soporte/api/terminales/" + str(terminal_a.pk) + "/rutas/",
            {"rutas": {"todos": a["id"], "barra": b["id"]}},
            metodo="put",
        )
        self.assertEqual(ruta.status_code, 200)
        self.assertEqual(
            resolver_ruta_impresion(self.sucursal, "pc-a", "barra")["printer_host"],
            b["host"],
        )
        self.assertEqual(
            resolver_ruta_impresion(self.sucursal, "pc-a", "caja")["printer_host"],
            a["host"],
        )

    def test_cambio_de_ip_conserva_trabajo_encolado_y_reintento_puede_refrescar(self):
        impresora = Impresora.objects.create(
            sucursal=self.sucursal, nombre="IMPRESORA-A", host="192.168.1.20"
        )
        terminal = ConfiguracionImpresionTerminal.objects.create(
            sucursal=self.sucursal, nombre="PC A", device_id="pc-a"
        )
        AsignacionImpresoraTerminal.objects.create(
            terminal=terminal, destino="todos", impresora=impresora
        )
        mesa = Mesa.objects.create(
            sucursal=self.sucursal, canal=Mesa.Canal.LLEVAR,
            clave="LLEVAR-1", nombre="Llevar uno",
        )
        ticket = Ticket.objects.create(
            sucursal=self.sucursal, mesa=mesa, folio=1,
            canal=Mesa.Canal.LLEVAR, estado=Ticket.Estado.COBRAR,
        )
        trabajo = encolar_impresiones(
            ticket, TrabajoImpresion.Formato.CUENTA, device_id="pc-a"
        )[0]
        self.assertEqual(trabajo.printer_host, "192.168.1.20")
        respuesta = self.enviar(
            "/soporte/api/impresoras/" + str(impresora.pk) + "/",
            {"host": "192.168.1.40"}, metodo="patch",
        )
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        trabajo.refresh_from_db()
        self.assertEqual(trabajo.printer_host, "192.168.1.20")
        trabajo.estado = TrabajoImpresion.Estado.ERROR
        trabajo.intentos = 1
        trabajo.save(update_fields=["estado", "intentos"])
        with override_settings(PRINT_BACKEND="tcp"):
            reintento = self.enviar(
                "/soporte/api/cola/" + str(trabajo.pk) + "/reintentar/",
                {"actualizar_ruta": True},
            )
        self.assertEqual(reintento.status_code, 200, reintento.content)
        trabajo.refresh_from_db()
        self.assertEqual(trabajo.estado, TrabajoImpresion.Estado.PENDIENTE)
        self.assertEqual(trabajo.printer_host, "192.168.1.40")

    def test_sondeo_no_envia_datos_y_reporta_impresora_inalcanzable(self):
        impresora = Impresora.objects.create(
            sucursal=self.sucursal, nombre="IMPRESORA-A", host="192.168.1.20"
        )
        ruta = "/soporte/api/impresoras/" + str(impresora.pk) + "/sondeo/"
        with patch("impresion.network.socket.create_connection") as conexion:
            respuesta = self.enviar(ruta, {})
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.json()["sondeo"]["alcanzable"])
        conexion.assert_called_once()
        self.assertFalse(conexion.return_value.__enter__.return_value.send.called)
        with patch(
            "impresion.network.socket.create_connection",
            side_effect=OSError("sin red"),
        ):
            respuesta = self.enviar(ruta, {})
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(respuesta.json()["sondeo"]["alcanzable"])
        self.assertNotIn("sin red", respuesta.content.decode("utf-8"))

    def test_reinicio_tcp_no_reimprime_automaticamente_trabajo_incierto(self):
        mesa = Mesa.objects.create(
            sucursal=self.sucursal, canal=Mesa.Canal.LLEVAR,
            clave="LLEVAR-2", nombre="Llevar dos",
        )
        ticket = Ticket.objects.create(
            sucursal=self.sucursal, mesa=mesa, folio=2,
            canal=Mesa.Canal.LLEVAR, estado=Ticket.Estado.COBRAR,
        )
        trabajo = TrabajoImpresion.objects.create(
            sucursal=self.sucursal, ticket=ticket,
            formato=TrabajoImpresion.Formato.CUENTA, destino="caja",
            estado=TrabajoImpresion.Estado.PROCESANDO,
            procesado_en=timezone.now() - timedelta(minutes=10),
        )
        with override_settings(PRINT_BACKEND="tcp"):
            self.assertIsNone(reclamar_siguiente())
        trabajo.refresh_from_db()
        self.assertEqual(trabajo.estado, TrabajoImpresion.Estado.ERROR)
        self.assertIn("Revisa el papel", trabajo.error)
        cola = self.client.get("/soporte/api/cola/").json()["trabajos"]
        self.assertEqual(cola[0]["estado"], "error")

    def test_no_acepta_ip_publica_o_ruta_de_otra_sucursal(self):
        publica = self.enviar("/soporte/api/impresoras/", {
            "nombre": "externa", "host": "8.8.8.8"
        })
        self.assertEqual(publica.status_code, 400)
        ajena = Sucursal.objects.create(clave="SANTA_ANITA", nombre="Santa Anita")
        impresora = Impresora.objects.create(
            sucursal=ajena, nombre="B", host="192.168.2.20"
        )
        terminal = ConfiguracionImpresionTerminal.objects.create(
            sucursal=self.sucursal, nombre="A", device_id="pc-a"
        )
        ruta = self.enviar(
            "/soporte/api/terminales/" + str(terminal.pk) + "/rutas/",
            {"rutas": {"todos": str(impresora.pk)}}, metodo="put",
        )
        self.assertEqual(ruta.status_code, 400)
        self.assertFalse(terminal.asignaciones_impresora.exists())


    def test_proxy_publico_o_sin_cliente_verificado_no_abre_soporte(self):
        with override_settings(WAITRESS_TRUSTED_PROXY="127.0.0.1"):
            sin_cliente = self.client.get(
                "/soporte/", REMOTE_ADDR="127.0.0.1",
            )
            self.assertEqual(sin_cliente.status_code, 403)
            publico = self.client.get(
                "/soporte/", REMOTE_ADDR="127.0.0.1",
                HTTP_X_FORWARDED_FOR="8.8.8.8",
            )
            self.assertEqual(publico.status_code, 403)
            discordante = self.client.get(
                "/soporte/", REMOTE_ADDR="127.0.0.1",
                HTTP_X_FORWARDED_FOR="192.168.1.20",
            )
            self.assertEqual(discordante.status_code, 403)
            local = self.client.get(
                "/soporte/", REMOTE_ADDR="192.168.1.20",
                HTTP_X_FORWARDED_FOR="192.168.1.20",
            )
            self.assertEqual(local.status_code, 200)

    def test_direccion_edge_anunciada_coincide_con_listener_configurado(self):
        with override_settings(
            ALLOWED_HOSTS=["testserver", "192.168.1.10"], HTTPS_ENABLED=False,
        ):
            guardada = self.enviar("/soporte/api/edge/", {
                "host": "192.168.1.10", "puerto": 8000, "usar_https": False,
            }, metodo="put")
            self.assertEqual(guardada.status_code, 200, guardada.content)
            self.assertEqual(
                guardada.json()["url_anunciada"], "http://192.168.1.10:8000/"
            )
            no_admitida = self.enviar("/soporte/api/edge/", {
                "host": "192.168.1.11", "puerto": 8000, "usar_https": False,
            }, metodo="put")
            self.assertEqual(no_admitida.status_code, 400)
            esquema_erroneo = self.enviar("/soporte/api/edge/", {
                "host": "192.168.1.10", "puerto": 8000, "usar_https": True,
            }, metodo="put")
            self.assertEqual(esquema_erroneo.status_code, 400)

    def test_crear_cuenta_tecnica_no_asigna_superusuario_ni_perfil_pos(self):
        with patch("sys.stdin", StringIO("ClaveTecnica!123456789\n")):
            call_command(
                "crear_cuenta_tecnica", username="soporte_nuevo",
                password_stdin=True, stdout=StringIO(),
            )
        usuario = get_user_model().objects.get(username="soporte_nuevo")
        self.assertTrue(usuario.is_staff)
        self.assertFalse(usuario.is_superuser)
        self.assertTrue(usuario.groups.filter(name="soporte_tecnico_edge").exists())
        self.assertTrue(usuario.check_password("ClaveTecnica!123456789"))

    def test_hostname_publico_o_loopback_no_puede_ser_recurso(self):
        with self.assertRaises(ValueError):
            normalizar_host_impresora("127.0.0.1")
        with patch("impresion.network.socket.getaddrinfo", return_value=[
            (2, 1, 0, "", ("8.8.8.8", 9100))
        ]):
            with self.assertRaises(OSError):
                resolver_ip_impresora("impresora.interna", 9100)


    def test_gate_fisico_exige_tcp_rutas_y_acta_auditada(self):
        resumen = self.client.get("/soporte/api/resumen/")
        self.assertEqual(resumen.status_code, 200, resumen.content)
        validacion = resumen.json()["validacion_impresion"]
        self.assertFalse(validacion["lista"])
        self.assertEqual(validacion["codigo"], "backend_archivo")
        self.assertContains(self.client.get("/soporte/"), "Pendiente de validar")
        confirmacion = {
            "confirmada": True,
            "nota": "Comanda y cuenta verificadas en papel",
        }
        bloqueada = self.enviar(
            "/soporte/api/impresion/confirmar-prueba-fisica/", confirmacion
        )
        self.assertEqual(bloqueada.status_code, 409)
        self.assertFalse(ValidacionImpresionFisica.objects.exists())

        with override_settings(PRINT_BACKEND="tcp"):
            impresora = Impresora.objects.create(
                sucursal=self.sucursal, nombre="IMPRESORA-A", host="192.168.1.20"
            )
            terminal = ConfiguracionImpresionTerminal.objects.create(
                sucursal=self.sucursal, nombre="PC A", device_id="pc-a"
            )
            sin_ruta = self.client.get("/soporte/api/resumen/").json()
            self.assertEqual(
                sin_ruta["validacion_impresion"]["codigo"], "rutas_incompletas"
            )
            self.assertEqual(
                self.enviar(
                    "/soporte/api/impresion/confirmar-prueba-fisica/", confirmacion
                ).status_code,
                409,
            )
            AsignacionImpresoraTerminal.objects.create(
                terminal=terminal, destino="todos", impresora=impresora
            )
            pendiente = self.client.get("/soporte/api/resumen/").json()
            self.assertEqual(
                pendiente["validacion_impresion"]["codigo"], "prueba_pendiente"
            )
            registrada = self.enviar(
                "/soporte/api/impresion/confirmar-prueba-fisica/", confirmacion
            )
            self.assertEqual(registrada.status_code, 201, registrada.content)
            self.assertTrue(registrada.json()["validacion_impresion"]["lista"])
            acta = ValidacionImpresionFisica.objects.get()
            self.assertEqual(acta.confirmado_por, self.tecnico)
            self.assertEqual(acta.nota, confirmacion["nota"])
            self.assertTrue(acta.confirmado_en)
            self.assertEqual(len(acta.huella_topologia), 64)

            impresora.host = "192.168.1.40"
            impresora.save(update_fields=["host"])
            cambiada = self.client.get("/soporte/api/resumen/").json()
            self.assertEqual(
                cambiada["validacion_impresion"]["codigo"], "topologia_cambiada"
            )
            self.assertFalse(cambiada["validacion_impresion"]["lista"])
            self.assertEqual(ValidacionImpresionFisica.objects.count(), 1)
