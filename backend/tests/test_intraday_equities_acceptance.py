"""P3 red tests for the read-only intraday equity evidence slice.

The fixtures use the final P2 bar contract: every minute has explicit,
timezone-aware UTC ``bar_start_at`` and ``bar_end_at``.  No test reconstructs
intervals from the legacy provider ``bar_time`` string.

These tests intentionally define the smallest provider-independent seam for
the P3 implementation.  They do not call OpenD, a remote API, or a source
SQLite database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
from typing import Any, Mapping, Sequence

import pytest


SESSION_OPEN = datetime(2026, 10, 15, 13, 30, tzinfo=UTC)
SESSION_CLOSE = datetime(2026, 10, 15, 20, 0, tzinfo=UTC)
CAPTURE_CUTOFF = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)


def test_intraday_equities_feature_gate_defaults_disabled() -> None:
    from app.core.config import Settings

    assert Settings().intraday_equities_enabled is False


def _minute_rows(
    symbol: str = "AAA",
    *,
    count: int = 30,
    start: datetime = SESSION_OPEN,
    missing: set[int] | None = None,
    close_offset: float = 0.0,
) -> list[dict[str, Any]]:
    missing = missing or set()
    rows: list[dict[str, Any]] = []
    for index in range(count):
        if index in missing:
            continue
        bar_start = start + timedelta(minutes=index)
        bar_end = bar_start + timedelta(minutes=1)
        close = 100.0 + close_offset + index * 0.1
        volume = 100.0 + index
        rows.append(
            {
                "symbol": symbol,
                "bar_start_at": bar_start.isoformat(),
                "bar_end_at": bar_end.isoformat(),
                "open": close - 0.05,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": volume,
                "turnover": volume * close,
                "provider": "moomoo_openapi",
                "quality_state": "ready",
            }
        )
    return rows


def _bars(result: object) -> list[dict[str, Any]]:
    if isinstance(result, list):
        return [dict(item) for item in result if isinstance(item, Mapping)]
    if isinstance(result, Mapping):
        for key in ("bars_5m", "completed_bars", "bars"):
            value = result.get(key)
            if isinstance(value, list):
                return [dict(item) for item in value if isinstance(item, Mapping)]
    raise AssertionError(f"aggregate_completed_bars returned no 5m bars: {result!r}")


def _capture_contract(
    minute_bars: Sequence[Mapping[str, Any]],
    *,
    benchmark_bars: Sequence[Mapping[str, Any]] = (),
    cutoff: datetime | None = None,
    captured_at: datetime | None = None,
    quality_complete: bool = True,
) -> dict[str, Any]:
    from app.decision_harness.contracts import content_sha256

    # Keep source timestamps coherent when a fixture extends beyond 14:00.
    if cutoff is None:
        all_bars = [*minute_bars, *benchmark_bars]
        cutoff = (
            max(
                datetime.fromisoformat(str(row["bar_end_at"]))
                for row in all_bars
                if row.get("bar_end_at")
            )
            if all_bars
            else CAPTURE_CUTOFF
        )
    captured_at = captured_at or max(cutoff, CAPTURE_CUTOFF)

    data = {
        "schema_version": "moomoo-intraday-equities-v1",
        "provider": "moomoo_openapi",
        "trading_date": "2026-10-15",
        "scheduled_at": cutoff.isoformat(),
        "evidence_cutoff": cutoff.isoformat(),
        "minute_timestamp_role": "start",
        "minute_timestamp_policy": {
            "source_fields": ["bar_start_at", "bar_end_at"],
            "role": "start",
            "interval_seconds": 60,
            "provider_boundary_verified": False,
        },
        "slot_index": 0,
        "rotation": 0,
        "quote_symbols": ["AAA", "SPY"],
        "minute_symbols": ["AAA", "SPY"],
        "group_by_symbol": {"AAA": "C", "SPY": "A"},
        "quotes": [
            {"symbol": "AAA", "last_price": 102.0},
            {"symbol": "SPY", "last_price": 500.0},
        ],
        "minute_bars": [dict(item) for item in [*minute_bars, *benchmark_bars]],
        "invalid_minute_bars": [],
        "minute_bar_unavailable_symbols": [],
        "unavailable_quote_symbols": [],
        "history_quota": {
            "available": True,
            "requested_symbols": ["AAA", "SPY"],
            "admitted_symbols": ["AAA", "SPY"],
            "deferred_symbols": [],
            "admissions": [],
        },
        "quality_summary": {
            "complete": quality_complete,
            "status": "ok" if quality_complete else "partial",
            "quote_coverage": 1.0,
            "minute_symbols_requested": 2,
            "minute_symbols_admitted": 2,
            "minute_bars_returned": len(minute_bars) + len(benchmark_bars),
            "unavailable_minute_symbols": [],
            "quality_warnings": [],
        },
        "provider_raw": {
            "market_snapshot_batches": [],
            "history_kline_batches": [],
        },
    }
    return {
        "schema_version": "urus.moomoo.capture.v2",
        "capture_id": "capture-p3-1",
        "kind": "capture_intraday_equities",
        "task_name": "intraday-equities",
        "provider": "moomoo_openapi",
        "scheduled_at": cutoff.isoformat(),
        "trading_date": "2026-10-15",
        "captured_at": captured_at.isoformat(),
        "requested_symbols": ["AAA", "SPY"],
        "content_sha256": content_sha256(data),
        "data": data,
    }


def _symbol_projection(projection: Mapping[str, Any], symbol: str) -> Mapping[str, Any]:
    symbols = projection.get("symbols")
    if isinstance(symbols, Mapping) and isinstance(symbols.get(symbol), Mapping):
        return symbols[symbol]
    if isinstance(symbols, list):
        for item in symbols:
            if isinstance(item, Mapping) and str(item.get("symbol")) == symbol:
                return item
    raise AssertionError(f"projection has no symbol {symbol}: {projection!r}")


def _features(projection: Mapping[str, Any], symbol: str = "AAA") -> Mapping[str, Any]:
    item = _symbol_projection(projection, symbol)
    features = item.get("features")
    assert isinstance(features, Mapping), item
    return features


def _refresh_contract_hash(contract: dict[str, Any]) -> None:
    from app.decision_harness.contracts import content_sha256

    contract["content_sha256"] = content_sha256(contract["data"])


def _reasons(projection: Mapping[str, Any], symbol: str = "AAA") -> Mapping[str, Any]:
    item = _symbol_projection(projection, symbol)
    reasons = item.get("reasons")
    assert isinstance(reasons, Mapping), item
    return reasons


def test_aggregate_completed_bars_aligns_utc_minutes_to_session_anchored_five_minute_windows() -> None:
    from app.intraday.equities import aggregate_completed_bars

    result = aggregate_completed_bars(
        _minute_rows(count=10),
        session_open=SESSION_OPEN,
        session_close=SESSION_CLOSE,
        cutoff=CAPTURE_CUTOFF,
    )
    bars = _bars(result)

    assert [bar["bar_start_at"] for bar in bars] == [
        "2026-10-15T13:30:00+00:00",
        "2026-10-15T13:35:00+00:00",
    ]
    assert [bar["bar_end_at"] for bar in bars] == [
        "2026-10-15T13:35:00+00:00",
        "2026-10-15T13:40:00+00:00",
    ]
    assert bars[0]["open"] == pytest.approx(99.95)
    assert bars[0]["high"] == pytest.approx(100.6)
    assert bars[0]["low"] == pytest.approx(99.8)
    assert bars[0]["close"] == pytest.approx(100.4)
    assert bars[0]["volume"] == pytest.approx(sum(100 + index for index in range(5)))
    assert bars[0]["quality_state"] == "ready"


def test_aggregate_drops_future_or_incomplete_five_minute_window_at_cutoff() -> None:
    from app.intraday.equities import aggregate_completed_bars

    bars = _bars(
        aggregate_completed_bars(
            _minute_rows(count=10),
            session_open=SESSION_OPEN,
            session_close=SESSION_CLOSE,
            cutoff=datetime(2026, 10, 15, 13, 39, tzinfo=UTC),
        )
    )

    assert [bar["bar_end_at"] for bar in bars] == ["2026-10-15T13:35:00+00:00"]


def test_aggregate_does_not_fill_missing_minutes_or_compress_across_a_gap() -> None:
    from app.intraday.equities import aggregate_completed_bars

    bars = _bars(
        aggregate_completed_bars(
            _minute_rows(count=10, missing={2}),
            session_open=SESSION_OPEN,
            session_close=SESSION_CLOSE,
            cutoff=CAPTURE_CUTOFF,
        )
    )

    assert [bar["bar_start_at"] for bar in bars] == ["2026-10-15T13:35:00+00:00"]


def test_aggregate_rejects_conflicting_duplicates_and_invalid_ohlcv() -> None:
    from app.intraday.equities import aggregate_completed_bars

    rows = _minute_rows(count=5)
    conflicting = dict(rows[2])
    conflicting["close"] = conflicting["close"] + 5.0
    invalid = dict(rows[3])
    invalid["volume"] = -1.0
    rows.extend([conflicting, invalid])

    bars = _bars(
        aggregate_completed_bars(
            rows,
            session_open=SESSION_OPEN,
            session_close=SESSION_CLOSE,
            cutoff=CAPTURE_CUTOFF,
        )
    )

    assert bars == []


def test_projection_exposes_session_vwap_only_for_complete_regular_session_minutes() -> None:
    from app.intraday.equities import build_intraday_projection

    projection = build_intraday_projection(
        _capture_contract(_minute_rows(count=30)),
        now=datetime(2026, 10, 15, 14, 0, tzinfo=UTC),
    )
    features = _features(projection)
    vwap = features["session_vwap"]

    assert vwap == pytest.approx(
        sum(row["turnover"] for row in _minute_rows(count=30))
        / sum(row["volume"] for row in _minute_rows(count=30))
    )
    assert "session_vwap" not in _reasons(projection)


def test_projection_marks_session_vwap_unavailable_when_an_input_minute_is_missing() -> None:
    from app.intraday.equities import build_intraday_projection

    projection = build_intraday_projection(
        _capture_contract(_minute_rows(count=30, missing={7})),
        now=datetime(2026, 10, 15, 14, 0, tzinfo=UTC),
    )
    vwap = _features(projection)["session_vwap"]

    assert vwap is None
    assert _reasons(projection)["session_vwap"]


def test_projection_ma5_has_five_complete_bar_warmup_and_no_forward_fill() -> None:
    from app.intraday.equities import aggregate_completed_bars
    from app.intraday.equities import build_intraday_projection

    minute_rows = _minute_rows(count=25)
    projection = build_intraday_projection(
        _capture_contract(minute_rows),
        now=datetime(2026, 10, 15, 14, 0, tzinfo=UTC),
    )
    features = _features(projection)
    bars = _bars(
        aggregate_completed_bars(
            minute_rows,
            session_open=SESSION_OPEN,
            session_close=SESSION_CLOSE,
            cutoff=SESSION_OPEN + timedelta(minutes=25),
        )
    )
    expected_ma5 = sum(float(bar["close"]) for bar in bars[-5:]) / 5
    assert features["ma5"] is not None
    assert features["ma5"] == pytest.approx(expected_ma5)
    assert features["ma5_distance_pct"] is not None
    assert features["ma5_slope"] is None

    minute_rows_with_slope = _minute_rows(count=30)
    projection_slope = build_intraday_projection(
        _capture_contract(minute_rows_with_slope),
        now=datetime(2026, 10, 15, 14, 0, tzinfo=UTC),
    )
    slope_features = _features(projection_slope)
    slope_bars = _bars(
        aggregate_completed_bars(
            minute_rows_with_slope,
            session_open=SESSION_OPEN,
            session_close=SESSION_CLOSE,
            cutoff=SESSION_OPEN + timedelta(minutes=30),
        )
    )
    expected_slope = (
        sum(float(bar["close"]) for bar in slope_bars[-5:]) / 5
        - sum(float(bar["close"]) for bar in slope_bars[-6:-1]) / 5
    )
    assert slope_features["ma5"] is not None
    assert slope_features["ma5_distance_pct"] is not None
    assert slope_features["ma5_slope"] == pytest.approx(expected_slope)

    short_projection = build_intraday_projection(
        _capture_contract(_minute_rows(count=4 * 5)),
        now=datetime(2026, 10, 15, 14, 0, tzinfo=UTC),
    )
    short_features = _features(short_projection)
    assert short_features["ma5"] is None
    assert short_features["ma5_distance_pct"] is None
    assert short_features["ma5_slope"] is None


def test_projection_uses_wilder_atr14_and_reports_distance_slope_without_zero_fallback() -> None:
    from app.intraday.equities import aggregate_completed_bars
    from app.intraday.equities import build_intraday_projection

    minute_rows = _minute_rows(count=16 * 5)
    for index, amplitude in ((14 * 5, 2.0), (15 * 5, 5.0)):
        for row in minute_rows[index : index + 5]:
            close = float(row["close"])
            row["high"] = close + amplitude
            row["low"] = close - amplitude
    projection = build_intraday_projection(
        _capture_contract(minute_rows),
        now=datetime(2026, 10, 15, 14, 50, tzinfo=UTC),
    )
    features = _features(projection)
    atr = features["atr14"]

    bars = _bars(
        aggregate_completed_bars(
            minute_rows,
            session_open=SESSION_OPEN,
            session_close=SESSION_CLOSE,
            cutoff=SESSION_OPEN + timedelta(minutes=16 * 5),
        )
    )
    true_ranges: list[float] = []
    previous_close: float | None = None
    for bar in bars:
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
        true_ranges.append(
            high - low
            if previous_close is None
            else max(high - low, abs(high - previous_close), abs(low - previous_close))
        )
        previous_close = close
    expected_atr = sum(true_ranges[:14]) / 14
    for true_range in true_ranges[14:16]:
        expected_atr = (expected_atr * 13 + true_range) / 14
    trailing_sma = sum(true_ranges[-14:]) / 14

    assert atr == pytest.approx(expected_atr)
    assert abs(float(atr) - trailing_sma) > 1e-3
    assert isinstance(atr, (int, float))

    short_projection = build_intraday_projection(
        _capture_contract(_minute_rows(count=13 * 5)),
        now=datetime(2026, 10, 15, 14, 35, tzinfo=UTC),
    )
    assert _features(short_projection)["atr14"] is None


def test_projection_reuses_macd_core_and_keeps_34_35_36_warmup_distinct() -> None:
    from app.analytics.technical import calculate_technical_indicators
    from app.intraday.equities import build_intraday_projection

    rows = _minute_rows(count=78 * 5)
    projection = build_intraday_projection(
        _capture_contract(rows),
        now=SESSION_CLOSE,
    )
    features = _features(projection)
    macd = features["macd"]
    bars = _symbol_projection(projection, "AAA")["bars"]
    closes = [
        {
            "date": row["bar_end_at"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
        }
        for row in bars
    ]
    expected = calculate_technical_indicators(closes, source="p3-test")["macd_12_26_9"]

    assert macd["histogram"] == pytest.approx(expected["histogram"])
    assert macd["histogram_delta"] == pytest.approx(expected["histogram_delta"])
    assert macd["histogram_delta2"] == pytest.approx(expected["histogram_delta2"])
    assert features["macd_warmup_bars"] == 78
    assert features["macd_signal_eligible"] is False

    def latest_macd(sample_bars: int) -> Mapping[str, Any]:
        return _features(
            build_intraday_projection(
                _capture_contract(_minute_rows(count=sample_bars * 5)),
                now=SESSION_CLOSE,
            )
        )["macd"]

    assert latest_macd(33)["histogram"] is None
    assert latest_macd(34)["histogram"] is not None
    assert latest_macd(34)["histogram_delta"] is None
    assert latest_macd(35)["histogram_delta"] is not None
    assert latest_macd(35)["histogram_delta2"] is None
    assert latest_macd(36)["histogram_delta2"] is not None


def test_projection_macd_value_can_exist_while_strategy_warmup_is_incomplete_for_78_bars() -> None:
    from app.intraday.equities import build_intraday_projection

    projection = build_intraday_projection(
        _capture_contract(_minute_rows(count=78 * 5)),
        now=SESSION_CLOSE,
    )
    macd = _features(projection)["macd"]

    assert macd["histogram"] is not None
    assert macd["histogram_delta"] is not None
    assert macd["histogram_delta2"] is not None
    assert _features(projection)["macd_warmup_bars"] == 78
    assert _features(projection)["macd_signal_eligible"] is False


def test_projection_aligns_rs15_rs30_to_common_symbol_and_spy_endpoints() -> None:
    from app.intraday.equities import build_intraday_projection

    symbol = _minute_rows("AAA", count=40, close_offset=0.0)
    benchmark = _minute_rows("SPY", count=40, close_offset=10.0)
    projection = build_intraday_projection(
        _capture_contract(symbol, benchmark_bars=benchmark),
        now=datetime(2026, 10, 15, 14, 10, tzinfo=UTC),
    )
    features = _features(projection)

    assert features["rs15"] is not None
    assert features["rs30"] is not None


def test_projection_does_not_turn_missing_spy_or_rvol_history_into_zero() -> None:
    from app.intraday.equities import build_intraday_projection

    projection = build_intraday_projection(
        _capture_contract(_minute_rows("AAA", count=40)),
        now=datetime(2026, 10, 15, 14, 10, tzinfo=UTC),
    )
    features = _features(projection)

    assert features["rs15"] is None
    assert features["rs30"] is None
    assert "benchmark" in str(_reasons(projection)["rs15"]).lower()
    assert "benchmark" in str(_reasons(projection)["rs30"]).lower()
    assert features["rvol"] is None
    assert _reasons(projection)["rvol"]


@pytest.mark.parametrize(
    ("update_time", "expected_quote_time", "expected_status"),
    [
        ("2026-10-15 10:00:03", "2026-10-15T14:00:03+00:00", "known"),
        (None, None, "unknown"),
        ("not-a-provider-time", None, "unknown"),
    ],
)
def test_projection_quote_uses_sdk_update_time_without_borrowing_slot_cutoff(
    update_time: str | None,
    expected_quote_time: str | None,
    expected_status: str,
) -> None:
    from app.intraday.equities import build_intraday_projection

    contract = _capture_contract(_minute_rows(count=30), cutoff=CAPTURE_CUTOFF)
    contract["data"]["quotes"][0]["update_time"] = update_time
    _refresh_contract_hash(contract)
    projection = build_intraday_projection(contract, now=CAPTURE_CUTOFF)

    quote = _symbol_projection(projection, "AAA")["quote"]
    assert quote["quote_time"] == expected_quote_time
    assert quote["quote_time_status"] == expected_status
    if expected_status == "unknown":
        # The scheduled cutoff is not a quote observation time.
        assert quote["quote_time"] != CAPTURE_CUTOFF.isoformat()


def test_projection_rejects_nonfinite_or_negative_input_instead_of_publishing_ready_features() -> None:
    from app.intraday.equities import build_intraday_projection

    rows = _minute_rows("AAA", count=10)
    rows[2]["close"] = math.inf
    rows[3]["volume"] = -1.0
    projection = build_intraday_projection(
        _capture_contract(rows),
        now=datetime(2026, 10, 15, 14, 0, tzinfo=UTC),
    )

    assert projection["coverage"]["status"] in {"partial", "invalid"}
    assert projection["coverage"]["invalid_minute_bars"] >= 2
    assert _features(projection)["ma5"] is None


@pytest.mark.parametrize("field", ["schema_version", "provider"])
def test_projection_rejects_data_schema_or_provider_mismatch(field: str) -> None:
    from app.intraday.equities import build_intraday_projection

    contract = _capture_contract(_minute_rows(count=10))
    contract["data"][field] = "unexpected"
    _refresh_contract_hash(contract)

    with pytest.raises(ValueError):
        build_intraday_projection(contract, now=CAPTURE_CUTOFF)


def test_projection_rejects_data_scheduled_at_mismatch() -> None:
    from app.intraday.equities import build_intraday_projection

    contract = _capture_contract(_minute_rows(count=10))
    contract["data"]["scheduled_at"] = (CAPTURE_CUTOFF + timedelta(minutes=1)).isoformat()
    _refresh_contract_hash(contract)

    with pytest.raises(ValueError):
        build_intraday_projection(contract, now=CAPTURE_CUTOFF)


def test_projection_rejects_cutoff_after_capture_receipt() -> None:
    from app.intraday.equities import build_intraday_projection

    contract = _capture_contract(_minute_rows(count=10))
    contract["data"]["evidence_cutoff"] = (CAPTURE_CUTOFF + timedelta(minutes=5)).isoformat()
    _refresh_contract_hash(contract)

    with pytest.raises(ValueError):
        build_intraday_projection(contract, now=CAPTURE_CUTOFF)


def test_projection_rejects_trading_date_that_does_not_match_slot_in_market_timezone() -> None:
    from app.intraday.equities import build_intraday_projection

    contract = _capture_contract(_minute_rows(count=10))
    contract["trading_date"] = "2026-10-14"
    contract["data"]["trading_date"] = "2026-10-14"
    _refresh_contract_hash(contract)

    with pytest.raises(ValueError):
        build_intraday_projection(contract, now=CAPTURE_CUTOFF)


def test_projection_rejects_minute_data_outside_envelope_requested_symbols() -> None:
    from app.intraday.equities import build_intraday_projection

    contract = _capture_contract(_minute_rows(count=10))
    extra = dict(_minute_rows("EVIL", count=5)[0])
    contract["data"]["minute_bars"].append(extra)
    _refresh_contract_hash(contract)

    with pytest.raises(ValueError):
        build_intraday_projection(contract, now=CAPTURE_CUTOFF)


def test_projection_marks_tail_gap_stale_instead_of_reusing_old_value_at_cutoff() -> None:
    from app.intraday.equities import build_intraday_projection

    cutoff = SESSION_OPEN + timedelta(minutes=10)
    rows = _minute_rows(count=10, missing={7})
    projection = build_intraday_projection(
        _capture_contract(rows, cutoff=cutoff),
        now=cutoff,
    )
    symbol = _symbol_projection(projection, "AAA")
    status = str(symbol.get("status") or projection.get("status") or projection["coverage"].get("status"))
    assert status in {"partial", "stale", "unavailable"}
    as_of = symbol.get("as_of") or symbol.get("feature_as_of")
    assert as_of is not None
    assert datetime.fromisoformat(str(as_of)) < cutoff


def test_projection_does_not_cross_tail_gap_for_macd_signals() -> None:
    from app.intraday.equities import build_intraday_projection

    cutoff = SESSION_OPEN + timedelta(minutes=36 * 5)
    rows = _minute_rows(count=36 * 5, missing={36 * 5 - 1})
    projection = build_intraday_projection(
        _capture_contract(rows, cutoff=cutoff),
        now=cutoff,
    )
    symbol = _symbol_projection(projection, "AAA")
    features = _features(projection)
    status = str(symbol.get("status") or projection.get("status") or projection["coverage"].get("status"))
    assert status in {"partial", "stale", "unavailable"}
    as_of = symbol.get("as_of") or symbol.get("feature_as_of")
    assert as_of is not None
    assert datetime.fromisoformat(str(as_of)) < cutoff
    macd = features["macd"]
    if macd["histogram"] is not None:
        macd_as_of = (
            macd.get("as_of")
            or features.get("macd_as_of")
            or symbol.get("as_of")
            or symbol.get("feature_as_of")
        )
        assert macd_as_of is not None
        assert datetime.fromisoformat(str(macd_as_of)) < cutoff


def test_projection_freshness_uses_evidence_cutoff_when_capture_arrives_late() -> None:
    from app.intraday.equities import build_intraday_projection

    cutoff = SESSION_OPEN + timedelta(minutes=10)
    received_at = SESSION_OPEN + timedelta(minutes=30)
    projection = build_intraday_projection(
        _capture_contract(
            _minute_rows(count=10),
            cutoff=cutoff,
            captured_at=received_at,
        ),
        now=SESSION_OPEN + timedelta(minutes=31),
    )

    assert projection["freshness"]["status"] == "stale"
    assert projection["freshness"]["age_seconds"] >= 20 * 60
    assert projection["status"] == "stale"


def test_projection_requires_continuous_rs_windows_not_only_matching_endpoints() -> None:
    from app.intraday.equities import build_intraday_projection

    # The gap is 15 minutes after the open.  It remains inside the latest
    # seven 5-minute bars (RS30) but is before the latest four bars (RS15),
    # so only the shorter close-to-close window stays eligible.
    symbol = _minute_rows("AAA", count=40, missing={15})
    benchmark = _minute_rows("SPY", count=40, close_offset=10.0)
    projection = build_intraday_projection(
        _capture_contract(symbol, benchmark_bars=benchmark),
        now=datetime(2026, 10, 15, 14, 10, tzinfo=UTC),
    )
    features = _features(projection)

    assert features["rs15"] is not None
    assert features["rs30"] is None
    assert _reasons(projection)["rs30"]


def test_projection_rs_uses_close_to_close_returns_over_four_and_seven_bars() -> None:
    from app.intraday.equities import build_intraday_projection

    symbol = _minute_rows("AAA", count=40)
    benchmark = _minute_rows("SPY", count=40, close_offset=10.0)
    # Make open-to-close and close-to-close produce different results.  These
    # opens sit at the first bar of each RS window; the closing prices remain
    # the independent reference series.
    for index, jump in ((5, 4.0), (20, 7.0)):
        row = symbol[index]
        row["open"] = float(row["close"]) + jump
        row["high"] = max(float(row["high"]), float(row["open"]) + 0.1)

    projection = build_intraday_projection(
        _capture_contract(symbol, benchmark_bars=benchmark),
        now=datetime(2026, 10, 15, 14, 10, tzinfo=UTC),
    )
    features = _features(projection)
    symbol_bars = _symbol_projection(projection, "AAA")["bars"]
    benchmark_bars = _symbol_projection(projection, "SPY")["bars"]

    def expected_excess(count: int) -> float:
        symbol_return = (
            float(symbol_bars[-1]["close"]) / float(symbol_bars[-count]["close"]) - 1.0
        ) * 100.0
        benchmark_return = (
            float(benchmark_bars[-1]["close"])
            / float(benchmark_bars[-count]["close"])
            - 1.0
        ) * 100.0
        return symbol_return - benchmark_return

    assert features["rs15"] == pytest.approx(expected_excess(4))
    assert features["rs30"] == pytest.approx(expected_excess(7))


def test_projection_breadth_excludes_symbol_without_complete_session_open_window() -> None:
    from app.intraday.equities import build_intraday_projection

    projection = build_intraday_projection(
        _capture_contract(
            _minute_rows("AAA", count=30, missing=set(range(5))),
            benchmark_bars=_minute_rows("SPY", count=30),
        ),
        now=datetime(2026, 10, 15, 14, 0, tzinfo=UTC),
    )

    breadth = projection["breadth"]
    assert breadth["requested_count"] == 2
    assert breadth["effective_denominator"] == 1


def test_projection_breadth_excludes_symbol_whose_latest_window_is_incomplete() -> None:
    from app.intraday.equities import build_intraday_projection

    projection = build_intraday_projection(
        _capture_contract(
            _minute_rows("AAA", count=30, missing={29}),
            benchmark_bars=_minute_rows("SPY", count=30),
        ),
        now=datetime(2026, 10, 15, 14, 0, tzinfo=UTC),
    )

    breadth = projection["breadth"]
    assert breadth["requested_count"] == 2
    assert breadth["effective_denominator"] == 1


def _api_capture_contract(
    capture_id: str,
    scheduled_at: str,
    *,
    minute_bars: Sequence[Mapping[str, Any]] = (),
    benchmark_bars: Sequence[Mapping[str, Any]] = (),
    quality_complete: bool = True,
    invalid_hash: bool = False,
    kind: str = "capture_intraday_equities",
) -> dict[str, Any]:
    from app.decision_harness.contracts import content_sha256

    slot = datetime.fromisoformat(scheduled_at)
    contract = _capture_contract(
        minute_bars,
        benchmark_bars=benchmark_bars,
        cutoff=slot,
        quality_complete=quality_complete,
    )
    contract["capture_id"] = capture_id
    contract["kind"] = kind
    contract["scheduled_at"] = scheduled_at
    contract["captured_at"] = max(slot, CAPTURE_CUTOFF).isoformat()
    contract["data"]["scheduled_at"] = scheduled_at
    contract["data"]["evidence_cutoff"] = scheduled_at
    contract["content_sha256"] = (
        "0" * 64 if invalid_hash else content_sha256(contract["data"])
    )
    return contract


def _write_sidecar(path: Path, contracts: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE raw_captures (
                capture_id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                kind TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE scheduled_captures (
                task_name TEXT NOT NULL,
                scheduled_at TEXT NOT NULL,
                capture_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                completed INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (task_name, scheduled_at)
            );
            """
        )
        for contract in contracts:
            encoded = json.dumps(contract, ensure_ascii=False, sort_keys=True)
            connection.execute(
                "INSERT INTO raw_captures VALUES (?, ?, ?, ?, ?)",
                (
                    contract["capture_id"],
                    contract["schema_version"],
                    contract["kind"],
                    contract["captured_at"],
                    encoded,
                ),
            )
            connection.execute(
                "INSERT INTO scheduled_captures VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "intraday-equities",
                    contract["scheduled_at"],
                    contract["capture_id"],
                    contract["kind"],
                    int(contract["data"]["quality_summary"]["complete"]),
                    contract["captured_at"],
                ),
            )


