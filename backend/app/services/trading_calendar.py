from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any, Protocol


class TradingDaySource(Protocol):
    """Provider boundary for a market's trading-date endpoint."""

    def trading_days(self, start: date, end: date) -> list[dict[str, Any]]: ...


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        candidate = value.date()
        if isinstance(candidate, date):
            return candidate
    return date.fromisoformat(str(value)[:10])


@dataclass
class MoomooTradingCalendar:
    """Moomoo trading dates with a separate session-time calendar.

    Moomoo's trading-date endpoint is authoritative for whether a US date is
    present in the provider's calendar. It does not expose session open/close
    timestamps, so those timestamps are deliberately delegated to the
    configured exchange calendar. A date must be present in both sources
    before a session time is returned; this keeps a calendar disagreement
    fail-closed for scheduling.
    """

    source: TradingDaySource
    session_time_calendar: Any
    chunk_days: int = 365
    _dates: dict[date, str] = field(default_factory=dict, init=False)
    _covered_start: date | None = field(default=None, init=False)
    _covered_end: date | None = field(default=None, init=False)

    @property
    def source_name(self) -> str:
        return "moomoo_openapi"

    def prefetch(self, start: date, end: date) -> None:
        self._ensure_range(start, end)

    def is_session(self, session_date: date) -> bool:
        self._ensure_range(session_date, session_date)
        return session_date in self._dates

    def sessions_in_range(self, start: str | date, end: str | date) -> list[date]:
        start_date = _as_date(start)
        end_date = _as_date(end)
        if start_date > end_date:
            return []
        self._ensure_range(start_date, end_date)
        return [
            session_date
            for session_date in sorted(self._dates)
            if start_date <= session_date <= end_date
        ]

    def trade_date_type(self, session_date: date) -> str | None:
        self._ensure_range(session_date, session_date)
        return self._dates.get(session_date)

    def session_open(self, session_date: date) -> Any | None:
        if not self.is_session(session_date):
            return None
        if not self.session_time_calendar.is_session(session_date):
            return None
        return self.session_time_calendar.session_open(session_date)

    def session_close(self, session_date: date) -> Any | None:
        if not self.is_session(session_date):
            return None
        if not self.session_time_calendar.is_session(session_date):
            return None
        return self.session_time_calendar.session_close(session_date)

    def _ensure_range(self, start: date, end: date) -> None:
        if start > end:
            return
        if (
            self._covered_start is not None
            and self._covered_end is not None
            and self._covered_start <= start
            and end <= self._covered_end
        ):
            return

        fetch_start = start if self._covered_start is None else min(start, self._covered_start)
        fetch_end = end if self._covered_end is None else max(end, self._covered_end)
        cursor = fetch_start
        chunk_size = max(1, int(self.chunk_days))
        while cursor <= fetch_end:
            chunk_end = min(fetch_end, cursor + timedelta(days=chunk_size - 1))
            for row in self.source.trading_days(cursor, chunk_end):
                raw_date = row.get("time", row.get("date", row.get("trading_date")))
                if raw_date is None:
                    continue
                session_date = _as_date(raw_date)
                self._dates[session_date] = str(
                    row.get("trade_date_type", row.get("trading_day_type", "WHOLE"))
                )
            cursor = chunk_end + timedelta(days=1)
        self._covered_start = fetch_start
        self._covered_end = fetch_end


def calendar_dates(values: Iterable[Any]) -> list[date]:
    """Normalize exchange-calendar or provider-calendar session labels."""

    return sorted({_as_date(value) for value in values})


@lru_cache(maxsize=4)
def exchange_session_time_calendar(name: str) -> Any:
    """Load the local calendar used only for session open/close timestamps."""

    import exchange_calendars as xcals

    return xcals.get_calendar(name)


def build_moomoo_calendar(source: TradingDaySource, exchange_name: str) -> MoomooTradingCalendar:
    return MoomooTradingCalendar(
        source=source,
        session_time_calendar=exchange_session_time_calendar(exchange_name),
    )
