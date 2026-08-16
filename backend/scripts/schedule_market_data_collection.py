"""Keep Urus available and run the configured Tokyo schedule.

The schedule is controlled by the backend's persisted ``/api/settings``
payload. Tail collection is always data-only; the two official decision slots
can independently be disabled or run without starting the AI decision step.
"""

from __future__ import annotations

import argparse
import fcntl
from functools import lru_cache
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import UTC, date, datetime, time as wall_time, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import exchange_calendars as xcals


BACKEND_DIR = Path(__file__).resolve().parents[1]
# Containers mount the host-owned persistence directory at /data. Development
# keeps the existing repository-local default unless explicitly overridden.
DATA_DIR = Path(
    os.environ.get(
        "URUS_SCHEDULER_DATA_DIR",
        str(BACKEND_DIR / "data" / "scheduled_collection"),
    )
).expanduser()
TOKYO = ZoneInfo("Asia/Tokyo")
DEFAULT_MARKET_CALENDAR = "XNYS"
SLOTS = (
    (wall_time(4, 0), "pre_close", "尾盘前"),
    (wall_time(5, 30), "post_close_review", "盘后"),
    (wall_time(21, 30), "pre_market", "盘前"),
)

sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

from app.core.config import Settings  # noqa: E402


logger = logging.getLogger("urus.scheduled_collection")


@lru_cache(maxsize=4)
def exchange_calendar(name: str) -> Any:
    """Return a cached exchange calendar for the scheduler process.

    The calendar is deliberately resolved once per name. ``exchange-calendars``
    keeps a full holiday and early-close schedule in memory, so recreating it
    on every 20-second polling cycle would add unnecessary work and could make
    a transient provider error look like a scheduling decision.
    """

    return xcals.get_calendar(name)


def _session_close(calendar: Any, market_date: date) -> datetime | None:
    """Return a session close as an aware UTC datetime, or ``None`` if closed."""

    if not calendar.is_session(market_date):
        return None
    row = calendar.schedule.loc[market_date.isoformat()]
    close = row.get("close")
    if close is None or str(close) == "NaT":
        return None
    close_at = close.to_pydatetime()
    if close_at.tzinfo is None:
        close_at = close_at.replace(tzinfo=UTC)
    return close_at.astimezone(UTC)


def effective_slot_time(
    nominal_at: datetime,
    run_type: str,
    market_timezone: ZoneInfo,
    include_weekends: bool,
    calendar: Any,
) -> datetime | None:
    """Apply the exchange session gate and early-close adjustments.

    The configured slots remain the normal-day defaults. On an early-close
    session, ``pre_close`` moves to one hour before the actual close rather
    than collecting after the market has shut; ``post_close_review`` moves to
    30 minutes after the actual close when its nominal JST time would still be
    inside the regular session. This keeps the persisted schedule readable
    while making the runtime safe across US daylight-saving and half-day rules.
    """

    market_date = nominal_at.astimezone(market_timezone).date()
    market_weekday = market_date.weekday()
    if market_weekday >= 5:
        # Preserve the explicit validation escape hatch. A normal scheduler
        # invocation still excludes weekends before reaching this branch.
        return nominal_at if include_weekends else None

    close_at = _session_close(calendar, market_date)
    if close_at is None:
        return None

    nominal_utc = nominal_at.astimezone(UTC)
    if run_type == "pre_close" and nominal_utc >= close_at:
        return (close_at - timedelta(hours=1)).astimezone(TOKYO)
    if run_type == "post_close_review" and nominal_utc <= close_at:
        return (close_at + timedelta(minutes=30)).astimezone(TOKYO)
    return nominal_at


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-base-url",
        help="Backend API root; defaults to APP_HOST/APP_PORT from .env.",
    )
    parser.add_argument(
        "--once",
        choices=[item[1] for item in SLOTS],
        help="Collect one phase immediately and exit (useful for validation).",
    )
    parser.add_argument("--poll-seconds", type=float, default=20.0)
    parser.add_argument("--catch-up-minutes", type=float, default=180.0)
    parser.add_argument("--retry-seconds", type=float, default=300.0)
    parser.add_argument("--backend-start-timeout", type=float, default=60.0)
    parser.add_argument(
        "--backend-managed-externally",
        action="store_true",
        help="Wait for the configured backend instead of starting another process (for containers).",
    )
    parser.add_argument("--request-timeout", type=float, default=7200.0)
    parser.add_argument(
        "--include-weekends",
        action="store_true",
        help="Also run slots whose timestamp falls on a US-market Saturday/Sunday.",
    )
    return parser.parse_args(argv)


