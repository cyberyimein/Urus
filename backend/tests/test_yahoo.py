from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import unquote

import httpx

from app.integrations.macro import FallbackDailyMacroAdapter
from app.integrations.yahoo import YahooDailyAdapter


def _chart_payload(symbol: str, value: float) -> dict[str, object]:
    timestamp = int(datetime(2026, 7, 31, 16, tzinfo=UTC).timestamp())
    return {
        "chart": {
            "result": [
                {
                    "meta": {"symbol": symbol},
                    "timestamp": [timestamp],
                    "indicators": {"quote": [{"close": [value]}]},
                }
            ],
            "error": None,
        }
    }


def test_yahoo_daily_adapter_parses_vix_and_yield_proxies() -> None:
    values = {"^VIX": 17.2, "^TNX": 4.25, "^TYX": 4.8}

    def handler(request: httpx.Request) -> httpx.Response:
        symbol = unquote(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json=_chart_payload(symbol, values[symbol]), request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = YahooDailyAdapter(client=client)
    result = adapter.daily_context(datetime(2026, 8, 3, 12, tzinfo=UTC))
    adapter.close()

    assert result["is_mock"] is False
    assert result["source"] == "yahoo_chart"
    assert result["quality_status"] == "partial"
    assert result["observations"]["vix"]["value"] == 17.2
    assert result["observations"]["us_10y_yield"]["value"] == 4.25
    assert any("2Y" in warning for warning in result["quality_warnings"])
    client.close()


def test_macro_fallback_merges_missing_primary_observations() -> None:
    class Primary:
        def daily_context(self, cutoff_time: datetime) -> dict[str, object]:
            return {
                "is_mock": False,
                "data_mode": "fred",
                "source": "fred_csv",
                "market_date": "2026-08-03",
                "collected_at": "2026-08-03T00:00:00+00:00",
                "observations": {
                    "vix": {"value": 17.2, "as_of": "2026-07-31", "source": "fred"},
                    "us_2y_yield": {"value": 3.75, "as_of": "2026-07-31", "source": "fred"},
                },
                "derived": {},
                "quality_status": "partial",
                "quality_warnings": ["FRED 10Y 缺失"],
                "quality_errors": [],
            }

    class Fallback:
        def daily_context(self, cutoff_time: datetime) -> dict[str, object]:
            return {
                "is_mock": False,
                "data_mode": "yahoo",
                "source": "yahoo_chart",
                "market_date": "2026-08-03",
                "collected_at": "2026-08-03T00:00:00+00:00",
                "observations": {
                    "vix": {"value": 18.1, "as_of": "2026-07-31", "source": "yahoo"},
                    "us_10y_yield": {"value": 4.25, "as_of": "2026-07-31", "source": "yahoo"},
                    "us_30y_yield": {"value": 4.8, "as_of": "2026-07-31", "source": "yahoo"},
                },
                "derived": {},
                "quality_status": "partial",
                "quality_warnings": ["Yahoo 无 2Y"],
                "quality_errors": [],
            }

    result = FallbackDailyMacroAdapter(Primary(), Fallback()).daily_context(
        datetime(2026, 8, 3, 12, tzinfo=UTC)
    )

    assert result["data_mode"] == "mixed"
    assert result["quality_status"] == "ok"
    assert result["yahoo"]["required"] is True
    assert result["observations"]["vix"]["value"] == 18.1
    assert result["cross_checks"]["vix_fred"]["value"] == 17.2
    assert result["derived"]["us_2s10s_spread"]["value"] == 0.5


def test_macro_adapter_collects_yahoo_even_when_fred_is_complete() -> None:
    calls = {"yahoo": 0}

    class CompletePrimary:
        def daily_context(self, cutoff_time: datetime) -> dict[str, object]:
            observations = {
                "vix": {"value": 17.2, "as_of": "2026-07-31", "source": "fred"},
                "us_2y_yield": {"value": 3.75, "as_of": "2026-07-31", "source": "fred"},
                "us_10y_yield": {"value": 4.25, "as_of": "2026-07-31", "source": "fred"},
                "us_30y_yield": {"value": 4.8, "as_of": "2026-07-31", "source": "fred"},
            }
            return {
                "is_mock": False,
                "source": "fred_csv",
                "observations": observations,
                "derived": {},
                "quality_status": "ok",
                "quality_warnings": [],
                "quality_errors": [],
            }

    class RequiredYahoo:
        def daily_context(self, cutoff_time: datetime) -> dict[str, object]:
            calls["yahoo"] += 1
            return {
                "is_mock": False,
                "source": "yahoo_chart",
                "observations": {
                    "vix": {"value": 16.1, "as_of": "2026-07-31", "source": "yahoo"},
                    "us_10y_yield": {"value": 4.3, "as_of": "2026-07-31", "source": "yahoo"},
                    "us_30y_yield": {"value": 4.9, "as_of": "2026-07-31", "source": "yahoo"},
                },
                "derived": {},
                "quality_status": "partial",
                "quality_warnings": ["Yahoo 无 2Y"],
                "quality_errors": [],
            }

    result = FallbackDailyMacroAdapter(CompletePrimary(), RequiredYahoo()).daily_context(
        datetime(2026, 8, 3, 12, tzinfo=UTC)
    )

    assert calls["yahoo"] == 1
    assert result["quality_status"] == "ok"
    assert result["observations"]["vix"]["value"] == 16.1
    assert result["observations"]["us_10y_yield"]["value"] == 4.3
    assert result["observations"]["us_30y_yield"]["value"] == 4.9
    assert result["cross_checks"]["vix_fred"]["value"] == 17.2
