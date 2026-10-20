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


class _HistoryProvider(_Provider):
    def __init__(self, symbols: tuple[str, ...]) -> None:
        super().__init__()
        self.history_requests: list[tuple[str, str]] = []
        self.quota_symbols = list(symbols)

    def quota_snapshot(self) -> dict[str, object]:
        return {
            "available": True,
            "history": {
                "used_quota": len(self.quota_symbols),
                "remain_quota": 100,
                "total": 200,
                "window_symbols": list(self.quota_symbols),
            },
        }

    def fetch_intraday_history_5m(
        self,
        symbol: str,
        trading_date: str,
        *,
        deadline_at: datetime,
        timestamp_role: str = "start",
    ) -> list[dict[str, object]]:
        assert timestamp_role == "start"
        self.history_requests.append((symbol, trading_date))
        return _regular_5m_bars(symbol, trading_date)


class _DeadlineHistoryProvider(_HistoryProvider):
    def __init__(self, symbols: tuple[str, ...], on_fetch) -> None:
        super().__init__(symbols)
        self.on_fetch = on_fetch

    def fetch_intraday_history_5m(
        self,
        symbol: str,
        trading_date: str,
        *,
        deadline_at: datetime,
        timestamp_role: str = "start",
    ) -> list[dict[str, object]]:
        bars = super().fetch_intraday_history_5m(
            symbol,
            trading_date,
            deadline_at=deadline_at,
            timestamp_role=timestamp_role,
        )
        self.on_fetch()
        return bars


def _regular_5m_bars(symbol: str, trading_date: str) -> list[dict[str, object]]:
    import exchange_calendars as xcals

    calendar = xcals.get_calendar("XNYS")
    opened = calendar.session_open(trading_date).to_pydatetime().astimezone(UTC)
    closed = calendar.session_close(trading_date).to_pydatetime().astimezone(UTC)
    bars = []
    current = opened
    index = 0
    while current < closed:
        close = 100.0 + index * 0.01
        bars.append(
            {
                "provider": "moomoo_openapi",
                "symbol": symbol,
                "timeframe": "5m",
                "adjustment": "none",
                "trading_date": trading_date,
                "bar_start_at": current.isoformat(),
                "bar_end_at": (current + timedelta(minutes=5)).isoformat(),
                "open": close,
                "high": close + 0.1,
                "low": close - 0.1,
                "close": close,
                "volume": 100.0,
                "turnover": close * 100.0,
                "vwap": close,
                "quality_state": "ready",
            }
        )
        current += timedelta(minutes=5)
        index += 1
    return bars


def test_maintenance_fills_only_missing_pool_history_and_backend_enrich_reads_refs(
    tmp_path: Path,
) -> None:
    import sys

    import exchange_calendars as xcals

    repo_root = Path(__file__).resolve().parents[2]
    backend_root = str(repo_root / "backend")
    collector_src = str(repo_root / "urus-moomoo" / "src")
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)
    if collector_src not in sys.path:
        sys.path.insert(0, collector_src)

    from app.decision_harness.contracts import content_sha256
    from app.intraday.history_features import enrich_indicator_history
    from app.intraday.references import hydrate_capture_from_path, resolved_capture_data
    from urus_moomoo.config import CollectionTask

    symbols = ("SPY", "QQQ", "AAA", "BBB")
    stock_symbols = ("AAA", "BBB")
    provider = _HistoryProvider(stock_symbols)
    clock = lambda: datetime(2026, 10, 15, 12, 10, tzinfo=UTC)
    runtime = _runtime(tmp_path, provider, clock)
    task = CollectionTask(
        "equity-maintenance",
        True,
        "pre-open",
        symbols,
        "maintain_intraday_equities",
        delay_seconds=90 * 60,
        params={
            "equity_retention_apply": True,
            "display_group_by_symbol": {
                "SPY": "大盘基准",
                "QQQ": "大盘基准",
                "AAA": "航天",
                "BBB": "半导体",
            },
            "prewarm_symbols": list(stock_symbols),
        },
    )
    try:
        result = runtime.collect_task(task, _MAINTENANCE_AT)
        first_requests = list(provider.history_requests)
        repeated = runtime.collect_task(task, _MAINTENANCE_AT)

        trading_date = _TRADING_DATE
        history = runtime.store.equity_history
        calendar = xcals.get_calendar("XNYS")
        prior_dates = ["2026-10-13", "2026-10-14"]
        range_start = calendar.session_open(prior_dates[0]).to_pydatetime()
        current_open = calendar.session_open(trading_date).to_pydatetime()
        ref = history.make_bar_ref(
            provider="moomoo_openapi",
            symbols=symbols,
            timeframe="5m",
            adjustment="none",
            from_time=range_start,
            through_time=current_open + timedelta(minutes=5),
            capture_trading_date=trading_date,
        )
        data = {
            "schema_version": "moomoo-intraday-equities-refs-v1",
            "evidence_cutoff": (current_open + timedelta(minutes=5)).isoformat(),
            "equity_data_refs": [ref],
        }
        capture = {
            "capture_id": "open-slot-with-history",
            "schema_version": "urus.moomoo.capture.v2",
            "kind": "capture_intraday_equities",
            "task_name": "equity-intraday:G1",
            "scheduled_at": (current_open + timedelta(minutes=5)).isoformat(),
            "trading_date": trading_date,
            "captured_at": (current_open + timedelta(minutes=6)).isoformat(),
            "provider": "moomoo_openapi",
            "requested_symbols": list(symbols),
            "content_sha256": content_sha256(data),
            "data": data,
        }
        hydrated = hydrate_capture_from_path(runtime.store.path, capture)
        resolved = resolved_capture_data(hydrated)
        historical = [
            row for row in resolved["historical_5m_bars"] if row["symbol"] == "AAA"
        ]
        current_bar = _regular_5m_bars("AAA", trading_date)[0]
        enriched = enrich_indicator_history(
            [current_bar],
            historical,
            symbol="AAA",
            trading_date=datetime.fromisoformat(trading_date).date(),
            cutoff=datetime.fromisoformat(current_bar["bar_end_at"]),
            market_calendar="XNYS",
            adjustment="none",
        )
        audit = runtime.store.get_meta(f"intraday_history_prewarm:{trading_date}")
    finally:
        runtime.close()

    assert result["history_prewarm"]["summary"] == {
        "cache_hit": 0,
        "requested": 2,
        "completed": 2,
        "skipped": 0,
        "failed": 0,
    }
    assert len(first_requests) == 4
    assert {symbol for symbol, _day in first_requests} == set(stock_symbols)
    assert not {symbol for symbol, _day in first_requests} & {"SPY", "QQQ"}
    assert {day for _symbol, day in first_requests} == set(prior_dates)
    assert repeated["history_prewarm"]["summary"]["cache_hit"] == 2
    assert provider.history_requests == first_requests
    assert ref["row_count"] == len(stock_symbols) * 156
    assert len(historical) == 156
    assert enriched["features"]["macd_warmup_bars"] == 157
    assert enriched["features"]["macd_signal_eligible"] is True
    assert audit is not None


