from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class InstrumentAnalysisBatchModel(Base):
    __tablename__ = "instrument_analysis_batches"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_instrument_batches_run_id"),
        UniqueConstraint("snapshot_id", name="uq_instrument_batches_snapshot_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("snapshots.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    source_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    persisted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    feature_version: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_symbols: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    quota_audit: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    instruments: Mapped[list[InstrumentSnapshotModel]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class InstrumentSnapshotModel(Base):
    __tablename__ = "instrument_snapshots"
    __table_args__ = (
        UniqueConstraint("batch_id", "symbol", name="uq_instrument_snapshots_batch_symbol"),
        Index("ix_instrument_snapshots_symbol_captured", "symbol", "captured_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("instrument_analysis_batches.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(16), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quote_time: Mapped[str | None] = mapped_column(String(64), nullable=True)
    spot: Mapped[float | None] = mapped_column(Float, nullable=True)
    previous_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality_status: Mapped[str] = mapped_column(String(24), nullable=False)
    quote_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    history_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    feature_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    relative_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    batch: Mapped[InstrumentAnalysisBatchModel] = relationship(back_populates="instruments")
    bars: Mapped[list[InstrumentDailyBarModel]] = relationship(
        back_populates="instrument", cascade="all, delete-orphan"
    )


class InstrumentDailyBarModel(Base):
    __tablename__ = "instrument_daily_bars"
    __table_args__ = (
        UniqueConstraint(
            "instrument_snapshot_id",
            "bar_date",
            "adjustment",
            name="uq_instrument_bars_snapshot_date_adjustment",
        ),
        Index("ix_instrument_bars_symbol_date", "instrument_snapshot_id", "bar_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("instrument_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    bar_date: Mapped[date] = mapped_column(Date, nullable=False)
    adjustment: Mapped[str] = mapped_column(String(16), nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    turnover: Mapped[float | None] = mapped_column(Float, nullable=True)
    turnover_rate: Mapped[float | None] = mapped_column(Float, nullable=True)

    instrument: Mapped[InstrumentSnapshotModel] = relationship(back_populates="bars")
