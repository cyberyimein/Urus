from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app.repositories.runs import RunRepository


def test_health_and_version(client: TestClient) -> None:
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["database"] == "ok"

    version = client.get("/api/version")
    assert version.status_code == 200
    assert version.json()["api_schema_version"] == "v1"


def test_interrupted_workflow_is_closed_for_restart_recovery(client: TestClient) -> None:
    run_id = str(uuid4())
    with client.app.state.session_factory() as session:
        repository = RunRepository(session)
        run = repository.create_run(
            run_id=run_id,
            run_type="pre_close",
            cutoff_time=datetime.now(UTC),
        )
        steps = repository.create_steps(
            run_id,
            [
                (str(uuid4()), 1, "1a", "running"),
                (str(uuid4()), 2, "1b", "pending"),
            ],
        )
        repository.update_run(run, status="running", started_at=datetime.now(UTC))

        assert repository.recover_interrupted_runs(completed_at=datetime.now(UTC)) == 1
        recovered = repository.get_run(run_id)

        assert recovered is not None
        assert recovered.status == "failed"
        assert recovered.completed_at is not None
        assert [step.status for step in recovered.steps] == ["failed", "skipped"]


def test_ai_decision_audit_endpoint_is_read_only(client: TestClient) -> None:
    response = client.get("/api/ai/decisions")
    assert response.status_code == 200
    assert response.json() == []
    missing = client.get("/api/ai/decisions/not-found")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "ai_decision_not_found"


def test_pre_market_run_persists_order_skip_states_and_read_model(client: TestClient) -> None:
    created = client.post("/api/runs", json={"run_type": "pre_market"})
    assert created.status_code == 201
    created_body = created.json()
    assert created_body["run_id"]
    assert created_body["snapshot_id"]

    run = client.get(f"/api/runs/{created_body['run_id']}")
    assert run.status_code == 200
    body = run.json()
    assert body["status"] == "mixed"
    assert [step["step_code"] for step in body["steps"]] == ["1a", "1b", "2", "3a", "3b", "4", "5"]
    assert body["steps"][0]["status"] == "succeeded"
    assert body["steps"][0]["data_state"] == "mock"
    assert body["steps"][1]["status"] == "skipped"
    assert body["steps"][4]["status"] == "skipped"
    assert body["steps"][2]["status"] == "placeholder"
    assert body["steps"][3]["status"] == "unavailable"
    assert body["steps"][5]["status"] == "placeholder"
    assert body["cutoff_time"].endswith("+00:00")
    assert body["started_at"].endswith("+00:00")
    assert body["completed_at"].endswith("+00:00")
    assert body["steps"][0]["started_at"].endswith("+00:00")
    assert body["steps"][0]["completed_at"].endswith("+00:00")

    frontend = client.get(f"/api/snapshots/{created_body['snapshot_id']}/frontend")
    assert frontend.status_code == 200
    read_model = frontend.json()
    assert read_model["is_mock"] is True
    assert read_model["data_state"] == "mock"
    assert read_model["market"]["symbol"] == "QQQ"
    assert read_model["instrument"]["symbol"] == "INTC"
    assert {item["symbol"] for item in read_model["instrument_cards"]} == {"INTC", "SMH"}
    assert read_model["macro_event"]["status"] == "skipped"
    assert read_model["options"]["available"] is False
    assert read_model["options"]["status"] == "placeholder"
    assert read_model["instrument"]["data_state"] == "unavailable"
    assert read_model["decision"]["is_mock"] is True
    assert read_model["decision"]["status"] == "placeholder"
    assert read_model["decision"]["availability_status"] == "disabled"
    assert read_model["technical_report"]["schema_version"] == "urus.technical_report.v1"
    assert read_model["cutoff_time"].endswith("+00:00")
    assert read_model["generated_at"].endswith("+00:00")
    snapshot = client.get(f"/api/snapshots/{created_body['snapshot_id']}")
    assert snapshot.status_code == 200
    assert snapshot.json()["cutoff_time"].endswith("+00:00")
    assert snapshot.json()["created_at"].endswith("+00:00")


def test_pre_close_can_simulate_both_conditional_events(client: TestClient) -> None:
    created = client.post(
        "/api/runs",
        json={
            "run_type": "pre_close",
            "simulate_macro_event": True,
            "simulate_instrument_event": True,
        },
    )
    assert created.status_code == 201
    run_id = created.json()["run_id"]
    snapshot_id = created.json()["snapshot_id"]
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["run_type"] == "pre_close"
    assert run["steps"][1]["status"] == "succeeded"
    assert run["steps"][4]["status"] == "succeeded"

    read_model = client.get(f"/api/snapshots/{snapshot_id}/frontend").json()
    assert "模拟摘要" in read_model["macro_event"]["summary"]
    assert "模拟摘要" in read_model["instrument_event"]["summary"]


