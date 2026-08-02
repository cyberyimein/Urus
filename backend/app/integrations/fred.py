from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx


class DailyMacroAdapter(Protocol):
    def daily_context(self, cutoff_time: datetime) -> dict[str, object]: ...


DEFAULT_FRED_SERIES: dict[str, dict[str, str]] = {
    "vix": {
        "series_id": "VIXCLS",
        "label": "Cboe Volatility Index",
        "unit": "index_points",
    },
    "us_2y_yield": {
        "series_id": "DGS2",
        "label": "2-Year Treasury Constant Maturity Rate",
        "unit": "percent",
    },
    "us_10y_yield": {
        "series_id": "DGS10",
        "label": "10-Year Treasury Constant Maturity Rate",
        "unit": "percent",
    },
    "us_30y_yield": {
        "series_id": "DGS30",
        "label": "30-Year Treasury Constant Maturity Rate",
        "unit": "percent",
    },
}


class FredDailyAdapter:
    """Fetch daily macro observations from FRED's public CSV endpoint.

    FRED is intentionally limited to slow daily context in this first slice.
    It is not used as a replacement for real-time ETF, premarket, or breadth
    data, and each observation retains its own source date.
    """

    def __init__(
        self,
        base_url: str = "https://fred.stlouisfed.org/graph/fredgraph.csv",
        *,
        timeout_seconds: float = 10.0,
        lookback_days: int = 30,
        market_timezone: str = "America/New_York",
        client: httpx.Client | None = None,
        series: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.base_url = base_url
        self.lookback_days = max(lookback_days, 7)
        self.market_timezone = market_timezone
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = client is None
        self.series = series or DEFAULT_FRED_SERIES

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def daily_context(self, cutoff_time: datetime) -> dict[str, object]:
        market_date = self._market_date(cutoff_time)
        start_date = market_date - timedelta(days=self.lookback_days)
        collected_at = datetime.now(UTC).isoformat()
        observations: dict[str, dict[str, object]] = {}
        warnings: list[str] = []
        errors: list[str] = []

        for key, definition in self.series.items():
            try:
                observation = self._fetch_latest(
                    definition["series_id"],
                    start_date=start_date,
                    end_date=market_date,
                )
                observations[key] = {
                    "series_id": definition["series_id"],
                    "label": definition["label"],
                    "unit": definition["unit"],
                    "value": observation["value"],
                    "as_of": observation["as_of"],
                    "source": "fred",
                }
            except Exception as exc:
                errors.append(f"{key}({definition['series_id']})：{exc}")

        derived: dict[str, dict[str, object]] = {}
        two_year = observations.get("us_2y_yield")
        ten_year = observations.get("us_10y_yield")
        if two_year and ten_year:
            derived["us_2s10s_spread"] = {
                "label": "10Y-2Y Treasury spread",
                "unit": "percentage_points",
                "value": round(float(ten_year["value"]) - float(two_year["value"]), 4),
                "as_of": min(str(two_year["as_of"]), str(ten_year["as_of"])),
                "source": "derived_from_fred",
            }
        elif self.series.get("us_2y_yield") and self.series.get("us_10y_yield"):
            warnings.append("2s10s 利差因 2Y 或 10Y 日值缺失而未计算。")

        expected = len(self.series)
        if not observations:
            quality_status = "unavailable"
        elif len(observations) < expected:
            quality_status = "partial"
        else:
            quality_status = "ok"
        if errors:
            warnings.extend(errors)

        return {
            "is_mock": False,
            "data_mode": "fred",
            "source": "fred_csv",
            "market_date": market_date.isoformat(),
            "collected_at": collected_at,
            "observations": observations,
            "derived": derived,
            "quality_status": quality_status,
            "quality_warnings": warnings,
            "quality_errors": errors,
        }

    def _fetch_latest(
        self,
        series_id: str,
        *,
        start_date: date,
        end_date: date,
    ) -> dict[str, object]:
        response = self._client.get(
            self.base_url,
            params={
                "id": series_id,
                "cosd": start_date.isoformat(),
                "coed": end_date.isoformat(),
            },
        )
        response.raise_for_status()
        rows = csv.DictReader(io.StringIO(response.text.lstrip("\ufeff")))
        latest: dict[str, object] | None = None
        for row in rows:
            observation_date = str(row.get("observation_date", "")).strip()
            raw_value = str(row.get(series_id, "")).strip()
            if not observation_date or raw_value in {"", ".", "nan", "NaN"}:
                continue
            parsed_date = date.fromisoformat(observation_date[:10])
            if parsed_date > end_date:
                continue
            latest = {"as_of": parsed_date.isoformat(), "value": float(raw_value)}
        if latest is None:
            raise RuntimeError(f"FRED {series_id} 在 {start_date}–{end_date} 没有有效日值")
        return latest

    def _market_date(self, cutoff_time: datetime) -> date:
        value = cutoff_time
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(ZoneInfo(self.market_timezone)).date()
