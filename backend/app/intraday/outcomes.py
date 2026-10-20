"""Pure, fixed-horizon measurements for completed intraday events."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
import math
from typing import Any


_FIVE_MINUTES = timedelta(minutes=5)
_SUPPORTED_HORIZONS = {15, 30, 60}


def measure_outcome(
    event: Mapping[str, Any],
    bars: Sequence[Mapping[str, Any]],
    *,
    horizon_minutes: int,
    market_calendar: str = "XNYS",
) -> dict[str, Any]:
    """Measure one fixed horizon after an event's complete 5-minute cutoff bar.

    This function reads no clock, settings, database, or provider.  Touch order
    is evaluated from bar highs and lows; if both frozen levels are inside one
    bar, their intrabar order remains unknown.
    """

    if (
        isinstance(horizon_minutes, bool)
        or not isinstance(horizon_minutes, int)
        or horizon_minutes not in _SUPPORTED_HORIZONS
    ):
        return _unavailable(
            "invalid_horizon",
            "horizon_minutes must be one of 15, 30, or 60.",
            horizon_minutes=horizon_minutes if isinstance(horizon_minutes, int) else None,
        )

    if not isinstance(event, Mapping):
        return _unavailable("invalid_event", "event must be an object.", horizon_minutes=horizon_minutes)
    if not isinstance(bars, Sequence) or isinstance(bars, (str, bytes)):
        return _unavailable("invalid_bars", "bars must be a sequence of 5-minute bars.", horizon_minutes=horizon_minutes)

    symbol = str(event.get("symbol") or "").strip().upper()
    cutoff = _parse_aware_utc(event.get("evidence_cutoff") or event.get("cutoff"))
    if not symbol or cutoff is None:
        return _unavailable("invalid_event_time", "event requires a symbol and aware cutoff.", horizon_minutes=horizon_minutes)

    try:
        import exchange_calendars as xcals

        calendar = xcals.get_calendar(market_calendar)
    except Exception as exc:
        return _unavailable(
            "invalid_market_calendar",
            str(exc) or "market_calendar is not available.",
            horizon_minutes=horizon_minutes,
        )

    trading_day = _event_trading_date(event, cutoff, calendar)
    if trading_day is None or not _calendar_is_session(calendar, trading_day):
        return _unavailable(
            "event_not_in_session",
            "event cutoff must belong to a valid market session.",
            horizon_minutes=horizon_minutes,
            symbol=symbol,
        )

    expected_ends = [cutoff + _FIVE_MINUTES * index for index in range(horizon_minutes // 5 + 1)]
    try:
        session_open = _parse_aware_utc(calendar.session_open(trading_day))
        session_close = _parse_aware_utc(calendar.session_close(trading_day))
    except Exception as exc:
        return _unavailable(
            "invalid_market_calendar",
            str(exc) or "market session boundaries are unavailable.",
            horizon_minutes=horizon_minutes,
            symbol=symbol,
        )
    if session_open is None or session_close is None:
        return _unavailable(
            "invalid_market_calendar",
            "market session boundaries are not timezone-aware.",
            horizon_minutes=horizon_minutes,
            symbol=symbol,
        )
    if cutoff - _FIVE_MINUTES < session_open or cutoff > session_close:
        return _unavailable(
            "anchor_outside_session",
            "the cutoff anchor bar must be complete inside the market session.",
            horizon_minutes=horizon_minutes,
            symbol=symbol,
        )
    if (cutoff - session_open) % _FIVE_MINUTES != timedelta(0):
        return _unavailable(
            "anchor_off_session_open_grid",
            "the cutoff anchor bar must align to a 5-minute slot from session open.",
            horizon_minutes=horizon_minutes,
            symbol=symbol,
        )
    if expected_ends[-1] > session_close:
        return _unavailable(
            "outcome_crosses_session_close",
            "the fixed horizon extends beyond the market session close.",
            horizon_minutes=horizon_minutes,
            symbol=symbol,
        )

    bars_by_end: dict[datetime, dict[str, Any]] = {}
    for raw_bar in bars:
        if not isinstance(raw_bar, Mapping):
            return _unavailable(
                "invalid_bar",
                "bars contains a non-object row.",
                horizon_minutes=horizon_minutes,
                symbol=symbol,
            )
        bar_symbol = str(raw_bar.get("symbol") or "").strip().upper()
        if bar_symbol != symbol:
            continue

        bar_start = _parse_aware_utc(raw_bar.get("bar_start_at"))
        bar_end = _parse_aware_utc(raw_bar.get("bar_end_at"))
        if bar_start is None or bar_end is None:
            return _unavailable(
                "invalid_bar_time",
                "a bar in the outcome window has an invalid or timezone-naive timestamp.",
                horizon_minutes=horizon_minutes,
                symbol=symbol,
            )
        if bar_end < cutoff or bar_end > expected_ends[-1]:
            continue
        if bar_end not in expected_ends or bar_start != bar_end - _FIVE_MINUTES:
            return _unavailable(
                "invalid_bar_window",
                "bars in the outcome window must be aligned complete 5-minute bars.",
                horizon_minutes=horizon_minutes,
                symbol=symbol,
            )
        if bar_start.astimezone(calendar.tz).date() != trading_day or bar_end.astimezone(calendar.tz).date() != trading_day:
            return _unavailable(
                "cross_session_window",
                "the complete outcome window must remain in the event's market session.",
                horizon_minutes=horizon_minutes,
                symbol=symbol,
            )
        validated = _validated_bar(raw_bar)
        if validated is None:
            return _unavailable(
                "invalid_bar_ohlcv",
                "a bar in the outcome window has invalid OHLCV values or quality.",
                horizon_minutes=horizon_minutes,
                symbol=symbol,
            )
        if bar_end in bars_by_end:
            return _unavailable(
                "duplicate_bar",
                "the outcome window contains more than one bar for the same 5-minute slot.",
                horizon_minutes=horizon_minutes,
                symbol=symbol,
            )
        bars_by_end[bar_end] = {**validated, "bar_start_at": bar_start, "bar_end_at": bar_end}

    missing_ends = [bar_end for bar_end in expected_ends if bar_end not in bars_by_end]
    if missing_ends:
        return _unavailable(
            "outcome_window_unavailable",
            "the complete same-session 5-minute outcome window is missing one or more bars.",
            horizon_minutes=horizon_minutes,
            symbol=symbol,
        )

    anchor = bars_by_end[cutoff]
    anchor_close = anchor["close"]
    future_bars = [bars_by_end[bar_end] for bar_end in expected_ends[1:]]
    terminal_close = future_bars[-1]["close"]
    forward_return_pct = (terminal_close / anchor_close - 1.0) * 100.0
    max_upside_pct = max(0.0, (max(bar["high"] for bar in future_bars) / anchor_close - 1.0) * 100.0)
    max_drawdown_pct = min(0.0, (min(bar["low"] for bar in future_bars) / anchor_close - 1.0) * 100.0)

    direction = str(event.get("direction") or "").strip().lower()
    confirmation = _positive_finite(event.get("confirmation_level"))
    invalidation = _positive_finite(event.get("invalidation_level"))
    first_touch_order = _first_touch_order(
        future_bars,
        direction=direction,
        confirmation=confirmation,
        invalidation=invalidation,
    )

    return {
        "schema_version": "urus.intraday.outcome.v1",
        "available": True,
        "reason_code": None,
        "message": "fixed-horizon outcome measured from the complete cutoff bar.",
        "symbol": symbol,
        "horizon_minutes": horizon_minutes,
        "anchor_cutoff": cutoff.isoformat(),
        "anchor_close": anchor_close,
        "forward_return_pct": forward_return_pct,
        "max_upside_pct": max_upside_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "first_touch_order": first_touch_order,
    }


def _first_touch_order(
    bars: Sequence[Mapping[str, Any]],
    *,
    direction: str,
    confirmation: float | None,
    invalidation: float | None,
) -> str:
    if direction not in {"bullish", "bearish"} or confirmation is None or invalidation is None:
        return "not_applicable"

    for bar in bars:
        if direction == "bullish":
            confirmation_touched = bar["high"] >= confirmation
            invalidation_touched = bar["low"] <= invalidation
        else:
            confirmation_touched = bar["low"] <= confirmation
            invalidation_touched = bar["high"] >= invalidation
        if confirmation_touched and invalidation_touched:
            return "same_bar_unknown"
        if confirmation_touched:
            return "confirmation_first"
        if invalidation_touched:
            return "invalidation_first"
    return "neither"


def _validated_bar(row: Mapping[str, Any]) -> dict[str, float] | None:
    if str(row.get("quality_state") or "").strip().lower() != "ready":
        return None
    values = {field: _finite_number(row.get(field)) for field in ("open", "high", "low", "close", "volume")}
    if any(value is None for value in values.values()):
        return None
    open_price = values["open"]
    high = values["high"]
    low = values["low"]
    close = values["close"]
    volume = values["volume"]
    assert open_price is not None and high is not None and low is not None and close is not None and volume is not None
    if min(open_price, high, low, close) <= 0 or volume < 0:
        return None
    if high < max(open_price, close, low) or low > min(open_price, close, high):
        return None
    if "turnover" in row:
        turnover = _finite_number(row.get("turnover"))
        if turnover is None or turnover < 0:
            return None
    return {key: float(value) for key, value in values.items() if value is not None}


def _event_trading_date(event: Mapping[str, Any], cutoff: datetime, calendar: Any) -> date | None:
    raw_date = event.get("trading_date")
    if raw_date is not None:
        if isinstance(raw_date, date) and not isinstance(raw_date, datetime):
            return raw_date
        try:
            return date.fromisoformat(str(raw_date))
        except ValueError:
            return None
    return cutoff.astimezone(calendar.tz).date()


def _calendar_is_session(calendar: Any, session_date: date) -> bool:
    try:
        return bool(calendar.is_session(session_date))
    except (TypeError, ValueError):
        return False


def _parse_aware_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _positive_finite(value: Any) -> float | None:
    number = _finite_number(value)
    return number if number is not None and number > 0 else None


def _unavailable(
    reason_code: str,
    message: str,
    *,
    horizon_minutes: int | None,
    symbol: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "urus.intraday.outcome.v1",
        "available": False,
        "reason_code": reason_code,
        "message": message,
        "symbol": symbol,
        "horizon_minutes": horizon_minutes,
        "anchor_cutoff": None,
        "anchor_close": None,
        "forward_return_pct": None,
        "max_upside_pct": None,
        "max_drawdown_pct": None,
        "first_touch_order": None,
    }
