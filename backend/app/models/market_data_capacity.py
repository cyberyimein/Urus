from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class HistoryQuotaSnapshotModel(Base):
    """Latest observation of a provider's rolling historical-bar quota.

    Urus is a single-user application, so one row per provider/quota kind is
    sufficient.  Captures overwrite this row instead of building an unbounded
    audit log.
    """

    __tablename__ = "moomoo_history_quota_snapshots"
    __table_args__ = (
        UniqueConstraint("provider", "quota_kind", name="uq_history_quota_provider_kind"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    quota_kind: Mapped[str] = mapped_column(String(64), nullable=False, default="history_candlestick")
    available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    used_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    remain_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    quality_status: Mapped[str] = mapped_column(String(24), nullable=False, default="unavailable")
    warning: Mapped[str | None] = mapped_column(Text, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HistoryCollectionStateModel(Base):
    """Operational state projected separately from immutable Universe versions."""

    __tablename__ = "history_collection_states"
    __table_args__ = (
        UniqueConstraint("provider", "symbol", name="uq_history_collection_state_provider_symbol"),
        Index("ix_history_collection_states_access_state", "access_state"),
        Index("ix_history_collection_states_updated", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False, default="XNYS")
    adjustment: Mapped[str] = mapped_column(String(16), nullable=False, default="QFQ")
    access_state: Mapped[str] = mapped_column(String(32), nullable=False, default="not_requested")
    quality_state: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown")
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    desired_history: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    universe_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("instrument_universe_versions.id", ondelete="SET NULL"), nullable=True
    )
    capacity_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("moomoo_history_quota_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    bar_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latest_bar_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    required_through_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    minimum_bar_count: Mapped[int] = mapped_column(Integer, nullable=False, default=260)
    quota_cost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_deferred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
