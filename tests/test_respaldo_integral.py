"""H18: ida y vuelta del respaldo integral en carpetas de laboratorio."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from herramientas import respaldo_integral as h18


class RespaldoIntegralTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "edge-origen"
        self.source.mkdir()
        (self.source / "runtime").mkdir()
        (self.source / "certs").mkdir()
        (self.source / "media").mkdir()
        (self.source / "runtime" / "purgas-pendientes").mkdir()
        (self.source / "runtime" / "purgas-pendientes" / "purga-lab.json").write_text(
            '{"estado":"pendiente"}', encoding="utf-8"
        )
        (self.source / "media" / "imagen.webp").write_bytes(b"imagen-lab")
        for name in ("central.pem", "orders.pem", "postgres.pem"):
            (self.source / "certs" / name).write_bytes(("CA-" + name).encode())
        (self.source / ".env").write_text(
            "DB_ENGINE=sqlite\n"
            "SQLITE_PATH=runtime/db.sqlite3\n"
            "SUCURSAL_CLAVE=ARBOLEDAS\n"
            "CENTRAL_BRANCH_ID=11111111-1111-4111-8111-111111111111\n"
            "CENTRAL_POS_INSTANCE_ID=23232323-2323-4232-8232-232323232323\n"
            "CENTRAL_CATALOG_TOKEN=secreto-de-laboratorio\n"
            "MODULOS_OPCIONALES=pedidos_sucursales\n"
            "PRINTER_CAJA_HOST=192.0.2.10\n"
            "CENTRAL_API_CA_BUNDLE=certs/central.pem\n"
            "PEDIDOS_API_CA_BUNDLE=certs/orders.pem\n"
            "PEDIDOS_SUCURSALES_DB_SSLROOTCERT=certs/postgres.pem\n",
            encoding="utf-8",
        )
        with sqlite3.connect(self.source / "runtime" / "db.sqlite3") as db:
            db.executescript(
                """
                PRAGMA foreign_keys=ON;
                CREATE TABLE personas_sucursal (id TEXT PRIMARY KEY, clave TEXT);
                CREATE TABLE ventas_eventooutbox (
                    id TEXT PRIMARY KEY, sucursal_id TEXT NOT NULL,
                    estado_entrega TEXT NOT NULL,
                    FOREIGN KEY(sucursal_id) REFERENCES personas_sucursal(id)
                );
                CREATE TABLE impresion_impresora (id TEXT PRIMARY KEY, host TEXT);
                CREATE TABLE impresion_terminal (id TEXT PRIMARY KEY, impresora_id TEXT);
                INSERT INTO personas_sucursal VALUES
                    ('11111111-1111-4111-8111-111111111111', 'ARBOLEDAS');
                INSERT INTO ventas_eventooutbox VALUES
                    ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
                     '11111111-1111-4111-8111-111111111111', 'pendiente');
                INSERT INTO impresion_impresora VALUES ('p1', '192.0.2.10');
                INSERT INTO impresion_terminal VALUES ('t1', 'p1');
                """
            )
        self.output = self.base / "externo-ntfs"
        self.output.mkdir()
        self.target = self.base / "edge-aislado"
        self.target.mkdir()
        (self.target / "manage.py").write_text("# release aislada\n", encoding="utf-8")
        (self.target / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        (self.target / ".h18-restauracion-aislada").write_text(h18.MARKER, encoding="utf-8")

    def test_paquete_integral_restaurable_con_outbox_identidad_y_trust(self):
        created = h18.create(self.source, self.output)
        bundle = Path(created["bundle"])
        manifest = h18.verify(bundle)
        manifest_text = (bundle / "manifest.json").read_text(encoding="utf-8")
        self.assertNotIn("secreto-de-laboratorio", manifest_text)
        self.assertEqual(manifest["sqlite"]["integrity_check"], "ok")
        self.assertEqual(manifest["sqlite"]["foreign_key_errors"], 0)
        self.assertEqual(manifest["sqlite"]["table_counts"]["ventas_eventooutbox"], 1)

        # El origen cambia después del backup; restore usa sólo la copia externa.
        with sqlite3.connect(self.source / "runtime" / "db.sqlite3") as db:
            db.execute("DELETE FROM ventas_eventooutbox")
        (self.source / ".env").write_text("DB_ENGINE=sqlite\n", encoding="utf-8")
        restored = h18.restore(bundle, self.target, self.source)
        self.assertEqual(restored["status"], "ok")
        with sqlite3.connect(self.target / "runtime" / "db.sqlite3") as db:
            row = db.execute(
                "SELECT estado_entrega FROM ventas_eventooutbox"
            ).fetchone()
        self.assertEqual(row, ("pendiente",))
        env = (self.target / ".env").read_text(encoding="utf-8")
        self.assertIn("CENTRAL_CATALOG_TOKEN=secreto-de-laboratorio", env)
        self.assertIn("SUCURSAL_CLAVE=ARBOLEDAS", env)
        self.assertIn("MODULOS_OPCIONALES=pedidos_sucursales", env)
        self.assertIn("PRINTER_CAJA_HOST=192.0.2.10", env)
        self.assertIn("CENTRAL_API_CA_BUNDLE=certs/h18/CENTRAL_API_CA_BUNDLE.pem", env)
        self.assertEqual(
            (self.target / "certs" / "h18" / "CENTRAL_API_CA_BUNDLE.pem").read_bytes(),
            b"CA-central.pem",
        )
        self.assertEqual(
            (self.target / "runtime" / "purgas-pendientes" / "purga-lab.json").read_text(),
            '{"estado":"pendiente"}',
        )
        self.assertEqual((self.target / "media" / "imagen.webp").read_bytes(), b"imagen-lab")

    def test_tamper_rechazado_antes_de_modificar_destino(self):
        bundle = Path(h18.create(self.source, self.output)["bundle"])
        (bundle / ".env").write_text("DB_ENGINE=sqlite\n", encoding="utf-8")
        with self.assertRaises(h18.BackupIntegralError):
            h18.restore(bundle, self.target, self.source)
        self.assertFalse((self.target / "runtime").exists())

    def test_restore_no_sobrescribe_instalacion_ni_origen(self):
        bundle = Path(h18.create(self.source, self.output)["bundle"])
        (self.target / ".env").write_text("SECRETO=otro\n", encoding="utf-8")
        with self.assertRaises(h18.BackupIntegralError):
            h18.restore(bundle, self.target, self.source)
        with self.assertRaises(h18.BackupIntegralError):
            h18.restore(bundle, self.source, self.source)
        self.assertEqual(
            (self.target / ".env").read_text(encoding="utf-8"), "SECRETO=otro\n"
        )

    def test_enlace_en_media_rechazado(self):
        outside = self.base / "afuera.txt"
        outside.write_text("no incluir", encoding="utf-8")
        link = self.source / "media" / "enlace.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("El usuario Windows no puede crear symlinks.")
        with self.assertRaises(h18.BackupIntegralError):
            h18.create(self.source, self.output)


if __name__ == "__main__":
    unittest.main()
