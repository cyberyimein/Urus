from __future__ import annotations

from app.decision_harness.daily_comparison import (
    build_instrument_temporal_context,
    previous_trading_date_from_chart,
)


def _chart(dates: list[str], closes: list[float], ma20: list[float]) -> dict[str, object]:
    bars = [
        {
            "date": trading_date,
            "open": close - 1,
            "high": close + 2,
            "low": close - 2,
            "close": close,
            "volume": 1000 + index * 100,
        }
        for index, (trading_date, close) in enumerate(zip(dates, closes))
    ]
    return {
        "instruments": {
            "INTC": {
                "symbol": "INTC",
                "price": {"bars": bars},
                "series": [
                    {
                        "series_id": "ma20",
                        "points": [
                            {"time": trading_date, "value": value}
                            for trading_date, value in zip(dates, ma20)
                        ],
                    }
                ],
                "indicator_snapshot_id": f"indicator-{dates[-1]}",
                "quality": {"status": "ok", "warnings": []},
            }
        }
    }


def _decision(
    *,
    decision_id: str,
    dataset_id: str,
    action: str,
    stage: str,
    score: float,
) -> dict[str, object]:
    return {
        "decision_id": decision_id,
        "dataset_id": dataset_id,
        "scope": {"symbol": "INTC"},
        "strategy": {"name": "trend_momentum_v1", "version": "1.0.0"},
        "stance": "bullish" if action == "prioritize" else "neutral",
        "action": action,
        "score": score,
        "confidence": 0.7,
        "setup_progress": {
            "stage": stage,
            "confirmation_distance_atr": 0.8,
            "invalidation_distance_atr": 1.2,
            "changed_from_previous_stage": stage == "confirmed",
        },
    }


def test_previous_trading_date_uses_the_bar_before_the_current_target() -> None:
    chart = _chart(
        ["2026-08-21", "2026-08-25"],
        [20.0, 21.0],
        [19.0, 19.5],
    )

    assert previous_trading_date_from_chart(chart, "INTC", "2026-08-25") == "2026-08-21"


def test_temporal_context_does_not_infer_t_minus_one_when_current_date_is_not_in_chart() -> None:
    chart = _chart(
        ["2026-08-21", "2026-08-25"],
        [20.0, 21.0],
        [19.0, 19.5],
    )
    previous_dataset = {
        "dataset_id": "dataset-previous",
        "trading_date": "2026-08-25",
        "quality": {"status": "ok"},
    }

    assert previous_trading_date_from_chart(chart, "INTC", "2026-08-26") is None

    context = build_instrument_temporal_context(
        current_dataset={
            "dataset_id": "dataset-current",
            "trading_date": "2026-08-26",
            "quality": {"status": "ok"},
        },
        current_chart=chart,
        current_strategy_decisions=[],
        current_synthesis={},
        previous_dataset=previous_dataset,
        previous_chart=_chart(["2026-08-25"], [21.0], [19.5]),
        previous_strategy_decisions=[],
        previous_synthesis={},
        symbol="INTC",
    )

    assert context["status"] == "partial"
    assert context["previous"]["trading_date"] == "2026-08-25"
    assert any("核验上一交易日" in warning for warning in context["warnings"])


def test_temporal_context_contains_prior_snapshot_deltas_and_strategy_transition() -> None:
    current_chart = _chart(
        ["2026-08-21", "2026-08-25"],
        [20.0, 21.0],
        [19.0, 19.5],
    )
    previous_chart = _chart(["2026-08-21"], [20.0], [19.0])
    previous_decision = _decision(
        decision_id="previous-decision",
        dataset_id="dataset-previous",
        action="watch",
        stage="near_confirmation",
        score=20,
    )
    current_decision = _decision(
        decision_id="current-decision",
        dataset_id="dataset-current",
        action="prioritize",
        stage="confirmed",
        score=45,
    )

    context = build_instrument_temporal_context(
        current_dataset={
            "dataset_id": "dataset-current",
            "schema_version": "urus.daily_decision_dataset.v1",
            "trading_date": "2026-08-25",
            "content_sha256": "c" * 64,
            "quality": {"status": "ok"},
        },
        current_chart=current_chart,
        current_strategy_decisions=[current_decision],
        current_synthesis={"consensus_state": "aligned", "suggested_action": "prioritize"},
        previous_dataset={
            "dataset_id": "dataset-previous",
            "schema_version": "urus.daily_decision_dataset.v1",
            "trading_date": "2026-08-21",
            "content_sha256": "p" * 64,
            "quality": {"status": "ok"},
        },
        previous_chart=previous_chart,
        previous_strategy_decisions=[previous_decision],
        previous_synthesis={"consensus_state": "mixed", "suggested_action": "watch"},
        symbol="INTC",
    )

    assert context["status"] == "ok"
    assert context["data_boundary"]["mode"] == "daily_close_only"
    assert context["data_boundary"]["premarket_available"] is False
    assert context["previous"]["trading_date"] == "2026-08-21"
    assert context["previous"]["dataset"]["dataset_id"] == "dataset-previous"
    assert context["changes"]["bar"]["close"] == {
        "previous": 20.0,
        "current": 21.0,
        "absolute": 1.0,
        "percent": 5.0,
    }
    assert context["changes"]["series"]["ma20"]["absolute"] == 0.5
    transition = context["changes"]["strategy_transitions"][0]
    assert transition["action_changed"] is True
    assert transition["stage_changed"] is True
    assert context["changes"]["synthesis"]["changed"] is True
    assert {
        ref["kind"] for ref in context["evidence_refs"]
    } >= {"daily_dataset", "decision_chart", "strategy_decision", "deterministic_synthesis"}


def test_temporal_context_marks_history_fallback_as_partial() -> None:
    context = build_instrument_temporal_context(
        current_dataset={
            "dataset_id": "dataset-current",
            "trading_date": "2026-08-25",
            "quality": {"status": "ok"},
        },
        current_chart=_chart(
            ["2026-08-21", "2026-08-25"],
            [20.0, 21.0],
            [19.0, 19.5],
        ),
        current_strategy_decisions=[],
        current_synthesis={},
        previous_dataset=None,
        previous_chart=None,
        previous_strategy_decisions=[],
        previous_synthesis={},
        symbol="INTC",
    )

    assert context["status"] == "partial"
    assert context["previous"]["source"] == "current_dataset_history"
    assert context["previous"]["dataset"] is None
    assert context["previous"]["trading_date"] == "2026-08-21"
    assert context["warnings"]


def test_temporal_context_downgrades_unverified_nearest_dataset() -> None:
    context = build_instrument_temporal_context(
        current_dataset={
            "dataset_id": "dataset-current",
            "trading_date": "2026-08-25",
            "quality": {"status": "ok"},
        },
        current_chart=None,
        current_strategy_decisions=[],
        current_synthesis={},
        previous_dataset={
            "dataset_id": "dataset-previous-available",
            "trading_date": "2026-08-21",
            "quality": {"status": "ok"},
        },
        previous_chart=_chart(["2026-08-21"], [20.0], [19.0]),
        previous_strategy_decisions=[],
        previous_synthesis={},
        symbol="INTC",
    )

    assert context["status"] == "partial"
    assert any("核验上一交易日" in warning for warning in context["warnings"])
