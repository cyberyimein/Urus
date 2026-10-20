"""Acceptance tests for the P6 pure trend/volatility scenario model.

These tests deliberately use an independent reference implementation.  The
module under test is expected to remain a pure function; service, persistence,
calendar and API behavior are covered separately after their contracts are
implemented.
"""

from __future__ import annotations

import math
from statistics import mean

import pytest

from app.systematic_risk.models import evaluate_trend_scenario


TARGET_VOL = 0.10
CAP = 1.0
TRADING_DAYS = 252


def _sma(values: list[float], window: int) -> float:
    return mean(values[-window:])


def _signal(values: list[float], *, fast: int, slow: int) -> int:
    difference = _sma(values, fast) - _sma(values, slow)
    return (difference > 0) - (difference < 0)


def _annualized_volatility(values: list[float], *, window: int = 20) -> float:
    returns = [math.log(current / previous) for previous, current in zip(values, values[1:])]
    recent = returns[-window:]
    average = mean(recent)
    variance = sum((item - average) ** 2 for item in recent) / (len(recent) - 1)
    return math.sqrt(variance) * math.sqrt(TRADING_DAYS)


def _clip_exposure(signal: int, volatility: float, *, target_vol: float, cap: float) -> float:
    raw = signal * target_vol / volatility
    return max(-cap, min(cap, raw))


def _flip_price(values: list[float], *, fast: int, slow: int) -> float:
    prior_fast_sum = sum(values[-(fast - 1) :])
    prior_slow_sum = sum(values[-(slow - 1) :])
    return (fast * prior_slow_sum - slow * prior_fast_sum) / (slow - fast)


def _trend_fixture() -> list[float]:
    return [100.0 + index * 0.6 + (index % 4) * 0.07 for index in range(40)]


def _uptrend_fixture() -> list[float]:
    return [100.0 + index * 0.5 + (index % 3) * 0.1 for index in range(40)]


def _downtrend_fixture() -> list[float]:
    return [150.0 - index * 0.5 - (index % 3) * 0.1 for index in range(40)]


def test_evaluate_matches_independent_sma_volatility_flip_and_decomposition() -> None:
    closes = _trend_fixture()
    assumed_close = 50.0
    fast, slow = 10, 20

    result = evaluate_trend_scenario(
        closes,
        assumed_close,
        fast=fast,
        slow=slow,
        target_vol=TARGET_VOL,
        cap=CAP,
        volatility_window=20,
    )

    assert result["available"] is True
    assert result["reason_code"] in {None, "available", "ok"}
    assert isinstance(result["model_version"], str) and result["model_version"]

    baseline_signal = _signal(closes, fast=fast, slow=slow)
    baseline_vol = _annualized_volatility(closes)
    scenario_values = [*closes, assumed_close]
    scenario_signal = _signal(scenario_values, fast=fast, slow=slow)
    scenario_vol = _annualized_volatility(scenario_values)
    baseline_exposure = _clip_exposure(
        baseline_signal,
        baseline_vol,
        target_vol=TARGET_VOL,
        cap=CAP,
    )
    scenario_exposure = _clip_exposure(
        scenario_signal,
        scenario_vol,
        target_vol=TARGET_VOL,
        cap=CAP,
    )

    baseline = result["baseline"]
    scenario = result["scenario"]
    assert baseline["trend_signal"] == baseline_signal
    assert baseline["annualized_volatility"] == pytest.approx(baseline_vol, rel=1e-12)
    assert baseline["target_exposure"] == pytest.approx(baseline_exposure, rel=1e-12)
    assert scenario["assumed_close"] == pytest.approx(assumed_close)
    assert scenario["trend_signal"] == scenario_signal
    assert scenario["annualized_volatility"] == pytest.approx(scenario_vol, rel=1e-12)
    assert scenario["target_exposure"] == pytest.approx(scenario_exposure, rel=1e-12)
    assert scenario["provisional"] is True

    expected_flip = _flip_price(closes, fast=fast, slow=slow)
    assert result["flip_price"] == pytest.approx(expected_flip, rel=1e-12)
    direct_fast = (sum(closes[-(fast - 1) :]) + expected_flip) / fast
    direct_slow = (sum(closes[-(slow - 1) :]) + expected_flip) / slow
    assert direct_fast == pytest.approx(direct_slow, rel=1e-12)

    changes = result["exposure_change"]
    expected_trend_only = _clip_exposure(
        scenario_signal,
        baseline_vol,
        target_vol=TARGET_VOL,
        cap=CAP,
    ) - baseline_exposure
    expected_volatility_only = _clip_exposure(
        baseline_signal,
        scenario_vol,
        target_vol=TARGET_VOL,
        cap=CAP,
    ) - baseline_exposure
    expected_joint = scenario_exposure - baseline_exposure
    assert changes["trend_only"] == pytest.approx(expected_trend_only, rel=1e-12)
    assert changes["volatility_only"] == pytest.approx(expected_volatility_only, rel=1e-12)
    assert changes["joint"] == pytest.approx(expected_joint, rel=1e-12)
    assert changes["interaction"] == pytest.approx(
        expected_joint - expected_trend_only - expected_volatility_only,
        rel=1e-12,
    )


