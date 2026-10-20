"""P5 red tests for causal intraday structure strategies.

These tests exercise only the public ``app.intraday.events.evaluate`` entry
point.  Fixtures are deterministic P3-shaped projections and never open a
provider, sidecar, or production database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from statistics import fmean
from typing import Any, Mapping, Sequence

import pytest


BASE = datetime(2026, 10, 16, 13, 30, tzinfo=UTC)  # 09:30 America/New_York
TRADING_DATE = "2026-10-16"
TASK_NAME = "p5-structure-fixture"


def _spec() -> dict[str, Any]:
    # P5 uses documented defaults when no s4-s8 overrides are supplied.
    return {
        "rule_version": "urus.intraday.events.v1",
        "timeframe": "5m",
        "s1": {"min_delta_pp": 0.05, "comparison_bars_ago": 2, "confirm_slots": 2},
        "s2": {"atr_buffer_multiple": 0.10, "price_buffer_bps": 2, "candidate_ttl_bars": 3},
        "s3": {"price_shock_atr_multiple": 2.0, "impulse_atr_multiple": 1.2, "rvol_min": 1.8},
        "cooldown_minutes": 15,
        "max_source_age_seconds": 600,
        "expire_at_session_close": True,
    }


def _bar(
    index: int,
    close: float,
    *,
    open_price: float | None = None,
    high: float | None = None,
    low: float | None = None,
    volume: float = 1_000.0,
    base: datetime = BASE,
) -> dict[str, Any]:
    start = base + timedelta(minutes=5 * index)
    open_value = close if open_price is None else open_price
    high_value = max(open_value, close) + 0.25 if high is None else high
    low_value = min(open_value, close) - 0.25 if low is None else low
    assert low_value <= min(open_value, close) <= max(open_value, close) <= high_value
    return {
        "bar_start_at": start.isoformat(),
        "bar_end_at": (start + timedelta(minutes=5)).isoformat(),
        "open": open_value,
        "high": high_value,
        "low": low_value,
        "close": close,
        "volume": volume,
        "turnover": close * volume,
        "quality_state": "ready",
    }


def _true_ranges(bars: Sequence[Mapping[str, Any]]) -> list[float]:
    result: list[float] = []
    for index, bar in enumerate(bars):
        high = float(bar["high"])
        low = float(bar["low"])
        if index == 0:
            result.append(high - low)
            continue
        previous_close = float(bars[index - 1]["close"])
        result.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return result


def _wilder_atr(bars: Sequence[Mapping[str, Any]], period: int = 14) -> float | None:
    ranges = _true_ranges(bars)
    if len(ranges) < period:
        return None
    value = sum(ranges[:period]) / period
    for true_range in ranges[period:]:
        value = (value * (period - 1) + true_range) / period
    return value


def _features(bars: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    closes = [float(bar["close"]) for bar in bars]
    ma5 = fmean(closes[-5:]) if len(closes) >= 5 else None
    previous_ma5 = fmean(closes[-6:-1]) if len(closes) >= 6 else None
    return {
        "ma5": ma5,
        "ma5_slope": ma5 - previous_ma5 if ma5 is not None and previous_ma5 is not None else None,
        "ma5_distance_pct": (closes[-1] / ma5 - 1.0) * 100 if ma5 else None,
        "atr14": _wilder_atr(bars),
        "rs15": None,
        "rs30": None,
        "rvol": None,
        "macd": {
            "histogram": None,
            "histogram_delta": None,
            "histogram_delta2": None,
        },
    }


def _projection(
    bars: Sequence[Mapping[str, Any]],
    *,
    cutoff_index: int | None = None,
    trading_date: str = TRADING_DATE,
    symbol: str = "AAA",
    task_name: str = TASK_NAME,
    content_hash: str = "b" * 64,
    base: datetime = BASE,
) -> dict[str, Any]:
    rows = [dict(item) for item in bars]
    assert rows
    visible_index = len(rows) - 1 if cutoff_index is None else cutoff_index
    assert 0 <= visible_index < len(rows)
    cutoff = datetime.fromisoformat(str(rows[visible_index]["bar_end_at"]))
    visible_bars = rows[: visible_index + 1]
    item = {
        "symbol": symbol,
        "status": "ok",
        "as_of": cutoff.isoformat(),
        "role": "C",
        # The evaluator must apply the source cutoff itself when rows after the
        # cutoff are present; this enables prefix-invariance checks.
        "bars": rows,
        "features": _features(visible_bars),
        "reasons": {},
        "coverage": {"status": "ok", "complete": True},
    }
    return {
        "schema_version": "urus.intraday.projection.v1",
        "formula_version": "urus.intraday.equities.v1",
        "status": "ok",
        "source": {
            "task_name": task_name,
            "capture_id": f"capture-{cutoff.isoformat()}",
            "content_sha256": content_hash,
            "scheduled_at": cutoff.isoformat(),
            "captured_at": cutoff.isoformat(),
            "trading_date": trading_date,
        },
        "freshness": {
            "status": "fresh",
            "as_of": cutoff.isoformat(),
            "expected_latest_bar_end": cutoff.isoformat(),
            "age_seconds": 0,
        },
        "quality": {
            "status": "ok",
            "source_complete": True,
            "minute_timestamp_boundary_verified": True,
        },
        "coverage": {"status": "ok"},
        "symbols": {symbol: item},
        "breadth": {"status": "ok", "effective_denominator": 1},
        "warnings": [],
    }


def _transitions(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = result.get("transitions")
    assert isinstance(value, list), result
    return [item for item in value if isinstance(item, Mapping)]


def _strategy_transitions(
    result: Mapping[str, Any],
    strategy: str,
    setup_type: str | None = None,
) -> list[Mapping[str, Any]]:
    wanted_strategy = strategy.upper()
    return [
        item
        for item in _transitions(result)
        if str(item.get("strategy") or "").upper() == wanted_strategy
        and (setup_type is None or item.get("setup_type") == setup_type)
    ]


def _setups(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    state = result.get("state")
    assert isinstance(state, Mapping), result
    value = state.get("setups")
    assert isinstance(value, list), result
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _setup_by_id(result: Mapping[str, Any], setup_id: str) -> dict[str, Any]:
    matches = [item for item in _setups(result) if item.get("setup_id") == setup_id]
    assert len(matches) == 1, result
    return matches[0]


def _orb_bars() -> list[dict[str, Any]]:
    # First three bars define the opening range.  The remaining pre-breakout
    # bars provide the P3 ATR warm-up without changing those frozen bars.
    rows = [
        _bar(0, 100.00, high=100.35, low=99.80),
        _bar(1, 100.10, open_price=100.00, high=100.40, low=99.85),
        _bar(2, 100.05, open_price=100.10, high=100.30, low=99.75),
    ]
    for index in range(3, 14):
        close = 100.05 + (0.03 if index % 2 else 0.0)
        rows.append(_bar(index, close, open_price=100.05, high=100.30, low=99.80))
    rows.append(_bar(14, 100.95, open_price=100.10, high=101.10, low=100.00))
    return rows


def _compressed_range_bars() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(8):
        close = 100.00 + (0.20 if index % 2 else 0.0)
        rows.append(_bar(index, close, open_price=100.00, high=100.55, low=99.45))
    # These are the six bars before the current breakout bar.
    for index in range(8, 14):
        close = 100.02 + (0.03 if index % 2 else 0.0)
        rows.append(_bar(index, close, open_price=100.02, high=100.15, low=99.85))
    rows.append(_bar(14, 101.00, open_price=100.05, high=101.25, low=99.70))
    return rows


def _reversal_bars(kind: str) -> list[dict[str, Any]]:
    assert kind in {"v", "a"}
    rows = [
        _bar(index, 100.00, high=100.50, low=99.50)
        for index in range(8)
    ]
    if kind == "v":
        rows.extend(
            [
                _bar(8, 101.50, open_price=100.00, high=102.00, low=99.80),
                _bar(9, 100.50, open_price=101.50, high=101.70, low=100.00),
                _bar(10, 98.50, open_price=100.50, high=100.80, low=97.50),
                # A complete reverse bar after the reference low.  Its high
                # is the frozen confirmation line for the V candidate.
                _bar(11, 99.50, open_price=98.50, high=101.00, low=98.20),
                _bar(12, 99.80, open_price=99.50, high=100.20, low=99.30),
                _bar(13, 99.00, open_price=99.80, high=100.00, low=98.70),
                # This crosses MA5 and creates a candidate, but does not
                # cross the frozen 101.00 confirmation line.
                _bar(14, 100.60, open_price=99.00, high=100.80, low=99.00),
            ]
        )
    else:
        rows.extend(
            [
                _bar(8, 98.50, open_price=100.00, high=100.20, low=97.50),
                _bar(9, 99.50, open_price=98.50, high=100.00, low=98.20),
                _bar(10, 101.50, open_price=99.50, high=102.00, low=99.20),
                # A complete reverse bar after the reference high.  Its low
                # is the frozen confirmation line for the A candidate.
                _bar(11, 100.50, open_price=101.50, high=101.80, low=99.00),
                _bar(12, 100.20, open_price=100.50, high=100.70, low=99.80),
                _bar(13, 101.00, open_price=100.20, high=101.30, low=100.00),
                _bar(14, 99.20, open_price=101.00, high=101.20, low=98.90),
            ]
        )
    return rows


def _nine_down_bars(*, base: datetime = BASE, trading_date: str = TRADING_DATE) -> list[dict[str, Any]]:
    # C[t] < C[t-4] for t=4..12 gives exactly nine consecutive comparisons.
    return [
        _bar(index, 110.0 - index, open_price=110.0 - index + 0.2, base=base)
        for index in range(13)
    ]


def test_s4_requires_three_complete_orb_bars_and_warmed_atr() -> None:
    from app.intraday.events import evaluate

    for bars in (_orb_bars()[:2], _orb_bars()[:3]):
        cutoff = len(bars) - 1
        result = evaluate(
            _projection(bars, cutoff_index=cutoff),
            {},
            _spec(),
            evaluated_at=datetime.fromisoformat(bars[-1]["bar_end_at"]) + timedelta(seconds=1),
        )
        assert not _strategy_transitions(result, "S4"), result


def test_s4_breakout_candidate_freezes_range_and_future_bars_cannot_repaint_prefix() -> None:
    from app.intraday.events import evaluate

    bars = _orb_bars()
    prefix = _projection(bars, cutoff_index=14)
    first = evaluate(
        prefix,
        {},
        _spec(),
        evaluated_at=datetime.fromisoformat(bars[14]["bar_end_at"]) + timedelta(seconds=1),
    )
    candidates = _strategy_transitions(first, "S4", "orb_breakout")
    assert candidates, first
    candidate = candidates[-1]
    setup_id = str(candidate["setup_id"])
    setup = _setup_by_id(first, setup_id)
    assert setup.get("status") == "candidate"
    frozen_levels = (setup.get("confirmation_level"), setup.get("invalidation_level"))

    future = bars + [
        _bar(15, 103.0, open_price=100.95, high=104.0, low=100.80),
        _bar(16, 98.0, open_price=103.0, high=103.2, low=97.0),
    ]
    # Appending unseen rows while retaining the same source cutoff must not
    # change the candidate or its frozen lines.
    with_future = evaluate(
        _projection(future, cutoff_index=14, content_hash=prefix["source"]["content_sha256"]),
        {},
        _spec(),
        evaluated_at=datetime.fromisoformat(bars[14]["bar_end_at"]) + timedelta(seconds=1),
    )
    assert _strategy_transitions(with_future, "S4", "orb_breakout") == _strategy_transitions(
        first, "S4", "orb_breakout"
    ), with_future
    assert _setup_by_id(with_future, setup_id)["confirmation_level"] == frozen_levels[0]
    assert _setup_by_id(with_future, setup_id)["invalidation_level"] == frozen_levels[1]

    next_projection = _projection(future, cutoff_index=15)
    advanced = evaluate(
        next_projection,
        first["state"],
        _spec(),
        evaluated_at=datetime.fromisoformat(future[15]["bar_end_at"]) + timedelta(seconds=1),
    )
    advanced_setup = _setup_by_id(advanced, setup_id)
    assert (
        advanced_setup.get("confirmation_level"),
        advanced_setup.get("invalidation_level"),
    ) == frozen_levels
    assert any(
        item.get("setup_id") == setup_id and item.get("status") == "confirmed"
        for item in _strategy_transitions(advanced, "S4", "orb_breakout")
    ), advanced


def test_s5_uses_six_bars_before_current_bar_and_freezes_breakout_boundary() -> None:
    from app.intraday.events import evaluate

    bars = _compressed_range_bars()
    current = bars[-1]
    prior_six = bars[-7:-1]
    prior_high = max(float(item["high"]) for item in prior_six)
    prior_low = min(float(item["low"]) for item in prior_six)
    assert float(current["high"]) > prior_high
    assert float(current["low"]) < prior_low
    assert _wilder_atr(bars[:14]) is not None

    first = evaluate(
        _projection(bars),
        {},
        _spec(),
        evaluated_at=datetime.fromisoformat(current["bar_end_at"]) + timedelta(seconds=1),
    )
    candidates = _strategy_transitions(first, "S5", "range_breakout")
    assert candidates, first
    setup_id = str(candidates[-1]["setup_id"])
    setup = _setup_by_id(first, setup_id)
    assert setup.get("status") == "candidate"
    # A candidate proves that the current bar was excluded from the six-bar
    # range; including its new high would make a breakout impossible.
    assert float(setup["confirmation_level"]) < float(current["high"])
    frozen_levels = (setup.get("confirmation_level"), setup.get("invalidation_level"))

    later = bars + [_bar(15, 102.0, open_price=101.0, high=102.5, low=100.8)]
    advanced = evaluate(
        _projection(later, cutoff_index=15),
        first["state"],
        _spec(),
        evaluated_at=datetime.fromisoformat(later[-1]["bar_end_at"]) + timedelta(seconds=1),
    )
    advanced_setup = _setup_by_id(advanced, setup_id)
    assert (
        advanced_setup.get("confirmation_level"),
        advanced_setup.get("invalidation_level"),
    ) == frozen_levels


@pytest.mark.parametrize(
    ("kind", "setup_type", "direction"),
    (("v", "v_reversal", "bullish"), ("a", "a_reversal", "bearish")),
)
def test_s6_requires_ordered_extrema_and_creates_candidate_before_later_confirmation(
    kind: str,
    setup_type: str,
    direction: str,
) -> None:
    from app.intraday.events import evaluate

    bars = _reversal_bars(kind)
    first = evaluate(
        _projection(bars),
        {},
        _spec(),
        evaluated_at=datetime.fromisoformat(bars[-1]["bar_end_at"]) + timedelta(seconds=1),
    )
    candidates = _strategy_transitions(first, "S6", setup_type)
    assert candidates, first
    candidate = candidates[-1]
    assert candidate.get("direction") == direction
    assert candidate.get("status") == "candidate"
    setup_id = str(candidate["setup_id"])
    setup = _setup_by_id(first, setup_id)
    frozen_levels = (setup.get("confirmation_level"), setup.get("invalidation_level"))
    assert all(value is not None for value in frozen_levels)

    confirm_close = 101.20 if kind == "v" else 98.80
    next_bar = _bar(
        15,
        confirm_close,
        open_price=float(bars[-1]["close"]),
        high=101.40 if kind == "v" else 101.20,
        low=100.40 if kind == "v" else 98.50,
    )
    advanced = evaluate(
        _projection(bars + [next_bar]),
        first["state"],
        _spec(),
        evaluated_at=datetime.fromisoformat(next_bar["bar_end_at"]) + timedelta(seconds=1),
    )
    advanced_setup = _setup_by_id(advanced, setup_id)
    assert (
        advanced_setup.get("confirmation_level"),
        advanced_setup.get("invalidation_level"),
    ) == frozen_levels
    assert any(
        item.get("setup_id") == setup_id and item.get("status") == "confirmed"
        for item in _strategy_transitions(advanced, "S6", setup_type)
    ), advanced


def test_s6_wrong_extrema_order_or_missing_reversal_bar_is_unavailable() -> None:
    from app.intraday.events import evaluate

    wrong_order = _reversal_bars("v")
    # Make the six-bar reference order low -> high while retaining a bullish
    # MA5 reclaim.  It must not be relabeled as a V reversal.
    wrong_order[8] = _bar(8, 98.50, open_price=100.00, high=100.20, low=97.50)
    wrong_order[9] = _bar(9, 100.50, open_price=98.50, high=101.00, low=98.20)
    wrong_order[10] = _bar(10, 101.50, open_price=100.50, high=102.00, low=99.20)
    wrong_result = evaluate(
        _projection(wrong_order),
        {},
        _spec(),
        evaluated_at=datetime.fromisoformat(wrong_order[-1]["bar_end_at"]) + timedelta(seconds=1),
    )
    assert not _strategy_transitions(wrong_result, "S6"), wrong_result

    no_reverse = _reversal_bars("v")
    # The post-low bar stays a continuation down bar, so no complete reverse
    # bar exists before the current MA5 reclaim.
    no_reverse[11] = _bar(11, 98.20, open_price=98.50, high=98.70, low=97.90)
    no_reverse_result = evaluate(
        _projection(no_reverse),
        {},
        _spec(),
        evaluated_at=datetime.fromisoformat(no_reverse[-1]["bar_end_at"]) + timedelta(seconds=1),
    )
    assert not _strategy_transitions(no_reverse_result, "S6"), no_reverse_result


@pytest.mark.parametrize(
    ("kind", "setup_type", "close", "open_price", "high", "low"),
    (
        ("v", "v_reversal", 100.80, 100.60, 101.20, 100.20),
        ("a", "a_reversal", 99.80, 99.20, 100.20, 98.80),
    ),
)
def test_s6_candidate_survives_fresh_bar_when_rolling_detector_is_unavailable(
    kind: str,
    setup_type: str,
    close: float,
    open_price: float,
    high: float,
    low: float,
) -> None:
    """A detector losing its rolling shape must not invent a coverage gap."""
    from app.intraday.events import evaluate

    bars = _reversal_bars(kind)
    first = evaluate(
        _projection(bars),
        {},
        _spec(),
        evaluated_at=datetime.fromisoformat(bars[-1]["bar_end_at"]) + timedelta(seconds=1),
    )
    candidate = _strategy_transitions(first, "S6", setup_type)[-1]
    setup_id = str(candidate["setup_id"])
    frozen_evidence = _setup_by_id(first, setup_id)["frozen_evidence"]

    next_bar = _bar(
        15,
        close,
        open_price=open_price,
        high=high,
        low=low,
    )
    advanced = evaluate(
        _projection(bars + [next_bar]),
        first["state"],
        _spec(),
        evaluated_at=datetime.fromisoformat(next_bar["bar_end_at"]) + timedelta(seconds=1),
    )
    s6_capability = next(
        value
        for value in advanced["capabilities"].values()
        if value.get("strategy") == "S6"
    )
    assert s6_capability.get("available") is False, advanced
    setup = _setup_by_id(advanced, setup_id)
    assert setup.get("status") == "candidate", advanced
    assert setup.get("transition_seq") == 1, advanced
    assert setup.get("frozen_evidence") == frozen_evidence, advanced
    assert not any(
        item.get("setup_id") == setup_id
        and item.get("status") in {"expired", "invalidated"}
        for item in _transitions(advanced)
    ), advanced


@pytest.mark.parametrize(
    ("kind", "setup_type", "confirm_close", "confirm_open", "confirm_high", "confirm_low", "invalidate_close", "invalidate_open", "invalidate_high", "invalidate_low"),
    (
        ("v", "v_reversal", 101.20, 100.60, 101.40, 100.40, 97.00, 101.20, 101.50, 96.80),
        ("a", "a_reversal", 98.80, 99.20, 101.20, 98.50, 102.50, 98.80, 102.80, 98.50),
    ),
)
def test_s6_confirmed_setup_uses_frozen_invalidation_when_detector_is_unavailable(
    kind: str,
    setup_type: str,
    confirm_close: float,
    confirm_open: float,
    confirm_high: float,
    confirm_low: float,
    invalidate_close: float,
    invalidate_open: float,
    invalidate_high: float,
    invalidate_low: float,
) -> None:
    """A confirmed V/A setup still invalidates on its frozen line."""
    from app.intraday.events import evaluate

    bars = _reversal_bars(kind)
    first = evaluate(
        _projection(bars),
        {},
        _spec(),
        evaluated_at=datetime.fromisoformat(bars[-1]["bar_end_at"]) + timedelta(seconds=1),
    )
    candidate = _strategy_transitions(first, "S6", setup_type)[-1]
    setup_id = str(candidate["setup_id"])
    frozen_evidence = _setup_by_id(first, setup_id)["frozen_evidence"]

    confirmation_bar = _bar(
        15,
        confirm_close,
        open_price=confirm_open,
        high=confirm_high,
        low=confirm_low,
    )
    confirmed = evaluate(
        _projection(bars + [confirmation_bar]),
        first["state"],
        _spec(),
        evaluated_at=datetime.fromisoformat(confirmation_bar["bar_end_at"]) + timedelta(seconds=1),
    )
    confirmed_setup = _setup_by_id(confirmed, setup_id)
    assert confirmed_setup.get("status") == "confirmed", confirmed
    assert confirmed_setup.get("frozen_evidence") == frozen_evidence, confirmed

    invalidation_bar = _bar(
        16,
        invalidate_close,
        open_price=invalidate_open,
        high=invalidate_high,
        low=invalidate_low,
    )
    invalidated = evaluate(
        _projection(bars + [confirmation_bar, invalidation_bar]),
        confirmed["state"],
        _spec(),
        evaluated_at=datetime.fromisoformat(invalidation_bar["bar_end_at"]) + timedelta(seconds=1),
    )
    s6_capability = next(
        value
        for value in invalidated["capabilities"].values()
        if value.get("strategy") == "S6"
    )
    assert s6_capability.get("available") is False, invalidated
    setup = _setup_by_id(invalidated, setup_id)
    assert setup.get("status") == "invalidated", invalidated
    assert setup.get("frozen_evidence") == frozen_evidence, invalidated
    assert any(
        item.get("setup_id") == setup_id
        and item.get("status") == "invalidated"
        and item.get("reason_code") == "frozen_level_invalidated"
        for item in _strategy_transitions(invalidated, "S6", setup_type)
    ), invalidated


def test_s7_requires_recorded_parent_and_preserves_parent_identity_on_failure() -> None:
    from app.intraday.events import evaluate

    bars = _compressed_range_bars()
    first = evaluate(
        _projection(bars),
        {},
        _spec(),
        evaluated_at=datetime.fromisoformat(bars[-1]["bar_end_at"]) + timedelta(seconds=1),
    )
    parent_candidates = _strategy_transitions(first, "S5", "range_breakout")
    assert parent_candidates, first
    parent_id = str(parent_candidates[-1]["setup_id"])

    # Return inside the frozen range on the first following complete bar.
    failed_bar = _bar(15, 100.00, open_price=101.0, high=101.20, low=99.80)
    failed = evaluate(
        _projection(bars + [failed_bar]),
        first["state"],
        _spec(),
        evaluated_at=datetime.fromisoformat(failed_bar["bar_end_at"]) + timedelta(seconds=1),
    )
    failures = _strategy_transitions(failed, "S7", "failed_breakout")
    assert failures, failed
    failure = failures[-1]
    parent_reference = failure.get("parent_setup_id") or (
        failure.get("evidence", {}).get("parent_setup_id")
        if isinstance(failure.get("evidence"), Mapping)
        else None
    )
    assert parent_reference == parent_id, failure
    parent_after = _setup_by_id(failed, parent_id)
    assert parent_after.get("status") == "invalidated", failed
    assert parent_after.get("frozen_evidence") == _setup_by_id(first, parent_id).get(
        "frozen_evidence"
    ), failed
    assert any(
        item.get("setup_id") == parent_id
        and item.get("strategy") == "S5"
        and item.get("status") == "invalidated"
        for item in _transitions(failed)
    ), failed
    assert failure.get("status") == "confirmed", failure
    assert failure.get("parent_setup_id") == parent_id, failure
    child = _setup_by_id(failed, str(failure["setup_id"]))
    assert child.get("parent_setup_id") == parent_id
    assert child.get("frozen_evidence", {}).get("parent_setup_id") == parent_id

    # The same price return without the recorded parent is not a valid S7.
    no_parent = evaluate(
        _projection(bars + [failed_bar]),
        {},
        _spec(),
        evaluated_at=datetime.fromisoformat(failed_bar["bar_end_at"]) + timedelta(seconds=1),
    )
    assert not _strategy_transitions(no_parent, "S7", "failed_breakout"), no_parent


def test_s7_parent_child_transitions_save_as_json_and_keep_current_status(tmp_path) -> None:
    """Persist actual S5→S7 results and verify immutable/current status views."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.core.database import Base
    from app.models.intraday_events import IntradayEventModel, IntradaySetupStateModel
    from app.repositories.intraday_events import IntradayEventRepository
    from app.intraday.events import evaluate

    bars = _compressed_range_bars()
    parent_projection = _projection(bars)
    parent_evaluated_at = datetime.fromisoformat(bars[-1]["bar_end_at"]) + timedelta(seconds=1)
    parent_result = evaluate(parent_projection, {}, _spec(), evaluated_at=parent_evaluated_at)
    parent_candidate = _strategy_transitions(parent_result, "S5", "range_breakout")[-1]
    parent_id = str(parent_candidate["setup_id"])
    parent_setup = _setup_by_id(parent_result, parent_id)

    failed_bar = _bar(15, 100.00, open_price=101.0, high=101.20, low=99.80)
    failed_projection = _projection(bars + [failed_bar])
    failed_evaluated_at = datetime.fromisoformat(failed_bar["bar_end_at"]) + timedelta(seconds=1)
    failed_result = evaluate(
        failed_projection,
        parent_result["state"],
        _spec(),
        evaluated_at=failed_evaluated_at,
    )
    child_transition = _strategy_transitions(failed_result, "S7", "failed_breakout")[-1]
    child_id = str(child_transition["setup_id"])

    assert any(
        item.get("setup_id") == parent_id
        and item.get("status") == "invalidated"
        for item in _strategy_transitions(failed_result, "S5", "range_breakout")
    ), failed_result
    assert child_transition.get("status") == "confirmed", failed_result
    assert child_transition.get("parent_setup_id") == parent_id, child_transition

    engine = create_engine(
        f"sqlite:///{tmp_path / 'events.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_factory = lambda: Session(engine)
    processor_id = "intraday-structure-parent-child-test"
    source_path = str(tmp_path / "source.sqlite")

    def save_result(result: Mapping[str, Any], projection: Mapping[str, Any], when: datetime) -> None:
        source = projection["source"]
        cursor_values = {
            "source_path_identity": source_path,
            "last_scheduled_at": source["scheduled_at"],
            "last_task_name": source["task_name"],
            "last_capture_id": source["capture_id"],
            "last_content_sha256": source["content_sha256"],
            "seen_source_slots_json": [
                f"{source['task_name']}|{source['scheduled_at']}"
            ],
            "backlog_remaining": False,
            "last_status": "ok",
            "last_error": None,
            "capabilities_summary_json": {},
        }
        with session_factory() as session:
            with session.begin():
                IntradayEventRepository(session).save_slot(
                    processor_id=processor_id,
                    task_name=str(source["task_name"]),
                    state=result["state"],
                    transitions=result["transitions"],
                    cursor_values=cursor_values,
                    now=when,
                )

    save_result(parent_result, parent_projection, parent_evaluated_at)
    save_result(failed_result, failed_projection, failed_evaluated_at)

    with session_factory() as session:
        events = list(
            session.scalars(
                select(IntradayEventModel)
                .where(IntradayEventModel.processor_id == processor_id)
                .order_by(IntradayEventModel.id.asc())
            )
        )
        state_row = session.scalar(
            select(IntradaySetupStateModel).where(
                IntradaySetupStateModel.processor_id == processor_id,
                IntradaySetupStateModel.task_name == TASK_NAME,
            )
        )
        current_status = IntradayEventRepository(session).event_statuses(
            processor_id, (parent_id, child_id)
        )

    parent_events = [event for event in events if event.setup_id == parent_id]
    child_events = [event for event in events if event.setup_id == child_id]
    assert [event.status for event in parent_events] == ["candidate", "invalidated"], events
    assert len(child_events) == 1 and child_events[0].status == "confirmed", events
    assert current_status[parent_id] == "invalidated", current_status
    assert current_status[child_id] == "confirmed", current_status
    assert child_events[0].payload_json.get("parent_setup_id") == parent_id
    assert child_events[0].payload_json.get("evidence", {}).get("parent_setup_id") == parent_id
    assert parent_events[0].payload_json.get("evidence") == parent_setup["frozen_evidence"]
    assert state_row is not None and isinstance(state_row.state_json, Mapping)
    persisted_parent = next(
        setup
        for setup in state_row.state_json["setups"]
        if setup.get("setup_id") == parent_id
    )
    persisted_child = next(
        setup
        for setup in state_row.state_json["setups"]
        if setup.get("setup_id") == child_id
    )
    assert persisted_parent["status"] == "invalidated"
    assert persisted_child["status"] == "confirmed"
    assert persisted_child["parent_setup_id"] == parent_id
    assert persisted_parent["frozen_evidence"] == parent_setup["frozen_evidence"]


def test_s8_nine_is_one_exhaustion_observation_and_continuation_does_not_repeat() -> None:
    from app.intraday.events import evaluate

    bars = _nine_down_bars()
    state: Mapping[str, Any] = {}
    all_transitions: list[Mapping[str, Any]] = []
    for index in range(4, len(bars)):
        result = evaluate(
            _projection(bars[: index + 1]),
            state,
            _spec(),
            evaluated_at=datetime.fromisoformat(bars[index]["bar_end_at"]) + timedelta(seconds=1),
        )
        all_transitions.extend(_strategy_transitions(result, "S8", "simplified_setup_exhaustion"))
        state = result["state"]

    assert len(all_transitions) == 1, all_transitions
    assert any(
        int(item.get("evidence", {}).get("count", 0)) == 9
        for item in all_transitions
        if isinstance(item.get("evidence"), Mapping)
    ), all_transitions

    continued = bars + [
        _bar(13, 97.0, open_price=98.0),
        _bar(14, 96.0, open_price=97.0),
    ]
    for index in (13, 14):
        result = evaluate(
            _projection(continued[: index + 1]),
            state,
            _spec(),
            evaluated_at=datetime.fromisoformat(continued[index]["bar_end_at"]) + timedelta(seconds=1),
        )
        assert not _strategy_transitions(result, "S8", "simplified_setup_exhaustion"), result
        state = result["state"]


def test_s8_exhaustion_does_not_repeat_after_a_skipped_observation() -> None:
    from app.intraday.events import evaluate

    bars = _nine_down_bars()
    state: Mapping[str, Any] = {}
    first_events: list[Mapping[str, Any]] = []
    for index in range(4, len(bars)):
        result = evaluate(
            _projection(bars[: index + 1]),
            state,
            _spec(),
            evaluated_at=datetime.fromisoformat(bars[index]["bar_end_at"]) + timedelta(seconds=1),
        )
        first_events.extend(
            _strategy_transitions(result, "S8", "simplified_setup_exhaustion")
        )
        state = result["state"]

    assert len(first_events) == 1, first_events
    assert first_events[0].get("evidence", {}).get("count") == 9, first_events

    continued = bars + [
        _bar(13, 97.0, open_price=98.0),
        _bar(14, 96.0, open_price=97.0),
    ]
    # Source observation 13 is skipped.  The next projection still contains a
    # complete, gap-free bar sequence through 14, so the sequence identity,
    # rather than cursor adjacency, must suppress a second count-9 event.
    result = evaluate(
        _projection(continued[:15]),
        state,
        _spec(),
        evaluated_at=datetime.fromisoformat(continued[14]["bar_end_at"]) + timedelta(seconds=1),
    )
    assert not _strategy_transitions(result, "S8", "simplified_setup_exhaustion"), result


def test_s8_equal_comparison_gap_and_trading_date_reset_the_session_counter() -> None:
    from app.intraday.events import evaluate

    bars = _nine_down_bars()
    state: Mapping[str, Any] = {}
    for index in range(4, 12):  # eight valid comparisons, no exhaustion yet
        result = evaluate(
            _projection(bars[: index + 1]),
            state,
            _spec(),
            evaluated_at=datetime.fromisoformat(bars[index]["bar_end_at"]) + timedelta(seconds=1),
        )
        state = result["state"]

    equal_bar = _bar(12, float(bars[8]["close"]), open_price=float(bars[11]["close"]))
    equal_result = evaluate(
        _projection(bars[:12] + [equal_bar]),
        state,
        _spec(),
        evaluated_at=datetime.fromisoformat(equal_bar["bar_end_at"]) + timedelta(seconds=1),
    )
    assert not _strategy_transitions(equal_result, "S8"), equal_result
    assert not any(
        str(item.get("setup_type") or "") == "simplified_setup_exhaustion"
        and item.get("status") in {"candidate", "confirmed"}
        and int(item.get("count", item.get("bars_elapsed", 0)) or 0) >= 8
        for item in _setups(equal_result)
    ), equal_result

    # A missing bar is a session sequence reset, even when the old state is
    # supplied.  The visible suffix is deliberately discontinuous.
    gap_bars = bars[:11] + bars[12:13]
    gap_result = evaluate(
        _projection(gap_bars),
        state,
        _spec(),
        evaluated_at=datetime.fromisoformat(gap_bars[-1]["bar_end_at"]) + timedelta(seconds=1),
    )
    assert not _strategy_transitions(gap_result, "S8"), gap_result
    assert not any(
        str(item.get("setup_type") or "") == "simplified_setup_exhaustion"
        and item.get("status") in {"candidate", "confirmed"}
        for item in _setups(gap_result)
    ), gap_result

    next_day_base = BASE + timedelta(days=1)
    next_day_bars = _nine_down_bars(base=next_day_base, trading_date="2026-10-17")[:6]
    next_day_result = evaluate(
        _projection(
            next_day_bars,
            trading_date="2026-10-17",
            base=next_day_base,
        ),
        state,
        _spec(),
        evaluated_at=datetime.fromisoformat(next_day_bars[-1]["bar_end_at"]) + timedelta(seconds=1),
    )
    assert not _strategy_transitions(next_day_result, "S8"), next_day_result
    assert not any(
        str(item.get("trading_date") or "") == TRADING_DATE
        and str(item.get("setup_type") or "") == "simplified_setup_exhaustion"
        and item.get("status") in {"candidate", "confirmed"}
        for item in _setups(next_day_result)
    ), next_day_result
