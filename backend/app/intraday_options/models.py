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
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class IntradayOptionBase(DeclarativeBase):
    """Metadata boundary for the dedicated intraday option database."""


class IntradayOptionCaptureRunModel(IntradayOptionBase):
    __tablename__ = "intraday_option_capture_runs"
    __table_args__ = (
        UniqueConstraint(
            "trading_date",
            "scheduled_at",
            "capture_tier",
            "schema_version",
            "capture_spec_hash",
            name="uq_intraday_option_logical_capture",
        ),
        Index("ix_intraday_option_capture_status", "status"),
        Index("ix_intraday_option_capture_date", "trading_date", "scheduled_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    capture_spec_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_time_min: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_time_max: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    market_session: Mapped[str] = mapped_column(String(24), nullable=False, default="regular")
    symbols_requested: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    symbols_completed: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    expirations_requested: Mapped[dict[str, list[str]]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    request_band_percent: Mapped[float] = mapped_column(Float, nullable=False)
    capture_tier: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="scheduled")
    is_late_capture: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    selected_attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    quality_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IntradayOptionCaptureAttemptModel(IntradayOptionBase):
    __tablename__ = "intraday_option_capture_attempts"
    __table_args__ = (
        UniqueConstraint("capture_run_id", "attempt_number", name="uq_intraday_option_attempt"),
        Index("ix_intraday_option_attempt_run", "capture_run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    capture_run_id: Mapped[str] = mapped_column(
        ForeignKey("intraday_option_capture_runs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="collecting")
    raw_payload_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    warning: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class IntradayUnderlyingMinuteBarModel(IntradayOptionBase):
    __tablename__ = "intraday_underlying_minute_bars"
    __table_args__ = (
        UniqueConstraint("symbol", "bar_time", "provider", name="uq_intraday_underlying_bar"),
        Index("ix_intraday_underlying_bar_time", "bar_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    bar_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    turnover: Mapped[float | None] = mapped_column(Float, nullable=True)
    vwap: Mapped[float | None] = mapped_column(Float, nullable=True)
    session: Mapped[str] = mapped_column(String(24), nullable=False, default="regular")
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quality_state: Mapped[str] = mapped_column(String(24), nullable=False, default="ready")


class IntradayUnderlyingMinuteBarRevisionModel(IntradayOptionBase):
    __tablename__ = "intraday_underlying_minute_bar_revisions"
    __table_args__ = (
        UniqueConstraint(
            "symbol", "bar_time", "provider", "content_sha256",
            name="uq_intraday_underlying_bar_revision",
        ),
        Index("ix_intraday_underlying_bar_revision_time", "symbol", "bar_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    capture_run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    capture_attempt_id: Mapped[str] = mapped_column(String(36), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    bar_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IntradayUnderlyingSnapshotModel(IntradayOptionBase):
    __tablename__ = "intraday_underlying_snapshots"
    __table_args__ = (
        UniqueConstraint("capture_attempt_id", "symbol", name="uq_intraday_underlying_snapshot"),
        Index("ix_intraday_underlying_snapshot_run", "capture_run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    capture_run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    capture_attempt_id: Mapped[str] = mapped_column(String(36), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    spot: Mapped[float] = mapped_column(Float, nullable=False)
    quote_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    request_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quote_age_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data_state: Mapped[str] = mapped_column(String(24), nullable=False)


class IntradayOptionContractModel(IntradayOptionBase):
    __tablename__ = "intraday_option_contracts"
    __table_args__ = (
        Index("ix_intraday_option_contract_expiration", "underlying_symbol", "expiration"),
    )

    contract_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    underlying_symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    option_type: Mapped[str] = mapped_column(String(8), nullable=False)
    expiration: Mapped[date] = mapped_column(Date, nullable=False)
    strike: Mapped[float] = mapped_column(Float, nullable=False)
    contract_multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    exercise_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class IntradayOptionContractSnapshotModel(IntradayOptionBase):
    __tablename__ = "intraday_option_contract_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "capture_attempt_id", "contract_code", name="uq_intraday_option_snapshot"
        ),
        Index("ix_intraday_option_snapshot_contract", "contract_code", "quote_time"),
        Index("ix_intraday_option_snapshot_run", "capture_run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    capture_run_id: Mapped[str] = mapped_column(
        ForeignKey("intraday_option_capture_runs.id", ondelete="CASCADE"), nullable=False
    )
    capture_attempt_id: Mapped[str] = mapped_column(
        ForeignKey("intraday_option_capture_attempts.id", ondelete="CASCADE"), nullable=False
    )
    underlying_symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    underlying_spot: Mapped[float] = mapped_column(Float, nullable=False)
    contract_code: Mapped[str] = mapped_column(
        ForeignKey("intraday_option_contracts.contract_code", ondelete="RESTRICT"), nullable=False
    )
    days_to_expiry: Mapped[int] = mapped_column(Integer, nullable=False)
    bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    bid_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    ask_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    last: Mapped[float | None] = mapped_column(Float, nullable=True)
    mark_or_mid: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    open_interest: Mapped[int | None] = mapped_column(Integer, nullable=True)
    implied_volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    gamma: Mapped[float | None] = mapped_column(Float, nullable=True)
    vega: Mapped[float | None] = mapped_column(Float, nullable=True)
    theta: Mapped[float | None] = mapped_column(Float, nullable=True)
    rho: Mapped[float | None] = mapped_column(Float, nullable=True)
    quote_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    request_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quote_age_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data_state: Mapped[str] = mapped_column(String(24), nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class IntradayOptionOutcomeModel(IntradayOptionBase):
    __tablename__ = "intraday_option_outcomes"
    __table_args__ = (
        UniqueConstraint("capture_run_id", "symbol", name="uq_intraday_option_outcome"),
        Index("ix_intraday_option_outcome_date", "trading_date", "symbol"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    capture_run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    entry_spot: Mapped[float] = mapped_column(Float, nullable=False)
    official_close: Mapped[float] = mapped_column(Float, nullable=False)
    close_source: Mapped[str] = mapped_column(String(64), nullable=False)
    close_return: Mapped[float] = mapped_column(Float, nullable=False)
    absolute_move: Mapped[float] = mapped_column(Float, nullable=False)
    max_up_move: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_down_move: Mapped[float | None] = mapped_column(Float, nullable=True)
    finalized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
