from __future__ import annotations

from datetime import UTC, date, datetime

from app.services.trading_calendar import MoomooTradingCalendar, calendar_dates
from scripts.schedule_intraday_options_collection import session_bounds


class FakeTradingDaySource:
    def __init__(self, dates: list[date]) -> None:
        self.dates = set(dates)
        self.calls: list[tuple[date, date]] = []

    def trading_days(self, start: date, end: date) -> list[dict[str, object]]:
        self.calls.append((start, end))
        return [
            {"time": value.isoformat(), "trade_date_type": "WHOLE"}
            for value in sorted(self.dates)
            if start <= value <= end
        ]


class FakeSessionTimeCalendar:
    def is_session(self, value: date) -> bool:
        # Simulate a local exchange calendar that disagrees about one date.
        return value.weekday() < 5 and value != date(2026, 7, 3)

    def session_open(self, value: date) -> datetime:
        return datetime(value.year, value.month, value.day, 14, 30, tzinfo=UTC)

    def session_close(self, value: date) -> datetime:
        return datetime(value.year, value.month, value.day, 21, 0, tzinfo=UTC)


def test_moomoo_dates_are_authoritative_and_cached() -> None:
    source = FakeTradingDaySource(
        [date(2026, 7, 3), date(2026, 7, 6), date(2026, 7, 7)]
    )
    calendar = MoomooTradingCalendar(source, FakeSessionTimeCalendar())

    calendar.prefetch(date(2026, 7, 3), date(2026, 7, 7))

    assert calendar.sessions_in_range("2026-07-03", "2026-07-07") == [
        date(2026, 7, 3),
        date(2026, 7, 6),
        date(2026, 7, 7),
    ]
    assert calendar.is_session(date(2026, 7, 7)) is True
    assert calendar.is_session(date(2026, 7, 8)) is False
    assert len(source.calls) == 2


def test_session_times_fail_closed_when_provider_and_exchange_disagree() -> None:
    source = FakeTradingDaySource([date(2026, 7, 3), date(2026, 7, 6)])
    calendar = MoomooTradingCalendar(source, FakeSessionTimeCalendar())

    assert calendar.session_close(date(2026, 7, 3)) is None
    assert calendar.session_open(date(2026, 7, 6)) == datetime(
        2026, 7, 6, 14, 30, tzinfo=UTC
    )
    assert session_bounds(calendar, date(2026, 7, 6)) == (
        datetime(2026, 7, 6, 14, 30, tzinfo=UTC),
        datetime(2026, 7, 6, 21, 0, tzinfo=UTC),
    )


def test_calendar_dates_normalizes_datetime_values() -> None:
    assert calendar_dates(
        [date(2026, 7, 6), datetime(2026, 7, 5, 23, 0, tzinfo=UTC)]
    ) == [date(2026, 7, 5), date(2026, 7, 6)]