def test_pre_close_pairs_with_same_day_pre_market_snapshot(client: TestClient) -> None:
    pre_market = client.post("/api/runs", json={"run_type": "pre_market"}).json()
    pre_close = client.post("/api/runs", json={"run_type": "pre_close"}).json()

    read_model = client.get(f"/api/snapshots/{pre_close['snapshot_id']}/frontend").json()
    decision = read_model["decision"]

    assert decision["status"] == "collection_only"
    assert decision["availability_status"] == "not_applicable"
    assert decision["provider"] == "not_called"
    assert decision["dataset_key"].startswith("daily-decision:")
    assert decision["source_run_ids"] == [pre_market["run_id"], pre_close["run_id"]]
    assert decision["source_snapshot_ids"] == [
        pre_market["snapshot_id"],
        pre_close["snapshot_id"],
    ]
    assert read_model["technical_report"]["source"]["evidence_scope"] == "paired"
    assert read_model["technical_report"]["source"]["run_count"] == 2


def test_pre_close_never_requires_or_invokes_ai_provider(tmp_path) -> None:
    from app.core.config import Settings
    from app.main import create_app

    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'collection-only.db'}",
        cors_origins="http://testserver",
        enabled_symbols="QQQ,INTC",
        instrument_validation_symbols="INTC,SMH",
        moomoo_enabled=False,
        fred_enabled=False,
        anomalo_enabled=False,
        expected_events_enabled=False,
        urus_agent_enabled=True,
        openrouter_api_key=None,
    )
    with TestClient(create_app(settings)) as enabled_client:
        created = enabled_client.post("/api/runs", json={"run_type": "pre_close"})

        assert created.status_code == 201
        run = enabled_client.get(f"/api/runs/{created.json()['run_id']}").json()
        decision = next(step for step in run["steps"] if step["step_code"] == "4")
        assert decision["status"] == "succeeded"
        assert decision["payload"]["status"] == "collection_only"
        assert decision["payload"]["provider"] == "not_called"


def test_manual_analysis_refuses_placeholder_when_ai_is_disabled(client: TestClient) -> None:
    created = client.post("/api/analysis/runs", json={})

    assert created.status_code == 503
    assert created.json()["error"]["code"] == "manual_analysis_ai_unavailable"


def test_post_close_review_run_type_is_persisted(client: TestClient) -> None:
    created = client.post("/api/runs", json={"run_type": "post_close_review"})
    assert created.status_code == 201
    body = created.json()
    run = client.get(f"/api/runs/{body['run_id']}").json()
    assert run["run_type"] == "post_close_review"
    assert run["steps"][1]["status"] == "skipped"
    assert run["steps"][4]["status"] == "skipped"


def test_run_can_force_ai_off_when_server_agent_is_enabled(tmp_path) -> None:
    from app.core.config import Settings
    from app.main import create_app

    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'agent-enabled.db'}",
        cors_origins="http://testserver",
        enabled_symbols="QQQ,INTC",
        instrument_validation_symbols="INTC,SMH",
        moomoo_enabled=False,
        fred_enabled=False,
        anomalo_enabled=False,
        expected_events_enabled=False,
        urus_agent_enabled=True,
        openrouter_api_key="must-not-be-used",
    )
    with TestClient(create_app(settings)) as enabled_client:
        created = enabled_client.post(
            "/api/runs",
            json={"run_type": "pre_market", "skip_ai_decision": True},
        )

        assert created.status_code == 201
        run = enabled_client.get(f"/api/runs/{created.json()['run_id']}").json()
        decision = next(step for step in run["steps"] if step["step_code"] == "4")
        assert decision["status"] == "placeholder"
        assert decision["payload"]["availability_status"] == "disabled"


def test_unsupported_symbol_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/runs",
        json={"run_type": "pre_market", "symbols": ["QQQ", "TSLA", "INTC"]},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_symbol"


def test_failed_step_leaves_error_read_model(client: TestClient) -> None:
    created = client.post(
        "/api/runs",
        json={"run_type": "pre_market", "fail_step": "1a"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "failed"
    run = client.get(f"/api/runs/{body['run_id']}").json()
    assert run["steps"][0]["status"] == "failed"
    assert run["status"] == "failed"
    read_model = client.get(f"/api/snapshots/{body['snapshot_id']}/frontend")
    assert read_model.status_code == 200
    assert read_model.json()["market"] is None
    assert read_model.json()["data_quality"]["errors"]


def test_optional_step_failure_is_partial_not_total_failure(client: TestClient) -> None:
    created = client.post(
        "/api/runs",
        json={
            "run_type": "pre_market",
            "simulate_macro_event": True,
            "fail_step": "1b",
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "partial"
    run = client.get(f"/api/runs/{body['run_id']}").json()
    assert run["steps"][1]["status"] == "failed"
