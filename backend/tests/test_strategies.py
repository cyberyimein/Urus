from __future__ import annotations

from datetime import date, timedelta
import json

from app.decision_harness.strategies import (
    QualityLeftSideReversalStrategy,
    RelativeStrengthRotationStrategy,
    StrategyContext,
    StrategyAdapter,
    StrategyRegistry,
    deterministic_synthesis,
    project_symbol_setups,
)


class ExplodingStrategy(StrategyAdapter):
    name = "exploding_fixture_v1"
    minimum_bars = 1
    rule_set = "fixture strategy that raises"

    def _evaluate(self, context):
        raise RuntimeError("fixture failure")


class SkippedStrategy(StrategyAdapter):
    name = "skipped_fixture_v1"
    minimum_bars = 1
    rule_set = "fixture strategy that is not applicable"

    def _evaluate(self, context):
        return self.not_applicable(context, "fixture_skip", "fixture skip")


def test_registry_is_extensible_and_isolates_one_strategy_failure() -> None:
    dataset = {
        "dataset_id": "dataset-1",
        "scope": {
            "scope_type": "instrument",
            "scope_id": "INTC",
            "symbols": ["INTC"],
            "benchmark_symbols": [],
        },
        "quality": {"symbols": {"INTC": {"status": "ok"}}},
    }
    chart = {
        "instruments": {
            "INTC": {
                "price": {
                    "bars": [
                        {
                            "date": "2026-08-21",
                            "open": 20.0,
                            "high": 21.0,
                            "low": 19.0,
                            "close": 20.5,
                            "volume": 1000.0,
                        }
                    ]
                }
            }
        }
    }

    decisions, synthesis = StrategyRegistry(
        adapters=(ExplodingStrategy(), SkippedStrategy())
    ).evaluate(dataset, chart)

    assert [item["strategy"]["name"] for item in decisions] == [
        "exploding_fixture_v1",
        "skipped_fixture_v1",
    ]
    assert [item["status"] for item in decisions] == ["error", "not_applicable"]
    assert synthesis["consensus_state"] == "insufficient_data"


def _left_side_bars(symbol: str, *, benchmark: bool = False) -> list[dict[str, object]]:
    start = date(2025, 10, 1)
    rows: list[dict[str, object]] = []
    for index in range(220):
        if benchmark:
            close = 300 + index * 0.12
            if index >= 217:
                close -= (index - 216) * 3.0
        elif index < 160:
            close = 90 + index * 0.03
        elif index == 160:
            close = 101.0
        elif index < 205:
            close = 101 + (index - 160) * 0.24
        else:
            close = 111.56 - (index - 204) * 0.82
            if index >= 217:
                close += (index - 216) * 0.55
        rows.append(
            {
                "date": (start + timedelta(days=index)).isoformat(),
                "open": close + (0.25 if index < 217 else -0.15),
                "high": close + 0.7,
                "low": close - 0.7,
                "close": close,
                "volume": 2_000_000.0,
            }
        )
    return rows


def test_quality_left_side_reversal_is_composite_cash_equity_strategy() -> None:
    bars = _left_side_bars("INTC")
    benchmark_bars = _left_side_bars("QQQ", benchmark=True)
    dataset = {
        "dataset_id": "dataset-left-side",
        "scope": {
            "scope_type": "instrument",
            "scope_id": "INTC",
            "symbols": ["INTC"],
            "benchmark_symbols": ["QQQ"],
        },
        "quality": {
            "symbols": {
                "INTC": {"status": "ok", "input_bar_hash": "instrument-hash"},
                "QQQ": {"status": "ok", "input_bar_hash": "benchmark-hash"},
            }
        },
    }
    chart = {
        "instruments": {
            "INTC": {"price": {"bars": bars}},
            "QQQ": {"price": {"bars": benchmark_bars}},
        }
    }

    decisions, _ = StrategyRegistry(
        adapters=(QualityLeftSideReversalStrategy(),)
    ).evaluate(dataset, chart)

    decision = decisions[0]
    reason_codes = {item["code"] for item in decision["reasons"]}
    anchor_kinds = {item["kind"] for item in decision["visual_anchors"]}
    serialized = json.dumps(decision, ensure_ascii=False).lower()
    assert decision["status"] == "ok"
    assert decision["setup_progress"]["stage"] in {"watching", "armed", "confirmed"}
    assert "research_scope_gate" in reason_codes
    assert "investability_passed" in reason_codes
    assert "rsi12_oversold" in reason_codes or "rsi12_recovery" in reason_codes
    assert "price_zone" in anchor_kinds
    assert "series_highlight" in anchor_kinds
    assert "sell put" not in serialized
    assert "strike" not in serialized


