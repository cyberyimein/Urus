from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import log, sqrt
from statistics import fmean, pstdev
from typing import Any


ANNUALIZATION_FACTOR = 252
RETURN_WINDOWS = (1, 5, 20, 60, 120, 252)
MOVING_AVERAGE_WINDOWS = (10, 20, 50, 100, 200)
REALIZED_VOLATILITY_WINDOWS = (10, 20, 60)
BOLLINGER_DEVIATIONS = (1, 2, 3)
MACD_FAST_WINDOW = 12
MACD_SLOW_WINDOW = 26
MACD_SIGNAL_WINDOW = 9
RSI_WINDOW = 14
VOLUME_WINDOW = 20
VOLUME_SURGE_RATIO = 1.5
VOLUME_DRY_RATIO = 0.8
WIDE_RANGE_ATR_RATIO = 1.0
MOVE_THRESHOLD_PERCENT = 0.5


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
    true ranges. Bollinger 20/1, 20/2 and 20/3 use the population standard
    deviation of the last 20 closes. MACD is the conventional 12/26/9 EMA
    configuration. RSI14 uses Wilder smoothing after the initial 14-period
    average gain/loss. Volume effort/result compares the latest volume with
    its 20-day average and the price result with the latest true range.
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

    atr14_value: float | None = None
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
            atr14_value = atr14
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
        for deviations in BOLLINGER_DEVIATIONS:
            result[f"bollinger_20_{deviations}"] = _bollinger_metric(
                closes,
                deviations=deviations,
                as_of=as_of,
                source=source,
            )
        bollinger = result["bollinger_20_2"]
        if isinstance(bollinger, dict):
            width = float(bollinger["upper"]) - float(bollinger["lower"])
            middle = float(bollinger["middle"])
            result["bollinger_bandwidth_20"] = _metric(
                value=(width / middle * 100) if middle else None,
                unit="percent",
                as_of=as_of,
                sample_count=20,
                source=source,
                window=20,
                standard_deviations=2,
            )
    else:
        warnings.append("布林带 20/1、20/2、20/3 需要至少 20 个收盘价。")

    macd = _calculate_macd(
        closes,
        as_of=as_of,
        source=source,
    )
    result["macd_12_26_9"] = macd
    warnings.extend(str(item) for item in macd.get("warnings", []))
    rsi14 = _calculate_rsi(
        closes,
        window=RSI_WINDOW,
        as_of=as_of,
        source=source,
    )
    result["rsi14"] = rsi14
    warnings.extend(str(item) for item in rsi14.get("warnings", []))
    volume_effort_result = _calculate_volume_effort_result(
        clean_bars,
        atr14=atr14_value,
        as_of=as_of,
        source=source,
    )
    result["volume_effort_result"] = volume_effort_result
    warnings.extend(str(item) for item in volume_effort_result.get("warnings", []))

    available_keys = {
        "realized_volatility_20d",
        "atr14",
        "atr14_percent",
        "bollinger_20_1",
        "bollinger_20_2",
        "bollinger_20_3",
        "rsi14",
    }
    result["sample_count"] = len(clean_bars)
    result["available"] = available_keys.issubset(result)
    result["quality_status"] = "ok" if result["available"] else "partial"
    result["warnings"] = warnings
    return result


