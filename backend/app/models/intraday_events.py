from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IntradaySetupStateModel(Base):
    """Latest serialized state for one processor and sidecar task."""

    __tablename__ = "intraday_setup_states"
    __table_args__ = (
        UniqueConstraint("processor_id", "task_name", name="uq_intraday_state_processor_task"),
        Index("ix_intraday_setup_states_task_updated", "task_name", "updated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    processor_id: Mapped[str] = mapped_column(String(96), nullable=False)
    task_name: Mapped[str] = mapped_column(String(128), nullable=False)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IntradayEventModel(Base):
    """Immutable setup transition, with a database generated monotonic id."""

    __tablename__ = "intraday_events"
    __table_args__ = (
        UniqueConstraint(
            "processor_id", "setup_id", "transition_seq", name="uq_intraday_event_setup_sequence"
        ),
        Index("ix_intraday_events_symbol_strategy_status", "symbol", "strategy", "status", "id"),
        Index("ix_intraday_events_task_cutoff", "task_name", "evidence_cutoff", "id"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    processor_id: Mapped[str] = mapped_column(String(96), nullable=False)
    setup_id: Mapped[str] = mapped_column(String(256), nullable=False)
    transition_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    task_name: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy: Mapped[str] = mapped_column(String(8), nullable=False)
    setup_type: Mapped[str] = mapped_column(String(64), nullable=False)
    transition: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(128), nullable=False)
    trading_date: Mapped[str] = mapped_column(String(10), nullable=False)
    evidence_cutoff: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class IntradayEventIngestCursorModel(Base):
    """Monotonic realtime ingest cursor for a fixed processor identity."""

    __tablename__ = "intraday_event_ingest_cursors"

    processor_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    source_path_identity: Mapped[str] = mapped_column(String(1024), nullable=False)
    last_scheduled_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_task_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_capture_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    seen_source_slots_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    backlog_remaining: Mapped[bool] = mapped_column(default=False, nullable=False)
    last_status: Mapped[str] = mapped_column(String(24), nullable=False, default="idle")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    capabilities_summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
