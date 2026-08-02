from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class RunModel(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cutoff_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    steps: Mapped[list[StepRunModel]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="StepRunModel.position",
    )


class StepRunModel(Base):
    __tablename__ = "step_runs"
    __table_args__ = (
        UniqueConstraint("run_id", "step_code", name="uq_step_runs_run_step"),
        Index("ix_step_runs_run_position", "run_id", "position"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    step_code: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    run: Mapped[RunModel] = relationship(back_populates="steps")


class SnapshotModel(Base):
    __tablename__ = "snapshots"
    __table_args__ = (UniqueConstraint("run_id", name="uq_snapshots_run_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    cutoff_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quality_status: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

