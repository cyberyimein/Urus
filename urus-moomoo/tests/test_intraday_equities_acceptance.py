"""Acceptance tests for the first bounded intraday-equity collection slice.

These tests deliberately use a fake OpenD SDK/context.  They describe the
small public boundary needed by the collector without opening a socket or
touching a deployment-owned task file.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import math
from pathlib import Path
from typing import Any

import pytest

from urus_moomoo.config import CollectionTask, CollectorConfig
from urus_moomoo.integrations.moomoo import OpenDMarketAdapter
from urus_moomoo.runtime import CollectorRuntime
from urus_moomoo.services.scheduler import CollectorScheduler
from urus_moomoo.services.quota import HistoryAdmission, HistoryCapacityService
from urus_moomoo.storage.sqlite import CollectorStore


class _NoopRateLimiter:
    def acquire_rate_slot(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _FakeSdk:
    RET_OK = 0

    class KLType:
        K_1M = "K_1M"

    class AuType:
        NONE = "None"


class _FakeEquityQuoteContext:
    """A deterministic SDK seam; any option call is an acceptance failure."""

    def __init__(self, *, remain_quota: int = 1000) -> None:
        self.remain_quota = remain_quota
        self.snapshot_calls: list[list[str]] = []
        self.history_calls: list[dict[str, Any]] = []
        self.option_calls: list[str] = []

    def get_market_snapshot(self, codes: list[str]) -> tuple[int, list[dict[str, Any]]]:
        self.snapshot_calls.append(list(codes))
        return 0, [
            {
                "code": code,
                "name": code,
                "last_price": 100.0,
                "prev_close_price": 99.0,
                "update_time": "2026-10-15 15:59:30",
            }
            for code in codes
        ]

    def request_history_kline(self, **kwargs: Any) -> tuple[int, list[dict[str, Any]]]:
        self.history_calls.append(dict(kwargs))
        code = str(kwargs["code"])
        return 0, [
            {
                "time_key": "2026-10-15 09:30:00",
                "open": 100.0,
                "high": 101.0,
                "low": 99.5,
                "close": 100.5,
                "volume": 10,
                "turnover": 1005.0,
                "code": code,
            },
            {
                "time_key": "2026-10-15 15:59:00",
                "open": 100.5,
                "high": 102.0,
                "low": 100.0,
                "close": 101.5,
                "volume": 12,
                "turnover": 1218.0,
                "code": code,
            },
        ]

    def get_option_expiration_date(self, *_args: Any, **_kwargs: Any) -> None:
        self.option_calls.append("get_option_expiration_date")
        raise AssertionError("intraday equities must not request option metadata")

    def get_option_chain(self, *_args: Any, **_kwargs: Any) -> None:
        self.option_calls.append("get_option_chain")
        raise AssertionError("intraday equities must not request option chains")

    def get_history_kl_quota(self, **_kwargs: Any) -> tuple[int, dict[str, Any]]:
        return 0, {
            "used_quota": 1000 - self.remain_quota,
            "remain_quota": self.remain_quota,
            "detail_list": [],
        }


class _CustomHistoryQuoteContext(_FakeEquityQuoteContext):
    """Fake OpenD context whose minute rows are supplied by each test."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        super().__init__(remain_quota=1000)
        self.rows = rows

    def request_history_kline(self, **kwargs: Any) -> tuple[int, list[dict[str, Any]]]:
        self.history_calls.append(dict(kwargs))
        return 0, [dict(row) for row in self.rows]


def _admission(symbols: list[str], *, remain: int) -> HistoryAdmission:
    service = HistoryCapacityService(reserve_absolute=0, reserve_ratio=0)
    admission = HistoryAdmission(
        service,
        quota_reader=lambda: {
            "available": True,
            "used": max(0, 1000 - remain),
            "remain": remain,
            "total": 1000,
        },
    )
    admission.prepare_symbols(symbols)
    return admission


def _adapter(
    context: _FakeEquityQuoteContext,
    symbols: list[str],
    *,
    remain: int,
    rate_limiter: Any | None = None,
) -> OpenDMarketAdapter:
    return OpenDMarketAdapter(
        host="loopback-fixture",
        port=0,
        market_symbols=symbols,
        history_days=20,
        sdk=_FakeSdk(),
        quote_context=context,
        history_admission=_admission(symbols, remain=remain),
        rate_limiter=rate_limiter or _NoopRateLimiter(),
        history_request_interval_seconds=0,
        snapshot_request_interval_seconds=0,
    )


