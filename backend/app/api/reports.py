from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.core.errors import AppError
from app.repositories.agent import AIDecisionRepository


router = APIRouter()


def _session_payload(model, repository: AIDecisionRepository | None = None) -> dict[str, Any]:
    policy = model.policy_json if isinstance(model.policy_json, dict) else {}
    payload = {
        "report_id": model.id,
        "session_id": model.id,
        "workflow_run_id": model.workflow_run_id,
        "dataset_key": model.dataset_key,
        "cutoff_time": model.cutoff_time,
        "decision_phase": model.decision_phase,
        "trading_date": model.trading_date,
        "parent_report_id": model.parent_session_id,
        "status": model.status,
        "policy": policy,
        "trigger_type": policy.get("trigger_type", "scheduled"),
        "analysis_mode": policy.get("analysis_mode", "official_cycle"),
        "session_context": policy.get("session_context", model.decision_phase),
        "report_scope": policy.get(
            "report_scope", ["technical_report", "ai_decision", "ai_review"]
        ),
        "official_cycle": bool(policy.get("official_cycle", True)),
        "eligible_for_scoring": bool(policy.get("eligible_for_scoring", True)),
        "updates_official_cta_state": bool(policy.get("updates_official_cta_state", True)),
        "technical_report_schema_version": model.technical_report_schema_version,
        "decision_report_schema_version": model.decision_report_schema_version,
        "equity_decision_run_id": model.equity_decision_run_id,
        "error_code": model.error_code,
        "error_message": model.error_message,
        "started_at": model.started_at,
        "completed_at": model.completed_at,
        "created_at": model.created_at,
        "quality": model.technical_report_json.get("quality") if isinstance(model.technical_report_json, dict) else {},
        "resources": {
            "technical": f"/api/research-reports/{model.id}/technical",
            "decision": f"/api/research-reports/{model.id}/decision",
            "trace": f"/api/research-reports/{model.id}/trace",
        },
    }
    if repository is not None:
        payload["run_summary"] = repository.session_summary(model.id)
    return payload


def _require(repository: AIDecisionRepository, report_id: str):
    model = repository.get_session(report_id)
    if model is None:
        raise AppError("找不到指定研究报告", code="research_report_not_found", status_code=404)
    return model


