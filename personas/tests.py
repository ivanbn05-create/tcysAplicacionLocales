from io import StringIO
from pathlib import Path
import tempfile
import uuid
from unittest.mock import patch
from zipfile import BadZipFile

from catalogo.models import Categoria, Precio, Producto
from django.contrib.auth import authenticate, get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from personas.identidad import normalizar_clave_sucursal, normalizar_nombre_sucursal
from personas.models import ModuloSucursal, Rol, Sucursal, UsuarioPOS
from personas.modulos import MODULOS_NUCLEO, configurar_modulos
from ventas.models import Mesa, PrecioProductoSucursal, ProductoSucursal, SucursalPedido


class NormalizacionIdentidadTests(SimpleTestCase):
    def test_normaliza_clave_sucursal(self):
        self.assertEqual(normalizar_clave_sucursal("  sucursal-norte_2 "), "SUCURSAL-NORTE_2")

    def test_rechaza_claves_ambiguas_o_fuera_de_rango(self):
        for clave in ("", "CON ESPACIOS", "-NORTE", "NORTE-", "ÁRBOL", "A" * 31):
            with self.subTest(clave=clave), self.assertRaises(ValueError):
                normalizar_clave_sucursal(clave)

    def test_normaliza_y_valida_nombre(self):
        self.assertEqual(normalizar_nombre_sucursal("  Sucursal Norte  "), "Sucursal Norte")
        for nombre in ("", "   ", "Norte\nSur", "A" * 121):
            with self.subTest(nombre=nombre), self.assertRaises(ValueError):
                normalizar_nombre_sucursal(nombre)


@override_settings(SUCURSAL_CLAVE="NORTE_2")
class AprovisionarSucursalTests(TestCase):
    def ejecutar(self, **opciones):
        salida = StringIO()
        call_command(
            "aprovisionar_sucursal",
            clave=opciones.get("clave", "norte_2"),
            nombre=opciones.get("nombre", "Sucursal Norte"),
            sucursal_id=opciones.get("sucursal_id"),
            stdout=salida,
        )
        return salida.getvalue()

    def test_crea_solo_la_identidad_configurada(self):
        salida = self.ejecutar()

        sucursal = Sucursal.objects.get()
        self.assertEqual(sucursal.clave, "NORTE_2")
        self.assertEqual(sucursal.nombre, "Sucursal Norte")
        self.assertTrue(sucursal.activa)
        self.assertEqual(sucursal.roles.count(), 0)
        self.assertEqual(sucursal.usuarios_pos.count(), 0)
        self.assertIn("Sucursal aprovisionada", salida)

    def test_repetir_el_mismo_aprovisionamiento_no_modifica_la_sucursal(self):
        self.ejecutar()
        original = Sucursal.objects.get()

        salida = self.ejecutar(clave="NORTE_2", nombre="Sucursal Norte")
        repetida = Sucursal.objects.get()

        self.assertEqual(Sucursal.objects.count(), 1)
        self.assertEqual(repetida.id, original.id)
        self.assertEqual(repetida.creada_en, original.creada_en)
        self.assertIn("ya aprovisionada sin cambios", salida)

    def test_admite_uuid_central_y_no_permite_reemplazarlo(self):
        sucursal_id = uuid.uuid4()
        self.ejecutar(sucursal_id=str(sucursal_id))
        self.assertEqual(Sucursal.objects.get().id, sucursal_id)

        with self.assertRaisesMessage(CommandError, "no reemplaza una identidad existente"):
            self.ejecutar(sucursal_id=str(uuid.uuid4()))

        self.assertEqual(Sucursal.objects.get().id, sucursal_id)

    def test_rechaza_uuid_invalido_sin_cambiar_la_base(self):
        with self.assertRaisesMessage(CommandError, "UUID válido"):
            self.ejecutar(sucursal_id="no-es-un-uuid")

        self.assertFalse(Sucursal.objects.exists())

    def test_rechaza_uuid_vacio_sin_cambiar_la_base(self):
        with self.assertRaisesMessage(CommandError, "UUID vacío"):
            self.ejecutar(sucursal_id="00000000-0000-0000-0000-000000000000")

        self.assertFalse(Sucursal.objects.exists())

    def test_rechaza_una_clave_distinta_a_la_configurada(self):
        with self.assertRaisesMessage(CommandError, "no coincide con SUCURSAL_CLAVE"):
            self.ejecutar(clave="SUR")

        self.assertFalse(Sucursal.objects.exists())

    def test_rechaza_cambiar_el_nombre_de_una_identidad_existente(self):
        self.ejecutar()

        with self.assertRaisesMessage(CommandError, "no modifica una identidad existente"):
            self.ejecutar(nombre="Otro nombre")

        self.assertEqual(Sucursal.objects.get().nombre, "Sucursal Norte")

    def test_rechaza_una_base_que_ya_pertenece_a_otra_sucursal(self):
        Sucursal.objects.create(clave="SUR", nombre="Sucursal Sur")

        with self.assertRaisesMessage(CommandError, "ya contiene otra identidad"):
            self.ejecutar()

        self.assertEqual(Sucursal.objects.count(), 1)

    def test_rechaza_reactivar_implicitamente_una_sucursal(self):
        Sucursal.objects.create(clave="NORTE_2", nombre="Sucursal Norte", activa=False)

        with self.assertRaisesMessage(CommandError, "está inactiva"):
            self.ejecutar()

        self.assertFalse(Sucursal.objects.get().activa)

    def test_serializa_el_primer_alta_en_postgresql(self):
        with patch(
            "personas.management.commands.aprovisionar_sucursal.connection"
        ) as conexion:
            conexion.vendor = "postgresql"
            cursor = conexion.cursor.return_value.__enter__.return_value
            self.ejecutar()

        cursor.execute.assert_called_once_with(
            "SELECT pg_advisory_xact_lock(%s)", [2026091101]
        )