@contextmanager
def _api_client(tmp_path: Path, sidecar: Path, *, enabled: bool = True):
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'intraday-api.db'}",
        sidecar_capture_database_path=str(sidecar),
        intraday_equities_enabled=enabled,
        enabled_symbols="AAA",
        instrument_validation_symbols="AAA,SPY",
        moomoo_enabled=False,
        fred_enabled=False,
        yahoo_enabled=False,
        anomalo_workflow_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def _api_json(client: Any, path: str) -> dict[str, Any]:
    response = client.get(path)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload, dict), payload
    return payload


def test_intraday_overview_disabled_is_explicit_and_does_not_create_sidecar(tmp_path: Path) -> None:
    sidecar = tmp_path / "missing" / "collector.sqlite"
    with _api_client(tmp_path, sidecar, enabled=False) as client:
        payload = _api_json(client, "/api/intraday/overview")

    assert payload["status"] == "disabled"
    assert not sidecar.exists()
    assert not sidecar.parent.exists()


def test_intraday_overview_missing_sidecar_is_no_data_and_read_only(tmp_path: Path) -> None:
    sidecar = tmp_path / "missing" / "collector.sqlite"
    with _api_client(tmp_path, sidecar) as client:
        payload = _api_json(client, "/api/intraday/overview")

    assert payload["status"] == "no_data"
    assert not sidecar.exists()
    assert not sidecar.parent.exists()


