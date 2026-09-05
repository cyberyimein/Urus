from __future__ import annotations

from datetime import UTC, datetime, timedelta
import sqlite3
from pathlib import Path

from app.services.database_health import DatabaseHealthMonitor


def _create_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO sample(value) VALUES ('ok')")
        connection.commit()
    finally:
        connection.close()


def test_database_health_is_cached_for_one_day(tmp_path: Path) -> None:
    database = tmp_path / "urus.db"
    _create_database(database)
    current = datetime(2026, 9, 5, 0, 0, tzinfo=UTC)
    monitor = DatabaseHealthMonitor(
        f"sqlite:///{database}",
        status_path=tmp_path / "database-health.json",
        clock=lambda: current,
    )

    first = monitor.get()
    assert first["status"] == "ok"
    assert first["integrity_check"] == "ok"
    assert first["foreign_key_violations"] == 0

    calls = 0
    original_inspect = monitor._inspect

    def counted_inspect(checked_at: datetime) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return original_inspect(checked_at)

    monitor._inspect = counted_inspect  # type: ignore[method-assign]
    second = monitor.get()

    assert second == first
    assert calls == 0


def test_database_health_rechecks_after_interval(tmp_path: Path) -> None:
    database = tmp_path / "urus.db"
    _create_database(database)
    current = [datetime(2026, 9, 5, 0, 0, tzinfo=UTC)]
    monitor = DatabaseHealthMonitor(
        f"sqlite:///{database}",
        status_path=tmp_path / "database-health.json",
        clock=lambda: current[0],
    )

    monitor.get()
    current[0] += timedelta(days=1, seconds=1)
    refreshed = monitor.get()

    assert refreshed["status"] == "ok"
    assert refreshed["checked_at"] == current[0].isoformat()


def test_database_health_reports_corrupt_file_without_writing_to_it(tmp_path: Path) -> None:
    database = tmp_path / "urus.db"
    database.write_bytes(b"not a sqlite database")
    original = database.read_bytes()
    monitor = DatabaseHealthMonitor(
        f"sqlite:///{database}",
        status_path=tmp_path / "database-health.json",
    )

    result = monitor.get()

    assert result["status"] == "error"
    assert result["integrity_check"] == "check_failed"
    assert result["message"]
    assert database.read_bytes() == original


def test_database_health_endpoint_uses_the_monitor(client) -> None:
    response = client.get("/api/health/database")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["integrity_check"] == "ok"
    assert payload["foreign_key_violations"] == 0
