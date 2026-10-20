"""P4 red tests for intraday strategy evaluation and event persistence.

These tests intentionally use only deterministic P3-shaped projections and
temporary SQLite databases.  They must never open OpenD, call a provider, or
touch a configured sidecar/operational database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any, Mapping, Sequence

import pytest


BASE = datetime(2026, 10, 16, 13, 30, tzinfo=UTC)
SLOT = BASE + timedelta(minutes=80)
SERVICE_SLOT = SLOT


def _spec() -> dict[str, Any]:
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


def _bar(index: int, close: float, *, high: float | None = None, low: float | None = None) -> dict[str, Any]:
    start = BASE + timedelta(minutes=5 * index)
    high = close + 0.25 if high is None else high
    low = close - 0.25 if low is None else low
    return {
        "bar_start_at": start.isoformat(),
        "bar_end_at": (start + timedelta(minutes=5)).isoformat(),
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1_000.0,
        "turnover": close * 1_000.0,
        "quality_state": "ready",
    }


def _wilder_atr(bars: Sequence[Mapping[str, Any]], period: int = 14) -> float | None:
    ranges: list[float] = []
    for index, bar in enumerate(bars):
        high = float(bar["high"])
        low = float(bar["low"])
        if index == 0:
            ranges.append(high - low)
        else:
            previous_close = float(bars[index - 1]["close"])
            ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    if len(ranges) < period:
        return None
    value = sum(ranges[:period]) / period
    for true_range in ranges[period:]:
        value = (value * (period - 1) + true_range) / period
    return value


def _p3_features(bars: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    closes = [float(bar["close"]) for bar in bars]
    ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else None
    previous_ma5 = sum(closes[-6:-1]) / 5 if len(closes) >= 6 else None
    return {
        "ma5": ma5,
        "ma5_slope": ma5 - previous_ma5 if ma5 is not None and previous_ma5 is not None else None,
        "ma5_distance_pct": (closes[-1] / ma5 - 1.0) * 100.0 if ma5 else None,
        "atr14": _wilder_atr(bars),
        "rs15": None,
        "rs30": None,
        "rvol": None,
        "macd": {"histogram": None, "histogram_delta": None, "histogram_delta2": None},
    }


def _flat_range_bars(*, current_close: float | None = None) -> list[dict[str, Any]]:
    bars = [
        _bar(index, 100.0, high=100.05, low=99.95)
        for index in range(15)
    ]
    if current_close is None:
        current_close = 100.0
    bars.append(
        _bar(15, current_close, high=current_close + 0.05, low=current_close - 0.05)
    )
    return bars


def _zero_range_bars(count: int = 16) -> list[dict[str, Any]]:
    return [_bar(index, 100.0, high=100.0, low=100.0) for index in range(count)]


def _shock_bars(delta: float) -> list[dict[str, Any]]:
    bars = [
        _bar(index, 100.0, high=100.5, low=99.5)
        for index in range(15)
    ]
    current = 100.0 + delta
    bars.append(_bar(15, current, high=current + 0.5, low=current - 0.5))
    return bars


def _projection(
    *,
    symbol: str = "AAA",
    features: Mapping[str, Any] | None = None,
    bars: Sequence[Mapping[str, Any]] | None = None,
    status: str = "ok",
    symbols: Mapping[str, Mapping[str, Any]] | None = None,
    source: Mapping[str, Any] | None = None,
    timestamp_verified: bool = True,
) -> dict[str, Any]:
    effective_bars = [
        dict(row)
        for row in (bars or [_bar(index, 100.0 + index * 0.1) for index in range(16)])
    ]
    base_features = _p3_features(effective_bars)
    base_features.update(dict(features or {}))
    as_of = str(effective_bars[-1]["bar_end_at"]) if effective_bars else None
    default_source = {
        "task_name": "p4-equities-fixture",
        "capture_id": "capture-p4-1",
        "content_sha256": "a" * 64,
        "scheduled_at": as_of,
        "trading_date": "2026-10-16",
    }
    effective_source = dict(source or default_source)
    effective_source.setdefault("scheduled_at", as_of)
    item = {
        "symbol": symbol,
        "status": status,
        "as_of": as_of,
        "role": "C",
        "bars": effective_bars,
        "features": base_features,
        "reasons": {},
        "coverage": {"status": status, "complete": status == "ok"},
    }
    symbol_map = dict(symbols or {symbol: item})
    return {
        "schema_version": "urus.intraday.projection.v1",
        "formula_version": "urus.intraday.equities.v1",
        "status": status,
        "source": effective_source,
        "freshness": {"status": "fresh", "as_of": as_of, "age_seconds": 0},
        "quality": {
            "status": status,
            "source_complete": status == "ok",
            "minute_timestamp_boundary_verified": timestamp_verified,
        },
        "coverage": {"status": status},
        "symbols": symbol_map,
        "breadth": {"status": "ok", "effective_denominator": len(symbol_map)},
        "warnings": [],
    }


def _transition_list(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    transitions = result.get("transitions")
    assert isinstance(transitions, list), result
    return [item for item in transitions if isinstance(item, Mapping)]


def _strategy_transitions(result: Mapping[str, Any], *tokens: str) -> list[Mapping[str, Any]]:
    lowered = tuple(token.lower() for token in tokens)
    return [
        transition
        for transition in _transition_list(result)
        if all(
            token in str(
                " ".join(
                    str(transition.get(field) or "")
                    for field in ("strategy_id", "strategy", "setup_type", "setup_id")
                )
            ).lower()
            for token in lowered
        )
    ]


def _capabilities(result: Mapping[str, Any], *tokens: str) -> list[Mapping[str, Any]]:
    capabilities = result.get("capabilities")
    if isinstance(capabilities, Mapping):
        # The frozen service shape permits a mapping keyed by a stable
        # strategy/symbol capability id.  Preserve that key while filtering;
        # otherwise a valid ``s3_...`` entry is invisible when its value only
        # contains the normalized fields.
        values = [
            {**value, "_capability_key": str(key)}
            for key, value in capabilities.items()
            if isinstance(value, Mapping)
        ]
    else:
        assert isinstance(capabilities, list), result
        values = [value for value in capabilities if isinstance(value, Mapping)]
    lowered = tuple(token.lower() for token in tokens)
    return [
        item
        for item in values
        if all(
            token
            in str(
                " ".join(
                    str(item.get(field) or "")
                    for field in ("strategy", "strategy_id", "setup_type", "key", "_capability_key")
                )
            ).lower()
            for token in lowered
        )
    ]


def _setup(
    *,
    symbol: str = "AAA",
    strategy: str = "S2",
    setup_type: str = "ma5_reclaim",
    status: str = "candidate",
    transition_seq: int = 1,
    started_at: datetime = SLOT - timedelta(minutes=10),
    last_cutoff: datetime = SLOT - timedelta(minutes=5),
    bars_elapsed: int = 1,
    confirmation_level: float | None = 100.02,
    invalidation_level: float | None = 99.98,
    cooldown_until: datetime | None = None,
) -> dict[str, Any]:
    return {
        "setup_id": f"p4-equities-fixture:{symbol}:{strategy}:{setup_type}",
        "task_name": "p4-equities-fixture",
        "symbol": symbol,
        "strategy": strategy,
        "setup_type": setup_type,
        "timeframe": "5m",
        "rule_version": "urus.intraday.events.v1",
        "trading_date": "2026-10-16",
        "status": status,
        "transition_seq": transition_seq,
        "started_at": started_at.isoformat(),
        "last_cutoff": last_cutoff.isoformat(),
        "bars_elapsed": bars_elapsed,
        "confirmation_level": confirmation_level,
        "invalidation_level": invalidation_level,
        "expires_at": (started_at + timedelta(minutes=15)).isoformat(),
        "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
        "frozen_evidence": {"source_capture_id": "capture-p4-1"},
    }


def _state(*setups: Mapping[str, Any]) -> dict[str, Any]:
    return {"schema_version": "urus.intraday.setup-state.v1", "setups": [dict(item) for item in setups]}


def _rs_projection(last_index: int) -> dict[str, Any]:
    aaa_closes = [
        100.00,
        100.00,
        100.00,
        100.00,
        100.00,
        100.00,
        100.00,
        100.00,
        100.00,
        100.00,
        100.00,
        100.02,
        100.04,
        100.10,
        100.20,
        100.40,
        100.70,
        101.00,
    ]
    aaa_bars = [_bar(index, aaa_closes[index]) for index in range(last_index + 1)]
    spy_bars = [_bar(index, 100.0) for index in range(last_index + 1)]
    def excess(count: int) -> float:
        start = aaa_closes[last_index - count + 1]
        end = aaa_closes[last_index]
        return (end / start - 1.0) * 100.0

    aaa = _projection(
        bars=aaa_bars,
        features={"rs15": excess(4), "rs30": excess(7)},
    )["symbols"]["AAA"]
    spy = _projection(symbol="SPY", bars=spy_bars)["symbols"]["SPY"]
    cutoff = aaa_bars[-1]["bar_end_at"]
    return _projection(
        symbols={"AAA": aaa, "SPY": spy},
        source={
            "task_name": "p4-equities-fixture",
            "capture_id": f"capture-p4-{last_index}",
            "content_sha256": f"{last_index:064d}",
            "scheduled_at": cutoff,
            "trading_date": "2026-10-16",
        },
    )


def test_evaluate_exposes_state_transitions_and_per_strategy_capabilities() -> None:
    from app.intraday.events import evaluate

    result = evaluate(_projection(), {}, _spec(), evaluated_at=SLOT + timedelta(seconds=1))

    assert set(result) >= {"state", "transitions", "capabilities"}
    assert isinstance(result["state"], Mapping)
    assert isinstance(result["transitions"], list)
    assert isinstance(result["capabilities"], (list, Mapping))
    # Every P4 strategy must explain why it is available or unavailable.
    for strategy in ("s1", "s2", "s3"):
        capabilities = _capabilities(result, strategy)
        assert capabilities and "available" in capabilities[0], result["capabilities"]


def test_ma5_buffer_uses_atr_or_two_basis_points_and_declares_rule_version() -> None:
    from app.intraday.events import evaluate

    # ATR contributes 0.01 while 2bp of the prior close contributes 0.02.
    # The current MA5 includes the current close, so these values are the
    # independent boundary around current_ma5 + 0.02 rather than a fabricated
    # previous_atr/previous_ma5 feature.
    inside = evaluate(
        _projection(bars=_flat_range_bars(current_close=100.0249)),
        {},
        _spec(),
        evaluated_at=SLOT,
    )
    outside = evaluate(
        _projection(bars=_flat_range_bars(current_close=100.0251)),
        {},
        _spec(),
        evaluated_at=SLOT,
    )

    assert not _strategy_transitions(inside, "ma5", "reclaim")
    candidates = _strategy_transitions(outside, "ma5", "reclaim")
    assert candidates, outside
    assert any(
        "atr_price_buffer_v1" in str(item.get("rule_version") or item.get("reasons"))
        for item in candidates
    )


def test_ma5_zero_or_missing_atr_is_unavailable_without_a_price_fallback() -> None:
    from app.intraday.events import evaluate

    for bars in (_flat_range_bars()[:13], _zero_range_bars()):
        projection = _projection(bars=bars)
        # The 13-bar fixture intentionally ends before the shared SLOT.  Use
        # its own source cutoff so this test exercises ATR availability rather
        # than the unrelated stale-source gate.
        evaluated_at = datetime.fromisoformat(str(projection["source"]["scheduled_at"])) + timedelta(seconds=1)
        result = evaluate(
            projection,
            {},
            _spec(),
            evaluated_at=evaluated_at,
        )
        assert not _strategy_transitions(result, "ma5")
        capabilities = result["capabilities"]
        unavailable = _capabilities(result, "s2") + _capabilities(result, "ma5")
        assert any(
            isinstance(value, Mapping)
            and value.get("available") is False
            and str(value.get("reason_code") or "") in {"atr_unavailable", "insufficient_history", "quality_unavailable"}
            for value in unavailable
        ), capabilities


@pytest.mark.parametrize("delta", (-2.0, -2.001))
def test_price_anomaly_has_strict_two_atr_boundary_and_does_not_require_rvol(delta: float) -> None:
    from app.intraday.events import evaluate

    result = evaluate(
        _projection(
            bars=_shock_bars(delta),
        ),
        {},
        _spec(),
        evaluated_at=SLOT,
    )
    anomaly = _strategy_transitions(result, "anomaly") or _strategy_transitions(result, "drop")
    if delta == -2.0:
        assert not anomaly, result
    else:
        assert anomaly, result
        assert any(
            str(item.get("status") or item.get("transition") or "").lower()
            in {"confirmed", "candidate"}
            for item in anomaly
        )


def test_unverified_minute_boundary_blocks_formal_price_events() -> None:
    from app.intraday.events import evaluate

    result = evaluate(
        _projection(
            timestamp_verified=False,
            bars=_shock_bars(-3.0),
        ),
        {},
        _spec(),
        evaluated_at=SLOT,
    )

    assert not (_strategy_transitions(result, "anomaly") or _strategy_transitions(result, "drop")), result
    assert any(
        value.get("available") is False
        and str(value.get("reason_code") or "") == "minute_timestamp_boundary_unverified"
        for value in _capabilities(result, "s3") + _capabilities(result, "impulse")
    ), result["capabilities"]


def test_evaluator_ignores_future_bars_and_non_five_minute_rows() -> None:
    from app.intraday.events import evaluate

    valid = _flat_range_bars()
    future = _bar(16, 97.0, high=97.2, low=96.8)
    malformed = _bar(17, 96.0, high=96.2, low=95.8)
    malformed["bar_end_at"] = (
        datetime.fromisoformat(malformed["bar_start_at"]) + timedelta(minutes=1)
    ).isoformat()
    projection = _projection(bars=valid)
    projection["symbols"]["AAA"]["bars"] = valid + [future, malformed]

    result = evaluate(projection, {}, _spec(), evaluated_at=SLOT)

    # The future shock and the malformed one-minute row are outside the
    # current cutoff/interval contract.  Including either would fabricate an
    # S3 event at the current slot.
    assert not (
        _strategy_transitions(result, "s3")
        or _strategy_transitions(result, "anomaly")
        or _strategy_transitions(result, "drop")
    ), result


def test_evaluator_uses_only_the_continuous_suffix_after_a_bar_gap() -> None:
    from app.intraday.events import evaluate

    bars = _shock_bars(-3.0)
    del bars[14]
    projection = _projection(bars=bars)
    result = evaluate(projection, {}, _spec(), evaluated_at=SLOT)

    assert not (
        _strategy_transitions(result, "s3")
        or _strategy_transitions(result, "anomaly")
        or _strategy_transitions(result, "drop")
    ), result
    assert any(
        item.get("available") is False
        and item.get("reason_code") == "incomplete_bars"
        for item in _capabilities(result, "s3")
    ), result["capabilities"]


def test_evaluate_rejects_invalid_spec_instead_of_silently_using_defaults() -> None:
    from app.intraday.events import evaluate

    invalid_specs = (
        {"timeframe": "1m"},
        {"s1": {"comparison_bars_ago": 0}},
        {"s2": {"price_buffer_bps": -1}},
        {"s3": {"rvol_min": float("nan")}},
        {"expire_at_session_close": "yes"},
    )
    for invalid in invalid_specs:
        with pytest.raises(ValueError):
            evaluate(_projection(), {}, invalid, evaluated_at=SLOT)


def test_s1_compares_current_slot_with_t_minus_2_and_needs_two_same_direction_slots() -> None:
    from app.intraday.events import evaluate

    # The immediate prior slot is intentionally omitted from the comparison;
    # a one-slot improvement must not confirm S1.  The second same-direction
    # slot is required before a confirmed transition is emitted.
    prior = {"schema_version": "urus.intraday.setup-state.v1", "setups": []}
    results: list[Mapping[str, Any]] = []
    state: Mapping[str, Any] = prior
    for offset, index in enumerate((13, 14, 15, 16)):
        result = evaluate(
            _rs_projection(index),
            state,
            _spec(),
            evaluated_at=SLOT - timedelta(minutes=10) + timedelta(minutes=5 * offset),
        )
        results.append(result)
        state = result["state"]

    third_relative = (
        _strategy_transitions(results[2], "s1")
        or _strategy_transitions(results[2], "relative")
        or _strategy_transitions(results[2], "rs")
    )
    fourth_relative = (
        _strategy_transitions(results[3], "s1")
        or _strategy_transitions(results[3], "relative")
        or _strategy_transitions(results[3], "rs")
    )
    assert any(str(item.get("status") or "").lower() == "candidate" for item in third_relative), results[2]
    assert any(str(item.get("status") or "").lower() == "confirmed" for item in fourth_relative), results[3]
    confirmed = next(item for item in fourth_relative if str(item.get("status") or "").lower() == "confirmed")
    assert all(
        confirmed.get(field)
        for field in (
            "setup_id",
            "task_name",
            "symbol",
            "strategy",
            "timeframe",
            "rule_version",
            "trading_date",
            "evidence_cutoff",
            "evaluated_at",
            "source_capture_id",
        )
    ), confirmed


def test_s1_gap_expires_old_candidate_before_creating_a_new_setup_identity() -> None:
    """A skipped source slot must split S1 lifecycle transitions.

    The candidate at cutoff t-2 is followed by no evaluation at t-1.  At t,
    the same-direction RS comparison is available again, but it cannot be a
    consecutive confirmation.  The evaluator must emit an expiry for the old
    setup, then create a distinct candidate for the new evidence cutoff.
    """
    from app.intraday.events import evaluate

    state: Mapping[str, Any] = _state()
    results: dict[int, Mapping[str, Any]] = {}
    # These are actual source slots.  The t-1 slot (index 16) is deliberately
    # not evaluated at all; no synthetic history/state is injected.
    for index in (13, 14, 15):
        result = evaluate(
            _rs_projection(index),
            state,
            _spec(),
            evaluated_at=BASE + timedelta(minutes=5 * (index + 1)),
        )
        results[index] = result
        state = result["state"]

    old_candidate = next(
        item
        for item in state["setups"]
        if str(item.get("strategy") or "").upper() == "S1"
        and str(item.get("status") or "").lower() == "candidate"
    )
    old_setup_id = str(old_candidate["setup_id"])

    current = evaluate(
        _rs_projection(17),
        state,
        _spec(),
        evaluated_at=BASE + timedelta(minutes=5 * 18),
    )
    s1_transitions = _strategy_transitions(current, "S1")
    expired = [
        item
        for item in s1_transitions
        if str(item.get("status") or "").lower() == "expired"
        and item.get("setup_id") == old_setup_id
    ]
    created = [
        item
        for item in s1_transitions
        if str(item.get("status") or "").lower() == "candidate"
        and item.get("setup_id") != old_setup_id
    ]

    assert expired, current
    assert expired[0].get("transition_seq") == 2, expired
    assert str(expired[0].get("reason_code") or "") == "coverage_gap", expired
    assert created, current
    assert created[0].get("transition_seq") == 1, created
    assert created[0].get("setup_id") != old_setup_id, created
    assert not any(
        str(item.get("status") or "").lower() == "confirmed"
        for item in s1_transitions
    ), current

    s1_state = [
        item
        for item in current["state"]["setups"]
        if str(item.get("strategy") or "").upper() == "S1"
    ]
    assert {str(item.get("setup_id")) for item in s1_state} >= {
        old_setup_id,
        str(created[0]["setup_id"]),
    }
    assert any(
        item.get("setup_id") == old_setup_id
        and str(item.get("status") or "").lower() == "expired"
        for item in s1_state
    ), s1_state
    assert any(
        item.get("setup_id") == created[0]["setup_id"]
        and str(item.get("status") or "").lower() == "candidate"
        for item in s1_state
    ), s1_state


def test_s1_gap_transitions_are_persisted_as_distinct_repository_events(tmp_path: Path) -> None:
    """Repository idempotency is scoped to setup identity plus sequence."""
    from app.intraday.events import evaluate
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.core.database import Base
    from app.models.intraday_events import IntradayEventModel
    from app.repositories.intraday_events import IntradayEventRepository

    engine = create_engine(
        f"sqlite:///{tmp_path / 'events.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_factory = lambda: Session(engine)
    processor_id = "intraday-events-s1-gap-test"
    source_path = str(tmp_path / "source.sqlite")
    state: Mapping[str, Any] = _state()

    for index in (13, 14, 15, 17):
        projection = _rs_projection(index)
        evaluated_at = BASE + timedelta(minutes=5 * (index + 1))
        result = evaluate(projection, state, _spec(), evaluated_at=evaluated_at)
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
                repository = IntradayEventRepository(session)
                repository.save_slot(
                    processor_id=processor_id,
                    task_name=str(source["task_name"]),
                    state=result["state"],
                    transitions=result["transitions"],
                    cursor_values=cursor_values,
                    now=evaluated_at,
                )
        state = result["state"]

    with session_factory() as session:
        events = list(
            session.scalars(
                select(IntradayEventModel)
                .where(IntradayEventModel.processor_id == processor_id)
                .order_by(IntradayEventModel.id.asc())
            )
        )

    s1_events = [event for event in events if event.strategy == "S1"]
    assert len(s1_events) == 3, events
    old_setup_id = s1_events[0].setup_id
    assert [event.setup_id for event in s1_events[:2]] == [old_setup_id, old_setup_id]
    assert [(event.transition_seq, event.status) for event in s1_events[:2]] == [
        (1, "candidate"),
        (2, "expired"),
    ]
    assert s1_events[2].transition_seq == 1
    assert s1_events[2].status == "candidate"
    assert s1_events[2].setup_id != old_setup_id


def test_s1_setup_identity_changes_with_day_start_evidence_and_parameters() -> None:
    from app.intraday.events import evaluate

    def run_chain(
        *,
        spec: Mapping[str, Any] | None = None,
        source_overrides: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        state: Mapping[str, Any] = _state()
        for offset, index in enumerate((13, 14, 15, 16)):
            projection = _rs_projection(index)
            if source_overrides:
                projection["source"] = {
                    **projection["source"],
                    **dict(source_overrides),
                }
            result = evaluate(
                projection,
                state,
                spec or _spec(),
                evaluated_at=SLOT - timedelta(minutes=10) + timedelta(minutes=5 * offset),
            )
            state = result["state"]
        setup = next(
            item
            for item in state["setups"]
            if str(item.get("strategy") or "").upper() == "S1"
            and str(item.get("status") or "").lower() == "confirmed"
        )
        return setup

    baseline = run_chain()
    changed_day = run_chain(source_overrides={"trading_date": "2026-10-17"})
    changed_evidence = run_chain(
        source_overrides={
            "capture_id": "capture-different-start",
            "content_sha256": "e" * 64,
        }
    )
    changed_parameters = run_chain(
        spec={
            **_spec(),
            "s1": {**_spec()["s1"], "min_delta_pp": 0.06},
        }
    )

    assert changed_day["setup_id"] != baseline["setup_id"]
    assert changed_evidence["setup_id"] != baseline["setup_id"]
    assert changed_parameters["setup_id"] != baseline["setup_id"]
    assert changed_day.get("trading_date") == "2026-10-17"
    assert changed_evidence.get("frozen_evidence")


def test_cross_day_projection_cannot_confirm_yesterday_candidate() -> None:
    from app.intraday.events import evaluate

    yesterday = _setup(
        strategy="S1",
        setup_type="relative_strength_improving",
        status="candidate",
        started_at=SLOT - timedelta(minutes=10),
        last_cutoff=SLOT - timedelta(minutes=5),
    )
    today = _projection(bars=_flat_range_bars(current_close=100.05), source={
        "task_name": "p4-equities-fixture",
        "capture_id": "capture-next-day",
        "content_sha256": "d" * 64,
        "scheduled_at": SLOT.isoformat(),
        "trading_date": "2026-10-17",
    })
    result = evaluate(today, _state(yesterday), _spec(), evaluated_at=SLOT)
    assert not any(
        str(item.get("status") or "").lower() == "confirmed"
        and str(item.get("setup_id") or "") == yesterday["setup_id"]
        for item in _transition_list(result)
    ), result


def test_partial_projection_keeps_healthy_spy_eligible_when_another_symbol_is_bad() -> None:
    from app.intraday.events import evaluate

    spy = _projection(
        symbol="SPY",
        bars=_shock_bars(-3.0),
        status="ok",
    )["symbols"]["SPY"]
    bad = _projection(symbol="AAA", status="unavailable")["symbols"]["AAA"]
    projection = _projection(status="partial", symbols={"SPY": spy, "AAA": bad})
    result = evaluate(projection, {}, _spec(), evaluated_at=SLOT)

    assert _strategy_transitions(result, "anomaly") or _strategy_transitions(result, "drop")
    assert any(
        "SPY" in str(item.get("symbol") or item.get("setup_id") or "")
        for item in _transition_list(result)
    )


def test_real_p3_projection_partial_tail_does_not_block_healthy_spy_event(tmp_path: Path) -> None:
    from app.intraday.equities import build_intraday_projection
    from app.intraday.events import evaluate

    contract = _capture_contract(
        "capture-real-partial",
        SERVICE_SLOT,
        include_spy=True,
        missing_tail_symbol="AAA",
        shock_symbol="SPY",
    )
    projection = build_intraday_projection(contract, now=SERVICE_SLOT)
    assert projection["status"] in {"partial", "stale"}
    assert projection["symbols"]["SPY"]["status"] == "ok"
    assert projection["symbols"]["SPY"]["as_of"] == SERVICE_SLOT.isoformat()
    assert projection["symbols"]["AAA"]["as_of"] != SERVICE_SLOT.isoformat()

    result = evaluate(projection, _state(), _spec(), evaluated_at=SERVICE_SLOT)
    assert any(
        str(item.get("symbol") or "") == "SPY"
        and str(item.get("status") or "").lower() in {"confirmed", "candidate"}
        for item in _transition_list(result)
    ), result


def test_tail_as_of_before_cutoff_is_unavailable_even_when_age_is_under_ten_minutes() -> None:
    from app.intraday.events import evaluate

    projection = _projection(bars=_shock_bars(-3.0), status="stale")
    projection["symbols"]["AAA"]["status"] = "stale"
    projection["symbols"]["AAA"]["as_of"] = (SLOT - timedelta(minutes=5)).isoformat()
    projection["freshness"]["age_seconds"] = 300
    result = evaluate(projection, _state(), _spec(), evaluated_at=SLOT)

    assert not (_strategy_transitions(result, "s3") or _strategy_transitions(result, "anomaly") or _strategy_transitions(result, "drop")), result
    assert any(
        item.get("available") is False
        and str(item.get("reason_code") or "") in {"symbol_stale", "stale_source", "source_stale", "incomplete_bars"}
        for item in _capabilities(result, "s3")
    ), result["capabilities"]


def test_gap_resets_confirmation_and_expires_candidate_as_coverage_gap() -> None:
    from app.intraday.events import evaluate

    prior = _state(_setup(bars_elapsed=2))
    gap_cutoff = SLOT + timedelta(minutes=10)
    result = evaluate(
        _projection(status="partial", source={"scheduled_at": gap_cutoff.isoformat()}),
        prior,
        _spec(),
        evaluated_at=gap_cutoff,
    )
    expired = [
        item
        for item in _transition_list(result)
        if str(item.get("status") or item.get("transition") or "").lower() == "expired"
    ]
    assert expired, result
    assert any(
        "coverage_gap" in str(
            item.get("reason_code") or item.get("reasons") or item.get("reason")
        )
        for item in expired
    )
    assert all(item.get("evaluated_at") for item in expired), expired


@pytest.mark.parametrize(
    ("setup_type", "close", "expected_status"),
    [
        ("ma5_reclaim", 100.05, "confirmed"),
        ("ma5_break", 99.95, "confirmed"),
        ("ma5_retest_hold", 100.05, "confirmed"),
        ("ma5_failed_reclaim", 99.95, "confirmed"),
    ],
)
def test_ma5_next_bar_lifecycle_confirms_or_invalidates_on_frozen_lines(
    setup_type: str,
    close: float,
    expected_status: str,
) -> None:
    from app.intraday.events import evaluate

    prior_setup = _setup(
        setup_type=setup_type,
        confirmation_level=100.02,
        invalidation_level=99.98,
    )
    result = evaluate(
        _projection(bars=_flat_range_bars(current_close=close)),
        _state(prior_setup),
        _spec(),
        evaluated_at=SLOT,
    )
    transitions = _strategy_transitions(result, "s2", setup_type)
    assert any(str(item.get("status") or "").lower() == expected_status for item in transitions), result
    lifecycle = next(
        item
        for item in transitions
        if str(item.get("status") or "").lower() == expected_status
    )
    assert lifecycle.get("confirmation_level") == pytest.approx(100.02)
    assert lifecycle.get("invalidation_level") == pytest.approx(99.98)


def test_ma5_candidate_expires_after_three_bars_without_backdating_or_false_failure() -> None:
    from app.intraday.events import evaluate

    prior_setup = _setup(
        bars_elapsed=3,
        started_at=SLOT - timedelta(minutes=15),
        last_cutoff=SLOT - timedelta(minutes=5),
    )
    result = evaluate(
        _projection(bars=_flat_range_bars(current_close=100.0)),
        _state(prior_setup),
        _spec(),
        evaluated_at=SLOT,
    )
    expired = [
        item
        for item in _strategy_transitions(result, "s2")
        if str(item.get("status") or "").lower() == "expired"
    ]
    assert expired, result
    assert all(
        "coverage_gap" not in str(item.get("reason_code") or "")
        for item in expired
    )


def test_confirmed_ma5_setup_invalidates_on_reverse_cross_despite_cooldown() -> None:
    from app.intraday.events import evaluate

    def bars_for(closes: Sequence[float]) -> list[dict[str, Any]]:
        return [
            _bar(index, close, high=close + 0.05, low=close - 0.05)
            for index, close in enumerate(closes)
        ]

    candidate = evaluate(
        _projection(bars=bars_for([100.0] * 15 + [100.0251])),
        {},
        _spec(),
        evaluated_at=SLOT,
    )
    candidate_transition = next(
        item for item in _strategy_transitions(candidate, "s2", "reclaim")
        if str(item.get("status") or "").lower() == "candidate"
    )
    setup_id = candidate_transition["setup_id"]

    confirmed = evaluate(
        _projection(bars=bars_for([100.0] * 15 + [100.0251, 100.05])),
        candidate["state"],
        _spec(),
        evaluated_at=SLOT + timedelta(minutes=5),
    )
    assert any(
        item.get("setup_id") == setup_id
        and str(item.get("status") or "").lower() == "confirmed"
        for item in _transition_list(confirmed)
    ), confirmed

    reverse = evaluate(
        _projection(bars=bars_for([100.0] * 15 + [100.0251, 100.05, 99.95])),
        confirmed["state"],
        _spec(),
        evaluated_at=SLOT + timedelta(minutes=10),
    )
    invalidations = [
        item
        for item in _strategy_transitions(reverse, "s2", "reclaim")
        if item.get("setup_id") == setup_id
        and str(item.get("status") or "").lower() == "invalidated"
    ]
    assert invalidations, reverse
    assert invalidations[0].get("invalidation_level") == pytest.approx(
        candidate_transition["invalidation_level"]
    )


def test_candidate_ttl_uses_the_supplied_spec_value() -> None:
    from app.intraday.events import evaluate

    spec = _spec()
    spec["s2"] = {**spec["s2"], "candidate_ttl_bars": 2}
    prior = _state(
        _setup(
            bars_elapsed=1,
            started_at=SLOT - timedelta(minutes=5),
            last_cutoff=SLOT - timedelta(minutes=5),
        )
    )
    result = evaluate(
        _projection(bars=_flat_range_bars(current_close=100.0)),
        prior,
        spec,
        evaluated_at=SLOT,
    )
    expired = [
        item
        for item in _strategy_transitions(result, "s2")
        if str(item.get("status") or "").lower() == "expired"
    ]
    assert expired, result
    assert any(item.get("reason_code") == "candidate_ttl" for item in expired), result


@pytest.mark.parametrize(
    ("trading_date", "cutoff", "evaluated_at"),
    (
        (
            "2026-10-16",
            datetime(2026, 10, 16, 19, 55, tzinfo=UTC),  # 15:55 ET, regular close 16:00 ET
            datetime(2026, 10, 16, 20, 10, tzinfo=UTC),
        ),
        (
            "2026-11-27",
            datetime(2026, 11, 27, 17, 55, tzinfo=UTC),  # 12:55 ET, half-day close 13:00 ET
            datetime(2026, 11, 27, 18, 10, tzinfo=UTC),
        ),
    ),
)
def test_expire_at_session_close_uses_exchange_calendar_for_regular_and_half_day(
    trading_date: str,
    cutoff: datetime,
    evaluated_at: datetime,
) -> None:
    from app.intraday.events import evaluate

    setup = _setup(
        status="candidate",
        started_at=cutoff,
        last_cutoff=cutoff,
        bars_elapsed=1,
    )
    setup["trading_date"] = trading_date
    # Keep candidate TTL after the close so only the session-close rule can
    # expire it. The exchange calendar, rather than a hard-coded 16:00, decides
    # both the regular and early-close cases.
    setup["expires_at"] = (evaluated_at + timedelta(hours=1)).isoformat()
    projection = _projection(
        timestamp_verified=False,
        source={
            "task_name": "p4-equities-fixture",
            "capture_id": f"capture-close-{trading_date}",
            "content_sha256": "d" * 64,
            "scheduled_at": cutoff.isoformat(),
            "trading_date": trading_date,
        },
    )
    result = evaluate(
        projection,
        _state(setup),
        _spec(),
        evaluated_at=evaluated_at,
    )

    expired = [
        item
        for item in _strategy_transitions(result, "s2")
        if str(item.get("status") or "").lower() == "expired"
    ]
    assert expired, result
    assert result["state"]["setups"][0]["status"] == "expired"


def test_cooldown_blocks_only_new_candidate_not_confirmation_invalidation_or_price_risk() -> None:
    from app.intraday.events import evaluate

    cooldown = SLOT + timedelta(minutes=10)
    prior = _state(
        _setup(
            status="candidate",
            cooldown_until=cooldown,
            confirmation_level=100.02,
            invalidation_level=99.98,
        )
    )
    invalidated = evaluate(
        _projection(bars=_flat_range_bars(current_close=99.95)),
        prior,
        _spec(),
        evaluated_at=SLOT,
    )
    assert any(
        str(item.get("status") or "").lower() == "invalidated"
        for item in _strategy_transitions(invalidated, "s2")
    ), invalidated

    risk = evaluate(
        _projection(bars=_shock_bars(-3.0)),
        prior,
        _spec(),
        evaluated_at=SLOT,
    )
    assert _strategy_transitions(risk, "s3") or _strategy_transitions(risk, "anomaly") or _strategy_transitions(risk, "drop"), risk


def test_candidate_cooldown_is_scoped_to_strategy_and_direction() -> None:
    """A recent S2 reclaim must not suppress an independent S1 signal."""
    from app.intraday.events import evaluate

    prior = _state(
        _setup(
            strategy="S2",
            setup_type="ma5_reclaim",
            status="confirmed",
            started_at=SLOT - timedelta(minutes=5),
            last_cutoff=SLOT - timedelta(minutes=5),
            cooldown_until=SLOT + timedelta(minutes=10),
        )
    )
    state: Mapping[str, Any] = prior
    last_result: Mapping[str, Any] | None = None
    # Build S1's t-2 evidence through real evaluator calls.  The pre-existing
    # S2 cooldown is deliberately carried in the same state snapshot.
    for index in (13, 14, 15):
        projection = _rs_projection(index)
        last_result = evaluate(
            projection,
            state,
            _spec(),
            evaluated_at=BASE + timedelta(minutes=5 * (index + 1)),
        )
        state = last_result["state"]

    assert last_result is not None
    assert _strategy_transitions(last_result, "S1", "relative_strength"), last_result


def test_s3_impulse_requires_strict_price_move_rvol_and_close_quarter() -> None:
    from app.intraday.events import evaluate

    def impulse_projection(
        *,
        delta: float,
        rvol: float,
        close_location: str,
        valid_context: bool = True,
    ) -> dict[str, Any]:
        previous = 100.0
        current = previous + delta
        if close_location == "top":
            low, high = current - 1.0, current + 0.05
        else:
            low, high = current - 0.05, current + 1.0
        bars = [
            _bar(index, previous, high=previous + 0.5, low=previous - 0.5)
            for index in range(15)
        ]
        bars.append(_bar(15, current, high=high, low=low))
        bars[-1]["volume"] = 1_000.0 * rvol
        bars[-1]["turnover"] = bars[-1]["close"] * bars[-1]["volume"]
        context = {
            "valid_days": 10,
            "baseline_median_volume": 1_000.0,
            "quality_status": "ready",
            "as_of_session_date": "2026-10-15",
            "slot_start": bars[-1]["bar_start_at"],
        }
        if not valid_context:
            context["quality_status"] = "stale"
        return _projection(bars=bars, features={"rvol_context": context})

    equal = evaluate(
        impulse_projection(delta=1.2, rvol=1.8, close_location="top"),
        {},
        _spec(),
        evaluated_at=SLOT,
    )
    assert not (_strategy_transitions(equal, "s3") or _strategy_transitions(equal, "impulse") or _strategy_transitions(equal, "volume")), equal

    below_rvol = evaluate(
        impulse_projection(delta=1.2001, rvol=1.799, close_location="top"),
        {},
        _spec(),
        evaluated_at=SLOT,
    )
    assert not (_strategy_transitions(below_rvol, "impulse") or _strategy_transitions(below_rvol, "volume")), below_rvol

    valid = evaluate(
        impulse_projection(delta=1.2001, rvol=1.8, close_location="top"),
        {},
        _spec(),
        evaluated_at=SLOT,
    )
    assert _strategy_transitions(valid, "impulse") or _strategy_transitions(valid, "volume"), valid

    invalid_context = evaluate(
        impulse_projection(delta=1.2001, rvol=2.0, close_location="top", valid_context=False),
        {},
        _spec(),
        evaluated_at=SLOT,
    )
    assert not (
        _strategy_transitions(invalid_context, "impulse")
        or _strategy_transitions(invalid_context, "volume")
    ), invalid_context
    assert any(
        item.get("available") is True
        and item.get("volume_impulse_available") is False
        for item in _capabilities(invalid_context, "s3")
    ), invalid_context["capabilities"]


def test_same_cutoff_different_tasks_do_not_confirm_each_others_setup() -> None:
    from app.intraday.events import evaluate

    first = _projection(
        bars=_flat_range_bars(current_close=100.0251),
        source={
            "task_name": "equities-core",
            "capture_id": "capture-core",
            "content_sha256": "b" * 64,
            "scheduled_at": SLOT.isoformat(),
            "trading_date": "2026-10-16",
        },
    )
    first_result = evaluate(first, _state(), _spec(), evaluated_at=SLOT)
    second = _projection(
        bars=_flat_range_bars(current_close=100.05),
        source={
            "task_name": "equities-rotation",
            "capture_id": "capture-rotation",
            "content_sha256": "c" * 64,
            "scheduled_at": SLOT.isoformat(),
            "trading_date": "2026-10-16",
        },
    )
    result = evaluate(second, first_result["state"], _spec(), evaluated_at=SLOT)
    assert not any(
        str(item.get("status") or "").lower() == "confirmed"
        and str(item.get("task_name") or "") == "equities-core"
        for item in _transition_list(result)
    ), result


def _capture_contract(
    capture_id: str,
    scheduled_at: datetime,
    *,
    drop_last_bar: bool = False,
    candidate_reclaim: bool = False,
    task_name: str = "p4-equities-fixture",
    include_spy: bool = False,
    missing_tail_symbol: str | None = None,
    shock_symbol: str | None = None,
) -> dict[str, Any]:
    """Make a small valid P2-shaped source capture for service tests."""
    from app.decision_harness.contracts import content_sha256

    minute_bars: list[dict[str, Any]] = []
    minute_count = max(5, int((scheduled_at - BASE).total_seconds() // 60))
    final_window_start = minute_count - 5
    symbols = ["AAA", "SPY"] if include_spy else ["AAA"]
    for symbol in symbols:
        for index in range(minute_count):
            if missing_tail_symbol == symbol and index >= final_window_start:
                continue
            start = BASE + timedelta(minutes=index)
            base_close = 500.0 if symbol == "SPY" else 100.0
            bar_close = base_close + (index * 0.02 if symbol == "AAA" else 0.0)
            if candidate_reclaim and symbol == "AAA" and index >= final_window_start:
                bar_close = 100.03
            if drop_last_bar and symbol == "AAA" and index >= final_window_start:
                bar_close -= 4.0
            if shock_symbol == symbol and index >= final_window_start:
                bar_close -= 4.0
            minute_bars.append(
                {
                    "symbol": symbol,
                    "bar_start_at": start.isoformat(),
                    "bar_end_at": (start + timedelta(minutes=1)).isoformat(),
                    "open": bar_close,
                    "high": bar_close + 0.2,
                    "low": bar_close - 0.2,
                    "close": bar_close,
                    "volume": 1_000.0,
                    "turnover": bar_close * 1_000.0,
                    "quality_state": "ready",
                }
            )
    quote_symbols = symbols
    minute_symbols = symbols
    data = {
        "schema_version": "moomoo-intraday-equities-v1",
        "provider": "moomoo_openapi",
        "trading_date": "2026-10-16",
        "scheduled_at": scheduled_at.isoformat(),
        "evidence_cutoff": scheduled_at.isoformat(),
        "minute_timestamp_role": "start",
        "minute_timestamp_policy": {
            "source_fields": ["bar_start_at", "bar_end_at"],
            "role": "start",
            "interval_seconds": 60,
            "provider_boundary_verified": True,
        },
        "slot_index": 0,
        "rotation": 0,
        "quote_symbols": quote_symbols,
        "minute_symbols": minute_symbols,
        "group_by_symbol": {symbol: ("A" if symbol == "SPY" else "C") for symbol in symbols},
        "quotes": [{"symbol": symbol, "last_price": (500.0 if symbol == "SPY" else 100.0)} for symbol in symbols],
        "minute_bars": minute_bars,
        "invalid_minute_bars": [],
        "minute_bar_unavailable_symbols": [],
        "unavailable_quote_symbols": [],
        "history_quota": {"available": True, "requested_symbols": symbols, "admitted_symbols": symbols, "deferred_symbols": [], "admissions": []},
        "quality_summary": {"complete": missing_tail_symbol is None, "status": "ok" if missing_tail_symbol is None else "partial", "quote_coverage": 1.0, "minute_symbols_requested": len(symbols), "minute_symbols_admitted": len(symbols), "minute_bars_returned": len(minute_bars), "unavailable_minute_symbols": [], "quality_warnings": []},
        "provider_raw": {"market_snapshot_batches": [], "history_kline_batches": []},
    }
    return {
        "schema_version": "urus.moomoo.capture.v2",
        "capture_id": capture_id,
        "kind": "capture_intraday_equities",
        "task_name": task_name,
        "provider": "moomoo_openapi",
        "scheduled_at": scheduled_at.isoformat(),
        "trading_date": "2026-10-16",
        "captured_at": (scheduled_at + timedelta(seconds=1)).isoformat(),
        "requested_symbols": symbols,
        "content_sha256": content_sha256(data),
        "data": data,
    }


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
            encoded = json.dumps(contract, sort_keys=True)
            connection.execute(
                "INSERT INTO raw_captures VALUES (?, ?, ?, ?, ?)",
                (contract["capture_id"], contract["schema_version"], contract["kind"], contract["captured_at"], encoded),
            )
            connection.execute(
                "INSERT INTO scheduled_captures VALUES (?, ?, ?, ?, ?, ?)",
                (contract["task_name"], contract["scheduled_at"], contract["capture_id"], contract["kind"], 1, contract["captured_at"]),
            )


def test_settings_default_disables_intraday_event_consumer() -> None:
    from app.core.config import Settings

    assert Settings().intraday_events_enabled is False


def test_watch_cli_is_rejected_when_event_consumer_is_disabled(tmp_path: Path) -> None:
    environment = {
        "INTRADAY_EVENTS_ENABLED": "false",
        "INTRADAY_EQUITIES_ENABLED": "false",
        "DATABASE_URL": f"sqlite:///{tmp_path / 'primary.sqlite'}",
        "SIDECAR_CAPTURE_DATABASE_PATH": str(tmp_path / "missing-sidecar.sqlite"),
        "MOOMOO_ENABLED": "false",
        "FRED_ENABLED": "false",
        "YAHOO_ENABLED": "false",
    }
    completed = subprocess.run(
        [sys.executable, "-m", "app.intraday.event_consumer", "--watch"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, **environment},
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}".lower()
    assert completed.returncode != 0, completed.stdout
    assert "disabled" in output or "关闭" in output, output


def test_run_once_is_bounded_and_idempotent_for_same_source_slot(tmp_path: Path) -> None:
    from sqlalchemy import create_engine, func, select
    from sqlalchemy.orm import Session

    from app.core.database import Base
    from app.intraday.event_service import run_once
    from app.models.intraday_events import IntradayEventIngestCursorModel, IntradayEventModel

    source = tmp_path / "sidecar.sqlite"
    contract = _capture_contract("capture-p4-1", SLOT, drop_last_bar=True)
    _write_sidecar(source, [contract])
    engine = create_engine(f"sqlite:///{tmp_path / 'events.sqlite'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = lambda: Session(engine)

    first = run_once(source_db_path=source, session_factory=session_factory, max_slots=1, now=SLOT + timedelta(minutes=1))
    second = run_once(source_db_path=source, session_factory=session_factory, max_slots=1, now=SLOT + timedelta(minutes=2))

    assert first.get("processed_slots", first.get("processed", 0)) <= 1
    assert second.get("processed_slots", second.get("processed", 0)) <= 1
    with session_factory() as session:
        event_count = session.scalar(select(func.count()).select_from(IntradayEventModel))
        cursor_count = session.scalar(select(func.count()).select_from(IntradayEventIngestCursorModel))
    assert event_count is not None
    assert cursor_count == 1
    # Re-running a committed slot must not duplicate immutable transitions.
    assert int(event_count) == int(first.get("event_count", event_count))


def test_run_once_uses_explicit_now_to_expire_candidate_when_no_new_source_exists(tmp_path: Path) -> None:
    from sqlalchemy import select
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core.database import Base
    from app.intraday.event_service import run_once
    from app.models.intraday_events import (
        IntradayEventIngestCursorModel,
        IntradayEventModel,
        IntradaySetupStateModel,
    )

    source = tmp_path / "sidecar.sqlite"
    contract = _capture_contract("capture-candidate", SERVICE_SLOT, candidate_reclaim=True)
    _write_sidecar(source, [contract])
    engine = create_engine(f"sqlite:///{tmp_path / 'events.sqlite'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = lambda: Session(engine)

    first = run_once(source_db_path=source, session_factory=session_factory, max_slots=1, now=SERVICE_SLOT)
    assert first.get("status") in {"ok", "partial"}, first
    with session_factory() as session:
        cursor_before = session.get(IntradayEventIngestCursorModel, "intraday-events-live-v1")
        assert cursor_before is not None
        source_cursor_before = cursor_before.last_scheduled_at
        initial_events = list(session.scalars(select(IntradayEventModel)))
        assert initial_events, first
        active_setup_ids = {
            event.setup_id
            for event in initial_events
            if event.status in {"candidate", "confirmed"}
        }
        assert active_setup_ids, initial_events

    with sqlite3.connect(source) as connection:
        connection.execute("DELETE FROM scheduled_captures")
        connection.execute("DELETE FROM raw_captures")
        connection.commit()

    expiry = run_once(
        source_db_path=source,
        session_factory=session_factory,
        max_slots=1,
        now=SERVICE_SLOT + timedelta(minutes=30),
    )
    assert int(expiry.get("setups_expired", 0)) >= 1, expiry
    with session_factory() as session:
        cursor_after = session.get(IntradayEventIngestCursorModel, "intraday-events-live-v1")
        assert cursor_after is not None
        assert cursor_after.last_scheduled_at == source_cursor_before
        events = list(session.scalars(select(IntradayEventModel)))
        expired_setup_ids = {
            event.setup_id for event in events if event.status == "expired"
        }
        assert active_setup_ids <= expired_setup_ids
        active_state_ids = {
            str(setup.get("setup_id"))
            for row in session.scalars(select(IntradaySetupStateModel))
            for setup in (row.state_json or {}).get("setups", [])
            if isinstance(setup, Mapping) and setup.get("status") in {"candidate", "confirmed"}
        }
        assert not active_setup_ids.intersection(active_state_ids)


def test_two_concurrent_watch_iterations_do_not_duplicate_a_source_slot(tmp_path: Path) -> None:
    """Two local watch iterations may race, but one immutable slot is committed once."""
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import create_engine, func, select
    from sqlalchemy.orm import Session

    from app.core.database import Base
    from app.intraday.event_service import run_once
    from app.models.intraday_events import IntradayEventIngestCursorModel, IntradayEventModel

    source = tmp_path / "sidecar.sqlite"
    _write_sidecar(source, [_capture_contract("capture-concurrent", SERVICE_SLOT, drop_last_bar=True)])
    engine = create_engine(
        f"sqlite:///{tmp_path / 'events.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    session_factory = lambda: Session(engine)

    def consume_once(_: int) -> dict[str, Any]:
        return run_once(
            source_db_path=source,
            session_factory=session_factory,
            max_slots=1,
            now=SERVICE_SLOT + timedelta(minutes=1),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(consume_once, (1, 2)))

    assert any(result.get("status") in {"ok", "partial", "idle"} for result in results), results
    with session_factory() as session:
        event_count = session.scalar(select(func.count()).select_from(IntradayEventModel))
        cursor_count = session.scalar(select(func.count()).select_from(IntradayEventIngestCursorModel))
        unique_transitions = session.execute(
            select(IntradayEventModel.setup_id, IntradayEventModel.transition_seq).distinct()
        ).all()
    assert cursor_count == 1
    assert event_count == len(unique_transitions)


def test_concurrent_idle_expiry_reloads_state_and_does_not_revert_cursor(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import create_engine, func, select
    from sqlalchemy.orm import Session

    from app.core.database import Base
    from app.intraday.event_service import run_once
    from app.models.intraday_events import (
        IntradayEventIngestCursorModel,
        IntradayEventModel,
        IntradaySetupStateModel,
    )

    source = tmp_path / "sidecar.sqlite"
    _write_sidecar(source, [_capture_contract("capture-expiry-race", SERVICE_SLOT, candidate_reclaim=True)])
    engine = create_engine(
        f"sqlite:///{tmp_path / 'events.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    session_factory = lambda: Session(engine)
    first = run_once(source_db_path=source, session_factory=session_factory, max_slots=1, now=SERVICE_SLOT)
    assert first.get("status") in {"ok", "partial"}, first
    with session_factory() as session:
        cursor_before = session.get(IntradayEventIngestCursorModel, "intraday-events-live-v1")
        assert cursor_before is not None
        source_cursor = cursor_before.last_scheduled_at
        initial_events = list(session.scalars(select(IntradayEventModel)))
        active_setup_ids = {
            event.setup_id
            for event in initial_events
            if event.status in {"candidate", "confirmed"}
        }
        assert active_setup_ids, initial_events

    with sqlite3.connect(source) as connection:
        connection.execute("DELETE FROM scheduled_captures")
        connection.execute("DELETE FROM raw_captures")
        connection.commit()

    def expire_once(offset: int) -> dict[str, Any]:
        return run_once(
            source_db_path=source,
            session_factory=session_factory,
            max_slots=1,
            now=SERVICE_SLOT + timedelta(minutes=30 + offset),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(expire_once, (0, 1)))

    with session_factory() as session:
        cursor_after = session.get(IntradayEventIngestCursorModel, "intraday-events-live-v1")
        assert cursor_after is not None
        assert cursor_after.last_scheduled_at == source_cursor
        events = list(session.scalars(select(IntradayEventModel)))
        expired_setup_ids = {
            event.setup_id for event in events if event.status == "expired"
        }
        assert active_setup_ids <= expired_setup_ids
        states = list(session.scalars(select(IntradaySetupStateModel)))
        active_state_ids = {
            str(setup.get("setup_id"))
            for row in states
            for setup in (row.state_json or {}).get("setups", [])
            if isinstance(setup, Mapping) and setup.get("status") in {"candidate", "confirmed"}
        }
        assert not active_setup_ids.intersection(active_state_ids)


def test_cursor_is_bounded_and_advances_monotonically_across_bounded_batches(
    tmp_path: Path,
) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core.database import Base
    from app.intraday.event_service import run_once
    from app.models.intraday_events import IntradayEventIngestCursorModel

    source = tmp_path / "sidecar.sqlite"
    # The implementation retains the latest 128 source identities.  Use
    # compact five-minute captures at one cutoff so this exercises the bound
    # without producing a large multi-day payload.
    retention_limit = 128
    contracts = [
        _capture_contract(
            f"capture-cursor-{index:03d}",
            BASE + timedelta(minutes=5),
            task_name=f"cursor-task-{index:03d}",
        )
        for index in range(retention_limit + 2)
    ]
    _write_sidecar(source, contracts)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'events.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_factory = lambda: Session(engine)

    cursor_times: list[datetime] = []
    cursor_tasks: list[str] = []
    for _ in contracts:
        result = run_once(
            source_db_path=source,
            session_factory=session_factory,
            max_slots=1,
            now=BASE + timedelta(minutes=10),
        )
        assert result.get("status") in {"ok", "partial", "idle"}, result
        with session_factory() as session:
            cursor = session.get(IntradayEventIngestCursorModel, "intraday-events-live-v1")
            assert cursor is not None
            assert cursor.last_scheduled_at is not None
            cursor_times.append(datetime.fromisoformat(str(cursor.last_scheduled_at)).astimezone(UTC))
            assert cursor.last_task_name is not None
            cursor_tasks.append(str(cursor.last_task_name))
            assert len(cursor.seen_source_slots_json) <= retention_limit

    assert cursor_times == sorted(cursor_times)
    assert cursor_tasks == sorted(cursor_tasks)
    with session_factory() as session:
        cursor = session.get(IntradayEventIngestCursorModel, "intraday-events-live-v1")
        assert cursor is not None
        assert len(cursor.seen_source_slots_json) == retention_limit


def test_run_once_late_revision_is_skipped_without_replaying_realtime_state(tmp_path: Path) -> None:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.core.database import Base
    from app.intraday.event_service import run_once
    from app.models.intraday_events import (
        IntradayEventIngestCursorModel,
        IntradayEventModel,
        IntradaySetupStateModel,
    )

    source = tmp_path / "sidecar.sqlite"
    latest = _capture_contract("capture-latest", SERVICE_SLOT, drop_last_bar=True)
    _write_sidecar(source, [latest])
    engine = create_engine(f"sqlite:///{tmp_path / 'events.sqlite'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = lambda: Session(engine)
    first = run_once(source_db_path=source, session_factory=session_factory, max_slots=1, now=SERVICE_SLOT)
    with session_factory() as session:
        cursor_before = session.get(IntradayEventIngestCursorModel, "intraday-events-live-v1")
        assert cursor_before is not None
        cursor_time_before = cursor_before.last_scheduled_at
        cursor_task_before = cursor_before.last_task_name
        event_count_before = session.query(IntradayEventModel).count()
        states_before = {
            row.task_name: dict(row.state_json or {})
            for row in session.scalars(select(IntradaySetupStateModel))
        }

    # Insert a late, older capture after the realtime cursor has advanced.
    late_slot = SERVICE_SLOT - timedelta(minutes=5)
    late = _capture_contract("capture-late", late_slot, drop_last_bar=True)
    with sqlite3.connect(source) as connection:
        encoded = json.dumps(late, sort_keys=True)
        connection.execute(
            "INSERT INTO raw_captures VALUES (?, ?, ?, ?, ?)",
            (late["capture_id"], late["schema_version"], late["kind"], late["captured_at"], encoded),
        )
        connection.execute(
            "INSERT INTO scheduled_captures VALUES (?, ?, ?, ?, ?, ?)",
            (late["task_name"], late["scheduled_at"], late["capture_id"], late["kind"], 1, late["captured_at"]),
        )
        connection.commit()

    second = run_once(source_db_path=source, session_factory=session_factory, max_slots=1, now=SERVICE_SLOT + timedelta(minutes=1))
    assert second.get("events_created", 0) == 0, second
    with session_factory() as session:
        cursor_after = session.get(IntradayEventIngestCursorModel, "intraday-events-live-v1")
        assert cursor_after is not None
        assert cursor_after.last_scheduled_at == cursor_time_before
        assert cursor_after.last_task_name == cursor_task_before
        assert session.query(IntradayEventModel).count() == event_count_before
        states_after = {
            row.task_name: dict(row.state_json or {})
            for row in session.scalars(select(IntradaySetupStateModel))
        }
    assert states_after == states_before


def test_run_once_transaction_failure_rolls_back_state_event_and_cursor(tmp_path: Path) -> None:
    from sqlalchemy import create_engine, event, func, select
    from sqlalchemy.orm import Session

    from app.core.database import Base
    from app.intraday.event_service import run_once
    from app.models.intraday_events import IntradayEventIngestCursorModel, IntradayEventModel, IntradaySetupStateModel

    source = tmp_path / "sidecar.sqlite"
    _write_sidecar(source, [_capture_contract("capture-rollback", SERVICE_SLOT, drop_last_bar=True)])
    engine = create_engine(f"sqlite:///{tmp_path / 'events.sqlite'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = lambda: Session(engine)

    def fail_before_commit(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("injected transaction failure")

    event.listen(Session, "before_commit", fail_before_commit)
    try:
        result = run_once(source_db_path=source, session_factory=session_factory, max_slots=1, now=SERVICE_SLOT)
    finally:
        event.remove(Session, "before_commit", fail_before_commit)

    assert result.get("status") == "error" or result.get("errors"), result
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(IntradaySetupStateModel)) == 0
        assert session.scalar(select(func.count()).select_from(IntradayEventModel)) == 0
        assert session.scalar(select(func.count()).select_from(IntradayEventIngestCursorModel)) == 0


def test_events_after_id_paginates_same_cutoff_without_duplicates_or_gaps(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core.config import Settings
    from app.core.database import Base
    from app.intraday.event_service import run_once
    from app.main import create_app

    source = tmp_path / "sidecar.sqlite"
    _write_sidecar(
        source,
        [
            _capture_contract("capture-core", SERVICE_SLOT, drop_last_bar=True, task_name="equities-core"),
            _capture_contract("capture-rotation", SERVICE_SLOT, drop_last_bar=True, task_name="equities-rotation"),
        ],
    )
    primary = tmp_path / "events.sqlite"
    engine = create_engine(f"sqlite:///{primary}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = lambda: Session(engine)
    run_once(source_db_path=source, session_factory=session_factory, max_slots=2, now=SERVICE_SLOT)
    engine.dispose()

    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{primary}",
        sidecar_capture_database_path=str(source),
        intraday_equities_enabled=False,
        intraday_events_enabled=True,
        moomoo_enabled=False,
        fred_enabled=False,
        yahoo_enabled=False,
        anomalo_workflow_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        latest = client.get("/api/intraday/events?limit=2")
        first_page = client.get("/api/intraday/events?after_id=0&limit=2")
        invalid = client.get("/api/intraday/events?after_id=not-an-int")
        negative = client.get("/api/intraday/events?after_id=-1")

        assert latest.status_code == 200, latest.text
        assert first_page.status_code == 200, first_page.text
        latest_payload = latest.json()
        first_payload = first_page.json()
        latest_ids = [int(item["id"]) for item in latest_payload.get("events", [])]
        first_ids = [int(item["id"]) for item in first_payload.get("events", [])]
        assert len(first_ids) >= 2, first_payload
        assert first_ids == sorted(first_ids)
        assert latest_ids == sorted(latest_ids)
        assert len(set(first_ids)) == len(first_ids)

        collected = list(first_ids)
        payload = first_payload
        while payload.get("has_more"):
            cursor = payload.get("next_after_id")
            assert cursor is not None
            page = client.get(f"/api/intraday/events?after_id={int(cursor)}&limit=2")
            assert page.status_code == 200, page.text
            payload = page.json()
            page_ids = [int(item["id"]) for item in payload.get("events", [])]
            assert page_ids == sorted(page_ids)
            assert not set(collected).intersection(page_ids)
            collected.extend(page_ids)

        # The omitted cursor form returns the latest tail, while explicit
        # after_id=0 starts at the oldest event.
        assert latest_ids == collected[-len(latest_ids) :]

    assert invalid.status_code in {400, 422}
    assert negative.status_code in {400, 422}
    assert collected == sorted(set(collected))


def test_run_once_rejects_same_processor_with_a_different_source_path(tmp_path: Path) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core.database import Base
    from app.intraday.event_service import run_once

    source_a = tmp_path / "sidecar-a.sqlite"
    source_b = tmp_path / "sidecar-b.sqlite"
    _write_sidecar(source_a, [_capture_contract("capture-a", SLOT)])
    _write_sidecar(source_b, [_capture_contract("capture-b", SLOT)])
    engine = create_engine(f"sqlite:///{tmp_path / 'events.sqlite'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = lambda: Session(engine)
    run_once(source_db_path=source_a, session_factory=session_factory, max_slots=1, now=SLOT)

    with pytest.raises((ValueError, RuntimeError)):
        run_once(source_db_path=source_b, session_factory=session_factory, max_slots=1, now=SLOT)


def test_run_once_accepts_real_collector_store_capture_timestamp_boundary(tmp_path: Path) -> None:
    """The P2 producer's stored_at is independent from envelope captured_at."""
    source_root = Path(__file__).resolve().parents[2] / "urus-moomoo" / "src"
    sys.path.insert(0, str(source_root))
    from urus_moomoo.contracts import capture_contract
    from urus_moomoo.storage.sqlite import CollectorStore

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core.database import Base
    from app.intraday.event_service import run_once

    source = tmp_path / "collector-real.sqlite"
    # Keep the scheduled slot before the test runner's wall clock so the
    # producer-created captured_at is a valid observation. The helper's bars
    # are intentionally outside this tiny source window; P3 can still build a
    # structurally valid unavailable projection, which is enough to exercise
    # the raw timestamp boundary.
    producer_slot = datetime(2026, 10, 15, 19, 0, tzinfo=UTC)
    data = dict(_capture_contract("capture-real-producer", producer_slot)["data"])
    data["trading_date"] = "2026-10-15"
    contract = capture_contract(
        kind="capture_intraday_equities",
        payload=data,
        requested_symbols=["AAA"],
        task_name="equities-capture-fixture",
        scheduled_at=producer_slot,
        trading_date="2026-10-15",
    )
    with CollectorStore(source) as store:
        assert store.write_scheduled_raw(
            "equities-capture-fixture",
            producer_slot,
            contract["capture_id"],
            contract["kind"],
            contract,
            schema_version=contract["schema_version"],
            completed=True,
        ) is True
        raw = store.read_raw(contract["capture_id"])
        assert raw is not None
        assert raw["captured_at"] != contract["captured_at"]

    engine = create_engine(f"sqlite:///{tmp_path / 'events.sqlite'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = lambda: Session(engine)
    result = run_once(
        source_db_path=source,
        session_factory=session_factory,
        max_slots=1,
        now=producer_slot + timedelta(seconds=1),
    )

    assert result.get("status") in {"ok", "partial", "idle"}, result
    assert not any(
        item.get("reason_code") == "invalid_source"
        for item in result.get("skipped_slots", [])
        if isinstance(item, Mapping)
    ), result


def test_intraday_event_api_is_read_only_and_disabled_by_default(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    primary = tmp_path / "primary.sqlite"
    source = tmp_path / "missing-sidecar.sqlite"
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{primary}",
        sidecar_capture_database_path=str(source),
        intraday_equities_enabled=False,
        intraday_events_enabled=False,
        moomoo_enabled=False,
        fred_enabled=False,
        yahoo_enabled=False,
        anomalo_workflow_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        before = hashlib.sha256(primary.read_bytes()).hexdigest()
        health = client.get("/api/intraday/health")
        events = client.get("/api/intraday/events?after_id=0&limit=20")
        after = hashlib.sha256(primary.read_bytes()).hexdigest()

    assert health.status_code == 200, health.text
    assert events.status_code == 200, events.text
    assert isinstance(health.json(), dict)
    assert isinstance(events.json(), dict)
    assert health.json().get("enabled") is False or health.json().get("status") in {"disabled", "idle"}
    assert events.json().get("events", []) == []
    assert before == after
    assert not source.exists()


def test_health_does_not_report_an_old_ok_cursor_as_current(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core.config import Settings
    from app.core.database import Base
    from app.main import create_app
    from app.models.intraday_events import IntradayEventIngestCursorModel

    primary = tmp_path / "events.sqlite"
    source = tmp_path / "missing-sidecar.sqlite"
    engine = create_engine(f"sqlite:///{primary}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            IntradayEventIngestCursorModel(
                processor_id="intraday-events-live-v1",
                source_path_identity=str(source.resolve()),
                last_scheduled_at=SERVICE_SLOT.isoformat(),
                last_task_name="p4-equities-fixture",
                last_capture_id="capture-old",
                last_content_sha256="e" * 64,
                seen_source_slots_json=[],
                backlog_remaining=False,
                last_status="ok",
                last_error=None,
                capabilities_summary_json={},
                updated_at=datetime.now(UTC) - timedelta(hours=2),
            )
        )
        session.commit()

    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{primary}",
        sidecar_capture_database_path=str(source),
        intraday_equities_enabled=False,
        intraday_events_enabled=True,
        moomoo_enabled=False,
        fred_enabled=False,
        yahoo_enabled=False,
        anomalo_workflow_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/intraday/health")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload.get("status") in {"stale", "idle", "error"}, payload
    assert payload.get("age_seconds") is not None or payload.get("last_evaluated_at") is not None, payload


def test_run_once_persists_source_parse_error_over_an_old_ok_cursor(tmp_path: Path) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core.database import Base
    from app.intraday.event_service import run_once
    from app.models.intraday_events import IntradayEventIngestCursorModel

    source = tmp_path / "broken-sidecar.sqlite"
    source.write_text("this is not sqlite", encoding="utf-8")
    engine = create_engine(f"sqlite:///{tmp_path / 'events.sqlite'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = lambda: Session(engine)
    with session_factory() as session:
        session.add(
            IntradayEventIngestCursorModel(
                processor_id="intraday-events-live-v1",
                source_path_identity=str(source.resolve()),
                last_scheduled_at=SERVICE_SLOT.isoformat(),
                last_task_name="p4-equities-fixture",
                last_capture_id="capture-old",
                last_content_sha256="f" * 64,
                seen_source_slots_json=[],
                backlog_remaining=False,
                last_status="ok",
                last_error=None,
                capabilities_summary_json={},
                updated_at=SERVICE_SLOT - timedelta(hours=2),
            )
        )
        session.commit()

    result = run_once(
        source_db_path=source,
        session_factory=session_factory,
        max_slots=1,
        now=SERVICE_SLOT + timedelta(minutes=1),
    )
    assert result.get("status") == "error", result
    with session_factory() as session:
        cursor = session.get(IntradayEventIngestCursorModel, "intraday-events-live-v1")
        assert cursor is not None
        assert cursor.last_status == "error"
        assert cursor.last_error


def test_run_once_persists_missing_source_error_over_an_old_ok_cursor(tmp_path: Path) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core.database import Base
    from app.intraday.event_service import run_once
    from app.models.intraday_events import IntradayEventIngestCursorModel

    source = tmp_path / "missing-sidecar.sqlite"
    engine = create_engine(f"sqlite:///{tmp_path / 'events.sqlite'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = lambda: Session(engine)
    with session_factory() as session:
        session.add(
            IntradayEventIngestCursorModel(
                processor_id="intraday-events-live-v1",
                source_path_identity=str(source.resolve()),
                last_scheduled_at=SERVICE_SLOT.isoformat(),
                last_task_name="p4-equities-fixture",
                last_capture_id="capture-old",
                last_content_sha256="0" * 64,
                seen_source_slots_json=[],
                backlog_remaining=False,
                last_status="ok",
                last_error=None,
                capabilities_summary_json={},
                updated_at=SERVICE_SLOT - timedelta(hours=2),
            )
        )
        session.commit()

    result = run_once(
        source_db_path=source,
        session_factory=session_factory,
        max_slots=1,
        now=SERVICE_SLOT + timedelta(minutes=1),
    )
    assert result.get("status") == "error", result
    with session_factory() as session:
        cursor = session.get(IntradayEventIngestCursorModel, "intraday-events-live-v1")
        assert cursor is not None
        assert cursor.last_status == "error"
        assert cursor.last_error


def test_event_repository_keeps_state_and_cursor_isolated_by_processor(tmp_path: Path) -> None:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.core.database import Base
    from app.repositories.intraday_events import IntradayEventRepository
    from app.models.intraday_events import (
        IntradayEventIngestCursorModel,
        IntradaySetupStateModel,
    )

    engine = create_engine(f"sqlite:///{tmp_path / 'events.sqlite'}")
    Base.metadata.create_all(engine)
    now = SERVICE_SLOT
    state = {"schema_version": "urus.intraday.setup-state.v1", "setups": [], "history": []}
    with Session(engine) as session:
        with session.begin():
            repository = IntradayEventRepository(session)
            for processor_id, source_path in (
                ("intraday-events-live-v1", str(tmp_path / "live.sqlite")),
                ("intraday-events-replay-v1", str(tmp_path / "replay.sqlite")),
            ):
                repository.save_slot(
                    processor_id=processor_id,
                    task_name="same-task",
                    state=state,
                    transitions=[],
                    cursor_values={
                        "source_path_identity": source_path,
                        "last_scheduled_at": SERVICE_SLOT.isoformat(),
                        "last_task_name": "same-task",
                        "last_capture_id": processor_id,
                        "last_content_sha256": "a" * 64,
                        "seen_source_slots_json": [processor_id],
                        "backlog_remaining": False,
                        "last_status": "ok",
                        "capabilities_summary_json": {},
                    },
                    now=now,
                )
        assert session.scalar(select(IntradaySetupStateModel).where(
            IntradaySetupStateModel.processor_id == "intraday-events-live-v1"
        )) is not None
        assert session.scalar(select(IntradaySetupStateModel).where(
            IntradaySetupStateModel.processor_id == "intraday-events-replay-v1"
        )) is not None
        cursors = list(session.scalars(select(IntradayEventIngestCursorModel)))

    assert {cursor.processor_id for cursor in cursors} == {
        "intraday-events-live-v1",
        "intraday-events-replay-v1",
    }
    assert {cursor.source_path_identity for cursor in cursors} == {
        str(tmp_path / "live.sqlite"),
        str(tmp_path / "replay.sqlite"),
    }


def test_events_api_keeps_historical_status_and_reports_current_status_separately(
    tmp_path: Path,
) -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core.config import Settings
    from app.core.database import Base
    from app.main import create_app
    from app.repositories.intraday_events import IntradayEventRepository

    primary = tmp_path / "events.sqlite"
    engine = create_engine(f"sqlite:///{primary}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    setup_id = "p4-history:AAA:S2:ma5_break"
    task_name = "p4-history"
    source_path = str(tmp_path / "sidecar.sqlite")
    candidate_cutoff = SERVICE_SLOT
    confirmed_cutoff = SERVICE_SLOT + timedelta(minutes=5)

    def transition(
        *,
        sequence: int,
        status: str,
        name: str,
        cutoff: datetime,
    ) -> dict[str, Any]:
        return {
            "setup_id": setup_id,
            "transition_seq": sequence,
            "task_name": task_name,
            "symbol": "AAA",
            "strategy": "S2",
            "setup_type": "ma5_break",
            "transition": name,
            "status": status,
            "timeframe": "5m",
            "rule_version": "urus.intraday.events.v1",
            "trading_date": "2026-10-16",
            "evidence_cutoff": cutoff.isoformat(),
            "evaluated_at": (cutoff + timedelta(seconds=1)).isoformat(),
            "source_capture_id": f"capture-{sequence}",
            "source_content_sha256": "a" * 64,
            "reason_code": name,
            "evidence": {"close": 100.0 - sequence},
            "confirmation_level": 99.0,
            "invalidation_level": 101.0,
        }

    with Session(engine) as session:
        repository = IntradayEventRepository(session)
        candidate_state = {
            "schema_version": "urus.intraday.setup-state.v1",
            "setups": [{
                "setup_id": setup_id,
                "task_name": task_name,
                "symbol": "AAA",
                "strategy": "S2",
                "setup_type": "ma5_break",
                "status": "candidate",
            }],
            "history": [],
        }
        confirmed_state = {
            **candidate_state,
            "setups": [{**candidate_state["setups"][0], "status": "confirmed"}],
        }
        cursor_values = {
            "source_path_identity": source_path,
            "last_scheduled_at": candidate_cutoff.isoformat(),
            "last_task_name": task_name,
            "last_capture_id": "capture-1",
            "last_content_sha256": "a" * 64,
            "seen_source_slots_json": ["slot-1"],
            "backlog_remaining": False,
            "last_status": "ok",
            "capabilities_summary_json": {},
        }
        with session.begin():
            repository.save_slot(
                processor_id="intraday-events-live-v1",
                task_name=task_name,
                state=candidate_state,
                transitions=[transition(
                    sequence=1,
                    status="candidate",
                    name="candidate_created",
                    cutoff=candidate_cutoff,
                )],
                cursor_values=cursor_values,
                now=candidate_cutoff,
            )
        cursor_values = {
            **cursor_values,
            "last_scheduled_at": confirmed_cutoff.isoformat(),
            "last_capture_id": "capture-2",
            "seen_source_slots_json": ["slot-1", "slot-2"],
        }
        with session.begin():
            repository.save_slot(
                processor_id="intraday-events-live-v1",
                task_name=task_name,
                state=confirmed_state,
                transitions=[transition(
                    sequence=2,
                    status="confirmed",
                    name="setup_confirmed",
                    cutoff=confirmed_cutoff,
                )],
                cursor_values=cursor_values,
                now=confirmed_cutoff,
            )

    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{primary}",
        sidecar_capture_database_path=str(tmp_path / "missing-sidecar.sqlite"),
        intraday_equities_enabled=False,
        intraday_events_enabled=True,
        moomoo_enabled=False,
        fred_enabled=False,
        yahoo_enabled=False,
        anomalo_workflow_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/intraday/events?after_id=0&limit=2")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload.get("events", [])) == 2, payload
    candidate_event = next(
        item for item in payload["events"] if item["status"] == "candidate"
    )
    assert candidate_event["current_status"] == "confirmed"