def test_intraday_latest_partial_slot_wins_by_utc_time_over_older_complete_slot(tmp_path: Path) -> None:
    sidecar = tmp_path / "collector.sqlite"
    old = _api_capture_contract(
        "capture-old",
        "2026-10-15T14:00:00+00:00",
        minute_bars=_minute_rows(count=30),
        quality_complete=True,
    )
    # Lexical order puts this slot before old, but its UTC instant is later.
    latest = _api_capture_contract(
        "capture-latest-partial",
        "2026-10-15T09:30:00-05:00",
        minute_bars=_minute_rows(count=25),
        quality_complete=False,
    )
    _write_sidecar(sidecar, [old, latest])

    with _api_client(tmp_path, sidecar) as client:
        payload = _api_json(client, "/api/intraday/overview")

    assert {
        "schema_version",
        "source",
        "freshness",
        "coverage",
        "symbols",
        "breadth",
        "warnings",
    } <= set(payload)
    assert payload["source"]["capture_id"] == "capture-latest-partial"
    assert payload["source"].get("content_sha256") or payload["source"].get("hash")
    assert payload["status"] in {"partial", "stale"}
    assert payload["source"]["capture_id"] != "capture-old"


def test_intraday_invalid_latest_capture_does_not_fallback_to_older_complete(tmp_path: Path) -> None:
    sidecar = tmp_path / "collector.sqlite"
    old = _api_capture_contract(
        "capture-old",
        "2026-10-15T14:00:00+00:00",
        minute_bars=_minute_rows(count=30),
        quality_complete=True,
    )
    invalid = _api_capture_contract(
        "capture-invalid",
        "2026-10-15T14:05:00+00:00",
        minute_bars=_minute_rows(count=30),
        quality_complete=True,
        invalid_hash=True,
    )
    _write_sidecar(sidecar, [old, invalid])

    with _api_client(tmp_path, sidecar) as client:
        payload = _api_json(client, "/api/intraday/overview")

    assert payload["status"] == "invalid_source"
    assert payload.get("source", {}).get("capture_id") in {None, "capture-invalid"}
    assert payload.get("source", {}).get("capture_id") != "capture-old"


