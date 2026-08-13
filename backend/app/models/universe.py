from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class InstrumentUniverseVersionModel(Base):
    __tablename__ = "instrument_universe_versions"
    __table_args__ = (
        Index("ix_instrument_universe_versions_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    # The same content may intentionally be restored as a later revision.
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="runtime")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    items: Mapped[list[InstrumentUniverseItemModel]] = relationship(
        back_populates="version",
        cascade="all, delete-orphan",
        order_by="InstrumentUniverseItemModel.position",
    )


class InstrumentUniverseItemModel(Base):
    __tablename__ = "instrument_universe_items"
    __table_args__ = (
        UniqueConstraint("version_id", "symbol", name="uq_universe_version_symbol"),
        Index("ix_universe_items_version_position", "version_id", "position"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("instrument_universe_versions.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(16), nullable=False)
    theme: Mapped[str] = mapped_column(String(64), nullable=False)
    themes: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    roles: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    benchmarks: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    collection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    version: Mapped[InstrumentUniverseVersionModel] = relationship(back_populates="items")
