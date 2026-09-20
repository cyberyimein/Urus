from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.core.errors import AppError
from app.decision_harness.contracts import content_sha256
from app.decision_harness.cross_section import CrossSectionService
from app.models.observation import ObservationGroupVersionModel
from app.repositories.observation import ObservationRepository


DISCOVERY_SCHEMA = "urus.discovery_projection.v1"


def build_discovery_projection(session, run_id: str) -> dict[str, Any]:
    """Build the first read-only discovery slice from existing projections."""

    return DiscoveryProjectionService(session).build(run_id)


class DiscoveryProjectionService:
    """Expose read-only indicator and group phase facts from frozen observations."""

    def __init__(self, session) -> None:
        self.repository = ObservationRepository(session)
        self.cross_section = CrossSectionService(session)

    def build(self, run_id: str) -> dict[str, Any]:
        run = self.repository.get_run(run_id)
        if run is None:
            raise AppError(
                "找不到 Observation Run",
                code="observation_run_not_found",
                status_code=404,
            )

        events_by_key: dict[str, dict[str, Any]] = {}
        comparisons: list[dict[str, Any]] = []
        warnings: list[str] = []
        partial_projection = False
        for definition in self.cross_section.indicator_catalog():
            projection = self.cross_section.indicator_projection(run.id, str(definition["id"]))
            comparisons.append(dict(projection.get("comparison") or {}))
            quality = dict(projection.get("quality") or {})
            partial_projection = partial_projection or quality.get("status") != "ok"
            warnings.extend(str(item) for item in quality.get("warnings") or [] if item)
            for row in projection.get("rows") or []:
                if not row.get("transition") or not row.get("previous_trading_date"):
                    continue
                event_key = _indicator_fact_key(run.id, definition, row)
                event = events_by_key.get(event_key)
                if event is None:
                    event = _indicator_event(run, definition, row, event_key)
                    events_by_key[event_key] = event
                else:
                    _merge_group(event, row)
                    _merge_evidence_context(event, row)

        group_events = _group_phase_events(run, self.repository)
        comparison = _comparison_summary(run, comparisons, group_events)
        if comparison["status"] == "unavailable":
            warnings.append(
                "当前 Observation Run 没有可比的前序冻结快照，未生成跨日变化。"
            )
        else:
            warnings.append(
                "当前仅透传前序 snapshot/dataset 身份；"
                "compare run 索引留待后续阶段。"
            )
        warnings.append("strategy_transition 留待后续阶段。")
        events = sorted(
            [*group_events, *events_by_key.values()],
            key=_event_sort_key,
        )
        quality = {
            "status": (
                "partial"
                if partial_projection or comparison["status"] == "partial"
                else "ok"
            ),
            "warnings": _unique_strings(warnings),
            "capabilities": {
                "indicator_transition": "available",
                "strategy_transition": "deferred",
                "group_phase_change": "available",
            },
        }
        result: dict[str, Any] = {
            "schema_version": DISCOVERY_SCHEMA,
            "run": {
                "run_id": run.id,
                "status": run.status,
                "trading_date": run.trading_date.isoformat(),
            },
            "comparison": comparison,
            "events": events,
            "quality": quality,
        }
        result["content_sha256"] = content_sha256(result)
        return result


_GROUP_STAGE_ORDER = {
    "deteriorating": 0,
    "broad_strength_start": 1,
    "broad_strength_continue": 2,
    "improving": 3,
    "narrow_leadership": 4,
}