def test_history_returned_after_maintenance_deadline_is_not_written_or_completed(
    tmp_path: Path,
) -> None:
    clock_state = {"now": datetime(2026, 10, 15, 12, 10, tzinfo=UTC)}
    provider = _DeadlineHistoryProvider(
        ("AAA",),
        lambda: clock_state.update(now=datetime(2026, 10, 15, 17, 0, tzinfo=UTC)),
    )
    runtime = _runtime(tmp_path, provider, lambda: clock_state["now"])
    task = CollectionTask(
        "equity-maintenance",
        True,
        "pre-open",
        ("AAA",),
        "maintain_intraday_equities",
        delay_seconds=90 * 60,
        params={"equity_retention_apply": True, "prewarm_symbols": ["AAA"]},
    )
    try:
        result = runtime.collect_task(task, _MAINTENANCE_AT)
        stored_count = runtime.store.connection.execute(
            "SELECT COUNT(*) FROM equity_bar_revisions "
            "WHERE timeframe = '5m' AND symbol = 'AAA'"
        ).fetchone()[0]
    finally:
        runtime.close()

    report = result["history_prewarm"]["symbols"][0]
    assert stored_count == 0
    assert report["status"] == "skipped"
    assert report["reason_code"] == "maintenance_deadline"
    assert result["history_prewarm"]["summary"]["completed"] == 0


def test_history_storage_failure_is_local_to_symbol(tmp_path: Path) -> None:
    provider = _HistoryProvider(("AAA", "BBB"))
    runtime = _runtime(
        tmp_path,
        provider,
        lambda: datetime(2026, 10, 15, 12, 10, tzinfo=UTC),
    )
    task = CollectionTask(
        "equity-maintenance",
        True,
        "pre-open",
        ("AAA", "BBB"),
        "maintain_intraday_equities",
        delay_seconds=90 * 60,
        params={
            "equity_retention_apply": True,
            "prewarm_symbols": ["AAA", "BBB"],
        },
    )
    history = runtime.store.equity_history
    original_upsert = history.upsert_bars

    def fail_one_symbol(rows, **kwargs):
        materialized = list(rows)
        if materialized and materialized[0].get("symbol") == "AAA":
            raise RuntimeError("synthetic SQLite write failure")
        return original_upsert(materialized, **kwargs)

    history.upsert_bars = fail_one_symbol
    try:
        result = runtime.collect_task(task, _MAINTENANCE_AT)
        aaa_count = runtime.store.connection.execute(
            "SELECT COUNT(*) FROM equity_bar_revisions "
            "WHERE timeframe = '5m' AND symbol = 'AAA'"
        ).fetchone()[0]
        bbb_count = runtime.store.connection.execute(
            "SELECT COUNT(*) FROM equity_bar_revisions "
            "WHERE timeframe = '5m' AND symbol = 'BBB'"
        ).fetchone()[0]
    finally:
        runtime.close()

    reports = {
        item["symbol"]: item for item in result["history_prewarm"]["symbols"]
    }
    assert reports["AAA"]["status"] == "failed"
    assert reports["AAA"]["reason_code"] == "storage_error"
    assert reports["BBB"]["status"] == "completed"
    assert aaa_count == 0
    assert bbb_count >= 100


