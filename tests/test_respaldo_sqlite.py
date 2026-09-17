import datetime as dt
import json
import os
import sqlite3
import stat
import tempfile
import unittest
import uuid
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from herramientas.respaldo_sqlite import (
    BackupError,
    _procesar_solicitudes_purga,
    _ruta_media_segura,
    main,
    realizar_respaldo,
)


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


    def _crear_base_consolidada(self, db_path, *, con_detalle):
        sucursal_id = uuid.uuid4()
        consolidacion_id = uuid.uuid4()
        with closing(sqlite3.connect(db_path)) as connection:
            connection.executescript(
                """
                CREATE TABLE ventas_consolidacionmensual (
                    id TEXT PRIMARY KEY,
                    sucursal_id TEXT NOT NULL,
                    periodo DATE NOT NULL,
                    estado TEXT NOT NULL,
                    acuse_vps TEXT NOT NULL,
                    confirmado_en DATETIME,
                    purgado_en DATETIME,
                    ultimo_error TEXT NOT NULL
                );
                CREATE TABLE ventas_ticket (
                    id TEXT PRIMARY KEY,
                    sucursal_id TEXT NOT NULL,
                    estado TEXT NOT NULL,
                    creado_en DATETIME NOT NULL,
                    activado_programado_en DATETIME
                );
                CREATE TABLE ventas_movimientocaja (
                    id TEXT PRIMARY KEY,
                    sucursal_id TEXT NOT NULL,
                    creado_en DATETIME NOT NULL
                );
                CREATE TABLE ventas_controlefectivodia (
                    id TEXT PRIMARY KEY,
                    sucursal_id TEXT NOT NULL,
                    fecha DATE NOT NULL
                );
                CREATE TABLE ventas_cortecaja (
                    id TEXT PRIMARY KEY,
                    sucursal_id TEXT NOT NULL,
                    inicio DATETIME,
                    fin DATETIME NOT NULL,
                    detalle_eliminado_en DATETIME
                );
                CREATE TABLE ventas_reporteadministrativo (
                    id TEXT PRIMARY KEY,
                    sucursal_id TEXT NOT NULL,
                    creado_en DATETIME NOT NULL
                );
                CREATE TABLE ventas_cortesucursal (
                    id TEXT PRIMARY KEY,
                    sucursal_id TEXT NOT NULL,
                    creado_en DATETIME NOT NULL
                );
                CREATE TABLE ventas_liquidacionrepartidor (
                    id TEXT PRIMARY KEY,
                    sucursal_id TEXT NOT NULL,
                    creado_en DATETIME NOT NULL
                );
                CREATE TABLE impresion_trabajoimpresion (
                    id TEXT PRIMARY KEY,
                    sucursal_id TEXT NOT NULL,
                    creado_en DATETIME NOT NULL
                );
                """
            )
            connection.execute(
                """
                INSERT INTO ventas_consolidacionmensual
                    (id, sucursal_id, periodo, estado, acuse_vps,
                     confirmado_en, purgado_en, ultimo_error)
                VALUES (?, ?, '2025-01-01', 'confirmada', 'acuse-001',
                        '2025-02-01 12:00:00', '2025-02-01 12:01:00', '')
                """,
                (consolidacion_id.hex, sucursal_id.hex),
            )
            if con_detalle:
                connection.execute(
                    """
                    INSERT INTO ventas_ticket(
                        id, sucursal_id, estado, creado_en, activado_programado_en
                    )
                    VALUES (
                        ?, ?, 'pagado',
                        '2024-12-20 18:00:00',
                        '2025-01-15 18:00:00'
                    )
                    """,
                    (uuid.uuid4().hex, sucursal_id.hex),
                )
            connection.commit()
        return sucursal_id, consolidacion_id

    def _escribir_solicitud(
        self,
        request_root,
        sucursal_id,
        consolidacion_id,
        *,
        rutas_archivos=None,
    ):
        request_root.mkdir(parents=True)
        solicitud_id = uuid.uuid4()
        path = request_root / f"purga-{solicitud_id}.json"
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "id": str(solicitud_id),
                    "tipo": "respaldos_periodo",
                    "referencia_tipo": "consolidacion",
                    "referencia_id": str(consolidacion_id),
                    "sucursal_id": str(sucursal_id),
                    "periodo": "2025-01-01",
                    "rutas_archivos": sorted(set(rutas_archivos or [])),
                    "creada_en": "2025-02-01T12:02:00+00:00",
                    "intentos": 0,
                    "ultimo_error": "",
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def test_solicitud_mensual_conserva_backup_nuevo_y_elimina_triplete_con_detalle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "runtime" / "db.sqlite3"
            backup_root = root / "backups"
            log_path = root / "logs" / "sqlite-backup.log"
            request_root = root / "runtime" / "purgas-pendientes"
            media_root = root / "media"
            db_path.parent.mkdir()
            media_root.mkdir()
            sucursal_id, consolidacion_id = self._crear_base_consolidada(
                db_path,
                con_detalle=True,
            )
            previo = realizar_respaldo(
                database_path=db_path,
                backup_root=backup_root,
                log_path=log_path,
                retention_days=3650,
                # Simula un reloj/nombre futuro: el contenido debe decidir.
                now=dt.datetime(2030, 2, 1, 12, 0, tzinfo=dt.timezone.utc),
            )
            previo_paths = (
                Path(previo["backup"]),
                Path(previo["backup"] + ".sha256"),
                Path(previo["backup"] + ".json"),
            )
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute("DELETE FROM ventas_ticket")
                connection.commit()
            solicitud = self._escribir_solicitud(
                request_root,
                sucursal_id,
                consolidacion_id,
            )

            nuevo = realizar_respaldo(
                database_path=db_path,
                backup_root=backup_root,
                log_path=log_path,
                retention_days=3650,
                now=dt.datetime(2025, 2, 1, 12, 5, tzinfo=dt.timezone.utc),
                request_root=request_root,
                media_root=media_root,
            )

            self.assertFalse(solicitud.exists())
            self.assertTrue(Path(nuevo["backup"]).is_file())
            self.assertTrue(Path(nuevo["backup"] + ".sha256").is_file())
            self.assertTrue(Path(nuevo["backup"] + ".json").is_file())
            self.assertFalse(any(path.exists() for path in previo_paths))
            self.assertEqual(
                set(nuevo["physical_purge"]["respaldos_eliminados"]),
                {path.name for path in previo_paths},
            )
            with closing(sqlite3.connect(db_path)) as connection:
                estado, purgado_en = connection.execute(
                    "SELECT estado, purgado_en FROM ventas_consolidacionmensual"
                ).fetchone()
            self.assertEqual(estado, "purgada")
            self.assertTrue(purgado_en)

    def test_no_declara_completa_si_un_respaldo_anterior_no_es_triplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "runtime" / "db.sqlite3"
            backup_root = root / "backups"
            log_path = root / "logs" / "sqlite-backup.log"
            request_root = root / "runtime" / "purgas-pendientes"
            media_root = root / "media"
            db_path.parent.mkdir()
            backup_root.mkdir()
            media_root.mkdir()
            sucursal_id, consolidacion_id = self._crear_base_consolidada(
                db_path,
                con_detalle=False,
            )
            incompleto = backup_root / "db-20250201-110000.sqlite3"
            with closing(sqlite3.connect(db_path)) as origen:
                with closing(sqlite3.connect(incompleto)) as destino:
                    origen.backup(destino)
            solicitud = self._escribir_solicitud(
                request_root,
                sucursal_id,
                consolidacion_id,
            )

            with self.assertRaisesRegex(BackupError, "solicitudes de purga física"):
                realizar_respaldo(
                    database_path=db_path,
                    backup_root=backup_root,
                    log_path=log_path,
                    retention_days=3650,
                    now=dt.datetime(2025, 2, 1, 12, 5, tzinfo=dt.timezone.utc),
                    request_root=request_root,
                    media_root=media_root,
                )

            self.assertTrue(incompleto.exists())
            self.assertTrue(solicitud.exists())
            pendiente = json.loads(solicitud.read_text(encoding="utf-8"))
            self.assertEqual(pendiente["intentos"], 1)
            with closing(sqlite3.connect(db_path)) as connection:
                estado = connection.execute(
                    "SELECT estado FROM ventas_consolidacionmensual"
                ).fetchone()[0]
            self.assertEqual(estado, "confirmada")

            artefactos_tras_fallo = {
                path.name for path in backup_root.iterdir()
            }
            for _ in range(2):
                resultado = _procesar_solicitudes_purga(
                    database_path=db_path,
                    backup_root=backup_root,
                    nuevo_backup=None,
                    request_root=request_root,
                    media_root=media_root,
                    timeout=30.0,
                )
                self.assertTrue(resultado["solicitudes_pendientes"])
                self.assertTrue(resultado["errores"])
                self.assertFalse(resultado["requiere_respaldo"])
                self.assertEqual(
                    {path.name for path in backup_root.iterdir()},
                    artefactos_tras_fallo,
                )

    def test_reporte_administrativo_huerfano_obliga_a_depurar_respaldo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "runtime" / "db.sqlite3"
            backup_root = root / "backups"
            log_path = root / "logs" / "sqlite-backup.log"
            request_root = root / "runtime" / "purgas-pendientes"
            media_root = root / "media"
            db_path.parent.mkdir()
            media_root.mkdir()
            sucursal_id, consolidacion_id = self._crear_base_consolidada(
                db_path,
                con_detalle=False,
            )
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute(
                    """
                    INSERT INTO ventas_reporteadministrativo(
                        id, sucursal_id, creado_en
                    ) VALUES (?, ?, '2025-01-20 18:00:00')
                    """,
                    (uuid.uuid4().hex, sucursal_id.hex),
                )
                connection.commit()
            previo = realizar_respaldo(
                database_path=db_path,
                backup_root=backup_root,
                log_path=log_path,
                retention_days=3650,
                now=dt.datetime(2031, 1, 1, tzinfo=dt.timezone.utc),
            )
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute("DELETE FROM ventas_reporteadministrativo")
                connection.commit()
            self._escribir_solicitud(
                request_root,
                sucursal_id,
                consolidacion_id,
            )

            realizar_respaldo(
                database_path=db_path,
                backup_root=backup_root,
                log_path=log_path,
                retention_days=3650,
                now=dt.datetime(2025, 2, 1, 13, 0, tzinfo=dt.timezone.utc),
                request_root=request_root,
                media_root=media_root,
            )

            self.assertFalse(Path(previo["backup"]).exists())
            self.assertFalse(Path(previo["backup"] + ".sha256").exists())
            self.assertFalse(Path(previo["backup"] + ".json").exists())

    def test_preflight_reporta_pendiente_con_error_y_codigo_no_cero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "db.sqlite3"
            backup_root = root / "backups"
            request_root = root / "requests"
            media_root = root / "media"
            request_root.mkdir()
            media_root.mkdir()
            (request_root / f"purga-{uuid.uuid4()}.json").write_text(
                "{json truncado",
                encoding="utf-8",
            )
            salida = StringIO()
            with redirect_stdout(salida):
                codigo = main(
                    [
                        "--database",
                        str(db_path),
                        "--backup-root",
                        str(backup_root),
                        "--log",
                        str(root / "backup.log"),
                        "--retention-days",
                        "30",
                        "--request-root",
                        str(request_root),
                        "--media-root",
                        str(media_root),
                        "--process-pending-only",
                    ]
                )
            resultado = json.loads(salida.getvalue())
            self.assertEqual(codigo, 2)
            self.assertEqual(resultado["status"], "pendientes_con_error")
            self.assertTrue(resultado["solicitudes_pendientes"])
            self.assertTrue(resultado["errores"])

    def test_solicitud_corrupta_ajena_no_bloquea_otra_consolidacion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "runtime" / "db.sqlite3"
            backup_root = root / "backups"
            log_path = root / "logs" / "sqlite-backup.log"
            request_root = root / "runtime" / "purgas-pendientes"
            media_root = root / "media"
            db_path.parent.mkdir()
            media_root.mkdir()
            sucursal_id, consolidacion_id = self._crear_base_consolidada(
                db_path,
                con_detalle=False,
            )
            solicitud = self._escribir_solicitud(
                request_root,
                sucursal_id,
                consolidacion_id,
            )
            corrupta = request_root / f"purga-{uuid.uuid4()}.json"
            corrupta.write_text("{json truncado", encoding="utf-8")

            with self.assertRaisesRegex(BackupError, "purga física"):
                realizar_respaldo(
                    database_path=db_path,
                    backup_root=backup_root,
                    log_path=log_path,
                    retention_days=30,
                    request_root=request_root,
                    media_root=media_root,
                )

            self.assertFalse(solicitud.exists())
            self.assertTrue(corrupta.exists())
            with closing(sqlite3.connect(db_path)) as connection:
                estado = connection.execute(
                    "SELECT estado FROM ventas_consolidacionmensual"
                ).fetchone()[0]
            self.assertEqual(estado, "purgada")
            self.assertEqual(len(list(backup_root.glob("db-*.sqlite3"))), 1)

    def test_archivos_esperados_bloquean_purgada_aunque_falte_solicitud_hermana(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "runtime" / "db.sqlite3"
            backup_root = root / "backups"
            log_path = root / "logs" / "sqlite-backup.log"
            request_root = root / "runtime" / "purgas-pendientes"
            media_root = root / "media"
            archivo = media_root / "impresion" / "cuenta.png"
            db_path.parent.mkdir()
            archivo.parent.mkdir(parents=True)
            archivo.write_bytes(b"detalle sensible")
            sucursal_id, consolidacion_id = self._crear_base_consolidada(
                db_path,
                con_detalle=False,
            )
            solicitud = self._escribir_solicitud(
                request_root,
                sucursal_id,
                consolidacion_id,
                rutas_archivos=["impresion/cuenta.png"],
            )

            with self.assertRaisesRegex(BackupError, "purga física"):
                realizar_respaldo(
                    database_path=db_path,
                    backup_root=backup_root,
                    log_path=log_path,
                    retention_days=30,
                    request_root=request_root,
                    media_root=media_root,
                )
            self.assertTrue(solicitud.exists())
            with closing(sqlite3.connect(db_path)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT estado FROM ventas_consolidacionmensual"
                    ).fetchone()[0],
                    "confirmada",
                )

            archivo.unlink()
            realizar_respaldo(
                database_path=db_path,
                backup_root=backup_root,
                log_path=log_path,
                retention_days=30,
                request_root=request_root,
                media_root=media_root,
            )
            self.assertFalse(solicitud.exists())
            with closing(sqlite3.connect(db_path)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT estado FROM ventas_consolidacionmensual"
                    ).fetchone()[0],
                    "purgada",
                )

    def test_reintentos_conservan_asignado_y_retencion_acota_tripletes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "runtime" / "db.sqlite3"
            backup_root = root / "backups"
            log_path = root / "logs" / "sqlite-backup.log"
            request_root = root / "runtime" / "purgas-pendientes"
            media_root = root / "media"
            db_path.parent.mkdir()
            media_root.mkdir()
            sucursal_id, consolidacion_id = self._crear_base_consolidada(
                db_path,
                con_detalle=False,
            )
            solicitud = self._escribir_solicitud(
                request_root,
                sucursal_id,
                consolidacion_id,
            )

            with mock.patch(
                "herramientas.respaldo_sqlite._depurar_respaldos_periodo",
                side_effect=BackupError("fallo persistente simulado"),
            ):
                for dia in (1, 3, 5):
                    with self.assertRaisesRegex(BackupError, "purga física"):
                        realizar_respaldo(
                            database_path=db_path,
                            backup_root=backup_root,
                            log_path=log_path,
                            retention_days=1,
                            now=dt.datetime(
                                2035,
                                1,
                                dia,
                                12,
                                0,
                                tzinfo=dt.timezone.utc,
                            ),
                            request_root=request_root,
                            media_root=media_root,
                        )

            pendiente = json.loads(solicitud.read_text(encoding="utf-8"))
            asignado = pendiente["respaldo_post_purga"]
            nombres = {path.name for path in backup_root.glob("db-*.sqlite3")}
            self.assertIn(asignado, nombres)
            self.assertLessEqual(len(nombres), 2)
            self.assertGreaterEqual(pendiente["intentos"], 3)

    def test_respaldo_asignado_perdido_solicita_reemplazo_y_cierra(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "runtime" / "db.sqlite3"
            backup_root = root / "backups"
            log_path = root / "logs" / "sqlite-backup.log"
            request_root = root / "runtime" / "purgas-pendientes"
            media_root = root / "media"
            db_path.parent.mkdir()
            media_root.mkdir()
            sucursal_id, consolidacion_id = self._crear_base_consolidada(
                db_path,
                con_detalle=False,
            )
            solicitud = self._escribir_solicitud(
                request_root,
                sucursal_id,
                consolidacion_id,
            )

            with mock.patch(
                "herramientas.respaldo_sqlite._depurar_respaldos_periodo",
                side_effect=BackupError("fallo antes del cierre"),
            ):
                with self.assertRaisesRegex(BackupError, "purga física"):
                    realizar_respaldo(
                        database_path=db_path,
                        backup_root=backup_root,
                        log_path=log_path,
                        retention_days=30,
                        request_root=request_root,
                        media_root=media_root,
                    )

            pendiente = json.loads(solicitud.read_text(encoding="utf-8"))
            asignado = backup_root / pendiente["respaldo_post_purga"]
            for pieza in (
                asignado,
                Path(str(asignado) + ".sha256"),
                Path(str(asignado) + ".json"),
            ):
                pieza.unlink()

            preflight = _procesar_solicitudes_purga(
                database_path=db_path,
                backup_root=backup_root,
                nuevo_backup=None,
                request_root=request_root,
                media_root=media_root,
                timeout=30.0,
            )
            self.assertTrue(preflight["requiere_respaldo"])
            pendiente = json.loads(solicitud.read_text(encoding="utf-8"))
            self.assertNotIn("respaldo_post_purga", pendiente)
            self.assertEqual(
                pendiente["respaldos_post_purga_descartados"][-1]["nombre"],
                asignado.name,
            )

            realizar_respaldo(
                database_path=db_path,
                backup_root=backup_root,
                log_path=log_path,
                retention_days=30,
                request_root=request_root,
                media_root=media_root,
            )
            self.assertFalse(solicitud.exists())
            with closing(sqlite3.connect(db_path)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT estado FROM ventas_consolidacionmensual"
                    ).fetchone()[0],
                    "purgada",
                )

    def test_rechaza_raices_reparse_sin_borrar_senuelo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request_root = root / "requests"
            media_root = root / "media"
            request_root.mkdir()
            media_root.mkdir()
            senuelo = media_root / "senuelo.png"
            senuelo.write_bytes(b"no borrar")
            lstat_real = Path.lstat

            def lstat_request_reparse(value):
                if value == request_root:
                    return mock.Mock(st_mode=stat.S_IFLNK, st_file_attributes=0)
                return lstat_real(value)

            with mock.patch.object(Path, "lstat", lstat_request_reparse):
                with self.assertRaisesRegex(BackupError, "enlace o junction"):
                    _procesar_solicitudes_purga(
                        database_path=root / "db.sqlite3",
                        backup_root=root / "backups",
                        nuevo_backup=None,
                        request_root=request_root,
                        media_root=media_root,
                        timeout=30.0,
                    )

            def lstat_media_reparse(value):
                if value == media_root:
                    return mock.Mock(st_mode=stat.S_IFLNK, st_file_attributes=0)
                return lstat_real(value)

            with mock.patch.object(Path, "lstat", lstat_media_reparse):
                with self.assertRaisesRegex(BackupError, "enlace o junction"):
                    _ruta_media_segura(media_root, "senuelo.png")
            self.assertEqual(senuelo.read_bytes(), b"no borrar")


if __name__ == "__main__":
    unittest.main()