def _minute_row(time_key: str, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "time_key": time_key,
        "open": 100.0,
        "high": 101.0,
        "low": 99.5,
        "close": 100.5,
        "volume": 10.0,
        "turnover": 1005.0,
    }
    row.update(overrides)
    return row


def _as_utc(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    return parsed.astimezone(UTC)


class _FailingHistoryQuoteContext(_FakeEquityQuoteContext):
    def __init__(self, failure_code: str) -> None:
        super().__init__(remain_quota=1000)
        self.failure_code = failure_code

    def request_history_kline(self, **kwargs: Any) -> tuple[int, object]:
        if str(kwargs["code"]) == self.failure_code:
            self.history_calls.append(dict(kwargs))
            return 1, "synthetic minute history failure"
        return super().request_history_kline(**kwargs)


class _RecordingRateLimiter(_NoopRateLimiter):
    def __init__(self) -> None:
        self.calls: list[tuple[str, float]] = []

    def acquire_rate_slot(self, rate_class: str, interval_seconds: float, **_kwargs: Any) -> None:
        self.calls.append((rate_class, interval_seconds))


class _FixedSessionCalendar:
    def is_session(self, value: Any) -> bool:
        return value.weekday() < 5

    def session_open(self, value: Any) -> datetime:
        return datetime(value.year, value.month, value.day, 14, 30, tzinfo=UTC)

    def session_close(self, value: Any) -> datetime:
        return datetime(value.year, value.month, value.day, 21, 0, tzinfo=UTC)


def _groups() -> dict[str, list[str]]:
    return {
        "A": ["AAA", "SHARED"],
        "B": ["BBB", "SHARED"],
        "C": ["CCC", "C_ONLY"],
        # Deliberately not alphabetic: D rotation must use a canonical,
        # repeatable order rather than configuration insertion order.
        "D": ["GGG", "DUP", "DDD", "EEE", "FFF"],
    }


def test_plan_quotes_full_pool_and_rotates_d_without_lowering_priority() -> None:
    from urus_moomoo.services.intraday_equities import build_intraday_equity_plan

    groups = _groups()
    plans = [build_intraday_equity_plan(groups, slot_index=index) for index in range(3)]
    all_symbols = {symbol for values in groups.values() for symbol in values}
    core = {"AAA", "SHARED", "BBB", "CCC", "C_ONLY"}
    d_only = sorted(set(groups["D"]) - core)

    for index, plan in enumerate(plans):
        assert set(plan["quote_symbols"]) == all_symbols
        assert len(plan["quote_symbols"]) == len(all_symbols)
        assert plan["group_by_symbol"]["SHARED"] == "A"
        assert plan["group_by_symbol"]["DUP"] == "D"
        assert core <= set(plan["minute_symbols"])
        expected_d = {
            symbol for position, symbol in enumerate(d_only) if position % 3 == index
        }
        assert set(plan["minute_symbols"]) - core == expected_d

    assert set().union(*(set(plan["minute_symbols"]) - core for plan in plans)) == set(d_only)
    assert set.intersection(*(set(plan["minute_symbols"]) - core for plan in plans)) == set()


def test_plan_is_stable_for_same_slot_and_has_no_quota_reservation_side_effect() -> None:
    from urus_moomoo.services.intraday_equities import build_intraday_equity_plan

    groups = _groups()
    first = build_intraday_equity_plan(groups, slot_index=7)
    second = build_intraday_equity_plan(groups, slot_index=7)
    assert first == second
    assert first["slot_index"] == 7
    # The pure planner only chooses symbols.  OpenD quota admission belongs to
    # the adapter's request-time HistoryAdmission gate.
    assert "quota" not in first
    assert "reservation" not in first


def test_plan_normalizes_us_prefixes_without_splitting_dot_tickers() -> None:
    from urus_moomoo.services.intraday_equities import build_intraday_equity_plan

    plan = build_intraday_equity_plan(
        {
            "A": ["US.AAPL", "BRK.B"],
            "B": ["AAPL", "US.BRK.B"],
            "C": ["MSFT"],
            "D": [],
        },
        slot_index=0,
        allowed_symbols=["AAPL", "BRK.B", "MSFT"],
    )

    assert set(plan["quote_symbols"]) == {"AAPL", "BRK.B", "MSFT"}
    assert plan["group_by_symbol"] == {"AAPL": "A", "BRK.B": "A", "MSFT": "C"}
    assert "BRK" not in plan["quote_symbols"]


def test_plan_rejects_groups_outside_task_symbol_scope() -> None:
    from urus_moomoo.services.intraday_equities import build_intraday_equity_plan

    with pytest.raises(ValueError, match="scope|allowed|task"):
        build_intraday_equity_plan(
            {"A": ["AAPL", "OUTSIDE"], "B": [], "C": [], "D": []},
            slot_index=0,
            allowed_symbols=["AAPL"],
        )


def test_plan_rejects_non_us_market_symbols_explicitly() -> None:
    from urus_moomoo.services.intraday_equities import build_intraday_equity_plan

    with pytest.raises(ValueError, match="US|market|市场"):
        build_intraday_equity_plan(
            {"A": ["HK.00700"], "B": [], "C": [], "D": []},
            slot_index=0,
            allowed_symbols=["HK.00700"],
        )


def test_adapter_batches_quote_pool_and_fetches_admitted_minute_symbols_one_by_one() -> None:
    quote_symbols = [f"EQ{index:03d}" for index in range(401)]
    minute_symbols = quote_symbols[:2]
    context = _FakeEquityQuoteContext(remain_quota=1000)
    adapter = _adapter(context, quote_symbols, remain=1000)
    try:
        payload = adapter.capture_intraday_equities(
            quote_symbols=quote_symbols,
            minute_symbols=minute_symbols,
            scheduled_at=datetime(2026, 10, 15, 20, 0, tzinfo=UTC),
        )
    finally:
        adapter.close()

    assert [len(batch) for batch in context.snapshot_calls] == [400, 1]
    assert all(len(batch) <= 400 for batch in context.snapshot_calls)
    assert {str(call["code"]) for call in context.history_calls} == {"US.EQ000", "US.EQ001"}
    assert len(context.history_calls) == len(minute_symbols)
    assert all(call["ktype"] == "K_1M" for call in context.history_calls)
    assert all(call["start"] == "2026-10-15" and call["end"] == "2026-10-15" for call in context.history_calls)
    assert context.option_calls == []

    assert set(payload["quote_symbols"]) == set(quote_symbols)
    assert set(payload["minute_symbols"]) == set(minute_symbols)
    assert {row["symbol"] for row in payload["minute_bars"]} == set(minute_symbols)
    times = {str(row["bar_time"]) for row in payload["minute_bars"]}
    assert {"2026-10-15 09:30:00", "2026-10-15 15:59:00"} <= times
    raw_batches = payload["provider_raw"]["market_snapshot_batches"]
    assert len(raw_batches) == 2
    assert all(len(batch["requested_codes"]) <= 400 for batch in raw_batches)
    assert len(payload["provider_raw"]["history_kline_batches"]) == len(minute_symbols)


def test_adapter_keeps_quotes_when_history_quota_is_exhausted() -> None:
    symbols = ["AAA", "BBB"]
    context = _FakeEquityQuoteContext(remain_quota=0)
    adapter = _adapter(context, symbols, remain=0)
    try:
        payload = adapter.capture_intraday_equities(
            quote_symbols=symbols,
            minute_symbols=symbols,
            scheduled_at=datetime(2026, 10, 15, 13, 35, tzinfo=UTC),
        )
    finally:
        adapter.close()

    assert len(context.snapshot_calls) == 1
    assert context.history_calls == []
    assert context.option_calls == []
    assert set(payload["quote_symbols"]) == set(symbols)
    assert {row["symbol"] for row in payload["quotes"]} == set(symbols)
    assert payload["minute_bars"] == []
    assert set(payload["minute_bar_unavailable_symbols"]) == set(symbols)
    assert payload["history_quota"]["deferred_symbols"] == symbols


def test_adapter_filters_minute_bars_after_the_scheduled_evidence_cutoff() -> None:
    symbols = ["AAA"]
    context = _FakeEquityQuoteContext(remain_quota=1000)
    adapter = _adapter(context, symbols, remain=1000)
    try:
        payload = adapter.capture_intraday_equities(
            quote_symbols=symbols,
            minute_symbols=symbols,
            scheduled_at=datetime(2026, 10, 15, 13, 35, tzinfo=UTC),
        )
    finally:
        adapter.close()

    # 13:35 UTC is 09:35 America/New_York on this date.  The 15:59
    # fixture bar must not leak into an earlier decision slot.
    times = {str(row["bar_time"]) for row in payload["minute_bars"]}
    assert times == {"2026-10-15 09:30:00"}
    assert payload["evidence_cutoff"] == "2026-10-15T13:35:00+00:00"


def test_adapter_explicitly_handles_start_and_end_minute_timestamp_roles() -> None:
    """Provider timestamps need an explicit role before UTC cutoff filtering."""

    cutoff = datetime(2026, 10, 15, 13, 35, tzinfo=UTC)
    cases = {
        "start": (
            [
                _minute_row("2026-10-15 09:34:00"),
                _minute_row("2026-10-15 09:35:00"),
            ],
            "2026-10-15 09:34:00",
            datetime(2026, 10, 15, 13, 34, tzinfo=UTC),
            datetime(2026, 10, 15, 13, 35, tzinfo=UTC),
        ),
        "end": (
            [
                _minute_row("2026-10-15 09:35:00"),
                _minute_row("2026-10-15 09:36:00"),
            ],
            "2026-10-15 09:35:00",
            datetime(2026, 10, 15, 13, 34, tzinfo=UTC),
            datetime(2026, 10, 15, 13, 35, tzinfo=UTC),
        ),
    }

    for role, (rows, included_bar_time, expected_start, expected_end) in cases.items():
        context = _CustomHistoryQuoteContext(rows)
        adapter = _adapter(context, ["AAA"], remain=1000)
        try:
            payload = adapter.capture_intraday_equities(
                quote_symbols=["AAA"],
                minute_symbols=["AAA"],
                scheduled_at=cutoff,
                evidence_cutoff=cutoff,
                minute_timestamp_role=role,
            )
        finally:
            adapter.close()

        assert payload["minute_timestamp_role"] == role
        bars = payload["minute_bars"]
        assert [bar["bar_time"] for bar in bars] == [included_bar_time]
        bar = bars[0]
        assert _as_utc(bar["bar_start_at"]) == expected_start
        assert _as_utc(bar["bar_end_at"]) == expected_end
        assert _as_utc(bar["bar_end_at"]) <= cutoff
        assert datetime.fromisoformat(str(bar["bar_start_at"]).replace("Z", "+00:00")).tzinfo is not None
        assert datetime.fromisoformat(str(bar["bar_end_at"]).replace("Z", "+00:00")).tzinfo is not None


def test_adapter_reports_partial_minute_failure_without_losing_other_symbols() -> None:
    symbols = ["AAA", "BBB"]
    context = _FailingHistoryQuoteContext("US.BBB")
    adapter = _adapter(context, symbols, remain=1000)
    try:
        payload = adapter.capture_intraday_equities(
            quote_symbols=symbols,
            minute_symbols=symbols,
            scheduled_at=datetime(2026, 10, 15, 20, 0, tzinfo=UTC),
        )
    finally:
        adapter.close()

    assert len(context.history_calls) == 2
    assert {row["symbol"] for row in payload["minute_bars"]} == {"AAA"}
    assert payload["minute_bar_unavailable_symbols"] == ["BBB"]
    assert payload["quality_summary"]["complete"] is False


def test_adapter_rejects_invalid_or_incomplete_minute_rows_instead_of_ready_defaults() -> None:
    rows = [
        _minute_row("2026-10-15 09:30:00"),
        _minute_row("2026-10-15 09:31:00", high=98.0, low=99.0),
        _minute_row("2026-10-15 09:32:00", close=math.nan),
        _minute_row("2026-10-15 09:33:00", open=math.inf),
        _minute_row("2026-10-15 09:34:00", volume=-1.0),
        _minute_row("2026-10-15 09:35:00", volume=None),
    ]
    rows[-1].pop("volume")
    context = _CustomHistoryQuoteContext(rows)
    adapter = _adapter(context, ["AAA"], remain=1000)
    try:
        payload = adapter.capture_intraday_equities(
            quote_symbols=["AAA"],
            minute_symbols=["AAA"],
            scheduled_at=datetime(2026, 10, 15, 20, 0, tzinfo=UTC),
        )
    finally:
        adapter.close()

    assert [bar["bar_time"] for bar in payload["minute_bars"]] == ["2026-10-15 09:30:00"]
    assert payload["quality_summary"]["minute_bars_returned"] == 1
    assert all(bar["quality_state"] == "ready" for bar in payload["minute_bars"])


def test_adapter_uses_one_shared_quote_history_pacing_class_for_all_provider_requests() -> None:
    symbols = ["AAA"]
    limiter = _RecordingRateLimiter()
    context = _FakeEquityQuoteContext(remain_quota=1000)
    adapter = _adapter(context, symbols, remain=1000, rate_limiter=limiter)
    try:
        adapter.capture_intraday_equities(
            quote_symbols=symbols,
            minute_symbols=symbols,
            scheduled_at=datetime(2026, 10, 15, 20, 0, tzinfo=UTC),
        )
    finally:
        adapter.close()

    assert len(limiter.calls) >= 2
    assert {rate_class for rate_class, _interval in limiter.calls} == {"moomoo_quote_history"}


def test_equity_scheduler_adds_stable_slot_index_without_option_parameters() -> None:
    task = CollectionTask(
        "intraday-equities",
        True,
        "intraday",
        ("AAA",),
        "capture_intraday_equities",
        params={
            "groups": {"A": ["AAA"], "B": [], "C": [], "D": []},
            "interval_seconds": 300,
            "start_margin_seconds": 0,
            "end_margin_seconds": 0,
        },
    )
    scheduler = CollectorScheduler(
        (task,),
        timezone="UTC",
        calendar=_FixedSessionCalendar(),
        runner=lambda *_args: {"completed": True},
    )

    due = scheduler.due(datetime(2026, 10, 15, 14, 36, tzinfo=UTC))
    assert [item.scheduled_at.strftime("%H:%M") for item in due] == ["14:30", "14:35"]
    assert [item.task.params["slot_index"] for item in due] == [0, 1]
    for item in due:
        assert "capture_tier" not in item.task.params
        assert "band_percent" not in item.task.params

    repeated = scheduler.due(datetime(2026, 10, 15, 14, 36, tzinfo=UTC))
    assert [item.task.params["slot_index"] for item in repeated] == [0, 1]


def test_same_slot_scheduled_raw_capture_is_bounded_and_completion_advances_once(tmp_path: Path) -> None:
    store = CollectorStore(tmp_path / "collector.sqlite")
    scheduled_at = datetime(2026, 10, 15, 14, 35, tzinfo=UTC)
    kind = "capture_intraday_equities"
    schema = "urus.moomoo.capture.v2"
    try:
        assert store.write_scheduled_raw(
            "intraday-equities",
            scheduled_at,
            "capture-1",
            kind,
            {"quality_summary": {"complete": False}, "attempt": 1},
            schema_version=schema,
            completed=False,
        ) is True
        assert store.write_scheduled_raw(
            "intraday-equities",
            scheduled_at,
            "capture-2",
            kind,
            {"quality_summary": {"complete": False}, "attempt": 2},
            schema_version=schema,
            completed=False,
        ) is False
        assert store.connection.execute(
            "SELECT COUNT(*) FROM raw_captures WHERE kind = ?", (kind,)
        ).fetchone()[0] == 1

        assert store.write_scheduled_raw(
            "intraday-equities",
            scheduled_at,
            "capture-3",
            kind,
            {"quality_summary": {"complete": True}, "attempt": 3},
            schema_version=schema,
            completed=True,
        ) is True
        assert store.connection.execute(
            "SELECT COUNT(*) FROM raw_captures WHERE kind = ?", (kind,)
        ).fetchone()[0] == 2
        assert store.scheduler_completed("intraday-equities", scheduled_at)
    finally:
        store.close()


def test_runtime_persists_equity_intraday_capture_as_raw_contract(tmp_path: Path) -> None:
    task = CollectionTask(
        "intraday-equities",
        True,
        "intraday",
        ("AAA", "BBB"),
        "capture_intraday_equities",
        params={
            "slot_index": 2,
            "groups": {"A": ["AAA"], "B": ["BBB"], "C": [], "D": []},
        },
    )

    class _Provider:
        def capture_intraday_equities(self, *, symbols: list[str], **params: Any) -> dict[str, Any]:
            minute_symbols = list(params.get("minute_symbols", symbols))
            return {
                "schema_version": "moomoo-intraday-equities-v1",
                "provider": "moomoo_openapi",
                "captured_at": "2026-10-15T13:31:01+00:00",
                "requested_symbols": symbols,
                "quote_symbols": symbols,
                "minute_symbols": minute_symbols,
                "quotes": [
                    {
                        "symbol": symbol,
                        "quote_code": f"US.{symbol}",
                        "last_price": 100.0,
                        "received_at": "2026-10-15T13:31:01+00:00",
                    }
                    for symbol in symbols
                ],
                "minute_bars": [
                    {
                        "symbol": symbol,
                        "bar_time": "2026-10-15 09:30:00",
                        "bar_start_at": "2026-10-15T13:30:00+00:00",
                        "bar_end_at": "2026-10-15T13:31:00+00:00",
                        "open": 99.9,
                        "high": 100.2,
                        "low": 99.8,
                        "close": 100.0,
                        "volume": 10.0,
                        "turnover": 1000.0,
                        "vwap": 100.0,
                        "provider": "moomoo_openapi",
                        "quality_state": "ready",
                    }
                    for symbol in minute_symbols
                ],
                "history_quota": {"deferred_symbols": []},
                "quality_summary": {
                    "complete": True,
                    "slot_index": params["slot_index"],
                    "minute_symbols_requested": len(minute_symbols),
                    "minute_bars_returned": len(minute_symbols),
                    "unavailable_minute_symbols": [],
                },
                "provider_raw": {"market_snapshot_batches": [], "history_kline_batches": []},
            }

        def close(self) -> None:
            return None

    scheduled_at = datetime(2026, 10, 15, 13, 31, tzinfo=UTC)
    runtime = CollectorRuntime(
        _Provider(),
        CollectorConfig(timezone="UTC", output_dir=tmp_path, tasks=(task,)),
    )
    try:
        result = runtime.collect_task(task, scheduled_at)
        row = runtime.store.read_raw(result["contract"]["capture_id"])
        assert row is not None
        stored = json.loads(str(row["payload_json"]))
        references = stored["data"]["equity_data_refs"]
        store_results = [runtime.store.equity_history.resolve_ref(ref) for ref in references]

        import sys

        backend_root = Path(__file__).resolve().parents[2] / "backend"
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))
        from app.intraday.references import hydrate_capture_from_path, resolved_capture_data

        hydrated = hydrate_capture_from_path(runtime.store.path, result["contract"])
        restored = resolved_capture_data(hydrated)
    finally:
        runtime.close()

    assert result["completed"] is True
    assert result["contract"]["kind"] == "capture_intraday_equities"
    assert row is not None
    stored = json.loads(str(row["payload_json"]))
    assert stored["schema_version"] == "urus.moomoo.capture.v2"
    assert stored["kind"] == "capture_intraday_equities"
    assert stored["data"]["schema_version"] == "moomoo-intraday-equities-refs-v1"
    assert stored["data"]["quality_summary"]["slot_index"] == 2
    assert stored["data"]["quality_summary"]["complete"] is True
    assert "minute_bars" not in stored["data"]
    assert "quotes" not in stored["data"]
    assert "provider_raw" not in stored["data"]

    assert len(references) == 3
    assert all(
        result["available"] == (reference["status"] == "available")
        for result, reference in zip(store_results, references, strict=True)
    )
    assert sum(len(result["rows"]) for result in store_results) == 4

    assert "minute_bars" not in hydrated["data"]
    assert "quotes" not in hydrated["data"]
    assert len(restored["minute_bars"]) == 2
    assert len(restored["historical_5m_bars"]) == 0
    assert len(restored["quotes"]) == 2
    assert {bar["symbol"] for bar in restored["minute_bars"]} == {"AAA", "BBB"}
    assert {bar["close"] for bar in restored["minute_bars"]} == {100.0}
    assert {quote["symbol"] for quote in restored["quotes"]} == {"AAA", "BBB"}
    assert {quote["last_price"] for quote in restored["quotes"]} == {100.0}
    assert restored["quality_summary"]["complete"] is True