def test_intraday_invalid_latest_kind_is_invalid_source_without_fallback(tmp_path: Path) -> None:
    sidecar = tmp_path / "collector.sqlite"
    old = _api_capture_contract(
        "capture-old",
        "2026-10-15T14:00:00+00:00",
        minute_bars=_minute_rows(count=30),
        quality_complete=True,
    )
    invalid = _api_capture_contract(
        "capture-invalid-kind",
        "2026-10-15T14:05:00+00:00",
        minute_bars=_minute_rows(count=30),
        quality_complete=True,
        kind="capture_intraday_options",
    )
    _write_sidecar(sidecar, [old, invalid])
    # The scheduled ledger still identifies this row as the equity operation;
    # only the raw/envelope kind is corrupt.  This exercises source-boundary
    # validation without forcing the API to recognize every other operation.
    with sqlite3.connect(sidecar) as connection:
        connection.execute(
            "UPDATE scheduled_captures SET kind = ? WHERE capture_id = ?",
            ("capture_intraday_equities", "capture-invalid-kind"),
        )
        connection.commit()

    with _api_client(tmp_path, sidecar) as client:
        payload = _api_json(client, "/api/intraday/overview")

    assert payload["status"] == "invalid_source"
    assert payload.get("source", {}).get("capture_id") != "capture-old"


