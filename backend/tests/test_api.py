from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_and_version(client: TestClient) -> None:
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["database"] == "ok"

    version = client.get("/api/version")
    assert version.status_code == 200
    assert version.json()["api_schema_version"] == "v1"


def test_framework_run_persists_order_and_mock_read_model(client: TestClient) -> None:
    created = client.post("/api/runs", json={"run_type": "pre_market"})
    assert created.status_code == 201
    body = created.json()
    assert body["run_id"]
    assert body["snapshot_id"]

    run = client.get(f"/api/runs/{body['run_id']}")
    assert run.status_code == 200
    run_body = run.json()
    assert run_body["status"] == "succeeded"
    assert [step["step_code"] for step in run_body["steps"]] == ["1a", "1b", "2", "3a", "3b", "4", "5"]
    assert run_body["steps"][0]["status"] == "succeeded"
    assert run_body["steps"][1]["status"] == "skipped"
    assert run_body["steps"][2]["status"] == "succeeded"
    assert run_body["steps"][4]["status"] == "skipped"

    read_model = client.get(f"/api/snapshots/{body['snapshot_id']}/frontend")
    assert read_model.status_code == 200
    model = read_model.json()
    assert model["is_mock"] is True
    assert model["market"]["symbol"] == "QQQ"
    assert model["instrument"]["symbol"] == "INTC"
    assert model["macro_event"]["status"] == "skipped"
    assert model["options"]["available"] is False
    assert model["decision"]["status"] == "not_implemented"


def test_conditional_events_can_be_simulated(client: TestClient) -> None:
    created = client.post(
        "/api/runs",
        json={
            "run_type": "pre_close",
            "simulate_macro_event": True,
            "simulate_instrument_event": True,
        },
    )
    assert created.status_code == 201
    run = client.get(f"/api/runs/{created.json()['run_id']}").json()
    assert run["steps"][1]["status"] == "succeeded"
    assert run["steps"][4]["status"] == "succeeded"


def test_unsupported_symbol_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/runs",
        json={"run_type": "pre_market", "symbols": ["QQQ", "AAPL", "INTC"]},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_symbol"


def test_failed_step_leaves_error_read_model(client: TestClient) -> None:
    created = client.post("/api/runs", json={"run_type": "pre_market", "fail_step": "1a"})
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
        json={"run_type": "pre_market", "simulate_macro_event": True, "fail_step": "1b"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "partial"
    run = client.get(f"/api/runs/{body['run_id']}").json()
    assert run["steps"][1]["status"] == "failed"
