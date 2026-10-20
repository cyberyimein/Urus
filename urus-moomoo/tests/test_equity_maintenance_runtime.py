from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from urus_moomoo.config import CollectionTask, CollectorConfig
from urus_moomoo.integrations.moomoo import OpenDMarketAdapter
from urus_moomoo.runtime import CollectorRuntime
from urus_moomoo.services.scheduler import CollectorScheduler
from urus_moomoo.storage.equity_history import CleanupDeadlineExceeded


_MARKET_TZ = ZoneInfo("America/New_York")
_TRADING_DATE = "2026-10-15"
_SESSION_OPEN = datetime(2026, 10, 15, 9, 30, tzinfo=_MARKET_TZ)
_MAINTENANCE_AT = datetime(2026, 10, 15, 8, 0, tzinfo=_MARKET_TZ)
_EOF_AT = datetime(2026, 10, 15, 16, 0, tzinfo=_MARKET_TZ)


class _Provider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def reset_collection_run(self) -> None:
        self.calls.append("reset")

    def capture_intraday_equities(self, **_kwargs: Any) -> dict[str, object]:
        self.calls.append("capture_intraday_equities")
        return {}


class _SessionCalendar:
    def is_session(self, _value):
        return True

    def session_open(self, value):
        return datetime(value.year, value.month, value.day, 9, 30, tzinfo=_MARKET_TZ)

    def session_close(self, value):
        return datetime(value.year, value.month, value.day, 16, 0, tzinfo=_MARKET_TZ)


def _task() -> CollectionTask:
    return CollectionTask(
        "equity-maintenance",
        True,
        "pre-open",
        ("SPY", "QQQ", "SMH", "AAA"),
        "maintain_intraday_equities",
        delay_seconds=90 * 60,
        params={"equity_retention_apply": True},
    )


def _runtime(tmp_path: Path, provider: _Provider, clock) -> CollectorRuntime:
    config = CollectorConfig(
        timezone="America/New_York",
        market_timezone="America/New_York",
        output_dir=tmp_path,
        tasks=(_task(),),
        lock_path=tmp_path / "collection.lock",
        sqlite_path=tmp_path / "collector.sqlite",
        exchange_calendar="XNYS",
    )
    return CollectorRuntime(provider, config, clock=clock)


def _seed_expired_equity_capture(runtime: CollectorRuntime) -> None:
    runtime.store.write_scheduled_raw(
        "equity-intraday",
        datetime(2026, 1, 5, 9, 35, tzinfo=_MARKET_TZ),
        "old-equity-capture",
        "capture_intraday_equities",
        {
            "requested_symbols": ["SPY"],
            "data": {
                "schema_version": "moomoo-intraday-equities-refs-v1",
                "trading_date": "2026-01-05",
                "equity_data_refs": [],
            },
        },
        schema_version="moomoo-intraday-equities-refs-v1",
        completed=True,
        capture_trading_date="2026-01-05",
    )


def _seed_eoflora_capture(runtime: CollectorRuntime) -> None:
    runtime.store.write_scheduled_raw(
        "options-eoflora-eod",
        datetime(2026, 10, 14, 16, 0, tzinfo=_MARKET_TZ),
        "eoflora-capture",
        "capture_eod_options",
        {
            "requested_symbols": ["SPY"],
            "data": {"trading_date": "2026-10-14", "complete": True},
        },
        schema_version="moomoo-eod-options-v1",
        completed=True,
    )


