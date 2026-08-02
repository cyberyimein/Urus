from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx


class YahooDailyAdapterProtocol(Protocol):
    def daily_context(self, cutoff_time: datetime) -> dict[str, object]: ...


DEFAULT_YAHOO_SERIES: dict[str, dict[str, str]] = {
    "vix": {
        "symbol": "^VIX",
        "label": "Cboe Volatility Index",
        "unit": "index_points",
    },
    "us_10y_yield": {
        "symbol": "^TNX",
        "label": "Yahoo/Cboe 10-Year Treasury Yield Index",
        "unit": "percent",
    },
    "us_30y_yield": {
        "symbol": "^TYX",
        "label": "Yahoo/Cboe 30-Year Treasury Yield Index",
        "unit": "percent",
    },
}


class YahooDailyAdapter:
    """Low-frequency Yahoo Finance chart observations for every 1A run.

    Yahoo's source label is retained in every observation. It intentionally
    does not claim to provide the official 2-year Treasury constant-maturity
    series; FRED remains the selected source only for that field.
    """

    def __init__(
        self,
        base_url: str = "https://query2.finance.yahoo.com/v8/finance/chart",
        *,
        timeout_seconds: float = 10.0,
        lookback_days: int = 30,
        market_timezone: str = "America/New_York",
        client: httpx.Client | None = None,
        series: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.lookback_days = max(lookback_days, 7)
        self.market_timezone = market_timezone
        self._client = client or httpx.Client(
            timeout=timeout_seconds,
            headers={"User-Agent": "Urus/0.1 stage-1a market fallback"},
        )
        self._owns_client = client is None
        self.series = series or DEFAULT_YAHOO_SERIES

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def daily_context(self, cutoff_time: datetime) -> dict[str, object]:
        market_date = self._market_date(cutoff_time)
        start_date = market_date - timedelta(days=self.lookback_days)
        collected_at = datetime.now(UTC).isoformat()
        observations: dict[str, dict[str, object]] = {}
        warnings = ["Yahoo 日频源未提供 2Y Treasury Constant Maturity Rate；该字段仍依赖 FRED。"]
        errors: list[str] = []

        for key, definition in self.series.items():
            try:
                observation = self._fetch_latest(
                    definition["symbol"],
                    start_date=start_date,
                    end_date=market_date,
                )
                observations[key] = {
                    "series_id": definition["symbol"],
                    "label": definition["label"],
                    "unit": definition["unit"],
                    "value": observation["value"],
                    "as_of": observation["as_of"],
                    "source": "yahoo_chart",
                }
            except Exception as exc:
                errors.append(f"{key}({definition['symbol']})：{exc}")

        if errors:
            warnings.extend(errors)
        if not observations:
            quality_status = "unavailable"
        else:
            quality_status = "partial"
        return {
            "is_mock": False,
            "data_mode": "yahoo",
            "source": "yahoo_chart",
            "market_date": market_date.isoformat(),
            "collected_at": collected_at,
            "observations": observations,
            "derived": {},
            "quality_status": quality_status,
            "quality_warnings": warnings,
            "quality_errors": errors,
        }

    def _fetch_latest(
        self,
        symbol: str,
        *,
        start_date: date,
        end_date: date,
    ) -> dict[str, object]:
        response = self._client.get(
            f"{self.base_url}/{quote(symbol, safe='')}",
            params={
                "range": "1mo",
                "interval": "1d",
                "includePrePost": "false",
                "events": "div,splits",
            },
        )
        response.raise_for_status()
        payload = response.json()
        chart = payload.get("chart", {})
        if chart.get("error"):
            raise RuntimeError(str(chart["error"]))
        results = chart.get("result") or []
        if not results:
            raise RuntimeError("Yahoo chart 返回空结果")
        result = results[0]
        timestamps = result.get("timestamp") or []
        quote_rows = (result.get("indicators", {}).get("quote") or [{}])[0]
        closes = quote_rows.get("close") or []
        latest: dict[str, object] | None = None
        timezone = ZoneInfo(self.market_timezone)
        for timestamp, close in zip(timestamps, closes):
            if close is None:
                continue
            observation_date = datetime.fromtimestamp(int(timestamp), UTC).astimezone(timezone).date()
            if observation_date < start_date or observation_date > end_date:
                continue
            latest = {"as_of": observation_date.isoformat(), "value": float(close)}
        if latest is None:
            raise RuntimeError(f"Yahoo {symbol} 在 {start_date}–{end_date} 没有有效日值")
        return latest

    def _market_date(self, cutoff_time: datetime) -> date:
        value = cutoff_time
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(ZoneInfo(self.market_timezone)).date()
