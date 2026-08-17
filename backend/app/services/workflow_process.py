from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from app.core.config import Settings
from app.schemas.read_model import RunCreateRequest


BACKEND_ROOT = Path(__file__).resolve().parents[2]
WORKER_SCRIPT = BACKEND_ROOT / "scripts" / "run_workflow_worker.py"


def process_isolation_enabled(settings: Settings) -> bool:
    """Tests stay in-process; deployed workloads use a recyclable child process."""

    return settings.workflow_process_isolation and settings.app_env.lower() != "test"


def workflow_worker_command(run_id: str, request: RunCreateRequest) -> list[str]:
    return [
        sys.executable,
        str(WORKER_SCRIPT),
        "--run-id",
        run_id,
        "--request-json",
        request.model_dump_json(),
    ]


def run_workflow_process(settings: Settings, run_id: str, request: RunCreateRequest) -> None:
    env = os.environ.copy()
    # Reproduce the exact create_app settings in the child, including values
    # changed by an embedding process. Secrets remain in the child environment
    # and never appear in argv or logs.
    for name, value in settings.model_dump().items():
        if value is None:
            env.pop(name.upper(), None)
        elif isinstance(value, bool):
            env[name.upper()] = "true" if value else "false"
        else:
            env[name.upper()] = str(value)
    subprocess.run(
        workflow_worker_command(run_id, request),
        cwd=BACKEND_ROOT,
        env=env,
        check=True,
        timeout=max(60.0, settings.workflow_process_timeout_seconds),
    )
