from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.core.errors import AppError
from app.models import RunModel
from app.repositories.agent import AIDecisionRepository, ReportDeletionConflict
from app.repositories.report_display import ReportDisplayRepository
from app.urus_agent.display_projection import (
    DISPLAY_PROJECTION_SCHEMA,
    build_report_display_projection,
    projection_content_sha256,
)


router = APIRouter()


def _display_manifest(model, display_repository: ReportDisplayRepository) -> dict[str, Any]:
    projection = display_repository.get(model.id)
    if projection is None:
        return {
            "schema_version": DISPLAY_PROJECTION_SCHEMA,
            "available": False,
            "endpoint": f"/api/research-reports/{model.id}/display",
            "options_endpoint": f"/api/research-reports/{model.id}/display/options/{{symbol}}",
            "source_snapshot_ids": [],
            "content_sha256": None,
            "data_quality": {
                "source_available": False,
                "warnings": ["展示投影尚未生成；请确认源 snapshot 仍然存在。"],
                "missing_sections": ["options"],
            },
        }
    payload = projection.payload_json if isinstance(projection.payload_json, dict) else {}
    quality = payload.get("data_quality") if isinstance(payload.get("data_quality"), dict) else {}
    return {
        "schema_version": projection.schema_version,
        "available": bool(quality.get("source_available", False)),
        "endpoint": f"/api/research-reports/{model.id}/display",
        "options_endpoint": f"/api/research-reports/{model.id}/display/options/{{symbol}}",
        "source_snapshot_ids": list(projection.source_snapshot_ids or []),
        "content_sha256": projection.content_sha256,
        "created_at": projection.created_at,
        "data_quality": quality,
    }


def _ensure_display_projection(
    model,
    repository: AIDecisionRepository,
    display_repository: ReportDisplayRepository,
):
    existing = display_repository.get(model.id)
    if existing is not None:
        return existing
    source_snapshot_ids: list[str] = []
    source_run_ids: list[str] = []
    for run in repository.runs_for_session(model.id):
        source_snapshot_ids.extend(str(item) for item in (run.source_snapshot_ids or []) if item)
        source_run_ids.extend(str(item) for item in (run.source_run_ids or []) if item)
    source_snapshot_ids = list(dict.fromkeys(source_snapshot_ids))
    source_run_ids = list(dict.fromkeys(source_run_ids))
    if not source_snapshot_ids:
        workflow_run = display_repository.session.get(RunModel, model.workflow_run_id)
        if workflow_run is not None and workflow_run.snapshot_id:
            source_snapshot_ids = [str(workflow_run.snapshot_id)]
            source_run_ids = list(dict.fromkeys([*source_run_ids, str(workflow_run.id)]))
    if not source_snapshot_ids:
        return None
    payload = build_report_display_projection(
        display_repository.session,
        report_id=model.id,
        source_snapshot_ids=source_snapshot_ids,
        source_run_ids=source_run_ids,
        captured_at=model.cutoff_time,
    )
    return display_repository.save(
        report_id=model.id,
        payload=payload,
        source_snapshot_ids=source_snapshot_ids,
        source_run_ids=source_run_ids,
        content_sha256=projection_content_sha256(payload),
        schema_version=str(payload.get("schema_version") or DISPLAY_PROJECTION_SCHEMA),
    )


def _session_payload(
    model,
    repository: AIDecisionRepository | None = None,
    display_repository: ReportDisplayRepository | None = None,
) -> dict[str, Any]:
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
            "display_manifest": f"/api/research-reports/{model.id}/display/manifest",
            "display_options": f"/api/research-reports/{model.id}/display/options/{{symbol}}",
        },
        "display_projection": (
            _display_manifest(model, display_repository)
            if display_repository is not None
            else {
                "schema_version": DISPLAY_PROJECTION_SCHEMA,
                "available": False,
                "endpoint": f"/api/research-reports/{model.id}/display",
            }
        ),
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
    display_repository = ReportDisplayRepository(db)
    return [
        _session_payload(item, repository, display_repository)
        for item in repository.sessions_for_workflow(run_id)[:limit]
    ]


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
    display_repository = ReportDisplayRepository(db)
    return [_session_payload(item, repository, display_repository) for item in repository.list_sessions(limit)]


