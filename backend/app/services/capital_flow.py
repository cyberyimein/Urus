from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from typing import Any, Protocol

from app.repositories.capital_flows import CapitalFlowRepository
from app.analytics.capital_flow import extract_capital_flow_signal
from app.services.trading_calendar import calendar_dates


CAPITAL_FLOW_PROVIDER = "moomoo_openapi"


class CapitalFlowSource(Protocol):
    def capital_flow_day(self, symbol: str, trading_date: date) -> dict[str, Any]: ...


@lru_cache(maxsize=4)
def _calendar(name: str) -> Any:
    # exchange_calendars imports pandas/numpy. Keep that allocation inside the
    # collection path instead of paying it during API module import.
    import exchange_calendars as xcals

    return xcals.get_calendar(name)


def _session_dates(calendar: Any, start_date: date, end_date: date) -> list[date]:
    if start_date > end_date:
        return []
    return calendar_dates(
        calendar.sessions_in_range(start_date.isoformat(), end_date.isoformat())
    )


def _session_close_utc(calendar: Any, session_date: date) -> datetime:
    session_label: Any = session_date
    if hasattr(calendar, "date_to_session"):
        session_label = calendar.date_to_session(session_date, direction="none")
    close = calendar.session_close(session_label)
    if close is None:
        raise ValueError(f"{session_date.isoformat()} 缺少交易日收市时间")
    if hasattr(close, "to_pydatetime"):
        close = close.to_pydatetime()
    if not isinstance(close, datetime):
        raise TypeError(f"无法解析 {session_date.isoformat()} 的收市时间：{close!r}")
    if close.tzinfo is None:
        close = close.replace(tzinfo=UTC)
    return close.astimezone(UTC)


def _calendar_instance(calendar_name: str, calendar_source: Any | None) -> Any:
    return calendar_source if calendar_source is not None else _calendar(calendar_name)


def latest_completed_session_date(
    cutoff_time: datetime,
    calendar_name: str,
    *,
    calendar_source: Any | None = None,
) -> date:
    """Return the latest exchange session whose official close is not in the future."""

    cutoff_utc = cutoff_time.astimezone(UTC)
    calendar = _calendar_instance(calendar_name, calendar_source)
    if hasattr(calendar, "date_to_session"):
        candidate = calendar.date_to_session(cutoff_utc.date(), direction="previous")
        candidate_date = calendar_dates([candidate])[0]
    else:
        sessions = _session_dates(
            calendar,
            cutoff_utc.date() - timedelta(days=370),
            cutoff_utc.date(),
        )
        if not sessions:
            raise ValueError(f"{cutoff_utc.date().isoformat()} 之前没有可用交易日")
        candidate_date = sessions[-1]

    if _session_close_utc(calendar, candidate_date) > cutoff_utc:
        previous = _session_dates(
            calendar,
            candidate_date - timedelta(days=370),
            candidate_date - timedelta(days=1),
        )
        if not previous:
            raise ValueError(f"{candidate_date.isoformat()} 之前没有可用交易日")
        candidate_date = previous[-1]
    return candidate_date


def is_trading_session_date(
    target_date: date,
    calendar_name: str,
    *,
    calendar_source: Any | None = None,
) -> bool:
    """Return whether ``target_date`` is an official exchange session."""

    calendar = _calendar_instance(calendar_name, calendar_source)
    if hasattr(calendar, "is_session"):
        try:
            return bool(calendar.is_session(target_date))
        except Exception:
            return False
    try:
        session = calendar.date_to_session(target_date, direction="none")
    except Exception:
        return False
    return calendar_dates([session])[0] == target_date


def trading_session_dates(
    start_date: date,
    end_date: date,
    calendar_name: str,
    *,
    calendar_source: Any | None = None,
) -> list[date]:
    """Return official exchange sessions in an inclusive date range."""

    if start_date > end_date:
        return []
    calendar = _calendar_instance(calendar_name, calendar_source)
    return _session_dates(calendar, start_date, end_date)


def completed_session_dates(
    cutoff_time: datetime,
    calendar_name: str,
    *,
    count: int,
    calendar_source: Any | None = None,
) -> list[date]:
    latest = latest_completed_session_date(
        cutoff_time,
        calendar_name,
        calendar_source=calendar_source,
    )
    lookback_days = max(370, max(1, count) * 3)
    dates = trading_session_dates(
        latest - timedelta(days=lookback_days),
        latest,
        calendar_name,
        calendar_source=calendar_source,
    )
    return dates[-max(1, count) :]


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
        calendar_source: Any | None = None,
    ) -> None:
        self.repository = repository
        self.source = source
        self.symbols = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
        self.calendar_name = calendar_name
        self.cache_days = max(5, cache_days)
        self.projection_days = max(1, min(projection_days, 5))
        self.calendar_source = calendar_source

    def collect(self, cutoff_time: datetime) -> dict[str, Any]:
        target_date = latest_completed_session_date(
            cutoff_time,
            self.calendar_name,
            calendar_source=self.calendar_source,
        )
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
            cutoff_time,
            self.calendar_name,
            count=max(1, days),
            calendar_source=self.calendar_source,
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
