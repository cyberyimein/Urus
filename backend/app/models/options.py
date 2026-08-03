from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class OptionAnalysisBatchModel(Base):
    __tablename__ = "option_analysis_batches"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_option_batches_run_id"),
        UniqueConstraint("snapshot_id", name="uq_option_batches_snapshot_id"),
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
    model_version: Mapped[str] = mapped_column(String(32), nullable=False)
    risk_free_rate_percent: Mapped[float] = mapped_column(Float, nullable=False)
    dividend_yield_percent: Mapped[float] = mapped_column(Float, nullable=False)
    gamma_profile_range_percent: Mapped[float] = mapped_column(Float, nullable=False)
    gamma_profile_points: Mapped[int] = mapped_column(Integer, nullable=False)

    symbols: Mapped[list[OptionSymbolSnapshotModel]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class OptionSymbolSnapshotModel(Base):
    __tablename__ = "option_symbol_snapshots"
    __table_args__ = (
        UniqueConstraint("batch_id", "symbol", name="uq_option_symbols_batch_symbol"),
        Index("ix_option_symbols_symbol_batch", "symbol", "batch_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("option_analysis_batches.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    spot: Mapped[float] = mapped_column(Float, nullable=False)
    spot_time: Mapped[str | None] = mapped_column(String(64), nullable=True)
    overview: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    batch: Mapped[OptionAnalysisBatchModel] = relationship(back_populates="symbols")
    expirations: Mapped[list[OptionExpirationAnalysisModel]] = relationship(
        back_populates="symbol_snapshot", cascade="all, delete-orphan"
    )


class OptionExpirationAnalysisModel(Base):
    __tablename__ = "option_expiration_analyses"
    __table_args__ = (
        UniqueConstraint(
            "symbol_snapshot_id", "expiration", name="uq_option_expirations_symbol_date"
        ),
        Index("ix_option_expirations_date", "expiration"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    symbol_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("option_symbol_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    expiration: Mapped[date] = mapped_column(Date, nullable=False)
    days_to_expiry: Mapped[int] = mapped_column(Integer, nullable=False)
    contract_count: Mapped[int] = mapped_column(Integer, nullable=False)
    max_pain: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_move_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_move_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_move_atm_strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    exposure_totals: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    exposure_walls: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    profile_available: Mapped[bool] = mapped_column(Boolean, nullable=False)
    primary_gamma_flip: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_spot_net_gex: Mapped[float | None] = mapped_column(Float, nullable=True)
    usable_iv_contracts: Mapped[int] = mapped_column(Integer, nullable=False)
    profile_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    symbol_snapshot: Mapped[OptionSymbolSnapshotModel] = relationship(
        back_populates="expirations"
    )
    contracts: Mapped[list[OptionContractSnapshotModel]] = relationship(
        back_populates="expiration_analysis", cascade="all, delete-orphan"
    )
    profile_points: Mapped[list[OptionGammaProfilePointModel]] = relationship(
        back_populates="expiration_analysis", cascade="all, delete-orphan"
    )
    gamma_flips: Mapped[list[OptionGammaFlipModel]] = relationship(
        back_populates="expiration_analysis", cascade="all, delete-orphan"
    )


class OptionContractSnapshotModel(Base):
    __tablename__ = "option_contract_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "expiration_analysis_id", "code", name="uq_option_contracts_expiration_code"
        ),
        Index(
            "ix_option_contracts_expiration_strike_type",
            "expiration_analysis_id",
            "strike",
            "option_type",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    expiration_analysis_id: Mapped[str] = mapped_column(
        ForeignKey("option_expiration_analyses.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(96), nullable=False)
    option_type: Mapped[str] = mapped_column(String(8), nullable=False)
    strike: Mapped[float] = mapped_column(Float, nullable=False)
    spot: Mapped[float] = mapped_column(Float, nullable=False)
    multiplier: Mapped[float] = mapped_column(Float, nullable=False)
    bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    last: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[int] = mapped_column(Integer, nullable=False)
    open_interest: Mapped[int] = mapped_column(Integer, nullable=False)
    implied_volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    gamma: Mapped[float | None] = mapped_column(Float, nullable=True)
    quote_time: Mapped[str | None] = mapped_column(String(64), nullable=True)

    expiration_analysis: Mapped[OptionExpirationAnalysisModel] = relationship(
        back_populates="contracts"
    )


class OptionGammaProfilePointModel(Base):
    __tablename__ = "option_gamma_profile_points"
    __table_args__ = (
        UniqueConstraint(
            "expiration_analysis_id", "point_index", name="uq_option_profile_expiration_point"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    expiration_analysis_id: Mapped[str] = mapped_column(
        ForeignKey("option_expiration_analyses.id", ondelete="CASCADE"), nullable=False
    )
    point_index: Mapped[int] = mapped_column(Integer, nullable=False)
    hypothetical_spot: Mapped[float] = mapped_column(Float, nullable=False)
    call_gex: Mapped[float] = mapped_column(Float, nullable=False)
    put_gex: Mapped[float] = mapped_column(Float, nullable=False)
    net_gex: Mapped[float] = mapped_column(Float, nullable=False)

    expiration_analysis: Mapped[OptionExpirationAnalysisModel] = relationship(
        back_populates="profile_points"
    )


class OptionGammaFlipModel(Base):
    __tablename__ = "option_gamma_flips"
    __table_args__ = (
        UniqueConstraint(
            "expiration_analysis_id", "position", name="uq_option_flips_expiration_position"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    expiration_analysis_id: Mapped[str] = mapped_column(
        ForeignKey("option_expiration_analyses.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[float] = mapped_column(Float, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False)

    expiration_analysis: Mapped[OptionExpirationAnalysisModel] = relationship(
        back_populates="gamma_flips"
    )
