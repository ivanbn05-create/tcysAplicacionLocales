"""H18: ida y vuelta del respaldo integral en carpetas de laboratorio."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from herramientas import respaldo_integral as h18


class RespaldoIntegralTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "edge-origen"
        self.source.mkdir()
        (self.source / "VERSION").write_text("1.0.0\n", encoding="utf-8")
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
        db.close()
        public_xml = "<RSAKeyValue><Modulus>QUJD</Modulus><Exponent>AQAB</Exponent></RSAKeyValue>"
        self.trust_source = self.base / "release-trust-origen.json"
        self.trust_source.write_text(json.dumps({
            "schema_version": 1,
            "keys": [{
                "key_id": hashlib.sha256(public_xml.encode("utf-8")).hexdigest(),
                "public_xml": public_xml,
                "status": "trusted",
            }],
        }), encoding="utf-8")
        self.trust_parent = self.base / "trust-restaurado"
        self.trust_parent.mkdir()
        self.trust_dest = self.trust_parent / "release-trust.json"
        self.output = self.base / "externo-ntfs"
        self.output.mkdir()
        self.target = self.base / "edge-aislado"
        self.target.mkdir()
        (self.target / "manage.py").write_text("# release aislada\n", encoding="utf-8")
        (self.target / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        (self.target / ".h18-restauracion-aislada").write_text(h18.MARKER, encoding="utf-8")

    def test_paquete_integral_restaurable_con_outbox_identidad_y_trust(self):
        created = h18.create(self.source, self.output, self.trust_source)
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
        db.close()
        (self.source / ".env").write_text("DB_ENGINE=sqlite\n", encoding="utf-8")
        self.source.rename(self.base / "edge-origen-perdido")
        restored = h18.restore(bundle, self.target, self.trust_dest)
        self.assertEqual(restored["status"], "ok")
        with sqlite3.connect(self.target / "runtime" / "db.sqlite3") as db:
            row = db.execute(
                "SELECT estado_entrega FROM ventas_eventooutbox"
            ).fetchone()
        db.close()
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
        self.assertEqual(self.trust_dest.read_bytes(), self.trust_source.read_bytes())
        self.assertFalse((self.target / "trust" / "release-trust.json").exists())
        self.assertEqual(manifest["release_trust"], h18.RELEASE_TRUST_REL)

    def test_trust_store_obligatorio_y_solo_claves_publicas(self):
        with self.assertRaises(h18.BackupIntegralError):
            h18.create(self.source, self.output)
        self.trust_source.unlink()
        with self.assertRaises(h18.BackupIntegralError):
            h18.create(self.source, self.output, self.trust_source)
        private_xml = (
            "<RSAKeyValue><Modulus>QUJD</Modulus><Exponent>AQAB</Exponent><D>QUJD</D></RSAKeyValue>"
        )
        self.trust_source.write_text(json.dumps({
            "schema_version": 1,
            "keys": [{
                "key_id": hashlib.sha256(private_xml.encode("utf-8")).hexdigest(),
                "public_xml": private_xml,
                "status": "trusted",
            }],
        }), encoding="utf-8")
        with self.assertRaises(h18.BackupIntegralError):
            h18.create(self.source, self.output, self.trust_source)

    def test_restore_exige_destino_externo_nuevo_para_trust_store(self):
        bundle = Path(h18.create(self.source, self.output, self.trust_source)["bundle"])
        with self.assertRaises(h18.BackupIntegralError):
            h18.restore(bundle, self.target)
        self.assertFalse((self.target / ".env").exists())
        with self.assertRaises(h18.BackupIntegralError):
            h18.restore(bundle, self.target, self.target / "release-trust.json")
        self.assertFalse((self.target / ".env").exists())

    def test_mutacion_despues_de_verify_rechaza_env_y_ca_sin_publicar(self):
        original_verify = h18.verify
        for relative, altered in (
            (".env", b"DB_ENGINE=sqlite\nSQLITE_PATH=runtime/db.sqlite3\n"),
            ("trust/CENTRAL_API_CA_BUNDLE.pem", b"CA-MODIFICADA"),
        ):
            with self.subTest(relative=relative):
                bundle = Path(h18.create(self.source, self.output, self.trust_source)["bundle"])

                def mutate_after_verify(path):
                    manifest = original_verify(path)
                    (path / relative).write_bytes(altered)
                    return manifest

                with patch.object(h18, "verify", side_effect=mutate_after_verify):
                    with self.assertRaises(h18.BackupIntegralError):
                        h18.restore(bundle, self.target, self.trust_dest)
                self.assertFalse((self.target / ".env").exists())
                self.assertFalse((self.target / "runtime").exists())
                self.assertFalse(self.trust_dest.exists())

    def test_tamper_rechazado_antes_de_modificar_destino(self):
        bundle = Path(h18.create(self.source, self.output, self.trust_source)["bundle"])
        (bundle / ".env").write_text("DB_ENGINE=sqlite\n", encoding="utf-8")
        with self.assertRaises(h18.BackupIntegralError):
            h18.restore(bundle, self.target, self.trust_dest)
        self.assertFalse((self.target / "runtime").exists())

    def test_restore_no_sobrescribe_instalacion_ni_origen(self):
        bundle = Path(h18.create(self.source, self.output, self.trust_source)["bundle"])
        (self.target / ".env").write_text("SECRETO=otro\n", encoding="utf-8")
        with self.assertRaises(h18.BackupIntegralError):
            h18.restore(bundle, self.target, self.trust_dest)
        with self.assertRaises(h18.BackupIntegralError):
            h18.restore(bundle, self.source)
        self.assertEqual(
            (self.target / ".env").read_text(encoding="utf-8"), "SECRETO=otro\n"
        )

    def test_version_de_release_distinta_bloquea_restore(self):
        bundle = Path(h18.create(self.source, self.output, self.trust_source)["bundle"])
        (self.target / "VERSION").write_text("1.0.1\n", encoding="utf-8")
        with self.assertRaises(h18.BackupIntegralError):
            h18.restore(bundle, self.target, self.trust_dest)
        self.assertFalse((self.target / ".env").exists())

    def test_enlace_en_media_rechazado(self):
        outside = self.base / "afuera.txt"
        outside.write_text("no incluir", encoding="utf-8")
        link = self.source / "media" / "enlace.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("El usuario Windows no puede crear symlinks.")
        with self.assertRaises(h18.BackupIntegralError):
            h18.create(self.source, self.output, self.trust_source)


if __name__ == "__main__":
    unittest.main()