def test_maintenance_cleans_only_equity_data_without_provider_or_capture_publish(
    tmp_path: Path,
) -> None:
    clock = lambda: datetime(2026, 10, 15, 12, 10, tzinfo=UTC)
    provider = _Provider()
    runtime = _runtime(tmp_path, provider, clock)
    dry_run_dates: list[str] = []
    original_cleanup = runtime.store.equity_history.cleanup_expired

    def tracked_cleanup(current_date: str, **kwargs):
        dry_run_dates.append(current_date)
        return original_cleanup(current_date, **kwargs)

    try:
        _seed_expired_equity_capture(runtime)
        _seed_eoflora_capture(runtime)
        runtime.store.equity_history.cleanup_expired = tracked_cleanup

        first = runtime.collect_task(_task(), _MAINTENANCE_AT)
        second = runtime.collect_task(_task(), _MAINTENANCE_AT)
        old_equity_capture = runtime.store.read_scheduled_raw(
            "equity-intraday",
            datetime(2026, 1, 5, 9, 35, tzinfo=_MARKET_TZ),
        )
        eoflora_capture = runtime.store.read_scheduled_raw(
            "options-eoflora-eod",
            datetime(2026, 10, 14, 16, 0, tzinfo=_MARKET_TZ),
        )
        maintenance_raw_count = runtime.store.connection.execute(
            "SELECT COUNT(*) FROM raw_captures WHERE kind = 'maintain_intraday_equities'"
        ).fetchone()[0]
        last_applied_date = runtime.store.get_meta("equity_retention_last_applied_date")
        pending_date = runtime.store.get_meta("equity_retention_pending_date")
        pending_reason = runtime.store.get_meta("equity_retention_pending_reason")
        eoflora_completed = runtime.store.scheduler_completed(
            "options-eoflora-eod", datetime(2026, 10, 14, 16, 0, tzinfo=_MARKET_TZ)
        )
    finally:
        runtime.close()

    assert provider.calls == []
    assert first["completed"] is True
    assert first["maintenance_pending"] is False
    assert first.get("contract") is None
    assert second["storage_retention"]["applied"] is False
    assert dry_run_dates == [_TRADING_DATE]
    assert old_equity_capture is None
    assert eoflora_capture is not None
    assert eoflora_completed is True
    assert maintenance_raw_count == 0
    assert last_applied_date == _TRADING_DATE
    assert pending_date is None
    assert pending_reason is None


def test_maintenance_failure_stays_pending_and_scheduler_does_not_mark_completion(
    tmp_path: Path,
) -> None:
    provider = _Provider()
    runtime = _runtime(
        tmp_path,
        provider,
        lambda: datetime(2026, 10, 15, 12, 10, tzinfo=UTC),
    )
    runtime.store.equity_history.cleanup_expired = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        TimeoutError("cleanup dry-run timed out")
    )
    scheduler = CollectorScheduler(
        (_task(),),
        timezone="America/New_York",
        market_timezone="America/New_York",
        calendar=_SessionCalendar(),
        runner=runtime.collect_task,
        store=runtime.store,
        clock=lambda: datetime(2026, 10, 15, 8, 10, tzinfo=_MARKET_TZ),
        catch_up_seconds=30 * 60,
    )
    try:
        ran = scheduler.run_once(datetime(2026, 10, 15, 8, 10, tzinfo=_MARKET_TZ))
        last_applied_date = runtime.store.get_meta("equity_retention_last_applied_date")
        pending_date = runtime.store.get_meta("equity_retention_pending_date")
        pending_reason = runtime.store.get_meta("equity_retention_pending_reason")
        completed = runtime.store.scheduler_completed("equity-maintenance", _MAINTENANCE_AT)
    finally:
        runtime.close()

    assert ran == []
    assert provider.calls == []
    assert last_applied_date is None
    assert pending_date == _TRADING_DATE
    assert "timed out" in str(pending_reason)
    assert completed is False


def test_direct_maintenance_call_after_cutoff_stays_pending_without_cleanup(tmp_path: Path) -> None:
    cleanup_calls: list[str] = []
    provider = _Provider()
    runtime = _runtime(
        tmp_path,
        provider,
        lambda: datetime(2026, 10, 15, 13, 1, tzinfo=UTC),
    )
    runtime.store.equity_history.cleanup_expired = lambda current_date, **_kwargs: (
        cleanup_calls.append(current_date) or {}
    )
    try:
        result = runtime.collect_task(_task(), _MAINTENANCE_AT)
        last_applied_date = runtime.store.get_meta("equity_retention_last_applied_date")
        pending_date = runtime.store.get_meta("equity_retention_pending_date")
    finally:
        runtime.close()

    assert result["completed"] is False
    assert result["pending"] is True
    assert result["reason_code"] == "maintenance_window_closed"
    assert cleanup_calls == []
    assert provider.calls == []
    assert last_applied_date is None
    assert pending_date == _TRADING_DATE


