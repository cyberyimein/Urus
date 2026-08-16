from __future__ import annotations

from app.analytics.capital_flow import extract_capital_flow_signal


def _day(day: int, block: float, mid: float, small: float) -> dict[str, object]:
    return {
        "trading_date": f"2026-07-{day:02d}",
        "in_flow": block + mid + small,
        "main_in_flow": block,
        "super_in_flow": block * 0.6,
        "big_in_flow": block * 0.4,
        "mid_in_flow": mid,
        "sml_in_flow": small,
    }


def test_extracts_large_order_absorption_candidate_after_persistent_outflow() -> None:
    result = extract_capital_flow_signal(
        [
            _day(23, -10, -2, -1),
            _day(24, -12, -2, -2),
            _day(25, -8, 1, -1),
            _day(28, -9, -3, -1),
            _day(29, 15, -4, -3),
        ]
    )

    assert result["signal"] == "large_order_absorption_candidate"
    assert result["features"]["prior_4_block_outflow_days"] == 4
    assert result["features"]["prior_block_outflow_streak_30d"] == 4
    assert result["features"]["latest_mid_small_flow"] == -7
    assert len(result["recent_5d"]) == 5
    assert "不能等同于机构" in result["interpretation_guardrail"]


def test_extracts_distribution_risk_when_small_orders_buy_and_large_orders_sell() -> None:
    result = extract_capital_flow_signal(
        [_day(21 + index, 5, 2, 1) for index in range(4)] + [_day(25, -12, 4, 3)]
    )

    assert result["signal"] == "large_order_distribution_risk"
    assert result["features"]["prior_4_block_inflow_days"] == 4


def test_requires_five_days_before_emitting_a_pattern_signal() -> None:
    result = extract_capital_flow_signal([_day(28, -5, -1, -1), _day(29, 9, -2, -1)])

    assert result["signal"] == "insufficient_data"
    assert result["confidence"] <= 0.5


def test_marks_bucket_identity_mismatch_as_partial_quality() -> None:
    observations = [_day(21 + index, 5, 2, 1) for index in range(5)]
    observations[-1]["in_flow"] = 999

    result = extract_capital_flow_signal(observations)

    assert result["quality_status"] == "partial"
    assert any("四档合计不一致" in warning for warning in result["quality_warnings"])


def test_projects_only_the_latest_five_days_from_a_longer_calculation_window() -> None:
    result = extract_capital_flow_signal(
        [_day(10 + index, float(index + 1), 2, 1) for index in range(10)]
    )

    assert result["features"]["available_trading_days"] == 10
    assert [item["trading_date"] for item in result["recent_5d"]] == [
        "2026-07-15",
        "2026-07-16",
        "2026-07-17",
        "2026-07-18",
        "2026-07-19",
    ]
