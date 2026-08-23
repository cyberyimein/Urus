from __future__ import annotations

from datetime import date, timedelta
import json

from app.decision_harness.strategies import (
    QualityLeftSideReversalStrategy,
    StrategyAdapter,
    StrategyRegistry,
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
