"""Independent acceptance tests for the pre-open equity maintenance operation.

The scheduler cases exercise the public task boundary.  The final case drives the
real runtime and temporary SQLite store with a fake provider; it never connects to
OpenD or a production database.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from urus_moomoo.config import CollectionTask, CollectorConfig
from urus_moomoo.runtime import CollectorRuntime
from urus_moomoo.services.scheduler import CollectorScheduler
from urus_moomoo.storage.sqlite import CollectorStore


_MARKET_TZ = ZoneInfo("America/New_York")
_TRADING_DATE = datetime(2026, 10, 15).date()
_SESSION_OPEN = datetime(2026, 10, 15, 9, 30, tzinfo=_MARKET_TZ)
_PREOPEN_AT = _SESSION_OPEN - timedelta(minutes=90)
_EOF_AT = datetime(2026, 10, 15, 16, 0, tzinfo=_MARKET_TZ)
_EXPIRED_CAPTURE_AT = datetime(2026, 1, 5, 9, 35, tzinfo=_MARKET_TZ)


class _SessionCalendar:
    def is_session(self, _value):
        return True

    def session_open(self, value):
        return datetime(value.year, value.month, value.day, 9, 30, tzinfo=_MARKET_TZ)

    def session_close(self, value):
        return datetime(value.year, value.month, value.day, 16, 0, tzinfo=_MARKET_TZ)


class _RuntimeProvider:
    """Provider seam used only for the real-runtime rollback test."""

    def __init__(self) -> None:
        self.eof_calls = 0
        self.reset_calls = 0

    def reset_collection_run(self) -> None:
        self.reset_calls += 1

    def capture_eod_options(
        self, *, symbols: list[str], trading_date, **_params: Any
    ) -> dict[str, object]:
        self.eof_calls += 1
        return {
            "trading_date": trading_date.isoformat(),
            "requested_symbols": list(symbols),
            "complete": True,
        }


def _maintenance_task(*, delay_seconds: float = 90 * 60) -> CollectionTask:
    return CollectionTask(
        "equity-maintenance",
        True,
        "pre-open",
        ("SPY", "QQQ", "SMH", "AAA"),
        "maintain_intraday_equities",
        delay_seconds=delay_seconds,
    )


def _scheduler(
    tasks: tuple[CollectionTask, ...],
    *,
    store: CollectorStore | None = None,
    catch_up_seconds: float = 2 * 60 * 60,
) -> CollectorScheduler:
    return CollectorScheduler(
        tasks,
        timezone="America/New_York",
        market_timezone="America/New_York",
        calendar=_SessionCalendar(),
        runner=lambda *_args: {"completed": True},
        store=store,
        catch_up_seconds=catch_up_seconds,
    )


def _runtime(tmp_path: Path, provider: _RuntimeProvider, clock) -> CollectorRuntime:
    return CollectorRuntime(
        provider,
        CollectorConfig(
            timezone="America/New_York",
            market_timezone="America/New_York",
            output_dir=tmp_path,
            tasks=(_maintenance_task(),),
            lock_path=tmp_path / "collection.lock",
            sqlite_path=tmp_path / "collector.sqlite",
            exchange_calendar="XNYS",
        ),
        clock=clock,
    )


def _seed_expired_capture(runtime: CollectorRuntime) -> None:
    """Seed one refs-v1 capture that the real retention plan must delete."""

    runtime.store.write_scheduled_raw(
        "equity-intraday",
        _EXPIRED_CAPTURE_AT,
        "expired-equity-capture",
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


def _table_snapshot(connection) -> dict[str, list[tuple[object, ...]]]:
    tables = (
        "raw_captures",
        "scheduled_captures",
        "scheduler_runs",
        "equity_capture_refs",
        "equity_bar_revisions",
        "equity_quote_batches",
        "equity_data_watermarks",
    )
    return {
        table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        for table in tables
    }


def test_pre_open_maintenance_is_90_minutes_before_open_and_leaves_legacy_slots_unchanged() -> None:
    maintenance = _maintenance_task()
    legacy = CollectionTask(
        "options-intraday",
        True,
        "intraday",
        ("SPY",),
        "capture_intraday",
        params={
            "interval_seconds": 300,
            "start_margin_seconds": 300,
            "end_margin_seconds": 300,
        },
    )
    scheduler = _scheduler((maintenance, legacy))

    maintenance_times = scheduler._task_times(maintenance, _TRADING_DATE)
    legacy_times = scheduler._task_times(legacy, _TRADING_DATE)

    assert len(maintenance_times) == 1
    assert maintenance_times[0][0] == _PREOPEN_AT
    assert maintenance_times[0][1].operation == "maintain_intraday_equities"
    assert legacy_times[0][0] == datetime(2026, 10, 15, 9, 35, tzinfo=_MARKET_TZ)
    assert legacy_times[-1][0] == datetime(2026, 10, 15, 15, 55, tzinfo=_MARKET_TZ)


def test_maintenance_is_not_caught_up_inside_thirty_minutes_before_open() -> None:
    maintenance = _maintenance_task()
    scheduler = _scheduler((maintenance,))
    scheduled_at, _ = scheduler._task_times(maintenance, _TRADING_DATE)[0]
    assert scheduled_at == _PREOPEN_AT

    # The scheduler has a generous catch-up window so this assertion specifically
    # verifies the maintenance safety cutoff rather than ordinary lateness.
    now = _SESSION_OPEN - timedelta(minutes=29)
    assert scheduler.due(now) == []


def test_pending_maintenance_is_not_marked_complete_and_does_not_run_with_equity_slot(
    tmp_path,
) -> None:
    maintenance = _maintenance_task()
    g1 = CollectionTask(
        "equity-intraday:G1",
        True,
        "09:35",
        ("SPY", "QQQ", "SMH", "AAA"),
        "capture_intraday_equities",
    )
    calls: list[str] = []

    def runner(task: CollectionTask, _scheduled_at: datetime) -> dict[str, object]:
        calls.append(task.operation)
        if task.operation == "maintain_intraday_equities":
            return {
                "completed": False,
                "pending": True,
                "terminal": False,
                "reason_code": "maintenance_timeout",
            }
        return {"completed": True, "terminal": True}

    with CollectorStore(tmp_path / "maintenance.sqlite") as store:
        scheduler = CollectorScheduler(
            (maintenance, g1),
            timezone="America/New_York",
            market_timezone="America/New_York",
            calendar=_SessionCalendar(),
            runner=runner,
            store=store,
            clock=lambda: _SESSION_OPEN + timedelta(minutes=5),
            catch_up_seconds=2 * 60 * 60,
        )

        # The maintenance slot is already too close to open.  G1 is the only
        # operation allowed to run at this tick; maintenance must not be applied
        # synchronously as part of it, and no maintenance completion is recorded.
        assert scheduler.run_once(_SESSION_OPEN + timedelta(minutes=5)) == [
            "equity-intraday:G1"
        ]
        assert calls == ["capture_intraday_equities"]
        assert store.scheduler_completed("equity-maintenance", _PREOPEN_AT) is False


def test_maintenance_failure_shape_remains_pending_when_attempted(tmp_path) -> None:
    maintenance = _maintenance_task()
    calls: list[str] = []

    def runner(task: CollectionTask, _scheduled_at: datetime) -> dict[str, object]:
        calls.append(task.operation)
        return {
            "completed": False,
            "pending": True,
            "terminal": False,
            "reason_code": "maintenance_provider_timeout",
        }

    with CollectorStore(tmp_path / "maintenance-pending.sqlite") as store:
        scheduler = CollectorScheduler(
            (maintenance,),
            timezone="America/New_York",
            market_timezone="America/New_York",
            calendar=_SessionCalendar(),
            runner=runner,
            store=store,
            clock=lambda: _PREOPEN_AT + timedelta(minutes=10),
            catch_up_seconds=30 * 60,
        )
        assert scheduler.run_once(_PREOPEN_AT + timedelta(minutes=10)) == []
        assert calls == ["maintain_intraday_equities"]
        assert store.scheduler_completed("equity-maintenance", _PREOPEN_AT) is False


def test_maintenance_crossing_open_deadline_rolls_back_and_does_not_block_next_eof(
    tmp_path,
) -> None:
    """A long maintenance run must stop at the 30-minute pre-open boundary.

    The runner fixture advances its clock while the maintenance operation is in
    progress.  This keeps the test independent of provider timing while making
    the public outcome explicit: the maintenance slot remains pending and the
    later Eoflora slot can still run.
    """

    maintenance = _maintenance_task()
    eof = CollectionTask(
        "options-eoflora-eod",
        True,
        "post-close",
        ("SPY",),
        "capture_eod_options",
    )
    clock = {"now": _PREOPEN_AT + timedelta(minutes=10)}
    calls: list[str] = []

    def runner(task: CollectionTask, _scheduled_at: datetime) -> dict[str, object]:
        calls.append(task.operation)
        if task.operation == "maintain_intraday_equities":
            # Maintenance started with 80 minutes remaining, then crossed the
            # hard 30-minute cutoff while processing a batch.
            clock["now"] = _SESSION_OPEN - timedelta(minutes=29)
            return {
                "completed": False,
                "pending": True,
                "terminal": False,
                "rolled_back": True,
                "reason_code": "maintenance_deadline_exceeded",
            }
        return {"completed": True, "terminal": True}

    with CollectorStore(tmp_path / "maintenance-deadline.sqlite") as store:
        scheduler = CollectorScheduler(
            (maintenance, eof),
            timezone="America/New_York",
            market_timezone="America/New_York",
            calendar=_SessionCalendar(),
            runner=runner,
            store=store,
            clock=lambda: clock["now"],
            # Keep ordinary scheduler catch-up bounded: the maintenance retry
            # must not be re-run eight hours later alongside the Eoflora slot.
            catch_up_seconds=2 * 60 * 60,
        )

        assert scheduler.run_once(clock["now"]) == []
        assert calls == ["maintain_intraday_equities"]
        assert store.scheduler_completed("equity-maintenance", _PREOPEN_AT) is False

        # The failed maintenance attempt must not poison a later Eoflora slot.
        eof_time = _SESSION_OPEN.replace(hour=16, minute=0)
        clock["now"] = eof_time
        assert scheduler.run_once(eof_time) == ["options-eoflora-eod"]
        assert calls == ["maintain_intraday_equities", "capture_eod_options"]
        assert store.scheduler_completed("options-eoflora-eod", eof_time) is True


def test_real_runtime_rollback_on_sqlite_delete_keeps_eoflora_schedulable(
    tmp_path: Path,
) -> None:
    """A real maintenance transaction must roll back when its deadline closes."""

    clock_state = {"now": datetime(2026, 10, 15, 12, 10, tzinfo=UTC)}
    provider = _RuntimeProvider()
    runtime = _runtime(tmp_path, provider, lambda: clock_state["now"])
    _seed_expired_capture(runtime)
    connection = runtime.store.connection
    before = _table_snapshot(connection)
    delete_seen = False

    def advance_at_first_delete(statement: str) -> None:
        nonlocal delete_seen
        if not delete_seen and "DELETE FROM" in statement.upper():
            delete_seen = True
            # 09:01 EDT: the maintenance run started at 08:10 EDT, but the
            # deadline was crossed while SQLite was applying its first delete.
            clock_state["now"] = datetime(2026, 10, 15, 13, 1, tzinfo=UTC)

    connection.set_trace_callback(advance_at_first_delete)

    eof_task = CollectionTask(
        "options-eoflora-eod",
        True,
        "post-close",
        ("SPY",),
        "capture_eod_options",
    )

    def runner(task: CollectionTask, scheduled_at: datetime) -> dict[str, object]:
        return runtime.collect_task(task, scheduled_at)

    scheduler = CollectorScheduler(
        (_maintenance_task(), eof_task),
        timezone="America/New_York",
        market_timezone="America/New_York",
        calendar=_SessionCalendar(),
        runner=runner,
        store=runtime.store,
        clock=lambda: clock_state["now"],
        catch_up_seconds=2 * 60 * 60,
    )

    try:
        maintenance_result = scheduler.run_once(_PREOPEN_AT + timedelta(minutes=10))
        after = _table_snapshot(connection)
        last_applied_date = runtime.store.get_meta("equity_retention_last_applied_date")
        pending_date = runtime.store.get_meta("equity_retention_pending_date")
        pending_reason = runtime.store.get_meta("equity_retention_pending_reason")
        maintenance_completed = runtime.store.scheduler_completed(
            "equity-maintenance", _PREOPEN_AT
        )

        assert delete_seen is True
        assert maintenance_result == []
        assert after == before
        assert maintenance_completed is False
        assert last_applied_date is None
        assert pending_date == _TRADING_DATE.isoformat()
        assert "deadline" in str(pending_reason)
        assert provider.eof_calls == 0

        # The failed maintenance transaction must not make the later Eoflora
        # slot unavailable.  This call is real runtime + scheduler execution;
        # only the provider itself remains a deterministic local fake.
        connection.set_trace_callback(None)
        clock_state["now"] = _EOF_AT.astimezone(UTC)
        eof_ran = scheduler.run_once(_EOF_AT)
        assert eof_ran == ["options-eoflora-eod"]
        assert provider.eof_calls == 1
        assert runtime.store.scheduler_completed("options-eoflora-eod", _EOF_AT) is True
    finally:
        connection.set_trace_callback(None)
        runtime.close()
