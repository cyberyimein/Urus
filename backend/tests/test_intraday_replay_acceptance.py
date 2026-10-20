"""P7 red tests for offline intraday replay and outcome measurement.

These fixtures are self-contained P2 capture contracts and explicit UTC 5m
bars.  They never read the sidecar or primary database, call a provider, or
import runtime settings.  The tests intentionally check causal/time/data
boundaries instead of mirroring a replay implementation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

import pytest


BASE = datetime(2026, 10, 15, 13, 30, tzinfo=UTC)


def _minute_rows(symbol: str, *, cutoff: datetime) -> list[dict[str, Any]]:
    count = max(1, int((cutoff - BASE).total_seconds() // 60))
    rows: list[dict[str, Any]] = []
    for index in range(count):
        start = BASE + timedelta(minutes=index)
        end = start + timedelta(minutes=1)
        if symbol == "AAA":
            # At 75 minutes (bar 14) this is the same warmed ORB breakout
            # shape used by P5: three opening bars, eleven quiet bars, then a
            # close outside the frozen opening range.  At 80 minutes (bar 15)
            # the next bar holds the confirmation line.  Keeping the source
            # at one-minute granularity makes replay exercise the real P3
            # aggregation before it reaches the shared P4 evaluator.
            segment = index // 5
            segment_shapes = {
                0: (100.00, 100.00, 100.35, 99.80),
                1: (100.00, 100.10, 100.40, 99.85),
                2: (100.10, 100.05, 100.30, 99.75),
                14: (100.10, 100.95, 101.10, 100.00),
                15: (100.95, 101.20, 101.40, 100.80),
            }
            open_value, close, high_value, low_value = segment_shapes.get(
                segment,
                (100.05, 100.05 + (0.03 if segment % 2 else 0.0), 100.30, 99.80),
            )
            if index % 5:
                open_value = close
                high_value = max(open_value, close) + 0.01
                low_value = min(open_value, close) - 0.01
        else:
            close = 100.2 + index * 0.01
            open_value = close - 0.01
            high_value = close + 0.12
            low_value = close - 0.12
        rows.append(
            {
                "symbol": symbol,
                "bar_start_at": start.isoformat(),
                "bar_end_at": end.isoformat(),
                "open": open_value,
                "high": high_value,
                "low": low_value,
                "close": close,
                "volume": 100.0 + index,
                "turnover": (100.0 + index) * close,
                "provider": "moomoo_openapi",
                "quality_state": "ready",
            }
        )
    return rows


def _capture(
    *,
    task_name: str = "replay-task-a",
    slot: int = 0,
    cutoff: datetime | None = None,
    captured_at: datetime | None = None,
    revision: int = 0,
    boundary_verified: bool = True,
) -> dict[str, Any]:
    from app.decision_harness.contracts import content_sha256

    cutoff = cutoff or (BASE + timedelta(minutes=30 + slot * 5))
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
            "provider_boundary_verified": boundary_verified,
        },
        "slot_index": slot,
        "rotation": slot % 3,
        "quote_symbols": ["AAA", "SPY"],
        "minute_symbols": ["AAA", "SPY"],
        "group_by_symbol": {"AAA": "C", "SPY": "A"},
        "requested_symbols": ["AAA", "SPY"],
        "quotes": [
            {
                "symbol": "AAA",
                "last_price": 101.0 + slot,
                "provider": "moomoo_openapi",
                "update_time": "2026-10-15 10:00:03",
                "quality_status": "ready",
            },
            {
                "symbol": "SPY",
                "last_price": 501.0 + slot,
                "provider": "moomoo_openapi",
                "update_time": "2026-10-15 10:00:03",
                "quality_status": "ready",
            },
        ],
        "minute_bars": [
            *_minute_rows("AAA", cutoff=cutoff),
            *_minute_rows("SPY", cutoff=cutoff),
        ],
        "invalid_minute_bars": [],
        "minute_bar_unavailable_symbols": [],
        "unavailable_quote_symbols": [],
        "history_quota": {"available": True, "requested_symbols": ["AAA", "SPY"]},
        "quality_summary": {
            "complete": True,
            "status": "ok",
            "quote_coverage": 1.0,
            "minute_symbols_requested": 2,
            "minute_symbols_admitted": 2,
            "minute_bars_returned": len(_minute_rows("AAA", cutoff=cutoff)) * 2,
            "unavailable_minute_symbols": [],
            "quality_warnings": [],
        },
        "provider_raw": {"market_snapshot_batches": [], "history_kline_batches": []},
    }
    capture_id = f"replay-{task_name}-{slot}"
    if revision:
        # A late revision retains the same task/slot identity while changing
        # the immutable content hash.  Replay must skip it instead of emitting
        # a second event for a slot it has already consumed.
        data["quotes"][0]["last_price"] += revision * 0.01
    captured_at = captured_at or (cutoff + timedelta(seconds=10))
    return {
        "schema_version": "urus.moomoo.capture.v2",
        "capture_id": capture_id,
        "kind": "capture_intraday_equities",
        "task_name": task_name,
        "provider": "moomoo_openapi",
        "scheduled_at": cutoff.isoformat(),
        "trading_date": "2026-10-15",
        "captured_at": captured_at.isoformat(),
        "requested_symbols": ["AAA", "SPY"],
        "content_sha256": content_sha256(data),
        "data": data,
    }


def _observation(capture: Mapping[str, Any], *, observed_at: datetime, provenance: str = "original_observed") -> dict[str, Any]:
    return {
        "capture": dict(capture),
        "observed_at": observed_at.isoformat(),
        "provenance": provenance,
    }


def _replay(observations: list[Mapping[str, Any]], **kwargs: Any) -> dict[str, Any]:
    from app.intraday.replay import replay_observations

    return replay_observations(observations, **kwargs)


def _outcome_bars(
    *,
    include_gap: bool = False,
    next_session: bool = False,
    offset_minutes: int = 0,
) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    cutoff = BASE + timedelta(minutes=30 + offset_minutes)
    anchor_start = cutoff - timedelta(minutes=5)
    bars.append({
        "symbol": "AAA",
        "bar_start_at": anchor_start.isoformat(),
        "bar_end_at": cutoff.isoformat(),
        "open": 100.0,
        "high": 100.2,
        "low": 99.8,
        "close": 100.0,
        "volume": 100.0,
        "turnover": 10_000.0,
        "quality_state": "ready",
    })
    for index in range(1, 13):
        start = cutoff + timedelta(minutes=(index - 1) * 5)
        if include_gap and index == 3:
            continue
        if next_session and index == 1:
            start = datetime(2026, 10, 16, 13, 30, tzinfo=UTC)
        close = 100.0 + index
        bars.append({
            "symbol": "AAA",
            "bar_start_at": start.isoformat(),
            "bar_end_at": (start + timedelta(minutes=5)).isoformat(),
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 100.0 + index,
            "turnover": (100.0 + index) * close,
            "quality_state": "ready",
        })
    return bars


def _event(
    *,
    direction: str | None = "bullish",
    confirmation: float | None = 102.0,
    invalidation: float | None = 98.0,
    cutoff: datetime | None = None,
    trading_date: str = "2026-10-15",
) -> dict[str, Any]:
    event_cutoff = cutoff or (BASE + timedelta(minutes=30))
    return {
        "event_id": "replay-event-1",
        "setup_id": "replay:AAA:S2:ma5_reclaim",
        "symbol": "AAA",
        "strategy": "S2",
        "setup_type": "ma5_reclaim" if direction else "relative_strength",
        "direction": direction,
        "status": "confirmed",
        "trading_date": trading_date,
        "evidence_cutoff": event_cutoff.isoformat(),
        "cutoff": event_cutoff.isoformat(),
        "confirmation_level": confirmation,
        "invalidation_level": invalidation,
    }


def _session_outcome_bars(*, cutoff: datetime, future_count: int) -> list[dict[str, Any]]:
    """Create an explicitly bounded same-session window around ``cutoff``."""

    bars: list[dict[str, Any]] = []
    anchor_start = cutoff - timedelta(minutes=5)
    bars.append({
        "symbol": "AAA",
        "bar_start_at": anchor_start.isoformat(),
        "bar_end_at": cutoff.isoformat(),
        "open": 100.0,
        "high": 100.2,
        "low": 99.8,
        "close": 100.0,
        "volume": 100.0,
        "turnover": 10_000.0,
        "quality_state": "ready",
    })
    for index in range(1, future_count + 1):
        start = cutoff + timedelta(minutes=(index - 1) * 5)
        close = 100.0 + index
        bars.append({
            "symbol": "AAA",
            "bar_start_at": start.isoformat(),
            "bar_end_at": (start + timedelta(minutes=5)).isoformat(),
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 100.0 + index,
            "turnover": (100.0 + index) * close,
            "quality_state": "ready",
        })
    return bars


def test_replay_orders_observations_and_keeps_task_state_separate() -> None:
    observations = [
        _observation(_capture(task_name="task-b", slot=1), observed_at=BASE + timedelta(minutes=36), provenance="reconstructed"),
        _observation(_capture(task_name="task-a", slot=0), observed_at=BASE + timedelta(minutes=31)),
    ]
    result = _replay(observations)
    assert str(result["schema_version"]).startswith("urus.intraday.replay.")
    assert result["observations_processed"] == 2
    assert result["skipped"] == []
    assert set(result["states_by_task"]) == {"task-a", "task-b"}
    assert result["counts_by_provenance"]["original_observed"] == 1
    assert result["counts_by_provenance"]["reconstructed"] == 1


def test_replay_duplicate_and_late_slot_cannot_replace_monotonic_task_state() -> None:
    first = _capture(slot=0)
    second = _capture(slot=1)
    late = _capture(slot=0)
    result = _replay([
        _observation(first, observed_at=BASE + timedelta(minutes=31)),
        _observation(second, observed_at=BASE + timedelta(minutes=36)),
        _observation(first, observed_at=BASE + timedelta(minutes=37)),
        _observation(late, observed_at=BASE + timedelta(minutes=38)),
    ])
    assert result["observations_processed"] == 2
    assert len(result["skipped"]) == 2
    assert all(item.get("reason_code") for item in result["skipped"])


def _strategy_events(
    result: Mapping[str, Any],
    strategy: str = "S4",
    symbol: str | None = None,
) -> list[Mapping[str, Any]]:
    events = result.get("events")
    assert isinstance(events, list), result
    return [
        item
        for item in events
        if isinstance(item, Mapping)
        and str(item.get("strategy") or "").upper() == strategy
        and (symbol is None or str(item.get("symbol") or "").upper() == symbol.upper())
    ]


def _transition_sequence(item: Mapping[str, Any]) -> int | None:
    for key in ("transition_seq", "sequence", "seq"):
        value = item.get(key)
        if isinstance(value, int):
            return value
    return None


def test_replay_uses_real_p3_projection_and_shared_s4_candidate_then_confirmation() -> None:
    # 75 minutes gives the warmed 14-bar prefix plus an ORB breakout.  The
    # following 80-minute capture is a separate observed slot whose next bar
    # confirms the frozen S4 candidate.
    first = _capture(task_name="task-s4", slot=9)
    second = _capture(task_name="task-s4", slot=10)
    result = _replay([
        _observation(first, observed_at=BASE + timedelta(minutes=76)),
        _observation(second, observed_at=BASE + timedelta(minutes=81)),
    ])
    events = _strategy_events(result, symbol="AAA")
    assert events, result
    assert any(item.get("status") == "candidate" for item in events), result
    assert any(item.get("status") == "confirmed" for item in events), result
    setup_ids = {str(item.get("setup_id")) for item in events if item.get("setup_id")}
    assert len(setup_ids) == 1, events
    sequences = [seq for item in events if (seq := _transition_sequence(item)) is not None]
    if sequences:
        assert sequences == sorted(sequences)
        assert len(set(sequences)) == len(sequences)
    assert result["states_by_task"].get("task-s4")


def test_replay_rejects_mixed_provenance_per_task_without_cross_labeling_state() -> None:
    """A reconstructed state must never confirm an original observation.

    Provenance is a run-level evidence boundary for each task.  Separate
    replays may use either source, and different tasks may use different
    sources, but one task cannot change source halfway through its cursor.
    """

    reconstructed_slot = _capture(task_name="task-mixed", slot=9)
    original_slot = _capture(task_name="task-mixed", slot=10)
    mixed = _replay([
        _observation(
            reconstructed_slot,
            observed_at=BASE + timedelta(minutes=76),
            provenance="reconstructed",
        ),
        _observation(
            original_slot,
            observed_at=BASE + timedelta(minutes=81),
            provenance="original_observed",
        ),
    ])
    assert mixed["observations_processed"] == 1
    assert len(mixed["skipped"]) == 1
    assert mixed["skipped"][0]["reason_code"] == "mixed_provenance_for_task"
    assert mixed["counts_by_provenance"] == {"original_observed": 0, "reconstructed": 1}
    mixed_events = _strategy_events(mixed, symbol="AAA")
    assert mixed_events
    assert all(item.get("provenance") == "reconstructed" for item in mixed_events)
    assert not any(item.get("provenance") == "original_observed" for item in mixed_events)

    reconstructed_only = _replay([
        _observation(
            reconstructed_slot,
            observed_at=BASE + timedelta(minutes=76),
            provenance="reconstructed",
        ),
    ])
    original_only = _replay([
        _observation(
            original_slot,
            observed_at=BASE + timedelta(minutes=81),
            provenance="original_observed",
        ),
    ])
    assert reconstructed_only["observations_processed"] == 1
    assert original_only["observations_processed"] == 1
    assert reconstructed_only["counts_by_provenance"]["reconstructed"] == 1
    assert original_only["counts_by_provenance"]["original_observed"] == 1

    different_tasks = _replay([
        _observation(
            _capture(task_name="task-reconstructed", slot=9),
            observed_at=BASE + timedelta(minutes=76),
            provenance="reconstructed",
        ),
        _observation(
            _capture(task_name="task-original", slot=9),
            observed_at=BASE + timedelta(minutes=76),
            provenance="original_observed",
        ),
    ])
    assert different_tasks["observations_processed"] == 2
    assert different_tasks["counts_by_provenance"] == {"original_observed": 1, "reconstructed": 1}


def test_replay_event_prefix_is_causal_and_future_capture_does_not_repaint_past() -> None:
    first = _capture(task_name="task-prefix", slot=9)
    second = _capture(task_name="task-prefix", slot=10)
    future = _capture(task_name="task-prefix", slot=11)
    prefix = _replay([
        _observation(first, observed_at=BASE + timedelta(minutes=76)),
        _observation(second, observed_at=BASE + timedelta(minutes=81)),
    ])
    extended = _replay([
        _observation(first, observed_at=BASE + timedelta(minutes=76)),
        _observation(second, observed_at=BASE + timedelta(minutes=81)),
        _observation(future, observed_at=BASE + timedelta(minutes=86)),
    ])
    prefix_events = _strategy_events(prefix, symbol="AAA")
    extended_events = _strategy_events(extended, symbol="AAA")
    assert prefix_events
    assert extended_events[: len(prefix_events)] == prefix_events


def test_replay_late_same_task_slot_revision_does_not_emit_duplicate_event() -> None:
    first = _capture(task_name="task-revision", slot=9)
    revision = _capture(task_name="task-revision", slot=9, revision=1)
    result = _replay([
        _observation(first, observed_at=BASE + timedelta(minutes=76)),
        _observation(revision, observed_at=BASE + timedelta(minutes=90)),
    ])
    assert result["observations_processed"] == 1
    assert len(result["skipped"]) == 1
    assert _strategy_events(result, symbol="AAA")
    reason = " ".join(str(result["skipped"][0].get(field) or "") for field in ("reason_code", "message")).lower()
    assert "duplicate" in reason or "revision" in reason or "late" in reason or "slot" in reason


def test_replay_real_s4_events_and_state_remain_isolated_between_tasks() -> None:
    task_a = _capture(task_name="task-a", slot=9)
    task_b = _capture(task_name="task-b", slot=9)
    result = _replay([
        _observation(task_b, observed_at=BASE + timedelta(minutes=76)),
        _observation(task_a, observed_at=BASE + timedelta(minutes=76)),
    ])
    assert set(result["states_by_task"]) == {"task-a", "task-b"}
    events = _strategy_events(result)
    assert events
    assert {str(item.get("task_name")) for item in events} >= {"task-a", "task-b"}


@pytest.mark.parametrize("bad_observation", [
    "future_slot",
    "known_after_observation",
    "invalid_hash",
    "naive_observed_at",
])
def test_replay_rejects_future_known_after_invalid_or_naive_observations(bad_observation: str) -> None:
    if bad_observation == "future_slot":
        # The source capture has been written before the observation, but its
        # scheduled slot is still in the future.  This must be distinguished
        # from a source that was only captured after the observation.
        observed_at = BASE + timedelta(minutes=31)
        capture = _capture(
            slot=1,
            captured_at=observed_at - timedelta(seconds=1),
        )
    elif bad_observation == "known_after_observation":
        observed_at = BASE + timedelta(minutes=31)
        capture = _capture(
            slot=0,
            captured_at=observed_at + timedelta(seconds=1),
        )
    else:
        capture = _capture(slot=1)
    if bad_observation == "invalid_hash":
        capture["content_sha256"] = "0" * 64
        observed_at = BASE + timedelta(minutes=36)
    elif bad_observation == "naive_observed_at":
        item = {"capture": capture, "observed_at": "2026-10-15T14:01:00", "provenance": "original_observed"}
        result = _replay([item])
        assert result["observations_processed"] == 0
        assert result["skipped"]
        return
    result = _replay([_observation(capture, observed_at=observed_at)])
    assert result["observations_processed"] == 0
    assert result["skipped"]
    reason = " ".join(
        str(result["skipped"][0].get(field) or "")
        for field in ("reason_code", "reason", "message")
    ).lower()
    assert reason
    if bad_observation == "future_slot":
        assert "future" in reason or "scheduled" in reason
    elif bad_observation == "known_after_observation":
        assert "known" in reason or "observ" in reason or "captur" in reason


def test_replay_rejects_unknown_provenance_and_groups_reconstructed_counts() -> None:
    capture = _capture(task_name="task-reconstructed")
    with pytest.raises((TypeError, ValueError)):
        _replay([_observation(capture, observed_at=BASE + timedelta(minutes=31), provenance="guessed")])

    result = _replay([
        _observation(capture, observed_at=BASE + timedelta(minutes=31), provenance="reconstructed"),
    ])
    assert result["counts_by_provenance"]["reconstructed"] == 1
    assert result["counts_by_provenance"]["original_observed"] == 0


def test_replay_preserves_zero_event_evaluation_reason_for_unverified_boundary() -> None:
    capture = _capture(task_name="task-unverified-boundary", slot=9, boundary_verified=False)
    result = _replay([
        _observation(capture, observed_at=BASE + timedelta(minutes=76)),
    ])

    assert result["observations_processed"] == 1
    assert result["events"] == []
    evaluations = result.get("evaluations")
    assert isinstance(evaluations, list)
    assert len(evaluations) == 1
    evaluation = evaluations[0]
    for field in (
        "input_index",
        "task_name",
        "capture_id",
        "content_sha256",
        "scheduled_at",
        "observed_at",
        "provenance",
        "projection_status",
        "event_count",
        "capability_summary",
    ):
        assert field in evaluation
    assert evaluation["input_index"] == 0
    assert evaluation["task_name"] == capture["task_name"]
    assert evaluation["capture_id"] == capture["capture_id"]
    assert evaluation["content_sha256"] == capture["content_sha256"]
    assert evaluation["provenance"] == "original_observed"
    assert evaluation["event_count"] == 0
    capability_summary = evaluation["capability_summary"]
    assert capability_summary["unavailable_count"] > 0
    assert capability_summary["reason_counts"]["minute_timestamp_boundary_unverified"] > 0


def _rule_snapshot_hash(report: Mapping[str, Any]) -> str:
    snapshot = report.get("rule_spec_snapshot")
    if isinstance(snapshot, Mapping):
        digest = snapshot.get("sha256") or snapshot.get("hash")
    else:
        digest = report.get("rule_spec_sha256") or report.get("rule_spec_hash")
    assert isinstance(digest, str) and digest
    return digest


def test_replay_empty_report_has_stable_rule_identity_and_spec_changes_hash() -> None:
    first = _replay([])
    second = _replay([])
    changed = _replay([], spec={"cooldown_minutes": 30})

    for report in (first, second, changed):
        assert report.get("rule_spec_snapshot") is not None
        assert isinstance(report.get("projection_parameters"), Mapping)
        assert report.get("evaluations") == []
    assert _rule_snapshot_hash(first) == _rule_snapshot_hash(second)
    assert _rule_snapshot_hash(first) != _rule_snapshot_hash(changed)


@pytest.mark.parametrize("horizon_minutes", [15, 30, 60])
def test_measure_outcome_uses_complete_cutoff_anchor_and_fixed_horizons(horizon_minutes: int) -> None:
    from app.intraday.outcomes import measure_outcome

    result = measure_outcome(_event(), _outcome_bars(), horizon_minutes=horizon_minutes)
    assert result["available"] is True
    assert result["anchor_close"] == pytest.approx(100.0)
    assert result["forward_return_pct"] == pytest.approx(float(horizon_minutes // 5))
    assert result["max_upside_pct"] >= 0
    assert result["max_drawdown_pct"] <= 0
    assert result["first_touch_order"] == "confirmation_first"


def test_measure_outcome_rejects_missing_or_invalid_same_session_five_minute_window() -> None:
    from app.intraday.outcomes import measure_outcome

    for bars in [_outcome_bars(include_gap=True), _outcome_bars(next_session=True)]:
        result = measure_outcome(_event(), bars, horizon_minutes=30)
        assert result["available"] is False
        assert result["reason_code"]

    invalid = _outcome_bars()
    invalid[2]["high"] = math.nan
    result = measure_outcome(_event(), invalid, horizon_minutes=30)
    assert result["available"] is False
    assert result["reason_code"]


def test_measure_outcome_rejects_five_minute_bars_off_session_open_grid() -> None:
    from app.intraday.outcomes import measure_outcome

    # The bars remain five minutes long and internally contiguous, but the
    # complete window starts two minutes off the XNYS session-open grid.  A
    # valid duration alone must not make this a trustworthy outcome anchor.
    shifted_cutoff = BASE + timedelta(minutes=32)
    result = measure_outcome(
        _event(cutoff=shifted_cutoff),
        _outcome_bars(offset_minutes=2),
        horizon_minutes=15,
    )
    assert result["available"] is False
    assert result["reason_code"]


def test_measure_outcome_does_not_cross_regular_or_half_day_close() -> None:
    from app.intraday.outcomes import measure_outcome

    close_boundaries = [
        # 2026-10-15 regular close: 16:00 ET / 20:00 UTC.
        ("2026-10-15", datetime(2026, 10, 15, 19, 55, tzinfo=UTC)),
        # 2026-11-27 Thanksgiving half-day close: 13:00 ET / 18:00 UTC.
        ("2026-11-27", datetime(2026, 11, 27, 17, 55, tzinfo=UTC)),
    ]
    for trading_date, cutoff in close_boundaries:
        result = measure_outcome(
            _event(cutoff=cutoff, trading_date=trading_date),
            _session_outcome_bars(cutoff=cutoff, future_count=1),
            horizon_minutes=15,
        )
        assert result["available"] is False
        assert result["reason_code"] == "outcome_crosses_session_close"

    # A sufficiently early half-day cutoff has a complete same-session window
    # and must remain measurable; the implementation must not reject the
    # whole shortened session merely because its close is earlier than usual.
    valid_cutoff = datetime(2026, 11, 27, 17, 0, tzinfo=UTC)
    valid = measure_outcome(
        _event(cutoff=valid_cutoff, trading_date="2026-11-27"),
        _session_outcome_bars(cutoff=valid_cutoff, future_count=3),
        horizon_minutes=15,
    )
    assert valid["available"] is True


def test_measure_outcome_marks_same_bar_confirmation_and_invalidation_as_unknown() -> None:
    from app.intraday.outcomes import measure_outcome

    result = measure_outcome(
        _event(confirmation=101.0, invalidation=100.0),
        _outcome_bars(),
        horizon_minutes=15,
    )
    assert result["available"] is True
    assert result["first_touch_order"] == "same_bar_unknown"


def test_measure_outcome_does_not_infer_trade_direction_from_relative_strength() -> None:
    from app.intraday.outcomes import measure_outcome

    result = measure_outcome(
        _event(direction=None, confirmation=102.0, invalidation=98.0),
        _outcome_bars(),
        horizon_minutes=15,
    )
    assert result["available"] is True
    assert result["first_touch_order"] == "not_applicable"


def test_measure_outcome_rejects_horizons_outside_15_30_60() -> None:
    from app.intraday.outcomes import measure_outcome

    try:
        result = measure_outcome(_event(), _outcome_bars(), horizon_minutes=10)
    except ValueError:
        return
    assert result["available"] is False
    assert result["reason_code"] == "invalid_horizon"


def test_replay_cli_is_offline_and_refuses_to_overwrite_reports(tmp_path: Path) -> None:
    capture = _capture()
    input_path = tmp_path / "observations.json"
    output_path = tmp_path / "report.json"
    input_path.write_text(
        json.dumps([_observation(capture, observed_at=BASE + timedelta(minutes=31))]),
        encoding="utf-8",
    )
    env = dict(__import__("os").environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    command = [sys.executable, "-m", "app.intraday.replay", "--input", str(input_path), "--output", str(output_path)]
    first = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["schema_version"]
    assert "observations_processed" in report
    output_path.write_text("keep-me", encoding="utf-8")
    second = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True)
    assert second.returncode != 0
    assert output_path.read_text(encoding="utf-8") == "keep-me"
