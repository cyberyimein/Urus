from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import exchange_calendars as xcals
import pytest
from fastapi.testclient import TestClient

from app.services import opportunity_views
from app.services.opportunity_views import (
    RESPONSE_SCHEMA,
    DailyOpportunityViewService,
    build_dataset_opportunity_view,
)


def _fixture_payload(*, bar_count: int = 75) -> tuple[dict[str, Any], dict[str, Any]]:
    calendar = xcals.get_calendar("XNYS")
    sessions = list(calendar.sessions_in_range("2026-04-01", "2026-08-31"))[-bar_count:]
    bars: list[dict[str, Any]] = []
    for index, session in enumerate(sessions):
        trading_date = session.date()
        if index < max(0, bar_count - 20):
            open_, high, low, close = 100.0, 101.0, 99.0, 100.0
        else:
            open_, high, low, close = 105.0, 110.0, 103.0, 105.0
        bars.append(
            {
                "date": trading_date.isoformat(),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": 2_000_000.0,
            }
        )

    target = sessions[-1]
    bars[-1] = {
        "date": target.date().isoformat(),
        "open": 109.0,
        "high": 112.0,
        "low": 108.0,
        "close": 111.0,
        "volume": 2_000_000.0,
    }
    cutoff = calendar.session_close(target).to_pydatetime().astimezone(UTC) + timedelta(hours=2)
    dataset = {
        "dataset_id": "dataset-api-1",
        "schema_version": "urus.daily_decision_dataset.v1",
        "scope": {
            "scope_type": "instrument",
            "scope_id": "AAA",
            "symbols": ["AAA"],
            "benchmark_symbols": ["SPY", "QQQ"],
        },
        "trading_date": target.date().isoformat(),
        "cutoff_time": cutoff.isoformat(),
        "price_basis": "adjusted",
        "bar_manifest": [
            {
                "symbol": "AAA",
                "source": "fixture-source",
                "exchange": "XNYS",
                "adjustment": "QFQ",
                "bar_count": len(bars),
            }
        ],
        "quality": {
            "status": "ok",
            "symbols": {"AAA": {"status": "ok", "warnings": []}},
        },
    }
    chart = {
        "dataset_id": dataset["dataset_id"],
        "instruments": {
            "AAA": {
                "asset_type": "equity",
                "price": {"bars": bars},
            }
        },
    }
    return dataset, chart


def test_opportunity_view_endpoint_returns_404_for_missing_dataset(client: TestClient) -> None:
    response = client.get("/api/daily-evidence/datasets/missing/opportunity-view")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "daily_dataset_not_found"


def test_opportunity_view_handles_missing_and_short_chart() -> None:
    dataset, _ = _fixture_payload()
    viewed_at = datetime(2026, 8, 31, 23, tzinfo=UTC)

    missing = build_dataset_opportunity_view(dataset, None, viewed_at=viewed_at)
    assert missing["schema_version"] == RESPONSE_SCHEMA
    assert missing["views"][0]["status"] == "unavailable"
    assert missing["views"][0]["opportunity"] is None
    assert "chart_projection_missing" in missing["warnings"]

    short_dataset, short_chart = _fixture_payload(bar_count=5)
    short = build_dataset_opportunity_view(short_dataset, short_chart, viewed_at=viewed_at)
    assert short["views"][0]["status"] == "unavailable"
    assert short["views"][0]["opportunity"] is None
    assert "insufficient_history" in short["views"][0]["reason_codes"]


def test_opportunity_view_reconstructs_daily_close_with_unknown_provenance() -> None:
    dataset, chart = _fixture_payload()
    viewed_at = datetime(2026, 8, 31, 23, tzinfo=UTC)

    payload = build_dataset_opportunity_view(dataset, chart, viewed_at=viewed_at)
    row = payload["views"][0]
    opportunity = row["opportunity"]

    assert row["symbol"] == "AAA"
    assert opportunity is not None
    assert opportunity["source_mode"] == "historical_reconstruction"
    assert opportunity["data_source"] == "unknown"
    assert opportunity["reference_kind"] == "daily_close"
    assert row["reference"]["kind"] == "daily_close"
    assert row["reference"]["price"] == 111.0
    assert opportunity["research_support"] == "unknown"
    assert opportunity["status"] != "in_entry_zone"
    assert opportunity["evidence_refs"][-1]["source"] == "fixture-source"
    assert "AAA:per_bar_available_at_unavailable" in payload["warnings"]


def test_opportunity_view_is_stale_after_dataset_cutoff() -> None:
    dataset, chart = _fixture_payload()
    viewed_at = datetime(2026, 9, 5, 22, tzinfo=UTC)

    payload = build_dataset_opportunity_view(dataset, chart, viewed_at=viewed_at)
    row = payload["views"][0]

    assert row["status"] == "stale"
    assert row["opportunity"]["freshness"] == "stale"
    assert "daily_close_older_than_current_session" in row["reason_codes"]
    assert row["reference"]["kind"] == "daily_close"


