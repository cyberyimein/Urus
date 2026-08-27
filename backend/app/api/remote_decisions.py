from __future__ import annotations

import hashlib
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_settings
from app.core.config import Settings
from app.core.errors import AppError
from app.core.time import utc_now
from app.decision_harness.contracts import canonical_json
from app.decision_harness.remote_workflow import RemoteDecisionCompiler
from app.models.remote_decision import RemoteDecisionRunModel
from app.repositories.remote_decision import RemoteDecisionRepository
from app.schemas.remote_decision import (
    RemoteDecisionArtifactSummary,
    RemoteDecisionEventResponse,
    RemoteDecisionPreflightRequest,
    RemoteDecisionPreflightResponse,
    RemoteDecisionRerunRequest,
    RemoteDecisionRunResponse,
    RemoteDecisionSource,
    RemoteDecisionSubmitRequest,
)


router = APIRouter(prefix="/remote-decisions", tags=["remote-decisions"])


@router.post("/preflight", response_model=RemoteDecisionPreflightResponse)
def preflight_remote_decision(
    payload: RemoteDecisionPreflightRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RemoteDecisionPreflightResponse:
    compiled = RemoteDecisionCompiler(db, settings).compile(payload.intent_type, payload.source)
    return RemoteDecisionCompiler(db, settings).preflight_response(compiled)


@router.post("", response_model=RemoteDecisionRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def submit_remote_decision(
    payload: RemoteDecisionSubmitRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RemoteDecisionRunResponse:
    compiler = RemoteDecisionCompiler(db, settings)
    compiled = compiler.compile(payload.intent_type, payload.source)
    if compiled.preflight_fingerprint != payload.preflight_fingerprint:
        raise AppError(
            "确认后的冻结证据或 Workflow Binding 已变化，请重新确认。",
            code="preflight_stale",
            status_code=409,
        )
    if compiled.blockers:
        issue = compiled.blockers[0]
        raise AppError(issue.message, code=issue.code, status_code=409, details=issue.details)
    if not compiled.binding or not compiled.preflight_fingerprint:
        raise AppError("Workflow Binding 不可用。", code="workflow_binding_unavailable", status_code=409)

    repository = RemoteDecisionRepository(db)
    existing_request = repository.by_request_intent(payload.request_intent_id)
    if existing_request is not None:
        if existing_request.preflight_fingerprint != payload.preflight_fingerprint:
            raise AppError(
                "request_intent_id 已用于另一份输入，不能复用。",
                code="request_intent_conflict",
                status_code=409,
            )
        return _run_response(db, existing_request)

    idempotency_key = _idempotency_key(payload.request_intent_id, compiled.binding.workflow_ref, compiled.input_sha256)
    existing_key = repository.by_idempotency(idempotency_key)
    if existing_key is not None:
        return _run_response(db, existing_key)
    local_run_id = str(uuid4())
    trigger_source = _trigger_source(payload.intent_type.value)
    metadata = {
        "source": "urus",
        "local_run_id": local_run_id,
        "intent_type": payload.intent_type.value,
        "trigger_source": trigger_source,
    }
    try:
        model = repository.create_run(
            {
                "id": local_run_id,
                "intent_type": payload.intent_type.value,
                "request_intent_id": payload.request_intent_id,
                "idempotency_key": idempotency_key,
                "scope_type": compiled.scope_type,
                "scope_id": compiled.scope_id,
                "scope_version": compiled.scope_version,
                "dataset_id": compiled.dataset_id,
                "lens_type": compiled.lens_type,
                "lens_id": compiled.lens_id,
                "lens_version": compiled.lens_version,
                "source_locator_json": compiled.source,
                "source_dataset_id": compiled.source_dataset_id,
                "source_snapshot_id": compiled.source_snapshot_id,
                "source_observation_run_id": compiled.source_observation_run_id,
                "workflow_ref": compiled.binding.workflow_ref,
                "definition_hash": compiled.binding.definition_hash,
                "compiled_hash": compiled.binding.compiled_hash,
                "input_schema_version": compiled.binding.input_schema_version,
                "input_sha256": compiled.input_sha256,
                "input_json": compiled.input_payload,
                "metadata_json": metadata,
                "trigger_mode": "manual",
                "trigger_source": trigger_source,
                "preflight_fingerprint": compiled.preflight_fingerprint,
            }
        )
    except IntegrityError as exc:
        db.rollback()
        existing_request = repository.by_request_intent(payload.request_intent_id)
        if existing_request is not None and existing_request.preflight_fingerprint == payload.preflight_fingerprint:
            return _run_response(db, existing_request)
        raise AppError("Remote Decision 幂等键已被占用。", code="idempotency_conflict", status_code=409) from exc
    supervisor = request.app.state.remote_decision_supervisor
    await supervisor.enqueue(model.id)
    return _run_response(db, model)


@router.get("", response_model=list[RemoteDecisionRunResponse])
def list_remote_decisions(
    scope_type: str | None = Query(default=None),
    scope_id: str | None = Query(default=None),
    dataset_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[RemoteDecisionRunResponse]:
    repository = RemoteDecisionRepository(db)
    return [_run_response(db, model) for model in repository.list_runs(scope_type=scope_type, scope_id=scope_id, dataset_id=dataset_id, limit=limit)]


@router.get("/{local_run_id}", response_model=RemoteDecisionRunResponse)
def get_remote_decision(local_run_id: str, db: Session = Depends(get_db)) -> RemoteDecisionRunResponse:
    model = RemoteDecisionRepository(db).get_run(local_run_id)
    if model is None:
        raise AppError("找不到 Remote Decision Run", code="remote_decision_not_found", status_code=404)
    return _run_response(db, model)


@router.get("/{local_run_id}/events", response_model=list[RemoteDecisionEventResponse])
def list_remote_decision_events(
    local_run_id: str,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[RemoteDecisionEventResponse]:
    repository = RemoteDecisionRepository(db)
    if repository.get_run(local_run_id) is None:
        raise AppError("找不到 Remote Decision Run", code="remote_decision_not_found", status_code=404)
    return [
        RemoteDecisionEventResponse(
            sequence=item.sequence,
            event_type=item.event_type,
            event_timestamp=item.event_timestamp,
            node_id=item.node_id,
            attempt=item.attempt,
            child_run_id=item.child_run_id,
            data=dict(item.safe_data_json or {}),
            created_at=item.created_at,
        )
        for item in repository.list_events(local_run_id, after_sequence=after_sequence, limit=limit)
    ]


@router.post("/{local_run_id}/stop", response_model=RemoteDecisionRunResponse)
async def stop_remote_decision(local_run_id: str, request: Request, db: Session = Depends(get_db)) -> RemoteDecisionRunResponse:
    if RemoteDecisionRepository(db).get_run(local_run_id) is None:
        raise AppError("找不到 Remote Decision Run", code="remote_decision_not_found", status_code=404)
    model = await request.app.state.remote_decision_supervisor.stop_run(local_run_id)
    if model is None:
        raise AppError("找不到 Remote Decision Run", code="remote_decision_not_found", status_code=404)
    return _run_response(db, model)


@router.post("/{local_run_id}/rerun", response_model=RemoteDecisionRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def rerun_remote_decision(
    local_run_id: str,
    request: Request,
    body: RemoteDecisionRerunRequest | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RemoteDecisionRunResponse:
    source_model = RemoteDecisionRepository(db).get_run(local_run_id)
    if source_model is None:
        raise AppError("找不到 Remote Decision Run", code="remote_decision_not_found", status_code=404)
    request_intent_id = (body.request_intent_id if body else None) or str(uuid4())
    source = RemoteDecisionSource(**dict(source_model.source_locator_json or {}))
    payload = RemoteDecisionSubmitRequest(
        intent_type=source_model.intent_type,
        source=source,
        preflight_fingerprint="0" * 64,
        request_intent_id=request_intent_id,
    )
    compiler = RemoteDecisionCompiler(db, settings)
    compiled = compiler.compile(payload.intent_type, payload.source)
    if compiled.blockers:
        issue = compiled.blockers[0]
        raise AppError(issue.message, code=issue.code, status_code=409, details=issue.details)
    if not compiled.preflight_fingerprint:
        raise AppError("无法重新确认冻结输入。", code="preflight_stale", status_code=409)
    payload.preflight_fingerprint = compiled.preflight_fingerprint
    return await submit_remote_decision(payload, request, db, settings)


def _run_response(db: Session, model: RemoteDecisionRunModel) -> RemoteDecisionRunResponse:
    artifact = RemoteDecisionRepository(db).get_artifact(model.id)
    artifact_summary = None
    if artifact is not None:
        artifact_summary = RemoteDecisionArtifactSummary(
            output_schema_version=artifact.output_schema_version,
            completeness=artifact.completeness,
            artifact_sha256=artifact.artifact_sha256,
            validation_status=artifact.validation_status,
            accepted_at=artifact.accepted_at,
        )
    from app.schemas.remote_decision import RemoteDecisionIntent, RemoteDecisionStatus

    return RemoteDecisionRunResponse(
        local_run_id=model.id,
        anomalo_run_id=model.anomalo_run_id,
        intent_type=RemoteDecisionIntent(model.intent_type),
        request_intent_id=model.request_intent_id,
        idempotency_key=model.idempotency_key,
        scope_type=model.scope_type,
        scope_id=model.scope_id,
        scope_version=model.scope_version,
        dataset_id=model.dataset_id,
        lens_type=model.lens_type,
        lens_id=model.lens_id,
        lens_version=model.lens_version,
        source=RemoteDecisionSource(**dict(model.source_locator_json or {})),
        workflow_ref=model.workflow_ref,
        input_schema_version=model.input_schema_version,
        input_sha256=model.input_sha256,
        status=RemoteDecisionStatus(model.status),
        remote_status=model.remote_status,
        validation_status=model.validation_status,
        latest_event_sequence=int(model.latest_event_sequence or 0),
        error_code=model.error_code,
        safe_error_message=model.safe_error_message,
        result=dict(model.result_json) if isinstance(model.result_json, dict) else None,
        artifact=artifact_summary,
        created_at=model.created_at,
        submitted_at=model.submitted_at,
        started_at=model.started_at,
        completed_at=model.completed_at,
    )


def _idempotency_key(request_intent_id: str, workflow_ref: str, input_sha256: str) -> str:
    value = "\0".join(("urus-remote-decision-v1", request_intent_id, workflow_ref, input_sha256))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _trigger_source(intent_type: str) -> str:
    return {
        "instrument_arbitration": "instrument_page",
        "group_arbitration": "group_page",
        "indicator_attention": "indicator_cross_section",
        "strategy_attention": "strategy_cross_section",
    }.get(intent_type, "unknown")
