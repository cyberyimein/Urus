from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import RunModel, SnapshotModel, StepRunModel


class RunRepository:
    """Persistence boundary for workflow runs, steps, and snapshots."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_run(
        self,
        *,
        run_id: str,
        run_type: str,
        cutoff_time: datetime,
    ) -> RunModel:
        run = RunModel(
            id=run_id,
            run_type=run_type,
            status="pending",
            cutoff_time=cutoff_time,
        )
        self.session.add(run)
        self.session.commit()
        return run

    def create_steps(self, run_id: str, steps: list[tuple[str, int, str, str]]) -> list[StepRunModel]:
        models = [
            StepRunModel(
                id=step_id,
                run_id=run_id,
                position=position,
                step_code=step_code,
                status=status,
            )
            for step_id, position, step_code, status in steps
        ]
        self.session.add_all(models)
        self.session.commit()
        return models

    def get_run(self, run_id: str) -> RunModel | None:
        statement = (
            select(RunModel)
            .options(selectinload(RunModel.steps))
            .where(RunModel.id == run_id)
        )
        return self.session.scalar(statement)

    def list_runs(self, limit: int = 50) -> list[RunModel]:
        statement = select(RunModel).order_by(RunModel.cutoff_time.desc()).limit(limit)
        return list(self.session.scalars(statement))

    def get_snapshot(self, snapshot_id: str) -> SnapshotModel | None:
        return self.session.get(SnapshotModel, snapshot_id)

    def save_snapshot(
        self,
        *,
        snapshot_id: str,
        run_id: str,
        schema_version: str,
        cutoff_time: datetime,
        created_at: datetime,
        quality_status: str,
        payload: dict[str, Any],
    ) -> SnapshotModel:
        snapshot = SnapshotModel(
            id=snapshot_id,
            run_id=run_id,
            schema_version=schema_version,
            cutoff_time=cutoff_time,
            created_at=created_at,
            quality_status=quality_status,
            payload=payload,
        )
        self.session.add(snapshot)
        self.session.commit()
        return snapshot

    def update_run(self, run: RunModel, **values: Any) -> RunModel:
        for field, value in values.items():
            setattr(run, field, value)
        self.session.add(run)
        self.session.commit()
        return run

    def update_step(self, step: StepRunModel, **values: Any) -> StepRunModel:
        for field, value in values.items():
            setattr(step, field, value)
        self.session.add(step)
        self.session.commit()
        return step
