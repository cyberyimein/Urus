from __future__ import annotations

from datetime import UTC, date, datetime
from functools import lru_cache
from typing import Any, Protocol

from app.repositories.capital_flows import CapitalFlowRepository
from app.analytics.capital_flow import extract_capital_flow_signal


CAPITAL_FLOW_PROVIDER = "moomoo_openapi"


class CapitalFlowSource(Protocol):
    def capital_flow_day(self, symbol: str, trading_date: date) -> dict[str, Any]: ...


@lru_cache(maxsize=4)
def _calendar(name: str) -> Any:
    # exchange_calendars imports pandas/numpy. Keep that allocation inside the
    # recyclable workflow worker instead of paying it in the API process.
    import exchange_calendars as xcals

    return xcals.get_calendar(name)


def latest_completed_session_date(cutoff_time: datetime, calendar_name: str) -> date:
    """Return the latest exchange session whose official close is not in the future."""

    cutoff_utc = cutoff_time.astimezone(UTC)
    calendar = _calendar(calendar_name)
    candidate = calendar.date_to_session(cutoff_utc.date(), direction="previous")
    close_at = calendar.session_close(candidate).to_pydatetime()
    if close_at.tzinfo is None:
        close_at = close_at.replace(tzinfo=UTC)
    if close_at.astimezone(UTC) > cutoff_utc:
        candidate = calendar.previous_session(candidate)
    return candidate.date()


def is_trading_session_date(target_date: date, calendar_name: str) -> bool:
    """Return whether ``target_date`` is an official exchange session."""

    calendar = _calendar(calendar_name)
    try:
        session = calendar.date_to_session(target_date, direction="none")
    except Exception:
        return False
    return session.date() == target_date


def trading_session_dates(start_date: date, end_date: date, calendar_name: str) -> list[date]:
    """Return official exchange sessions in an inclusive date range."""

    if start_date > end_date:
        return []
    calendar = _calendar(calendar_name)
    sessions = calendar.sessions_in_range(start_date.isoformat(), end_date.isoformat())
    return [session.date() for session in sessions]


def completed_session_dates(
    cutoff_time: datetime, calendar_name: str, *, count: int
) -> list[date]:
    calendar = _calendar(calendar_name)
    latest = latest_completed_session_date(cutoff_time, calendar_name)
    session = calendar.date_to_session(latest)
    dates = [session.date()]
    for _ in range(max(1, count) - 1):
        session = calendar.previous_session(session)
        dates.append(session.date())
    return list(reversed(dates))


class CapitalFlowService:
    """Incrementally cache one completed day and return a bounded hot window."""

    def __init__(
        self,
        repository: CapitalFlowRepository,
        source: CapitalFlowSource,
        *,
        symbols: list[str],
        calendar_name: str,
        cache_days: int = 30,
        projection_days: int = 5,
    ) -> None:
        self.repository = repository
        self.source = source
        self.symbols = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
        self.calendar_name = calendar_name
        self.cache_days = max(5, cache_days)
        self.projection_days = max(1, min(projection_days, 5))

    def collect(self, cutoff_time: datetime) -> dict[str, Any]:
        target_date = latest_completed_session_date(cutoff_time, self.calendar_name)
        result = self._collect_dates([target_date])
        return {
            **result,
            "as_of_date": target_date.isoformat(),
            "fetched_symbols": [
                item["symbol"] for item in result["fetched_observations"]
            ],
            "cache_hit_symbols": [
                item["symbol"] for item in result["cache_hit_observations"]
            ],
        }

    def backfill(self, cutoff_time: datetime, *, days: int = 5) -> dict[str, Any]:
        session_dates = completed_session_dates(
            cutoff_time, self.calendar_name, count=max(1, days)
        )
        result = self._collect_dates(session_dates)
        return {
            **result,
            "schema_version": "urus.capital_flow_backfill.v1",
            "as_of_date": session_dates[-1].isoformat(),
            "session_dates": [item.isoformat() for item in session_dates],
        }

    def _collect_dates(self, trading_dates: list[date]) -> dict[str, Any]:
        fetched_symbols: list[str] = []
        cache_hit_symbols: list[str] = []
        fetched_observations: list[dict[str, str]] = []
        cache_hit_observations: list[dict[str, str]] = []
        warnings: list[str] = []
        for target_date in trading_dates:
            for symbol in self.symbols:
                cached = self.repository.get(
                    provider=CAPITAL_FLOW_PROVIDER,
                    symbol=symbol,
                    trading_date=target_date,
                )
                observation = {
                    "symbol": symbol,
                    "trading_date": target_date.isoformat(),
                }
                if cached is not None:
                    cache_hit_symbols.append(symbol)
                    cache_hit_observations.append(observation)
                    continue
                try:
                    payload = self.source.capital_flow_day(symbol, target_date)
                    self.repository.add_if_missing(
                        {
                            **payload,
                            "provider": CAPITAL_FLOW_PROVIDER,
                            "symbol": symbol,
                            "trading_date": target_date,
                            "period_type": "DAY",
                            "fetched_at": datetime.now(UTC),
                        }
                    )
                    fetched_symbols.append(symbol)
                    fetched_observations.append(observation)
                except Exception as exc:
                    warnings.append(f"{symbol} {target_date.isoformat()} 资金流不可用：{exc}")
        self.repository.session.commit()
        target_date = trading_dates[-1]
        return {
            "schema_version": "urus.capital_flow_cache.v1",
            "provider": CAPITAL_FLOW_PROVIDER,
            "period_type": "DAY",
            "symbols": [
                self._symbol_window(symbol, target_date)
                for symbol in self.symbols
            ],
            "fetched_symbols": fetched_symbols,
            "cache_hit_symbols": cache_hit_symbols,
            "fetched_observations": fetched_observations,
            "cache_hit_observations": cache_hit_observations,
            "quality_status": "ok" if not warnings else "partial",
            "quality_warnings": warnings,
        }

    def _symbol_window(self, symbol: str, target_date: date) -> dict[str, Any]:
        rows = self.repository.recent(
            provider=CAPITAL_FLOW_PROVIDER,
            symbol=symbol,
            through_date=target_date,
            limit=self.cache_days,
        )
        observations = [
            {
                "trading_date": row.trading_date.isoformat(),
                "in_flow": row.in_flow,
                "main_in_flow": row.main_in_flow,
                "super_in_flow": row.super_in_flow,
                "big_in_flow": row.big_in_flow,
                "mid_in_flow": row.mid_in_flow,
                "sml_in_flow": row.sml_in_flow,
                "quality_status": row.quality_status,
            }
            for row in rows
        ]
        return {
            "symbol": symbol,
            "cached_trading_days": len(rows),
            "signal_projection": extract_capital_flow_signal(
                observations, projection_days=self.projection_days
            ),
        }
