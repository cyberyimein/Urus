from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import log, sqrt, tanh
from statistics import fmean, pstdev
from typing import Any


ANNUALIZATION_FACTOR = 252
MOMENTUM_WINDOWS = (20, 60, 120, 252)
EMA_PAIRS = ((16, 64), (32, 128), (64, 256))
DONCHIAN_WINDOWS = (55, 120, 250)
VOLATILITY_WINDOWS = (20, 60)
TARGET_VOLATILITY = 0.10
MAX_EXPOSURE = 2.0


def calculate_cta_proxy_signal(
    bars: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    source: str,
    proxy_for: str,
    target_volatility: float = TARGET_VOLATILITY,
) -> dict[str, object]:
    """Estimate a transparent trend-following position from daily proxy bars.

    The result is a model estimate, not an observed CTA position or fund flow.
    Signals are intentionally parameter-ensembled and volatility-scaled.  The
    previous-day state is recomputed from the same frozen input so the reported
    pressure cannot depend on mutable process state.
    """

    clean = _clean_bars(bars)
    base: dict[str, object] = {
        "schema_version": "urus.cta_proxy_signal.v1",
        "symbol": symbol.upper(),
        "proxy_for": proxy_for,
        "source": source,
        "source_mode": "etf_proxy",
        "as_of": str(clean[-1]["date"]) if clean else None,
        "sample_count": len(clean),
        "available": False,
        "quality_status": "unavailable",
        "warnings": [],
        "methodology": {
            "momentum_windows": list(MOMENTUM_WINDOWS),
            "ema_pairs": [list(pair) for pair in EMA_PAIRS],
            "donchian_windows": list(DONCHIAN_WINDOWS),
            "component_weights": {"momentum": 0.4, "ema": 0.4, "donchian": 0.2},
            "target_volatility": target_volatility,
            "max_exposure": MAX_EXPOSURE,
        },
    }
    if len(clean) < 65:
        base["warnings"] = ["CTA 代理信号至少需要 65 根有效日线。"]
        return base

    current = _state(clean, target_volatility=target_volatility)
    previous = _state(clean[:-1], target_volatility=target_volatility)
    if current is None:
        base["warnings"] = ["有效日线不足以形成波动率缩放的 CTA 代理信号。"]
        return base

    exposure = float(current["target_exposure"])
    previous_exposure = (
        float(previous["target_exposure"]) if previous is not None else exposure
    )
    exposure_change = exposure - previous_exposure
    pressure_index = 100.0 * tanh(exposure_change / 0.25)
    warnings: list[str] = []
    if len(clean) < 253:
        warnings.append("不足 253 根日线；12 个月动量或 250 日突破尚未完整。")
    if len(clean) < 257:
        warnings.append("不足 257 根日线；64/256 EMA 慢周期尚未完整。")

    base.update(
        {
            "available": True,
            "quality_status": "ok" if not warnings else "partial",
            "warnings": warnings,
            "forecast_volatility": current["forecast_volatility"],
            "components": current["components"],
            "raw_signal": current["raw_signal"],
            "target_exposure": round(exposure, 6),
            "previous_target_exposure": round(previous_exposure, 6),
            "exposure_change": round(exposure_change, 6),
            "pressure_index": round(pressure_index, 2),
            "direction": _direction(exposure),
            "pressure_direction": _pressure_direction(exposure_change),
        }
    )
    return base


def aggregate_cta_proxy_signals(signals: Sequence[Mapping[str, Any]]) -> dict[str, object]:
    available = [item for item in signals if item.get("available") is True]
    if not available:
        return {
            "available": False,
            "signal_count": 0,
            "average_target_exposure": None,
            "average_pressure_index": None,
            "classification": "unavailable",
        }
    exposure = fmean(float(item["target_exposure"]) for item in available)
    pressure = fmean(float(item["pressure_index"]) for item in available)
    return {
        "available": True,
        "signal_count": len(available),
        "average_target_exposure": round(exposure, 6),
        "average_pressure_index": round(pressure, 2),
        "classification": _direction(exposure),
        "pressure_classification": _pressure_direction(pressure / 100.0),
    }


