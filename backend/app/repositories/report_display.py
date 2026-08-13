from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import ReportDisplayProjectionModel


class ReportDisplayRepository:
    """Persistence boundary for the report-only display projection."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, report_id: str) -> ReportDisplayProjectionModel | None:
        return self.session.scalar(
            select(ReportDisplayProjectionModel).where(
                ReportDisplayProjectionModel.report_id == report_id
            )
        )

    def save(
        self,
        *,
        report_id: str,
        payload: dict[str, Any],
        source_snapshot_ids: list[str],
        source_run_ids: list[str],
        content_sha256: str,
        schema_version: str,
        created_at: datetime | None = None,
    ) -> ReportDisplayProjectionModel:
        model = self.get(report_id)
        if model is None:
            model = ReportDisplayProjectionModel(
                id=str(uuid4()),
                report_id=report_id,
                schema_version=schema_version,
                source_snapshot_ids=list(source_snapshot_ids),
                source_run_ids=list(source_run_ids),
                payload_json=payload,
                content_sha256=content_sha256,
                created_at=created_at or utc_now(),
            )
        else:
            model.schema_version = schema_version
            model.source_snapshot_ids = list(source_snapshot_ids)
            model.source_run_ids = list(source_run_ids)
            model.payload_json = payload
            model.content_sha256 = content_sha256
            model.created_at = created_at or utc_now()
        self.session.add(model)
        self.session.commit()
        return model

    def delete(self, report_id: str) -> bool:
        model = self.get(report_id)
        if model is None:
            return False
        self.session.delete(model)
        self.session.commit()
        return True
