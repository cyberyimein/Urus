from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.core.errors import AppError
from app.repositories.agent import AIDecisionRepository


router = APIRouter(prefix="/ai")


def _run_payload(model) -> dict[str, object]:
    return {
        "id": model.id,
        "task_type": model.task_type,
        "status": model.status,
        "dataset_key": model.dataset_key,
        "source_run_ids": model.source_run_ids,
        "source_snapshot_ids": model.source_snapshot_ids,
        "cutoff_time": model.cutoff_time,
        "target_symbol": model.target_symbol,
        "requested_symbols": model.requested_symbols,
        "skill_name": model.skill_name,
        "skill_hash": model.skill_hash,
        "provider": model.provider,
        "model": model.model,
        "input_schema_version": model.input_schema_version,
        "input_hash": model.input_hash,
        "output_schema_version": model.output_schema_version,
        "parsed_output": model.parsed_output,
        "error_code": model.error_code,
        "error_message": model.error_message,
        "prompt_tokens": model.prompt_tokens,
        "completion_tokens": model.completion_tokens,
        "estimated_cost": model.estimated_cost,
        "started_at": model.started_at,
        "completed_at": model.completed_at,
        "created_at": model.created_at,
    }


@router.get("/decisions")
def list_decisions(
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return [_run_payload(model) for model in AIDecisionRepository(db).list(limit)]


@router.get("/decisions/{decision_id}")
def get_decision(decision_id: str, db: Session = Depends(get_db)) -> dict[str, object]:
    repository = AIDecisionRepository(db)
    model = repository.get(decision_id)
    if model is None:
        raise AppError("找不到指定 AI 决策", code="ai_decision_not_found", status_code=404)
    payload = _run_payload(model)
    payload["tool_calls"] = [
        {
            "id": item.id,
            "sequence": item.sequence,
            "tool_call_id": item.tool_call_id,
            "tool_name": item.tool_name,
            "arguments": item.arguments,
            "result": item.result,
            "ok": item.ok,
            "error_code": item.error_code,
            "duration_ms": item.duration_ms,
            "result_bytes": item.result_bytes,
            "started_at": item.started_at,
            "completed_at": item.completed_at,
        }
        for item in repository.tool_calls(decision_id)
    ]
    return payload
