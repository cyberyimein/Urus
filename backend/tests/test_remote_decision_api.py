from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.models.daily_evidence import DailyDecisionDatasetModel
from app.repositories.remote_decision import RemoteDecisionRepository


def _settings(tmp_path):
    return Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'remote.db'}",
        cors_origins="http://testserver",
        observation_universe_source_url="",
        enabled_symbols="QQQ,INTC",
        instrument_validation_symbols="INTC,SMH",
        moomoo_enabled=False,
        fred_enabled=False,
        anomalo_enabled=False,
        anomalo_workflow_enabled=True,
        anomalo_workflow_fake_adapter=True,
        expected_events_enabled=False,
        urus_agent_enabled=False,
    )


def test_preflight_submit_and_idempotency_with_fake_adapter(tmp_path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        with app.state.session_factory() as session:
            session.add(
                DailyDecisionDatasetModel(
                    id="dataset-1",
                    schema_version="urus.daily_decision_dataset.v1",
                    scope_type="instrument",
                    scope_id="INTC",
                    scope_version=None,
                    trading_date=date(2026, 8, 25),
                    cutoff_time=datetime(2026, 8, 25, 20, tzinfo=timezone.utc),
                    market_timezone="America/New_York",
                    bar_completion_policy="official_exchange_close_only_v1",
                    status="ok",
                    scope_json={"scope_type": "instrument", "scope_id": "INTC", "symbols": ["INTC"]},
                    bar_manifest_json=[{"symbol": "INTC"}],
                    indicator_snapshot_ids=[],
                    group_snapshot_ids=[],
                    quality_json={"status": "ok", "warnings": []},
                    payload_json={
                        "dataset_id": "dataset-1",
                        "schema_version": "urus.daily_decision_dataset.v1",
                        "scope": {"scope_type": "instrument", "scope_id": "INTC", "symbols": ["INTC"]},
                        "trading_date": "2026-08-25",
                        "status": "ok",
                        "bar_manifest": [{"symbol": "INTC"}],
                        "quality": {"status": "ok", "warnings": []},
                    },
                    content_sha256="d" * 64,
                    created_at=datetime.now(timezone.utc),
                )
            )
            RemoteDecisionRepository(session).save_binding(
                {
                    "intent_type": "instrument_arbitration",
                    "workflow_ref": "urus-instrument-arbitration@1",
                    "status": "active",
                    "definition_hash": "1" * 64,
                    "compiled_hash": "2" * 64,
                    "capability_manifest_hash": "3" * 64,
                    "output_schema_version": "urus.remote_decision_artifact.v1",
                    "published_at": datetime.now(timezone.utc),
                    "verified_at": datetime.now(timezone.utc),
                }
            )
            session.commit()

        preview = client.post(
            "/api/remote-decisions/preflight",
            json={"intent_type": "instrument_arbitration", "source": {"dataset_id": "dataset-1", "symbol": "INTC"}},
        )
        assert preview.status_code == 200
        assert preview.json()["enabled"] is True

        submit_body = {
            "intent_type": "instrument_arbitration",
            "source": {"dataset_id": "dataset-1", "symbol": "INTC"},
            "preflight_fingerprint": preview.json()["preflight_fingerprint"],
            "request_intent_id": "intent-1",
        }
        submitted = client.post("/api/remote-decisions", json=submit_body)
        assert submitted.status_code == 202
        local_run_id = submitted.json()["local_run_id"]
        repeated = client.post("/api/remote-decisions", json=submit_body)
        assert repeated.status_code == 202
        assert repeated.json()["local_run_id"] == local_run_id

        run = client.get(f"/api/remote-decisions/{local_run_id}").json()
        assert run["status"] in {"accepted", "succeeded", "running", "submitting"}
