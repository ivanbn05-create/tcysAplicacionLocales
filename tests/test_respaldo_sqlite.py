import datetime as dt
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from herramientas.respaldo_sqlite import BackupError, realizar_respaldo


class RespaldoSqliteTests(unittest.TestCase):
    def test_respaldo_consistente_verificado_y_con_retencion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "runtime" / "db.sqlite3"
            backup_root = root / "backups"
            log_path = root / "logs" / "sqlite-backup.log"
            db_path.parent.mkdir()

            connection = sqlite3.connect(db_path)
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("CREATE TABLE venta (id INTEGER PRIMARY KEY, total INTEGER NOT NULL)")
                connection.execute("INSERT INTO venta(total) VALUES (100)")
                connection.commit()
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("INSERT INTO venta(total) VALUES (999)")

                old_backup = backup_root / "db-20000101-000000.sqlite3"
                backup_root.mkdir()
                sqlite3.connect(old_backup).close()
                old_sha = Path(str(old_backup) + ".sha256")
                old_sha.write_text("old  db-20000101-000000.sqlite3\n", encoding="ascii")
                old_time = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=10)).timestamp()
                os.utime(old_backup, (old_time, old_time))
                os.utime(old_sha, (old_time, old_time))

                metadata = realizar_respaldo(
                    database_path=db_path,
                    backup_root=backup_root,
                    log_path=log_path,
                    retention_days=1,
                    now=dt.datetime.now(dt.timezone.utc),
                )
            finally:
                connection.rollback()
                connection.close()

            self.assertEqual(metadata["status"], "ok")
            backup_path = Path(metadata["backup"])
            self.assertTrue(backup_path.is_file())
            self.assertTrue(Path(str(backup_path) + ".sha256").is_file())
            self.assertTrue(Path(str(backup_path) + ".json").is_file())
            self.assertFalse(old_backup.exists())
            self.assertFalse(old_sha.exists())

            with closing(sqlite3.connect(backup_path)) as backup:
                rows = backup.execute("SELECT total FROM venta ORDER BY id").fetchall()
            self.assertEqual(rows, [(100,)])
            self.assertEqual(metadata["sqlite_backup_check"]["integrity_check"], "ok")
            self.assertEqual(metadata["restore_check"]["integrity_check"], "ok")

            log_records = [
                json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(log_records[-1]["status"], "ok")
            self.assertEqual(log_records[-1]["backup_file"], backup_path.name)
            self.assertFalse(any(path.name.endswith(".tmp") for path in backup_root.iterdir()))

    def test_retencion_no_separa_un_triplete_por_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "runtime" / "db.sqlite3"
            backup_root = root / "backups"
            log_path = root / "logs" / "sqlite-backup.log"
            db_path.parent.mkdir()
            backup_root.mkdir()
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute("CREATE TABLE venta (id INTEGER PRIMARY KEY)")
                connection.commit()

            old_backup = backup_root / "db-20000101-000000.sqlite3"
            old_sha = Path(str(old_backup) + ".sha256")
            old_json = Path(str(old_backup) + ".json")
            sqlite3.connect(old_backup).close()
            old_sha.write_text("old", encoding="ascii")
            old_json.write_text("{}", encoding="utf-8")
            old_time = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=10)).timestamp()
            for path in (old_backup, old_sha):
                os.utime(path, (old_time, old_time))

            realizar_respaldo(
                database_path=db_path,
                backup_root=backup_root,
                log_path=log_path,
                retention_days=1,
            )

            self.assertTrue(old_backup.exists())
            self.assertTrue(old_sha.exists())
            self.assertTrue(old_json.exists())

    def test_triplete_ya_es_completo_si_falla_la_retencion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "runtime" / "db.sqlite3"
            backup_root = root / "backups"
            log_path = root / "logs" / "sqlite-backup.log"
            db_path.parent.mkdir()
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute("CREATE TABLE venta (id INTEGER PRIMARY KEY)")
                connection.commit()

            with mock.patch(
                "herramientas.respaldo_sqlite._aplicar_retencion",
                side_effect=BackupError("fallo de retencion simulado"),
            ):
                with self.assertRaisesRegex(BackupError, "fallo de retencion"):
                    realizar_respaldo(
                        database_path=db_path,
                        backup_root=backup_root,
                        log_path=log_path,
                        retention_days=1,
                    )

            backups = list(backup_root.glob("db-*.sqlite3"))
            self.assertEqual(len(backups), 1)
            self.assertTrue(Path(str(backups[0]) + ".sha256").is_file())
            metadata_path = Path(str(backups[0]) + ".json")
            self.assertTrue(metadata_path.is_file())
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["status"], "ok")
            self.assertEqual(metadata["backup_file"], backups[0].name)

    def test_fallo_del_log_no_oculta_la_causa_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch(
                "herramientas.respaldo_sqlite._append_json_log",
                side_effect=OSError("log no disponible"),
            ):
                with self.assertRaises(BackupError) as raised:
                    realizar_respaldo(
                        database_path=root / "runtime" / "inexistente.sqlite3",
                        backup_root=root / "backups",
                        log_path=root / "logs" / "sqlite-backup.log",
                        retention_days=1,
                    )
            self.assertIn("No existe la base SQLite esperada", str(raised.exception))
            self.assertTrue(
                any("log no disponible" in note for note in raised.exception.__notes__)
            )


if __name__ == "__main__":
    unittest.main()
