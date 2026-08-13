"""Export a three-phase Stage 4B daily fixture from persisted workflow snapshots."""

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
from app.core.database import create_database
from app.core.time import as_utc
from app.models import RunModel, SnapshotModel, StepRunModel
from app.repositories.events import EventRepository


SCHEMA = "urus.stage4b_daily_cycle_fixture.v1"


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()


def _step_payloads(session: Any, run_id: str) -> dict[str, dict[str, Any]]:
    steps = list(
        session.scalars(
            select(StepRunModel).where(StepRunModel.run_id == run_id).order_by(StepRunModel.position)
        )
    )
    return {
        step.step_code: {
            "position": step.position,
            "status": step.status,
            "started_at": as_utc(step.started_at).isoformat() if step.started_at else None,
            "completed_at": as_utc(step.completed_at).isoformat() if step.completed_at else None,
            "summary": step.summary,
            "error_message": step.error_message,
            "payload": step.payload or {},
        }
        for step in steps
    }


def _observation(session: Any, run_id: str, phase: str) -> dict[str, Any]:
    run = session.get(RunModel, run_id)
    if run is None:
        raise ValueError(f"Run not found: {run_id}")
    if not run.snapshot_id:
        raise ValueError(f"Run has no snapshot: {run_id}")
    snapshot = session.get(SnapshotModel, run.snapshot_id)
    if snapshot is None:
        raise ValueError(f"Snapshot not found: {run.snapshot_id}")
    payload = snapshot.payload or {}
    market = payload.get("market") if isinstance(payload, dict) else {}
    actual_session = market.get("session") if isinstance(market, dict) else None
    actual_session_label = market.get("session_label") if isinstance(market, dict) else None
    if phase == "post_close_review" and actual_session not in {"afterhours", "overnight"}:
        raise ValueError(
            f"The selected post-close fixture is not an after-hours snapshot: {actual_session!r}"
        )
    return {
        "phase": phase,
        "run": {
            "id": run.id,
            "run_type": run.run_type,
            "fixture_phase": phase,
            "status": run.status,
            "cutoff_time": as_utc(run.cutoff_time).isoformat(),
            "started_at": as_utc(run.started_at).isoformat() if run.started_at else None,
            "completed_at": as_utc(run.completed_at).isoformat() if run.completed_at else None,
        },
        "snapshot": {
            "id": snapshot.id,
            "schema_version": snapshot.schema_version,
            "cutoff_time": as_utc(snapshot.cutoff_time).isoformat(),
            "created_at": as_utc(snapshot.created_at).isoformat(),
            "quality_status": snapshot.quality_status,
            "session": actual_session,
            "session_label": actual_session_label,
            "payload": snapshot.payload,
        },
        "steps": _step_payloads(session, run_id),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pre-market-run-id", required=True)
    parser.add_argument("--pre-close-run-id", required=True)
    parser.add_argument("--post-close-run-id", required=True)
    parser.add_argument("--trading-date", required=True, help="US market date, e.g. 2026-08-03")
    parser.add_argument("--output", default=None)
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)

    settings = Settings(**({"database_url": args.database_url} if args.database_url else {}))
    database_url = args.database_url or settings.database_url
    engine, session_factory = create_database(database_url)
    try:
        with session_factory() as session:
            observations = {
                "pre_market": _observation(session, args.pre_market_run_id, "pre_market"),
                "pre_close": _observation(session, args.pre_close_run_id, "pre_close"),
                "post_close_review": _observation(session, args.post_close_run_id, "post_close_review"),
            }
            events = [
                EventRepository.event_payload(event)
                for category in ("macro", "instrument")
                for event in EventRepository(session).list_events(category)
            ]
            generated_at = datetime.now(UTC).isoformat()
            fixture: dict[str, Any] = {
                "schema_version": SCHEMA,
                "test_fixture": True,
                "fixture_reason": "真实 Moomoo/OpenD 盘后快照用于 Stage 4B 三阶段工作流验证",
                "trading_date": args.trading_date,
                "generated_at": generated_at,
                "observation_order": ["pre_market", "pre_close", "post_close_review"],
                "source_run_ids": [
                    args.pre_market_run_id,
                    args.pre_close_run_id,
                    args.post_close_run_id,
                ],
                "observations": observations,
                "events": {"captured_at": generated_at, "records": events},
                "execution_ready": False,
                "execution_blockers": ["Test fixture only; no orders are permitted."],
            }
            fixture["content_sha256"] = hashlib.sha256(_canonical(fixture)).hexdigest()
            output = Path(args.output) if args.output else Path("data/strategy_research") / (
                f"stage4b-daily-{args.trading_date}-test.json"
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(fixture, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
            print(json.dumps({
                "output": str(output),
                "schema_version": SCHEMA,
                "test_fixture": True,
                "trading_date": args.trading_date,
                "phases": list(observations),
                "post_close_session": observations["post_close_review"]["snapshot"]["session"],
                "content_sha256": fixture["content_sha256"],
            }, ensure_ascii=False, indent=2))
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