def _group_phase_events(run, repository: ObservationRepository) -> list[dict[str, Any]]:
    """Project qualified theme snapshots in the exact current run manifest."""

    session = getattr(repository, "session", None)
    manifest = list((getattr(run, "payload_json", None) or {}).get("group_snapshots") or [])
    if session is None or not manifest:
        return []

    events: list[dict[str, Any]] = []
    for item in manifest:
        if not isinstance(item, dict) or item.get("status") != "succeeded":
            continue
        snapshot_id = item.get("snapshot_id")
        if not snapshot_id:
            continue
        current_snapshot = repository.get_snapshot(str(snapshot_id))
        if current_snapshot is None:
            continue
        if item.get("group_id") and str(item["group_id"]) != str(current_snapshot.group_id):
            continue
        if item.get("group_version_id") and str(item["group_version_id"]) != str(current_snapshot.group_version_id):
            continue
        if item.get("dataset_id") and str(item["dataset_id"]) != str(current_snapshot.dataset_id):
            continue

        group_version = session.get(ObservationGroupVersionModel, str(current_snapshot.group_version_id))
        tags = {str(tag).strip().lower() for tag in (group_version.tags or [])} if group_version else set()
        if (
            group_version is None
            or "theme" not in tags
            or str(current_snapshot.group_id) == "universe-core-watchlist"
            or str(group_version.group_id) in {"universe-core-watchlist", "indicator-recommendation"}
            or "indicator-recommendation" in tags
            or str(group_version.display_name or "") == "指标推荐"
            or len(list(group_version.symbols or [])) <= 1
        ):
            continue

        payload = dict(current_snapshot.payload_json or {})
        decision = dict(payload.get("group_decision") or {})
        support = dict(decision.get("sample_support") or payload.get("sample_support") or {})
        valid_count = _integer(support.get("valid_symbol_count"))
        requested_count = _integer(support.get("requested_symbol_count"))
        coverage = _number(support.get("coverage"))
        if (
            decision.get("rank_eligible") is not True
            or valid_count is None
            or valid_count < 4
            or coverage is None
            or coverage < 0.75
        ):
            continue

        changes = dict(payload.get("changes") or {})
        previous_trading_date = changes.get("previous_trading_date")
        if not previous_trading_date:
            continue
        previous_snapshot = repository.previous_snapshot(
            group_id=str(current_snapshot.group_id),
            group_version_id=str(current_snapshot.group_version_id),
            trading_date=current_snapshot.trading_date,
        )
        if previous_snapshot is None:
            continue
        if _date_string(previous_snapshot.trading_date) != _date_string(previous_trading_date):
            continue

        group_state = dict(changes.get("group_state") or {})
        previous_state = group_state.get("from")
        current_state = group_state.get("to") or decision.get("state")
        if previous_state is None:
            previous_payload = dict(previous_snapshot.payload_json or {})
            previous_decision = dict(previous_payload.get("group_decision") or {})
            previous_state = previous_decision.get("state")
        impulse = str(decision.get("impulse") or "")
        if group_state.get("changed") is not True and impulse not in {"improving", "deteriorating"}:
            continue
        stage = _group_phase_stage(str(current_state or ""), str(previous_state or ""), impulse)
        if stage is None:
            continue
        events.append(
            _group_phase_event(
                run,
                current_snapshot,
                previous_snapshot,
                group_version,
                decision,
                support,
                changes,
                str(previous_state or ""),
                str(current_state or ""),
                stage,
            )
        )
    return events


def _group_phase_stage(
    current_state: str,
    previous_state: str,
    impulse: str,
) -> tuple[str, str] | None:
    if current_state == "narrow_leadership":
        return "narrow_leadership", "少数龙头"
    if impulse == "deteriorating":
        return "deteriorating", "开始恶化"
    if current_state == "broad_strength" and previous_state != "broad_strength":
        return "broad_strength_start", "参与扩散至广泛强势"
    if current_state == "broad_strength" and impulse == "improving":
        return "broad_strength_continue", "广泛强势继续扩散"
    if impulse == "improving":
        return "improving", "边际改善"
    return None


