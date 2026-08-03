from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class EventScheduleInitializationModel(Base):
    """Persistent audit record for an explicit full schedule initialization."""

    __tablename__ = "event_schedule_initializations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_categories: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    requested_definitions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    requested_targets: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    discovered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    missing_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    api_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EventDefinitionModel(Base):
    """A configured obligation describing which future events Urus should find."""

    __tablename__ = "event_definitions"

    key: Mapped[str] = mapped_column(String(96), primary_key=True)
    category: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    discovery_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduled")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False, default=90)
    result_schema_name: Mapped[str] = mapped_column(String(96), nullable=False)
    metadata_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    events: Mapped[list[EventModel]] = relationship(back_populates="definition")


class EventModel(Base):
    """A single scheduled or discovered event and its lifecycle state."""

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_category_status_next_check", "category", "status", "next_check_at"),
        Index("ix_events_subject_scheduled", "subject", "scheduled_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_key: Mapped[str] = mapped_column(String(192), nullable=False, unique=True)
    definition_key: Mapped[str] = mapped_column(
        ForeignKey("event_definitions.key"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subject: Mapped[str] = mapped_column(String(96), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    period: Mapped[str | None] = mapped_column(String(64), nullable=True)
    discovery_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduled")
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    time_precision: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown")
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    announced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_expected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    result_available_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    latest_result_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    definition: Mapped[EventDefinitionModel] = relationship(back_populates="events")
    sources: Mapped[list[EventSourceModel]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    results: Mapped[list[EventResultModel]] = relationship(
        back_populates="event", cascade="all, delete-orphan", order_by="EventResultModel.version"
    )
    agent_runs: Mapped[list[EventAgentRunModel]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    market_reactions: Mapped[list[EventMarketReactionModel]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )


class EventSourceModel(Base):
    __tablename__ = "event_sources"
    __table_args__ = (
        Index("ix_event_sources_event_published", "event_id", "published_at"),
        UniqueConstraint("event_id", "url", name="uq_event_sources_event_url"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    publisher: Mapped[str] = mapped_column(String(160), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="secondary")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evidence_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    event: Mapped[EventModel] = relationship(back_populates="sources")


class EventResultModel(Base):
    __tablename__ = "event_results"
    __table_args__ = (
        Index("ix_event_results_event_captured", "event_id", "captured_at"),
        UniqueConstraint("event_id", "version", name="uq_event_results_event_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    result_status: Mapped[str] = mapped_column(String(24), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    facts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    guidance: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    needs_follow_up: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    event: Mapped[EventModel] = relationship(back_populates="results")


class EventAgentRunModel(Base):
    __tablename__ = "event_agent_runs"
    __table_args__ = (
        Index("ix_event_agent_runs_event_started", "event_id", "started_at"),
        Index("ix_event_agent_runs_operation_status", "operation", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_id: Mapped[str | None] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=True
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    agent: Mapped[str] = mapped_column(String(128), nullable=False)
    session_id: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    event: Mapped[EventModel | None] = relationship(back_populates="agent_runs")


class EventMarketReactionModel(Base):
    __tablename__ = "event_market_reactions"
    __table_args__ = (
        Index("ix_event_reactions_event_window", "event_id", "window"),
        UniqueConstraint(
            "event_id", "run_id", "window", name="uq_event_reactions_event_run_window"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    window: Mapped[str] = mapped_column(String(24), nullable=False)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    event: Mapped[EventModel] = relationship(back_populates="market_reactions")
