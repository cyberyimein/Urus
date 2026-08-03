from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import RunModel, SnapshotModel, StepRunModel, StrategyResearchDatasetModel
from app.repositories.strategy import StrategyResearchRepository


def test_strategy_dataset_captures_snapshot_steps_and_pending_event_state() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    timestamp = datetime(2026, 8, 3, 12, 30, tzinfo=UTC)
    with Session(engine) as session:
        session.add(
            RunModel(
                id="run-step4-fixture",
                run_type="pre_market",
                status="mixed",
                started_at=timestamp,
                completed_at=timestamp,
                cutoff_time=timestamp,
                snapshot_id="snapshot-step4-fixture",
            )
        )
        session.add(
            SnapshotModel(
                id="snapshot-step4-fixture",
                run_id="run-step4-fixture",
                schema_version="v1",
                cutoff_time=timestamp,
                created_at=timestamp,
                quality_status="mixed",
                payload={"market": {"symbol": "QQQ"}, "options": {"is_mock": False}},
            )
        )
        for position, code, status in (
            (2, "1b", "skipped"),
            (5, "3b", "skipped"),
        ):
            session.add(
                StepRunModel(
                    id=f"step-{code}",
                    run_id="run-step4-fixture",
                    position=position,
                    step_code=code,
                    status=status,
                    summary="event collection disabled",
                    payload={"status": status},
                )
            )
        session.commit()

        model = StrategyResearchRepository(session).capture_run(
            run_id="run-step4-fixture",
            dataset_key="step4-fixture",
            label="Step4 fixture",
        )

        assert model.status == "pending_events"
        assert model.event_collection_status["complete"] is False
        assert model.payload["snapshot"]["options"]["is_mock"] is False
        assert model.payload["steps"]["1b"]["status"] == "skipped"
        assert session.scalar(select(StrategyResearchDatasetModel)) is not None
