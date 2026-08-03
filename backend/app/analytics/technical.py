from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import log, sqrt
from statistics import fmean, pstdev
from typing import Any


ANNUALIZATION_FACTOR = 252
RETURN_WINDOWS = (1, 5, 20, 60, 120, 252)
MOVING_AVERAGE_WINDOWS = (10, 20, 50, 100, 200)
REALIZED_VOLATILITY_WINDOWS = (10, 20, 60)


def calculate_technical_indicators(
    bars: Sequence[Mapping[str, Any]],
    *,
    source: str,
) -> dict[str, object]:
    """Calculate daily technical indicators from OHLC bars.

    The function is provider-independent so the same calculation can be
    reused for QQQ in 1A and INTC/other instruments in 3A. Realized volatility
    uses 10/20/60 log-return observations and population standard deviation,
    annualized by ``sqrt(252)``. ATR14 uses the simple average of the last 14
    true ranges. Bollinger 20/2 uses the population standard deviation of the
    last 20 closes.
    """
    clean_bars = _clean_bars(bars)
    as_of = str(clean_bars[-1]["date"]) if clean_bars else None
    result: dict[str, object] = {
        "is_mock": False,
        "available": False,
        "quality_status": "unavailable",
        "source": source,
        "as_of": as_of,
        "sample_count": 0,
        "warnings": [],
    }
    if not clean_bars:
        result["warnings"] = ["没有可用于计算技术指标的有效日线。"]
        return result

    closes = [float(bar["close"]) for bar in clean_bars]
    warnings: list[str] = []

    result["returns_percent"] = {
        f"{window}d": _lookback_return(closes, window)
        for window in RETURN_WINDOWS
    }
    result["moving_average"] = {
        f"{window}d": _average(closes, window)
        for window in MOVING_AVERAGE_WINDOWS
    }
    result["high_low_distance_percent"] = {
        "20d_high": _distance_from_extreme(closes, window=20, high=True),
        "20d_low": _distance_from_extreme(closes, window=20, high=False),
        "60d_high": _distance_from_extreme(closes, window=60, high=True),
        "60d_low": _distance_from_extreme(closes, window=60, high=False),
        "252d_high": _distance_from_extreme(closes, window=252, high=True),
        "252d_low": _distance_from_extreme(closes, window=252, high=False),
    }

    for window in REALIZED_VOLATILITY_WINDOWS:
        key = f"realized_volatility_{window}d"
        if len(closes) >= window + 1:
            log_returns = [
                log(closes[index] / closes[index - 1])
                for index in range(1, len(closes))
                if closes[index] > 0 and closes[index - 1] > 0
            ][-window:]
            if len(log_returns) == window:
                result[key] = _metric(
                    value=pstdev(log_returns) * sqrt(ANNUALIZATION_FACTOR) * 100,
                    unit="percent",
                    as_of=as_of,
                    sample_count=window,
                    source=source,
                    window=window,
                    annualization_factor=ANNUALIZATION_FACTOR,
                )
        if key not in result:
            warnings.append(f"{window} 日年化实现波动率需要至少 {window + 1} 个有效收盘价。")

    if len(clean_bars) >= 15:
        true_ranges: list[float] = []
        for index in range(1, len(clean_bars)):
            bar = clean_bars[index]
            previous_close = float(clean_bars[index - 1]["close"])
            high = float(bar["high"])
            low = float(bar["low"])
            true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        true_ranges = true_ranges[-14:]
        if len(true_ranges) == 14:
            atr14 = fmean(true_ranges)
            latest_close = closes[-1]
            result["atr14"] = _metric(
                value=atr14,
                unit="price",
                as_of=as_of,
                sample_count=14,
                source=source,
                window=14,
            )
            result["atr14_percent"] = _metric(
                value=(atr14 / latest_close * 100) if latest_close else None,
                unit="percent",
                as_of=as_of,
                sample_count=14,
                source=source,
                window=14,
            )
    else:
        warnings.append("ATR14 需要至少 15 个 OHLC 日线。")

    if len(closes) >= 20:
        window = closes[-20:]
        middle = fmean(window)
        deviation = pstdev(window)
        upper = middle + 2 * deviation
        lower = middle - 2 * deviation
        current = closes[-1]
        width = upper - lower
        position_ratio = (current - lower) / width if width else None
        result["bollinger_20_2"] = {
            "upper": round(upper, 4),
            "middle": round(middle, 4),
            "lower": round(lower, 4),
            "current_price": round(current, 4),
            "position_ratio": round(position_ratio, 4) if position_ratio is not None else None,
            "position_percent": round(position_ratio * 100, 4) if position_ratio is not None else None,
            "unit": "price",
            "as_of": as_of,
            "sample_count": 20,
            "source": source,
            "window": 20,
            "standard_deviations": 2,
        }
    else:
        warnings.append("布林带 20/2 需要至少 20 个收盘价。")

    available_keys = {"realized_volatility_20d", "atr14", "atr14_percent", "bollinger_20_2"}
    result["sample_count"] = len(clean_bars)
    result["available"] = available_keys.issubset(result)
    result["quality_status"] = "ok" if result["available"] else "partial"
    result["warnings"] = warnings
    return result


