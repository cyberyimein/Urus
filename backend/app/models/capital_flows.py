from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Float, Index, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CapitalFlowDailyModel(Base):
    """One provider-classified regular-session capital-flow observation."""

    __tablename__ = "capital_flow_daily"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "symbol",
            "trading_date",
            "period_type",
            name="uq_capital_flow_daily_provider_symbol_date_period",
        ),
        Index(
            "ix_capital_flow_daily_symbol_date",
            "symbol",
            "trading_date",
        ),
    )

    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    trading_date: Mapped[date] = mapped_column(Date, primary_key=True)
    period_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    in_flow: Mapped[float | None] = mapped_column(Float, nullable=True)
    main_in_flow: Mapped[float | None] = mapped_column(Float, nullable=True)
    super_in_flow: Mapped[float | None] = mapped_column(Float, nullable=True)
    big_in_flow: Mapped[float | None] = mapped_column(Float, nullable=True)
    mid_in_flow: Mapped[float | None] = mapped_column(Float, nullable=True)
    sml_in_flow: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_time: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quality_status: Mapped[str] = mapped_column(String(24), nullable=False)
    quality_warnings: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
