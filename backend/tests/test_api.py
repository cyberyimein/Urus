from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from app.repositories.daily_evidence import DailyEvidenceRepository
from app.services.capital_flow import completed_session_dates


def _daily_api_bars(symbol: str, dates: list[date], start: float) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, trading_date in enumerate(dates):
        close = start + index * 0.2
        rows.append(
            {
                "symbol": symbol,
                "date": trading_date.isoformat(),
                "open": close - 0.15,
                "high": close + 0.4,
                "low": close - 0.4,
                "close": close,
                "volume": 100000.0 + index * 100.0,
            }
        )
    return rows


def test_health_and_version(client: TestClient) -> None:
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["database"] == "ok"

    version = client.get("/api/version")
    assert version.status_code == 200
    assert version.json()["api_schema_version"] == "v1"


def test_daily_evidence_api_returns_deterministic_strategy_bundle(client: TestClient) -> None:
    cutoff = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    session_dates = completed_session_dates(cutoff, "XNYS", count=260)
    with client.app.state.session_factory() as session:
        repository = DailyEvidenceRepository(session)
        repository.upsert_bars(
            _daily_api_bars("INTC", session_dates, 20.0),
            source="fixture",
            collected_at=cutoff,
        )
        repository.upsert_bars(
            _daily_api_bars("QQQ", session_dates, 300.0),
            source="fixture",
            collected_at=cutoff,
        )
        session.commit()

    created = client.post(
        "/api/daily-evidence/datasets",
        json={
            "scope_type": "instrument",
            "scope_id": "INTC",
            "scope_version": 1,
            "symbols": ["INTC"],
            "benchmark_symbols": ["QQQ"],
            "trading_date": session_dates[-1].isoformat(),
            "cutoff_time": cutoff.isoformat(),
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert len(body["strategy_decisions"]) == 5
    assert "quality_left_side_reversal_v1" in {
        item["strategy"]["name"] for item in body["strategy_decisions"]
    }
    assert body["deterministic_synthesis"]["strategy_set_sha256"]
    assert body["chart"]["overlays"]

    dataset_id = body["dataset"]["dataset_id"]
    chart = client.get(f"/api/daily-evidence/datasets/{dataset_id}/chart")
    assert chart.status_code == 200
    assert chart.json()["overlays"]
    assert chart.json()["content_sha256"]

    strategies = client.get(f"/api/daily-evidence/datasets/{dataset_id}/strategies")
    assert strategies.status_code == 200
    assert len(strategies.json()["strategy_decisions"]) == 5