def test_intraday_api_reads_real_capture_contract_and_collector_store_timestamp_boundary(tmp_path: Path) -> None:
    """The API must consume the real P2 ledger/raw shape, including its timestamps.

    ``capture_contract`` stamps the envelope and ``CollectorStore`` stamps the
    raw row independently.  They are intentionally not compared for byte-level
    equality: the ledger contract is the source identity and its envelope is
    what the projection validates.  The task name is also fixture-specific so
    the API cannot silently hard-code one scheduler name.
    """
    source_root = Path(__file__).resolve().parents[2] / "urus-moomoo" / "src"
    sys.path.insert(0, str(source_root))
    from urus_moomoo.contracts import capture_contract
    from urus_moomoo.storage.sqlite import CollectorStore

    sidecar = tmp_path / "collector-real.sqlite"
    scheduled_at = CAPTURE_CUTOFF
    data = dict(
        _capture_contract(
            _minute_rows(count=30),
            cutoff=scheduled_at,
        )["data"]
    )
    contract = capture_contract(
        kind="capture_intraday_equities",
        payload=data,
        requested_symbols=["AAA", "SPY"],
        task_name="equities-capture-fixture",
        scheduled_at=scheduled_at,
        trading_date="2026-10-15",
    )
    with CollectorStore(sidecar) as store:
        assert store.write_scheduled_raw(
            "equities-capture-fixture",
            scheduled_at,
            contract["capture_id"],
            contract["kind"],
            contract,
            schema_version=contract["schema_version"],
            completed=True,
        ) is True
        raw = store.read_raw(contract["capture_id"])
        assert raw is not None
        assert raw["payload_json"]

    with _api_client(tmp_path, sidecar) as client:
        payload = _api_json(client, "/api/intraday/overview")

    assert payload["status"] not in {"invalid_source", "no_data"}
    assert payload["source"]["capture_id"] == contract["capture_id"]
    assert isinstance(payload["symbols"], Mapping)
    assert isinstance(payload["symbols"]["AAA"]["features"], Mapping)


