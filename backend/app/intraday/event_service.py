from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.decision_harness.contracts import content_sha256
from app.intraday.equities import _aware_utc, build_intraday_projection
from app.intraday.events import _STATE_SCHEMA, evaluate
from app.models.intraday_events import (
    IntradayEventIngestCursorModel,
    IntradaySetupStateModel,
)
from app.repositories.intraday_events import IntradayEventRepository


PROCESSOR_ID = "intraday-events-live-v1"


class SourcePathChangedError(ValueError):
    """Raised when live processor state is pointed at another sidecar."""


def run_once(
    *,
    source_db_path: str | Path,
    session_factory: Callable[[], Session],
    max_slots: int = 12,
    now: datetime,
    spec: Mapping[str, Any] | None = None,
    processor_id: str = PROCESSOR_ID,
) -> dict[str, Any]:
    """Read bounded sidecar captures and persist each source slot atomically."""

    if isinstance(max_slots, bool) or not isinstance(max_slots, int) or max_slots < 1:
        raise ValueError("max_slots must be a positive integer")
    now_utc = _as_utc(now)
    normalized_processor_id = str(processor_id).strip()
    if not normalized_processor_id:
        raise ValueError("processor_id must not be empty")
    source_identity = _normalized_source_path(source_db_path)
    result: dict[str, Any] = {
        "status": "idle",
        "source_path_identity": source_identity,
        "scanned_slots": 0,
        "processed_slots": 0,
        "skipped_slots": [],
        "last_cursor": None,
        "backlog_remaining": False,
        "events_created": 0,
        "event_count": 0,
        "setups_expired": 0,
        "capabilities_summary": {},
        "errors": [],
    }

    try:
        with session_factory() as session:
            repository = IntradayEventRepository(session)
            existing_cursor = repository.get_cursor(normalized_processor_id)
            if (
                existing_cursor is not None
                and existing_cursor.source_path_identity != source_identity
            ):
                raise SourcePathChangedError(
                    "intraday event processor cannot reuse state with a different source path"
                )
            cursor_data = _cursor_snapshot(existing_cursor, source_identity)

        source_rows, source_backlog, source_error = _read_source_rows(
            source_identity,
            cursor_data=cursor_data,
            max_slots=max_slots,
        )
        if source_error:
            result["errors"].append(source_error)
            result["status"] = "error"
            try:
                result["setups_expired"] = _expire_due_setups(
                    session_factory,
                    processor_id=normalized_processor_id,
                    source_identity=source_identity,
                    cursor_data=cursor_data,
                    now=now_utc,
                    spec=spec,
                    capabilities=result["capabilities_summary"],
                    result=result,
                )
                cursor_data = _persist_source_error(
                    session_factory,
                    processor_id=normalized_processor_id,
                    source_identity=source_identity,
                    message=str(source_error["message"]),
                    now=now_utc,
                )
            except Exception as exc:
                if isinstance(exc, SourcePathChangedError):
                    raise
                result["errors"].append({"message": str(exc)})
            result["last_cursor"] = _public_cursor(cursor_data)
            result["event_count"] = result["events_created"]
            return result

        seen_slots = set(cursor_data["seen_source_slots_json"])
        selected_rows = [
            row for row in source_rows if _source_slot_key(row) not in seen_slots
        ][:max_slots]
        result["scanned_slots"] = len(selected_rows)
        result["backlog_remaining"] = source_backlog
        cursor_data["backlog_remaining"] = result["backlog_remaining"]
        normalized_processed_tasks: set[str] = set()

        for row in selected_rows:
            try:
                outcome = _consume_source_row(
                    session_factory,
                    row,
                    processor_id=normalized_processor_id,
                    source_identity=source_identity,
                    now=now_utc,
                    spec=spec,
                    backlog_remaining=result["backlog_remaining"],
                )
                cursor_data = outcome["cursor_data"]
                if outcome["kind"] == "processed":
                    source_data = (row.get("contract") or {}).get("data")
                    if isinstance(source_data, Mapping) and source_data.get("schema_version") == "moomoo-intraday-equities-refs-v1":
                        normalized_processed_tasks.add(str(row["task_name"]))
                    result["processed_slots"] += 1
                    result["events_created"] += outcome["events_created"]
                    _merge_capabilities(
                        result["capabilities_summary"], outcome["capabilities"]
                    )
                elif outcome["kind"] == "skipped":
                    result["skipped_slots"].append(outcome["skipped_slot"])
            except Exception as exc:
                result["errors"].append(
                    {"capture_id": row["capture_id"], "message": str(exc)}
                )
                result["status"] = "error"
                break

        if result["status"] != "error":
            result["setups_expired"] = _expire_due_setups(
                session_factory,
                processor_id=normalized_processor_id,
                source_identity=source_identity,
                cursor_data=cursor_data,
                now=now_utc,
                spec=spec,
                capabilities=result["capabilities_summary"],
                result=result,
            )
            if normalized_processed_tasks:
                from app.intraday.event_retention import prune_event_history

                with session_factory() as session:
                    with session.begin():
                        _begin_write_transaction(session, normalized_processor_id)
                        cursor = IntradayEventRepository(session).get_cursor(normalized_processor_id)
                        if cursor is None or cursor.source_path_identity != source_identity:
                            raise SourcePathChangedError("event retention source identity changed")
                        result["event_retention"] = prune_event_history(
                            session, processor_id=normalized_processor_id, now=now_utc,
                            task_names=sorted(normalized_processed_tasks), dry_run=False,
                            market_calendar=str((spec or {}).get("market_calendar", "XNYS")),
                        )

        result["last_cursor"] = _public_cursor(cursor_data)
        result["event_count"] = result["events_created"]
        result["backlog_remaining"] = bool(cursor_data.get("backlog_remaining"))
        if result["status"] != "error":
            if result["skipped_slots"] or result["backlog_remaining"]:
                result["status"] = "partial"
            elif result["processed_slots"] or result["setups_expired"]:
                result["status"] = "ok"
            else:
                result["status"] = "idle"
        return result
    except Exception as exc:
        if isinstance(exc, SourcePathChangedError):
            raise
        result["status"] = "error"
        result["errors"].append({"message": str(exc)})
        return result


