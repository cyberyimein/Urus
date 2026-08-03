from __future__ import annotations

from datetime import UTC, datetime

import httpx

from app.integrations.fred import FredDailyAdapter


def test_fred_daily_adapter_parses_observations_and_derives_2s10s() -> None:
    payloads = {
        "VIXCLS": "observation_date,VIXCLS\n2026-08-01,17.2\n2026-08-02,.\n",
        "DGS2": "observation_date,DGS2\n2026-08-01,3.75\n",
        "DGS10": "observation_date,DGS10\n2026-08-01,4.25\n",
        "DGS30": "observation_date,DGS30\n2026-08-01,4.80\n",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        series_id = request.url.params["id"]
        return httpx.Response(200, text=payloads[series_id], request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = FredDailyAdapter(client=client)

    result = adapter.daily_context(datetime(2026, 8, 2, 12, tzinfo=UTC))
    adapter.close()

    assert result["is_mock"] is False
    assert result["quality_status"] == "ok"
    assert result["observations"]["vix"]["value"] == 17.2
    assert result["derived"]["us_2s10s_spread"]["value"] == 0.5
    assert result["derived"]["us_2s10s_spread"]["as_of"] == "2026-08-01"
    client.close()


def test_fred_daily_adapter_marks_partial_source_without_faking_missing_values() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        series_id = request.url.params["id"]
        if series_id == "VIXCLS":
            return httpx.Response(
                200,
                text="observation_date,VIXCLS\n2026-08-01,17.2\n",
                request=request,
            )
        return httpx.Response(
            200,
            text=f"observation_date,{series_id}\n2026-08-01,.\n",
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = FredDailyAdapter(client=client)

    result = adapter.daily_context(datetime(2026, 8, 2, 12, tzinfo=UTC))
    adapter.close()

    assert result["quality_status"] == "partial"
    assert set(result["observations"]) == {"vix"}
    assert result["derived"] == {}
    assert result["quality_warnings"]
    client.close()