def test_quality_left_side_reversal_requires_a_benchmark() -> None:
    bars = _left_side_bars("INTC")
    dataset = {
        "dataset_id": "dataset-no-benchmark",
        "scope": {
            "scope_type": "instrument",
            "scope_id": "INTC",
            "symbols": ["INTC"],
            "benchmark_symbols": [],
        },
        "quality": {"symbols": {"INTC": {"status": "ok"}}},
    }
    chart = {"instruments": {"INTC": {"price": {"bars": bars}}}}

    decisions, _ = StrategyRegistry(
        adapters=(QualityLeftSideReversalStrategy(),)
    ).evaluate(dataset, chart)

    assert decisions[0]["status"] == "not_applicable"
    assert decisions[0]["reasons"][0]["code"] == "benchmark_missing"


def test_strategy_quality_gate_blocks_partial_daily_evidence() -> None:
    dataset = {
        "dataset_id": "dataset-partial-quality",
        "scope": {
            "scope_type": "instrument",
            "scope_id": "INTC",
            "symbols": ["INTC"],
            "benchmark_symbols": [],
        },
        "quality": {"symbols": {"INTC": {"status": "partial"}}},
    }
    chart = {
        "instruments": {
            "INTC": {
                "price": {
                    "bars": [
                        {
                            "date": "2026-08-21",
                            "open": 20.0,
                            "high": 21.0,
                            "low": 19.0,
                            "close": 20.5,
                            "volume": 1000.0,
                        }
                    ]
                }
            }
        }
    }

    decisions, synthesis = StrategyRegistry(
        adapters=(SkippedStrategy(),)
    ).evaluate(dataset, chart)

    assert decisions[0]["status"] == "not_applicable"
    assert decisions[0]["reasons"][0]["code"] == "data_quality"
    assert synthesis["consensus_state"] == "insufficient_data"


def _synthesis_decision(
    *, decision_id: str, symbol: str, stance: str, score: float, strategy_name: str
) -> dict[str, object]:
    return {
        "decision_id": decision_id,
        "scope": {"symbol": symbol},
        "strategy": {
            "name": strategy_name,
            "version": "1.0",
            "implementation_sha256": f"hash-{strategy_name}",
        },
        "status": "ok",
        "stance": stance,
        "score": score,
        "quality": {"status": "ok"},
    }


def test_deterministic_synthesis_exposes_independent_symbol_summaries() -> None:
    dataset = {
        "dataset_id": "dataset-per-symbol",
        "scope": {
            "scope_type": "observation_run",
            "scope_id": "run-1",
            "symbols": ["AAA", "BBB"],
        },
    }
    decisions = [
        _synthesis_decision(
            decision_id="aaa-bullish",
            symbol="AAA",
            stance="bullish",
            score=70,
            strategy_name="trend",
        ),
        _synthesis_decision(
            decision_id="aaa-bearish",
            symbol="AAA",
            stance="bearish",
            score=-65,
            strategy_name="reversion",
        ),
        _synthesis_decision(
            decision_id="bbb-bearish-1",
            symbol="BBB",
            stance="bearish",
            score=-70,
            strategy_name="trend",
        ),
        _synthesis_decision(
            decision_id="bbb-bearish-2",
            symbol="BBB",
            stance="bearish",
            score=-55,
            strategy_name="breakout",
        ),
    ]

    synthesis = deterministic_synthesis(dataset, decisions, "strategy-set")

    assert synthesis["consensus_state"] == "conflicted"
    assert set(synthesis["symbol_summaries"]) == {"AAA", "BBB"}
    assert synthesis["symbol_summaries"]["AAA"]["consensus_state"] == "conflicted"
    assert synthesis["symbol_summaries"]["AAA"]["suggested_action"] == "watch"
    assert synthesis["symbol_summaries"]["BBB"]["consensus_state"] == "aligned"
    assert synthesis["symbol_summaries"]["BBB"]["suggested_action"] == "avoid"