def _group_phase_event(
    run,
    current_snapshot,
    previous_snapshot,
    group_version,
    decision: dict[str, Any],
    support: dict[str, Any],
    changes: dict[str, Any],
    previous_state: str,
    current_state: str,
    stage: tuple[str, str],
) -> dict[str, Any]:
    stage_id, stage_label = stage
    group_id = str(current_snapshot.group_id)
    group_name = str(group_version.display_name or group_id)
    event_key = content_sha256(
        {
            "kind": "group_phase_change",
            "run_id": run.id,
            "group_id": group_id,
            "group_version_id": current_snapshot.group_version_id,
            "snapshot_id": current_snapshot.id,
            "previous_snapshot_id": previous_snapshot.id,
            "stage": stage_id,
            "from": previous_state,
            "to": current_state,
        }
    )
    event_id = f"group_phase_change:{event_key[:32]}"
    payload = dict(current_snapshot.payload_json or {})
    features = dict(payload.get("features") or {})
    breadth = _nested_value(features, "breadth", "above_ma20")
    relative = _nested_value(features, "relative_strength", "median_excess_20d")
    participation = decision.get("participation")
    if participation is None:
        participation = breadth
    metrics = {
        "participation": participation,
        "breadth_ma20": breadth,
        "breadth_ma20_delta": changes.get("breadth_ma20_delta"),
        "relative_20d": relative,
        "relative_20d_delta": changes.get("relative_20d_delta_percent"),
        "leader_concentration": features.get("leader_concentration"),
        "valid_symbol_count": support.get("valid_symbol_count"),
        "requested_symbol_count": support.get("requested_symbol_count"),
        "coverage": support.get("coverage"),
        "leaders_added": _string_list(changes.get("leaders_added")),
        "leaders_removed": _string_list(changes.get("leaders_removed")),
    }
    return {
        "event_id": event_id,
        "kind": "group_phase_change",
        "group": {"id": group_id, "name": group_name},
        "groups": [{"id": group_id, "name": group_name}],
        "current": {
            "run_id": run.id,
            "trading_date": _date_string(current_snapshot.trading_date),
            "snapshot_ids": [current_snapshot.id],
            "dataset_ids": [current_snapshot.dataset_id],
        },
        "compare": {
            "run_id": None,
            "trading_date": _date_string(previous_snapshot.trading_date),
            "snapshot_ids": [previous_snapshot.id],
            "dataset_ids": [previous_snapshot.dataset_id],
        },
        "before": {
            "value": None,
            "display_value": None,
            "state": previous_state or None,
            "state_label": _group_state_label(previous_state),
        },
        "after": {
            "value": None,
            "display_value": None,
            "state": current_state or None,
            "state_label": _group_state_label(current_state),
        },
        "stage": {"id": stage_id, "label": stage_label},
        "metrics": metrics,
        "duration": {"status": "unavailable", "label": "持续时长未知"},
        "transition": {
            "from": previous_state or None,
            "to": current_state or None,
            "impulse": decision.get("impulse"),
        },
        "priority_reason": f"{group_name}：{stage_label}。",
        "route": {
            "name": "group-observation",
            "params": {"groupId": group_id},
            "query": {"run": run.id, "discoveryEvent": event_id},
        },
    }


def _nested_value(value: dict[str, Any], *path: str) -> Any:
    current: Any = value
    for key in path:
        current = current.get(key) if isinstance(current, dict) else None
    return current