def _calculate_rsi(
    closes: list[float],
    *,
    window: int,
    as_of: str | None,
    source: str,
) -> dict[str, object]:
    """Return Wilder RSI with the previous value and an interpretable state."""
    warnings: list[str] = []
    if len(closes) < window + 1:
        warnings.append(f"RSI{window} 需要至少 {window + 1} 个收盘价。")
        return {
            "available": False,
            "quality_status": "unavailable",
            "value": None,
            "previous_value": None,
            "change": None,
            "state": "unavailable",
            "unit": "index",
            "as_of": as_of,
            "sample_count": len(closes),
            "source": source,
            "window": window,
            "method": "wilder",
            "thresholds": {"oversold": 30, "overbought": 70},
            "warnings": warnings,
        }

    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    average_gain = fmean(gains[:window])
    average_loss = fmean(losses[:window])

    def rsi(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        if gain == 0:
            return 0.0
        relative_strength = gain / loss
        return 100 - (100 / (1 + relative_strength))

    values = [rsi(average_gain, average_loss)]
    for index in range(window, len(changes)):
        average_gain = ((average_gain * (window - 1)) + gains[index]) / window
        average_loss = ((average_loss * (window - 1)) + losses[index]) / window
        values.append(rsi(average_gain, average_loss))

    value = values[-1]
    previous = values[-2] if len(values) > 1 else None
    if value >= 70:
        state = "overbought"
    elif value <= 30:
        state = "oversold"
    elif value >= 50:
        state = "positive"
    else:
        state = "negative"
    return {
        "available": True,
        "quality_status": "ok",
        "value": round(value, 6),
        "previous_value": round(previous, 6) if previous is not None else None,
        "change": round(value - previous, 6) if previous is not None else None,
        "state": state,
        "unit": "index",
        "as_of": as_of,
        "sample_count": len(closes),
        "source": source,
        "window": window,
        "method": "wilder",
        "thresholds": {"oversold": 30, "overbought": 70},
        "warnings": warnings,
    }


def _bollinger_metric(
    closes: list[float],
    *,
    deviations: int,
    as_of: str | None,
    source: str,
) -> dict[str, object]:
    window = closes[-20:]
    middle = fmean(window)
    standard_deviation = pstdev(window)
    upper = middle + deviations * standard_deviation
    lower = middle - deviations * standard_deviation
    current = closes[-1]
    width = upper - lower
    position_ratio = (current - lower) / width if width else None
    return {
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
        "standard_deviations": deviations,
    }


def _ema_series(values: list[float], window: int) -> list[float | None]:
    """Return an EMA aligned to the input, seeded with the initial SMA."""
    series: list[float | None] = [None] * len(values)
    if len(values) < window:
        return series
    current = fmean(values[:window])
    series[window - 1] = current
    alpha = 2 / (window + 1)
    for index in range(window, len(values)):
        current = (values[index] - current) * alpha + current
        series[index] = current
    return series


def _calculate_macd(
    closes: list[float],
    *,
    as_of: str | None,
    source: str,
) -> dict[str, object]:
    fast = _ema_series(closes, MACD_FAST_WINDOW)
    slow = _ema_series(closes, MACD_SLOW_WINDOW)
    dif_series: list[float | None] = [
        (fast[index] - slow[index])
        if fast[index] is not None and slow[index] is not None
        else None
        for index in range(len(closes))
    ]
    dif_start = next((index for index, value in enumerate(dif_series) if value is not None), None)
    dea_series: list[float | None] = [None] * len(closes)
    if dif_start is not None:
        valid_dif = [value for value in dif_series[dif_start:] if value is not None]
        if len(valid_dif) >= MACD_SIGNAL_WINDOW:
            alpha = 2 / (MACD_SIGNAL_WINDOW + 1)
            dea = fmean(valid_dif[:MACD_SIGNAL_WINDOW])
            dea_index = dif_start + MACD_SIGNAL_WINDOW - 1
            dea_series[dea_index] = dea
            for offset, dif in enumerate(valid_dif[MACD_SIGNAL_WINDOW:], start=MACD_SIGNAL_WINDOW):
                dea = (dif - dea) * alpha + dea
                dea_series[dif_start + offset] = dea

    histogram_series: list[float | None] = [
        (dif_series[index] - dea_series[index])
        if dif_series[index] is not None and dea_series[index] is not None
        else None
        for index in range(len(closes))
    ]
    current_index = len(closes) - 1
    previous_index = current_index - 1
    dif = dif_series[current_index]
    dea = dea_series[current_index]
    histogram = histogram_series[current_index]
    previous_dif = dif_series[previous_index] if previous_index >= 0 else None
    previous_dea = dea_series[previous_index] if previous_index >= 0 else None
    previous_histogram = histogram_series[previous_index] if previous_index >= 0 else None

    crossover = "none"
    if previous_dif is not None and previous_dea is not None and dif is not None and dea is not None:
        if previous_dif <= previous_dea and dif > dea:
            crossover = "bullish_cross"
        elif previous_dif >= previous_dea and dif < dea:
            crossover = "bearish_cross"

    if dif is None:
        zero_axis = "unavailable"
    elif dif > 0:
        zero_axis = "above_zero"
    elif dif < 0:
        zero_axis = "below_zero"
    else:
        zero_axis = "on_zero"

    if histogram is None or previous_histogram is None:
        momentum = "unavailable"
    elif histogram > 0 and histogram > previous_histogram:
        momentum = "bullish_accelerating"
    elif histogram > 0:
        momentum = "bullish_fading"
    elif histogram < 0 and histogram < previous_histogram:
        momentum = "bearish_accelerating"
    elif histogram < 0:
        momentum = "bearish_fading"
    else:
        momentum = "flat"

    warnings: list[str] = []
    if len(closes) < MACD_SLOW_WINDOW:
        warnings.append(f"MACD 需要至少 {MACD_SLOW_WINDOW} 个收盘价。")
    elif len(closes) < MACD_SLOW_WINDOW + MACD_SIGNAL_WINDOW - 1:
        warnings.append(
            f"MACD DEA 需要至少 {MACD_SLOW_WINDOW + MACD_SIGNAL_WINDOW - 1} 个收盘价。"
        )
    available = dif is not None and dea is not None and histogram is not None
    return {
        "available": available,
        "quality_status": "ok" if available else "partial" if dif is not None else "unavailable",
        "source": source,
        "as_of": as_of,
        "sample_count": len(closes),
        "fast_window": MACD_FAST_WINDOW,
        "slow_window": MACD_SLOW_WINDOW,
        "signal_window": MACD_SIGNAL_WINDOW,
        "dif": round(dif, 6) if dif is not None else None,
        "dea": round(dea, 6) if dea is not None else None,
        "histogram": round(histogram, 6) if histogram is not None else None,
        "previous_dif": round(previous_dif, 6) if previous_dif is not None else None,
        "previous_dea": round(previous_dea, 6) if previous_dea is not None else None,
        "previous_histogram": round(previous_histogram, 6) if previous_histogram is not None else None,
        "crossover": crossover,
        "zero_axis": zero_axis,
        "momentum": momentum,
        "warnings": warnings,
    }


def _calculate_volume_effort_result(
    bars: list[dict[str, object]],
    *,
    atr14: float | None,
    as_of: str | None,
    source: str,
) -> dict[str, object]:
    warnings: list[str] = []
    result: dict[str, object] = {
        "available": False,
        "quality_status": "unavailable",
        "source": source,
        "as_of": as_of,
        "sample_count": 0,
        "latest_volume": None,
        "volume_sma_20": None,
        "volume_ratio_20d": None,
        "return_1d_percent": None,
        "true_range": None,
        "range_atr_ratio": None,
        "close_location_ratio": None,
        "effort": "unavailable",
        "result_direction": "unavailable",
        "combination": "unavailable",
        "signal": "unavailable",
        "signal_strength": "unavailable",
        "thresholds": {
            "volume_surge_ratio": VOLUME_SURGE_RATIO,
            "volume_dry_ratio": VOLUME_DRY_RATIO,
            "wide_range_atr_ratio": WIDE_RANGE_ATR_RATIO,
            "move_threshold_percent": MOVE_THRESHOLD_PERCENT,
        },
        "warnings": warnings,
    }
    if len(bars) < VOLUME_WINDOW + 1:
        warnings.append(f"成交量 Effort vs Result 需要至少 {VOLUME_WINDOW + 1} 根日线。")
        return result

    volumes = [bar.get("volume") for bar in bars[-VOLUME_WINDOW - 1 :]]
    if any(not isinstance(value, (int, float)) or float(value) < 0 for value in volumes):
        warnings.append("历史日线缺少有效成交量，无法计算 Effort vs Result。")
        return result

    numeric_volumes = [float(value) for value in volumes]
    baseline = numeric_volumes[:-1]
    latest_volume = numeric_volumes[-1]
    volume_sma = fmean(baseline)
    volume_ratio = latest_volume / volume_sma if volume_sma else None
    latest = bars[-1]
    previous = bars[-2]
    close = float(latest["close"])
    previous_close = float(previous["close"])
    high = float(latest["high"])
    low = float(latest["low"])
    true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
    close_location = (close - low) / (high - low) if high != low else None
    return_percent = ((close - previous_close) / previous_close * 100) if previous_close else None

    effort = "unavailable"
    if volume_ratio is not None:
        if volume_ratio >= VOLUME_SURGE_RATIO:
            effort = "high"
        elif volume_ratio <= VOLUME_DRY_RATIO:
            effort = "low"
        else:
            effort = "normal"
    if return_percent is None:
        result_direction = "unavailable"
    elif return_percent <= -MOVE_THRESHOLD_PERCENT:
        result_direction = "down"
    elif return_percent >= MOVE_THRESHOLD_PERCENT:
        result_direction = "up"
    else:
        result_direction = "flat"
    combination = (
        f"{effort}_{result_direction}"
        if effort != "unavailable" and result_direction != "unavailable"
        else "unavailable"
    )
    range_atr_ratio = true_range / atr14 if atr14 else None
    wide_range = range_atr_ratio is not None and range_atr_ratio >= WIDE_RANGE_ATR_RATIO
    close_low = close_location is not None and close_location <= 0.3
    close_high = close_location is not None and close_location >= 0.7

    signal = "neutral"
    signal_strength = "neutral"
    if effort == "high" and result_direction == "down" and wide_range and close_low:
        signal, signal_strength = "volume_down_distribution", "strong"
    elif effort == "high" and result_direction == "down":
        signal, signal_strength = "volume_down_absorption", "moderate"
    elif effort == "high" and result_direction == "up" and wide_range and close_high:
        signal, signal_strength = "volume_up_demand", "strong"
    elif effort == "high" and result_direction == "up":
        signal, signal_strength = "volume_up_absorption", "moderate"
    elif effort == "low" and result_direction in {"up", "down"}:
        signal, signal_strength = "low_volume_move", "weak"

    result.update(
        {
            "available": True,
            "quality_status": "ok",
            "sample_count": VOLUME_WINDOW,
            "latest_volume": round(latest_volume, 4),
            "volume_sma_20": round(volume_sma, 4),
            "volume_ratio_20d": round(volume_ratio, 4) if volume_ratio is not None else None,
            "return_1d_percent": round(return_percent, 4) if return_percent is not None else None,
            "true_range": round(true_range, 4),
            "range_atr_ratio": round(range_atr_ratio, 4) if range_atr_ratio is not None else None,
            "close_location_ratio": round(close_location, 4) if close_location is not None else None,
            "effort": effort,
            "result_direction": result_direction,
            "combination": combination,
            "signal": signal,
            "signal_strength": signal_strength,
        }
    )
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
            cleaned: dict[str, object] = {
                "date": str(bar["date"]),
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
            }
            raw_volume = bar.get("volume")
            if raw_volume is not None and str(raw_volume).strip() not in {"", "N/A", "--", "nan"}:
                cleaned["volume"] = float(raw_volume)
            else:
                cleaned["volume"] = None
            clean.append(cleaned)
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
