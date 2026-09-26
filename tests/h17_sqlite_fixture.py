"""SQLite real de H17 en RUNNER_TEMP; no usa una instalación POS."""
import sqlite3
import sys
import uuid
from pathlib import Path


def identifiers(sale: str, outbox: str) -> tuple[str, str]:
    return (
        str(uuid.uuid5(uuid.NAMESPACE_URL, "h17/sale/" + sale)),
        str(uuid.uuid5(uuid.NAMESPACE_URL, "h17/outbox/" + outbox)),
    )


def create(path: Path, sale: str, outbox: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sale_id, outbox_id = identifiers(sale, outbox)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE sale (id TEXT PRIMARY KEY, total INTEGER NOT NULL)")
        connection.execute(
            "CREATE TABLE outbox (id TEXT PRIMARY KEY, sale_id TEXT NOT NULL, payload TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO sale VALUES (?, ?)", (sale_id, 12500))
        connection.execute(
            "INSERT INTO outbox VALUES (?, ?, ?)", (outbox_id, sale_id, outbox)
        )


def verify(path: Path, sale: str, outbox: str) -> None:
    expected_sale, expected_outbox = identifiers(sale, outbox)
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT id, total FROM sale").fetchall() == [
            (expected_sale, 12500)
        ]
        assert connection.execute(
            "SELECT id, sale_id, payload FROM outbox"
        ).fetchall() == [(expected_outbox, expected_sale, outbox)]


if __name__ == "__main__":
    if len(sys.argv) != 5:
        raise SystemExit("usage: h17_sqlite_fixture.py create|verify PATH SALE OUTBOX")
    operation, database_path, sale_value, outbox_value = sys.argv[1:]
    database = Path(database_path).resolve()
    if operation == "create":
        create(database, sale_value, outbox_value)
    elif operation == "verify":
        verify(database, sale_value, outbox_value)
    else:
        raise SystemExit("operation must be create or verify")