@router.get("/runs/{run_id}/research-reports")
def list_research_reports(
    run_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    repository = AIDecisionRepository(db)
    return [_session_payload(item, repository) for item in repository.sessions_for_workflow(run_id)[:limit]]


@router.get("/research-reports")
def list_all_research_reports(
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return report metadata for the report workspace index.

    The list intentionally excludes technical, decision and raw trace payloads;
    the workspace loads those resources only after a report is selected.
    """
    repository = AIDecisionRepository(db)
    return [_session_payload(item, repository) for item in repository.list_sessions(limit)]


@router.get("/research-reports/{report_id}")
def get_research_report(report_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    repository = AIDecisionRepository(db)
    model = _require(repository, report_id)
    payload = _session_payload(model, repository)
    # The index/metadata request stays small.  The three tabs fetch their
    # payload independently so opening a report does not eagerly transfer the
    # technical packet and trace-adjacent JSON.
    payload["technical_report"] = None
    payload["decision_report"] = None
    payload["trace_summary"] = {
        "node_count": len(repository.trace_nodes(report_id)),
        "model_run_count": len(repository.runs_for_session(report_id)),
    }
    return payload


@router.get("/research-reports/{report_id}/technical")
def get_technical_report(report_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    model = _require(AIDecisionRepository(db), report_id)
    return model.technical_report_json


@router.get("/research-reports/{report_id}/decision")
def get_ai_decision_report(report_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    repository = AIDecisionRepository(db)
    model = _require(repository, report_id)
    if not model.decision_report_json:
        raise AppError("AI 决策报告尚未生成", code="research_report_not_ready", status_code=409)
    return model.decision_report_json


@router.get("/research-reports/{report_id}/trace")
def get_decision_trace(report_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    repository = AIDecisionRepository(db)
    _require(repository, report_id)
    nodes = repository.trace_nodes(report_id)
    serialized = [_node_payload(node) for node in nodes]
    edges: list[dict[str, str]] = []
    for node in nodes:
        if node.parent_node_id:
            edges.append({"from": node.parent_node_id, "to": node.id, "kind": "parent"})
        for dependency in node.depends_on_node_ids or []:
            if dependency != node.parent_node_id:
                edges.append({"from": dependency, "to": node.id, "kind": "dependency"})
    return {
        "schema_version": "urus.decision_trace_graph.v1",
        "report_id": report_id,
        "nodes": serialized,
        "edges": edges,
    }


@router.get("/research-reports/{report_id}/trace/nodes/{node_id}")
def get_trace_node(report_id: str, node_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    repository = AIDecisionRepository(db)
    _require(repository, report_id)
    node = next((item for item in repository.trace_nodes(report_id) if item.id == node_id), None)
    if node is None:
        raise AppError("找不到指定复盘节点", code="trace_node_not_found", status_code=404)
    payload = _node_payload(node)
    if node.decision_run_id:
        run = repository.get(node.decision_run_id)
        if run is not None:
            payload["decision_run"] = {
                "id": run.id,
                "stage": run.stage,
                "status": run.status,
                "provider": run.provider,
                "model": run.model,
                "tool_call_count": len(repository.tool_calls(run.id)),
                "temperature": run.temperature,
                "prompt_tokens": run.prompt_tokens,
                "completion_tokens": run.completion_tokens,
                "estimated_cost": run.estimated_cost,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
            }
            payload["tool_calls"] = [
                {
                    "sequence": call.sequence,
                    "tool_name": call.tool_name,
                    "arguments": call.arguments,
                    "ok": call.ok,
                    "error_code": call.error_code,
                    "duration_ms": call.duration_ms,
                    "result_bytes": call.result_bytes,
                }
                for call in repository.tool_calls(run.id)
            ]
    return payload


@router.get("/research-reports/{report_id}/trace/nodes/{node_id}/raw-response")
def get_trace_node_raw_response(report_id: str, node_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    repository = AIDecisionRepository(db)
    _require(repository, report_id)
    node = next((item for item in repository.trace_nodes(report_id) if item.id == node_id), None)
    if node is None:
        raise AppError("找不到指定复盘节点", code="trace_node_not_found", status_code=404)
    if not node.decision_run_id:
        return {"node_id": node_id, "model_turns": []}
    turns = repository.model_turns(node.decision_run_id)
    node_turns = [turn for turn in turns if turn.trace_node_id == node_id]
    # The invocation summary node owns the whole Agent Invocation, while the
    # child model-turn nodes own individual responses. Let either node expose
    # the same explicitly requested raw material.
    selected_turns = node_turns or turns
    return {
        "node_id": node_id,
        "unvalidated": True,
        "warning": "原始模型返回仅供复盘，不构成决策证据。",
        "model_turns": [
            {
                "sequence": turn.sequence,
                "response_message": turn.response_message,
                "raw_provider_response": turn.raw_provider_response,
                "raw_response_bytes": turn.raw_response_bytes,
                "raw_response_truncated": turn.raw_response_truncated,
                "returned_reasoning_fields": _returned_reasoning_fields(turn.response_message, turn.raw_provider_response),
                "prompt_tokens": turn.prompt_tokens,
                "completion_tokens": turn.completion_tokens,
                "created_at": turn.created_at,
            }
            for turn in selected_turns
        ],
    }


def _returned_reasoning_fields(message: dict[str, Any], raw: dict[str, Any]) -> list[str]:
    """Report reasoning-like keys the provider actually returned, without
    treating them as validated evidence or displaying them by default."""
    names = {"reasoning", "reasoning_content", "reasoning_details", "analysis", "thinking", "chain_of_thought"}
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).lower().replace("-", "_")
                if normalized in names:
                    found.add(str(key))
                walk(child)
        elif isinstance(value, list):
            for child in value[:20]:
                walk(child)

    walk(message)
    walk(raw)
    return sorted(found)


def _node_payload(node) -> dict[str, Any]:
    return {
        "id": node.id,
        "decision_run_id": node.decision_run_id,
        "parent_node_id": node.parent_node_id,
        "depends_on_node_ids": node.depends_on_node_ids,
        "sequence": node.sequence,
        "lane": node.lane,
        "node_type": node.node_type,
        "label": node.label,
        "status": node.status,
        "input_summary": node.input_summary,
        "output_summary": node.output_summary,
        "evidence_refs": node.evidence_refs,
        "metrics": node.metrics,
        "error_code": node.error_code,
        "error_message": node.error_message,
        "started_at": node.started_at,
        "completed_at": node.completed_at,
    }