def test_runtime_keeps_scheduled_slot_authoritative_over_task_params(tmp_path: Path) -> None:
    trusted_scheduled_at = datetime(2026, 10, 15, 14, 35, tzinfo=UTC)
    calls: list[dict[str, Any]] = []

    class _Provider:
        def capture_intraday_equities(self, *, symbols: list[str], **params: Any) -> dict[str, Any]:
            calls.append(dict(params))
            return {
                "schema_version": "moomoo-intraday-equities-v1",
                "provider": "moomoo_openapi",
                "requested_symbols": symbols,
                "quote_symbols": symbols,
                "minute_symbols": symbols,
                "quotes": [],
                "minute_bars": [],
                "history_quota": {"deferred_symbols": []},
                "quality_summary": {"complete": True},
                "provider_raw": {},
            }

        def close(self) -> None:
            return None

    task = CollectionTask(
        "intraday-authoritative-slot",
        True,
        "intraday",
        ("AAA",),
        "capture_intraday_equities",
        params={
            "trading_date": "1999-01-01",
            "scheduled_at": datetime(1999, 1, 1, tzinfo=UTC),
            "evidence_cutoff": datetime(1999, 1, 1, tzinfo=UTC),
        },
    )
    runtime = CollectorRuntime(
        _Provider(),
        CollectorConfig(timezone="UTC", output_dir=tmp_path, tasks=(task,)),
    )
    try:
        try:
            runtime.collect_task(task, trusted_scheduled_at)
        except ValueError as exc:
            # Rejecting conflicting slot fields is as safe as overwriting them
            # with the scheduler's authoritative values.
            assert "scheduled" in str(exc).lower() or "cutoff" in str(exc).lower() or "trading" in str(exc).lower()
            return
    finally:
        runtime.close()

    assert len(calls) == 1
    params = calls[0]
    assert str(params["trading_date"]) == "2026-10-15"
    assert params["scheduled_at"] == trusted_scheduled_at
    assert params["evidence_cutoff"] == trusted_scheduled_at


