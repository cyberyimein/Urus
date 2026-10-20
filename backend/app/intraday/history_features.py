"""Bounded indicator history from validated, session-aligned five-minute bars."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from statistics import fmean, median
from typing import Any
from zoneinfo import ZoneInfo

from app.analytics.technical import calculate_macd_projection
from app.intraday.equities import _aware_utc, _validated_minute_values, _wilder_atr_series


def enrich_indicator_history(
    current_bars: Sequence[Mapping[str, Any]],
    historical_bars: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    trading_date: date,
    cutoff: datetime,
    market_calendar: str = "XNYS",
    adjustment: str = "NONE",
) -> dict[str, Any]:
    import exchange_calendars as xcals

    calendar = xcals.get_calendar(market_calendar)
    days = list(calendar.sessions_in_range(trading_date - timedelta(days=65), trading_date))[-21:]
    sessions = {
        day.date(): (
            calendar.session_open(day).to_pydatetime().astimezone(UTC),
            calendar.session_close(day).to_pydatetime().astimezone(UTC),
        )
        for day in days
    }
    day_order = {day: index for index, day in enumerate(sessions)}
    cutoff = _aware_utc(cutoff, "cutoff")
    by_key: dict[datetime, dict[str, Any]] = {}
    conflicting: set[datetime] = set()
    candidates = [(row, False) for row in historical_bars]
    candidates.extend((row, True) for row in current_bars)
    for row, is_current in candidates:
        if str(row.get("symbol", symbol)).upper() != symbol:
            continue
        if row.get("provider", "moomoo_openapi") != "moomoo_openapi":
            continue
        if row.get("adjustment", adjustment) != adjustment:
            continue
        try:
            start = _aware_utc(row.get("bar_start_at"), "bar_start_at")
            end = _aware_utc(row.get("bar_end_at"), "bar_end_at")
            session_date = start.astimezone(ZoneInfo("America/New_York")).date()
            opened, closed = sessions[session_date]
            values, valid = _validated_minute_values(row)
        except (KeyError, TypeError, ValueError):
            continue
        if not valid or row.get("quality_state", "ready") != "ready":
            continue
        if end - start != timedelta(minutes=5) or end > min(closed, cutoff):
            continue
        if start < opened or (start - opened).total_seconds() % 300:
            continue
        # A historical cache must never contribute current-day or future rows.
        if (is_current and session_date != trading_date) or (
            not is_current and session_date >= trading_date
        ):
            continue
        normalized = {
            **values, "symbol": symbol, "bar_start_at": start.isoformat(),
            "bar_end_at": end.isoformat(), "session_date": session_date,
        }
        existing = by_key.get(end)
        if existing is not None and existing != normalized:
            conflicting.add(end)
        by_key[end] = normalized
    clean = [row for key, row in sorted(by_key.items()) if key not in conflicting]
    current = [row for row in clean if row["session_date"] == trading_date]
    history = [row for row in clean if row["session_date"] < trading_date]
    suffix: list[dict[str, Any]] = []
    if current:
        suffix = [clean[-1]]
        for row in reversed(clean[:-1]):
            following = suffix[0]
            left_day, right_day = row["session_date"], following["session_date"]
            if left_day == right_day:
                adjacent = row["bar_end_at"] == following["bar_start_at"]
            else:
                adjacent = (
                    day_order[right_day] == day_order[left_day] + 1
                    and _aware_utc(row["bar_end_at"], "end") == sessions[left_day][1]
                    and _aware_utc(following["bar_start_at"], "start") == sessions[right_day][0]
                )
            if not adjacent:
                break
            suffix.insert(0, row)

    closes = [row["close"] for row in suffix]
    macd = calculate_macd_projection(
        closes, dates=[row["bar_end_at"] for row in suffix], source="urus_intraday_equities",
    )
    current_times = {row["bar_end_at"] for row in current}
    for series in macd.get("series", []):
        series["points"] = [point for point in series["points"] if point["time"] in current_times]
        if "markers" in series:
            series["markers"] = [point for point in series["markers"] if point["time"] in current_times]
    atr = _wilder_atr_series(suffix, period=14)
    features = {
        "macd": macd, "macd_warmup_bars": len(closes),
        "macd_signal_eligible": len(closes) >= 100,
        "ma5": fmean(closes[-5:]) if len(closes) >= 5 else None,
        "ma5_slope": fmean(closes[-5:]) - fmean(closes[-6:-1]) if len(closes) >= 6 else None,
        "ma5_distance_pct": (closes[-1] / fmean(closes[-5:]) - 1) * 100 if len(closes) >= 5 else None,
        "atr14": atr[-1] if atr else None,
        "rvol": None,
    }
    reasons = {}
    if len(closes) < 100:
        reasons["macd_warmup"] = f"MACD 需要 100 根连续完整 bar；目前 {len(closes)} 根。"
    context = {
        "valid_days": 0, "baseline_median_volume": None,
        "as_of_session_date": None, "slot_start": None, "quality_status": "unavailable",
    }
    if current:
        latest = current[-1]
        latest_start = _aware_utc(latest["bar_start_at"], "start")
        slot = latest_start - sessions[trading_date][0]
        samples = {
            row["session_date"]: row["volume"] for row in history
            if _aware_utc(row["bar_start_at"], "start") - sessions[row["session_date"]][0] == slot
        }
        baseline = float(median(samples.values())) if samples else None
        ready = len(samples) >= 10 and baseline is not None and baseline > 0
        context.update({
            "valid_days": len(samples), "baseline_median_volume": baseline,
            "as_of_session_date": max(samples).isoformat() if samples else None,
            "slot_start": latest_start.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M"),
            "quality_status": "ready" if ready else "unavailable",
        })
        if ready:
            features["rvol"] = latest["volume"] / baseline
    features["rvol_context"] = context
    if features["rvol"] is None:
        reasons["rvol"] = "RVOL 需要至少 10 个有效历史交易日的同时段量能及正值中位数。"
    return {"features": features, "reasons": reasons}
