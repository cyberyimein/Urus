"""Run the Urus API and market-data scheduler under one container supervisor.

The production Apple Container image has one persistent SQLite volume and one
application container.  The scheduler remains a separate process so its
long-running polling loop cannot block Uvicorn, but it shares the container's
network namespace and lifecycle with the API.
"""

from __future__ import annotations

import os
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence
from urllib.error import URLError
from urllib.request import urlopen


APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HEALTH_TIMEOUT_SECONDS = 60.0
DEFAULT_POLL_SECONDS = 1.0
DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 10.0


def _float_env(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _uv_command() -> str:
    return os.environ.get("URUS_UV_COMMAND") or shutil.which("uv") or "uv"


def _app_port() -> str:
    return os.environ.get("APP_PORT", "8000")


def api_command(uv: str, host: str, port: str) -> list[str]:
    return [uv, "run", "uvicorn", "app.main:app", "--host", host, "--port", port]


def scheduler_command(uv: str, api_base_url: str) -> list[str]:
    return [
        uv,
        "run",
        "python",
        "scripts/schedule_market_data_collection.py",
        "--api-base-url",
        api_base_url,
        "--backend-managed-externally",
    ]


def _run_migrations(uv: str) -> None:
    subprocess.run(
        [uv, "run", "alembic", "upgrade", "head"],
        cwd=APP_ROOT,
        check=True,
    )


def _start(command: Sequence[str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        list(command),
        cwd=APP_ROOT,
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def _is_healthy(url: str, timeout: float = 5.0) -> bool:
    try:
        with urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def _wait_for_health(
    process: subprocess.Popen[bytes],
    health_url: str,
    timeout_seconds: float,
    poll_seconds: float,
    stop_requested: list[bool],
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if stop_requested[0]:
            raise KeyboardInterrupt
        if process.poll() is not None:
            raise RuntimeError(
                f"Urus API exited before health check passed (code={process.returncode})"
            )
        if _is_healthy(health_url):
            return
        time.sleep(poll_seconds)
    raise RuntimeError(f"Urus API did not become healthy within {timeout_seconds:g} seconds")


def _terminate(process: subprocess.Popen[bytes] | None, timeout_seconds: float) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=timeout_seconds)


def main() -> int:
    uv = _uv_command()
    host = os.environ.get("APP_HOST", "0.0.0.0")
    port = _app_port()
    health_host = os.environ.get("URUS_SUPERVISOR_HEALTH_HOST", "127.0.0.1")
    health_url = f"http://{health_host}:{port}/api/health"
    scheduler_api_host = os.environ.get("URUS_SCHEDULER_API_HOST", "127.0.0.1")
    scheduler_url = f"http://{scheduler_api_host}:{port}/api"
    health_timeout = _float_env(
        "URUS_SUPERVISOR_HEALTH_TIMEOUT_SECONDS", DEFAULT_HEALTH_TIMEOUT_SECONDS
    )
    poll_seconds = _float_env("URUS_SUPERVISOR_POLL_SECONDS", DEFAULT_POLL_SECONDS)
    shutdown_timeout = _float_env(
        "URUS_SUPERVISOR_SHUTDOWN_TIMEOUT_SECONDS", DEFAULT_SHUTDOWN_TIMEOUT_SECONDS
    )

    stop_requested = [False]
    api_process: subprocess.Popen[bytes] | None = None
    scheduler_process: subprocess.Popen[bytes] | None = None

    def request_shutdown(_signum: int, _frame: object) -> None:
        stop_requested[0] = True

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)

    try:
        _run_migrations(uv)
        api_process = _start(api_command(uv, host, port))
        _wait_for_health(
            api_process,
            health_url,
            health_timeout,
            poll_seconds,
            stop_requested,
        )
        scheduler_process = _start(scheduler_command(uv, scheduler_url))

        while not stop_requested[0]:
            if api_process.poll() is not None:
                raise RuntimeError(f"Urus API exited unexpectedly (code={api_process.returncode})")
            if scheduler_process.poll() is not None:
                raise RuntimeError(
                    f"Urus scheduler exited unexpectedly (code={scheduler_process.returncode})"
                )
            time.sleep(poll_seconds)
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Urus container supervisor failed: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        # Stop the scheduler first so it cannot submit a new run while the API
        # is shutting down, then stop Uvicorn and reap both process groups.
        _terminate(scheduler_process, shutdown_timeout)
        _terminate(api_process, shutdown_timeout)


if __name__ == "__main__":
    raise SystemExit(main())
