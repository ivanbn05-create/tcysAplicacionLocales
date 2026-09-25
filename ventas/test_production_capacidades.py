import json

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import Group
from django.test import RequestFactory, TestCase, override_settings

from personas.capacidades import Capacidad, perfil_tiene_capacidad, tiene_capacidad
from personas.models import Rol, Sucursal, UsuarioPOS
from ventas.models import ConfiguracionSucursal, Mesa


@override_settings(
    SUCURSAL_CLAVE="ARBOLEDAS",
    POS_REQUIRE_AUTH=True,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class CapacidadesProductionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sucursal = Sucursal.objects.create(clave="ARBOLEDAS", nombre="Arboledas")
        ConfiguracionSucursal.objects.create(
            sucursal=cls.sucursal, clave_administrador=make_password("0000")
        )
        cls.perfiles = {}
        for tipo, pin in (
            (Rol.Tipo.DUENO, "1111"),
            (Rol.Tipo.ELEVADO, "2222"),
            (Rol.Tipo.MESERO, "3333"),
            (Rol.Tipo.REPARTIDOR, "4444"),
        ):
            rol = Rol.objects.create(sucursal=cls.sucursal, nombre=tipo, tipo=tipo)
            cuenta = get_user_model().objects.create_user(
                username=f"production-{tipo}", password="ClaveSegura!2026"
            )
            perfil = UsuarioPOS(
                sucursal=cls.sucursal, rol=rol, cuenta=cuenta, nombre=tipo, clave=""
            )
            perfil.set_clave(pin)
            perfil.save()
            cls.perfiles[tipo] = perfil
        cls.mesa = Mesa.objects.create(
            sucursal=cls.sucursal,
            canal=Mesa.Canal.COMEDOR,
            clave="P1-COMEDOR",
            nombre="Mesa Production",
        )

    def _como(self, tipo):
        self.client.force_login(self.perfiles[tipo].cuenta)

    def _post(self, ruta, datos):
        return self.client.post(
            ruta, data=json.dumps(datos), content_type="application/json"
        )

    def test_matriz_explicita_de_capacidades_y_revocacion(self):
        dueno = self.perfiles[Rol.Tipo.DUENO]
        elevado = self.perfiles[Rol.Tipo.ELEVADO]
        mesero = self.perfiles[Rol.Tipo.MESERO]
        repartidor = self.perfiles[Rol.Tipo.REPARTIDOR]

        for capacidad in (
            Capacidad.VENTAS, Capacidad.COMANDAS, Capacidad.MODIFICAR_PARTIDAS,
            Capacidad.COBRAR, Capacidad.CANCELAR, Capacidad.ADMINISTRAR_NEGOCIO,
            Capacidad.GESTIONAR_USUARIOS, Capacidad.CAMBIAR_PINES_AJENOS,
            Capacidad.REINICIAR_FOLIOS,
        ):
            self.assertTrue(perfil_tiene_capacidad(dueno, capacidad), capacidad)
        for capacidad in (Capacidad.COBRAR, Capacidad.CANCELAR,
                          Capacidad.ADMINISTRAR_NEGOCIO):
            self.assertTrue(perfil_tiene_capacidad(elevado, capacidad), capacidad)
        for capacidad in (Capacidad.GESTIONAR_USUARIOS,
                          Capacidad.CAMBIAR_PINES_AJENOS,
                          Capacidad.REINICIAR_FOLIOS):
            self.assertFalse(perfil_tiene_capacidad(elevado, capacidad), capacidad)
        self.assertTrue(perfil_tiene_capacidad(mesero, Capacidad.COMANDAS))
        self.assertTrue(perfil_tiene_capacidad(mesero, Capacidad.MODIFICAR_PARTIDAS))
        for capacidad in (Capacidad.COBRAR, Capacidad.CANCELAR,
                          Capacidad.ADMINISTRAR_NEGOCIO):
            self.assertFalse(perfil_tiene_capacidad(mesero, capacidad), capacidad)
        self.assertEqual(repartidor.rol.capacidades, [])
        mesero_legado = Rol.objects.create(
            sucursal=self.sucursal, nombre="Mesero con flag antiguo",
            tipo=Rol.Tipo.MESERO, puede_cobrar=True,
        )
        self.assertNotIn(Capacidad.COBRAR, mesero_legado.capacidades)
        legado = Rol.objects.create(
            sucursal=self.sucursal, nombre="Encargado antiguo",
            tipo=Rol.Tipo.ENCARGADO,
        )
        self.assertNotIn(Capacidad.ADMINISTRAR_NEGOCIO, legado.capacidades)
        self.assertNotIn(Capacidad.GESTIONAR_USUARIOS, legado.capacidades)
        self.assertNotIn(Capacidad.REINICIAR_FOLIOS, legado.capacidades)

        dueno.rol.capacidades.remove(Capacidad.COBRAR)
        dueno.rol.save(update_fields=["capacidades"])
        self.assertFalse(perfil_tiene_capacidad(dueno, Capacidad.COBRAR))

    def test_dueno_gestiona_personal_y_reinicia_folios(self):
        self._como(Rol.Tipo.DUENO)
        acceso = self._post("/api/administrador/acceso/", {"clave_administrador": "1111"})
        self.assertEqual(acceso.status_code, 200)
        self.assertTrue(acceso.json()["permisos"]["gestionar_usuarios"])
        self.assertFalse(acceso.json()["permisos"]["gestionar_configuracion_tecnica"])

        alta = self._post(
            "/api/administrador/usuarios/",
            {"nombre": "Mesera nueva", "tipo": "mesero", "clave": "5555"},
        )
        self.assertEqual(alta.status_code, 201)
        self.assertEqual(
            self._post("/api/administrador/folios/reiniciar/", {}).status_code, 200
        )

    def test_elevado_opera_administracion_cotidiana_sin_identidades_ni_folios(self):
        self._como(Rol.Tipo.ELEVADO)
        acceso = self._post("/api/administrador/acceso/", {"clave_administrador": "2222"})
        self.assertEqual(acceso.status_code, 200)
        self.assertEqual(acceso.json()["nivel"], "elevado")
        self.assertEqual(self.client.get("/api/administrador/resumen/").status_code, 200)
        for ruta, datos in (
            ("/api/administrador/usuarios/", {"nombre": "Prohibido", "tipo": "mesero", "clave": "5555"}),
            ("/api/administrador/folios/reiniciar/", {}),
            ("/api/administrador/clave/", {"clave_administrador": "0000", "nueva_clave": "9999"}),
        ):
            self.assertEqual(self._post(ruta, datos).status_code, 403)

        # Conocer la clave maestra tampoco concede capacidades de dueño.
        acceso_maestro = self._post("/api/administrador/acceso/", {"clave_administrador": "0000"})
        self.assertEqual(acceso_maestro.status_code, 200)
        self.assertFalse(acceso_maestro.json()["permisos"]["gestionar_usuarios"])
        self.assertEqual(self._post("/api/administrador/folios/reiniciar/", {}).status_code, 403)

    def test_mesero_abre_comanda_pero_no_cobra_cancela_ni_administra(self):
        self._como(Rol.Tipo.MESERO)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(
            self._post("/api/operador/identificar/", {"clave": "3333"}).status_code,
            200,
        )
        apertura = self._post("/api/tickets/abrir/", {"mesa_id": str(self.mesa.id)})
        self.assertEqual(apertura.status_code, 200)
        ticket_id = apertura.json()["ticket"]["id"]
        for ruta, datos in (
            (f"/api/tickets/{ticket_id}/cobrar/", {"clave_administrador": "0000"}),
            (f"/api/tickets/{ticket_id}/cancelar/", {}),
            (f"/api/tickets/{ticket_id}/partidas/ajustar/", {"partida_ids": [], "eliminar": True}),
            ("/api/administrador/acceso/", {"clave_administrador": "0000"}),
        ):
            self.assertEqual(self._post(ruta, datos).status_code, 403)
        self.assertEqual(self.client.get("/administrador/").status_code, 403)

    def test_repartidor_no_entra_a_ventas(self):
        self._como(Rol.Tipo.REPARTIDOR)
        self.assertEqual(self.client.get("/").status_code, 403)
        self.assertEqual(self.client.get("/api/estado/").status_code, 403)
        self.client.logout()
        acceso = self.client.post(
            "/acceso/",
            {"username": "production-repartidor", "password": "ClaveSegura!2026"},
        )
        self.assertEqual(acceso.status_code, 403)

    def test_soporte_tecnico_exige_staff_y_grupo_sin_heredar_ventas(self):
        grupo, _ = Group.objects.get_or_create(name="soporte_tecnico_edge")
        tecnico = get_user_model().objects.create_user(
            username="soporte-production", password="ClaveSegura!2026", is_staff=True
        )
        solicitud = RequestFactory().get("/soporte/")
        solicitud.user = tecnico
        self.assertFalse(tiene_capacidad(solicitud, Capacidad.SOPORTE_TECNICO))
        tecnico.groups.add(grupo)
        self.assertTrue(tiene_capacidad(solicitud, Capacidad.SOPORTE_TECNICO))
        self.assertFalse(tiene_capacidad(solicitud, Capacidad.VENTAS))
        self.client.force_login(tecnico)
        self.assertEqual(self.client.get("/").status_code, 403)

        tecnico.is_staff = False
        tecnico.save(update_fields=["is_staff"])
        self.assertFalse(tiene_capacidad(solicitud, Capacidad.SOPORTE_TECNICO))
