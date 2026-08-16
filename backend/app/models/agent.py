from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AIDecisionSessionModel(Base):
    """One Stage 4B orchestration and report version."""

    __tablename__ = "ai_decision_sessions"
    __table_args__ = (
        Index("ix_ai_decision_sessions_workflow_created", "workflow_run_id", "created_at"),
        Index("ix_ai_decision_sessions_status_created", "status", "created_at"),
        Index("ix_ai_decision_sessions_trading_phase", "trading_date", "decision_phase"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    dataset_key: Mapped[str] = mapped_column(String(128), nullable=False)
    cutoff_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decision_phase: Mapped[str] = mapped_column(String(32), nullable=False, default="pre_close")
    trading_date: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    parent_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    policy_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    technical_report_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    technical_report_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    decision_report_schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision_report_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    equity_decision_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AIDecisionRunModel(Base):
    """Immutable audit record for one Urus Agent decision attempt."""

    __tablename__ = "ai_decision_runs"
    __table_args__ = (
        Index("ix_ai_decision_runs_dataset_task", "dataset_key", "task_type", "created_at"),
        Index("ix_ai_decision_runs_target_created", "target_symbol", "created_at"),
        Index("ix_ai_decision_runs_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    decision_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    workflow_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    parent_decision_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    stage: Mapped[str] = mapped_column(String(24), nullable=False, default="equity")
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    dataset_key: Mapped[str] = mapped_column(String(128), nullable=False)
    source_run_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    source_snapshot_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    cutoff_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    target_symbol: Mapped[str | None] = mapped_column(String(16), nullable=True)
    requested_symbols: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    skill_name: Mapped[str] = mapped_column(String(128), nullable=False)
    skill_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_output: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AIToolCallModel(Base):
    """One ordered tool call belonging to an AI decision run."""

    __tablename__ = "ai_tool_calls"
    __table_args__ = (
        UniqueConstraint("decision_run_id", "sequence", name="uq_ai_tool_calls_run_sequence"),
        Index("ix_ai_tool_calls_run_sequence", "decision_run_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    decision_run_id: Mapped[str] = mapped_column(
        ForeignKey("ai_decision_runs.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prefetched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ForecastExperienceModel(Base):
    """A reusable, falsifiable lesson derived from completed forecast reviews."""

    __tablename__ = "forecast_experiences"
    __table_args__ = (
        UniqueConstraint("pattern_key", name="uq_forecast_experiences_pattern_key"),
        Index("ix_forecast_experiences_status_last_seen", "status", "last_seen_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    pattern_key: Mapped[str] = mapped_column(String(96), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    applicability_tags: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    evidence_refs: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="candidate")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    support_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    contradiction_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_report_id: Mapped[str | None] = mapped_column(
        ForeignKey("ai_decision_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_pre_market_report_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    first_seen_trading_date: Mapped[str] = mapped_column(String(10), nullable=False)
    last_seen_trading_date: Mapped[str] = mapped_column(String(10), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AITraceNodeModel(Base):
    """Bounded graph node for the user-facing Decision Trace."""

    __tablename__ = "ai_trace_nodes"
    __table_args__ = (
        Index("ix_ai_trace_nodes_session_sequence", "decision_session_id", "sequence"),
        Index("ix_ai_trace_nodes_run_sequence", "decision_run_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    decision_session_id: Mapped[str] = mapped_column(
        ForeignKey("ai_decision_sessions.id", ondelete="CASCADE"), nullable=False
    )
    decision_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    parent_node_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    depends_on_node_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    lane: Mapped[str] = mapped_column(String(32), nullable=False)
    node_type: Mapped[str] = mapped_column(String(24), nullable=False)
    label: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    input_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    output_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    evidence_refs: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AIModelTurnModel(Base):
    """Raw provider response for one model turn, available on explicit inspection."""

    __tablename__ = "ai_model_turns"
    __table_args__ = (
        UniqueConstraint("decision_run_id", "sequence", name="uq_ai_model_turns_run_sequence"),
        Index("ix_ai_model_turns_node_sequence", "trace_node_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    decision_run_id: Mapped[str] = mapped_column(
        ForeignKey("ai_decision_runs.id", ondelete="CASCADE"), nullable=False
    )
    trace_node_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    response_message: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    raw_provider_response: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    raw_response_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    raw_response_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
