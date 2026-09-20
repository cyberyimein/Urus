from __future__ import annotations

from datetime import UTC, date, datetime
import hashlib
import json
import sqlite3

from urus_moomoo.config import CollectionTask, CollectorConfig
from urus_moomoo.runtime import CollectorRuntime
from urus_moomoo.services.calendar import MoomooTradingCalendar
from urus_moomoo.services.scheduler import CollectorScheduler
from urus_moomoo.storage.sqlite import CollectorStore


class _SessionSource:
    def trading_days(self, start, end):
        session = date(2026, 9, 8)
        return [{"time": session}] if start <= session <= end else []


class _Sessions:
    def is_session(self, value):
        return value == date(2026, 9, 8)

    def session_close(self, value):
        return datetime(value.year, value.month, value.day, 21, tzinfo=UTC)


class _EodProvider:
    def __init__(self):
        self.complete = False

    def capture_eod_options(self, *, symbols, trading_date, **_params):
        return {
            "trading_date": trading_date.isoformat(),
            "requested_symbols": symbols,
            "provider_raw": {"large": "x" * 1024},
            "complete": self.complete,
        }


def _task():
    return CollectionTask(
        "options-eoflora-eod",
        True,
        "21:00",
        ("SPY", "QQQ", "IWM"),
        "capture_eod_options",
    )


def test_eod_retries_keep_one_raw_slot_and_complete_atomically(tmp_path):
    task = _task()
    provider = _EodProvider()
    runtime = CollectorRuntime(
        provider,
        CollectorConfig(timezone="UTC", output_dir=tmp_path, tasks=(task,)),
    )
    scheduled_at = datetime(2026, 9, 8, 21, tzinfo=UTC)
    try:
        first = runtime.collect_task(task, scheduled_at)
        second = runtime.collect_task(task, scheduled_at)
        raw_count = runtime.store.connection.execute(
            "SELECT COUNT(*) FROM raw_captures WHERE kind = 'capture_eod_options'"
        ).fetchone()[0]
        stored_before_completion = runtime.store.read_raw(first["contract"]["capture_id"])

        provider.complete = True
        final = runtime.collect_task(task, scheduled_at)
        stored_final = runtime.store.read_raw(final["contract"]["capture_id"])
        final_raw_count = runtime.store.connection.execute(
            "SELECT COUNT(*) FROM raw_captures WHERE kind = 'capture_eod_options'"
        ).fetchone()[0]

        assert first["contract"]["capture_id"] != second["contract"]["capture_id"]
        assert second["contract"]["capture_id"] != final["contract"]["capture_id"]
        assert raw_count == 1
        assert final_raw_count == 2
        assert json.loads(stored_before_completion["payload_json"])["data"]["complete"] is False
        assert json.loads(stored_final["payload_json"])["data"]["complete"] is True
        assert runtime.store.scheduler_completed(task.name, scheduled_at)
        scheduler_capture = runtime.store.connection.execute(
            "SELECT capture_id FROM scheduler_runs WHERE task_name = ? AND scheduled_at = ?",
            (task.name, scheduled_at.isoformat()),
        ).fetchone()["capture_id"]
        assert scheduler_capture == final["contract"]["capture_id"]
        assert final["contract"]["schema_version"] == "urus.moomoo.capture.v2"
        assert final["contract"]["task_name"] == task.name
        assert final["contract"]["scheduled_at"] == scheduled_at.isoformat()
        assert final["contract"]["trading_date"] == "2026-09-08"
        expected_digest = hashlib.sha256(
            json.dumps(
                final["contract"]["data"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
        ).hexdigest()
        assert final["contract"]["content_sha256"] == expected_digest
    finally:
        runtime.close()


def test_scheduler_recovers_legacy_complete_eod_capture(tmp_path):
    task = _task()
    scheduled_at = datetime(2026, 9, 8, 21, tzinfo=UTC)
    store = CollectorStore(tmp_path / "collector.sqlite")
    store.write_raw(
        "legacy-complete",
        "capture_eod_options",
        {
            "requested_symbols": ["SPY", "QQQ", "IWM"],
            "data": {"trading_date": "2026-09-08", "complete": True},
        },
        schema_version="urus.moomoo.capture.v1",
    )
    calls = []
    scheduler = CollectorScheduler(
        (task,),
        timezone="UTC",
        market_timezone="UTC",
        calendar=MoomooTradingCalendar(_SessionSource(), _Sessions()),
        runner=lambda *_args: calls.append(True),
        store=store,
        catch_up_seconds=600,
    )
    try:
        assert scheduler.run_once(datetime(2026, 9, 8, 21, 1, tzinfo=UTC)) == []
        assert calls == []
        assert store.scheduler_completed(task.name, scheduled_at)
        recovered_capture = store.connection.execute(
            "SELECT capture_id FROM scheduler_runs WHERE task_name = ? AND scheduled_at = ?",
            (task.name, scheduled_at.isoformat()),
        ).fetchone()["capture_id"]
        assert recovered_capture == "legacy-complete"
    finally:
        store.close()


def test_v3_migration_preserves_runs_and_raw_captures_are_immutable(tmp_path):
    database = tmp_path / "collector.sqlite"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE scheduler_runs (
            task_name TEXT NOT NULL, scheduled_at TEXT NOT NULL, completed_at TEXT NOT NULL,
            PRIMARY KEY(task_name, scheduled_at)
        );
        INSERT INTO scheduler_runs VALUES ('old-task', '2026-09-08T21:00:00+00:00', 'done');
        """
    )
    connection.commit()
    connection.close()

    store = CollectorStore(database)
    try:
        columns = {
            row["name"] for row in store.connection.execute("PRAGMA table_info(scheduler_runs)")
        }
        migrated = store.connection.execute(
            "SELECT task_name, capture_id FROM scheduler_runs WHERE task_name = 'old-task'"
        ).fetchone()
        store.write_raw("immutable", "daily", {"value": 1}, schema_version="v1")
        store.write_raw("immutable", "daily", {"value": 2}, schema_version="v1")
        raw = store.read_raw("immutable")

        assert "capture_id" in columns
        assert migrated["task_name"] == "old-task"
        assert migrated["capture_id"] is None
        assert json.loads(raw["payload_json"]) == {"value": 1}
    finally:
        store.close()
