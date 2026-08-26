from __future__ import annotations

from math import isfinite
from typing import Any

from app.decision_harness.contracts import content_sha256


REPORT_SCHEMA = "urus.observation_report.v1"


def build_observation_report(
    *,
    run_id: str,
    trading_date: str,
    snapshots: list[dict[str, Any]],
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate frozen group snapshots into one deterministic close report."""

    rankings: list[dict[str, Any]] = []
    improving: list[dict[str, Any]] = []
    deteriorating: list[dict[str, Any]] = []
    leaders: list[dict[str, Any]] = []
    laggards: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    quality_issues: list[dict[str, Any]] = []
    opportunity_lanes: dict[str, list[dict[str, Any]]] = {
        "confirmed": [],
        "near_confirmation": [],
        "forming": [],
    }
    risk_lanes: dict[str, list[dict[str, Any]]] = {"invalidated": [], "bearish": []}
    momentum_map: list[dict[str, Any]] = []
    breadth_delta: list[dict[str, Any]] = []
    state_transitions: list[dict[str, Any]] = []

    for source in snapshots:
        group_id = str(source.get("group_id") or "")
        if source.get("status") == "failed":
            quality_issues.append(
                {
                    "scope": "group",
                    "group_id": group_id,
                    "status": "failed",
                    "message": source.get("error_message") or "组快照失败",
                }
            )
            continue
        payload = dict(source.get("payload") or {})
        group = dict(payload.get("group") or {})
        group_name = str(group.get("display_name") or group_id)
        features = dict(payload.get("features") or {})
        changes = dict(payload.get("changes") or {})
        quality = dict(payload.get("quality") or {})
        group_decision = dict(payload.get("group_decision") or {})
        median_20d = _nested_number(features, "returns_percent", "20d", "median")
        relative_20d = _nested_number(features, "relative_strength", "median_excess_20d")
        breadth_ma20 = _nested_number(features, "breadth", "above_ma20")
        rank_score = sum(
            value
            for value in (
                median_20d,
                relative_20d,
                breadth_ma20 * 10 if breadth_ma20 is not None else None,
            )
            if value is not None
        )
        ranking = {
            "group_id": group_id,
            "group_name": group_name,
            "snapshot_id": source.get("snapshot_id"),
            "dataset_id": source.get("dataset_id"),
            "state": group_decision.get("state"),
            "stance": group_decision.get("stance"),
            "action": group_decision.get("action"),
            "median_20d": median_20d,
            "relative_20d": relative_20d,
            "breadth_ma20": breadth_ma20,
            "technical_rank_score": round(rank_score, 4),
        }
        rankings.append(ranking)

        median_delta = _number(changes.get("median_20d_delta_percent"))
        relative_delta = _number(changes.get("relative_20d_delta_percent"))
        breadth_change = _number(changes.get("breadth_ma20_delta"))
        change_score = sum(
            value
            for value in (
                median_delta,
                relative_delta,
                breadth_change * 100 if breadth_change is not None else None,
            )
            if value is not None
        )
        change_item = {
            "group_id": group_id,
            "group_name": group_name,
            "previous_trading_date": changes.get("previous_trading_date"),
            "change_score": round(change_score, 4),
            "median_20d_delta_percent": median_delta,
            "relative_20d_delta_percent": relative_delta,
            "breadth_ma20_delta": breadth_change,
        }
        if changes.get("previous_trading_date"):
            (improving if change_score > 0 else deteriorating).append(change_item)

        momentum_map.append(
            {
                "group_id": group_id,
                "group_name": group_name,
                "relative_20d": relative_20d,
                "relative_20d_change": relative_delta,
                "breadth_ma20": breadth_ma20,
            }
        )
        breadth_delta.append(
            {
                "group_id": group_id,
                "group_name": group_name,
                "value": breadth_change,
            }
        )
        group_state = dict(changes.get("group_state") or {})
        if group_state.get("changed"):
            state_transitions.append(
                {
                    "group_id": group_id,
                    "group_name": group_name,
                    "from": group_state.get("from"),
                    "to": group_state.get("to"),
                }
            )

        for row in payload.get("symbols") or []:
            symbol = str(row.get("symbol") or "")
            if not row.get("valid"):
                quality_issues.append(
                    {
                        "scope": "symbol",
                        "group_id": group_id,
                        "symbol": symbol,
                        "status": row.get("quality_status") or "missing",
                        "message": "; ".join(row.get("warnings") or []) or "数据不足",
                    }
                )
                continue
            return_20d = _nested_number(row, "returns_percent", "20")
            relative_symbol = _nested_number(row, "relative_excess_percent", "20d")
            anomaly = {
                "group_id": group_id,
                "group_name": group_name,
                "symbol": symbol,
                "return_20d": return_20d,
                "relative_20d": relative_symbol,
                "dataset_id": source.get("dataset_id"),
                "snapshot_id": source.get("snapshot_id"),
            }
            leaders.append(anomaly)
            laggards.append(anomaly)

        decisions_by_symbol: dict[str, list[dict[str, Any]]] = {}
        for decision in payload.get("strategy_decisions") or []:
            symbol = str((decision.get("scope") or {}).get("symbol") or decision.get("symbol") or "")
            if not symbol:
                continue
            decisions_by_symbol.setdefault(symbol, []).append(decision)
            item = _decision_item(group_id, group_name, source, decision)
            stage = str(item["stage"])
            stance = str(item["stance"])
            distance = _number((decision.get("setup_progress") or {}).get("confirmation_distance_atr"))
            if stage == "confirmed":
                opportunity_lanes["confirmed"].append(item)
            elif stage in {"armed", "watching"} and distance is not None and distance <= 1:
                opportunity_lanes["near_confirmation"].append(item)
            elif stage in {"forming", "armed", "watching"}:
                opportunity_lanes["forming"].append(item)
            if stage == "invalidated":
                risk_lanes["invalidated"].append(item)
            if stance == "bearish":
                risk_lanes["bearish"].append(item)

        for symbol, decisions in decisions_by_symbol.items():
            stances = {
                str(item.get("stance"))
                for item in decisions
                if item.get("status") == "ok" and item.get("stance") in {"bullish", "bearish"}
            }
            if stances == {"bullish", "bearish"}:
                conflicts.append(
                    {
                        "group_id": group_id,
                        "group_name": group_name,
                        "symbol": symbol,
                        "decision_ids": [item.get("decision_id") for item in decisions],
                        "summary": "同一冻结数据上的确定性策略同时出现 bullish 与 bearish。",
                    }
                )

        for warning in quality.get("warnings") or []:
            quality_issues.append(
                {
                    "scope": "group",
                    "group_id": group_id,
                    "status": quality.get("status") or "partial",
                    "message": str(warning),
                }
            )

    rankings.sort(key=lambda item: item["technical_rank_score"], reverse=True)
    improving.sort(key=lambda item: item["change_score"], reverse=True)
    deteriorating.sort(key=lambda item: item["change_score"])
    leaders.sort(key=_anomaly_sort, reverse=True)
    laggards.sort(key=_anomaly_sort)
    for lane in (*opportunity_lanes.values(), *risk_lanes.values()):
        lane.sort(key=lambda item: abs(_number(item.get("score")) or 0), reverse=True)

    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA,
        "mode": "deterministic-only",
        "run_id": run_id,
        "trading_date": trading_date,
        "provenance": dict(provenance or {}),
        "summary": {
            "requested_group_count": len(snapshots),
            "successful_group_count": sum(item.get("status") != "failed" for item in snapshots),
            "failed_group_count": sum(item.get("status") == "failed" for item in snapshots),
            "quality_issue_count": len(quality_issues),
            "strategy_conflict_count": len(conflicts),
        },
        "group_rankings": rankings,
        "improving_groups": improving,
        "deteriorating_groups": deteriorating,
        "anomalies": {"leaders": leaders[:12], "laggards": laggards[:12]},
        "strategy_conflicts": conflicts,
        "quality_issues": quality_issues,
        "opportunity_lanes": opportunity_lanes,
        "risk_lanes": risk_lanes,
        "visuals": {
            "group_momentum_map": momentum_map,
            "breadth_delta": breadth_delta,
            "state_transitions": state_transitions,
        },
    }
    # Run/database identifiers and source URLs identify the artifact but are
    # not business evidence. Excluding them keeps the report hash stable when
    # the same frozen values are replayed under a new run identifier.
    report["content_sha256"] = content_sha256(_stable_hash_payload(report))
    return report


def _stable_hash_payload(value: Any) -> Any:
    if isinstance(value, dict):
        ignored = {
            "run_id",
            "snapshot_id",
            "dataset_id",
            "group_version_id",
            "decision_id",
            "decision_ids",
            "universe_revision_id",
            "source_url",
            "dataset_ids",
            "snapshot_ids",
            "group_version_ids",
        }
        return {
            key: _stable_hash_payload(item)
            for key, item in value.items()
            if key not in ignored and key != "content_sha256"
        }
    if isinstance(value, list):
        return [_stable_hash_payload(item) for item in value]
    return value


def _decision_item(
    group_id: str,
    group_name: str,
    source: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    strategy = dict(decision.get("strategy") or {})
    progress = dict(decision.get("setup_progress") or {})
    return {
        "group_id": group_id,
        "group_name": group_name,
        "symbol": (decision.get("scope") or {}).get("symbol") or decision.get("symbol"),
        "strategy_name": strategy.get("name"),
        "strategy_version": strategy.get("version"),
        "implementation_sha256": strategy.get("implementation_sha256"),
        "decision_id": decision.get("decision_id"),
        "stage": progress.get("stage") or "missing",
        "stance": decision.get("stance") or "insufficient_data",
        "action": decision.get("action") or "no_action",
        "score": _number(decision.get("score")),
        "confirmation_distance_atr": _number(progress.get("confirmation_distance_atr")),
        "dataset_id": source.get("dataset_id"),
        "snapshot_id": source.get("snapshot_id"),
    }


def _anomaly_sort(item: dict[str, Any]) -> float:
    return (_number(item.get("relative_20d")) or 0) + (_number(item.get("return_20d")) or 0)


def _nested_number(value: dict[str, Any], *path: str) -> float | None:
    current: Any = value
    for key in path:
        current = current.get(key) if isinstance(current, dict) else None
    return _number(current)


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value)):
        return float(value)
    return None