def test_history_quota_probe_runs_inside_provider_request_budget(tmp_path: Path) -> None:
    provider = _HistoryProvider(("AAA",))
    runtime = _runtime(
        tmp_path,
        provider,
        lambda: datetime(2026, 10, 15, 12, 10, tzinfo=UTC),
    )
    budget_events: list[str] = []

    @contextmanager
    def request_budget(_deadline: datetime, timeout_seconds: float):
        budget_events.append(f"enter:{timeout_seconds}")
        try:
            yield
        finally:
            budget_events.append("exit")

    provider._request_budget = request_budget
    original_quota_snapshot = provider.quota_snapshot

    def quota_snapshot() -> dict[str, object]:
        assert budget_events and budget_events[-1].startswith("enter:")
        return original_quota_snapshot()

    provider.quota_snapshot = quota_snapshot
    task = CollectionTask(
        "equity-maintenance",
        True,
        "pre-open",
        ("AAA",),
        "maintain_intraday_equities",
        delay_seconds=90 * 60,
        params={"equity_retention_apply": True, "prewarm_symbols": ["AAA"]},
    )
    try:
        result = runtime.collect_task(task, _MAINTENANCE_AT)
    finally:
        runtime.close()

    assert result["history_prewarm"]["summary"]["completed"] == 1
    assert budget_events[0].startswith("enter:")
    assert budget_events[-1] == "exit"


def test_non_ready_cached_history_is_a_gap_and_gets_refreshed(tmp_path: Path) -> None:
    provider = _HistoryProvider(("AAA",))
    runtime = _runtime(
        tmp_path,
        provider,
        lambda: datetime(2026, 10, 15, 12, 10, tzinfo=UTC),
    )
    task = CollectionTask(
        "equity-maintenance",
        True,
        "pre-open",
        ("AAA",),
        "maintain_intraday_equities",
        delay_seconds=90 * 60,
        params={"equity_retention_apply": True, "prewarm_symbols": ["AAA"]},
    )
    history = runtime.store.equity_history
    for session_date in ("2026-10-13", "2026-10-14"):
        stale_rows = _regular_5m_bars("AAA", session_date)
        for row in stale_rows:
            row["quality_state"] = "stale"
        history.upsert_bars(
            stale_rows,
            timeframe="5m",
            trading_date=session_date,
            source_capture_id=f"stale-seed:{session_date}",
            strict=True,
        )
    try:
        result = runtime.collect_task(task, _MAINTENANCE_AT)
        first_requests = list(provider.history_requests)
    finally:
        runtime.close()

    assert result["history_prewarm"]["summary"]["cache_hit"] == 0
    assert result["history_prewarm"]["summary"]["completed"] == 1
    assert {session_date for _symbol, session_date in first_requests} == {
        "2026-10-13",
        "2026-10-14",
    }


def test_opend_five_minute_history_uses_bounded_regular_session_request() -> None:
    class _Sdk:
        RET_OK = 0

        class KLType:
            K_5M = "K_5M"

        class AuType:
            NONE = "NONE"

    class _QuoteContext:
        def __init__(self) -> None:
            self.request: dict[str, object] | None = None

        def request_history_kline(self, **kwargs):
            self.request = dict(kwargs)
            return 0, [
                {
                    "time_key": "2026-10-14 09:35:00",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.5,
                    "volume": 100,
                    "turnover": 10050,
                }
            ]

    class _NoopRateLimiter:
        def acquire_rate_slot(self, *_args, **_kwargs):
            return None

    quote_context = _QuoteContext()
    adapter = OpenDMarketAdapter(
        "localhost",
        11111,
        sdk=_Sdk(),
        quote_context=quote_context,
        rate_limiter=_NoopRateLimiter(),
    )
    try:
        bars = adapter.fetch_intraday_history_5m(
            "AAA",
            "2026-10-14",
            deadline_at=datetime.now(UTC) + timedelta(minutes=1),
            timestamp_role="end",
        )
    finally:
        adapter.close()

    assert quote_context.request is not None
    assert quote_context.request["code"] == "US.AAA"
    assert quote_context.request["start"] == quote_context.request["end"] == "2026-10-14"
    assert quote_context.request["ktype"] == "K_5M"
    assert quote_context.request["autype"] == "NONE"
    assert quote_context.request["extended_time"] is False
    assert quote_context.request["max_count"] == 1000
    assert len(bars) == 1
    assert bars[0]["bar_start_at"] == datetime(
        2026, 10, 14, 13, 30, tzinfo=UTC
    ).isoformat()
    assert bars[0]["bar_end_at"] == datetime(
        2026, 10, 14, 13, 35, tzinfo=UTC
    ).isoformat()