@pytest.mark.parametrize(
    ("fast", "slow"),
    [(10, 20), (20, 60), (50, 200)],
)
def test_three_speed_models_use_the_requested_windows(fast: int, slow: int) -> None:
    closes = [250.0 + index * 0.15 + (index % 5) * 0.03 for index in range(260)]
    result = evaluate_trend_scenario(
        closes,
        closes[-1] * 1.01,
        fast=fast,
        slow=slow,
    )

    assert result["available"] is True
    assert result["baseline"]["trend_signal"] in {-1, 0, 1}
    assert result["scenario"]["trend_signal"] in {-1, 0, 1}
    assert set(result["exposure_change"]) >= {
        "trend_only",
        "volatility_only",
        "joint",
        "interaction",
        "direction",
    }


def test_equal_smas_have_zero_trend_signal() -> None:
    # The fast and slow windows have the same mean while returns still have
    # non-zero variance.  This isolates the neutral trend signal from the
    # separate zero-volatility degradation case below.
    closes = [99.5 if index % 2 == 0 else 100.5 for index in range(40)]
    result = evaluate_trend_scenario(closes, 100.0, fast=10, slow=20)

    assert result["available"] is True
    assert result["baseline"]["trend_signal"] == 0
    assert result["baseline"]["target_exposure"] == pytest.approx(0.0)


@pytest.mark.parametrize(
    ("kwargs", "reason_fragment"),
    [
        ({"fast": 20, "slow": 10}, "window"),
        ({"fast": 10.5, "slow": 20}, "window"),
        ({"fast": 10, "slow": 20, "target_vol": 0.0}, "target"),
        ({"fast": 10, "slow": 20, "cap": 0.0}, "cap"),
    ],
)
def test_invalid_model_parameters_do_not_silently_use_defaults(
    kwargs: dict[str, object],
    reason_fragment: str,
) -> None:
    closes = _trend_fixture()
    try:
        result = evaluate_trend_scenario(closes, 120.0, **kwargs)
    except ValueError:
        return

    assert result["available"] is False
    assert reason_fragment in str(result["reason_code"])


def test_negative_flip_root_is_unavailable_even_when_models_are_calculable() -> None:
    closes = [100.0] * 195 + [110.0, 120.0, 130.0, 140.0, 180.0]
    result = evaluate_trend_scenario(
        closes,
        170.0,
        fast=10,
        slow=20,
        target_vol=TARGET_VOL,
        cap=CAP,
    )

    assert result["available"] is True
    assert result["flip_price"] is None
    assert result["flip_price_reason_code"] == "non_positive_flip_root"


