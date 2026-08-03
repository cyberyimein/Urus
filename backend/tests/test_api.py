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
    assert [item["symbol"] for item in read_model["instrument_cards"]] == ["INTC", "SMH"]
    assert read_model["macro_event"]["status"] == "skipped"
    assert read_model["options"]["available"] is False
    assert read_model["options"]["status"] == "placeholder"
    assert read_model["instrument"]["data_state"] == "unavailable"
    assert read_model["decision"]["is_mock"] is True
    assert read_model["decision"]["status"] == "placeholder"
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


def test_unsupported_symbol_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/runs",
        json={"run_type": "pre_market", "symbols": ["QQQ", "AAPL", "INTC"]},
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
