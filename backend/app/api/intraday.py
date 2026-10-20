from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_settings
from app.core.config import Settings
from app.intraday.event_service import PROCESSOR_ID
from app.intraday.equities import _aware_utc, build_intraday_projection
from app.repositories.intraday_events import IntradayEventRepository


router = APIRouter(prefix="/intraday", tags=["intraday"])
HEALTH_STALE_AFTER_SECONDS = 600


@router.get("/overview")
def intraday_overview(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    if not settings.intraday_equities_enabled:
        return _empty_projection("disabled", "盘中股票观察功能当前已关闭。")

    contract, error = _load_latest_capture(settings.sidecar_capture_database_path)
    if error is not None:
        return _empty_projection(
            "invalid_source",
            str(error["message"]),
            capture_id=error.get("capture_id"),
        )
    if contract is None:
        return _empty_projection("no_data", "本地 sidecar 中没有盘中股票采集槽。")

    try:
        return build_intraday_projection(
            contract,
            now=datetime.now(UTC),
            market_timezone=settings.market_timezone,
            market_calendar=settings.market_calendar,
        )
    except (TypeError, ValueError, KeyError) as exc:
        return _empty_projection(
            "invalid_source",
            str(exc),
            capture_id=str(contract.get("capture_id") or "") or None,
        )


@router.get("/symbols/{symbol}")
def intraday_symbol(
    symbol: str,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    normalized = symbol.strip().upper()
    if not settings.intraday_equities_enabled:
        return _empty_symbol("disabled", normalized, "盘中股票观察功能当前已关闭。")

    contract, error = _load_latest_capture(settings.sidecar_capture_database_path)
    if error is not None:
        return _empty_symbol(
            "invalid_source",
            normalized,
            str(error["message"]),
            capture_id=error.get("capture_id"),
        )
    if contract is None:
        return _empty_symbol("no_data", normalized, "本地 sidecar 中没有盘中股票采集槽。")

    try:
        projection = build_intraday_projection(
            contract,
            now=datetime.now(UTC),
            market_timezone=settings.market_timezone,
            market_calendar=settings.market_calendar,
        )
    except (TypeError, ValueError, KeyError) as exc:
        return _empty_symbol(
            "invalid_source",
            normalized,
            str(exc),
            capture_id=str(contract.get("capture_id") or "") or None,
        )

    item = projection.get("symbols", {}).get(normalized)
    if not isinstance(item, dict):
        return {
            "schema_version": projection["schema_version"],
            "formula_version": projection["formula_version"],
            "status": "not_found",
            "symbol": normalized,
            "source": projection["source"],
            "freshness": projection["freshness"],
            "bars": [],
            "features": {},
            "reasons": {},
            "warnings": [f"最新采集槽中没有 {normalized}。"],
        }
    return {
        "schema_version": projection["schema_version"],
        "formula_version": projection["formula_version"],
        "status": item["status"],
        "symbol": normalized,
        "source": projection["source"],
        "freshness": projection["freshness"],
        "coverage": item["coverage"],
        "as_of": item["as_of"],
        "role": item["role"],
        "quote": item["quote"],
        "bars": item["bars"],
        "features": item["features"],
        "reasons": item["reasons"],
        "warnings": projection["warnings"],
    }


@router.get("/health")
def intraday_event_health(
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    if not settings.intraday_events_enabled:
        return {
            "enabled": False,
            "status": "disabled",
            "cursor": None,
            "backlog_remaining": False,
            "last_error": None,
            "capabilities": {},
            "last_evaluated_at": None,
            "age_seconds": None,
            "stale": False,
            "stale_after_seconds": HEALTH_STALE_AFTER_SECONDS,
        }
    if not _primary_database_file_exists(settings):
        return {
            "enabled": True,
            "status": "idle",
            "cursor": None,
            "backlog_remaining": False,
            "last_error": None,
            "capabilities": {},
            "last_evaluated_at": None,
            "age_seconds": None,
            "stale": False,
            "stale_after_seconds": HEALTH_STALE_AFTER_SECONDS,
        }
    try:
        cursor = IntradayEventRepository(session).current_health(PROCESSOR_ID)
    except SQLAlchemyError as exc:
        return {
            "enabled": True,
            "status": "error",
            "cursor": None,
            "backlog_remaining": False,
            "last_error": str(exc),
            "capabilities": {},
            "last_evaluated_at": None,
            "age_seconds": None,
            "stale": False,
            "stale_after_seconds": HEALTH_STALE_AFTER_SECONDS,
        }
    if cursor is None:
        status = "idle"
        cursor_payload = None
        backlog = False
        last_error = None
        capabilities: dict[str, Any] = {}
        last_evaluated_at = None
        age_seconds = None
        stale = False
    else:
        raw_status = str(cursor.last_status or "idle")
        status = "error" if raw_status == "error" or cursor.last_error else (
            "idle" if raw_status == "idle" else "ok"
        )
        cursor_payload = {
            "scheduled_at": cursor.last_scheduled_at,
            "task_name": cursor.last_task_name,
            "capture_id": cursor.last_capture_id,
            "content_sha256": cursor.last_content_sha256,
        }
        backlog = bool(cursor.backlog_remaining)
        last_error = cursor.last_error
        capabilities = dict(cursor.capabilities_summary_json or {})
        evaluated_at = cursor.updated_at
        if evaluated_at.tzinfo is None:
            evaluated_at = evaluated_at.replace(tzinfo=UTC)
        last_evaluated_at = evaluated_at.astimezone(UTC).isoformat()
        age_seconds = max(0.0, (datetime.now(UTC) - evaluated_at.astimezone(UTC)).total_seconds())
        stale = age_seconds > HEALTH_STALE_AFTER_SECONDS
        if stale and status == "ok":
            status = "idle"
    return {
        "enabled": True,
        "status": status,
        "cursor": cursor_payload,
        "backlog_remaining": backlog,
        "last_error": last_error,
        "capabilities": capabilities,
        "last_evaluated_at": last_evaluated_at,
        "age_seconds": age_seconds,
        "stale": stale,
        "stale_after_seconds": HEALTH_STALE_AFTER_SECONDS,
    }


@router.get("/events")
def intraday_events(
    after_id: int | None = Query(default=None, ge=0),
    symbol: str | None = Query(default=None, min_length=1, max_length=32),
    strategy: str | None = Query(default=None, min_length=2, max_length=8),
    status: str | None = Query(default=None, min_length=1, max_length=24),
    limit: int = Query(default=50, ge=1, le=100),
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    if not settings.intraday_events_enabled:
        return {
            "enabled": False,
            "status": "disabled",
            "events": [],
            "next_after_id": after_id,
            "has_more": False,
            "limit": limit,
        }
    if strategy is not None and strategy.upper() not in {"S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"}:
        raise HTTPException(status_code=400, detail="strategy must be S1 through S8")
    if not _primary_database_file_exists(settings):
        return {
            "enabled": True,
            "status": "idle",
            "events": [],
            "next_after_id": after_id,
            "has_more": False,
            "limit": limit,
        }

    repository = IntradayEventRepository(session)
    try:
        if after_id is None:
            rows = repository.event_tail(
                processor_id=PROCESSOR_ID,
                limit=limit,
                symbol=symbol,
                strategy=strategy,
                status=status,
            )
            has_more = False
        else:
            rows = repository.list_events(
                processor_id=PROCESSOR_ID,
                after_id=after_id,
                limit=limit + 1,
                symbol=symbol,
                strategy=strategy,
                status=status,
            )
            has_more = len(rows) > limit
            rows = rows[:limit]
        latest_statuses = repository.event_statuses(
            PROCESSOR_ID,
            [row.setup_id for row in rows],
        )
    except SQLAlchemyError as exc:
        return {
            "enabled": True,
            "status": "error",
            "events": [],
            "next_after_id": after_id,
            "has_more": False,
            "limit": limit,
            "error": str(exc),
        }

    payloads = []
    for row in rows:
        payload = dict(row.payload_json or {})
        payload.update(
            {
                "id": row.id,
                "status": row.status,
                "current_status": latest_statuses.get(row.setup_id, row.status),
            }
        )
        payloads.append(payload)
    next_after_id = rows[-1].id if rows else after_id
    return {
        "enabled": True,
        "status": "ok",
        "events": payloads,
        "next_after_id": next_after_id,
        "has_more": has_more,
        "limit": limit,
    }


def _primary_database_file_exists(settings: Settings) -> bool:
    database_url = make_url(settings.database_url)
    if not database_url.drivername.startswith("sqlite"):
        return True
    if database_url.database in {None, "", ":memory:"}:
        return True
    return Path(database_url.database).expanduser().is_file()


def _load_latest_capture(path_value: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    path = Path(path_value).expanduser()
    if not path.is_file():
        return None, None

    database_uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"
    try:
        connection = sqlite3.connect(database_uri, uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        try:
            latest_row = connection.execute(
                "SELECT scheduled.task_name, scheduled.scheduled_at, "
                "scheduled.capture_id, scheduled.kind AS scheduled_kind, "
                "raw.schema_version AS raw_schema_version, raw.kind AS raw_kind, "
                "raw.captured_at AS stored_at, raw.payload_json "
                "FROM scheduled_captures AS scheduled "
                "LEFT JOIN raw_captures AS raw "
                "ON raw.capture_id = scheduled.capture_id "
                "WHERE scheduled.kind = ? "
                "ORDER BY julianday(scheduled.scheduled_at) DESC "
                "LIMIT 1",
                ("capture_intraday_equities",),
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return None, {"message": f"无法只读 sidecar 调度槽：{exc}"}

    if latest_row is None:
        return None, None
    capture_id = str(latest_row["capture_id"] or "")
    if latest_row["payload_json"] is None:
        return None, {"capture_id": capture_id, "message": "最新调度槽没有对应的 raw capture。"}
    try:
        contract = json.loads(str(latest_row["payload_json"]))
    except (TypeError, json.JSONDecodeError) as exc:
        return None, {"capture_id": capture_id, "message": f"最新 raw capture JSON 无效：{exc}"}
    if not isinstance(contract, dict):
        return None, {"capture_id": capture_id, "message": "最新 raw capture 必须是 JSON 对象。"}

    try:
        scheduled_at = _aware_utc(latest_row["scheduled_at"], "scheduled_at")
    except (TypeError, ValueError) as exc:
        return None, {"capture_id": capture_id, "message": str(exc)}
    if str(contract.get("capture_id") or "") != capture_id:
        return None, {"capture_id": capture_id, "message": "最新 raw capture ID 与调度槽不一致。"}
    if str(contract.get("schema_version") or "") != str(latest_row["raw_schema_version"] or ""):
        return None, {"capture_id": capture_id, "message": "最新 raw capture schema 与 envelope 不一致。"}
    if str(contract.get("kind") or "") != str(latest_row["raw_kind"] or ""):
        return None, {"capture_id": capture_id, "message": "最新 raw capture kind 与 envelope 不一致。"}
    if str(latest_row["scheduled_kind"] or "") != str(contract.get("kind") or ""):
        return None, {"capture_id": capture_id, "message": "最新调度槽 kind 与 capture envelope 不一致。"}
    if str(latest_row["task_name"] or "") != str(contract.get("task_name") or ""):
        return None, {"capture_id": capture_id, "message": "调度 ledger task_name 与 capture envelope 不一致。"}
    try:
        if _aware_utc(contract.get("scheduled_at"), "scheduled_at") != scheduled_at:
            return None, {"capture_id": capture_id, "message": "最新调度槽时间与 capture envelope 不一致。"}
        stored_at = _aware_utc(latest_row["stored_at"], "stored_at")
        _aware_utc(contract.get("captured_at"), "captured_at")
    except (TypeError, ValueError) as exc:
        return None, {"capture_id": capture_id, "message": str(exc)}
    contract["_stored_at"] = stored_at.isoformat()
    try:
        from app.intraday.references import hydrate_capture_from_path

        contract = hydrate_capture_from_path(path, contract)
    except (sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        return None, {"capture_id": capture_id, "message": f"日内数据引用不可用：{exc}"}
    return contract, None


def _empty_projection(
    status: str,
    message: str,
    *,
    capture_id: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "urus.intraday.projection.v1",
        "formula_version": "urus.intraday.equities.v1",
        "status": status,
        "source": {"capture_id": capture_id} if capture_id else None,
        "freshness": {"status": "unavailable", "age_seconds": None, "as_of": None},
        "quality": {
            "status": status,
            "minute_timestamp_boundary_verified": False,
        },
        "coverage": {"status": status},
        "symbols": {},
        "breadth": {},
        "warnings": [message],
    }


def _empty_symbol(
    status: str,
    symbol: str,
    message: str,
    *,
    capture_id: str | None = None,
) -> dict[str, Any]:
    payload = _empty_projection(status, message, capture_id=capture_id)
    payload.update(
        {
            "symbol": symbol,
            "bars": [],
            "features": {},
            "reasons": {},
        }
    )
    return payload