def test_intraday_symbol_endpoint_exposes_bars_features_and_source_slot(tmp_path: Path) -> None:
    sidecar = tmp_path / "collector.sqlite"
    contract = _api_capture_contract(
        "capture-symbol",
        "2026-10-15T14:00:00+00:00",
        minute_bars=_minute_rows(count=30),
        quality_complete=True,
    )
    _write_sidecar(sidecar, [contract])

    with _api_client(tmp_path, sidecar) as client:
        payload = _api_json(client, "/api/intraday/symbols/AAA")

    assert payload["source"]["capture_id"] == "capture-symbol"
    assert payload["source"].get("content_sha256") or payload["source"].get("hash")
    assert payload["symbol"] == "AAA"
    assert isinstance(payload["bars"], list)
    assert isinstance(payload["features"], Mapping)


def test_intraday_symbol_endpoint_reports_unknown_symbol_without_using_other_symbol(tmp_path: Path) -> None:
    sidecar = tmp_path / "collector.sqlite"
    contract = _api_capture_contract(
        "capture-symbol",
        "2026-10-15T14:00:00+00:00",
        minute_bars=_minute_rows(count=30),
        quality_complete=True,
    )
    _write_sidecar(sidecar, [contract])

    with _api_client(tmp_path, sidecar) as client:
        response = client.get("/api/intraday/symbols/BBB")
        assert response.status_code in {200, 404}
        payload = response.json()

    assert payload.get("status") in {"no_data", "not_found", "invalid_source"} or response.status_code == 404


def test_intraday_api_reads_sidecar_without_mutating_it(tmp_path: Path) -> None:
    sidecar = tmp_path / "collector.sqlite"
    contract = _api_capture_contract(
        "capture-readonly",
        "2026-10-15T14:00:00+00:00",
        minute_bars=_minute_rows(count=30),
        quality_complete=True,
    )
    _write_sidecar(sidecar, [contract])
    before_hash = hashlib.sha256(sidecar.read_bytes()).hexdigest()
    with _api_client(tmp_path, sidecar) as client:
        _api_json(client, "/api/intraday/overview")
        _api_json(client, "/api/intraday/symbols/AAA")
    after_hash = hashlib.sha256(sidecar.read_bytes()).hexdigest()

    assert after_hash == before_hash
