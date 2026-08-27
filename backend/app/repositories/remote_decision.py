from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.remote_decision import (
    DecisionWorkflowBindingModel,
    RemoteDecisionArtifactModel,
    RemoteDecisionEventModel,
    RemoteDecisionRunModel,
)


TERMINAL_STATUSES = {"accepted", "rejected_result", "succeeded", "failed", "stopped"}
ACTIVE_STATUSES = {"queued", "submitting", "running", "stopping"}
RECOVERABLE_STATUSES = ACTIVE_STATUSES | {"succeeded"}
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"submitting", "stopping", "stopped", "failed"},
    "submitting": {"running", "failed", "stopping", "stopped"},
    "running": {"succeeded", "failed", "stopping", "stopped"},
    "stopping": {"succeeded", "stopped", "failed"},
    "succeeded": {"accepted", "rejected_result"},
    "failed": set(),
    "stopped": set(),
    "accepted": set(),
    "rejected_result": set(),
}


class RemoteDecisionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_binding(self, intent_type: str, *, include_disabled: bool = False) -> DecisionWorkflowBindingModel | None:
        statement = (
            select(DecisionWorkflowBindingModel)
            .where(
                DecisionWorkflowBindingModel.intent_type == intent_type,
                DecisionWorkflowBindingModel.verified_at.is_not(None),
            )
            .order_by(DecisionWorkflowBindingModel.updated_at.desc())
        )
        if not include_disabled:
            statement = statement.where(DecisionWorkflowBindingModel.status == "active")
        return self.session.scalar(statement.limit(1))

    def save_binding(self, payload: dict[str, Any]) -> DecisionWorkflowBindingModel:
        now = utc_now()
        existing = self.session.scalar(
            select(DecisionWorkflowBindingModel).where(
                DecisionWorkflowBindingModel.intent_type == payload["intent_type"],
                DecisionWorkflowBindingModel.workflow_ref == payload["workflow_ref"],
            )
        )
        if existing is None:
            existing = DecisionWorkflowBindingModel(
                id=str(payload.get("id") or uuid4()),
                intent_type=str(payload["intent_type"]),
                workflow_ref=str(payload["workflow_ref"]),
                status=str(payload.get("status", "disabled")),
                definition_hash=str(payload["definition_hash"]),
                compiled_hash=str(payload["compiled_hash"]),
                capability_manifest_hash=str(payload.get("capability_manifest_hash") or ""),
                input_schema_version=str(payload.get("input_schema_version") or "urus.remote_decision_input.v1"),
                output_schema_version=str(payload["output_schema_version"]),
                definition_json=dict(payload.get("definition_json") or {}),
                manifest_json=dict(payload.get("manifest_json") or {}),
                published_at=_as_datetime(payload.get("published_at")),
                verified_at=_as_datetime(payload.get("verified_at")),
                last_error=payload.get("last_error"),
                created_at=now,
                updated_at=now,
            )
            self.session.add(existing)
        else:
            # Release tooling may rotate a binding in place; run rows keep
            # copied hashes so this never changes the meaning of an old run.
            for field in (
                "status", "definition_hash", "compiled_hash", "capability_manifest_hash",
                "input_schema_version", "output_schema_version", "definition_json", "manifest_json",
                "published_at", "verified_at", "last_error",
            ):
                if field in payload:
                    value = payload[field]
                    if field in {"published_at", "verified_at"}:
                        value = _as_datetime(value)
                    setattr(existing, field, value)
            existing.updated_at = now
        if str(payload.get("status", existing.status)) == "active":
            siblings = list(
                self.session.scalars(
                    select(DecisionWorkflowBindingModel).where(
                        DecisionWorkflowBindingModel.intent_type == payload["intent_type"],
                        DecisionWorkflowBindingModel.status == "active",
                    )
                )
            )
            for sibling in siblings:
                if sibling.id != existing.id:
                    sibling.status = "retired"
                    sibling.updated_at = now
        self.session.commit()
        return existing

    def get_run(self, local_run_id: str) -> RemoteDecisionRunModel | None:
        return self.session.get(RemoteDecisionRunModel, local_run_id)

    def by_request_intent(self, request_intent_id: str) -> RemoteDecisionRunModel | None:
        return self.session.scalar(
            select(RemoteDecisionRunModel).where(RemoteDecisionRunModel.request_intent_id == request_intent_id)
        )

    def by_idempotency(self, idempotency_key: str) -> RemoteDecisionRunModel | None:
        return self.session.scalar(
            select(RemoteDecisionRunModel).where(RemoteDecisionRunModel.idempotency_key == idempotency_key)
        )

    def create_run(self, payload: dict[str, Any]) -> RemoteDecisionRunModel:
        now = utc_now()
        model = RemoteDecisionRunModel(
            id=str(payload.get("id") or uuid4()),
            anomalo_run_id=payload.get("anomalo_run_id"),
            intent_type=str(payload["intent_type"]),
            request_intent_id=str(payload["request_intent_id"]),
            idempotency_key=str(payload["idempotency_key"]),
            scope_type=str(payload["scope_type"]),
            scope_id=str(payload["scope_id"]),
            scope_version=payload.get("scope_version"),
            dataset_id=payload.get("dataset_id"),
            lens_type=payload.get("lens_type"),
            lens_id=payload.get("lens_id"),
            lens_version=payload.get("lens_version"),
            source_locator_json=dict(payload.get("source_locator_json") or {}),
            source_dataset_id=payload.get("source_dataset_id"),
            source_snapshot_id=payload.get("source_snapshot_id"),
            source_observation_run_id=payload.get("source_observation_run_id"),
            workflow_ref=str(payload["workflow_ref"]),
            definition_hash=str(payload["definition_hash"]),
            compiled_hash=str(payload["compiled_hash"]),
            input_schema_version=str(payload["input_schema_version"]),
            input_sha256=str(payload["input_sha256"]),
            input_json=dict(payload["input_json"]),
            metadata_json=dict(payload.get("metadata_json") or {}),
            trigger_mode=str(payload.get("trigger_mode", "manual")),
            trigger_source=str(payload["trigger_source"]),
            status="queued",
            remote_status=None,
            latest_event_sequence=0,
            validation_status="pending",
            preflight_fingerprint=str(payload["preflight_fingerprint"]),
            created_at=now,
            updated_at=now,
        )
        self.session.add(model)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise
        return model

    def update_remote(self, model: RemoteDecisionRunModel, **fields: Any) -> RemoteDecisionRunModel:
        for field, value in fields.items():
            if hasattr(model, field):
                setattr(model, field, value)
        model.updated_at = utc_now()
        self.session.commit()
        return model

    def transition(
        self,
        model: RemoteDecisionRunModel,
        status: str,
        *,
        remote_status: str | None = None,
        error_code: str | None = None,
        safe_error_message: str | None = None,
    ) -> RemoteDecisionRunModel:
        current = str(model.status)
        if status != current and status not in ALLOWED_TRANSITIONS.get(current, set()):
            # Duplicate/out-of-order remote events are ignored by the worker.
            # Invalid local transitions are an integration defect and should
            # never silently rewrite a terminal result.
            if current in TERMINAL_STATUSES:
                return model
            raise ValueError(f"非法 Remote Decision 状态转换：{current} -> {status}")
        model.status = status
        if remote_status is not None:
            model.remote_status = remote_status
        if error_code is not None:
            model.error_code = error_code
        if safe_error_message is not None:
            model.safe_error_message = safe_error_message[:2000]
        now = utc_now()
        if status == "submitting" and model.submitted_at is None:
            model.submitted_at = now
        if status == "running" and model.started_at is None:
            model.started_at = now
        if status in TERMINAL_STATUSES and model.completed_at is None:
            model.completed_at = now
        model.updated_at = now
        self.session.commit()
        return model

    def add_event(self, model: RemoteDecisionRunModel, payload: dict[str, Any]) -> RemoteDecisionEventModel:
        sequence = int(payload["sequence"])
        existing = self.session.scalar(
            select(RemoteDecisionEventModel).where(
                RemoteDecisionEventModel.local_run_id == model.id,
                RemoteDecisionEventModel.sequence == sequence,
            )
        )
        if existing is not None:
            return existing
        event = RemoteDecisionEventModel(
            id=str(uuid4()),
            local_run_id=model.id,
            sequence=sequence,
            event_type=str(payload.get("event_type") or payload.get("type") or "unknown"),
            event_timestamp=payload.get("event_timestamp"),
            node_id=payload.get("node_id"),
            attempt=payload.get("attempt"),
            child_run_id=payload.get("child_run_id"),
            safe_data_json=_safe_json(dict(payload.get("data") or {})),
            created_at=utc_now(),
        )
        self.session.add(event)
        model.latest_event_sequence = max(int(model.latest_event_sequence or 0), sequence)
        model.updated_at = utc_now()
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.session.scalar(
                select(RemoteDecisionEventModel).where(
                    RemoteDecisionEventModel.local_run_id == model.id,
                    RemoteDecisionEventModel.sequence == sequence,
                )
            )
            if existing is None:
                raise
            return existing
        return event

    def list_events(self, local_run_id: str, *, after_sequence: int = 0, limit: int = 500) -> list[RemoteDecisionEventModel]:
        return list(
            self.session.scalars(
                select(RemoteDecisionEventModel)
                .where(
                    RemoteDecisionEventModel.local_run_id == local_run_id,
                    RemoteDecisionEventModel.sequence > after_sequence,
                )
                .order_by(RemoteDecisionEventModel.sequence.asc())
                .limit(limit)
            )
        )

    def save_artifact(
        self,
        model: RemoteDecisionRunModel,
        *,
        artifact: dict[str, Any],
        artifact_sha256: str,
        validation_status: str,
    ) -> RemoteDecisionArtifactModel:
        existing = self.session.scalar(
            select(RemoteDecisionArtifactModel).where(RemoteDecisionArtifactModel.local_run_id == model.id)
        )
        if existing is not None:
            return existing
        now = utc_now()
        result = RemoteDecisionArtifactModel(
            id=str(uuid4()),
            local_run_id=model.id,
            output_schema_version=str(artifact.get("schema_version") or ""),
            completeness=str(artifact.get("completeness") or "insufficient_evidence"),
            artifact_json=artifact,
            artifact_sha256=artifact_sha256,
            evidence_refs_json=list(artifact.get("evidence_refs") or []),
            usage_json=dict(artifact.get("usage") or {}),
            trace_ref=artifact.get("trace_ref"),
            validation_status=validation_status,
            accepted_at=now if validation_status == "accepted" else None,
            created_at=now,
        )
        self.session.add(result)
        model.result_json = artifact if validation_status == "accepted" else None
        model.validation_status = validation_status
        model.updated_at = now
        self.session.commit()
        return result

    def get_artifact(self, local_run_id: str) -> RemoteDecisionArtifactModel | None:
        return self.session.scalar(
            select(RemoteDecisionArtifactModel).where(RemoteDecisionArtifactModel.local_run_id == local_run_id)
        )

    def list_runs(
        self,
        *,
        scope_type: str | None = None,
        scope_id: str | None = None,
        dataset_id: str | None = None,
        limit: int = 50,
    ) -> list[RemoteDecisionRunModel]:
        filters = []
        if scope_type:
            filters.append(RemoteDecisionRunModel.scope_type == scope_type)
        if scope_id:
            filters.append(RemoteDecisionRunModel.scope_id == scope_id)
        if dataset_id:
            filters.append(RemoteDecisionRunModel.dataset_id == dataset_id)
        return list(
            self.session.scalars(
                select(RemoteDecisionRunModel)
                .where(*filters)
                .order_by(RemoteDecisionRunModel.created_at.desc())
                .limit(limit)
            )
        )

    def active_runs(self) -> list[RemoteDecisionRunModel]:
        return list(
            self.session.scalars(
                select(RemoteDecisionRunModel)
                .where(RemoteDecisionRunModel.status.in_(ACTIVE_STATUSES))
                .order_by(RemoteDecisionRunModel.created_at.asc())
            )
        )

    def recoverable_runs(self) -> list[RemoteDecisionRunModel]:
        """Return runs which still need remote polling or artifact finalisation.

        ``succeeded`` is intentionally included here.  The supervisor records
        the remote terminal state before validating and storing the Artifact;
        a process crash in that small window must be recoverable on restart.
        """

        return list(
            self.session.scalars(
                select(RemoteDecisionRunModel)
                .where(RemoteDecisionRunModel.status.in_(RECOVERABLE_STATUSES))
                .order_by(RemoteDecisionRunModel.created_at.asc())
            )
        )


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _safe_json(item)
            for key, item in value.items()
            if not _is_sensitive_key(str(key))
        }
    if isinstance(value, list):
        return [_safe_json(item) for item in value]
    if isinstance(value, str):
        if _contains_sensitive_value(value):
            return "[redacted]"
    return value


