import datetime as dt
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from herramientas.respaldo_sqlite import realizar_respaldo


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


if __name__ == "__main__":
    unittest.main()