def test_opportunity_view_is_deterministic_for_same_inputs() -> None:
    dataset, chart = _fixture_payload()
    viewed_at = datetime(2026, 8, 31, 23, tzinfo=UTC)

    first = build_dataset_opportunity_view(dataset, chart, viewed_at=viewed_at)
    second = build_dataset_opportunity_view(dataset, chart, viewed_at=viewed_at)

    assert first == second


def test_opportunity_view_service_reads_only_dataset_and_chart(monkeypatch) -> None:
    class FakeDailyMarketEvidenceService:
        instances: list["FakeDailyMarketEvidenceService"] = []

        def __init__(self, session, settings) -> None:
            self.calls: list[tuple[str, str]] = []
            self.__class__.instances.append(self)

        def get_dataset(self, dataset_id: str):
            self.calls.append(("get_dataset", dataset_id))
            return {
                "dataset_id": dataset_id,
                "scope": {"symbols": []},
                "cutoff_time": "2026-08-31T22:00:00+00:00",
            }

        def get_chart(self, dataset_id: str):
            self.calls.append(("get_chart", dataset_id))
            return None

    monkeypatch.setattr(
        opportunity_views,
        "DailyMarketEvidenceService",
        FakeDailyMarketEvidenceService,
    )
    service = DailyOpportunityViewService(object(), object())

    payload = service.get_dataset_opportunity_view(
        "dataset-read-only",
        viewed_at=datetime(2026, 9, 1, tzinfo=UTC),
    )

    assert payload is not None
    assert payload["dataset_id"] == "dataset-read-only"
    assert FakeDailyMarketEvidenceService.instances[0].calls == [
        ("get_dataset", "dataset-read-only"),
        ("get_chart", "dataset-read-only"),
    ]


@pytest.mark.parametrize("cutoff", [None, "2026-08-31T22:00:00", "2026-09-01T22:00:00Z"])
def test_unusable_or_future_cutoff_does_not_expose_reconstructed_prices(cutoff) -> None:
    dataset, chart = _fixture_payload()
    dataset["cutoff_time"] = cutoff
    payload = build_dataset_opportunity_view(
        dataset, chart, viewed_at=datetime(2026, 8, 31, 23, tzinfo=UTC)
    )
    row = payload["views"][0]
    assert row["status"] == "unavailable"
    assert row["opportunity"] is None
    assert row["reference"]["price"] is None
    if cutoff is None or cutoff == "2026-08-31T22:00:00":
        assert payload["knowledge_at"] is None


def test_dataset_symbol_quality_conflict_blocks_an_otherwise_valid_chart() -> None:
    dataset, chart = _fixture_payload()
    dataset["quality"]["symbols"]["AAA"]["status"] = "conflicted"
    payload = build_dataset_opportunity_view(
        dataset, chart, viewed_at=datetime(2026, 8, 31, 23, tzinfo=UTC)
    )
    assert payload["views"][0]["status"] == "unavailable"
    assert payload["views"][0]["opportunity"] is None


def test_explicit_scope_symbol_is_retained_when_it_is_also_the_benchmark() -> None:
    dataset, chart = _fixture_payload()
    dataset["scope"]["benchmark_symbols"] = ["AAA"]
    chart["instruments"]["AAA"]["asset_type"] = "etf"
    payload = build_dataset_opportunity_view(
        dataset, chart, viewed_at=datetime(2026, 8, 31, 23, tzinfo=UTC)
    )
    assert [row["symbol"] for row in payload["views"]] == ["AAA"]
    assert payload["views"][0]["opportunity"] is not None
    assert "scan_setup_mismatch" not in payload["views"][0]["reason_codes"]


def test_opportunity_view_endpoint_serializes_a_valid_read(client: TestClient, monkeypatch) -> None:
    dataset, chart = _fixture_payload()
    payload = build_dataset_opportunity_view(
        dataset, chart, viewed_at=datetime(2026, 8, 31, 23, tzinfo=UTC)
    )
    monkeypatch.setattr(
        DailyOpportunityViewService, "get_dataset_opportunity_view",
        lambda self, dataset_id, **kwargs: payload,
    )
    response = client.get("/api/daily-evidence/datasets/dataset-api-1/opportunity-view")
    assert response.status_code == 200
    value = response.json()
    assert value["dataset_id"] == "dataset-api-1"
    assert value["views"][0]["opportunity"]["knowledge_at"]
    assert value["views"][0]["reference"]["price"] == 111.0