def configure_logging() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    file_handler = RotatingFileHandler(
        DATA_DIR / "scheduler.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(stream)
    logger.addHandler(file_handler)


def api_base_url(settings: Settings, override: str | None) -> str:
    if override:
        return override.rstrip("/")
    host = settings.app_host
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    return f"http://{host}:{settings.app_port}/api"


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Cannot reach {url}: {exc.reason}") from exc
    parsed = json.loads(data)
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Expected a JSON object from {url}")
    return parsed


def backend_is_healthy(base_url: str) -> bool:
    try:
        return request_json(f"{base_url}/health", timeout=5.0).get("status") == "ok"
    except (RuntimeError, json.JSONDecodeError, TimeoutError):
        return False


def start_backend(settings: Settings) -> subprocess.Popen[bytes]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    backend_log = (DATA_DIR / "backend.log").open("ab", buffering=0)
    environment = os.environ.copy()
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        settings.app_host,
        "--port",
        str(settings.app_port),
    ]
    logger.info("后端未启动，正在启动 Urus backend（保留现有 AI 配置；采集请求单独禁用 AI）")
    return subprocess.Popen(
        command,
        cwd=BACKEND_DIR,
        env=environment,
        stdout=backend_log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def ensure_backend(
    settings: Settings, base_url: str, start_timeout: float, *, allow_start: bool = True
) -> subprocess.Popen[bytes] | None:
    if backend_is_healthy(base_url):
        return None
    if not allow_start:
        raise RuntimeError(f"Externally managed backend is not healthy: {base_url}")
    process = start_backend(settings)
    deadline = time.monotonic() + start_timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Backend exited with code {process.returncode}; see {DATA_DIR / 'backend.log'}"
            )
        if backend_is_healthy(base_url):
            logger.info("Urus backend 已就绪 pid=%s", process.pid)
            return process
        time.sleep(1.0)
    raise RuntimeError(f"Backend did not become healthy within {start_timeout:g} seconds")


def verify_skip_ai_contract(base_url: str) -> None:
    # Fail closed when an older already-running backend would silently ignore
    # the new request field and could therefore invoke its configured agent.
    openapi_url = f"{base_url.removesuffix('/api')}/openapi.json"
    schema = request_json(openapi_url, timeout=10.0)
    properties = (
        schema.get("components", {})
        .get("schemas", {})
        .get("RunCreateRequest", {})
        .get("properties", {})
    )
    if "skip_ai_decision" not in properties:
        raise RuntimeError(
            "Running backend does not support skip_ai_decision; restart it with the current code"
        )


def load_schedule_settings(base_url: str) -> dict[str, dict[str, Any]]:
    payload = request_json(f"{base_url}/settings", timeout=10.0)
    schedule = payload.get("schedule")
    if not isinstance(schedule, dict):
        raise RuntimeError("Backend settings response is missing schedule policy")
    return {
        str(run_type): value
        for run_type, value in schedule.items()
        if isinstance(value, dict)
    }


def slot_policy(
    schedule: dict[str, dict[str, Any]], run_type: str
) -> tuple[bool, bool]:
    policy = schedule.get(run_type, {})
    enabled = bool(policy.get("enabled", True))
    skip_ai = bool(policy.get("skip_ai_decision", run_type == "pre_close"))
    # This is a safety boundary, not a UI preference: pre_close never invokes
    # the decision agent because it is the raw tail-data collection slot.
    if run_type == "pre_close":
        skip_ai = True
    return enabled, skip_ai


def collect(
    base_url: str,
    run_type: str,
    timeout: float,
    skip_ai_decision: bool = True,
) -> dict[str, Any]:
    verify_skip_ai_contract(base_url)
    logger.info(
        "开始采集 phase=%s（skip_ai_decision=%s）", run_type, skip_ai_decision
    )
    result = request_json(
        f"{base_url}/runs",
        method="POST",
        payload={"run_type": run_type, "skip_ai_decision": skip_ai_decision},
        timeout=timeout,
    )
    logger.info(
        "采集完成 phase=%s run_id=%s snapshot_id=%s status=%s",
        run_type,
        result.get("run_id"),
        result.get("snapshot_id"),
        result.get("status"),
    )
    return result


def load_state() -> dict[str, Any]:
    path = DATA_DIR / "state.json"
    if not path.exists():
        return {"completed": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read scheduler state {path}: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("completed"), dict):
        raise RuntimeError(f"Invalid scheduler state: {path}")
    return value


def save_state(state: dict[str, Any]) -> None:
    path = DATA_DIR / "state.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def slot_key(scheduled_at: datetime, run_type: str) -> str:
    return f"{scheduled_at.date().isoformat()}:{run_type}"


