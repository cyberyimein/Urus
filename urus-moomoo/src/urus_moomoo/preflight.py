"""Lightweight deployment check for the Urus observation publication path."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

import exchange_calendars as xcals

from urus_moomoo.config import CollectorConfig
from urus_moomoo.publisher import _get_json, fetch_collection_plan


def latest_completed_session(calendar: Any, now: datetime) -> date:
    current = now.astimezone(UTC)
    for offset in range(11):
        candidate = current.date() - timedelta(days=offset)
        if not calendar.is_session(candidate):
            continue
        close = calendar.session_close(candidate)
        close_at = close.to_pydatetime() if hasattr(close, "to_pydatetime") else close
        if close_at.tzinfo is None:
            close_at = close_at.replace(tzinfo=UTC)
        if close_at.astimezone(UTC) <= current:
            return candidate
    raise RuntimeError("Cannot find a completed market session for the observation preflight")


def run_preflight(
    config: CollectorConfig,
    *,
    now: datetime | None = None,
    calendar: Any | None = None,
    get: Callable[[str, float], tuple[int, Mapping[str, Any]]] = _get_json,
    fetch_plan: Callable[[str, str, float], Mapping[str, Any]] = fetch_collection_plan,
) -> dict[str, str | int]:
    """Check the real API route and plan contract without collecting market data."""

    publisher = config.publisher or {}
    api_url = str(publisher.get("api_url") or "").strip().rstrip("/")
    if not api_url:
        raise RuntimeError("Observation preflight requires runtime.publisher.api_url")
    enabled = {task.operation for task in config.tasks if task.enabled}
    if not {"instrument_cards", "options_snapshot"} <= enabled:
        raise RuntimeError("Observation preflight requires both publishable collection tasks")
    timeout = min(30.0, max(1.0, float(publisher.get("timeout_seconds", 15.0))))
    status, health = get(f"{api_url}/api/health", timeout)
    if status != 200 or health.get("status") != "ok" or health.get("database") != "ok":
        raise RuntimeError(f"Urus API health check failed: HTTP {status}")

    session_date = latest_completed_session(
        calendar or xcals.get_calendar(config.exchange_calendar), now or datetime.now(UTC)
    )
    plan = fetch_plan(api_url, session_date.isoformat(), timeout)
    if plan.get("schema_version") != "urus.observation.collection_plan.v1":
        raise RuntimeError("Observation collection plan has an unexpected schema")
    if plan.get("trading_date") != session_date.isoformat():
        raise RuntimeError("Observation collection plan has the wrong trading date")
    if not isinstance(plan.get("plan_id"), str) or len(plan["plan_id"]) != 64:
        raise RuntimeError("Observation collection plan is missing its identity")
    for field in ("group_ids", "group_version_ids", "market_symbols", "options_symbols"):
        value = plan.get(field)
        if not isinstance(value, list) or not value or not all(isinstance(x, str) and x for x in value):
            raise RuntimeError(f"Observation collection plan has no valid {field}")
    if len(plan["group_ids"]) != len(plan["group_version_ids"]):
        raise RuntimeError("Observation collection plan group versions do not match groups")
    return {
        "trading_date": session_date.isoformat(),
        "group_count": len(plan["group_ids"]),
        "market_symbol_count": len(plan["market_symbols"]),
        "options_symbol_count": len(plan["options_symbols"]),
    }