def _integer(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _number(value: object) -> float | int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return sorted({str(item) for item in value if str(item).strip()})


def _date_string(value: object) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _group_state_label(state: str | None) -> str | None:
    if not state:
        return None
    return {
        "broad_strength": "广泛走强",
        "broad_weakness": "广泛走弱",
        "narrow_leadership": "少数领涨",
        "mixed": "内部混合",
        "insufficient_data": "数据不足",
    }.get(state, state)


def _indicator_fact_key(run_id: str, definition: dict[str, Any], row: dict[str, Any]) -> str:
    indicator_id = str(definition["id"])
    benchmark = ()
    if indicator_id == "relative_strength_20d":
        benchmark = tuple(sorted(str(item).upper() for item in row.get("benchmark_symbols") or []))
    return content_sha256(
        {
            "kind": "indicator_transition",
            "run_id": run_id,
            "indicator_id": indicator_id,
            "symbol": row.get("symbol"),
            "dataset_id": row.get("dataset_id"),
            "previous_dataset_id": row.get("previous_dataset_id"),
            "trading_date": row.get("transition_date"),
            "previous_trading_date": row.get("previous_trading_date"),
            "value": row.get("value"),
            "previous_value": row.get("previous_value"),
            "state": row.get("state"),
            "previous_state": row.get("previous_state"),
            "transition": row.get("transition"),
            "benchmark": benchmark,
        }
    )


def _indicator_event(
    run,
    definition: dict[str, Any],
    row: dict[str, Any],
    event_key: str,
) -> dict[str, Any]:
    indicator_id = str(definition["id"])
    symbol = str(row.get("symbol") or "")
    event_id = f"indicator_transition:{event_key[:32]}"
    current_dataset_ids = [value for value in (row.get("dataset_id"),) if value]
    query = {
        "run": run.id,
        "discoveryEvent": event_id,
        "lens": indicator_id,
    }
    if len(current_dataset_ids) == 1:
        query["dataset"] = current_dataset_ids[0]
    return {
        "event_id": event_id,
        "kind": "indicator_transition",
        "symbol": symbol,
        "lens": {
            "id": indicator_id,
            "name": definition.get("name", indicator_id),
            "version": definition.get("version") or definition.get("feature_version"),
        },
        "groups": [
            {
                "id": str(row.get("group_id") or ""),
                "name": str(row.get("group_name") or row.get("group_id") or ""),
            }
        ],
        "current": {
            "run_id": run.id,
            "trading_date": run.trading_date.isoformat(),
            "snapshot_ids": [row.get("snapshot_id")],
            "dataset_ids": [row.get("dataset_id")],
        },
        "compare": {
            "run_id": None,
            "trading_date": row.get("previous_trading_date"),
            "snapshot_ids": [row.get("previous_snapshot_id")],
            "dataset_ids": [row.get("previous_dataset_id")],
        },
        "before": {
            "value": row.get("previous_value"),
            "display_value": row.get("previous_display_value"),
            "state": row.get("previous_state"),
            "state_label": row.get("previous_state_label"),
        },
        "after": {
            "value": row.get("value"),
            "display_value": row.get("display_value"),
            "state": row.get("state"),
            "state_label": row.get("state_label"),
        },
        "transition": dict(row.get("transition") or {}),
        "priority_reason": (
            f"{definition.get('name', indicator_id)} 从 "
            f"{row.get('previous_state_label')} 切换为 {row.get('state_label')}。"
        ),
        "route": {
            "name": "instrument-decision",
            "params": {
                "symbol": symbol,
            },
            "query": query,
        },
    }


def _merge_group(event: dict[str, Any], row: dict[str, Any]) -> None:
    group = {
        "id": str(row.get("group_id") or ""),
        "name": str(row.get("group_name") or row.get("group_id") or ""),
    }
    if group["id"] and group not in event["groups"]:
        event["groups"].append(group)
        event["groups"].sort(key=lambda item: (item["id"], item["name"]))


def _merge_evidence_context(event: dict[str, Any], row: dict[str, Any]) -> None:
    current = event["current"]
    compare = event["compare"]
    for target, values in (
        (current, ("snapshot_ids", row.get("snapshot_id"), "dataset_ids", row.get("dataset_id"))),
        (
            compare,
            (
                "snapshot_ids",
                row.get("previous_snapshot_id"),
                "dataset_ids",
                row.get("previous_dataset_id"),
            ),
        ),
    ):
        for key, value in zip(values[::2], values[1::2]):
            if value and value not in target[key]:
                target[key].append(value)
                target[key].sort()
    query = event["route"]["query"]
    if len(current["dataset_ids"]) == 1:
        query["dataset"] = current["dataset_ids"][0]
    else:
        query.pop("dataset", None)


def _comparison_summary(
    run,
    comparisons: list[dict[str, Any]],
    group_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    previous_dates = sorted(
        {
            str(item.get("previous_trading_date"))
            for item in comparisons
            if item.get("previous_trading_date")
        }
    )
    previous_dates = sorted(
        {
            *previous_dates,
            *{
                str((item.get("compare") or {}).get("trading_date"))
                for item in group_events or []
                if (item.get("compare") or {}).get("trading_date")
            },
        }
    )
    statuses = {str(item.get("status")) for item in comparisons if item.get("status")}
    if not previous_dates:
        status = "unavailable"
    elif statuses == {"ok"} and len(previous_dates) == 1:
        status = "ok"
    else:
        status = "partial"
    return {
        "mode": "previous_trading_session",
        "status": status,
        "current_trading_date": run.trading_date.isoformat(),
        "previous_trading_date": previous_dates[0] if len(previous_dates) == 1 else None,
        "previous_trading_dates": previous_dates,
        "compare_run_id": None,
    }


def _event_sort_key(event: dict[str, Any]) -> tuple[int, int, str, str, str]:
    if event.get("kind") == "group_phase_change":
        stage = str((event.get("stage") or {}).get("id") or "")
        group = event.get("group") or {}
        return (
            0,
            _GROUP_STAGE_ORDER.get(stage, len(_GROUP_STAGE_ORDER)),
            str(group.get("name") or ""),
            str(group.get("id") or ""),
            str(event.get("event_id") or ""),
        )
    return (
        1,
        len(_GROUP_STAGE_ORDER),
        str((event.get("lens") or {}).get("id") or ""),
        str(event.get("symbol") or ""),
        str(event.get("event_id") or ""),
    )


def _unique_strings(values: Iterable[object]) -> list[str]:
    return sorted({str(value) for value in values if str(value).strip()})
