from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ObservationUniverseRevisionModel(Base):
    """Immutable audit record for the Universe used by Phase C synchronization."""

    __tablename__ = "observation_universe_revisions"
    __table_args__ = (
        UniqueConstraint(
            "source_url",
            "content_sha256",
            name="uq_observation_universe_source_content",
        ),
        Index("ix_observation_universe_revisions_source_fetched", "source_url", "fetched_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_url: Mapped[str] = mapped_column(String(512), nullable=False)
    upstream_version_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    upstream_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    local_universe_version_id: Mapped[str] = mapped_column(
        ForeignKey("instrument_universe_versions.id", ondelete="RESTRICT"), nullable=False
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ObservationGroupVersionModel(Base):
    __tablename__ = "observation_group_versions"
    __table_args__ = (
        UniqueConstraint("group_id", "version", name="uq_observation_group_id_version"),
        Index("ix_observation_group_status_order", "status", "display_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="manual")
    universe_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("observation_universe_revisions.id", ondelete="SET NULL"), nullable=True
    )
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    symbols: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    benchmark_symbols: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    tags: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GroupDailySnapshotModel(Base):
    __tablename__ = "group_daily_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "group_version_id",
            "dataset_id",
            "snapshot_schema_version",
            name="uq_group_daily_snapshot_version_dataset_schema",
        ),
        Index("ix_group_daily_snapshots_group_date", "group_id", "trading_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    group_version_id: Mapped[str] = mapped_column(
        ForeignKey("observation_group_versions.id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("daily_decision_datasets.id", ondelete="CASCADE"), nullable=False
    )
    group_id: Mapped[str] = mapped_column(String(64), nullable=False)
    group_version: Mapped[int] = mapped_column(Integer, nullable=False)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    snapshot_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ObservationRunModel(Base):
    __tablename__ = "observation_runs"
    __table_args__ = (
        Index("ix_observation_runs_date_status", "trading_date", "status"),
        Index("ix_observation_runs_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    trigger_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    universe_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("observation_universe_revisions.id", ondelete="SET NULL"), nullable=True
    )
    universe_freshness: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    universe_source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    cutoff_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    group_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    group_version_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
