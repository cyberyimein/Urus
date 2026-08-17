from scripts.start_urus_container import api_command, scheduler_command
from app.schemas.read_model import RunCreateRequest
from app.services.workflow_process import workflow_worker_command


def test_supervisor_starts_api_with_configured_port() -> None:
    assert api_command("uv", "0.0.0.0", "8123") == [
        "uv",
        "run",
        "uvicorn",
        "app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8123",
    ]


def test_supervisor_runs_scheduler_against_loopback_api() -> None:
    assert scheduler_command("uv", "http://127.0.0.1:8000/api") == [
        "uv",
        "run",
        "python",
        "scripts/schedule_market_data_collection.py",
        "--api-base-url",
        "http://127.0.0.1:8000/api",
        "--backend-managed-externally",
    ]


def test_workflow_worker_command_contains_only_non_secret_request_data() -> None:
    command = workflow_worker_command("run-1", RunCreateRequest(run_type="pre_market"))

    assert command[-4:-2] == ["--run-id", "run-1"]
    assert command[-2] == "--request-json"
    assert '"run_type":"pre_market"' in command[-1]
    assert "api_key" not in command[-1]


def test_adapter_cleanup_continues_after_one_adapter_raises() -> None:
    from app.services.run_service import RunService

    closed: list[str] = []

    class Broken:
        def close(self) -> None:
            closed.append("broken")
            raise RuntimeError("close failed")

    class Healthy:
        def close(self) -> None:
            closed.append("healthy")

    RunService._close_adapters(Broken(), Healthy())

    assert closed == ["broken", "healthy"]
