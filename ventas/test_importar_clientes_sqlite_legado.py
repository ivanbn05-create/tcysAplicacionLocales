import hashlib
import io
import sqlite3
import tempfile
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from personas.models import Sucursal
from ventas.models import (
    Cliente,
    ConsecutivoCliente,
    DomicilioCliente,
    TelefonoCliente,
    Ticket,
)


class ImportarClientesSqliteLegadoTests(TestCase):
    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory()
        self.ruta = Path(self.temporal.name) / "clientes-legado.sqlite3"
        self.sucursal = Sucursal.objects.create(
            clave="ARBOLEDAS", nombre="Arboledas destino"
        )
        self.sucursal_origen_id = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        self.cliente_uno_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
        self.cliente_dos_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
        self.telefono_id = uuid.UUID("33333333-3333-3333-3333-333333333333")
        self.domicilio_id = uuid.UUID("44444444-4444-4444-4444-444444444444")
        self._crear_fuente()

    def tearDown(self):
        self.temporal.cleanup()

    def _crear_fuente(self):
        with closing(sqlite3.connect(self.ruta)) as origen:
            origen.executescript(
                """
                CREATE TABLE personas_sucursal (
                    id TEXT PRIMARY KEY,
                    clave TEXT NOT NULL
                );
                CREATE TABLE ventas_cliente (
                    id TEXT PRIMARY KEY,
                    sucursal_id TEXT NOT NULL,
                    clave_corta TEXT NOT NULL,
                    nombre TEXT NOT NULL,
                    nombre_normalizado TEXT NOT NULL,
                    notas TEXT NOT NULL,
                    comentarios_multiples INTEGER NOT NULL,
                    activo INTEGER NOT NULL,
                    creado_en TEXT NOT NULL,
                    actualizado_en TEXT NOT NULL
                );
                CREATE TABLE ventas_telefonocliente (
                    id TEXT PRIMARY KEY,
                    sucursal_id TEXT NOT NULL,
                    cliente_id TEXT NOT NULL,
                    numero TEXT NOT NULL,
                    normalizado TEXT NOT NULL,
                    etiqueta TEXT NOT NULL,
                    principal INTEGER NOT NULL,
                    activo INTEGER NOT NULL,
                    creado_en TEXT NOT NULL
                );
                CREATE TABLE ventas_domiciliocliente (
                    id TEXT PRIMARY KEY,
                    sucursal_id TEXT NOT NULL,
                    cliente_id TEXT NOT NULL,
                    etiqueta TEXT NOT NULL,
                    calle TEXT NOT NULL,
                    numero_exterior TEXT NOT NULL,
                    numero_interior TEXT NOT NULL,
                    colonia TEXT NOT NULL,
                    codigo_postal TEXT NOT NULL,
                    municipio TEXT NOT NULL,
                    referencia TEXT NOT NULL,
                    normalizado TEXT NOT NULL,
                    principal INTEGER NOT NULL,
                    activo INTEGER NOT NULL,
                    creado_en TEXT NOT NULL
                );
                CREATE TABLE ventas_consecutivocliente (
                    sucursal_id TEXT PRIMARY KEY,
                    ultimo INTEGER NOT NULL
                );
                CREATE TABLE ventas_ticket (
                    id TEXT PRIMARY KEY,
                    cliente_id TEXT,
                    total TEXT
                );
                """
            )
            sucursal_id = self.sucursal_origen_id.hex
            origen.execute(
                "INSERT INTO personas_sucursal (id, clave) VALUES (?, ?)",
                (sucursal_id, "ARBOLEDAS"),
            )
            origen.executemany(
                """
                INSERT INTO ventas_cliente (
                    id, sucursal_id, clave_corta, nombre, nombre_normalizado,
                    notas, comentarios_multiples, activo, creado_en, actualizado_en
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        self.cliente_uno_id.hex,
                        sucursal_id,
                        "100101",
                        "Cliente legado uno",
                        "cliente legado uno",
                        "Nota original\nsegunda linea",
                        1,
                        1,
                        "2026-08-01 12:34:56.123456",
                        "2026-09-02 13:45:01.654321",
                    ),
                    (
                        self.cliente_dos_id.hex,
                        sucursal_id,
                        "100107",
                        "Cliente legado inactivo",
                        "cliente legado inactivo",
                        "",
                        0,
                        0,
                        "2026-08-03 08:00:00",
                        "2026-08-04 09:00:00",
                    ),
                ],
            )
            origen.execute(
                """
                INSERT INTO ventas_telefonocliente (
                    id, sucursal_id, cliente_id, numero, normalizado, etiqueta,
                    principal, activo, creado_en
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.telefono_id.hex,
                    sucursal_id,
                    self.cliente_uno_id.hex,
                    "33 1234 5678",
                    "3312345678",
                    "Celular legado",
                    1,
                    1,
                    "2026-08-01 12:35:00",
                ),
            )
            origen.execute(
                """
                INSERT INTO ventas_domiciliocliente (
                    id, sucursal_id, cliente_id, etiqueta, calle, numero_exterior,
                    numero_interior, colonia, codigo_postal, municipio, referencia,
                    normalizado, principal, activo, creado_en
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.domicilio_id.hex,
                    sucursal_id,
                    self.cliente_uno_id.hex,
                    "Principal legado",
                    "Avenida Ejemplo",
                    "321",
                    "INT 4",
                    "Centro",
                    "45000",
                    "Zapopan",
                    "Porton azul",
                    "avenida ejemplo 321 int 4 centro 45000 zapopan porton azul",
                    1,
                    1,
                    "2026-08-01 12:36:00",
                ),
            )
            origen.execute(
                "INSERT INTO ventas_consecutivocliente (sucursal_id, ultimo) VALUES (?, ?)",
                (sucursal_id, 100109),
            )
            origen.execute(
                "INSERT INTO ventas_ticket (id, cliente_id, total) VALUES (?, ?, ?)",
                (uuid.uuid4().hex, self.cliente_uno_id.hex, "999.99"),
            )
            origen.commit()

    def _ejecutar(self, *argumentos, **opciones):
        salida = io.StringIO()
        call_command(
            "importar_clientes_sqlite_legado",
            self.ruta,
            "--sucursal",
            "ARBOLEDAS",
            *argumentos,
            stdout=salida,
            **opciones,
        )
        return salida.getvalue()

    def test_dry_run_valida_y_cuenta_sin_escribir(self):
        hash_antes = hashlib.sha256(self.ruta.read_bytes()).hexdigest()

        salida = self._ejecutar("--dry-run")

        self.assertIn("se importarian 2 clientes, 1 telefonos y 1 domicilios", salida)
        self.assertFalse(Cliente.objects.exists())
        self.assertFalse(TelefonoCliente.objects.exists())
        self.assertFalse(DomicilioCliente.objects.exists())
        self.assertFalse(ConsecutivoCliente.objects.exists())
        self.assertEqual(
            hashlib.sha256(self.ruta.read_bytes()).hexdigest(),
            hash_antes,
        )

    def test_importa_exactamente_y_no_copia_tickets_ni_ventas(self):
        salida = self._ejecutar()

        self.assertIn("Importados 2 clientes, 1 telefonos y 1 domicilios", salida)
        cliente = Cliente.objects.get(pk=self.cliente_uno_id)
        self.assertEqual(cliente.sucursal, self.sucursal)
        self.assertEqual(cliente.clave_corta, "100101")
        self.assertEqual(cliente.nombre, "Cliente legado uno")
        self.assertEqual(cliente.nombre_normalizado, "cliente legado uno")
        self.assertEqual(cliente.notas, "Nota original\nsegunda linea")
        self.assertTrue(cliente.comentarios_multiples)
        self.assertTrue(cliente.activo)
        self.assertEqual(
            cliente.creado_en,
            datetime(2026, 8, 1, 12, 34, 56, 123456, tzinfo=timezone.utc),
        )
        self.assertEqual(
            cliente.actualizado_en,
            datetime(2026, 9, 2, 13, 45, 1, 654321, tzinfo=timezone.utc),
        )
        inactivo = Cliente.objects.get(pk=self.cliente_dos_id)
        self.assertFalse(inactivo.activo)

        telefono = TelefonoCliente.objects.get(pk=self.telefono_id)
        self.assertEqual(telefono.sucursal, self.sucursal)
        self.assertEqual(telefono.cliente, cliente)
        self.assertEqual(telefono.numero, "33 1234 5678")
        self.assertEqual(telefono.normalizado, "3312345678")
        self.assertEqual(telefono.etiqueta, "Celular legado")
        self.assertEqual(
            telefono.creado_en,
            datetime(2026, 8, 1, 12, 35, tzinfo=timezone.utc),
        )

        domicilio = DomicilioCliente.objects.get(pk=self.domicilio_id)
        self.assertEqual(domicilio.sucursal, self.sucursal)
        self.assertEqual(domicilio.cliente, cliente)
        self.assertEqual(domicilio.calle, "Avenida Ejemplo")
        self.assertEqual(domicilio.numero_exterior, "321")
        self.assertEqual(domicilio.numero_interior, "INT 4")
        self.assertEqual(domicilio.referencia, "Porton azul")
        self.assertEqual(
            domicilio.creado_en,
            datetime(2026, 8, 1, 12, 36, tzinfo=timezone.utc),
        )
        self.assertEqual(
            ConsecutivoCliente.objects.get(sucursal=self.sucursal).ultimo,
            100109,
        )
        self.assertFalse(Ticket.objects.exists())

    def test_segunda_ejecucion_identica_es_no_op(self):
        self._ejecutar()
        cliente = Cliente.objects.get(pk=self.cliente_uno_id)
        actualizado_antes = cliente.actualizado_en

        salida = self._ejecutar()

        self.assertIn("ya coincide", salida)
        self.assertIn("sin cambios", salida)
        self.assertEqual(Cliente.objects.count(), 2)
        self.assertEqual(TelefonoCliente.objects.count(), 1)
        self.assertEqual(DomicilioCliente.objects.count(), 1)
        cliente.refresh_from_db()
        self.assertEqual(cliente.actualizado_en, actualizado_antes)

    def test_destino_parcial_o_conflictivo_falla_sin_cambios(self):
        Cliente.objects.create(
            id=self.cliente_uno_id,
            sucursal=self.sucursal,
            clave_corta="100101",
            nombre="Dato distinto",
        )
        estado_antes = list(
            Cliente.objects.values_list("id", "clave_corta", "nombre")
        )

        with self.assertRaisesRegex(CommandError, "parcial o distinto"):
            self._ejecutar()

        self.assertEqual(
            list(Cliente.objects.values_list("id", "clave_corta", "nombre")),
            estado_antes,
        )
        self.assertFalse(TelefonoCliente.objects.exists())
        self.assertFalse(DomicilioCliente.objects.exists())
        self.assertFalse(ConsecutivoCliente.objects.exists())

    def test_rechaza_sqlite_corrupta_sin_escribir(self):
        corrupta = Path(self.temporal.name) / "corrupta.sqlite3"
        corrupta.write_bytes(b"esto no es una base SQLite")

        with self.assertRaisesRegex(CommandError, "modo de solo lectura"):
            call_command(
                "importar_clientes_sqlite_legado",
                corrupta,
                "--sucursal",
                "ARBOLEDAS",
            )

        self.assertFalse(Cliente.objects.exists())
        self.assertFalse(ConsecutivoCliente.objects.exists())
