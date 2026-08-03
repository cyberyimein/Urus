from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import as_utc, utc_now
from app.models import (
    RunModel,
    SnapshotModel,
    StepRunModel,
    StrategyResearchDatasetModel,
)
from app.repositories.events import EventRepository


class StrategyResearchRepository:
    """Store and retrieve evidence bundles reserved for Step 4 research."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def capture_run(
        self,
        *,
        run_id: str,
        dataset_key: str,
        label: str,
        captured_at: datetime | None = None,
        note: str | None = None,
    ) -> StrategyResearchDatasetModel:
        run = self.session.get(RunModel, run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        if not run.snapshot_id:
            raise ValueError(f"Run has no snapshot: {run_id}")
        snapshot = self.session.get(SnapshotModel, run.snapshot_id)
        if snapshot is None:
            raise ValueError(f"Snapshot not found: {run.snapshot_id}")

        steps = list(
            self.session.scalars(
                select(StepRunModel)
                .where(StepRunModel.run_id == run_id)
                .order_by(StepRunModel.position)
            )
        )
        step_payloads = {
            step.step_code: {
                "status": step.status,
                "started_at": step.started_at.isoformat() if step.started_at else None,
                "completed_at": step.completed_at.isoformat() if step.completed_at else None,
                "summary": step.summary,
                "error_message": step.error_message,
                "payload": step.payload or {},
            }
            for step in steps
        }
        event_steps = {
            code: {
                "status": step_payloads.get(code, {}).get("status", "missing"),
                "summary": step_payloads.get(code, {}).get("summary"),
            }
            for code in ("1b", "3b")
        }
        event_complete = all(
            details["status"] == "succeeded" for details in event_steps.values()
        )
        events = [
            EventRepository.event_payload(event)
            for category in ("macro", "instrument")
            for event in EventRepository(self.session).list_events(category)
        ]
        payload = {
            "schema_version": "step4_strategy_evidence.v1",
            "source": {
                "run_id": run.id,
                "snapshot_id": snapshot.id,
                "run_type": run.run_type,
                "run_status": run.status,
                "cutoff_time": as_utc(run.cutoff_time).isoformat(),
                "captured_at": as_utc(captured_at or snapshot.created_at).isoformat(),
            },
            "snapshot": snapshot.payload,
            "steps": step_payloads,
            "events": events,
        }
        event_status = {
            "complete": event_complete,
            "steps": event_steps,
            "event_count": len(events),
            "note": (
                "1B and 3B are complete and can be used by Step 4."
                if event_complete
                else "1B and/or 3B are incomplete; treat this dataset as pending event enrichment."
            ),
        }
        model = self.session.scalar(
            select(StrategyResearchDatasetModel).where(
                StrategyResearchDatasetModel.source_run_id == run_id
            )
        )
        timestamp = utc_now()
        values = {
            "dataset_key": dataset_key,
            "label": label,
            "status": "ready" if event_complete else "pending_events",
            "source_snapshot_id": snapshot.id,
            "source_run_type": run.run_type,
            "captured_at": captured_at or snapshot.created_at,
            "event_collection_status": event_status,
            "payload": payload,
            "metadata_payload": {
                "source_run_status": run.status,
                "source_step_statuses": {
                    code: details["status"] for code, details in event_steps.items()
                },
            },
            "note": note
            or (
                "Captured for Step 4 strategy AI study; event steps are pending."
                if not event_complete
                else "Captured for Step 4 strategy AI study."
            ),
        }
        if model is None:
            model = StrategyResearchDatasetModel(
                id=str(uuid4()),
                source_run_id=run_id,
                created_at=timestamp,
                **values,
            )
            self.session.add(model)
        else:
            for key, value in values.items():
                setattr(model, key, value)
        self.session.commit()
        return model