def _read_source_rows(
    source_path_identity: str,
    *,
    cursor_data: Mapping[str, Any],
    max_slots: int,
) -> tuple[list[dict[str, Any]], bool, dict[str, Any] | None]:
    path = Path(source_path_identity)
    if not path.is_file():
        return [], False, {"message": f"sidecar 数据库不存在：{path}"}
    database_uri = f"file:{quote(str(path), safe='/')}?mode=ro"
    try:
        connection = sqlite3.connect(database_uri, uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        try:
            base_query = (
                "SELECT scheduled.task_name, scheduled.scheduled_at, scheduled.capture_id, "
                "scheduled.kind AS scheduled_kind, scheduled.completed, scheduled.updated_at, "
                "raw.schema_version AS raw_schema_version, raw.kind AS raw_kind, "
                "raw.captured_at AS stored_at, raw.payload_json "
                "FROM scheduled_captures AS scheduled "
                "LEFT JOIN raw_captures AS raw ON raw.capture_id = scheduled.capture_id "
            )
            cursor_order = _cursor_order(cursor_data)
            backlog = False
            if cursor_order is None:
                raw_rows = list(
                    connection.execute(
                        base_query
                        + "WHERE scheduled.kind = ? "
                        + "ORDER BY julianday(scheduled.scheduled_at) ASC, scheduled.task_name ASC "
                        + "LIMIT ?",
                        ("capture_intraday_equities", max_slots),
                    ).fetchall()
                )
            else:
                cursor_time, cursor_task = cursor_order
                cursor_text = cursor_time.isoformat()
                raw_rows = list(
                    connection.execute(
                        base_query
                        + "WHERE scheduled.kind = ? AND ("
                        + "julianday(scheduled.scheduled_at) > julianday(?) OR "
                        + "(julianday(scheduled.scheduled_at) = julianday(?) "
                        + "AND scheduled.task_name > ?)) "
                        + "ORDER BY julianday(scheduled.scheduled_at) ASC, scheduled.task_name ASC "
                        + "LIMIT ?",
                        (
                            "capture_intraday_equities",
                            cursor_text,
                            cursor_text,
                            cursor_task,
                            max_slots,
                        ),
                    ).fetchall()
                )
            if raw_rows:
                last_row = raw_rows[-1]
                backlog = _source_rows_remain_after(
                    connection,
                    scheduled_at=last_row["scheduled_at"],
                    task_name=str(last_row["task_name"] or ""),
                )
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return [], False, {"message": f"无法只读 sidecar 调度槽：{exc}"}

    rows: list[dict[str, Any]] = []
    for raw_row in raw_rows:
        try:
            scheduled_at = _aware_utc(raw_row["scheduled_at"], "scheduled_at")
        except (TypeError, ValueError):
            scheduled_at = None
        try:
            contract = json.loads(str(raw_row["payload_json"])) if raw_row["payload_json"] else None
        except (TypeError, json.JSONDecodeError):
            contract = None
        if not isinstance(contract, dict):
            contract = None
        reference_error = None
        if contract is not None:
            try:
                from app.intraday.references import hydrate_capture_from_path

                contract = hydrate_capture_from_path(path, contract)
            except (sqlite3.Error, ValueError, TypeError, KeyError) as exc:
                reference_error = str(exc)
        data = contract.get("data") if contract else None
        digest = str(contract.get("content_sha256") or "") if contract else ""
        rows.append(
            {
                "task_name": str(raw_row["task_name"] or ""),
                "capture_id": str(raw_row["capture_id"] or ""),
                "scheduled_at": scheduled_at,
                "scheduled_kind": str(raw_row["scheduled_kind"] or ""),
                "completed": bool(raw_row["completed"]),
                "updated_at": raw_row["updated_at"],
                "raw_schema_version": str(raw_row["raw_schema_version"] or ""),
                "raw_kind": str(raw_row["raw_kind"] or ""),
                "stored_at": raw_row["stored_at"],
                "contract": contract,
                "reference_error": reference_error,
                "content_sha256": digest,
                "data_hash_valid": bool(
                    isinstance(data, Mapping) and digest and content_sha256(data) == digest
                ),
            }
        )
    rows.sort(key=_row_order)
    return rows, backlog, None


def _source_rows_remain_after(
    connection: sqlite3.Connection,
    *,
    scheduled_at: Any,
    task_name: str,
) -> bool:
    if not isinstance(scheduled_at, str) or _safe_aware_utc(scheduled_at) is None:
        return True
    row = connection.execute(
        "SELECT 1 FROM scheduled_captures "
        "WHERE kind = ? AND ("
        "julianday(scheduled_at) > julianday(?) OR "
        "(julianday(scheduled_at) = julianday(?) AND task_name > ?)) "
        "LIMIT 1",
        ("capture_intraday_equities", scheduled_at, scheduled_at, task_name),
    ).fetchone()
    return row is not None


def _projection_for_row(
    row: Mapping[str, Any],
    now: datetime,
    *,
    market_timezone: str = "America/New_York",
    market_calendar: str = "XNYS",
) -> tuple[dict[str, Any] | None, str | None]:
    contract = row.get("contract")
    scheduled_at = row.get("scheduled_at")
    if row.get("reference_error"):
        return None, f"日内数据引用不可用：{row['reference_error']}"
    if not isinstance(contract, Mapping):
        return None, "调度槽没有有效的 raw capture JSON 对象。"
    if not isinstance(scheduled_at, datetime):
        return None, "调度槽 scheduled_at 缺少有效时区。"
    if str(row.get("capture_id") or "") != str(contract.get("capture_id") or ""):
        return None, "调度槽 capture_id 与 raw capture 不一致。"
    if str(row.get("task_name") or "") != str(contract.get("task_name") or ""):
        return None, "调度 ledger task_name 与 raw capture 不一致。"
    if str(row.get("scheduled_kind") or "") != "capture_intraday_equities":
        return None, "调度槽 kind 不匹配。"
    if str(row.get("raw_kind") or "") != str(contract.get("kind") or ""):
        return None, "raw capture kind 与存储 envelope 不一致。"
    if str(row.get("raw_schema_version") or "") != str(contract.get("schema_version") or ""):
        return None, "raw capture schema 与存储 envelope 不一致。"
    try:
        envelope_scheduled_at = _aware_utc(contract.get("scheduled_at"), "scheduled_at")
        captured_at = _aware_utc(contract.get("captured_at"), "captured_at")
    except (TypeError, ValueError) as exc:
        return None, str(exc)
    if envelope_scheduled_at != scheduled_at:
        return None, "调度槽时间与 raw capture envelope 不一致。"
    stored_at = _safe_aware_utc(row.get("stored_at"))
    if stored_at is None:
        return None, "raw capture stored_at 缺少有效时区。"
    if stored_at < captured_at:
        return None, "raw capture 存储时间早于 envelope captured_at。"
    if not row.get("data_hash_valid"):
        return None, "raw capture data content hash 不匹配。"
    try:
        contract_with_provenance = dict(contract)
        contract_with_provenance["_stored_at"] = stored_at.isoformat()
        return (
            build_intraday_projection(
                contract_with_provenance,
                now=now,
                market_timezone=market_timezone,
                market_calendar=market_calendar,
            ),
            None,
        )
    except (TypeError, ValueError, KeyError) as exc:
        return None, str(exc)


def _consume_source_row(
    session_factory: Callable[[], Session],
    row: Mapping[str, Any],
    *,
    processor_id: str,
    source_identity: str,
    now: datetime,
    spec: Mapping[str, Any] | None,
    backlog_remaining: bool,
) -> dict[str, Any]:
    projection_spec = spec if isinstance(spec, Mapping) else {}
    projection, projection_error = _projection_for_row(
        row,
        now,
        market_timezone=str(projection_spec.get("market_timezone") or "America/New_York"),
        market_calendar=str(projection_spec.get("market_calendar") or "XNYS"),
    )
    scheduled_at = row.get("scheduled_at")
    if not isinstance(scheduled_at, datetime):
        projection_error = projection_error or "调度槽 scheduled_at 缺少有效时区。"
    slot_age = (now - scheduled_at).total_seconds() if isinstance(scheduled_at, datetime) else None
    if projection_error:
        skip_reason = ("invalid_source", projection_error)
    elif slot_age is not None and slot_age < 0:
        skip_reason = ("future_source_slot", "源槽时间晚于显式 now。")
    elif slot_age is not None and slot_age > 600:
        skip_reason = ("stale_source_slot", "实时源槽超过 600 秒新鲜度上限。")
    else:
        skip_reason = None

    slot_key = _source_slot_key(row)
    with session_factory() as session:
        with session.begin():
            _begin_write_transaction(session, processor_id)
            repository = IntradayEventRepository(session)
            locked_cursor = repository.get_cursor(processor_id)
            if (
                locked_cursor is not None
                and locked_cursor.source_path_identity != source_identity
            ):
                raise SourcePathChangedError(
                    "intraday event processor cannot reuse state with a different source path"
                )
            cursor_data = _cursor_snapshot(locked_cursor, source_identity)
            seen = list(cursor_data["seen_source_slots_json"])
            if slot_key in seen:
                return {"kind": "already_seen", "cursor_data": cursor_data}

            current_order = _cursor_order(cursor_data)
            if current_order is not None and _row_order(row) <= current_order:
                # Another consumer advanced this UTC/task cursor after this
                # process selected the bounded source page.
                return {"kind": "already_advanced", "cursor_data": cursor_data}

            _remember_source_slot(cursor_data, slot_key)
            cursor_data.update(
                {
                    "source_path_identity": source_identity,
                    "last_scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
                    "last_task_name": str(row.get("task_name") or ""),
                    "last_capture_id": str(row.get("capture_id") or ""),
                    "last_content_sha256": str(row.get("content_sha256") or ""),
                    "backlog_remaining": backlog_remaining,
                }
            )

            if skip_reason is not None:
                reason_code, message = skip_reason
                cursor_data["last_status"] = "partial"
                cursor_data["last_error"] = message
                repository.save_cursor(processor_id, cursor_data, now=now)
                return {
                    "kind": "skipped",
                    "cursor_data": cursor_data,
                    "skipped_slot": _skipped_slot(row, reason_code, message),
                }

            task_name = str(row.get("task_name") or "")
            prior_state = repository.get_state(processor_id, task_name)
            evaluated = evaluate(projection or {}, prior_state, spec, evaluated_at=now)
            cursor_data["last_status"] = "ok"
            cursor_data["last_error"] = None
            summary = dict(cursor_data.get("capabilities_summary_json") or {})
            _merge_capabilities(summary, evaluated["capabilities"])
            cursor_data["capabilities_summary_json"] = summary
            created = repository.save_slot(
                processor_id=processor_id,
                task_name=task_name,
                state=evaluated["state"],
                transitions=evaluated["transitions"],
                cursor_values=cursor_data,
                now=now,
            )
            return {
                "kind": "processed",
                "cursor_data": cursor_data,
                "events_created": created,
                "capabilities": evaluated["capabilities"],
            }


def _persist_source_error(
    session_factory: Callable[[], Session],
    *,
    processor_id: str,
    source_identity: str,
    message: str,
    now: datetime,
) -> dict[str, Any]:
    with session_factory() as session:
        with session.begin():
            _begin_write_transaction(session, processor_id)
            repository = IntradayEventRepository(session)
            cursor = repository.get_cursor(processor_id)
            if cursor is not None and cursor.source_path_identity != source_identity:
                raise SourcePathChangedError(
                    "intraday event processor cannot reuse state with a different source path"
                )
            cursor_data = _cursor_snapshot(cursor, source_identity)
            cursor_data.update(
                {
                    "last_status": "error",
                    "last_error": message,
                    "backlog_remaining": False,
                }
            )
            repository.save_cursor(processor_id, cursor_data, now=now)
            return cursor_data


def _begin_write_transaction(session: Session, processor_id: str) -> None:
    connection = session.connection()
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        return
    session.execute(
        select(IntradayEventIngestCursorModel)
        .where(IntradayEventIngestCursorModel.processor_id == processor_id)
        .with_for_update()
    ).first()


def _remember_source_slot(cursor_data: dict[str, Any], slot_key: str) -> None:
    seen = list(cursor_data.get("seen_source_slots_json") or [])
    if slot_key not in seen:
        seen.append(slot_key)
    cursor_data["seen_source_slots_json"] = seen[-128:]


def _expire_due_setups(
    session_factory: Callable[[], Session],
    *,
    processor_id: str,
    source_identity: str,
    cursor_data: dict[str, Any],
    now: datetime,
    spec: Mapping[str, Any] | None,
    capabilities: dict[str, Any],
    result: dict[str, Any],
) -> int:
    with session_factory() as session:
        state_rows = list(
            session.scalars(
                select(IntradaySetupStateModel.task_name).where(
                    IntradaySetupStateModel.processor_id == processor_id
                )
            )
        )
    expired_count = 0
    projection_spec = spec if isinstance(spec, Mapping) else {}
    market_timezone = str(projection_spec.get("market_timezone") or "America/New_York")
    current_date = now.astimezone(ZoneInfo(market_timezone)).date().isoformat()
    for task_name in state_rows:
        with session_factory() as session:
            with session.begin():
                _begin_write_transaction(session, processor_id)
                repository = IntradayEventRepository(session)
                latest_cursor = repository.get_cursor(processor_id)
                if (
                    latest_cursor is not None
                    and latest_cursor.source_path_identity != source_identity
                ):
                    raise SourcePathChangedError(
                        "intraday event processor cannot reuse state with a different source path"
                    )
                current_cursor_data = _cursor_snapshot(latest_cursor, source_identity)
                state = repository.get_state(processor_id, task_name)
                last_cutoff = _state_latest_cutoff(state)
                source_capture_id = ""
                source_content_hash = ""
                if str(current_cursor_data.get("last_task_name") or "") == task_name:
                    source_capture_id = str(current_cursor_data.get("last_capture_id") or "")
                    source_content_hash = str(
                        current_cursor_data.get("last_content_sha256") or ""
                    )
                projection = {
                    "source": {
                        "task_name": task_name,
                        "capture_id": source_capture_id,
                        "content_sha256": source_content_hash,
                        "scheduled_at": last_cutoff,
                        "trading_date": current_date,
                    },
                    "quality": {"minute_timestamp_boundary_verified": False},
                    "freshness": {},
                    "symbols": {},
                }
                evaluated = evaluate(projection, state, spec, evaluated_at=now)
                transitions = evaluated["transitions"]
                expiries = [item for item in transitions if item.get("status") == "expired"]
                if not expiries:
                    continue
                _merge_capabilities(capabilities, evaluated["capabilities"])
                _merge_capabilities(
                    current_cursor_data["capabilities_summary_json"],
                    evaluated["capabilities"],
                )
                current_cursor_data["last_status"] = "ok"
                current_cursor_data["last_error"] = None
                created = repository.save_slot(
                    processor_id=processor_id,
                    task_name=task_name,
                    state=evaluated["state"],
                    transitions=transitions,
                    cursor_values=current_cursor_data,
                    now=now,
                )
        cursor_data.clear()
        cursor_data.update(current_cursor_data)
        result["events_created"] += created
        expired_count += len(expiries)
    return expired_count


def _cursor_snapshot(cursor: Any, source_identity: str) -> dict[str, Any]:
    defaults = {
        "source_path_identity": source_identity,
        "last_scheduled_at": None,
        "last_task_name": None,
        "last_capture_id": None,
        "last_content_sha256": None,
        "seen_source_slots_json": [],
        "backlog_remaining": False,
        "last_status": "idle",
        "last_error": None,
        "capabilities_summary_json": {},
    }
    if cursor is None:
        return defaults
    defaults.update(
        {
            "source_path_identity": cursor.source_path_identity,
            "last_scheduled_at": cursor.last_scheduled_at,
            "last_task_name": cursor.last_task_name,
            "last_capture_id": cursor.last_capture_id,
            "last_content_sha256": cursor.last_content_sha256,
            "seen_source_slots_json": list(cursor.seen_source_slots_json or []),
            "backlog_remaining": bool(cursor.backlog_remaining),
            "last_status": cursor.last_status,
            "last_error": cursor.last_error,
            "capabilities_summary_json": dict(cursor.capabilities_summary_json or {}),
        }
    )
    return defaults


def _source_slot_key(row: Mapping[str, Any]) -> str:
    scheduled_at = row.get("scheduled_at")
    return "|".join(
        (
            str(row.get("task_name") or ""),
            scheduled_at.isoformat() if isinstance(scheduled_at, datetime) else "invalid",
            str(row.get("capture_id") or ""),
            str(row.get("content_sha256") or ""),
        )
    )


def _row_order(row: Mapping[str, Any]) -> tuple[datetime, str]:
    scheduled_at = row.get("scheduled_at")
    if not isinstance(scheduled_at, datetime):
        return datetime.min.replace(tzinfo=UTC), str(row.get("task_name") or "")
    return scheduled_at, str(row.get("task_name") or "")


def _cursor_order(cursor: Mapping[str, Any]) -> tuple[datetime, str] | None:
    scheduled_at = _safe_aware_utc(cursor.get("last_scheduled_at"))
    if scheduled_at is None:
        return None
    return scheduled_at, str(cursor.get("last_task_name") or "")


def _skipped_slot(
    row: Mapping[str, Any], reason_code: str, message: str
) -> dict[str, Any]:
    scheduled_at = row.get("scheduled_at")
    return {
        "task_name": row.get("task_name"),
        "scheduled_at": scheduled_at.isoformat() if isinstance(scheduled_at, datetime) else None,
        "capture_id": row.get("capture_id"),
        "reason_code": reason_code,
        "message": message,
    }


def _state_latest_cutoff(state: Mapping[str, Any]) -> str | None:
    setups = state.get("setups")
    if not isinstance(setups, list):
        return None
    cutoffs = [
        _safe_aware_utc(item.get("last_cutoff"))
        for item in setups
        if isinstance(item, Mapping)
    ]
    valid_cutoffs = [cutoff for cutoff in cutoffs if cutoff is not None]
    if not valid_cutoffs:
        return None
    return max(valid_cutoffs).isoformat()


def _merge_capabilities(target: dict[str, Any], values: Any) -> None:
    if not isinstance(values, Mapping):
        return
    for capability_id, raw in values.items():
        if not isinstance(raw, Mapping):
            continue
        entry = target.setdefault(
            str(capability_id),
            {"available_count": 0, "unavailable_count": 0, "reasons": {}},
        )
        count_key = "available_count" if raw.get("available") is True else "unavailable_count"
        entry[count_key] += 1
        reason = str(raw.get("reason_code") or "unknown")
        reasons = entry.setdefault("reasons", {})
        reasons[reason] = int(reasons.get(reason, 0)) + 1


def _public_cursor(cursor: Mapping[str, Any]) -> dict[str, Any] | None:
    if not cursor.get("last_scheduled_at"):
        return None
    return {
        "scheduled_at": cursor.get("last_scheduled_at"),
        "task_name": cursor.get("last_task_name"),
        "capture_id": cursor.get("last_capture_id"),
        "content_sha256": cursor.get("last_content_sha256"),
    }


def _normalized_source_path(value: str | Path) -> str:
    return str(Path(value).expanduser().resolve(strict=False))


def _safe_aware_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return _aware_utc(value, "timestamp")
    except (TypeError, ValueError):
        return None


def _as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(UTC)
