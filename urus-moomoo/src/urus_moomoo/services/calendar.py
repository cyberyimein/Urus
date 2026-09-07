from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Protocol


class TradingDaySource(Protocol):
    def trading_days(self, start: date, end: date) -> list[dict[str, Any]]: ...


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


@dataclass
class MoomooTradingCalendar:
    source: TradingDaySource
    session_time_calendar: Any
    cache_path: Path | None = None
    chunk_days: int = 365
    _dates: dict[date, str] = field(default_factory=dict, init=False)
    _covered_start: date | None = field(default=None, init=False)
    _covered_end: date | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.cache_path and self.cache_path.is_file():
            try:
                payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
                self._dates = {date.fromisoformat(key): str(value) for key, value in payload.get("dates", {}).items()}
                if self._dates:
                    self._covered_start, self._covered_end = min(self._dates), max(self._dates)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                self._dates = {}

    @property
    def source_name(self) -> str:
        return "moomoo_openapi"

    def prefetch(self, start: date, end: date) -> None:
        self._ensure_range(start, end)

    def is_session(self, session_date: date) -> bool:
        self._ensure_range(session_date, session_date)
        return session_date in self._dates

    def sessions_in_range(self, start: str | date, end: str | date) -> list[date]:
        start_date, end_date = _as_date(start), _as_date(end)
        if start_date > end_date:
            return []
        self._ensure_range(start_date, end_date)
        return [item for item in sorted(self._dates) if start_date <= item <= end_date]

    def trade_date_type(self, session_date: date) -> str | None:
        self._ensure_range(session_date, session_date)
        return self._dates.get(session_date)

    def session_open(self, session_date: date) -> Any | None:
        return self._session_time(session_date, "session_open")

    def session_close(self, session_date: date) -> Any | None:
        return self._session_time(session_date, "session_close")

    def _session_time(self, session_date: date, method: str) -> Any | None:
        if not self.is_session(session_date) or not self.session_time_calendar.is_session(session_date):
            return None
        return getattr(self.session_time_calendar, method)(session_date)

    def _ensure_range(self, start: date, end: date) -> None:
        if start > end or (self._covered_start is not None and self._covered_end is not None and self._covered_start <= start <= end <= self._covered_end):
            return
        fetch_start = start if self._covered_start is None else min(start, self._covered_start)
        fetch_end = end if self._covered_end is None else max(end, self._covered_end)
        cursor = fetch_start
        while cursor <= fetch_end:
            chunk_end = min(fetch_end, cursor + timedelta(days=max(1, self.chunk_days) - 1))
            for row in self.source.trading_days(cursor, chunk_end):
                if not isinstance(row, dict):
                    continue
                raw = row.get("time", row.get("date", row.get("trading_date")))
                if raw is not None:
                    self._dates[_as_date(raw)] = str(row.get("trade_date_type", row.get("trading_day_type", "WHOLE")))
            cursor = chunk_end + timedelta(days=1)
        self._covered_start, self._covered_end = fetch_start, fetch_end
        self._persist()

    def _persist(self) -> None:
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps({"schema_version": "urus.moomoo_calendar.v1", "dates": {key.isoformat(): value for key, value in self._dates.items()}}, sort_keys=True), encoding="utf-8")


@lru_cache(maxsize=4)
def exchange_session_time_calendar(name: str) -> Any:
    import exchange_calendars as xcals

    return xcals.get_calendar(name)


def build_moomoo_calendar(source: TradingDaySource, exchange_name: str, *, cache_path: Path | None = None) -> MoomooTradingCalendar:
    return MoomooTradingCalendar(source, exchange_session_time_calendar(exchange_name), cache_path=cache_path)