def _state(
    bars: Sequence[Mapping[str, object]], *, target_volatility: float
) -> dict[str, object] | None:
    closes = [float(item["close"]) for item in bars]
    if len(closes) < 65:
        return None
    volatility = _forecast_volatility(closes)
    if volatility is None or volatility <= 0:
        return None
    daily_volatility = volatility / sqrt(ANNUALIZATION_FACTOR)

    momentum_scores: dict[str, float] = {}
    for window in MOMENTUM_WINDOWS:
        if len(closes) <= window:
            continue
        total_return = log(closes[-1] / closes[-window - 1])
        score = tanh(total_return / (daily_volatility * sqrt(window)))
        momentum_scores[f"{window}d"] = round(score, 6)

    ema_scores: dict[str, float] = {}
    for fast, slow in EMA_PAIRS:
        if len(closes) < slow:
            continue
        fast_value = _ema(closes, fast)
        slow_value = _ema(closes, slow)
        scale = closes[-1] * daily_volatility * sqrt(max(slow - fast, 1))
        score = tanh((fast_value - slow_value) / scale) if scale else 0.0
        ema_scores[f"{fast}_{slow}"] = round(score, 6)

    donchian_scores: dict[str, float] = {}
    for window in DONCHIAN_WINDOWS:
        if len(closes) < window:
            continue
        sample = closes[-window:]
        low, high = min(sample), max(sample)
        score = 2.0 * (closes[-1] - low) / (high - low) - 1.0 if high > low else 0.0
        donchian_scores[f"{window}d"] = round(_clip(score, -1.0, 1.0), 6)

    momentum = fmean(momentum_scores.values()) if momentum_scores else 0.0
    ema = fmean(ema_scores.values()) if ema_scores else 0.0
    donchian = fmean(donchian_scores.values()) if donchian_scores else 0.0
    raw_signal = _clip(0.4 * momentum + 0.4 * ema + 0.2 * donchian, -1.0, 1.0)
    volatility_scalar = min(target_volatility / volatility, MAX_EXPOSURE)
    target_exposure = _clip(raw_signal * volatility_scalar, -MAX_EXPOSURE, MAX_EXPOSURE)
    return {
        "forecast_volatility": round(volatility, 6),
        "raw_signal": round(raw_signal, 6),
        "target_exposure": round(target_exposure, 6),
        "components": {
            "momentum": {
                "score": round(momentum, 6),
                "horizons": momentum_scores,
            },
            "ema": {"score": round(ema, 6), "pairs": ema_scores},
            "donchian": {
                "score": round(donchian, 6),
                "windows": donchian_scores,
            },
        },
    }


def _forecast_volatility(closes: Sequence[float]) -> float | None:
    returns = [log(closes[index] / closes[index - 1]) for index in range(1, len(closes))]
    estimates = [
        pstdev(returns[-window:]) * sqrt(ANNUALIZATION_FACTOR)
        for window in VOLATILITY_WINDOWS
        if len(returns) >= window
    ]
    return fmean(estimates) if estimates else None


def _ema(values: Sequence[float], window: int) -> float:
    current = fmean(values[:window])
    alpha = 2.0 / (window + 1.0)
    for value in values[window:]:
        current += alpha * (value - current)
    return current


def _clean_bars(bars: Sequence[Mapping[str, Any]]) -> list[dict[str, object]]:
    cleaned: dict[str, dict[str, object]] = {}
    for index, item in enumerate(bars):
        date = str(item.get("date") or item.get("bar_date") or item.get("time") or index)
        try:
            close = float(item["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if close <= 0:
            continue
        cleaned[date] = {"date": date, "close": close}
    return [cleaned[key] for key in sorted(cleaned)]


def _direction(value: float) -> str:
    if value >= 0.25:
        return "long"
    if value <= -0.25:
        return "short"
    return "neutral"


def _pressure_direction(value: float) -> str:
    if value >= 0.05:
        return "buying"
    if value <= -0.05:
        return "selling"
    return "stable"


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
