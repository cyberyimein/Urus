from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DailyBarModel(Base):
    """Canonical completed daily OHLCV bar shared by all future scopes."""

    __tablename__ = "daily_bars"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "exchange",
            "bar_date",
            "adjustment",
            "source",
            "source_revision",
            name="uq_daily_bars_symbol_exchange_date_adjustment_source",
        ),
        Index("ix_daily_bars_symbol_date", "symbol", "bar_date"),
        Index("ix_daily_bars_date_source", "bar_date", "source"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False, default="XNYS")
    asset_type: Mapped[str] = mapped_column(String(16), nullable=False, default="equity")
    bar_date: Mapped[date] = mapped_column(Date, nullable=False)
    market_timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="America/New_York")
    adjustment: Mapped[str] = mapped_column(String(16), nullable=False, default="QFQ")
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    turnover: Mapped[float | None] = mapped_column(Float, nullable=True)
    turnover_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    corrected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quality_status: Mapped[str] = mapped_column(String(24), nullable=False, default="ok")
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class DailyIndicatorSnapshotModel(Base):
    """Versioned latest indicator vector for one canonical bar window."""

    __tablename__ = "daily_indicator_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "exchange",
            "bar_date",
            "adjustment",
            "feature_version",
            "input_bar_hash",
            name="uq_daily_indicators_symbol_date_feature_input",
        ),
        Index("ix_daily_indicators_symbol_date", "symbol", "bar_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False, default="XNYS")
    bar_date: Mapped[date] = mapped_column(Date, nullable=False)
    adjustment: Mapped[str] = mapped_column(String(16), nullable=False, default="QFQ")
    feature_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_bar_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    quality_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DailyDecisionDatasetModel(Base):
    """Immutable frozen evidence package for one daily Decision Scope."""

    __tablename__ = "daily_decision_datasets"
    __table_args__ = (
        UniqueConstraint("content_sha256", name="uq_daily_decision_datasets_content_hash"),
        Index("ix_daily_decision_datasets_scope_date", "scope_type", "scope_id", "trading_date"),
        Index("ix_daily_decision_datasets_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(24), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(128), nullable=False)
    scope_version: Mapped[int | None] = mapped_column(nullable=True)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    cutoff_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    market_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    bar_completion_policy: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    scope_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    bar_manifest_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    indicator_snapshot_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    group_snapshot_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    quality_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DecisionChartProjectionModel(Base):
    """Immutable chart-oriented projection derived from a daily dataset."""

    __tablename__ = "decision_chart_projections"
    __table_args__ = (
        UniqueConstraint("dataset_id", name="uq_decision_chart_projections_dataset"),
        Index("ix_decision_chart_projections_scope", "scope_type", "scope_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("daily_decision_datasets.id", ondelete="CASCADE"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(24), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