class CargaHistoricaSucursalTests(TestCase):
    @override_settings(SUCURSAL_CLAVE="ARBOLEDAS")
    def test_exige_arboledas_ya_aprovisionada(self):
        with self.assertRaisesMessage(CommandError, "ya aprovisionada"):
            call_command("cargar_datos_iniciales", verbosity=0)

        self.assertFalse(Sucursal.objects.exists())

    @override_settings(SUCURSAL_CLAVE="NORTE")
    def test_rechaza_otra_sucursal_sin_crear_arboledas(self):
        Sucursal.objects.create(clave="NORTE", nombre="Sucursal Norte")

        with self.assertRaisesMessage(CommandError, "sólo se admite"):
            call_command("cargar_datos_iniciales", verbosity=0)

        self.assertEqual(list(Sucursal.objects.values_list("clave", flat=True)), ["NORTE"])

    @override_settings(SUCURSAL_CLAVE="ARBOLEDAS")
    def test_xlsx_corrupto_revierte_toda_la_carga_historica(self):
        sucursal = Sucursal.objects.create(clave="ARBOLEDAS", nombre="Arboledas")

        with tempfile.TemporaryDirectory() as temporal:
            base_dir = Path(temporal)
            directorio_datos = base_dir / "datos"
            directorio_datos.mkdir()
            (directorio_datos / "Listado-Productos.xlsx").write_bytes(
                b"esto no es un archivo XLSX"
            )

            with override_settings(BASE_DIR=base_dir), self.assertRaisesMessage(
                CommandError, "se revirtió toda la carga"
            ) as error:
                call_command("cargar_datos_iniciales", verbosity=0)

        self.assertIsInstance(error.exception.__cause__, BadZipFile)

        self.assertEqual(list(Sucursal.objects.all()), [sucursal])
        self.assertFalse(Rol.objects.exists())
        self.assertFalse(Categoria.objects.exists())
        self.assertFalse(Producto.objects.exists())
        self.assertFalse(Precio.objects.exists())
        self.assertFalse(Mesa.objects.exists())
        self.assertFalse(SucursalPedido.objects.exists())
        self.assertFalse(ProductoSucursal.objects.exists())
        self.assertFalse(PrecioProductoSucursal.objects.exists())


@override_settings(SUCURSAL_CLAVE="NORTE")
class VerificarIdentidadLocalTests(TestCase):
    def test_admite_base_vacia_para_instalacion_inicial(self):
        salida = StringIO()
        call_command("verificar_identidad_local", stdout=salida)
        self.assertIn("Base sin identidad", salida.getvalue())

    def test_admite_exactamente_la_identidad_activa_configurada(self):
        Sucursal.objects.create(clave="NORTE", nombre="Sucursal Norte")
        salida = StringIO()
        call_command("verificar_identidad_local", stdout=salida)
        self.assertIn("Identidad local verificada", salida.getvalue())

    def test_rechaza_otra_identidad_sin_modificarla(self):
        Sucursal.objects.create(clave="SUR", nombre="Sucursal Sur")
        with self.assertRaisesMessage(CommandError, "no coincide"):
            call_command("verificar_identidad_local", verbosity=0)
        self.assertEqual(list(Sucursal.objects.values_list("clave", flat=True)), ["SUR"])

    def test_rechaza_multiples_identidades(self):
        Sucursal.objects.create(clave="NORTE", nombre="Sucursal Norte")
        Sucursal.objects.create(clave="SUR", nombre="Sucursal Sur")
        with self.assertRaisesMessage(CommandError, "más de una identidad"):
            call_command("verificar_identidad_local", verbosity=0)