def test_strategy_registry_exposes_machine_readable_setup_plan_v2() -> None:
    bars = _left_side_bars("INTC")
    benchmark_bars = _left_side_bars("QQQ", benchmark=True)
    dataset = {
        "dataset_id": "dataset-setup-plan",
        "scope": {
            "scope_type": "instrument",
            "scope_id": "INTC",
            "symbols": ["INTC"],
            "benchmark_symbols": ["QQQ"],
        },
        "quality": {
            "symbols": {
                "INTC": {"status": "ok", "input_bar_hash": "instrument-hash"},
                "QQQ": {"status": "ok", "input_bar_hash": "benchmark-hash"},
            }
        },
    }
    chart = {
        "instruments": {
            "INTC": {"price": {"bars": bars}},
            "QQQ": {"price": {"bars": benchmark_bars}},
        }
    }

    decisions, _ = StrategyRegistry(
        adapters=(QualityLeftSideReversalStrategy(),)
    ).evaluate(dataset, chart)

    decision = decisions[0]
    plan = decision["setup_plan"]
    assert decision["schema_version"] == "urus.strategy_decision.v2"
    assert plan["direction"] == decision["stance"]
    assert plan["stage"] == decision["setup_progress"]["stage"]
    assert set(plan) >= {
        "direction",
        "stage",
        "confirmation_price",
        "confirmation_operator",
        "observation_price",
        "remaining_price",
        "remaining_percent",
        "remaining_atr",
        "invalidation_price",
        "invalidation_distance_price",
        "invalidation_distance_percent",
        "invalidation_distance_atr",
    }


def _planned_decision(
    *,
    decision_id: str,
    symbol: str,
    strategy_name: str,
    stage: str,
    distance_atr: float,
    score: float,
) -> dict[str, object]:
    return {
        "decision_id": decision_id,
        "scope": {"symbol": symbol},
        "strategy": {"name": strategy_name, "version": "2.0", "implementation_sha256": strategy_name},
        "status": "ok",
        "stance": "bullish",
        "action": "prioritize",
        "score": score,
        "setup_progress": {
            "stage": stage,
            "confirmation_distance_atr": distance_atr,
            "invalidation_distance_atr": 1.5,
        },
        "setup_plan": {
            "direction": "bullish",
            "stage": stage,
            "confirmation_price": 101.0,
            "confirmation_operator": ">=",
            "observation_price": 100.0,
            "remaining_price": 1.0,
            "remaining_percent": 1.0,
            "remaining_atr": distance_atr,
            "invalidation_price": 95.0,
            "invalidation_distance_price": 5.0,
            "invalidation_distance_percent": 5.0,
            "invalidation_distance_atr": 1.5,
        },
    }


