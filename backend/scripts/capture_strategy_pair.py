"""Capture a pre-market/pre-close pair for Stage 4B strategy research."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings
from app.core.database import Base, create_database
from app.core.time import as_utc
from app.models import RunModel, SnapshotModel, StepRunModel
from app.repositories.events import EventRepository


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _step_payloads(session: Any, run_id: str) -> dict[str, dict[str, Any]]:
    steps = list(
        session.scalars(
            select(StepRunModel)
            .where(StepRunModel.run_id == run_id)
            .order_by(StepRunModel.position)
        )
    )
    return {
        step.step_code: {
            "position": step.position,
            "status": step.status,
            "started_at": step.started_at.isoformat() if step.started_at else None,
            "completed_at": step.completed_at.isoformat() if step.completed_at else None,
            "summary": step.summary,
            "error_message": step.error_message,
            "payload": step.payload or {},
        }
        for step in steps
    }


def _observation(session: Any, run_id: str, expected_type: str) -> dict[str, Any]:
    run = session.get(RunModel, run_id)
    if run is None:
        raise ValueError(f"Run not found: {run_id}")
    if run.run_type != expected_type:
        raise ValueError(
            f"Run {run_id} has type {run.run_type!r}; expected {expected_type!r}."
        )
    if not run.snapshot_id:
        raise ValueError(f"Run has no snapshot: {run_id}")
    snapshot = session.get(SnapshotModel, run.snapshot_id)
    if snapshot is None:
        raise ValueError(f"Snapshot not found: {run.snapshot_id}")
    return {
        "run": {
            "id": run.id,
            "run_type": run.run_type,
            "status": run.status,
            "cutoff_time": as_utc(run.cutoff_time).isoformat(),
            "started_at": as_utc(run.started_at).isoformat() if run.started_at else None,
            "completed_at": as_utc(run.completed_at).isoformat()
            if run.completed_at
            else None,
        },
        "snapshot": {
            "id": snapshot.id,
            "schema_version": snapshot.schema_version,
            "cutoff_time": as_utc(snapshot.cutoff_time).isoformat(),
            "created_at": as_utc(snapshot.created_at).isoformat(),
            "quality_status": snapshot.quality_status,
            "payload": snapshot.payload,
        },
        "steps": _step_payloads(session, run_id),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pre-market-run-id", required=True)
    parser.add_argument("--pre-close-run-id", required=True)
    parser.add_argument("--dataset-key", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", default=None)
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)

    settings = Settings(**({"database_url": args.database_url} if args.database_url else {}))
    engine, session_factory = create_database(settings.database_url)
    Base.metadata.create_all(bind=engine)
    try:
        with session_factory() as session:
            observations = {
                "pre_market": _observation(session, args.pre_market_run_id, "pre_market"),
                "pre_close": _observation(session, args.pre_close_run_id, "pre_close"),
            }
            events = [
                EventRepository.event_payload(event)
                for category in ("macro", "instrument")
                for event in EventRepository(session).list_events(category)
            ]
            captured_at = datetime.now(UTC).isoformat()
            pair = {
                "backup_schema": "urus.stage4b_strategy_pair.v1",
                "dataset_key": args.dataset_key,
                "label": args.label,
                "captured_at": captured_at,
                "pair": {
                    "observation_order": ["pre_market", "pre_close"],
                    "observations": observations,
                },
                "events": {
                    "captured_at": captured_at,
                    "records": events,
                },
            }
            pair["content_sha256"] = hashlib.sha256(_canonical(pair)).hexdigest()
            output = Path(args.output) if args.output else Path("data/strategy_research") / (
                f"{args.dataset_key}.json"
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(pair, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
            print(
                json.dumps(
                    {
                        "output": str(output),
                        "dataset_key": args.dataset_key,
                        "schema": pair["backup_schema"],
                        "pre_market_run_id": args.pre_market_run_id,
                        "pre_close_run_id": args.pre_close_run_id,
                        "event_count": len(events),
                        "bytes": output.stat().st_size,
                        "content_sha256": pair["content_sha256"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
