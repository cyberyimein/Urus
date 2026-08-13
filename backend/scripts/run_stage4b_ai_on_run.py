"""Run the full Stage 4B equity decision workflow on an existing snapshot.

The collection workflow may be run with ``skip_ai_decision=true``.  This CLI
reuses that persisted SQLite snapshot and invokes Market -> parallel Themes ->
Equity Synthesis without collecting market data a second time.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings, get_settings
from app.core.database import create_database
from app.core.time import as_utc
from app.models import RunModel, SnapshotModel
from app.repositories.agent import AIDecisionRepository
from app.repositories.events import EventRepository
from app.repositories.runs import RunRepository
from app.urus_agent.coordinator import CoordinatorRequest, DecisionCoordinator
from app.urus_agent.packet import build_stage_decision_packet
from app.urus_agent.prompts import load_agent_profile
from app.services.run_service import RunService


def _find_run(session, run_id: str | None):
    if run_id:
        run = session.get(RunModel, run_id)
        if run is None:
            raise ValueError(f"workflow run not found: {run_id}")
        return run
    statement = (
        select(RunModel)
        .where(
            RunModel.run_type == "pre_market",
            RunModel.snapshot_id.is_not(None),
            RunModel.status.in_(("succeeded", "mixed", "partial")),
        )
        .order_by(RunModel.cutoff_time.desc())
        .limit(1)
    )
    run = session.scalar(statement)
    if run is None:
        raise ValueError("no persisted pre_market run with a snapshot was found")
    return run


def _event_records(session) -> list[dict[str, object]]:
    repository = EventRepository(session)
    return [
        EventRepository.event_payload(event)
        for category in ("macro", "instrument")
        for event in repository.list_events(category)
    ]


def _symbols(payload: dict[str, object], requested: str) -> list[str]:
    if requested.strip():
        return list(dict.fromkeys(value.strip().upper() for value in requested.split(",") if value.strip()))
    values: list[str] = []
    for item in payload.get("instrument_cards") or []:
        if isinstance(item, dict) and item.get("symbol"):
            values.append(str(item["symbol"]).upper())
    market = payload.get("market") or {}
    if isinstance(market, dict):
        primary = market.get("primary") or {}
        if isinstance(primary, dict) and primary.get("symbol"):
            values.append(str(primary["symbol"]).upper())
    return list(dict.fromkeys(values))


def _observation(run: RunModel, snapshot: SnapshotModel) -> dict[str, object]:
    return RunService._persisted_decision_observation(run, snapshot)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", help="Existing pre_market workflow run. Defaults to the latest persisted one.")
    parser.add_argument("--symbols", default="", help="Optional comma-separated symbol universe override.")
    parser.add_argument("--database-url", default=None, help="SQLite/database URL. Defaults to application settings.")
    parser.add_argument("--model", default=None, help="Optional OpenRouter model override for this run.")
    parser.add_argument("--timeout-seconds", type=float, default=None, help="Optional provider timeout override.")
    parser.add_argument("--json-output", default=None, help="Optional path for a compact result summary.")
    args = parser.parse_args(argv)

    base_settings = get_settings()
    settings_kwargs: dict[str, object] = {}
    if args.database_url:
        settings_kwargs["database_url"] = args.database_url
    if args.model:
        settings_kwargs["urus_agent_model"] = args.model
    if args.timeout_seconds is not None:
        settings_kwargs["urus_agent_timeout_seconds"] = args.timeout_seconds
    settings: Settings = base_settings.model_copy(update=settings_kwargs) if settings_kwargs else base_settings
    if not settings.openrouter_api_key:
        parser.error("OPENROUTER_API_KEY is not configured")

    database_url = args.database_url or settings.database_url
    engine, session_factory = create_database(database_url)
    try:
        with session_factory() as session:
            run = _find_run(session, args.run_id)
            if run.run_type != "pre_market":
                parser.error(f"--run-id must refer to pre_market, got {run.run_type!r}")
            if not run.snapshot_id:
                raise ValueError(f"workflow run has no snapshot: {run.id}")
            snapshot = session.get(SnapshotModel, run.snapshot_id)
            if snapshot is None:
                raise ValueError(f"snapshot not found: {run.snapshot_id}")
            payload = snapshot.payload if isinstance(snapshot.payload, dict) else {}
            symbols = _symbols(payload, args.symbols)
            if not symbols:
                raise ValueError("no symbols found in the persisted snapshot; pass --symbols")

            cutoff = as_utc(run.cutoff_time)
            trading_date = cutoff.astimezone(ZoneInfo(settings.market_timezone)).date().isoformat()
            agent_repository = AIDecisionRepository(session)
            previous = agent_repository.latest_session_before(trading_date, "post_close_review")
            prior_reports = {
                "previous_post_close": (
                    dict(previous.decision_report_json)
                    if previous is not None and isinstance(previous.decision_report_json, dict)
                    else None
                )
            }
            current_observation = _observation(run, snapshot)
            packet = build_stage_decision_packet(
                dataset_key=f"daily-decision:{trading_date}:pre_market:{run.id}:manual-ai",
                label=f"{trading_date} pre_market AI replay · {run.id}",
                captured_at=cutoff,
                decision_phase="pre_market",
                trading_date=trading_date,
                observations={"pre_market": current_observation},
                prior_reports=prior_reports,
                events=_event_records(session),
                agent_profile=load_agent_profile("pre_market"),
            )
            print(
                json.dumps(
                    {
                        "event": "started",
                        "workflow_run_id": run.id,
                        "snapshot_id": snapshot.id,
                        "phase": "pre_market",
                        "trading_date": trading_date,
                        "symbols": symbols,
                        "model": settings.urus_agent_model,
                        "timeout_seconds": settings.urus_agent_timeout_seconds,
                        "previous_post_close_report": previous.id if previous is not None else None,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            result = DecisionCoordinator(session, settings).execute(
                CoordinatorRequest(
                    workflow_run_id=run.id,
                    cutoff_time=cutoff,
                    evidence={},
                    symbols=symbols,
                    dataset_key=str(packet["source"]["dataset_key"]),
                    source_snapshot_ids=[snapshot.id],
                    source_run_ids=[run.id],
                    decision_packet=packet,
                    decision_phase="pre_market",
                    trading_date=trading_date,
                    parent_session_id=previous.id if previous is not None else None,
                )
            )
            summary = {
                "event": "finished",
                "workflow_run_id": run.id,
                "snapshot_id": snapshot.id,
                "report_id": result.session_id,
                "status": result.decision_report.get("status"),
                "decision_report_schema_version": result.decision_report.get("schema_version"),
                "equity_decision_run_id": result.decision_report.get("equity_decision_run_id"),
                "equity_option_context_count": len(result.decision_report.get("equity_option_context") or []),
                "report_url": f"/research/reports/{result.session_id}?tab=decision",
            }
            print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
            if args.json_output:
                output_path = Path(args.json_output)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return 0 if summary["status"] in {"succeeded", "partial"} else 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
