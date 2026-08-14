from scripts.start_urus_container import api_command, scheduler_command


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