def test_runtime_reuses_stored_capture_when_same_incomplete_slot_is_retried(tmp_path: Path) -> None:
    task = CollectionTask(
        "intraday-idempotent",
        True,
        "intraday",
        ("AAA",),
        "capture_intraday_equities",
        params={},
    )
    attempts = 0

    class _Provider:
        def capture_intraday_equities(self, *, symbols: list[str], **_params: Any) -> dict[str, Any]:
            nonlocal attempts
            attempts += 1
            return {
                "schema_version": "moomoo-intraday-equities-v1",
                "provider": "moomoo_openapi",
                "requested_symbols": symbols,
                "quote_symbols": symbols,
                "minute_symbols": symbols,
                "quotes": [],
                "minute_bars": [],
                "history_quota": {"deferred_symbols": symbols},
                "quality_summary": {"complete": False, "attempt": attempts},
                "provider_raw": {},
            }

        def close(self) -> None:
            return None

    scheduled_at = datetime(2026, 10, 15, 14, 35, tzinfo=UTC)
    runtime = CollectorRuntime(
        _Provider(),
        CollectorConfig(timezone="UTC", output_dir=tmp_path, tasks=(task,)),
    )
    try:
        first = runtime.collect_task(task, scheduled_at)
        second = runtime.collect_task(task, scheduled_at)
        first_id = str(first["contract"]["capture_id"])
        second_id = str(second["contract"]["capture_id"])
        ledger = runtime.store.connection.execute(
            "SELECT capture_id, completed FROM scheduled_captures "
            "WHERE task_name = ? AND scheduled_at = ?",
            (task.name, scheduled_at.isoformat()),
        ).fetchone()
        assert ledger is not None
        assert bool(ledger["completed"]) is False
        assert first_id == second_id == str(ledger["capture_id"])
        assert runtime.store.read_raw(first_id) is not None
        assert runtime.store.read_raw(second_id) is not None
        assert runtime.store.scheduler_completed(task.name, scheduled_at) is False
    finally:
        runtime.close()


def test_existing_intraday_options_operation_still_dispatches(tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    class _Provider:
        def capture_intraday(self, *, symbols: list[str], **params: Any) -> dict[str, Any]:
            calls.append((symbols, params))
            return {"symbols": symbols, "quality_summary": {"complete": False}}

        def close(self) -> None:
            return None

    task = CollectionTask(
        "legacy-intraday-options",
        True,
        "intraday",
        ("SPY",),
        "capture_intraday",
        params={"capture_tier": "fast", "band_percent": 3},
    )
    runtime = CollectorRuntime(
        _Provider(),
        CollectorConfig(timezone="UTC", output_dir=tmp_path, tasks=(task,)),
    )
    try:
        result = runtime.collect_task(task, datetime(2026, 10, 15, 13, 30, tzinfo=UTC))
    finally:
        runtime.close()

    assert calls == [(["SPY"], {"capture_tier": "fast", "band_percent": 3})]
    assert result["completed"] is False
    assert result["contract"]["kind"] == "capture_intraday"