@router.get("/research-reports/{report_id}")
def get_research_report(report_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    repository = AIDecisionRepository(db)
    model = _require(repository, report_id)
    display_repository = ReportDisplayRepository(db)
    _ensure_display_projection(model, repository, display_repository)
    payload = _session_payload(model, repository, display_repository)
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


@router.delete("/research-reports/{report_id}")
def delete_research_report(report_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    repository = AIDecisionRepository(db)
    if repository.get_session(report_id) is None:
        raise AppError("找不到指定研究报告", code="research_report_not_found", status_code=404)
    try:
        repository.delete_session(report_id)
    except ReportDeletionConflict as exc:
        raise AppError(str(exc), code="research_report_delete_conflict", status_code=409) from exc
    return {"report_id": report_id, "deleted": True}


@router.get("/research-reports/{report_id}/display/manifest")
def get_display_manifest(report_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    repository = AIDecisionRepository(db)
    model = _require(repository, report_id)
    display_repository = ReportDisplayRepository(db)
    _ensure_display_projection(model, repository, display_repository)
    return {
        "report_id": report_id,
        **_display_manifest(model, display_repository),
    }


@router.get("/research-reports/{report_id}/display")
def get_display_projection(report_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    repository = AIDecisionRepository(db)
    model = _require(repository, report_id)
    display_repository = ReportDisplayRepository(db)
    projection = _ensure_display_projection(model, repository, display_repository)
    if projection is None:
        raise AppError(
            "展示投影不可用：找不到关联 source snapshot。",
            code="display_projection_source_unavailable",
            status_code=409,
        )
    return projection.payload_json


@router.get("/research-reports/{report_id}/display/options/{symbol}")
def get_display_options(
    report_id: str,
    symbol: str,
    expiration: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    repository = AIDecisionRepository(db)
    model = _require(repository, report_id)
    display_repository = ReportDisplayRepository(db)
    projection = _ensure_display_projection(model, repository, display_repository)
    if projection is None:
        raise AppError(
            "展示投影不可用：源 snapshot 不存在或没有标准化期权数据。",
            code="display_projection_source_unavailable",
            status_code=409,
        )
    payload = projection.payload_json if isinstance(projection.payload_json, dict) else {}
    symbols = ((payload.get("options") or {}).get("symbols") or {})
    symbol_key = next((key for key in symbols if str(key).upper() == symbol.upper()), None)
    if symbol_key is None:
        raise AppError(
            f"展示投影中没有 {symbol.upper()} 的期权数据。",
            code="display_projection_symbol_not_found",
            status_code=404,
        )
    symbol_payload = symbols[symbol_key]
    expirations = symbol_payload.get("expirations") if isinstance(symbol_payload, dict) else {}
    if not isinstance(expirations, dict) or not expirations:
        raise AppError(
            f"展示投影中没有 {symbol.upper()} 的到期日数据。",
            code="display_projection_expiration_not_found",
            status_code=404,
        )
    selected_expiration = expiration or next(iter(expirations))
    if selected_expiration not in expirations:
        raise AppError(
            f"展示投影中没有 {symbol.upper()} {selected_expiration} 的数据。",
            code="display_projection_expiration_not_found",
            status_code=404,
        )
    prefix = f"options.symbols.{symbol_key}.expirations.{selected_expiration}"
    chart_specs = [
        item
        for item in (payload.get("chart_specs") or [])
        if isinstance(item, dict) and str(item.get("data_ref", "")).startswith(prefix)
    ]
    return {
        "schema_version": projection.schema_version,
        "report_id": report_id,
        "symbol": symbol_key,
        "spot": symbol_payload.get("spot"),
        "as_of": symbol_payload.get("as_of"),
        "overview": symbol_payload.get("overview") or {},
        "expiration": selected_expiration,
        "data": expirations[selected_expiration],
        "source": payload.get("source") or {},
        "chart_specs": chart_specs,
        "data_quality": payload.get("data_quality") or {},
    }


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
