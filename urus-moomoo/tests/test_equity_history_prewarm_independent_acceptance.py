"""Independent acceptance tests for the bounded intraday MACD history prewarm.

These tests deliberately use a fake provider and a temporary collector SQLite
database.  They exercise the public runtime/store boundary and the backend
reference resolver; they do not connect to OpenD or any remote service.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pytest

from urus_moomoo.config import CollectionTask, CollectorConfig
from urus_moomoo.runtime import CollectorRuntime


_MARKET_TZ = ZoneInfo("America/New_York")
_TRADING_DATE = "2026-10-15"
_MAINTENANCE_AT = datetime(2026, 10, 15, 8, 0, tzinfo=_MARKET_TZ)
_CLOCK_AT = datetime(2026, 10, 15, 12, 10, tzinfo=UTC)


def _bar(symbol: str, opened: datetime, index: int, *, close_offset: float = 0.0) -> dict[str, object]:
    close = 100.0 + close_offset + index * 0.07
    ended = opened + timedelta(minutes=5)
    return {
        "provider": "moomoo_openapi",
        "symbol": symbol,
        "timeframe": "5m",
        "adjustment": "none",
        "trading_date": opened.astimezone(_MARKET_TZ).date().isoformat(),
        "bar_start_at": opened.astimezone(UTC).isoformat(),
        "bar_end_at": ended.astimezone(UTC).isoformat(),
        "open": close - 0.02,
        "high": close + 0.10,
        "low": close - 0.10,
        "close": close,
        "volume": 100.0 + index,
        "turnover": close * (100.0 + index),
        "vwap": close,
        "quality_state": "ready",
    }


def _regular_5m_bars(symbol: str, trading_date: str, *, close_offset: float = 0.0) -> list[dict[str, object]]:
    calendar = xcals.get_calendar("XNYS")
    opened = calendar.session_open(trading_date).to_pydatetime().astimezone(UTC)
    closed = calendar.session_close(trading_date).to_pydatetime().astimezone(UTC)
    rows: list[dict[str, object]] = []
    current = opened
    index = 0
    while current + timedelta(minutes=5) <= closed:
        rows.append(_bar(symbol, current, index, close_offset=close_offset))
        current += timedelta(minutes=5)
        index += 1
    return rows


class _PrewarmProvider:
    def __init__(self, *, failures: set[str] | None = None) -> None:
        self.failures = set(failures or ())
        self.history_requests: list[tuple[str, str]] = []
        self.in_transaction_during_request: list[bool] = []
        self.connection = None

    def reset_collection_run(self) -> None:
        return None

    def quota_snapshot(self) -> dict[str, object]:
        return {
            "available": True,
            "history": {
                "used_quota": 0,
                "remain_quota": 1000,
                "total": 1000,
                "window_symbols": [],
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
        del deadline_at, timestamp_role
        self.history_requests.append((symbol, trading_date))
        if self.connection is not None:
            self.in_transaction_during_request.append(bool(self.connection.in_transaction))
        if symbol in self.failures:
            raise RuntimeError(f"synthetic history failure for {symbol}")
        return _regular_5m_bars(symbol, trading_date, close_offset=10.0 if symbol == "AAA" else 20.0)


def _maintenance_task(*, prewarm_symbols: tuple[str, ...]) -> CollectionTask:
    return CollectionTask(
        "equity-maintenance",
        True,
        "pre-open",
        ("SPY", "QQQ", "AAA", "BBB"),
        "maintain_intraday_equities",
        delay_seconds=90 * 60,
        params={
            "equity_retention_apply": True,
            "prewarm_symbols": list(prewarm_symbols),
        },
    )


def _runtime(tmp_path: Path, provider: _PrewarmProvider, task: CollectionTask) -> CollectorRuntime:
    # Include the normal equity capture task so the same HistoryAdmission
    # scope is present, while the maintenance task remains responsible for the
    # explicit prewarm_symbols subset.
    capture_task = CollectionTask(
        "equity-intraday",
        True,
        "intraday",
        ("AAA", "BBB"),
        "capture_intraday_equities",
        params={"minute_timestamp_role": "start"},
    )
    config = CollectorConfig(
        timezone="America/New_York",
        market_timezone="America/New_York",
        output_dir=tmp_path,
        tasks=(task, capture_task),
        lock_path=tmp_path / "collection.lock",
        sqlite_path=tmp_path / "collector.sqlite",
        exchange_calendar="XNYS",
    )
    runtime = CollectorRuntime(provider, config, clock=lambda: _CLOCK_AT)
    provider.connection = runtime.store.connection
    return runtime


def _backend_modules() -> tuple[Any, Any, Any, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    backend_root = str(repo_root / "backend")
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)
    from app.decision_harness.contracts import content_sha256
    from app.intraday.history_features import enrich_indicator_history
    from app.intraday.references import hydrate_capture_from_path, resolved_capture_data

    return content_sha256, enrich_indicator_history, hydrate_capture_from_path, resolved_capture_data


def _capture_for_history_ref(runtime: CollectorRuntime, symbol: str = "AAA") -> dict[str, Any]:
    content_sha256, _enrich, _hydrate, _resolved = _backend_modules()
    calendar = xcals.get_calendar("XNYS")
    prior_sessions = list(calendar.sessions_in_range(date(2026, 10, 1), date(2026, 10, 15)))[-3:-1]
    first_open = calendar.session_open(prior_sessions[0]).to_pydatetime().astimezone(UTC)
    current_end = calendar.session_open(_TRADING_DATE).to_pydatetime().astimezone(UTC) + timedelta(minutes=5)
    ref = runtime.store.equity_history.make_bar_ref(
        provider="moomoo_openapi",
        symbols=[symbol],
        timeframe="5m",
        adjustment="none",
        from_time=first_open,
        through_time=current_end,
        capture_trading_date=_TRADING_DATE,
    )
    data: dict[str, object] = {
        "schema_version": "moomoo-intraday-equities-refs-v1",
        "provider": "moomoo_openapi",
        "trading_date": _TRADING_DATE,
        "scheduled_at": current_end.isoformat(),
        "evidence_cutoff": current_end.isoformat(),
        "requested_symbols": [symbol],
        "quote_symbols": [symbol],
        "minute_symbols": [symbol],
        "equity_data_refs": [ref],
    }
    return {
        "schema_version": "urus.moomoo.capture.v2",
        "capture_id": "prewarm-independent-capture",
        "kind": "capture_intraday_equities",
        "task_name": "equity-intraday:G1",
        "provider": "moomoo_openapi",
        "scheduled_at": current_end.isoformat(),
        "trading_date": _TRADING_DATE,
        "captured_at": (current_end + timedelta(minutes=1)).isoformat(),
        "requested_symbols": [symbol],
        "content_sha256": content_sha256(data),
        "data": data,
    }


def _ema(values: list[float], window: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) < window:
        return result
    current = sum(values[:window]) / window
    result[window - 1] = current
    alpha = 2.0 / (window + 1.0)
    for index in range(window, len(values)):
        current = current + alpha * (values[index] - current)
        result[index] = current
    return result


def _reference_macd(closes: list[float]) -> tuple[float, float, float]:
    fast = _ema(closes, 12)
    slow = _ema(closes, 26)
    dif = [
        fast[index] - slow[index]
        if fast[index] is not None and slow[index] is not None
        else None
        for index in range(len(closes))
    ]
    dea: list[float | None] = [None] * len(closes)
    start = next(index for index, value in enumerate(dif) if value is not None)
    valid = [value for value in dif[start:] if value is not None]
    current = sum(valid[:9]) / 9.0
    dea[start + 8] = current
    alpha = 2.0 / 10.0
    for offset, value in enumerate(valid[9:], start=9):
        current = current + alpha * (value - current)
        dea[start + offset] = current
    histogram = [
        dif[index] - dea[index]
        if dif[index] is not None and dea[index] is not None
        else None
        for index in range(len(closes))
    ]
    delta = [None] * len(closes)
    delta2 = [None] * len(closes)
    for index in range(1, len(closes)):
        if histogram[index] is not None and histogram[index - 1] is not None:
            delta[index] = histogram[index] - histogram[index - 1]
    for index in range(1, len(closes)):
        if delta[index] is not None and delta[index - 1] is not None:
            delta2[index] = delta[index] - delta[index - 1]
    return histogram[-1], delta[-1], delta2[-1]


def test_prewarm_scope_cache_hit_and_provider_is_outside_sqlite_transaction(tmp_path: Path) -> None:
    provider = _PrewarmProvider()
    task = _maintenance_task(prewarm_symbols=("AAA",))
    runtime = _runtime(tmp_path, provider, task)
    try:
        first = runtime.collect_task(task, _MAINTENANCE_AT)
        first_requests = list(provider.history_requests)
        first_row_count = runtime.store.connection.execute(
            "SELECT COUNT(*) FROM equity_bar_revisions WHERE timeframe = '5m' AND symbol = 'AAA'"
        ).fetchone()[0]
        second = runtime.collect_task(task, _MAINTENANCE_AT)
        second_row_count = runtime.store.connection.execute(
            "SELECT COUNT(*) FROM equity_bar_revisions WHERE timeframe = '5m' AND symbol = 'AAA'"
        ).fetchone()[0]
    finally:
        runtime.close()

    assert first["history_prewarm"]["summary"]["completed"] == 1
    assert first["history_prewarm"]["summary"]["failed"] == 0
    assert first_requests
    assert {symbol for symbol, _day in first_requests} == {"AAA"}
    assert second["history_prewarm"]["summary"]["cache_hit"] == 1
    assert provider.history_requests == first_requests
    assert second_row_count == first_row_count >= 100
    assert provider.in_transaction_during_request
    assert not any(provider.in_transaction_during_request)


def test_prewarm_refs_feed_first_current_bar_macd_and_derivatives(tmp_path: Path) -> None:
    _content_sha256, enrich_indicator_history, hydrate_capture_from_path, resolved_capture_data = _backend_modules()
    provider = _PrewarmProvider()
    task = _maintenance_task(prewarm_symbols=("AAA",))
    runtime = _runtime(tmp_path, provider, task)
    try:
        result = runtime.collect_task(task, _MAINTENANCE_AT)
        assert result["history_prewarm"]["summary"]["completed"] == 1
        capture = _capture_for_history_ref(runtime)
        hydrated = hydrate_capture_from_path(runtime.store.path, capture)
        resolved = resolved_capture_data(hydrated)
        historical = [row for row in resolved["historical_5m_bars"] if row["symbol"] == "AAA"]
        current = _regular_5m_bars("AAA", _TRADING_DATE, close_offset=50.0)[0]
        enriched = enrich_indicator_history(
            [current],
            historical,
            symbol="AAA",
            trading_date=date.fromisoformat(_TRADING_DATE),
            cutoff=datetime.fromisoformat(current["bar_end_at"]),
            market_calendar="XNYS",
            adjustment="none",
        )
    finally:
        runtime.close()

    features = enriched["features"]
    macd = features["macd"]
    closes = [float(row["close"]) for row in [*historical, current]]
    expected_histogram, expected_delta, expected_delta2 = _reference_macd(closes)
    assert features["macd_warmup_bars"] >= 100
    assert features["macd_signal_eligible"] is True
    assert macd["histogram"] == pytest.approx(round(expected_histogram, 6))
    assert macd["histogram_delta"] == pytest.approx(round(expected_delta, 6))
    assert macd["histogram_delta2"] == pytest.approx(round(expected_delta2, 6))
    assert all(row["trading_date"] < _TRADING_DATE for row in historical)
    assert all(row["bar_end_at"] <= current["bar_start_at"] for row in historical)


def test_prewarm_failure_is_local_to_symbol_and_successful_rows_remain_resolvable(tmp_path: Path) -> None:
    provider = _PrewarmProvider(failures={"BBB"})
    task = _maintenance_task(prewarm_symbols=("AAA", "BBB"))
    runtime = _runtime(tmp_path, provider, task)
    try:
        result = runtime.collect_task(task, _MAINTENANCE_AT)
        aaa_count = runtime.store.connection.execute(
            "SELECT COUNT(*) FROM equity_bar_revisions WHERE timeframe = '5m' AND symbol = 'AAA'"
        ).fetchone()[0]
        bbb_count = runtime.store.connection.execute(
            "SELECT COUNT(*) FROM equity_bar_revisions WHERE timeframe = '5m' AND symbol = 'BBB'"
        ).fetchone()[0]
        audit = runtime.store.get_meta(f"intraday_history_prewarm:{_TRADING_DATE}")
    finally:
        runtime.close()

    summary = result["history_prewarm"]["summary"]
    assert summary["completed"] == 1
    assert summary["failed"] == 1
    assert aaa_count >= 100
    assert bbb_count == 0
    assert audit is not None
    assert {symbol for symbol, _day in provider.history_requests} == {"AAA", "BBB"}
