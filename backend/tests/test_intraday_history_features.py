from datetime import UTC, datetime, timedelta
from statistics import median

import exchange_calendars as xcals
import pytest

from app.analytics.technical import calculate_macd_projection
from app.intraday.history_features import enrich_indicator_history


def _bars(day: str, *, volume: float = 100.0):
    calendar = xcals.get_calendar("XNYS")
    start = calendar.session_open(day).to_pydatetime().astimezone(UTC)
    end = calendar.session_close(day).to_pydatetime().astimezone(UTC)
    result = []
    while start < end:
        price = 100.0 + len(result) * 0.13
        result.append({
            "symbol": "SPY", "provider": "moomoo_openapi", "adjustment": "NONE",
            "quality_state": "ready", "bar_start_at": start.isoformat(),
            "bar_end_at": (start + timedelta(minutes=5)).isoformat(),
            "open": price, "high": price + 0.4, "low": price - 0.3,
            "close": price + 0.1, "volume": volume, "turnover": price * volume,
        })
        start += timedelta(minutes=5)
    return result


def _enrich(current, history):
    return enrich_indicator_history(
        current, history, symbol="SPY",
        trading_date=datetime.fromisoformat(current[0]["bar_start_at"]).date(),
        cutoff=datetime.fromisoformat(current[-1]["bar_end_at"]),
        market_calendar="XNYS", adjustment="NONE",
    )


def test_cross_session_macd_uses_history_but_displays_current_session_only():
    history = _bars("2026-10-14") + _bars("2026-10-15")
    current = _bars("2026-10-16")[:5]
    result = _enrich(current, history)
    assert result["features"]["macd_warmup_bars"] == 161
    assert result["features"]["macd_signal_eligible"] is True
    expected = calculate_macd_projection(
        [bar["close"] for bar in history + current],
        dates=[bar["bar_end_at"] for bar in history + current], source="urus_intraday_equities",
    )
    for name in ("histogram", "histogram_delta", "histogram_delta2"):
        assert result["features"]["macd"][name] == expected[name]
    for series in result["features"]["macd"]["series"]:
        assert [point["time"] for point in series["points"]] == [bar["bar_end_at"] for bar in current]


def test_weekend_is_not_a_gap_but_missing_previous_close_is():
    current = _bars("2026-10-19")[:5]
    history = _bars("2026-10-15") + _bars("2026-10-16")
    assert _enrich(current, history)["features"]["macd_warmup_bars"] == 161
    assert _enrich(current, history[:-1])["features"]["macd_warmup_bars"] == 5


def test_rvol_uses_previous_twenty_sessions_and_excludes_current_future_and_other_basis():
    calendar = xcals.get_calendar("XNYS")
    days = list(calendar.sessions_in_range("2026-09-01", "2026-10-15"))[-21:]
    history = []
    for index, day in enumerate(days):
        history.extend(_bars(str(day.date()), volume=100 + index))
    history += _bars("2026-10-16", volume=999999)
    history += _bars("2026-10-19", volume=999999)
    wrong_basis = _bars("2026-10-15", volume=999999)[0]
    wrong_basis["adjustment"] = "QFQ"
    history.append(wrong_basis)
    current = _bars("2026-10-16", volume=221.0)[:1]
    result = _enrich(current, history)
    context = result["features"]["rvol_context"]
    assert context["valid_days"] == 20
    assert context["baseline_median_volume"] == median(range(101, 121))
    assert result["features"]["rvol"] == pytest.approx(2.0)
    assert context["quality_status"] == "ready"


def test_rvol_short_or_invalid_baseline_remains_unavailable():
    history = _bars("2026-10-14") + _bars("2026-10-15")
    result = _enrich(_bars("2026-10-16")[:1], history)
    assert result["features"]["rvol"] is None
    assert result["features"]["rvol_context"]["valid_days"] == 2
    assert result["features"]["rvol_context"]["quality_status"] == "unavailable"


def test_half_day_close_bridges_to_next_real_session_without_fake_bars():
    history = _bars("2026-11-25") + _bars("2026-11-27")
    assert len(history) == 120
    result = _enrich(_bars("2026-11-30")[:1], history)
    assert result["features"]["macd_warmup_bars"] == 121


def test_invalid_history_and_current_gaps_never_warm_up_macd():
    history = _bars("2026-10-14") + _bars("2026-10-15")
    history[-1]["low"] = history[-1]["high"] + 1
    current = _bars("2026-10-16")[:8]
    assert _enrich(current, history)["features"]["macd_warmup_bars"] == 8
    history = _bars("2026-10-14") + _bars("2026-10-15")
    current.pop(2)
    assert _enrich(current, history)["features"]["macd_warmup_bars"] == 5