def due_slots(
    now: datetime,
    completed: dict[str, Any],
    catch_up: timedelta,
    market_timezone: ZoneInfo,
    include_weekends: bool,
    schedule: dict[str, dict[str, Any]] | None = None,
    calendar: Any | None = None,
) -> list[tuple[datetime, str, str]]:
    candidates: list[tuple[datetime, str, str]] = []
    calendar = calendar or exchange_calendar(DEFAULT_MARKET_CALENDAR)
    for day_offset in (-1, 0):
        date = (now + timedelta(days=day_offset)).date()
        for scheduled_time, run_type, label in SLOTS:
            if schedule is not None and not slot_policy(schedule, run_type)[0]:
                continue
            nominal_at = datetime.combine(date, scheduled_time, tzinfo=TOKYO)
            scheduled_at = effective_slot_time(
                nominal_at,
                run_type,
                market_timezone,
                include_weekends,
                calendar,
            )
            if scheduled_at is None:
                continue
            age = now - scheduled_at
            if age < timedelta(0) or age > catch_up:
                continue
            if slot_key(scheduled_at, run_type) not in completed:
                candidates.append((scheduled_at, run_type, label))
    return sorted(candidates)


def acquire_single_instance_lock():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock = (DATA_DIR / "scheduler.lock").open("a+")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise RuntimeError("Another market-data scheduler is already running") from exc
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()))
    lock.flush()
    return lock


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging()
    instance_lock = acquire_single_instance_lock()
    settings = Settings()
    base_url = api_base_url(settings, args.api_base_url)
    allow_backend_start = not args.backend_managed_externally
    ensure_backend(
        settings, base_url, args.backend_start_timeout, allow_start=allow_backend_start
    )
    verify_skip_ai_contract(base_url)
    schedule = load_schedule_settings(base_url)

    if args.once:
        enabled, skip_ai_decision = slot_policy(schedule, args.once)
        if not enabled:
            logger.info("phase=%s 已在设置中停用，本次不执行", args.once)
            return 0
        collect(base_url, args.once, args.request_timeout, skip_ai_decision)
        return 0

    state = load_state()
    completed = state["completed"]
    retry_after: dict[str, float] = {}
    market_timezone = ZoneInfo(settings.market_timezone)
    calendar = exchange_calendar(settings.market_calendar)
    logger.info(
        "调度器已启动 timezone=Asia/Tokyo market_timezone=%s calendar=%s "
        "slots=21:30/pre_market,04:00/pre_close,05:30/post_close_review",
        settings.market_timezone,
        settings.market_calendar,
    )
    try:
        while True:
            now = datetime.now(TOKYO)
            for scheduled_at, run_type, label in due_slots(
                now,
                completed,
                timedelta(minutes=args.catch_up_minutes),
                market_timezone,
                args.include_weekends,
                schedule,
                calendar,
            ):
                key = slot_key(scheduled_at, run_type)
                if time.monotonic() < retry_after.get(key, 0):
                    continue
                try:
                    ensure_backend(
                        settings,
                        base_url,
                        args.backend_start_timeout,
                        allow_start=allow_backend_start,
                    )
                    # Refresh immediately before a due slot so an operator can
                    # pause or bypass AI without restarting this long-running
                    # process.
                    schedule = load_schedule_settings(base_url)
                    enabled, skip_ai_decision = slot_policy(schedule, run_type)
                    if not enabled:
                        logger.info("phase=%s 已在设置中停用，本次跳过", run_type)
                        completed[key] = {
                            "scheduled_at": scheduled_at.isoformat(),
                            "completed_at": datetime.now(TOKYO).isoformat(),
                            "label": label,
                            "status": "disabled",
                        }
                        save_state(state)
                        continue
                    result = collect(
                        base_url, run_type, args.request_timeout, skip_ai_decision
                    )
                except Exception:
                    retry_after[key] = time.monotonic() + args.retry_seconds
                    logger.exception("采集失败 label=%s phase=%s；稍后重试", label, run_type)
                    continue
                completed[key] = {
                    "scheduled_at": scheduled_at.isoformat(),
                    "completed_at": datetime.now(TOKYO).isoformat(),
                    "label": label,
                    **result,
                }
                save_state(state)
                retry_after.pop(key, None)
            time.sleep(max(1.0, args.poll_seconds))
    except KeyboardInterrupt:
        logger.info("调度器已停止；由本脚本启动的 backend 将继续运行")
        return 0
    finally:
        instance_lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
