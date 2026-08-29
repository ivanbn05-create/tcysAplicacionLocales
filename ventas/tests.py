import json
import sqlite3
import tempfile
import uuid
from io import BytesIO
from collections import OrderedDict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from PIL import Image

from catalogo.models import Producto
from impresion.models import TrabajoImpresion
from catalogo.configuracion_menu import configuracion_producto
from impresion.render import (
    _agrupar_partidas_total,
    _datos_comanda_por_nombres,
    _identificador_ticket,
    _segmentos_bebidas,
    _texto_entrega,
    _texto_salsas,
    escpos_raster,
    render_comanda,
    render_cuenta,
    render_domicilio,
    render_sucursal,
)
from impresion.services import encolar_impresiones
from personas.models import Rol, Sucursal, UsuarioPOS

from .integracion_sucursales import _parametros_conexion_postgres, sincronizar_pedidos_confirmados
from .admin_services import activar_programados
from .models import (
    Cliente,
    EventoOutbox,
    Mesa,
    ModificadorTicket,
    Partida,
    PedidoSucursalImportado,
    PrecioProductoSucursal,
    ProductoSucursal,
    SucursalPedido,
    Ticket,
    ReporteAdministrativo,
    LiquidacionRepartidor,
)
from .orden import ordenar_partidas
from .services import (
    ErrorVenta,
    abrir_ticket,
    agregar_partida,
    agregar_partida_sucursal,
    alternar_comentario_general,
    alternar_modificador,
    actualizar_partida,
    cobrar_ticket,
    procesar_ticket,
)
from .views import ASSET_VERSION


SECURITY_MIDDLEWARE = list(settings.MIDDLEWARE)
if "ventas.middleware.POSSessionAuthenticationMiddleware" not in SECURITY_MIDDLEWARE:
    SECURITY_MIDDLEWARE.append("ventas.middleware.POSSessionAuthenticationMiddleware")


class FlujoPOSTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("cargar_datos_iniciales", verbosity=0)

    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory()
        self.ajustes = override_settings(
            MEDIA_ROOT=self.temporal.name,
            PRINT_SYNC=True,
            PRINT_BACKEND="archivo",
            PEDIDOS_SUCURSALES_AUTO_SYNC=False,
        )
        self.ajustes.enable()
        self.sucursal = Sucursal.objects.get(clave="ARBOLEDAS")
        self.producto = Producto.objects.get(sucursal=self.sucursal, codigo="TB")

    def tearDown(self):
        self.ajustes.disable()
        self.temporal.cleanup()

    def _autorizar_administrador(self, clave="1212"):
        return self.client.post(
            "/api/administrador/acceso/",
            data=json.dumps({"clave_administrador": clave}),
            content_type="application/json",
        )

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

    def _asignar_cliente_domicilio(self, ticket, nombre="Cliente domicilio", exterior="70"):
        cliente = self.client.post(
            "/api/clientes/",
            data=json.dumps(self.datos_cliente(nombre=nombre, exterior=exterior)),
            content_type="application/json",
        ).json()["cliente"]
        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/cliente/",
            data=json.dumps(
                {
                    "cliente_id": cliente["id"],
                    "telefono_id": cliente["telefonos"][0]["id"],
                    "domicilio_id": cliente["domicilios"][0]["id"],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        ticket.refresh_from_db()
        return ticket

    def test_catalogo_confirmado_del_menu(self):
        self.assertEqual(Producto.objects.filter(sucursal=self.sucursal, activo=True).count(), 47)
        self.assertEqual(self.producto.precio_actual().importe, Decimal("25.00"))
        self.assertEqual(Mesa.objects.filter(sucursal=self.sucursal, canal="comedor").count(), 24)
        self.assertEqual(Mesa.objects.filter(sucursal=self.sucursal, canal="recoger").count(), 12)
        self.assertEqual(Mesa.objects.filter(sucursal=self.sucursal, canal="llevar").count(), 12)
        self.assertEqual(Mesa.objects.filter(sucursal=self.sucursal, canal="domicilio").count(), 100)
        self.assertEqual(SucursalPedido.objects.filter(sucursal=self.sucursal, activa=True).count(), 10)
        self.assertEqual(Mesa.objects.filter(sucursal=self.sucursal, canal="sucursales", activa=True).count(), 240)
        self.assertTrue(
            Mesa.objects.filter(
                sucursal=self.sucursal,
                canal="sucursales",
                cliente_sucursal__nombre="Estancia",
                orden=1,
                nombre="Estancia 1",
            ).exists()
        )
        self.assertEqual(ProductoSucursal.objects.filter(sucursal=self.sucursal, activo=True).count(), 38)
        self.assertEqual(PrecioProductoSucursal.objects.filter(sucursal=self.sucursal).count(), 372)

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

    def test_pedido_sucursal_captura_decimal_imprime_y_completa(self):
        posicion = Mesa.objects.get(
            sucursal=self.sucursal,
            canal=Mesa.Canal.SUCURSALES,
            cliente_sucursal__origen_id=3,
            orden=1,
        )
        ticket, _ = abrir_ticket(posicion)
        payload = self.client.get(f"/api/tickets/{ticket.id}/").json()
        self.assertEqual(payload["ticket"]["cliente_sucursal"]["nombre"], "Estancia")
        self.assertEqual(len(payload["ticket"]["catalogo_sucursal"]), 38)

        barbacoa = ProductoSucursal.objects.get(sucursal=self.sucursal, origen_id=1)
        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/partidas/",
            data=json.dumps({"producto_sucursal_id": str(barbacoa.id), "cantidad": "1"}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        partida = respuesta.json()["ticket"]["partidas"][0]
        respuesta = self.client.patch(
            f"/api/partidas/{partida['id']}/",
            data=json.dumps({"cantidad": "36.000"}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(Decimal(respuesta.json()["ticket"]["total"]), Decimal("6948.00"))

        chile = ProductoSucursal.objects.get(sucursal=self.sucursal, origen_id=7)
        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/partidas/",
            data=json.dumps({"producto_sucursal_id": str(chile.id), "cantidad": "30.000"}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(Decimal(respuesta.json()["ticket"]["total"]), Decimal("7012.00"))

        respuesta = self.client.post(f"/api/tickets/{ticket.id}/procesar/", data="{}", content_type="application/json")
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["impresiones"][0]["formato"], TrabajoImpresion.Formato.SUCURSAL)
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, Ticket.Estado.PROCESADO)
        imagen = render_sucursal(ticket)
        self.assertEqual(imagen.width, 576)
        self.assertGreater(imagen.height, 600)
        self.assertLess(imagen.height, 750)

        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/completar-sucursal/",
            data=json.dumps({"clave_administrador": "1212"}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, Ticket.Estado.PAGADO)

    def test_cliente_mayorista_usa_precio_y_catalogo_especificos(self):
        posicion = Mesa.objects.get(
            sucursal=self.sucursal,
            canal=Mesa.Canal.SUCURSALES,
            cliente_sucursal__origen_id=8,
            orden=1,
        )
        ticket, _ = abrir_ticket(posicion)
        respuesta = self.client.get(f"/api/tickets/{ticket.id}/")
        catalogo = respuesta.json()["ticket"]["catalogo_sucursal"]
        self.assertEqual(len(catalogo), 36)
        barbacoa = next(producto for producto in catalogo if producto["origen_id"] == 1)
        self.assertEqual(Decimal(barbacoa["precio"]), Decimal("203.00"))
        self.assertEqual(barbacoa["nombre_ticket"], "BARBACOA .M")
        self.assertNotIn(25, {producto["origen_id"] for producto in catalogo})
        self.assertNotIn(26, {producto["origen_id"] for producto in catalogo})

    def test_integracion_local_importa_todos_los_confirmados_del_dia_una_sola_vez(self):
        ruta = Path(self.temporal.name) / "pedidos-externos.sqlite3"
        conexion = sqlite3.connect(ruta)
        conexion.executescript(
            """
            CREATE TABLE pedidos_pedido (
                id INTEGER PRIMARY KEY,
                sucursal_cliente_id INTEGER NOT NULL,
                codigo_publico TEXT NOT NULL,
                estado TEXT NOT NULL,
                fecha_confirmacion TEXT,
                eliminado INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE pedidos_itempedido (
                id INTEGER PRIMARY KEY,
                pedido_id INTEGER NOT NULL,
                producto_id INTEGER NOT NULL,
                cantidad TEXT NOT NULL,
                precio_unitario TEXT NOT NULL
            );
            """
        )
        conexion.execute(
            "INSERT INTO pedidos_pedido VALUES (?, ?, ?, ?, ?, ?)",
            (901, 3, "codigo-prueba", "confirmado", timezone.now().isoformat(), 0),
        )
        conexion.execute(
            "INSERT INTO pedidos_itempedido VALUES (?, ?, ?, ?, ?)",
            (1, 901, 1, "2.000", "193.00"),
        )
        conexion.execute(
            "INSERT INTO pedidos_pedido VALUES (?, ?, ?, ?, ?, ?)",
            (1901, 3, "codigo-prueba-dos", "confirmado", timezone.now().isoformat(), 0),
        )
        conexion.execute(
            "INSERT INTO pedidos_itempedido VALUES (?, ?, ?, ?, ?)",
            (2, 1901, 1, "1.000", "193.00"),
        )
        conexion.commit()
        conexion.close()

        with override_settings(
            DEBUG=True,
            PEDIDOS_SUCURSALES_AUTO_SYNC=True,
            PEDIDOS_SUCURSALES_FUENTE="sqlite",
            PEDIDOS_SUCURSALES_DATABASE_URL="",
            PEDIDOS_SUCURSALES_POSTGRES={"password": ""},
            PEDIDOS_SUCURSALES_DB=ruta,
        ):
            primero = sincronizar_pedidos_confirmados(self.sucursal, forzar=True)
            segundo = sincronizar_pedidos_confirmados(self.sucursal, forzar=True)
        self.assertEqual(primero["importados"], 2)
        self.assertEqual(segundo["importados"], 0)
        importacion = PedidoSucursalImportado.objects.select_related("ticket").get(origen_id=901)
        self.assertEqual(importacion.ticket.estado, Ticket.Estado.ABIERTO)
        self.assertEqual(importacion.ticket.mesa.cliente_sucursal.origen_id, 3)
        self.assertEqual(importacion.ticket.total, Decimal("386.00"))
        importaciones = PedidoSucursalImportado.objects.filter(origen_id__in=[901, 1901]).order_by(
            "ticket__mesa__orden"
        )
        self.assertEqual(list(importaciones.values_list("ticket__mesa__orden", flat=True)), [1, 2])

    def test_integracion_supabase_tiene_prioridad_y_es_idempotente(self):
        pedidos = [
            (
                {
                    "id": 902,
                    "sucursal_cliente_id": 4,
                    "sucursal_nombre": "Eventos MO",
                    "codigo_publico": "eventos-mo-prueba",
                    "estado": "confirmado",
                    "fecha_confirmacion": timezone.now(),
                },
                [
                    {
                        "producto_id": 1,
                        "producto_nombre": "LITRO DE BARBACOA",
                        "producto_nombre_ticket": "BARBACOA",
                        "cantidad": Decimal("2.000"),
                        "precio_unitario": Decimal("193.00"),
                    }
                ],
            )
        ]
        postgres = {"password": "configurada", "connect_timeout": 8}
        with (
            override_settings(
                PEDIDOS_SUCURSALES_AUTO_SYNC=True,
                PEDIDOS_SUCURSALES_FUENTE="supabase",
                PEDIDOS_SUCURSALES_DATABASE_URL="",
                PEDIDOS_SUCURSALES_POSTGRES=postgres,
                PEDIDOS_SUCURSALES_DB=Path(self.temporal.name) / "no-existe.sqlite3",
            ),
            patch("ventas.integracion_sucursales._leer_confirmados_postgres", return_value=pedidos) as lector,
        ):
            primero = sincronizar_pedidos_confirmados(self.sucursal, forzar=True)
            segundo = sincronizar_pedidos_confirmados(self.sucursal, forzar=True)

        self.assertEqual(primero["importados"], 1)
        self.assertEqual(primero["fuente"], "Supabase")
        self.assertEqual(segundo["importados"], 0)
        self.assertEqual(lector.call_count, 2)
        importacion = PedidoSucursalImportado.objects.select_related("ticket__mesa__cliente_sucursal").get(
            origen_id=902
        )
        self.assertEqual(importacion.origen, "pedidos_sucursales_supabase")
        self.assertEqual(importacion.ticket.mesa.cliente_sucursal.nombre, "Eventos MO")
        self.assertEqual(importacion.ticket.total, Decimal("386.00"))
        self.assertEqual(importacion.ticket.partidas.get().producto_sucursal.origen_id, 1)

    def test_integracion_rechaza_datos_manipulados_y_continua_con_pedidos_validos(self):
        def pedido_remoto(origen_id, cantidad, precio):
            return (
                {
                    "id": origen_id,
                    "sucursal_cliente_id": 4,
                    "sucursal_nombre": "Eventos MO",
                    "codigo_publico": f"integridad-{origen_id}",
                    "estado": "confirmado",
                    "fecha_confirmacion": timezone.now(),
                },
                [
                    {
                        "producto_id": 1,
                        "producto_nombre": "LITRO DE BARBACOA",
                        "producto_nombre_ticket": "BARBACOA",
                        "cantidad": cantidad,
                        "precio_unitario": precio,
                    }
                ],
            )

        pedidos = [
            pedido_remoto(910, "NaN", "193.00"),
            pedido_remoto(911, "-0.001", "193.00"),
            pedido_remoto(912, "1000.000", "193.00"),
            pedido_remoto(913, "1.000", "194.00"),
            pedido_remoto(914, "1.000", "NaN"),
            pedido_remoto(915, "2.000", "193.00"),
        ]
        with (
            override_settings(
                PEDIDOS_SUCURSALES_AUTO_SYNC=True,
                PEDIDOS_SUCURSALES_FUENTE="supabase",
                PEDIDOS_SUCURSALES_DATABASE_URL="",
                PEDIDOS_SUCURSALES_POSTGRES={"password": "configurada"},
                PEDIDOS_SUCURSALES_DB=Path(self.temporal.name) / "no-existe.sqlite3",
            ),
            patch("ventas.integracion_sucursales._leer_confirmados_postgres", return_value=pedidos),
        ):
            resultado = sincronizar_pedidos_confirmados(self.sucursal, forzar=True)

        self.assertTrue(resultado["activa"])
        self.assertEqual(resultado["importados"], 1)
        self.assertEqual(resultado["rechazados"], 5)
        self.assertFalse(PedidoSucursalImportado.objects.filter(origen_id__in=range(910, 915)).exists())
        importacion = PedidoSucursalImportado.objects.get(origen_id=915)
        partida = importacion.ticket.partidas.get()
        self.assertEqual(partida.cantidad, Decimal("2.000"))
        self.assertEqual(partida.precio_unitario, Decimal("193.00"))

    def test_integracion_supabase_bloquea_rol_privilegiado_y_tls_debil(self):
        with override_settings(
            DEBUG=False,
            PEDIDOS_SUCURSALES_DATABASE_URL="",
            PEDIDOS_SUCURSALES_POSTGRES={
                "host": "db.proyecto.supabase.co",
                "port": 5432,
                "dbname": "postgres",
                "user": "postgres.proyecto",
                "password": "secreto",
                "sslmode": "verify-full",
            },
        ):
            with self.assertRaisesMessage(ValueError, "pos_local_reader"):
                _parametros_conexion_postgres()

        with override_settings(
            DEBUG=False,
            PEDIDOS_SUCURSALES_DATABASE_URL="",
            PEDIDOS_SUCURSALES_POSTGRES={
                "host": "db.proyecto.supabase.co",
                "port": 5432,
                "dbname": "postgres",
                "user": "pos_local_reader.proyecto",
                "password": "secreto",
                "sslmode": "require",
            },
        ):
            with self.assertRaisesMessage(ValueError, "sslmode=verify-full"):
                _parametros_conexion_postgres()

        with override_settings(
            DEBUG=False,
            PEDIDOS_SUCURSALES_DATABASE_URL=(
                "postgresql://postgres.proyecto:secreto@db.proyecto.supabase.co:5432/postgres"
                "?sslmode=verify-full&sslrootcert=C%3A%5Ccerts%5Csupabase-ca.crt"
            ),
            PEDIDOS_SUCURSALES_POSTGRES={},
        ):
            with self.assertRaisesMessage(ValueError, "pos_local_reader"):
                _parametros_conexion_postgres()

        with override_settings(
            DEBUG=False,
            PEDIDOS_SUCURSALES_DATABASE_URL="",
            PEDIDOS_SUCURSALES_POSTGRES={
                "host": "db.proyecto.supabase.co",
                "port": 5432,
                "dbname": "postgres",
                "user": "pos_local_reader.proyecto",
                "password": "secreto",
                "sslmode": "verify-full",
            },
        ):
            with self.assertRaisesMessage(ValueError, "sslrootcert"):
                _parametros_conexion_postgres()

        with override_settings(
            DEBUG=False,
            PEDIDOS_SUCURSALES_DATABASE_URL="",
            PEDIDOS_SUCURSALES_POSTGRES={
                "host": "base-no-autorizada.example",
                "port": 5432,
                "dbname": "postgres",
                "user": "pos_local_reader.proyecto",
                "password": "secreto",
                "sslmode": "verify-full",
                "sslrootcert": "C:\\certs\\supabase-ca.crt",
            },
        ):
            with self.assertRaisesMessage(ValueError, "no pertenece a Supabase"):
                _parametros_conexion_postgres()

    def test_integracion_supabase_acepta_solo_configuracion_dedicada_con_ca_existente(self):
        ca = Path(self.temporal.name) / "supabase-ca.crt"
        ca.write_text("certificado de prueba", encoding="utf-8")
        with override_settings(
            PEDIDOS_SUCURSALES_DATABASE_URL="",
            PEDIDOS_SUCURSALES_POSTGRES={
                "host": "db.proyecto.supabase.co",
                "port": 5432,
                "dbname": "postgres",
                "user": "pos_local_reader.proyecto",
                "password": "secreto",
                "sslmode": "verify-full",
                "sslrootcert": str(ca),
                "connect_timeout": 8,
            },
        ):
            parametros = _parametros_conexion_postgres()

        self.assertEqual(parametros["user"], "pos_local_reader.proyecto")
        self.assertEqual(parametros["sslmode"], "verify-full")
        self.assertEqual(Path(parametros["sslrootcert"]), ca.resolve())

    def test_integracion_automatica_pausa_fuera_del_horario(self):
        fuera_de_horario = timezone.make_aware(datetime.combine(timezone.localdate(), time(18, 0)))
        with (
            override_settings(
                PEDIDOS_SUCURSALES_AUTO_SYNC=True,
                PEDIDOS_SUCURSALES_FUENTE="supabase",
                PEDIDOS_SUCURSALES_DATABASE_URL="",
                PEDIDOS_SUCURSALES_POSTGRES={"password": "configurada"},
                PEDIDOS_SUCURSALES_HORA_INICIO="06:00",
                PEDIDOS_SUCURSALES_HORA_FIN="17:35",
            ),
            patch("ventas.integracion_sucursales.timezone.localtime", return_value=fuera_de_horario),
            patch("ventas.integracion_sucursales._leer_confirmados_postgres") as lector,
        ):
            resultado = sincronizar_pedidos_confirmados(self.sucursal)

        self.assertTrue(resultado["activa"])
        self.assertIn("fuera del horario", resultado["mensaje"])
        lector.assert_not_called()

    def test_sincronizacion_supabase_usa_post_dedicado(self):
        resultado = {"activa": True, "importados": 0, "fuente": "Supabase", "mensaje": "Al día"}
        with patch("ventas.views.sincronizar_pedidos_confirmados", return_value=resultado) as sincronizar:
            normal = self.client.get("/api/estado/")
            query_obsoleto = self.client.get("/api/estado/?sincronizar_sucursales=1")
            solicitado = self.client.post(
                "/api/sincronizacion/sucursales/",
                data="{}",
                content_type="application/json",
            )

        self.assertEqual(normal.status_code, 200)
        self.assertEqual(query_obsoleto.status_code, 200)
        self.assertEqual(solicitado.status_code, 200)
        self.assertEqual(solicitado.json()["integracion_sucursales"]["fuente"], "Supabase")
        sincronizar.assert_called_once_with(self.sucursal)

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
            respuesta = self.client.post(
                "/api/clientes/buscar/",
                data=json.dumps({"q": consulta}),
                content_type="application/json",
            )
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

        siguiente, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="DOM-4"))
        Ticket.objects.filter(pk=siguiente.pk).update(
            contacto_pedido_nombre="CONTACTO ANTERIOR",
            contacto_pedido_telefono="3300000000",
        )
        respuesta = self.client.post(
            f"/api/tickets/{siguiente.id}/cliente/",
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
        self.assertEqual(respuesta.json()["ticket"]["cliente"]["contacto_pedido_nombre"], "")
        self.assertEqual(respuesta.json()["ticket"]["cliente"]["contacto_pedido_telefono"], "")

    def test_tabletas_solo_presenta_el_acceso_de_comedor(self):
        respuesta = self.client.get("/tabletas/")
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'data-modo-tableta="true"')
        self.assertNotContains(respuesta, 'class="tableta-identidad"')
        self.assertContains(respuesta, 'id="pantalla-completa"')
        self.assertContains(respuesta, 'id="salir-tableta"')
        self.assertContains(respuesta, "manifest.webmanifest?modo=tableta")
        self.assertContains(respuesta, 'class="topbar"')
        self.assertContains(respuesta, 'id="atajos-menu"')
        self.assertNotContains(respuesta, 'class="barra-estado-posiciones"')
        self.assertNotContains(respuesta, 'data-canal="domicilio"')
        self.assertNotContains(respuesta, 'data-canal="sucursales"')
        manifest = self.client.get("/manifest.webmanifest?modo=tableta").json()
        self.assertEqual(manifest["id"], "/tabletas/")
        self.assertEqual(manifest["start_url"], "/tabletas/")
        self.assertEqual(manifest["display_override"], ["fullscreen", "standalone"])

    def test_pc_inicia_en_selector_de_perfil_y_admin_esta_habilitado(self):
        respuesta = self.client.get("/")
        self.assertContains(respuesta, 'id="pantalla-acceso"')
        self.assertContains(respuesta, 'id="entrar-mesero"')
        self.assertContains(respuesta, 'id="entrar-administrador" class="perfil-acceso administrador"')
        self.assertNotContains(respuesta, 'class="perfil-acceso administrador" type="button" disabled')
        self.assertNotContains(respuesta, "¿Cómo deseas entrar?")
        self.assertContains(respuesta, 'id="pantalla-completa"')
        self.assertContains(respuesta, 'id="comentario"')
        self.assertContains(respuesta, f"app.css?v={ASSET_VERSION}")
        self.assertContains(respuesta, f"brand-pos.css?v={ASSET_VERSION}")
        self.assertContains(respuesta, f"app.js?v={ASSET_VERSION}")
        self.assertContains(respuesta, "102e4b50")
        self.assertContains(respuesta, 'data-canal="comedor" type="button" aria-pressed="true"')
        self.assertContains(respuesta, 'aria-labelledby="titulo-dialogo-cobro"')
        self.assertContains(respuesta, 'id="switch-tipo-pedido"')
        self.assertContains(respuesta, 'id="switch-modo-nombres"')
        self.assertContains(respuesta, 'id="datos-servicio-directo"')
        self.assertContains(respuesta, 'id="atajos-menu"')
        self.assertNotContains(respuesta, 'class="barra-estado-posiciones"')
        self.assertEqual(respuesta.content.decode().count('class="perfil-icono"'), 2)
        self.assertEqual(respuesta.content.decode().count('data-salir-mesero'), 1)
        worker = self.client.get("/service-worker.js")
        self.assertEqual(worker.headers["Cache-Control"], "no-cache")
        self.assertContains(worker, f"tocayos-pos-{ASSET_VERSION}")
        self.assertContains(worker, "Montserrat-Variable.woff2")
        self.assertContains(worker, "BebasNeue-Regular.woff2")

    @patch("ventas.views.timezone.localdate", return_value=date(2026, 8, 30))
    def test_catalogo_inicial_ordena_siete_secciones_y_expone_promociones_no_disponibles(self, _fecha):
        respuesta = self.client.get("/")
        html = respuesta.content.decode()
        marcador = '<script id="datos-productos" type="application/json">'
        catalogo = json.loads(html.split(marcador, 1)[1].split("</script>", 1)[0])

        categorias = list(dict.fromkeys(producto["categoria"] for producto in catalogo))
        self.assertEqual(
            categorias,
            [
                "Taco",
                "Promoción",
                "Consomé y Barbacoa",
                "Lonches",
                "Gringas y Quesadillas",
                "Bebidas",
                "Postre",
            ],
        )
        promociones = [producto for producto in catalogo if producto["es_promocion"]]
        self.assertTrue(promociones)
        self.assertTrue(all(not producto["disponible_hoy"] for producto in promociones))

    def test_javascript_conserva_contratos_de_atajos_y_promociones_inactivas(self):
        javascript = (Path(settings.BASE_DIR) / "ventas" / "static" / "ventas" / "app.js").read_text(
            encoding="utf-8"
        )
        for contrato in (
            'data-menu-atajo="${numero}"',
            'aria-controls="menu-seccion-${numero}"',
            'id="${idSeccion}" data-menu-seccion="${numeroSeccion}"',
            'no-disponible-hoy',
            'b.disabled = !abierto || b.classList.contains("no-disponible-hoy")',
            '(prefers-reduced-motion: reduce)',
        ):
            with self.subTest(contrato=contrato):
                self.assertIn(contrato, javascript)

    def test_imagen_producto_se_publica_como_miniatura_webp_privada(self):
        contenido = BytesIO()
        Image.new("RGB", (640, 420), (228, 37, 34)).save(contenido, format="PNG")
        self.producto.imagen = SimpleUploadedFile(
            "taco-real.png",
            contenido.getvalue(),
            content_type="image/png",
        )
        self.producto.full_clean()
        self.producto.save(update_fields=["imagen", "actualizado_en"])

        pagina = self.client.get("/")
        ruta = f"/catalogo/productos/{self.producto.id}/imagen.webp"
        self.assertContains(pagina, ruta)
        respuesta = self.client.get(ruta)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.headers["Content-Type"], "image/webp")
        self.assertEqual(respuesta.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("private", respuesta.headers["Cache-Control"])
        self.assertIn("immutable", respuesta.headers["Cache-Control"])
        self.assertTrue(respuesta.headers["ETag"])
        self.assertEqual(Image.open(BytesIO(respuesta.content)).size, (320, 240))

        no_modificada = self.client.get(ruta, HTTP_IF_NONE_MATCH=respuesta.headers["ETag"])
        self.assertEqual(no_modificada.status_code, 304)

    def test_imagen_producto_faltante_o_corrupta_no_expone_archivo(self):
        sin_imagen = self.client.get(f"/catalogo/productos/{self.producto.id}/imagen.webp")
        self.assertEqual(sin_imagen.status_code, 404)

        self.producto.imagen = SimpleUploadedFile(
            "archivo-corrupto.png",
            b"esto no es una imagen",
            content_type="image/png",
        )
        self.producto.save(update_fields=["imagen", "actualizado_en"])
        corrupta = self.client.get(f"/catalogo/productos/{self.producto.id}/imagen.webp")
        self.assertEqual(corrupta.status_code, 404)

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
        self.assertGreater(imagen.height, 1000)
        self.assertLess(imagen.height, 1150)
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

    def test_comanda_impresa_omite_fila_promocion_y_conserva_componentes(self):
        promocion = Producto.objects.get(sucursal=self.sucursal, codigo="PK")
        bistec = Producto.objects.get(sucursal=self.sucursal, codigo="TBI")
        ticket_promocion, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="MESA-15"))
        with patch("ventas.services.timezone.localdate", return_value=date(2026, 8, 17)):
            partida_promocion = agregar_partida(ticket_promocion, promocion)
        agregar_partida(
            ticket_promocion,
            bistec,
            comensal=4,
            cantidad=Decimal("3"),
            promocion_aplicada=partida_promocion,
        )

        ticket_sin_promocion, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="MESA-16"))
        agregar_partida(ticket_sin_promocion, bistec, comensal=4, cantidad=Decimal("3"))

        # Ambas comandas deben ocupar exactamente una fila de producto (TBI).
        # Si se reintroduce la fila PK en impresión, la primera será 48 px más alta.
        self.assertEqual(
            render_comanda(ticket_promocion, "cocina").height,
            render_comanda(ticket_sin_promocion, "cocina").height,
        )

    def test_promocion_rechaza_componentes_ajenos(self):
        ticket, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="MESA-12"))
        promocion = Producto.objects.get(sucursal=self.sucursal, codigo="PK")
        consome = Producto.objects.get(sucursal=self.sucursal, codigo="CO8")
        with patch("ventas.services.timezone.localdate", return_value=date(2026, 8, 17)):
            partida_promocion = agregar_partida(ticket, promocion)
        with self.assertRaisesMessage(ErrorVenta, "no forma parte"):
            agregar_partida(ticket, consome, promocion_aplicada=partida_promocion)

    def test_promocion_asigna_sin_modo_especial_y_al_eliminar_conserva_productos(self):
        ticket, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="MESA-17"))
        promocion = Producto.objects.get(sucursal=self.sucursal, codigo="PK")
        bistec = Producto.objects.get(sucursal=self.sucursal, codigo="TBI")
        with patch("ventas.services.timezone.localdate", return_value=date(2026, 8, 17)):
            partida_promocion = agregar_partida(ticket, promocion)

        componente = agregar_partida(ticket, bistec, comensal=3, cantidad=Decimal("3"))
        componente.refresh_from_db()
        ticket.refresh_from_db()
        self.assertEqual(componente.promocion_aplicada_id, partida_promocion.id)
        self.assertEqual(componente.precio_unitario, Decimal("0.00"))
        self.assertEqual(ticket.total, Decimal("90.00"))

        actualizar_partida(partida_promocion, Decimal("0"))
        componente = ticket.partidas.get(producto=bistec)
        ticket.refresh_from_db()
        self.assertIsNone(componente.promocion_aplicada_id)
        self.assertEqual(componente.cantidad, Decimal("3"))
        self.assertEqual(componente.precio_unitario, bistec.precio_actual().importe)

    def test_ticket_total_agrupa_producto_termino_cantidad_e_importe(self):
        ticket, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="MESA-18"))
        primera = agregar_partida(ticket, self.producto, cantidad=Decimal("4"), termino="dorado")
        Partida.objects.create(
            sucursal=ticket.sucursal,
            ticket=ticket,
            producto=primera.producto,
            comensal=2,
            cantidad=Decimal("3"),
            precio_unitario=Decimal("20.00"),
            nombre_producto=primera.nombre_producto,
            nombre_corto=primera.nombre_corto,
            termino=primera.termino,
        )
        conceptos = _agrupar_partidas_total(ticket)
        self.assertEqual(len(conceptos), 1)
        self.assertEqual(conceptos[0]["cantidad"], Decimal("7"))
        self.assertEqual(conceptos[0]["importe"], Decimal("160.00"))
        self.assertEqual(conceptos[0]["precio_unitario"].quantize(Decimal("0.01")), Decimal("22.86"))

    def test_comanda_reserva_franja_de_preparacion_aunque_este_vacia(self):
        sin_comentario, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="MESA-21"))
        con_comentario, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="MESA-22"))
        agregar_partida(sin_comentario, self.producto)
        agregar_partida(con_comentario, self.producto)
        alternar_modificador(con_comentario, 1, "C/T", "CON TODO")
        self.assertEqual(
            render_comanda(sin_comentario, "cocina").height,
            render_comanda(con_comentario, "cocina").height,
        )

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

    def test_comentario_especial_coexiste_con_preparacion_general(self):
        ticket, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="MESA-19"))
        agregar_partida(ticket, self.producto)
        alternar_comentario_general(ticket, "TODO_PLATO", "TODO POR PLATO")
        respuesta = self.client.patch(
            f"/api/tickets/{ticket.id}/",
            data=json.dumps({"comentario_general": "ENTREGAR EN DOS BOLSAS"}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.comentario_general, "ENTREGAR EN DOS BOLSAS")
        self.assertEqual(ticket.comentarios_generales, [{"codigo": "TODO_PLATO", "nombre": "TODO POR PLATO"}])

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
        self.assertEqual(_identificador_ticket(ticket), f"Ticket: {ticket.folio}, 4")
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
        self.assertEqual(trabajo.error, "No fue posible completar la impresión.")
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
            data=json.dumps({"forma_pago": "efectivo", "importe_recibido": "25", "imprimir_ticket": False, "clave_administrador": "1212"}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["impresiones"], [])
        self.assertFalse(TrabajoImpresion.objects.filter(ticket=ticket, formato="cuenta").exists())

    def test_cobro_rechaza_importes_ausentes_insuficientes_no_finitos_y_con_decimales_extra(self):
        mesa = Mesa.objects.get(sucursal=self.sucursal, clave="MESA-10")
        ticket, _ = abrir_ticket(mesa)
        agregar_partida(ticket, self.producto)
        procesar_ticket(ticket)
        casos = [None, "-1", "24.99", "NaN", "Infinity", "25.001"]
        for recibido in casos:
            respuesta = self.client.post(
                f"/api/tickets/{ticket.id}/cobrar/",
                data=json.dumps({"forma_pago": "efectivo", "importe_recibido": recibido, "clave_administrador": "1212"}),
                content_type="application/json",
            )
            self.assertEqual(respuesta.status_code, 400, recibido)
            ticket.refresh_from_db()
            self.assertEqual(ticket.estado, Ticket.Estado.PROCESADO)

        tarjeta_inexacta = self.client.post(
            f"/api/tickets/{ticket.id}/cobrar/",
            data=json.dumps({"forma_pago": "tarjeta", "importe_recibido": "26.00", "clave_administrador": "1212"}),
            content_type="application/json",
        )
        self.assertEqual(tarjeta_inexacta.status_code, 400)
        valida = self.client.post(
            f"/api/tickets/{ticket.id}/cobrar/",
            data=json.dumps(
                {"forma_pago": "efectivo", "importe_recibido": "25.00", "imprimir_ticket": False, "clave_administrador": "1212"}
            ),
            content_type="application/json",
        )
        self.assertEqual(valida.status_code, 200)

    def test_conversion_domicilio_recoger_mueve_la_misma_orden_y_usa_el_primer_lugar_libre(self):
        ocupado, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="REC-1"))
        self.assertEqual(ocupado.mesa.orden, 1)
        ticket, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="DOM-3"))
        agregar_partida(ticket, self.producto, comensal=2, cantidad=Decimal("3"))
        Ticket.objects.filter(pk=ticket.pk).update(
            cliente_nombre="Ana Pérez",
            cliente_telefono="3311223344",
            cliente_domicilio="Av. Vallarta 100",
        )
        ticket.refresh_from_db()

        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/convertir/",
            data=json.dumps({"canal": "recoger"}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        convertido = respuesta.json()["ticket"]
        self.assertEqual(convertido["id"], str(ticket.id))
        self.assertEqual(convertido["canal"], "recoger")
        self.assertEqual(convertido["posicion_numero"], 2)
        self.assertEqual(convertido["cliente"]["nombre"], "Ana Pérez")
        self.assertEqual(convertido["cliente"]["telefono"], "3311223344")
        self.assertEqual(len(convertido["partidas"]), 1)

        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/convertir/",
            data=json.dumps({"canal": "domicilio"}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        convertido = respuesta.json()["ticket"]
        self.assertEqual(convertido["posicion_numero"], 1)
        self.assertEqual(convertido["cliente"]["nombre"], "")
        self.assertEqual(convertido["cliente"]["telefono"], "")
        self.assertEqual(convertido["cliente"]["domicilio"], "")

    def test_recoger_exige_contacto_imprime_solo_comanda_y_no_genera_ticket_total(self):
        ticket, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="REC-3"))
        agregar_partida(ticket, self.producto)
        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/procesar/", data="{}", content_type="application/json"
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("nombre", respuesta.json()["error"].lower())

        respuesta = self.client.patch(
            f"/api/tickets/{ticket.id}/",
            data=json.dumps({"cliente_nombre": "Luis", "cliente_telefono": "3312345678", "terminal": True}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/procesar/", data="{}", content_type="application/json"
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual([item["formato"] for item in respuesta.json()["impresiones"]], ["comanda"])
        ticket.refresh_from_db()
        self.assertEqual(_identificador_ticket(ticket), f"Ticket: {ticket.folio}, R3")

        respuesta = self.client.post(
            f"/api/tickets/{ticket.id}/cobrar/",
            data=json.dumps({"forma_pago": "tarjeta", "importe_recibido": str(ticket.total), "imprimir_ticket": True, "clave_administrador": "1212"}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["impresiones"], [])
        self.assertFalse(TrabajoImpresion.objects.filter(ticket=ticket, formato="cuenta").exists())

    def test_terminal_se_imprime_al_final_de_la_comanda_de_domicilio(self):
        con_terminal, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="DOM-10"))
        sin_terminal, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="DOM-11"))
        agregar_partida(con_terminal, self.producto)
        agregar_partida(sin_terminal, self.producto)
        con_terminal.terminal = True
        con_terminal.save(update_fields=["terminal"])
        imagen_terminal = render_comanda(con_terminal, "cocina")
        imagen_normal = render_comanda(sin_terminal, "cocina")
        self.assertEqual(imagen_terminal.width, 576)
        self.assertGreaterEqual(imagen_terminal.height - imagen_normal.height, 50)

    def test_llevar_exige_nombre_y_conserva_el_flujo_de_comedor(self):
        ticket, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="LLEV-1"))
        agregar_partida(ticket, self.producto)
        with self.assertRaisesMessage(ErrorVenta, "nombre"):
            procesar_ticket(ticket)
        ticket.cliente_nombre = "Mónica"
        ticket.save(update_fields=["cliente_nombre"])
        procesar_ticket(ticket)
        trabajos = encolar_impresiones(ticket, TrabajoImpresion.Formato.COMANDA)
        self.assertEqual(len(trabajos), 1)
        self.assertEqual(trabajos[0].destino, TrabajoImpresion.Destino.COCINA)

    def test_pedido_por_nombres_reserva_cuatro_columnas_y_asocia_complementos(self):
        ticket, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="MESA-23"))
        ticket.captura_por_nombres = True
        ticket.nombres_comensales = {"1": "Amanda", "2": "Cecy"}
        ticket.save(update_fields=["captura_por_nombres", "nombres_comensales"])
        agregar_partida(ticket, self.producto, comensal=1, cantidad=Decimal("5"))
        agregar_partida(ticket, Producto.objects.get(sucursal=self.sucursal, codigo="CO05"), comensal=1)
        agregar_partida(ticket, Producto.objects.get(sucursal=self.sucursal, codigo="CC"), comensal=2)
        datos = _datos_comanda_por_nombres(ticket)
        self.assertEqual(len(datos["columnas"]), 4)
        self.assertEqual(sum(columna is None for columna in datos["columnas"]), 3)
        self.assertEqual([extra["nombre"] for extra in datos["extras"]], ["Amanda", "Cecy"])
        imagen = render_comanda(ticket, "cocina")
        self.assertEqual(imagen.width, 576)
        self.assertGreater(imagen.height, 300)
        procesar_ticket(ticket)

    def test_pedido_por_nombres_rechaza_un_quinto_producto_principal(self):
        ticket, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="MESA-24"))
        ticket.captura_por_nombres = True
        ticket.save(update_fields=["captura_por_nombres"])
        for codigo in ["TB", "TBI", "LB", "GRB"]:
            agregar_partida(ticket, Producto.objects.get(sucursal=self.sucursal, codigo=codigo))
        with self.assertRaisesMessage(ErrorVenta, "máximo de 4"):
            agregar_partida(ticket, Producto.objects.get(sucursal=self.sucursal, codigo="QKH"))
        self.assertEqual(ticket.partidas.count(), 4)

    def test_no_activa_modo_por_nombres_si_la_orden_ya_supera_cuatro_productos(self):
        ticket, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="MESA-24"))
        for codigo in ["TB", "TBI", "LB", "GRB", "QKH"]:
            agregar_partida(ticket, Producto.objects.get(sucursal=self.sucursal, codigo=codigo))
        respuesta = self.client.patch(
            f"/api/tickets/{ticket.id}/",
            data=json.dumps({"captura_por_nombres": True}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("máximo de 4", respuesta.json()["error"])
        ticket.refresh_from_db()
        self.assertFalse(ticket.captura_por_nombres)

    def test_administrador_valida_clave_gestiona_personal_y_permite_cambiar_su_clave(self):
        self.assertEqual(self.client.get("/api/administrador/resumen/").status_code, 401)
        self.assertEqual(self._autorizar_administrador("0000").status_code, 400)
        self.assertEqual(self._autorizar_administrador().status_code, 200)

        alta = self.client.post(
            "/api/administrador/usuarios/",
            data=json.dumps(
                {
                    "nombre": "Mesero de prueba",
                    "tipo": "mesero",
                    "clave": "4321",
                    "activo": True,
                    "clave_administrador": "1212",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(alta.status_code, 201)
        perfil = UsuarioPOS.objects.get(pk=alta.json()["usuario"]["id"])
        self.assertNotEqual(perfil.clave, "4321")
        self.assertTrue(perfil.check_clave("4321"))

        identificacion = self.client.post(
            "/api/operador/identificar/",
            data=json.dumps({"clave": "4321"}),
            content_type="application/json",
        )
        self.assertEqual(identificacion.status_code, 200)
        self.assertEqual(identificacion.json()["operador"]["nombre"], "Mesero de prueba")

        cambio = self.client.post(
            "/api/administrador/clave/",
            data=json.dumps({"clave_administrador": "1212", "nueva_clave": "3434"}),
            content_type="application/json",
        )
        self.assertEqual(cambio.status_code, 200)
        anterior = self.client.post(
            "/api/administrador/movimientos/",
            data=json.dumps(
                {
                    "tipo": "entrada",
                    "concepto": "Prueba de clave anterior",
                    "importe": "1",
                    "clave_administrador": "1212",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(anterior.status_code, 400)
        nueva = self.client.post(
            "/api/administrador/movimientos/",
            data=json.dumps(
                {
                    "tipo": "entrada",
                    "concepto": "Prueba de clave nueva",
                    "importe": "1",
                    "clave_administrador": "3434",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(nueva.status_code, 201)

    def test_domicilio_no_se_cobra_y_se_liquida_por_repartidor_con_fondo(self):
        rol = Rol.objects.get(sucursal=self.sucursal, tipo=Rol.Tipo.REPARTIDOR)
        repartidor = UsuarioPOS.objects.create(
            sucursal=self.sucursal,
            rol=rol,
            nombre="Repartidor prueba",
            clave="",
        )
        repartidor.set_clave("8765")
        repartidor.save(update_fields=["clave"])
        ticket, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="DOM-70"))
        agregar_partida(ticket, self.producto)
        ticket = self._asignar_cliente_domicilio(ticket, exterior="70")
        ticket.entrega_aproximada = time(14, 0)
        ticket.save(update_fields=["entrega_aproximada"])
        procesar_ticket(ticket)

        cobro = self.client.post(
            f"/api/tickets/{ticket.id}/cobrar/",
            data=json.dumps(
                {
                    "forma_pago": "efectivo",
                    "importe_recibido": "25",
                    "clave_administrador": "1212",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(cobro.status_code, 400)
        self.assertIn("domicilio", cobro.json()["error"].lower())

        self.assertEqual(self._autorizar_administrador().status_code, 200)
        asignacion = self.client.post(
            f"/api/administrador/tickets/{ticket.id}/repartidor/",
            data=json.dumps(
                {"repartidor_id": str(repartidor.id), "clave_administrador": "1212"}
            ),
            content_type="application/json",
        )
        self.assertEqual(asignacion.status_code, 200)
        liquidacion = self.client.post(
            "/api/administrador/liquidaciones/",
            data=json.dumps(
                {"repartidor_id": str(repartidor.id), "fondo": "50", "clave_administrador": "1212"}
            ),
            content_type="application/json",
        )
        self.assertEqual(liquidacion.status_code, 201)
        total = LiquidacionRepartidor.objects.get(pk=liquidacion.json()["liquidacion_id"])
        self.assertEqual(total.total_efectivo, Decimal("25.00"))
        self.assertEqual(total.total_terminal, Decimal("0.00"))
        self.assertEqual(total.total_a_entregar, Decimal("75.00"))
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, Ticket.Estado.PROCESADO)

    def test_programado_se_activa_por_fecha_en_la_primera_casilla_de_domicilio_libre(self):
        ocupado, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="DOM-1"))
        agregar_partida(ocupado, self.producto)
        ticket, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="DOM-8"))
        agregar_partida(ticket, self.producto)
        ticket = self._asignar_cliente_domicilio(ticket, nombre="Agenda prueba", exterior="8")
        ticket.entrega_aproximada = time(14, 0)
        ticket.save(update_fields=["entrega_aproximada"])
        procesar_ticket(ticket)
        fecha_programada = timezone.localdate() + timedelta(days=1)

        self.assertEqual(self._autorizar_administrador().status_code, 200)
        programacion = self.client.post(
            f"/api/administrador/tickets/{ticket.id}/programar/",
            data=json.dumps(
                {"fecha_programada": fecha_programada.isoformat(), "clave_administrador": "1212"}
            ),
            content_type="application/json",
        )
        self.assertEqual(programacion.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, Ticket.Estado.PROGRAMADO)
        self.assertEqual(activar_programados(self.sucursal, timezone.localdate()), 0)
        self.assertEqual(activar_programados(self.sucursal, fecha_programada), 1)
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, Ticket.Estado.PROCESADO)
        self.assertEqual(ticket.mesa.clave, "DOM-2")
        self.assertIsNotNone(ticket.activado_programado_en)
        self.assertEqual(ticket.entrega_aproximada, time(14, 0))

    def test_reporte_parcial_suma_cuatro_canales_sin_separar_estados(self):
        casos = [
            ("MESA-18", Ticket.Estado.ABIERTO),
            ("LLEV-9", Ticket.Estado.PROCESADO),
            ("DOM-71", Ticket.Estado.PROCESADO),
            ("REC-8", Ticket.Estado.PAGADO),
        ]
        for clave, estado_ticket in casos:
            ticket, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave=clave))
            agregar_partida(ticket, self.producto)
            Ticket.objects.filter(pk=ticket.pk).update(estado=estado_ticket)

        self.assertEqual(self._autorizar_administrador().status_code, 200)
        respuesta = self.client.post(
            "/api/administrador/reportes/parcial/",
            data=json.dumps({"clave_administrador": "1212"}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 201)
        reporte = ReporteAdministrativo.objects.get(pk=respuesta.json()["reporte_id"])
        self.assertEqual(
            reporte.datos["canales"],
            {"comedor": "25.00", "llevar": "25.00", "domicilio": "25.00", "recoger": "25.00"},
        )
        self.assertEqual(reporte.datos["total"], "100.00")
        self.assertNotIn("estados", reporte.datos)

    def test_admin_reasigna_descuenta_y_cancela_un_pedido_procesado_sin_borrar_auditoria(self):
        ticket, _ = abrir_ticket(Mesa.objects.get(sucursal=self.sucursal, clave="MESA-12"))
        agregar_partida(ticket, self.producto)
        procesar_ticket(ticket)
        destino = Mesa.objects.get(sucursal=self.sucursal, clave="LLEV-12")
        self.assertEqual(self._autorizar_administrador().status_code, 200)

        reasignacion = self.client.post(
            f"/api/administrador/tickets/{ticket.id}/reasignar/",
            data=json.dumps({"mesa_id": str(destino.id), "clave_administrador": "1212"}),
            content_type="application/json",
        )
        self.assertEqual(reasignacion.status_code, 200)
        descuento = self.client.post(
            f"/api/administrador/tickets/{ticket.id}/descuento/",
            data=json.dumps({"porcentaje": "10", "clave_administrador": "1212"}),
            content_type="application/json",
        )
        self.assertEqual(descuento.status_code, 200)
        self.assertEqual(Decimal(descuento.json()["ticket"]["total"]), Decimal("22.50"))
        cancelacion = self.client.post(
            f"/api/administrador/tickets/{ticket.id}/cancelar/",
            data=json.dumps({"clave_administrador": "1212"}),
            content_type="application/json",
        )
        self.assertEqual(cancelacion.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, Ticket.Estado.CANCELADO)
        self.assertEqual(ticket.canal, Mesa.Canal.LLEVAR)
        self.assertEqual(ticket.partidas.count(), 1)

    def test_corte_de_sucursal_agrupa_partidas_y_cierra_sus_pedidos(self):
        cliente = SucursalPedido.objects.get(sucursal=self.sucursal, origen_id=3)
        posicion = Mesa.objects.get(
            sucursal=self.sucursal,
            canal=Mesa.Canal.SUCURSALES,
            cliente_sucursal=cliente,
            orden=1,
        )
        producto = ProductoSucursal.objects.get(sucursal=self.sucursal, origen_id=1)
        ticket, _ = abrir_ticket(posicion)
        agregar_partida_sucursal(ticket, producto, Decimal("2.000"))
        procesar_ticket(ticket)
        self.assertEqual(self._autorizar_administrador().status_code, 200)
        respuesta = self.client.post(
            "/api/administrador/corte-sucursal/",
            data=json.dumps(
                {"cliente_sucursal_id": str(cliente.id), "clave_administrador": "1212"}
            ),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 201)
        reporte = ReporteAdministrativo.objects.get(pk=respuesta.json()["reporte_id"])
        self.assertEqual(reporte.tipo, ReporteAdministrativo.Tipo.CORTE_SUCURSAL)
        self.assertEqual(reporte.datos["total"], "386.00")
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, Ticket.Estado.PAGADO)


@override_settings(
    POS_REQUIRE_AUTH=True,
    MIDDLEWARE=SECURITY_MIDDLEWARE,
    PEDIDOS_SUCURSALES_AUTO_SYNC=False,
    PRINT_BACKEND="archivo",
    PRINT_SYNC=False,
)
class SeguridadPOSTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sucursal = Sucursal.objects.create(clave="ARBOLEDAS", nombre="Arboledas")
        cls.user = get_user_model().objects.create_user(
            username="operador-seguridad",
            password="Clave-prueba-segura-2026",
        )
        cls.rol = Rol.objects.create(
            sucursal=cls.sucursal,
            nombre="Operador de seguridad",
            puede_cobrar=True,
            puede_reimprimir=True,
            puede_cancelar=True,
            puede_sincronizar=True,
        )
        cls.perfil = UsuarioPOS.objects.create(
            sucursal=cls.sucursal,
            rol=cls.rol,
            cuenta=cls.user,
            nombre="Operador de seguridad",
            clave="",
        )
        cls.perfil.set_clave("1234")
        cls.perfil.save(update_fields=["clave"])

    def setUp(self):
        cache.clear()
        self.temporal = tempfile.TemporaryDirectory()
        self.media_settings = override_settings(MEDIA_ROOT=self.temporal.name)
        self.media_settings.enable()

    def tearDown(self):
        self.media_settings.disable()
        self.temporal.cleanup()
        cache.clear()

    def _login(self, client=None, **extra):
        client = client or self.client
        return client.post(
            "/acceso/",
            {"username": self.user.username, "password": "Clave-prueba-segura-2026", **extra},
        )

    def test_anonimo_recibe_redirect_en_ui_401_json_en_api_y_salud_publica(self):
        ui = self.client.get("/")
        api = self.client.get("/api/estado/")
        salud = self.client.get("/salud/")
        manifest = self.client.get("/manifest.webmanifest")

        self.assertRedirects(ui, "/acceso/?next=/", fetch_redirect_response=False)
        self.assertEqual(api.status_code, 401)
        self.assertEqual(api.json(), {"error": "Autenticación requerida."})
        self.assertEqual(api.headers["WWW-Authenticate"], "Session")
        self.assertIn("no-store", api.headers["Cache-Control"])
        self.assertIn("default-src 'self'", api.headers["Content-Security-Policy"])
        self.assertIn("object-src 'none'", api.headers["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", api.headers["Content-Security-Policy"])
        self.assertIn("camera=()", api.headers["Permissions-Policy"])
        self.assertEqual(api.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(salud.status_code, 200)
        self.assertEqual(salud.json(), {"estado": "ok"})
        self.assertEqual(manifest.status_code, 200)

    def test_ui_autenticada_no_se_guarda_en_cache_y_atribuye_el_operador(self):
        self.client.force_login(self.user)
        pagina = self.client.get("/")
        self.assertEqual(pagina.status_code, 200)
        self.assertIn("no-store", pagina.headers["Cache-Control"])
        identificacion = self.client.post(
            "/api/operador/identificar/",
            data=json.dumps({"clave": "1234"}),
            content_type="application/json",
        )
        self.assertEqual(identificacion.status_code, 200)

        mesa = Mesa.objects.create(
            sucursal=self.sucursal,
            canal=Mesa.Canal.COMEDOR,
            clave="AUDIT-ACTOR",
            nombre="Auditoría actor",
        )
        respuesta = self.client.post(
            "/api/tickets/abrir/",
            data=json.dumps({"mesa_id": str(mesa.id)}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        ticket = Ticket.objects.get(pk=respuesta.json()["ticket"]["id"])
        self.assertEqual(ticket.atendio, self.perfil)

    def test_acciones_cotidianas_usan_clave_admin_y_la_impresion_no_la_requiere(self):
        restringido = get_user_model().objects.create_user(
            username="operador-restringido",
            password="Clave-restringida-2026",
        )
        rol = Rol.objects.create(sucursal=self.sucursal, nombre="Sólo captura")
        perfil_restringido = UsuarioPOS.objects.create(
            sucursal=self.sucursal,
            rol=rol,
            cuenta=restringido,
            nombre="Sólo captura",
            clave="",
        )
        perfil_restringido.set_clave("5678")
        perfil_restringido.save(update_fields=["clave"])
        self.client.force_login(restringido)
        mesa = Mesa.objects.create(
            sucursal=self.sucursal,
            canal=Mesa.Canal.COMEDOR,
            clave="SEG-COBRO",
            nombre="Seguridad cobro",
        )
        ticket = Ticket.objects.create(
            sucursal=self.sucursal,
            mesa=mesa,
            folio=1,
            canal=Mesa.Canal.COMEDOR,
            estado=Ticket.Estado.PROCESADO,
        )

        sin_clave = self.client.post(
            f"/api/tickets/{ticket.id}/cobrar/",
            data=json.dumps({"forma_pago": "efectivo", "importe_recibido": "0"}),
            content_type="application/json",
        )
        self.assertEqual(sin_clave.status_code, 400)
        self.assertIn("clave", sin_clave.json()["error"].lower())

        impresion = self.client.post(
            f"/api/tickets/{ticket.id}/imprimir/",
            data=json.dumps({"formato": "cuenta"}),
            content_type="application/json",
        )
        self.assertEqual(impresion.status_code, 200)

        sincronizacion = self.client.post(
            "/api/sincronizacion/sucursales/",
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(sincronizacion.status_code, 403)
        self.assertIn("permiso", sincronizacion.json()["error"])

    def test_login_valido_sin_perfil_pos_no_crea_sesion(self):
        sin_perfil = get_user_model().objects.create_user(
            username="sin-perfil",
            password="Clave-sin-perfil-2026",
        )
        respuesta = self.client.post(
            "/acceso/",
            {"username": sin_perfil.username, "password": "Clave-sin-perfil-2026"},
        )
        self.assertEqual(respuesta.status_code, 403)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_rechaza_redirect_externo_y_logout_solo_acepta_post(self):
        login = self._login(next="https://malicioso.example/robar")
        self.assertRedirects(login, "/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/api/estado/").status_code, 200)
        self.assertEqual(self.client.get("/salir/").status_code, 405)
        self.assertRedirects(self.client.post("/salir/"), "/acceso/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/api/estado/").status_code, 401)

    def test_login_y_logout_exigen_csrf(self):
        client = Client(enforce_csrf_checks=True)
        page = client.get("/acceso/")
        token = page.cookies["csrftoken"].value
        credentials = {"username": self.user.username, "password": "Clave-prueba-segura-2026"}

        self.assertEqual(client.post("/acceso/", credentials).status_code, 403)
        login = client.post("/acceso/", credentials, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(login.status_code, 302)
        self.assertEqual(client.post("/salir/").status_code, 403)
        logout = client.post("/salir/", HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertEqual(logout.status_code, 302)

    @override_settings(
        POS_LOGIN_MAX_ATTEMPTS=10,
        POS_LOGIN_MAX_IP_ATTEMPTS=2,
        POS_LOGIN_LOCKOUT_SECONDS=321,
    )
    def test_rate_limit_agregado_bloquea_pulverizacion_de_usuarios_por_ip(self):
        first = self.client.post(
            "/acceso/",
            {"username": "usuario-inexistente-1", "password": "incorrecta"},
            REMOTE_ADDR="10.20.30.40",
        )
        second = self.client.post(
            "/acceso/",
            {"username": "usuario-inexistente-2", "password": "incorrecta"},
            REMOTE_ADDR="10.20.30.40",
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.headers["Retry-After"], "321")
        self.assertNotIn("10.20.30.40", " ".join(cache._cache.keys()))
        self.assertNotIn("usuario-inexistente", " ".join(cache._cache.keys()))

    def test_busqueda_pii_usa_post_y_json_no_objeto_devuelve_400(self):
        self.client.force_login(self.user)
        get_response = self.client.get("/api/clientes/buscar/?q=Maria")
        search = self.client.post(
            "/api/clientes/buscar/",
            data=json.dumps({"q": "Maria", "limite": 5}),
            content_type="application/json",
        )
        invalid_json_shape = self.client.post(
            "/api/clientes/",
            data="[]",
            content_type="application/json",
        )

        self.assertEqual(get_response.status_code, 405)
        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.wsgi_request.get_full_path(), "/api/clientes/buscar/")
        self.assertIn("no-store", search.headers["Cache-Control"])
        self.assertEqual(invalid_json_shape.status_code, 400)
        self.assertIn("debe ser un objeto", invalid_json_shape.json()["error"])

    def test_limita_registros_anidados_de_cliente(self):
        self.client.force_login(self.user)
        response = self.client.post(
            "/api/clientes/",
            data=json.dumps(
                {
                    "nombre": "Cliente excesivo",
                    "telefonos": [
                        {"numero": f"33123456{index:02d}", "etiqueta": "Celular"}
                        for index in range(11)
                    ],
                    "domicilios": [{"calle": "Patria", "numero_exterior": "1"}],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("máximo 10", response.json()["error"])

    def test_preview_impresion_requiere_sesion_y_valida_archivo(self):
        mesa = Mesa.objects.create(
            sucursal=self.sucursal,
            canal=Mesa.Canal.COMEDOR,
            clave="AUDIT-MESA",
            nombre="Auditoría",
        )
        ticket = Ticket.objects.create(sucursal=self.sucursal, mesa=mesa, folio=1, canal=Mesa.Canal.COMEDOR)
        relative = Path("impresiones") / "seguridad" / "preview.png"
        absolute = Path(self.temporal.name) / relative
        absolute.parent.mkdir(parents=True, exist_ok=True)
        png = b"\x89PNG\r\n\x1a\ncontenido-prueba"
        absolute.write_bytes(png)
        trabajo = TrabajoImpresion.objects.create(
            sucursal=self.sucursal,
            ticket=ticket,
            formato=TrabajoImpresion.Formato.CUENTA,
            destino=TrabajoImpresion.Destino.CAJA,
            archivo=relative.as_posix(),
        )
        url = f"/api/impresiones/{trabajo.id}/archivo/"

        self.assertEqual(self.client.get(url).status_code, 401)
        self.client.force_login(self.user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "image/png")
        self.assertTrue(response.headers["Content-Disposition"].startswith("inline;"))
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(b"".join(response.streaming_content), png)
        response.close()
        self.assertEqual(
            self.client.get(f"/api/impresiones/{uuid.uuid4()}/archivo/").status_code,
            404,
        )

    @override_settings(
        PRINT_BACKEND="tcp",
        PRINTER_HOSTS={"caja": "10.99.88.77"},
        PRINTER_PORT=9100,
        PRINTER_TIMEOUT=1,
    )
    @patch("impresion.services.socket.create_connection", side_effect=OSError("10.99.88.77:9100 secreto"))
    def test_estado_impresora_no_expone_topologia_ni_error_crudo(self, _connection):
        self.client.force_login(self.user)
        response = self.client.get("/api/impresion/estado/")
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("host", payload)
        self.assertNotIn("puerto", payload)
        self.assertNotIn("10.99.88.77", payload["mensaje"])
        self.assertNotIn("secreto", payload["mensaje"])
