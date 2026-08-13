from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ReportDisplayProjectionModel(Base):
    """Immutable, chart-oriented projection for one research report.

    The projection deliberately lives outside the AI report JSON.  It can
    therefore contain every persisted strike row and gamma-profile point
    without increasing the model input packet or the initial report payload.
    """

    __tablename__ = "report_display_projections"
    __table_args__ = (UniqueConstraint("report_id", name="uq_report_display_report"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    report_id: Mapped[str] = mapped_column(
        ForeignKey("ai_decision_sessions.id", ondelete="CASCADE"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_snapshot_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    source_run_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
