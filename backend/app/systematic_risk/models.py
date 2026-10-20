"""Pure daily trend and volatility-target scenario calculations."""

from __future__ import annotations

from collections.abc import Sequence
import math
from statistics import fmean
from typing import Any


MODEL_VERSION = "urus.systematic_risk.trend_vol_proxy.v1"
TRADING_DAYS_PER_YEAR = 252


def evaluate_trend_scenario(
    closes: Sequence[float],
    assumed_close: float,
    *,
    fast: int,
    slow: int,
    target_vol: float = 0.10,
    cap: float = 1.0,
    volatility_window: int = 20,
) -> dict[str, Any]:
    """Compare a daily-close baseline with one provisional assumed close.

    ``closes`` must contain ordered, completed daily closes through yesterday.
    The scenario appends exactly one assumed close and replaces the oldest
    return in the volatility window.  It never adds intraday observations as
    additional daily returns.
    """

    normalized_assumed_close = _positive_finite_number(assumed_close)
    normalized_closes = _normalize_closes(closes)
    normalized_target_vol = _positive_finite_number(target_vol)
    normalized_cap = _positive_finite_number(cap)

    if normalized_closes is None:
        return _unavailable("invalid_closes", normalized_assumed_close)
    if normalized_assumed_close is None:
        return _unavailable("invalid_assumed_close", None)
    if not _is_positive_integer(fast):
        return _unavailable("invalid_fast_window", normalized_assumed_close)
    if not _is_positive_integer(slow) or slow <= fast:
        return _unavailable("invalid_slow_window", normalized_assumed_close)
    if not _is_positive_integer(volatility_window) or volatility_window < 2:
        return _unavailable("invalid_volatility_window", normalized_assumed_close)
    if normalized_target_vol is None:
        return _unavailable("invalid_target_volatility", normalized_assumed_close)
    if normalized_cap is None:
        return _unavailable("invalid_exposure_cap", normalized_assumed_close)

    required_closes = max(slow, volatility_window + 1)
    if len(normalized_closes) < required_closes:
        return _unavailable("insufficient_daily_closes", normalized_assumed_close)

    scenario_closes = [*normalized_closes, normalized_assumed_close]
    baseline_signal = _trend_signal(normalized_closes, fast=fast, slow=slow)
    scenario_signal = _trend_signal(scenario_closes, fast=fast, slow=slow)
    baseline_volatility = _annualized_volatility(
        normalized_closes,
        window=volatility_window,
    )
    scenario_volatility = _annualized_volatility(
        scenario_closes,
        window=volatility_window,
    )

    baseline: dict[str, Any] = {
        "trend_signal": baseline_signal,
        "annualized_volatility": baseline_volatility,
        "target_exposure": None,
    }
    scenario: dict[str, Any] = {
        "assumed_close": normalized_assumed_close,
        "trend_signal": scenario_signal,
        "annualized_volatility": scenario_volatility,
        "target_exposure": None,
        "provisional": True,
    }
    if baseline_volatility is None:
        return _unavailable(
            "baseline_volatility_unavailable",
            normalized_assumed_close,
            baseline=baseline,
            scenario=scenario,
        )
    if baseline_volatility == 0:
        return _unavailable(
            "baseline_zero_volatility",
            normalized_assumed_close,
            baseline=baseline,
            scenario=scenario,
        )
    if scenario_volatility is None:
        return _unavailable(
            "scenario_volatility_unavailable",
            normalized_assumed_close,
            baseline=baseline,
            scenario=scenario,
        )
    if scenario_volatility == 0:
        return _unavailable(
            "scenario_zero_volatility",
            normalized_assumed_close,
            baseline=baseline,
            scenario=scenario,
        )

    baseline_exposure = _target_exposure(
        baseline_signal,
        baseline_volatility,
        target_vol=normalized_target_vol,
        cap=normalized_cap,
    )
    scenario_exposure = _target_exposure(
        scenario_signal,
        scenario_volatility,
        target_vol=normalized_target_vol,
        cap=normalized_cap,
    )
    baseline["target_exposure"] = baseline_exposure
    scenario["target_exposure"] = scenario_exposure

    flip_price, flip_price_reason = _flip_price(
        normalized_closes,
        fast=fast,
        slow=slow,
    )
    trend_only_exposure = _target_exposure(
        scenario_signal,
        baseline_volatility,
        target_vol=normalized_target_vol,
        cap=normalized_cap,
    )
    volatility_only_exposure = _target_exposure(
        baseline_signal,
        scenario_volatility,
        target_vol=normalized_target_vol,
        cap=normalized_cap,
    )
    trend_only_change = trend_only_exposure - baseline_exposure
    volatility_only_change = volatility_only_exposure - baseline_exposure
    joint_change = scenario_exposure - baseline_exposure
    return {
        "available": True,
        "reason_code": "available",
        "message": "趋势与波动目标情景计算可用。",
        "model_version": MODEL_VERSION,
        "baseline": baseline,
        "scenario": scenario,
        "flip_price": flip_price,
        "flip_price_reason_code": flip_price_reason,
        "exposure_change": {
            "trend_only": trend_only_change,
            "volatility_only": volatility_only_change,
            "joint": joint_change,
            "interaction": joint_change - trend_only_change - volatility_only_change,
            "direction": _exposure_change_direction(
                baseline_exposure,
                scenario_exposure,
            ),
        },
    }


