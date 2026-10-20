"""Pure, causal detectors for intraday price structure strategies."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
import math
from typing import Any

from app.intraday.equities import _wilder_atr_series


_FIVE_MINUTES = timedelta(minutes=5)


def previous_atr_and_buffer(
    bars: Sequence[Mapping[str, Any]],
    *,
    atr_buffer_multiple: float,
    price_buffer_bps: float,
) -> dict[str, float] | None:
    """Return the prior-bar ATR and P4-compatible absolute price buffer."""

    if len(bars) < 2:
        return None
    atr_series = _wilder_atr_series(bars, period=14)
    previous_atr = atr_series[-2] if len(atr_series) >= 2 else None
    previous_close = _number(bars[-2].get("close"))
    if previous_atr is None or previous_atr <= 0 or previous_close is None or previous_close <= 0:
        return None
    buffer = max(
        float(atr_buffer_multiple) * previous_atr,
        float(price_buffer_bps) / 10_000.0 * previous_close,
    )
    if not math.isfinite(buffer):
        return None
    return {"previous_atr14": float(previous_atr), "buffer": buffer}


def detect_opening_range_breakout(
    bars: Sequence[Mapping[str, Any]],
    *,
    session_open: datetime | None,
    previous_atr14: float | None,
    buffer: float | None,
) -> dict[str, Any]:
    """Detect a close outside the frozen first-three-bar opening range."""

    if session_open is None:
        return _unavailable("session_unavailable", "无法确定交易时段开盘时间。")
    if session_open.tzinfo is None:
        return _unavailable("session_unavailable", "交易时段开盘时间缺少时区。")
    if len(bars) < 3:
        return _unavailable("opening_range_incomplete", "开盘区间需要三根完整的 5 分钟 bar。")

    open_utc = session_open.astimezone(UTC)
    expected_starts = [open_utc + index * _FIVE_MINUTES for index in range(3)]
    bars_by_start: dict[datetime, Mapping[str, Any]] = {}
    for bar in bars:
        start = _time(bar.get("bar_start_at"))
        if start is None:
            continue
        if start in bars_by_start:
            return _unavailable("opening_range_conflict", "开盘区间包含重复的 bar 时间。")
        bars_by_start[start] = bar
    opening_bars = [bars_by_start.get(start) for start in expected_starts]
    if any(bar is None for bar in opening_bars):
        return _unavailable("opening_range_incomplete", "开盘 09:30–09:45 区间覆盖不完整。")

    current_bar = bars[-1]
    current_start = _time(current_bar.get("bar_start_at"))
    current_close = _number(current_bar.get("close"))
    if current_start is None or current_close is None:
        return _unavailable("current_bar_unavailable", "当前完整 bar 缺少有效时间或收盘价。")
    opening_end = open_utc + 3 * _FIVE_MINUTES
    if current_start < opening_end:
        return _unavailable("opening_range_not_closed", "开盘区间形成前不评估 ORB 突破。")
    if previous_atr14 is None or previous_atr14 <= 0 or buffer is None:
        return _unavailable("atr_unavailable", "ORB 需要有效的前一根 Wilder ATR14 与价格缓冲。")

    opening_high = max(float(bar["high"]) for bar in opening_bars if bar is not None)
    opening_low = min(float(bar["low"]) for bar in opening_bars if bar is not None)
    if _strictly_greater(current_close, opening_high + buffer):
        direction = "bullish"
        confirmation_level = opening_high + buffer
        invalidation_level = opening_low - buffer
    elif _strictly_less(current_close, opening_low - buffer):
        direction = "bearish"
        confirmation_level = opening_low - buffer
        invalidation_level = opening_high + buffer
    else:
        return _available(None, "opening_range_not_broken", "当前收盘仍在冻结开盘区间内。")

    return _available(
        {
            "setup_type": "orb_breakout",
            "direction": direction,
            "confirmation_level": confirmation_level,
            "invalidation_level": invalidation_level,
            "evidence": {
                "opening_range_high": opening_high,
                "opening_range_low": opening_low,
                "opening_range_start": _iso(open_utc),
                "opening_range_end": _iso(opening_end),
                "previous_atr14": previous_atr14,
                "buffer": buffer,
                "breakout_close": current_close,
            },
        },
        "orb_breakout",
        "完整 bar 收于冻结的 09:30–09:45 开盘区间之外。",
    )


def detect_range_breakout(
    bars: Sequence[Mapping[str, Any]],
    *,
    previous_atr14: float | None,
    buffer: float | None,
    lookback_bars: int = 6,
    maximum_range_atr: float = 2.5,
) -> dict[str, Any]:
    """Detect a compressed six-bar range breakout with range expansion."""

    if len(bars) < lookback_bars + 1:
        return _unavailable("range_history_incomplete", "区间突破需要当前 bar 前六根完整 bar。")
    if previous_atr14 is None or previous_atr14 <= 0 or buffer is None:
        return _unavailable("atr_unavailable", "区间突破需要有效的前一根 Wilder ATR14 与价格缓冲。")

    prior_bars = list(bars[-(lookback_bars + 1) : -1])
    current_bar = bars[-1]
    current_close = _number(current_bar.get("close"))
    current_high = _number(current_bar.get("high"))
    current_low = _number(current_bar.get("low"))
    previous_close = _number(bars[-2].get("close"))
    if None in (current_close, current_high, current_low, previous_close):
        return _unavailable("current_bar_unavailable", "区间突破当前 bar 的 OHLC 输入不完整。")

    range_high = max(float(bar["high"]) for bar in prior_bars)
    range_low = min(float(bar["low"]) for bar in prior_bars)
    range_atr = (range_high - range_low) / previous_atr14
    if range_atr > maximum_range_atr and not math.isclose(
        range_atr, maximum_range_atr, rel_tol=1e-12, abs_tol=1e-12
    ):
        return _available(None, "range_not_compressed", "前六根区间未达到压缩范围阈值。")

    true_range = max(
        float(current_high) - float(current_low),
        abs(float(current_high) - float(previous_close)),
        abs(float(current_low) - float(previous_close)),
    )
    if not _strictly_greater(true_range, previous_atr14):
        return _available(None, "range_not_expanded", "当前真实波幅未超过前一根 ATR14。")

    if _strictly_greater(float(current_close), range_high + buffer):
        direction = "bullish"
        confirmation_level = range_high + buffer
        invalidation_level = range_low - buffer
    elif _strictly_less(float(current_close), range_low - buffer):
        direction = "bearish"
        confirmation_level = range_low - buffer
        invalidation_level = range_high + buffer
    else:
        return _available(None, "range_not_broken", "当前收盘尚未突破压缩区间。")

    return _available(
        {
            "setup_type": "range_breakout",
            "direction": direction,
            "confirmation_level": confirmation_level,
            "invalidation_level": invalidation_level,
            "evidence": {
                "range_high": range_high,
                "range_low": range_low,
                "range_start": _iso(_time(prior_bars[0].get("bar_start_at"))),
                "range_end": _iso(_time(prior_bars[-1].get("bar_end_at"))),
                "range_atr": range_atr,
                "maximum_range_atr": maximum_range_atr,
                "previous_atr14": previous_atr14,
                "current_true_range": true_range,
                "buffer": buffer,
                "breakout_close": current_close,
            },
        },
        "range_breakout",
        "前六根压缩区间之后出现波幅扩张，并收于冻结边界之外。",
    )


def detect_reversal_candidate(
    bars: Sequence[Mapping[str, Any]],
    *,
    previous_atr14: float | None,
    buffer: float | None,
    ma5_values: Mapping[str, Any],
    lookback_bars: int = 6,
    minimum_swing_atr: float = 2.0,
) -> dict[str, Any]:
    """Detect an ordered six-bar extreme followed by a MA5 reclaim/loss."""

    if len(bars) < lookback_bars + 1:
        return _unavailable("reversal_history_incomplete", "A/V 结构需要当前 bar 前六根完整 bar。")
    if previous_atr14 is None or previous_atr14 <= 0 or buffer is None:
        return _unavailable("atr_unavailable", "A/V 结构需要有效的前一根 Wilder ATR14 与价格缓冲。")
    if ma5_values.get("available") is not True:
        return _unavailable("ma5_unavailable", "A/V 候选需要 MA5 与连续完整 bar。")

    prior_bars = list(bars[-(lookback_bars + 1) : -1])
    current_close = _number(bars[-1].get("close"))
    previous_close = _number(ma5_values.get("previous_close"))
    current_ma5 = _number(ma5_values.get("ma5"))
    previous_ma5 = _number(ma5_values.get("previous_ma5"))
    if None in (current_close, previous_close, current_ma5, previous_ma5):
        return _unavailable("ma5_unavailable", "A/V 候选的当前或前一 MA5 不完整。")

    high_index = max(range(len(prior_bars)), key=lambda index: float(prior_bars[index]["high"]))
    low_index = min(range(len(prior_bars)), key=lambda index: float(prior_bars[index]["low"]))
    reference_high = float(prior_bars[high_index]["high"])
    reference_low = float(prior_bars[low_index]["low"])
    swing_size = reference_high - reference_low
    if swing_size < minimum_swing_atr * previous_atr14 and not math.isclose(
        swing_size, minimum_swing_atr * previous_atr14, rel_tol=1e-12, abs_tol=1e-12
    ):
        return _available(None, "reversal_swing_too_small", "六根结构的高低幅度不足两倍前置 ATR14。")

    if high_index < low_index:
        setup_type = "v_reversal"
        direction = "bullish"
        extreme_index = low_index
        reverse_bar = prior_bars[extreme_index + 1] if extreme_index + 1 < len(prior_bars) else None
        is_reverse_bar = (
            reverse_bar is not None
            and float(reverse_bar["close"]) > float(reverse_bar["open"])
        )
        ma5_crossed = previous_close <= previous_ma5 and current_close > current_ma5 + buffer
        confirmation_base = _number(reverse_bar.get("high")) if reverse_bar else None
        invalidation_level = reference_low - buffer
    elif low_index < high_index:
        setup_type = "a_reversal"
        direction = "bearish"
        extreme_index = high_index
        reverse_bar = prior_bars[extreme_index + 1] if extreme_index + 1 < len(prior_bars) else None
        is_reverse_bar = (
            reverse_bar is not None
            and float(reverse_bar["close"]) < float(reverse_bar["open"])
        )
        ma5_crossed = previous_close >= previous_ma5 and current_close < current_ma5 - buffer
        confirmation_base = _number(reverse_bar.get("low")) if reverse_bar else None
        invalidation_level = reference_high + buffer
    else:
        return _unavailable("reversal_extrema_order_unavailable", "六根结构无法区分先后高低点。")

    if not is_reverse_bar or confirmation_base is None:
        return _unavailable("reversal_bar_unavailable", "极值之后紧接的完整 bar 尚未反向。")
    if not ma5_crossed:
        return _available(None, "ma5_reclaim_unavailable", "当前收盘尚未完成方向对应的 MA5 收复／失守。")

    confirmation_level = (
        confirmation_base + buffer if direction == "bullish" else confirmation_base - buffer
    )
    return _available(
        {
            "setup_type": setup_type,
            "direction": direction,
            "confirmation_level": confirmation_level,
            "invalidation_level": invalidation_level,
            "evidence": {
                "reference_high": reference_high,
                "reference_high_at": _iso(_time(prior_bars[high_index].get("bar_start_at"))),
                "reference_low": reference_low,
                "reference_low_at": _iso(_time(prior_bars[low_index].get("bar_start_at"))),
                "swing_size": swing_size,
                "minimum_swing_atr": minimum_swing_atr,
                "previous_atr14": previous_atr14,
                "buffer": buffer,
                "reverse_bar_start": _iso(_time(reverse_bar.get("bar_start_at"))),
                "reverse_bar_end": _iso(_time(reverse_bar.get("bar_end_at"))),
                "confirmation_base": confirmation_base,
                "previous_close": previous_close,
                "close": current_close,
                "previous_ma5": previous_ma5,
                "ma5": current_ma5,
            },
        },
        setup_type,
        "六根有序极值结构、反向 bar 与 MA5 收复／失守均已出现。",
    )


def setup_exhaustion_streak(
    bars: Sequence[Mapping[str, Any]],
    *,
    comparison_bars_ago: int = 4,
) -> dict[str, Any]:
    """Return the current uninterrupted close-vs-four-bars-ago streak."""

    upward_count = 0
    downward_count = 0
    upward_start: datetime | None = None
    downward_start: datetime | None = None
    for index in range(comparison_bars_ago, len(bars)):
        close = _number(bars[index].get("close"))
        prior_close = _number(bars[index - comparison_bars_ago].get("close"))
        if close is None or prior_close is None:
            upward_count = 0
            downward_count = 0
            upward_start = None
            downward_start = None
            continue
        if close > prior_close:
            if upward_count == 0:
                upward_start = _time(bars[index - comparison_bars_ago].get("bar_start_at"))
            upward_count += 1
            downward_count = 0
            downward_start = None
        elif close < prior_close:
            if downward_count == 0:
                downward_start = _time(bars[index - comparison_bars_ago].get("bar_start_at"))
            downward_count += 1
            upward_count = 0
            upward_start = None
        else:
            upward_count = 0
            downward_count = 0
            upward_start = None
            downward_start = None
    return {
        "bullish": {"count": upward_count, "started_at": upward_start},
        "bearish": {"count": downward_count, "started_at": downward_start},
    }


def _available(signal: dict[str, Any] | None, reason_code: str, message: str) -> dict[str, Any]:
    return {
        "available": True,
        "reason_code": reason_code,
        "message": message,
        "signal": signal,
    }


def _unavailable(reason_code: str, message: str) -> dict[str, Any]:
    return {
        "available": False,
        "reason_code": reason_code,
        "message": message,
        "signal": None,
    }


def _number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _time(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _strictly_greater(value: float, boundary: float) -> bool:
    return value > boundary and not math.isclose(value, boundary, rel_tol=1e-12, abs_tol=1e-12)


def _strictly_less(value: float, boundary: float) -> bool:
    return value < boundary and not math.isclose(value, boundary, rel_tol=1e-12, abs_tol=1e-12)
