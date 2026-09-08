from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from app.core.database import create_database


def test_file_sqlite_connections_use_wal_and_bounded_busy_timeout(tmp_path: Path) -> None:
    engine, _ = create_database(f"sqlite:///{tmp_path / 'database.db'}")
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("PRAGMA journal_mode")) == "wal"
            assert connection.scalar(text("PRAGMA busy_timeout")) == 30_000
    finally:
        engine.dispose()


def test_file_sqlite_writer_can_commit_while_reader_holds_transaction(tmp_path: Path) -> None:
    engine, session_factory = create_database(f"sqlite:///{tmp_path / 'database.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)"))
            connection.execute(text("INSERT INTO sample(value) VALUES ('before')"))

        reader = session_factory()
        writer = session_factory()
        try:
            reader.connection().exec_driver_sql("BEGIN")
            reader.execute(text("SELECT * FROM sample")).all()
            writer.execute(text("INSERT INTO sample(value) VALUES ('after')"))
            writer.commit()
        finally:
            reader.close()
            writer.close()

        with engine.connect() as connection:
            assert connection.scalar(text("SELECT COUNT(*) FROM sample")) == 2
    finally:
        engine.dispose()


def test_in_memory_sqlite_still_uses_shared_test_connection() -> None:
    engine, session_factory = create_database("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)"))
            connection.execute(text("INSERT INTO sample(value) VALUES ('ok')"))

        with session_factory() as session:
            assert session.scalar(text("SELECT value FROM sample")) == "ok"
    finally:
        engine.dispose()