def _normalize_closes(closes: Sequence[float]) -> list[float] | None:
    if isinstance(closes, (str, bytes)) or not isinstance(closes, Sequence):
        return None
    normalized: list[float] = []
    for value in closes:
        number = _positive_finite_number(value)
        if number is None:
            return None
        normalized.append(number)
    return normalized


def _positive_finite_number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _is_positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _trend_signal(closes: Sequence[float], *, fast: int, slow: int) -> int:
    fast_average = fmean(closes[-fast:])
    slow_average = fmean(closes[-slow:])
    return (fast_average > slow_average) - (fast_average < slow_average)


def _annualized_volatility(closes: Sequence[float], *, window: int) -> float | None:
    if len(closes) < window + 1:
        return None
    returns = [
        math.log(current) - math.log(previous)
        for previous, current in zip(closes, closes[1:])
    ][-window:]
    if len(returns) != window:
        return None
    average_return = fmean(returns)
    squared_deviations = [(item - average_return) ** 2 for item in returns]
    variance = math.fsum(squared_deviations) / (window - 1)
    if not math.isfinite(variance) or variance < 0:
        return None
    volatility = math.sqrt(variance) * math.sqrt(TRADING_DAYS_PER_YEAR)
    return volatility if math.isfinite(volatility) else None


def _target_exposure(
    trend_signal: int,
    annualized_volatility: float,
    *,
    target_vol: float,
    cap: float,
) -> float:
    raw_exposure = trend_signal * target_vol / annualized_volatility
    return max(-cap, min(cap, raw_exposure))


def _flip_price(
    closes: Sequence[float],
    *,
    fast: int,
    slow: int,
) -> tuple[float | None, str | None]:
    fast_history = closes[-(fast - 1) :] if fast > 1 else []
    slow_history = closes[-(slow - 1) :]
    if len(fast_history) != fast - 1 or len(slow_history) != slow - 1:
        return None, "insufficient_flip_history"

    fast_sum = math.fsum(fast_history)
    slow_sum = math.fsum(slow_history)
    denominator = slow - fast
    root = (fast * slow_sum - slow * fast_sum) / denominator
    if not math.isfinite(root) or root <= 0:
        return None, "non_positive_flip_root"

    fast_average = (fast_sum + root) / fast
    slow_average = (slow_sum + root) / slow
    if not math.isclose(fast_average, slow_average, rel_tol=1e-12, abs_tol=1e-12):
        return None, "flip_root_verification_failed"
    return root, None


def _exposure_change_direction(baseline: float, scenario: float) -> str:
    if scenario == baseline:
        return "unchanged"
    if baseline > 0 and scenario < 0:
        return "long_to_short"
    if baseline < 0 and scenario > 0:
        return "short_to_long"
    if baseline > 0 and scenario < baseline:
        return "long_reduction"
    if baseline < 0 and scenario > baseline:
        return "short_cover"
    if scenario > 0:
        return "long_increase"
    if scenario < 0:
        return "short_increase"
    return "unchanged"


def _unavailable(
    reason_code: str,
    assumed_close: float | None,
    *,
    baseline: dict[str, Any] | None = None,
    scenario: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "available": False,
        "reason_code": reason_code,
        "message": _reason_message(reason_code),
        "model_version": MODEL_VERSION,
        "baseline": baseline
        or {
            "trend_signal": None,
            "annualized_volatility": None,
            "target_exposure": None,
        },
        "scenario": scenario
        or {
            "assumed_close": assumed_close,
            "trend_signal": None,
            "annualized_volatility": None,
            "target_exposure": None,
            "provisional": True,
        },
        "flip_price": None,
        "exposure_change": {
            "trend_only": None,
            "volatility_only": None,
            "joint": None,
            "interaction": None,
            "direction": "unavailable",
        },
    }


def _reason_message(reason_code: str) -> str:
    messages = {
        "invalid_closes": "历史日收盘价必须是按时间排序的有限正数序列。",
        "invalid_assumed_close": "假设收盘价必须是有限正数。",
        "invalid_fast_window": "快速均线窗口必须是正整数。",
        "invalid_slow_window": "慢速均线窗口必须是大于快速窗口的正整数。",
        "invalid_volatility_window": "波动率窗口必须是至少为 2 的整数。",
        "invalid_target_volatility": "目标年化波动率必须是有限正数。",
        "invalid_exposure_cap": "敞口上限必须是有限正数。",
        "insufficient_daily_closes": "日线历史不足以计算慢速均线和波动率。",
        "baseline_volatility_unavailable": "基线年化波动率不可用。",
        "baseline_zero_volatility": "基线波动率为零，不能计算波动目标敞口。",
        "scenario_volatility_unavailable": "假设收盘情景的年化波动率不可用。",
        "scenario_zero_volatility": "假设收盘情景的波动率为零，不能计算波动目标敞口。",
        "flip_price_unavailable": "均线翻转价无正值解析根或未通过代入复核。",
    }
    return messages.get(reason_code, "系统性趋势风险情景不可用。")