def test_cleanup_deadline_progress_handler_is_removed_after_interruption(
    tmp_path: Path,
) -> None:
    runtime = _runtime(
        tmp_path,
        _Provider(),
        lambda: datetime(2026, 10, 15, 12, 10, tzinfo=UTC),
    )
    try:
        with pytest.raises(CleanupDeadlineExceeded):
            with runtime.store.equity_history._cleanup_deadline_guard(lambda: True):
                runtime.store.connection.execute(
                    "WITH RECURSIVE sequence(value) AS ("
                    "SELECT 1 UNION ALL SELECT value + 1 FROM sequence WHERE value < 5000) "
                    "SELECT SUM(value) FROM sequence"
                ).fetchone()

        # The query would be interrupted if the expired progress callback had
        # leaked beyond its maintenance scope.
        total = runtime.store.connection.execute(
            "WITH RECURSIVE sequence(value) AS ("
            "SELECT 1 UNION ALL SELECT value + 1 FROM sequence WHERE value < 5000) "
            "SELECT SUM(value) FROM sequence"
        ).fetchone()[0]
    finally:
        runtime.close()

    assert total == sum(range(1, 5001))


def test_deadline_crossing_during_apply_rolls_back_and_eoflora_can_run(
    tmp_path: Path,
) -> None:
    clock_state = {"now": datetime(2026, 10, 15, 12, 10, tzinfo=UTC)}
    provider = _Provider()
    runtime = _runtime(tmp_path, provider, lambda: clock_state["now"])
    _seed_expired_equity_capture(runtime)
    _seed_eoflora_capture(runtime)
    connection = runtime.store.connection
    tracked_tables = (
        "raw_captures",
        "scheduled_captures",
        "scheduler_runs",
        "equity_capture_refs",
        "equity_quote_batches",
        "equity_bar_revisions",
        "equity_data_watermarks",
    )

    def snapshot_tables() -> dict[str, list[tuple[object, ...]]]:
        snapshot: dict[str, list[tuple[object, ...]]] = {}
        for table in tracked_tables:
            rows = connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            snapshot[table] = [tuple(row) for row in rows]
        return snapshot

    before_cleanup = snapshot_tables()
    delete_statements: list[str] = []

    def advance_on_first_cleanup_delete(statement: str) -> None:
        if statement.lstrip().upper().startswith("DELETE FROM SCHEDULED_CAPTURES"):
            delete_statements.append(statement)
            clock_state["now"] = datetime(2026, 10, 15, 13, 1, tzinfo=UTC)

    connection.set_trace_callback(advance_on_first_cleanup_delete)

    def runner(task: CollectionTask, scheduled_at: datetime) -> dict[str, object]:
        if task.operation == "maintain_intraday_equities":
            return runtime.collect_task(task, scheduled_at)
        return {"completed": True, "terminal": True}

    eof_task = CollectionTask(
        "options-eoflora-eod",
        True,
        "post-close",
        ("SPY",),
        "capture_eod_options",
    )
    scheduler = CollectorScheduler(
        (_task(), eof_task),
        timezone="America/New_York",
        market_timezone="America/New_York",
        calendar=_SessionCalendar(),
        runner=runner,
        store=runtime.store,
        clock=lambda: clock_state["now"],
        catch_up_seconds=2 * 60 * 60,
    )
    try:
        maintenance_result = runtime.collect_task(_task(), _MAINTENANCE_AT)
        after_cleanup = snapshot_tables()
        expired_capture = runtime.store.read_scheduled_raw(
            "equity-intraday",
            datetime(2026, 1, 5, 9, 35, tzinfo=_MARKET_TZ),
        )
        last_applied_date = runtime.store.get_meta("equity_retention_last_applied_date")
        pending_date = runtime.store.get_meta("equity_retention_pending_date")
        scheduler_completed = runtime.store.scheduler_completed(
            "equity-maintenance", _MAINTENANCE_AT
        )
        connection.set_trace_callback(None)

        clock_state["now"] = _EOF_AT
        eof_ran = scheduler.run_once(_EOF_AT)
        eof_completed = runtime.store.scheduler_completed("options-eoflora-eod", _EOF_AT)
    finally:
        connection.set_trace_callback(None)
        runtime.close()

    assert maintenance_result["completed"] is False
    assert maintenance_result["pending"] is True
    assert maintenance_result["rolled_back"] is True
    assert maintenance_result["reason_code"] == "maintenance_deadline_exceeded"
    assert delete_statements
    assert after_cleanup == before_cleanup
    assert expired_capture is not None
    assert last_applied_date is None
    assert pending_date == _TRADING_DATE
    assert scheduler_completed is False
    assert eof_ran == ["options-eoflora-eod"]
    assert eof_completed is True
    assert provider.calls == []
