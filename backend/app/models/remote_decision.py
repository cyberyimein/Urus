from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DecisionWorkflowBindingModel(Base):
    """Immutable-ish release record for one published Anomalo Workflow Ref."""

    __tablename__ = "decision_workflow_bindings"
    __table_args__ = (
        UniqueConstraint("intent_type", "workflow_ref", name="uq_decision_binding_intent_ref"),
        Index("ix_decision_binding_intent_status", "intent_type", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    intent_type: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="disabled")
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    compiled_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    capability_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_schema_version: Mapped[str] = mapped_column(String(128), nullable=False)
    output_schema_version: Mapped[str] = mapped_column(String(128), nullable=False)
    definition_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RemoteDecisionRunModel(Base):
    """Local lifecycle and frozen input for one remote Workflow invocation."""

    __tablename__ = "remote_decision_runs"
    __table_args__ = (
        Index("ix_remote_decision_runs_scope", "scope_type", "scope_id"),
        Index("ix_remote_decision_runs_status", "status"),
        Index("ix_remote_decision_runs_created", "created_at"),
        Index("ix_remote_decision_runs_anomalo_run_id", "anomalo_run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    anomalo_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    intent_type: Mapped[str] = mapped_column(String(64), nullable=False)
    request_intent_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(128), nullable=False)
    scope_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dataset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lens_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lens_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lens_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_locator_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    source_dataset_id: Mapped[str | None] = mapped_column(
        ForeignKey("daily_decision_datasets.id", ondelete="RESTRICT"), nullable=True
    )
    source_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("group_daily_snapshots.id", ondelete="RESTRICT"), nullable=True
    )
    source_observation_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("observation_runs.id", ondelete="RESTRICT"), nullable=True
    )
    workflow_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    compiled_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_schema_version: Mapped[str] = mapped_column(String(128), nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    trigger_mode: Mapped[str] = mapped_column(String(24), nullable=False, default="manual")
    trigger_source: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    remote_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    latest_event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    validation_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    preflight_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    safe_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RemoteDecisionEventModel(Base):
    """Sanitised, replayable event stream keyed by local run and sequence."""

    __tablename__ = "remote_decision_events"
    __table_args__ = (
        UniqueConstraint("local_run_id", "sequence", name="uq_remote_decision_event_run_sequence"),
        Index("ix_remote_decision_events_run_created", "local_run_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    local_run_id: Mapped[str] = mapped_column(
        ForeignKey("remote_decision_runs.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(96), nullable=False)
    event_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    node_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    child_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    safe_data_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RemoteDecisionArtifactModel(Base):
    """One immutable accepted/rejected result envelope per local run."""

    __tablename__ = "remote_decision_artifacts"
    __table_args__ = (UniqueConstraint("local_run_id", name="uq_remote_decision_artifact_run"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    local_run_id: Mapped[str] = mapped_column(
        ForeignKey("remote_decision_runs.id", ondelete="CASCADE"), nullable=False
    )
    output_schema_version: Mapped[str] = mapped_column(String(128), nullable=False)
    completeness: Mapped[str] = mapped_column(String(32), nullable=False)
    artifact_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_refs_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    usage_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    trace_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    validation_status: Mapped[str] = mapped_column(String(24), nullable=False, default="accepted")
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