@override_settings(SUCURSAL_CLAVE="NORTE")
class ModulosSucursalTests(TestCase):
    def setUp(self):
        self.sucursal = Sucursal.objects.create(clave="NORTE", nombre="Sucursal Norte")

    def test_configura_nucleo_y_resuelve_dependencias(self):
        salida = StringIO()
        call_command(
            "configurar_modulos_sucursal",
            modulos="programados",
            stdout=salida,
        )
        estados = dict(
            ModuloSucursal.objects.filter(sucursal=self.sucursal).values_list(
                "modulo__clave", "habilitado"
            )
        )
        self.assertTrue(all(estados[clave] for clave in MODULOS_NUCLEO))
        self.assertTrue(estados["programados"])
        self.assertTrue(estados["domicilios"])
        self.assertFalse(estados["reparto"])
        self.assertFalse(estados["pedidos_sucursales"])
        nucleo = ModuloSucursal.objects.get(
            sucursal=self.sucursal, modulo__clave="pos"
        )
        nucleo.habilitado = False
        nucleo.save(update_fields=["habilitado", "actualizado_en"])
        from personas.modulos import modulo_habilitado
        self.assertTrue(modulo_habilitado(self.sucursal, "pos"))
        self.assertIn("programados", salida.getvalue())

    def test_rechaza_modulo_desconocido_sin_estado_parcial(self):
        with self.assertRaisesMessage(CommandError, "desconocidos"):
            call_command("configurar_modulos_sucursal", modulos="inventado")
        self.assertFalse(ModuloSucursal.objects.filter(sucursal=self.sucursal).exists())

    def test_desactivar_conserva_configuracion_y_datos(self):
        configurar_modulos(self.sucursal, ["domicilios"])
        asignacion = ModuloSucursal.objects.get(
            sucursal=self.sucursal, modulo__clave="domicilios"
        )
        asignacion.configuracion = {"zona": "norte"}
        asignacion.save(update_fields=["configuracion", "actualizado_en"])

        call_command("configurar_modulos_sucursal", sin_opcionales=True, verbosity=0)

        asignacion.refresh_from_db()
        self.assertFalse(asignacion.habilitado)
        self.assertEqual(asignacion.configuracion, {"zona": "norte"})

    def test_api_y_ui_rechazan_modulos_deshabilitados(self):
        call_command("configurar_modulos_sucursal", sin_opcionales=True, verbosity=0)
        user = get_user_model().objects.create_superuser(
            username="admin-modulos", password="ClaveSegura!2026"
        )
        self.client.force_login(user)

        api = self.client.post("/api/sincronizacion/sucursales/")
        self.assertEqual(api.status_code, 403)
        self.assertEqual(api.json()["codigo"], "modulo_no_habilitado")

        interfaz = self.client.get("/")
        self.assertEqual(interfaz.status_code, 200)
        self.assertNotContains(interfaz, 'data-canal="domicilio"')
        self.assertNotContains(interfaz, 'data-canal="sucursales"')


@override_settings(SUCURSAL_CLAVE="NORTE")
class CrearOperadorInicialTests(TestCase):
    def setUp(self):
        self.sucursal = Sucursal.objects.create(clave="NORTE", nombre="Sucursal Norte")

    @patch(
        "personas.management.commands.crear_operador_pos.getpass",
        side_effect=["ClaveOperativa!2026", "ClaveOperativa!2026"],
    )
    def test_crea_perfil_cuenta_y_pin_en_una_base_nueva(self, _getpass):
        salida = StringIO()
        call_command(
            "crear_operador_pos",
            username="operador",
            nombre="Caja principal",
            pin="4321",
            crear_perfil_inicial=True,
            stdout=salida,
        )

        perfil = UsuarioPOS.objects.select_related("cuenta", "rol").get()
        self.assertEqual(perfil.nombre, "Caja principal")
        self.assertEqual(perfil.rol.tipo, Rol.Tipo.ENCARGADO)
        self.assertTrue(perfil.check_clave("4321"))
        self.assertFalse(perfil.cuenta.is_staff)
        self.assertIsNotNone(authenticate(username="operador", password="ClaveOperativa!2026"))
        self.assertIn("vinculada", salida.getvalue())

    def test_sin_autorizacion_de_alta_conserva_el_contrato_anterior(self):
        with self.assertRaisesMessage(CommandError, "No hay un perfil POS libre"):
            call_command("crear_operador_pos", username="operador", verbosity=0)
        self.assertFalse(UsuarioPOS.objects.exists())