def calculate_relative_strength(
    bars: Sequence[Mapping[str, Any]],
    benchmark_bars: Sequence[Mapping[str, Any]],
    *,
    benchmark: str,
    source: str,
) -> dict[str, object]:
    """Calculate date-aligned excess returns, beta, correlation and residuals.

    Both series are converted to close-to-close log returns before alignment.
    The output is intentionally descriptive; it does not assign a bullish or
    bearish score to the instrument.
    """
    instrument = _clean_bars(bars)
    benchmark_clean = _clean_bars(benchmark_bars)
    instrument_closes = {str(item["date"]): float(item["close"]) for item in instrument}
    benchmark_closes = {str(item["date"]): float(item["close"]) for item in benchmark_clean}
    dates = sorted(set(instrument_closes) & set(benchmark_closes))
    instrument_returns = _dated_returns(instrument_closes, dates)
    benchmark_returns = _dated_returns(benchmark_closes, dates)
    aligned = [
        (day, instrument_returns[day], benchmark_returns[day])
        for day in dates
        if day in instrument_returns and day in benchmark_returns
    ]
    result: dict[str, object] = {
        "is_mock": False,
        "available": bool(aligned),
        "quality_status": "ok" if aligned else "unavailable",
        "benchmark": benchmark,
        "source": source,
        "as_of": aligned[-1][0] if aligned else None,
        "sample_count": len(aligned),
        "excess_returns_percent": {},
        "beta": {},
        "correlation": {},
        "residual_returns_percent": {},
        "warnings": [],
    }
    if not aligned:
        result["warnings"] = ["标的与基准没有可对齐的共同日线收益。"]
        return result

    instrument_values = [item[1] for item in aligned]
    benchmark_values = [item[2] for item in aligned]
    for window in (5, 20, 60):
        if len(aligned) < window:
            result["warnings"].append(f"相对强弱 {window} 日窗口样本不足。")
            continue
        instrument_window = instrument_values[-window:]
        benchmark_window = benchmark_values[-window:]
        instrument_total = _compound_return(instrument_window)
        benchmark_total = _compound_return(benchmark_window)
        excess = (1 + instrument_total) / (1 + benchmark_total) - 1
        key = f"{window}d"
        result["excess_returns_percent"][key] = round(excess * 100, 4)
        result["residual_returns_percent"][key] = round(
            (instrument_total - benchmark_total) * 100,
            4,
        )

    for window in (20, 60):
        if len(aligned) < window:
            continue
        instrument_window = instrument_values[-window:]
        benchmark_window = benchmark_values[-window:]
        beta = _beta(instrument_window, benchmark_window)
        correlation = _correlation(instrument_window, benchmark_window)
        result["beta"][f"{window}d"] = round(beta, 6) if beta is not None else None
        result["correlation"][f"{window}d"] = (
            round(correlation, 6) if correlation is not None else None
        )
    result["available"] = bool(result["excess_returns_percent"])
    result["quality_status"] = "ok" if result["available"] else "partial"
    return result


def _average(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return round(fmean(values[-window:]), 4)


def _lookback_return(values: list[float], periods: int) -> float | None:
    if len(values) <= periods or values[-periods - 1] == 0:
        return None
    return round((values[-1] - values[-periods - 1]) / values[-periods - 1] * 100, 4)


def _distance_from_extreme(values: list[float], *, window: int, high: bool) -> float | None:
    if len(values) < window or values[-1] == 0:
        return None
    extreme = max(values[-window:]) if high else min(values[-window:])
    return round((values[-1] - extreme) / extreme * 100, 4) if extreme else None


def _dated_returns(closes: dict[str, float], dates: list[str]) -> dict[str, float]:
    returns: dict[str, float] = {}
    previous: float | None = None
    for day in dates:
        current = closes[day]
        if previous is not None and previous > 0 and current > 0:
            returns[day] = log(current / previous)
        previous = current
    return returns


def _compound_return(values: list[float]) -> float:
    total = 1.0
    for value in values:
        total *= 1 + value
    return total - 1


def _beta(values: list[float], benchmark: list[float]) -> float | None:
    if len(values) != len(benchmark) or len(values) < 2:
        return None
    benchmark_mean = fmean(benchmark)
    variance = sum((item - benchmark_mean) ** 2 for item in benchmark)
    if variance == 0:
        return None
    covariance = sum(
        (value - fmean(values)) * (item - benchmark_mean)
        for value, item in zip(values, benchmark, strict=True)
    )
    return covariance / variance


def _correlation(values: list[float], benchmark: list[float]) -> float | None:
    if len(values) != len(benchmark) or len(values) < 2:
        return None
    values_mean = fmean(values)
    benchmark_mean = fmean(benchmark)
    numerator = sum(
        (value - values_mean) * (item - benchmark_mean)
        for value, item in zip(values, benchmark, strict=True)
    )
    values_std = sqrt(sum((value - values_mean) ** 2 for value in values))
    benchmark_std = sqrt(sum((item - benchmark_mean) ** 2 for item in benchmark))
    if values_std == 0 or benchmark_std == 0:
        return None
    return numerator / (values_std * benchmark_std)


def _clean_bars(bars: Sequence[Mapping[str, Any]]) -> list[dict[str, object]]:
    clean: list[dict[str, object]] = []
    for bar in bars:
        try:
            clean.append(
                {
                    "date": str(bar["date"]),
                    "open": float(bar["open"]),
                    "high": float(bar["high"]),
                    "low": float(bar["low"]),
                    "close": float(bar["close"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(clean, key=lambda item: str(item["date"]))


def _metric(
    *,
    value: float | None,
    unit: str,
    as_of: str | None,
    sample_count: int,
    source: str,
    window: int,
    **extra: object,
) -> dict[str, object]:
    return {
        "value": round(value, 6) if value is not None else None,
        "unit": unit,
        "as_of": as_of,
        "sample_count": sample_count,
        "source": source,
        "window": window,
        **extra,
    }
