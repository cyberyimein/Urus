from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class StrategyDecisionModel(Base):
    """Immutable deterministic strategy output for one frozen dataset."""

    __tablename__ = "strategy_decisions"
    __table_args__ = (
        UniqueConstraint(
            "dataset_id",
            "strategy_set_sha256",
            "symbol",
            "strategy_name",
            "strategy_version",
            "implementation_sha256",
            name="uq_strategy_decisions_dataset_strategy_symbol_version",
        ),
        Index("ix_strategy_decisions_dataset_symbol", "dataset_id", "symbol"),
        Index("ix_strategy_decisions_strategy_created", "strategy_name", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("daily_decision_datasets.id", ondelete="CASCADE"), nullable=False
    )
    scope_type: Mapped[str] = mapped_column(String(24), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_set_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    implementation_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DeterministicSynthesisModel(Base):
    """Immutable transparent synthesis of all strategy decisions."""

    __tablename__ = "deterministic_syntheses"
    __table_args__ = (
        UniqueConstraint(
            "dataset_id",
            "strategy_set_sha256",
            name="uq_deterministic_syntheses_dataset_strategy_set",
        ),
        Index("ix_deterministic_syntheses_dataset_created", "dataset_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("daily_decision_datasets.id", ondelete="CASCADE"), nullable=False
    )
    scope_type: Mapped[str] = mapped_column(String(24), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy_set_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