def test_project_symbol_setups_deduplicates_and_selects_primary_deterministically() -> None:
    decisions = [
        _planned_decision(
            decision_id="forming-setup",
            symbol="AAA",
            strategy_name="zulu",
            stage="forming",
            distance_atr=0.1,
            score=95,
        ),
        _planned_decision(
            decision_id="near-setup",
            symbol="AAA",
            strategy_name="charlie",
            stage="near_confirmation",
            distance_atr=0.1,
            score=90,
        ),
        _planned_decision(
            decision_id="confirmed-far",
            symbol="AAA",
            strategy_name="bravo",
            stage="confirmed",
            distance_atr=1.2,
            score=99,
        ),
        _planned_decision(
            decision_id="confirmed-near",
            symbol="AAA",
            strategy_name="alpha",
            stage="confirmed",
            distance_atr=0.4,
            score=50,
        ),
        _planned_decision(
            decision_id="confirmed-near",
            symbol="AAA",
            strategy_name="alpha",
            stage="confirmed",
            distance_atr=0.4,
            score=50,
        ),
    ]

    projected = project_symbol_setups(decisions)

    assert set(projected) == {"AAA"}
    assert projected["AAA"]["candidate_count"] == 4
    primary = projected["AAA"]["primary_setup"]
    assert primary["decision_id"] == "confirmed-near"
    assert primary["strategy_name"] == "alpha"
    assert primary["stage"] == "confirmed"
    assert primary["confirmation_distance_atr"] == 0.4
    assert primary["score"] == 50
    assert primary["setup_plan"]["confirmation_price"] == 101.0
    assert primary["setup_plan"]["confirmation_operator"] == ">="
    assert primary["setup_plan"]["observation_price"] == 100.0
    assert primary["setup_plan"]["remaining_price"] == 1.0
    assert primary["setup_plan"]["remaining_percent"] == 1.0
    assert primary["setup_plan"]["remaining_atr"] == 0.4
    assert primary["setup_plan"]["invalidation_price"] == 95.0
    assert primary["setup_plan"]["invalidation_distance_price"] == 5.0
    assert primary["setup_plan"]["invalidation_distance_percent"] == 5.0
    assert primary["setup_plan"]["invalidation_distance_atr"] == 1.5


def _relative_rotation_context(*, five_day: float, twenty_day: float, sixty_day: float) -> StrategyContext:
    bars = [
        {
            "date": f"2026-08-{index:02d}",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1_000_000.0,
        }
        for index in range(1, 21)
    ]
    return StrategyContext(
        dataset={
            "dataset_id": "dataset-relative-rotation",
            "scope": {
                "scope_type": "instrument",
                "scope_id": "AAA",
                "symbols": ["AAA"],
                "benchmark_symbols": ["QQQ"],
            },
        },
        chart={},
        symbol="AAA",
        bars=bars,
        quality={"status": "ok"},
        indicators={},
        benchmark="QQQ",
        benchmark_bars=bars,
        relative={
            "excess_returns_percent": {
                "5d": five_day,
                "20d": twenty_day,
                "60d": sixty_day,
            }
        },
    )


def test_relative_rotation_bullish_conditions_and_invalidation_follow_stance() -> None:
    decision = RelativeStrengthRotationStrategy().evaluate(
        _relative_rotation_context(five_day=6.0, twenty_day=6.0, sixty_day=4.0)
    )

    confirmation = " ".join(decision["confirmation_conditions"]).replace(" ", "")
    invalidation = " ".join(decision["invalidation_conditions"]).replace(" ", "")
    assert decision["stance"] == "bullish"
    assert "5日" in confirmation and "正" in confirmation
    assert "5日" in invalidation and "转负" in invalidation and "持续" in invalidation


def test_relative_rotation_bearish_conditions_and_invalidation_follow_stance() -> None:
    decision = RelativeStrengthRotationStrategy().evaluate(
        _relative_rotation_context(five_day=-6.0, twenty_day=-6.0, sixty_day=-4.0)
    )

    confirmation = " ".join(decision["confirmation_conditions"]).replace(" ", "")
    invalidation = " ".join(decision["invalidation_conditions"]).replace(" ", "")
    assert decision["stance"] == "bearish"
    assert "5日" in confirmation and "负" in confirmation
    assert "5日" in invalidation and "转正" in invalidation and "持续" in invalidation
