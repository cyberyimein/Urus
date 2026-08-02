from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import log, sqrt
from statistics import fmean, pstdev
from typing import Any


ANNUALIZATION_FACTOR = 252


def calculate_technical_indicators(
    bars: Sequence[Mapping[str, Any]],
    *,
    source: str,
) -> dict[str, object]:
    """Calculate daily technical indicators from OHLC bars.

    The function is provider-independent so the same calculation can be
    reused for QQQ in 1A and INTC/other instruments in 3A.  Realized
    volatility uses 20 log-return observations and sample population standard
    deviation, annualized by ``sqrt(252)``.  ATR14 uses the simple average of
    the last 14 true ranges.  Bollinger 20/2 uses the population standard
    deviation of the last 20 closes.
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

    if len(closes) >= 21:
        log_returns = [
            log(closes[index] / closes[index - 1])
            for index in range(1, len(closes))
            if closes[index] > 0 and closes[index - 1] > 0
        ][-20:]
        if len(log_returns) == 20:
            realized_volatility = pstdev(log_returns) * sqrt(ANNUALIZATION_FACTOR) * 100
            result["realized_volatility_20d"] = _metric(
                value=realized_volatility,
                unit="percent",
                as_of=as_of,
                sample_count=20,
                source=source,
                window=20,
                annualization_factor=ANNUALIZATION_FACTOR,
            )
        else:
            warnings.append("20 日年化实现波动率因有效收盘价不足而未计算。")
    else:
        warnings.append("20 日年化实现波动率需要至少 21 个收盘价。")

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
