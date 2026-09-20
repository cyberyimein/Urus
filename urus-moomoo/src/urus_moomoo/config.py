from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import tomllib
from typing import Any


ALLOWED_OPERATIONS = {
    "instrument_cards",
    "capital_flow_day",
    "options_snapshot",
    "capture_intraday",
    "capture_session_close",
    "capture_eod_options",
}


@dataclass(frozen=True)
class CollectionTask:
    """One deployment-owned collection task loaded from a local TOML file."""

    name: str
    enabled: bool
    schedule: str
    symbols: tuple[str, ...]
    operation: str = "instrument_cards"
    delay_seconds: float = 0.0
    params: dict[str, Any] | None = None


@dataclass(frozen=True)
class CollectorConfig:
    timezone: str
    output_dir: Path
    tasks: tuple[CollectionTask, ...]
    calendar_cache: Path | None = None
    lock_path: Path | None = None
    sqlite_path: Path | None = None
    opend_host: str | None = None
    opend_port: int | None = None
    market_timezone: str = "America/New_York"
    exchange_calendar: str = "XNYS"
    poll_seconds: float = 30.0
    catch_up_seconds: float = 3600.0
    intraday_catch_up_seconds: float = 600.0
    options: dict[str, Any] | None = None
    intraday_sqlite_path: Path | None = None
    intraday_raw_payload_dir: Path | None = None
    publisher: dict[str, Any] | None = None


def default_config_path() -> Path:
    configured = os.environ.get("URUS_MOOMOO_TASK_CONFIG", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[2] / "config" / "collection_tasks.local.toml"


def load_config(path: str | Path | None = None) -> CollectorConfig:
    config_path = Path(path).expanduser() if path is not None else default_config_path()
    if not config_path.is_file():
        raise FileNotFoundError(
            f"Missing local urus-moomoo task configuration: {config_path}. "
            "Create it from config/README.md; task definitions are intentionally not tracked."
        )
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    return _parse_config(raw, config_path)


def _parse_config(raw: dict[str, Any], config_path: Path) -> CollectorConfig:
    runtime = raw.get("runtime")
    if not isinstance(runtime, dict):
        raise ValueError(f"{config_path}: [runtime] is required")
    timezone = _required_text(runtime, "timezone", config_path)
    output_dir = Path(str(runtime.get("output_dir", "data"))).expanduser()
    raw_tasks = raw.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError(f"{config_path}: at least one [[tasks]] entry is required")
    tasks = tuple(_parse_task(item, index, config_path) for index, item in enumerate(raw_tasks))
    names = [task.name for task in tasks]
    if len(names) != len(set(names)):
        raise ValueError(f"{config_path}: task names must be unique")
    return CollectorConfig(
        timezone=timezone,
        output_dir=output_dir,
        tasks=tasks,
        calendar_cache=_optional_path(runtime.get("calendar_cache")),
        lock_path=_optional_path(runtime.get("lock_path")),
        sqlite_path=_optional_path(runtime.get("sqlite_path")),
        opend_host=_optional_text(runtime.get("opend_host")),
        opend_port=_optional_int(runtime.get("opend_port")),
        market_timezone=str(runtime.get("market_timezone", "America/New_York")),
        exchange_calendar=str(runtime.get("exchange_calendar", "XNYS")),
        poll_seconds=max(0.1, float(runtime.get("poll_seconds", 30.0))),
        catch_up_seconds=max(0.0, float(runtime.get("catch_up_seconds", 3600.0))),
        intraday_catch_up_seconds=max(
            0.0, float(runtime.get("intraday_catch_up_seconds", 600.0))
        ),
        intraday_sqlite_path=_optional_path(runtime.get("intraday_sqlite_path")),
        intraday_raw_payload_dir=_optional_path(runtime.get("intraday_raw_payload_dir")),
        options=(
            dict(runtime.get("options", {}))
            if isinstance(runtime.get("options", {}), dict)
            else None
        ),
        publisher=(
            dict(runtime.get("publisher", {}))
            if isinstance(runtime.get("publisher", {}), dict)
            else None
        ),
    )


def _optional_path(value: object) -> Path | None:
    text = str(value or "").strip()
    return Path(text).expanduser() if text else None


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_int(value: object) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _parse_task(value: object, index: int, config_path: Path) -> CollectionTask:
    if not isinstance(value, dict):
        raise ValueError(f"{config_path}: tasks[{index}] must be a table")
    name = _required_text(value, "name", config_path, prefix=f"tasks[{index}]")
    schedule = _required_text(value, "schedule", config_path, prefix=f"tasks[{index}]")
    symbols = value.get("symbols")
    if not isinstance(symbols, list) or not symbols:
        raise ValueError(f"{config_path}: tasks[{index}].symbols must be a non-empty list")
    normalized = tuple(dict.fromkeys(str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()))
    if not normalized:
        raise ValueError(f"{config_path}: tasks[{index}].symbols must contain text")
    operation = str(value.get("operation", "instrument_cards")).strip()
    if operation not in ALLOWED_OPERATIONS:
        raise ValueError(
            f"{config_path}: tasks[{index}].operation must be one of "
            f"{', '.join(sorted(ALLOWED_OPERATIONS))}"
        )
    return CollectionTask(
        name=name,
        enabled=bool(value.get("enabled", True)),
        schedule=schedule,
        symbols=normalized,
        operation=operation,
        delay_seconds=max(0.0, float(value.get("delay_seconds", 0.0))),
        params=dict(value.get("params", {})) if isinstance(value.get("params", {}), dict) else None,
    )


def _required_text(
    value: dict[str, Any], key: str, config_path: Path, *, prefix: str = "runtime"
) -> str:
    text = str(value.get(key, "")).strip()
    if not text:
        raise ValueError(f"{config_path}: {prefix}.{key} is required")
    return text
