import copy
import uuid
from datetime import date
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from catalogo.models import Categoria, IdentidadProductoCentral, Precio, Producto, PublicacionCatalogoCentral
from impresion.render import _es_promocion
from personas.models import Sucursal
from ventas.models import (
    DefinicionPromocion,
    Mesa,
    Partida,
    Ticket,
)
from ventas.promociones import (
    aplicar_promociones_publicadas,
    capacidad_componentes,
    configuracion_promocion,
    promocion_disponible,
    validar_cupo_componente,
    validar_promociones,
    validar_promociones_publicadas,
)


class PromocionesPublicadasTests(TestCase):
    def setUp(self):
        self.sucursal = Sucursal.objects.create(clave="DINAMICAS", nombre="Dinámicas")
        self.categoria = Categoria.objects.create(sucursal=self.sucursal, nombre="Menú")
        self.mapeos = {}
        self.productos = {}
        for codigo, precio in (("NUEVA", "79.00"), ("NUEVA2", "49.00"), ("TACO", "25.00"), ("AGUA", "30.00"), ("TACO2", "27.00")):
            producto = Producto.objects.create(
                sucursal=self.sucursal, categoria=self.categoria,
                codigo=codigo, nombre=codigo, nombre_corto=codigo,
            )
            Precio.objects.create(sucursal=self.sucursal, producto=producto, importe=Decimal(precio))
            identidad = IdentidadProductoCentral.objects.create(
                sucursal=self.sucursal, central_id=uuid.uuid4(), producto=producto,
            )
            self.productos[codigo] = producto
            self.mapeos[identidad.central_id] = identidad
        self.ids = {codigo: self.productos[codigo].identidad_central.central_id for codigo in self.productos}
        self.snapshot_productos = {
            central_id: {
                "activo": True,
                "codigo": identidad.producto.codigo,
                "disponible_sucursal": True,
                "precio": {"importe": str(identidad.producto.precio_actual().importe)},
            }
            for central_id, identidad in self.mapeos.items()
        }
        self.regla = {
            "id": str(uuid.uuid4()),
            "codigo": "NUEVA",
            "nombre": "Promoción nueva",
            "producto_central_id": str(self.ids["NUEVA"]),
            "precio": "79.00",
            "activo": True,
            "dias_semana": [0, 1, 2, 3, 4, 5, 6],
            "fecha_desde": None,
            "fecha_hasta": None,
            "grupos": [
                {
                    "id": str(uuid.uuid4()), "nombre": "Tacos", "orden": 0,
                    "cantidad": 2,
                    "productos_permitidos": [str(self.ids["TACO"]), str(self.ids["TACO2"])],
                },
                {
                    "id": str(uuid.uuid4()), "nombre": "Bebida", "orden": 1,
                    "cantidad": 1,
                    "productos_permitidos": [str(self.ids["AGUA"])],
                },
            ],
        }
        self.mesa = Mesa.objects.create(
            sucursal=self.sucursal, canal=Mesa.Canal.COMEDOR, clave="M1", nombre="Mesa 1"
        )
        self.ticket = Ticket.objects.create(
            sucursal=self.sucursal, mesa=self.mesa, folio=1, canal=Mesa.Canal.COMEDOR,
        )

    def publicar(self, reglas=None, version=1):
        reglas = [self.regla] if reglas is None else reglas
        validar_promociones_publicadas(reglas, self.snapshot_productos)
        publicacion = PublicacionCatalogoCentral.objects.create(
            sucursal=self.sucursal, release_id=uuid.uuid4(), publicacion_id=uuid.uuid4(),
            version=version, version_contrato=3,
            estado=PublicacionCatalogoCentral.Estado.APLICADA,
            checksum="0" * 64,
        )
        aplicar_promociones_publicadas(self.sucursal, publicacion, reglas, self.mapeos)
        return publicacion

    def linea(self, producto, **campos):
        cantidad = campos.pop("cantidad", Decimal("1"))
        precio = producto.precio_actual().importe
        return Partida.objects.create(
            sucursal=self.sucursal, ticket=self.ticket, producto=producto,
            comensal=1, cantidad=cantidad, precio_unitario=precio,
            precio_lista_capturado=precio, nombre_producto=producto.nombre,
            nombre_corto=producto.nombre_corto, **campos,
        )

    def test_reimpresion_conserva_clasificacion_capturada_tras_publicacion(self):
        # Una venta anterior de este artículo ordinario no se convierte en
        # promoción histórica cuando una publicación introduce la regla.
        ordinaria = self.linea(self.productos["NUEVA"])
        self.assertFalse(_es_promocion(ordinaria))
        self.publicar()
        self.assertFalse(_es_promocion(ordinaria))
        raiz = self.linea(
            self.productos["NUEVA"],
            promocion_definicion=configuracion_promocion(self.productos["NUEVA"]),
        )
        self.assertTrue(_es_promocion(raiz))

    def test_grupo_con_varios_productos_permitidos_y_cantidad_exacta(self):
        self.publicar()
        definicion = configuracion_promocion(self.productos["NUEVA"])
        raiz = self.linea(self.productos["NUEVA"], promocion_definicion=definicion)
        grupos = list(definicion.grupos.all())
        tacos, bebida = grupos
        for codigo in ("TACO", "TACO2"):
            grupo = validar_cupo_componente(raiz, self.productos[codigo], Decimal("1"), grupo_id=tacos.id)
            self.linea(self.productos[codigo], promocion_aplicada=raiz, promocion_grupo=grupo)
        grupo = validar_cupo_componente(raiz, self.productos["AGUA"], Decimal("1"), grupo_id=bebida.id)
        self.linea(self.productos["AGUA"], promocion_aplicada=raiz, promocion_grupo=grupo)
        self.assertEqual(capacidad_componentes(raiz), {tacos.id: Decimal("2"), bebida.id: Decimal("1")})
        validar_promociones(self.ticket)
        with self.assertRaisesMessage(ValueError, "sólo admite 0"):
            validar_cupo_componente(raiz, self.productos["TACO"], Decimal("1"), grupo_id=tacos.id)
        with self.assertRaisesMessage(ValueError, "no forma parte"):
            validar_cupo_componente(raiz, self.productos["AGUA"], Decimal("1"), grupo_id=tacos.id)

    def test_cantidad_insuficiente_y_producto_no_permitido(self):
        self.publicar()
        definicion = configuracion_promocion(self.productos["NUEVA"])
        raiz = self.linea(self.productos["NUEVA"], promocion_definicion=definicion)
        with self.assertRaisesMessage(ValueError, "Completa la promoción NUEVA"):
            validar_promociones(self.ticket)
        with self.assertRaisesMessage(ValueError, "no forma parte"):
            validar_cupo_componente(raiz, self.productos["NUEVA"], Decimal("1"))

    def test_dias_fechas_estado_y_nueva_publicacion_congelan_ticket_abierto_y_cerrado(self):
        self.regla["dias_semana"] = [0]
        self.regla["fecha_desde"] = "2026-09-01"
        self.regla["fecha_hasta"] = "2026-09-30"
        self.publicar()
        vieja = configuracion_promocion(self.productos["NUEVA"])
        self.assertTrue(promocion_disponible(self.productos["NUEVA"], date(2026, 9, 7)))
        self.assertFalse(promocion_disponible(self.productos["NUEVA"], date(2026, 9, 6)))
        self.assertFalse(promocion_disponible(self.productos["NUEVA"], date(2026, 8, 31)))
        self.assertFalse(promocion_disponible(self.productos["NUEVA"], date(2026, 10, 5)))
        raiz = self.linea(self.productos["NUEVA"], promocion_definicion=vieja)
        regla_nueva = copy.deepcopy(self.regla)
        regla_nueva["precio"] = "79.00"
        regla_nueva["grupos"][0]["cantidad"] = 1
        regla_nueva["activo"] = False
        self.publicar([regla_nueva], version=2)
        nueva = configuracion_promocion(self.productos["NUEVA"])
        self.assertNotEqual(nueva.id, vieja.id)
        self.assertFalse(promocion_disponible(self.productos["NUEVA"], date(2026, 9, 7)))
        self.assertEqual(configuracion_promocion(self.productos["NUEVA"], partida=raiz).id, vieja.id)
        self.assertEqual(next(iter(capacidad_componentes(raiz).values())), Decimal("2"))
        self.ticket.estado = Ticket.Estado.PAGADO
        self.ticket.save(update_fields=["estado"])
        raiz.refresh_from_db()
        self.assertEqual(raiz.precio_unitario, Decimal("79.00"))
        self.assertEqual(raiz.nombre_producto, "NUEVA")

    def test_producto_exacto_y_dos_promociones_independientes(self):
        segunda = {
            "id": str(uuid.uuid4()), "codigo": "NUEVA2", "nombre": "Otra promoción",
            "producto_central_id": str(self.ids["NUEVA2"]), "precio": "49.00",
            "activo": True, "dias_semana": [0, 1, 2, 3, 4, 5, 6],
            "fecha_desde": None, "fecha_hasta": None,
            "grupos": [{
                "id": str(uuid.uuid4()), "nombre": "Taco único", "orden": 0,
                "cantidad": 1, "productos_permitidos": [str(self.ids["TACO"])],
            }],
        }
        # Un mismo maestro puede ser elegible en varias reglas; cada partida
        # concreta pertenece a una sola raíz mediante promocion_aplicada.
        self.publicar([self.regla, segunda])
        raiz1 = self.linea(
            self.productos["NUEVA"],
            promocion_definicion=configuracion_promocion(self.productos["NUEVA"]),
        )
        raiz2 = self.linea(
            self.productos["NUEVA2"],
            promocion_definicion=configuracion_promocion(self.productos["NUEVA2"]),
        )
        grupos1 = list(raiz1.promocion_definicion.grupos.all())
        grupo2 = raiz2.promocion_definicion.grupos.get()
        self.assertEqual(
            validar_cupo_componente(raiz1, self.productos["TACO"], Decimal("2"), grupo_id=grupos1[0].id),
            grupos1[0],
        )
        self.linea(
            self.productos["TACO"], cantidad=Decimal("2"),
            promocion_aplicada=raiz1, promocion_grupo=grupos1[0],
        )
        self.linea(
            self.productos["AGUA"], promocion_aplicada=raiz1,
            promocion_grupo=grupos1[1],
        )
        self.linea(
            self.productos["TACO"], promocion_aplicada=raiz2,
            promocion_grupo=grupo2,
        )
        validar_promociones(self.ticket)
        with self.assertRaisesMessage(ValueError, "sólo admite 0"):
            validar_cupo_componente(raiz2, self.productos["TACO"], Decimal("1"), grupo_id=grupo2.id)
        self.assertEqual(
            self.ticket.partidas.filter(producto=self.productos["TACO"], promocion_aplicada=raiz1).count(), 1
        )
        self.assertEqual(
            self.ticket.partidas.filter(producto=self.productos["TACO"], promocion_aplicada=raiz2).count(), 1
        )

    def test_rechaza_inactivos_no_disponibles_recursion_y_precio_divergente(self):
        for mutacion in ("precio", "disponibilidad", "recursion"):
            regla = copy.deepcopy(self.regla)
            productos = copy.deepcopy(self.snapshot_productos)
            if mutacion == "precio":
                regla["precio"] = "80.00"
            elif mutacion == "disponibilidad":
                productos[self.ids["AGUA"]]["disponible_sucursal"] = False
            else:
                regla["grupos"][0]["productos_permitidos"] = [str(self.ids["NUEVA"])]
            with self.subTest(mutacion=mutacion), self.assertRaises(ValueError):
                validar_promociones_publicadas([regla], productos)


class PromocionesLegadoEquivalenciaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("aprovisionar_sucursal", clave="ARBOLEDAS", nombre="Arboledas", verbosity=0)
        call_command("cargar_datos_iniciales", verbosity=0)

    def test_publicacion_v2_preserva_backfill_hasta_primera_v3(self):
        sucursal = Sucursal.objects.get(clave="ARBOLEDAS")
        promocion = Producto.objects.get(sucursal=sucursal, codigo="PB")
        legado = configuracion_promocion(promocion)
        self.assertIsNotNone(legado)
        PublicacionCatalogoCentral.objects.create(
            sucursal=sucursal, release_id=uuid.uuid4(), publicacion_id=uuid.uuid4(),
            version=1, version_contrato=2,
            estado=PublicacionCatalogoCentral.Estado.APLICADA, checksum="0" * 64,
        )
        self.assertEqual(configuracion_promocion(promocion).id, legado.id)
        PublicacionCatalogoCentral.objects.create(
            sucursal=sucursal, release_id=uuid.uuid4(), publicacion_id=uuid.uuid4(),
            version=2, version_contrato=3,
            estado=PublicacionCatalogoCentral.Estado.APLICADA, checksum="0" * 64,
        )
        # Una publicación v3 sin PB la retiró para nuevas capturas; una raíz
        # histórica seguiría apuntando a `legado` mediante promocion_definicion.
        self.assertIsNone(configuracion_promocion(promocion))

    def test_pb_pl_p4_pk_son_datos_y_productos_permitidos_equivalentes(self):
        sucursal = Sucursal.objects.get(clave="ARBOLEDAS")
        esperados = {
            "PB": ({0, 3}, [(3, {"TB"}), (1, None)]),
            "PL": ({1, 2}, [(1, {"LB", "LOBQ"}), (1, {"TB"})]),
            "P4": ({1, 2}, [(4, {"TB"})]),
            "PK": ({0, 1, 2, 3, 4}, [(3, {"TBI"})]),
        }
        for codigo, (dias, grupos) in esperados.items():
            with self.subTest(codigo=codigo):
                producto = Producto.objects.get(sucursal=sucursal, codigo=codigo)
                definicion = configuracion_promocion(producto)
                self.assertIsNotNone(definicion)
                self.assertEqual(set(definicion.dias_semana), dias)
                self.assertEqual(definicion.precio, producto.precio_actual().importe)
                for grupo, (cantidad, codigos) in zip(definicion.grupos.all(), grupos):
                    self.assertEqual(grupo.cantidad, cantidad)
                    permitidos = set(grupo.permitidos.values_list("producto__codigo", flat=True))
                    if codigos is None:
                        self.assertIn("CC", permitidos)
                        self.assertIn("HR05", permitidos)
                        self.assertNotIn("HR1", permitidos)
                    else:
                        self.assertEqual(permitidos, codigos)
