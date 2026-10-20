"""Independent acceptance tests for the stock normalized reference store.

These tests deliberately exercise the public ``CollectorStore``/``EquityHistoryStore``
boundary.  They use only a temporary SQLite database; no provider or scheduler is
started here.  The corresponding implementation is expected to expose the history
store as ``CollectorStore.equity_history``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path
import sqlite3

import pytest

from urus_moomoo.storage.sqlite import CollectorStore


_TRADE_DATE = "2026-10-15"
_OPEN = datetime(2026, 10, 15, 13, 30, tzinfo=UTC)


def _bar(
    symbol: str,
    *,
    start: datetime,
    timeframe: str = "1m",
    close: float = 100.0,
    volume: float = 10.0,
) -> dict[str, object]:
    duration = timedelta(minutes=1 if timeframe == "1m" else 5)
    return {
        "symbol": symbol,
        "bar_start_at": start.isoformat(),
        "bar_end_at": (start + duration).isoformat(),
        "open": close - 0.1,
        "high": close + 0.2,
        "low": close - 0.2,
        "close": close,
        "volume": volume,
        "turnover": close * volume,
        "vwap": close,
        "provider": "moomoo_openapi",
        "quality_state": "ready",
    }


def _weekdays(start: date, count: int) -> list[str]:
    result: list[str] = []
    current = start
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current.isoformat())
        current += timedelta(days=1)
    return result


def _trading_sessions(start: date, count: int) -> list[str]:
    """Use the local XNYS calendar when installed; retain an offline fallback."""

    try:
        import exchange_calendars as xcals

        calendar = xcals.get_calendar("XNYS")
        sessions = calendar.sessions_in_range(
            start.isoformat(),
            (start + timedelta(days=count * 3)).isoformat(),
        )
        values = [session.date().isoformat() for session in sessions]
        if len(values) >= count:
            return values[:count]
    except (ImportError, ValueError):
        pass
    return _weekdays(start, count)


def _bar_for_date(
    symbol: str,
    trading_date: str,
    *,
    timeframe: str,
    close: float,
) -> dict[str, object]:
    start = datetime.fromisoformat(f"{trading_date}T13:30:00+00:00")
    return _bar(symbol, start=start, timeframe=timeframe, close=close)


def _use_session_fixture(history: object, trading_dates: list[str]) -> None:
    """Keep retention tests deterministic without consulting a live calendar."""

    history.session_dates_provider = lambda current, count: [
        value for value in trading_dates if value <= current
    ][-count:]


def _count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_same_bar_is_idempotent_and_a_b_a_keeps_each_revision_and_watermark(
    tmp_path: Path,
) -> None:
    store = CollectorStore(tmp_path / "collector.sqlite")
    history = store.equity_history
    first_bar = _bar("AAA", start=_OPEN, close=100.0)
    try:
        first = history.upsert_bars(
            [first_bar],
            timeframe="1m",
            trading_date=_TRADE_DATE,
            source_capture_id="capture-a1",
            observed_at="2026-10-15T13:31:10Z",
        )
        original_ref = history.make_bar_ref(
            provider="moomoo_openapi",
            symbols=["AAA"],
            timeframe="1m",
            adjustment="none",
            from_time=_OPEN,
            through_time=_OPEN + timedelta(minutes=1),
            asof_revision_watermark=first["watermark"],
            capture_trading_date=_TRADE_DATE,
        )
        repeated = history.upsert_bars(
            [first_bar],
            timeframe="1m",
            trading_date=_TRADE_DATE,
            source_capture_id="capture-a-retry",
            observed_at="2026-10-15T13:32:10Z",
        )
        revision_b = history.upsert_bars(
            [_bar("AAA", start=_OPEN, close=101.0)],
            timeframe="1m",
            trading_date=_TRADE_DATE,
            source_capture_id="capture-b",
            observed_at="2026-10-15T13:33:10Z",
        )
        revision_a = history.upsert_bars(
            [first_bar],
            timeframe="1m",
            trading_date=_TRADE_DATE,
            source_capture_id="capture-a2",
            observed_at="2026-10-15T13:34:10Z",
        )
        current_ref = history.make_bar_ref(
            provider="moomoo_openapi",
            symbols=["AAA"],
            timeframe="1m",
            adjustment="none",
            from_time=_OPEN,
            through_time=_OPEN + timedelta(minutes=1),
            asof_revision_watermark=revision_a["watermark"],
            capture_trading_date=_TRADE_DATE,
        )
        old = history.resolve_ref(original_ref)
        current = history.resolve_ref(current_ref)
        revisions = store.connection.execute(
            "SELECT watermark, content_sha256 FROM equity_bar_revisions "
            "WHERE provider = 'moomoo_openapi' AND symbol = 'AAA' "
            "AND timeframe = '1m' ORDER BY watermark"
        ).fetchall()
    finally:
        store.close()

    assert first["inserted"] == 1
    assert repeated == {"inserted": 0, "revised": 0, "unchanged": 1, "watermark": first["watermark"]}
    assert revision_b["revised"] == 1
    assert revision_a["revised"] == 1
    assert len(revisions) == 3
    assert [int(row["watermark"]) for row in revisions] == sorted(int(row["watermark"]) for row in revisions)
    assert old["available"] is True
    assert current["available"] is True
    assert old["rows"][0]["close"] == 100.0
    assert current["rows"][0]["close"] == 100.0


def test_quote_batch_is_sorted_idempotent_and_same_slot_revision_fails_closed(
    tmp_path: Path,
) -> None:
    store = CollectorStore(tmp_path / "collector.sqlite")
    history = store.equity_history
    scheduled_at = datetime(2026, 10, 15, 13, 35, tzinfo=UTC)
    captured_at = datetime(2026, 10, 15, 13, 35, 2, tzinfo=UTC)
    quotes = [
        {"symbol": "AAA", "quote_code": "US.AAA", "last_price": 101.5},
        {"symbol": "BBB", "quote_code": "US.BBB", "last_price": 88.0},
    ]
    try:
        first = history.write_quote_batch(
            task_name="equity-intraday",
            scheduled_at=scheduled_at,
            trading_date=_TRADE_DATE,
            capture_id="capture-g1",
            captured_at=captured_at,
            quotes=quotes,
        )
        retry = history.write_quote_batch(
            task_name="equity-intraday",
            scheduled_at=scheduled_at,
            trading_date=_TRADE_DATE,
            capture_id="capture-g1-retry",
            captured_at=captured_at + timedelta(seconds=3),
            quotes=list(reversed(quotes)),
        )
        with pytest.raises(ValueError, match="different content|内容"):
            history.write_quote_batch(
                task_name="equity-intraday",
                scheduled_at=scheduled_at,
                trading_date=_TRADE_DATE,
                capture_id="capture-g1-conflict",
                captured_at=captured_at + timedelta(seconds=4),
                quotes=[{**quotes[0], "last_price": 102.0}, quotes[1]],
            )
        resolved = history.resolve_ref(first)
        batches = _count(store.connection, "equity_quote_batches")
    finally:
        store.close()

    assert first == retry
    assert first["row_count"] == 2
    assert resolved["available"] is True
    assert [row["symbol"] for row in resolved["rows"]] == ["AAA", "BBB"]
    assert batches == 1


def test_overlapping_shards_materialize_unique_bars_and_quotes_without_provider_raw_rows(
    tmp_path: Path,
) -> None:
    store = CollectorStore(tmp_path / "collector.sqlite")
    history = store.equity_history
    g1_start = _OPEN
    g2_start = _OPEN + timedelta(minutes=1)
    g3_start = _OPEN + timedelta(minutes=2)
    try:
        # The second shard deliberately repeats the first shard's boundary bar.
        history.upsert_bars(
            [
                _bar("AAA", start=g1_start, close=100.0),
                _bar("AAA", start=g2_start, close=100.5),
            ],
            timeframe="1m",
            trading_date=_TRADE_DATE,
            source_capture_id="capture-g1",
        )
        history.upsert_bars(
            [
                _bar("AAA", start=g2_start, close=100.5),
                _bar("AAA", start=g3_start, close=101.0),
            ],
            timeframe="1m",
            trading_date=_TRADE_DATE,
            source_capture_id="capture-g2",
        )
        history.upsert_bars(
            [_bar("AAA", start=g3_start, close=101.0)],
            timeframe="1m",
            trading_date=_TRADE_DATE,
            source_capture_id="capture-g3",
        )
        refs = [
            history.make_bar_ref(
                provider="moomoo_openapi",
                symbols=["AAA"],
                timeframe="1m",
                adjustment="none",
                # The storage resolver treats ``from`` as an inclusive
                # bar-end boundary.  Move it one second into the interval so
                # adjacent shard refs do not claim the prior boundary bar.
                from_time=start + timedelta(seconds=1),
                through_time=start + timedelta(minutes=1),
                capture_trading_date=_TRADE_DATE,
            )
            for start in (g1_start, g2_start, g3_start)
        ]
        resolved_rows = [
            row
            for ref in refs
            for row in history.resolve_ref(ref)["rows"]
        ]
        logical_keys = {(row["symbol"], row["bar_end_at"]) for row in resolved_rows}
        revision_count = _count(store.connection, "equity_bar_revisions")
        provider_raw_tables = [
            str(row["name"])
            for row in store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE '%provider%raw%'"
            ).fetchall()
        ]
    finally:
        store.close()

    assert revision_count == 3
    assert len(logical_keys) == 3
    assert len(resolved_rows) == 3
    assert provider_raw_tables == []
    assert all(ref["row_count"] == 1 for ref in refs)


def test_cleanup_dry_run_is_read_only_and_apply_preserves_live_ref_and_eof_data(
    tmp_path: Path,
) -> None:
    path = tmp_path / "collector.sqlite"
    store = CollectorStore(path)
    history = store.equity_history
    trading_dates = _trading_sessions(date(2026, 9, 14), 23)
    old_date, current_date = trading_dates[0], trading_dates[-1]
    _use_session_fixture(history, trading_dates)
    try:
        for index, trading_date in enumerate(trading_dates):
            start = datetime.fromisoformat(f"{trading_date}T13:30:00+00:00")
            history.upsert_bars(
                [_bar("AAA", start=start, timeframe="1m", close=100.0 + index)],
                timeframe="1m",
                trading_date=trading_date,
                source_capture_id=f"bar-1m-{trading_date}",
            )
            history.upsert_bars(
                [_bar("AAA", start=start, timeframe="5m", close=200.0 + index)],
                timeframe="5m",
                trading_date=trading_date,
                source_capture_id=f"bar-5m-{trading_date}",
            )

        # ``make_bar_ref``'s 5m descriptor is bounded to the most recent 21
        # trading dates.  Pick the oldest date inside that declared retention
        # window so this is a valid live reference while the 1m row below is
        # genuinely outside the two-day retention window.
        protected_date = trading_dates[2]
        protected_ref = history.make_bar_ref(
            provider="moomoo_openapi",
            symbols=["AAA"],
            timeframe="5m",
            adjustment="none",
            from_time=f"{protected_date}T13:30:00+00:00",
            through_time=f"{protected_date}T13:35:00+00:00",
            capture_trading_date=current_date,
            asof_revision_watermark=history.latest_watermark(),
        )
        history.register_capture_refs("live-current", current_date, [protected_ref])

        for quote_date in (old_date, current_date):
            quote_time = datetime.fromisoformat(f"{quote_date}T13:35:00+00:00")
            history.write_quote_batch(
                task_name="equity-intraday",
                scheduled_at=quote_time,
                trading_date=quote_date,
                capture_id=f"quote-{quote_date}",
                captured_at=quote_time + timedelta(seconds=2),
                quotes=[{"symbol": "AAA", "last_price": 100.0}],
            )

        old_payload = {
            "capture_id": "expired-equity",
            "task_name": "equity-intraday",
            "scheduled_at": f"{old_date}T14:00:00+00:00",
            "captured_at": f"{old_date}T14:00:03+00:00",
            "schema_version": "urus.moomoo.capture.v2",
            "kind": "capture_intraday_equities",
            "requested_symbols": ["AAA"],
            "data": {
                "schema_version": "moomoo-intraday-equities-refs-v1",
                "trading_date": old_date,
                "equity_data_refs": [],
            },
        }
        assert store.write_scheduled_raw(
            "equity-intraday",
            datetime.fromisoformat(old_payload["scheduled_at"]),
            "expired-equity",
            "capture_intraday_equities",
            old_payload,
            schema_version="urus.moomoo.capture.v2",
            completed=True,
        ) is True
        store.write_raw(
            "legacy-equity",
            "capture_intraday_equities",
            {"data": {"schema_version": "moomoo-intraday-equities-v1", "trading_date": old_date}},
            schema_version="urus.moomoo.capture.v2",
        )
        store.write_raw(
            "eof-options",
            "capture_eod_options",
            {"data": {"schema_version": "eod-options-v3", "trading_date": old_date}},
            schema_version="urus.moomoo.capture.v2",
        )

        before_counts = {
            table: _count(store.connection, table)
            for table in (
                "equity_bar_revisions",
                "equity_quote_batches",
                "equity_capture_refs",
                "raw_captures",
                "scheduled_captures",
                "scheduler_runs",
            )
        }
        before_size = path.stat().st_size
        plan = history.cleanup_expired(current_date, dry_run=True)
        after_counts = {
            table: _count(store.connection, table)
            for table in before_counts
        }
        after_size = path.stat().st_size
        stale_1m = history.make_bar_ref(
            provider="moomoo_openapi",
            symbols=["AAA"],
            timeframe="1m",
            adjustment="none",
            from_time=f"{old_date}T13:30:00+00:00",
            through_time=f"{old_date}T13:31:00+00:00",
            capture_trading_date=current_date,
            asof_revision_watermark=history.latest_watermark(),
        )
        applied = history.apply_cleanup(plan)
        protected = history.resolve_ref(protected_ref)
        stale_after = history.resolve_ref(stale_1m)
        legacy = store.read_raw("legacy-equity")
        eof = store.read_raw("eof-options")
        expired = store.read_raw("expired-equity")
        live_ref_count = _count(store.connection, "equity_capture_refs")
    finally:
        store.close()

    assert before_counts == after_counts
    assert before_size == after_size
    assert plan["dry_run"] is True
    assert plan["counts"]["equity_bar_revisions"] >= 1
    assert plan["counts"]["equity_quote_batches"] == 1
    assert plan["counts"]["raw_captures"] == 1
    assert "expired-equity" in plan["candidates"]["raw_captures"]
    assert "legacy-equity" not in plan["candidates"]["raw_captures"]
    assert applied["deleted"]["raw_captures"] == 1
    assert applied["deleted"]["equity_quote_batches"] == 1
    assert protected["available"] is True
    assert stale_after["available"] is False
    assert legacy is not None
    assert eof is not None
    assert expired is None
    assert live_ref_count == 1


def test_actual_100_symbol_23_session_capacity_gate_does_not_block_eof_raw(
    tmp_path: Path,
) -> None:
    path = tmp_path / "collector.sqlite"
    store = CollectorStore(path)
    history = store.equity_history
    trading_dates = _trading_sessions(date(2026, 9, 14), 23)
    symbols = [f"S{index:03d}" for index in range(100)]
    try:
        for day_index, trading_date in enumerate(trading_dates):
            start = datetime.fromisoformat(f"{trading_date}T13:30:00+00:00")
            one_minute: list[dict[str, object]] = []
            if day_index >= len(trading_dates) - 2:
                for symbol_index, symbol in enumerate(symbols):
                    one_minute.extend(
                        _bar(
                            symbol,
                            start=start + timedelta(minutes=bar_index),
                            timeframe="1m",
                            close=50.0 + day_index + symbol_index * 0.001 + bar_index * 0.0001,
                            volume=100.0 + bar_index,
                        )
                        for bar_index in range(390)
                    )
            five_minute: list[dict[str, object]] = []
            for symbol_index, symbol in enumerate(symbols):
                five_minute.extend(
                    _bar(
                        symbol,
                        start=start + timedelta(minutes=5 * bar_index),
                        timeframe="5m",
                        close=50.0 + day_index + symbol_index * 0.001 + bar_index * 0.01,
                        volume=500.0 + bar_index,
                    )
                    for bar_index in range(78)
                )
            if one_minute:
                history.upsert_bars(
                    one_minute,
                    timeframe="1m",
                    trading_date=trading_date,
                    source_capture_id=f"bulk-1m-{trading_date}",
                )
            history.upsert_bars(
                five_minute,
                timeframe="5m",
                trading_date=trading_date,
                source_capture_id=f"bulk-5m-{trading_date}",
            )

        capacity = history.measure_capacity()
        one_minute_count = int(
            store.connection.execute(
                "SELECT COUNT(*) FROM equity_bar_revisions WHERE timeframe = '1m'"
            ).fetchone()[0]
        )
        five_minute_count = int(
            store.connection.execute(
                "SELECT COUNT(*) FROM equity_bar_revisions WHERE timeframe = '5m'"
            ).fetchone()[0]
        )
        assert one_minute_count == len(symbols) * 2 * 390
        assert five_minute_count == len(symbols) * len(trading_dates) * 78
        assert one_minute_count + five_minute_count == 257_400
        policy = history.capacity_policy(
            capacity,
            metrics_budget_bytes=max(1, capacity["equity_5m_bytes"] - 1),
            raw_budget_bytes=max(1, capacity["equity_1m_bytes"] - 1),
            total_budget_bytes=max(1, capacity["equity_logical_bytes"] - 1),
        )
        before_eof = _count(store.connection, "raw_captures")
        store.write_raw(
            "eof-still-allowed",
            "capture_eod_options",
            {"data": {"schema_version": "eod-options-v3", "complete": True}},
            schema_version="urus.moomoo.capture.v2",
        )
        after_eof = _count(store.connection, "raw_captures")
        main_size = path.stat().st_size
        wal_size = int(capacity["wal_bytes"])
    finally:
        store.close()

    assert capacity["equity_1m_bytes"] > 0
    assert capacity["equity_5m_bytes"] > 0
    assert capacity["equity_logical_bytes"] >= capacity["equity_1m_bytes"] + capacity["equity_5m_bytes"]
    assert capacity["main_database_bytes"] == main_size or capacity["main_database_bytes"] > 0
    assert wal_size >= 0
    assert policy["skip_all_equities"] is True
    assert after_eof == before_eof + 1


def test_xnys_ttl_window_does_not_extend_for_an_uncollected_session(
    tmp_path: Path,
) -> None:
    """Retention follows XNYS sessions, rather than the dates present in SQLite.

    One real session in the current 21-session window is deliberately left without
    a capture.  An older 5m row must still be expired; otherwise a missing capture
    would incorrectly extend the 21-session retention window.
    """

    xcals = pytest.importorskip("exchange_calendars")
    calendar = xcals.get_calendar("XNYS")
    sessions = calendar.sessions_in_range("2026-08-01", "2026-10-16")
    trading_dates = [session.date().isoformat() for session in sessions]
    assert len(trading_dates) >= 22
    current_date = trading_dates[-1]
    retained_dates = trading_dates[-21:]
    missing_capture_date = retained_dates[-5]
    expired_date = trading_dates[-22]

    store = CollectorStore(tmp_path / "collector.sqlite")
    history = store.equity_history
    try:
        # The missing date is a valid XNYS session but has no normalized rows or
        # watermark.  The older row is the only captured market data in this case.
        history.upsert_bars(
            [
                _bar_for_date(
                    "TTL",
                    expired_date,
                    timeframe="5m",
                    close=101.0,
                )
            ],
            timeframe="5m",
            trading_date=expired_date,
            source_capture_id="ttl-expired",
        )
        recent = history.recent_trading_dates(current_date, count=21)
        plan = history.cleanup_expired(current_date, dry_run=True)
    finally:
        store.close()

    assert recent == retained_dates
    assert missing_capture_date in recent
    assert expired_date not in recent
    assert plan["candidates"]["equity_bar_revisions"]


def test_scheduler_runtime_refs_hydrate_into_backend_p3_and_p4(
    tmp_path: Path,
) -> None:
    """Exercise the real scheduler/runtime boundary before backend projection."""

    import sys

    backend_root = Path(__file__).resolve().parents[2] / "backend"
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    from app.intraday.equities import build_intraday_projection
    from app.intraday.events import evaluate
    from app.intraday.references import hydrate_capture_from_path
    from test_equity_windows_independent_acceptance import (
        CUTOFF,
        _SessionCalendar,
        _WindowProvider,
        _runtime,
        _set_clock,
        _window_task,
    )
    from urus_moomoo.services.scheduler import CollectorScheduler

    provider = _WindowProvider()
    clock = {"now": CUTOFF + timedelta(seconds=60)}
    runtime = _runtime(tmp_path, provider, clock)
    scheduler = CollectorScheduler(
        [_window_task()],
        timezone="America/New_York",
        market_timezone="America/New_York",
        calendar=_SessionCalendar(),
        runner=runtime.collect_task,
        store=runtime.store,
        clock=lambda: clock["now"],
    )
    try:
        for offset in (60, 120, 180, 240):
            _set_clock(clock, CUTOFF + timedelta(seconds=offset))
            scheduler.run_once(clock["now"])
        stored = runtime.store.read_scheduled_raw("equity-intraday", CUTOFF)
        assert stored is not None
        compact = stored["contract"]
        hydrated = hydrate_capture_from_path(runtime.store.path, compact)
        projection = build_intraday_projection(
            hydrated,
            now=CUTOFF + timedelta(seconds=30),
        )
        result = evaluate(
            projection,
            None,
            None,
            evaluated_at=CUTOFF + timedelta(seconds=30),
        )
    finally:
        runtime.close()

    assert compact["data"]["schema_version"] == "moomoo-intraday-equities-refs-v1"
    assert "minute_bars" not in compact["data"]
    assert "quotes" not in compact["data"]
    assert "_resolved_equity_refs" in hydrated
    assert projection["source"]["capture_id"] == compact["capture_id"]
    assert projection["status"] in {"ok", "partial", "stale"}
    assert set(projection["symbols"]) == {"SPY", "QQQ", "SMH", "AAA"}
    assert result["capabilities"]
    assert any(key.startswith("equity-intraday:SPY:") for key in result["capabilities"])