@pytest.mark.parametrize("assumed_close", [0.0, math.nan])
def test_invalid_assumed_close_degrades_the_scenario(assumed_close: float) -> None:
    try:
        result = evaluate_trend_scenario(
            _trend_fixture(),
            assumed_close,
            fast=10,
            slow=20,
        )
    except ValueError:
        return

    assert result["available"] is False
    assert result["reason_code"] == "invalid_assumed_close"


def test_zero_or_insufficient_volatility_is_unavailable() -> None:
    result = evaluate_trend_scenario(
        [100.0] * 40,
        100.5,
        fast=10,
        slow=20,
    )
    assert result["available"] is False
    assert result["reason_code"]

    insufficient = evaluate_trend_scenario(
        [100.0 + index for index in range(20)],
        121.0,
        fast=10,
        slow=20,
    )
    assert insufficient["available"] is False
    assert insufficient["reason_code"]


@pytest.mark.parametrize("bad_value", [0.0, -1.0, math.nan, math.inf, -math.inf])
def test_non_positive_or_non_finite_closes_are_rejected(bad_value: float) -> None:
    closes = _trend_fixture()
    closes[-1] = bad_value
    result = evaluate_trend_scenario(closes, 120.0, fast=10, slow=20)

    assert result["available"] is False
    assert result["reason_code"]


def test_exposure_is_capped_for_both_baseline_and_scenario() -> None:
    closes = _trend_fixture()
    result = evaluate_trend_scenario(
        closes,
        130.0,
        fast=10,
        slow=20,
        target_vol=1.0,
        cap=0.25,
    )

    assert result["available"] is True
    assert abs(result["baseline"]["target_exposure"]) <= 0.25
    assert abs(result["scenario"]["target_exposure"]) <= 0.25


@pytest.mark.parametrize(
    ("closes", "assumed_close", "expected_direction"),
    [
        (_uptrend_fixture(), 107.55, "long_reduction"),
        (_downtrend_fixture(), 117.45, "short_cover"),
    ],
)
def test_exposure_direction_distinguishes_long_reduction_from_short_cover(
    closes: list[float],
    assumed_close: float,
    expected_direction: str,
) -> None:
    result = evaluate_trend_scenario(
        closes,
        assumed_close,
        fast=10,
        slow=20,
        target_vol=TARGET_VOL,
        cap=CAP,
    )

    assert result["available"] is True
    assert result["exposure_change"]["direction"] == expected_direction


@pytest.mark.parametrize(
    ("closes", "assumed_close", "expected_direction"),
    [
        (_uptrend_fixture(), 50.0, "long_to_short"),
        (_downtrend_fixture(), 200.0, "short_to_long"),
    ],
)
def test_exposure_direction_marks_cross_zero_transitions(
    closes: list[float],
    assumed_close: float,
    expected_direction: str,
) -> None:
    result = evaluate_trend_scenario(
        closes,
        assumed_close,
        fast=10,
        slow=20,
        target_vol=TARGET_VOL,
        cap=CAP,
    )

    assert result["available"] is True
    assert result["baseline"]["trend_signal"] != result["scenario"]["trend_signal"]
    assert result["baseline"]["target_exposure"] * result["scenario"]["target_exposure"] < 0
    assert result["exposure_change"]["direction"] == expected_direction


def test_repeating_a_scenario_does_not_mutate_baseline_or_accumulate_returns() -> None:
    closes = _trend_fixture()
    first = evaluate_trend_scenario(closes, 120.0, fast=10, slow=20)
    repeated = evaluate_trend_scenario(closes, 120.0, fast=10, slow=20)
    different_quote = evaluate_trend_scenario(closes, 119.0, fast=10, slow=20)

    assert repeated == first
    assert different_quote["baseline"] == first["baseline"]
    assert different_quote["scenario"]["assumed_close"] == 119.0
    assert different_quote["scenario"]["annualized_volatility"] != first["scenario"]["annualized_volatility"]