def _is_sensitive_key(key: str) -> bool:
    """Recognise credential-shaped keys across common casing/separators."""

    import re

    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    normalized = re.sub(r"[^a-z0-9]+", "_", separated.lower()).strip("_")
    if normalized in {
        "authorization",
        "cookie",
        "set_cookie",
        "token",
        "access_token",
        "refresh_token",
        "service_token",
        "admin_token",
        "api_key",
        "apikey",
        "secret",
        "password",
        "credential",
        "credentials",
    }:
        return True
    return (
        normalized.startswith("token_")
        or normalized.endswith("_token")
        or "api_key" in normalized
        or normalized.endswith("_secret")
        or normalized.endswith("_password")
        or normalized.endswith("_credential")
    )


def _contains_sensitive_value(value: str) -> bool:
    import re

    return bool(
        re.search(
            r"(?:bearer\s+|(?:[a-z0-9-]+[_-])?(?:access|refresh|service|admin)?[_-]?token\s*[:=]\s*|(?:[a-z0-9-]+[_-])?api[_-]?key\s*[:=]\s*)",
            value,
            flags=re.IGNORECASE,
        )
    )


def _as_datetime(value: Any):
    if value is None or hasattr(value, "tzinfo"):
        return value
    try:
        from datetime import datetime

        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
