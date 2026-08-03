"""Run the two-phase FOMC workflow against the live Anomalo API.

The script uses an in-memory SQLite database. It seeds the 2026-07-29 FOMC
meeting without a result, fetches the next four months of FOMC calendar entries, and
then independently fetches the missing historical result.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import Base
from app.core.time import as_utc, to_iso
from app.events.contracts import EventCandidate, EventSourceEvidence
from app.events.definitions import DEFAULT_EVENT_DEFINITIONS
from app.events.service import ScheduledEventCoordinator
from app.integrations.anomalo import HttpAnomaloAdapter
from app.models import EventAgentRunModel, EventModel, StepStatus
from app.repositories.events import EventRepository
from app.workflows.context import RunContext


HISTORICAL_EVENT_KEY = "macro:fomc_decision:2026-07-29"
HISTORICAL_MEETING_AT = datetime(2026, 7, 29, 18, 0, tzinfo=UTC)


def _seed_historical_fomc(repository: EventRepository, now: datetime) -> EventModel:
    return repository.upsert_candidate(
        EventCandidate(
            definition_key="macro:fomc_decision",
            event_key=HISTORICAL_EVENT_KEY,
            category="macro",
            subject_type="market",
            subject="market",
            event_type="fomc_decision",
            title="FOMC 利率决议（2026-07-29）",
            period="2026-07-29",
            discovery_mode="scheduled",
            status="scheduled",
            scheduled_at=HISTORICAL_MEETING_AT,
            time_precision="exact",
            timezone="America/New_York",
            is_estimated=False,
            announced_at=None,
            result_expected_at=HISTORICAL_MEETING_AT,
            next_check_at=HISTORICAL_MEETING_AT,
            confidence=1.0,
            sources=[
                EventSourceEvidence(
                    publisher="Federal Reserve",
                    url="https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
                    source_type="official_calendar",
                    is_primary=True,
                    evidence_note="验证夹具只补录会议时间，不预填会议结果。",
                )
            ],
        ),
        now=now,
    )


def _tool_error_count(run: EventAgentRunModel) -> int:
    events = run.response_payload.get("events", [])
    if not isinstance(events, list):
        return 0
    return sum(
        1
        for event in events
        if isinstance(event, dict) and event.get("type") == "tool.error"
    )


def main() -> int:
    base_url = os.getenv("ANOMALO_BASE_URL", "https://agent.yimeinforge.com")
    agent_name = os.getenv("ANOMALO_TEST_AGENT", "scheduled-event-investigator")
    timeout_seconds = float(os.getenv("ANOMALO_TEST_TIMEOUT_SECONDS", "600"))
    now = datetime.now(UTC)
    if now <= HISTORICAL_MEETING_AT:
        print("historical_fixture_is_not_past=true")
        return 2

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    adapter = HttpAnomaloAdapter(base_url=base_url, timeout_seconds=timeout_seconds)
    try:
        with Session(engine) as session:
            repository = EventRepository(session)
            repository.ensure_definitions(DEFAULT_EVENT_DEFINITIONS, now=now)
            historical = _seed_historical_fomc(repository, now)
            context = RunContext(
                run_id=f"fomc-combined-validation-{uuid4()}",
                run_type="pre_close",
                cutoff_time=now,
                symbols=["QQQ"],
                instrument_symbols=[],
                anomalo_adapter=adapter,
                event_repository=repository,
                expected_events_enabled=True,
                scheduled_event_agent=agent_name,
                event_horizon_days=120,
            )
            coordinator = ScheduledEventCoordinator(repository)
            fomc_definition = next(
                definition
                for definition in DEFAULT_EVENT_DEFINITIONS
                if definition.key == "macro:fomc_decision"
            )

            print("phase_1=schedule_step")
            schedule_step = coordinator._run_schedule_step(
                context,
                category="macro",
                definitions=[fomc_definition],
                now=now,
            )
            print(f"schedule_status={schedule_step.status.value}")
            print(f"schedule_api_called={schedule_step.payload.get('api_called')}")
            print(f"schedule_discovered_count={schedule_step.payload.get('discovered_count')}")
            print(
                "schedule_missing_definitions="
                f"{schedule_step.payload.get('missing_definitions', [])}"
            )
            if schedule_step.error_message:
                print(f"schedule_error={schedule_step.error_message}")

            print("phase_2=result_step")
            result_step = coordinator._run_result_step(
                context,
                category="macro",
                now=now,
            )
            print(f"result_status={result_step.status.value}")
            print(f"result_due_count={result_step.payload.get('due_count')}")
            print(f"result_api_call_count={result_step.payload.get('api_call_count')}")
            print(f"result_completed_count={result_step.payload.get('completed_count')}")
            if result_step.error_message:
                print(f"result_error={result_step.error_message}")

            events = repository.list_events("macro")
            future_events = [
                event
                for event in events
                if event.definition_key == "macro:fomc_decision"
                and event.scheduled_at is not None
                and as_utc(event.scheduled_at) > now
            ]
            print(f"future_fomc_count={len(future_events)}")
            for index, event in enumerate(future_events, start=1):
                print(
                    f"future_fomc_{index}={event.event_key} "
                    f"scheduled_at={to_iso(event.scheduled_at)} status={event.status}"
                )

            refreshed = session.get(EventModel, historical.id)
            if refreshed is None:
                print("historical_event_missing=true")
                return 3
            session.refresh(refreshed)
            historical_payload = EventRepository.event_payload(refreshed)
            result_payload = historical_payload.get("result")
            print(f"historical_event_key={historical_payload['event_key']}")
            print(f"historical_event_status={historical_payload['status']}")
            print(f"historical_result_present={result_payload is not None}")
            substantive_fact_count = 0
            historical_source_count = 0
            if isinstance(result_payload, dict):
                print(f"historical_result_version={result_payload.get('version')}")
                print(f"historical_result_status={result_payload.get('status')}")
                facts = result_payload.get("facts", [])
                print(f"historical_fact_count={len(facts) if isinstance(facts, list) else 0}")
                if isinstance(facts, list):
                    substantive_fact_count = sum(
                        1
                        for fact in facts
                        if isinstance(fact, dict)
                        and fact.get("actual") not in (None, "")
                    )
                    for index, fact in enumerate(facts, start=1):
                        if isinstance(fact, dict):
                            print(
                                f"historical_fact_{index}={fact.get('name')} "
                                f"actual={fact.get('actual')}"
                            )
                historical_source_count = int(result_payload.get("source_count") or 0)
                print(f"historical_source_count={historical_source_count}")
            print(f"historical_substantive_fact_count={substantive_fact_count}")

            runs = list(
                session.scalars(
                    select(EventAgentRunModel).order_by(EventAgentRunModel.started_at)
                )
            )
            print(f"agent_run_count={len(runs)}")
            for index, run in enumerate(runs, start=1):
                print(
                    f"agent_run_{index}=operation:{run.operation} status:{run.status} "
                    f"tool_errors:{_tool_error_count(run)}"
                )

            schedule_ok = (
                schedule_step.status == StepStatus.SUCCEEDED and len(future_events) > 0
            )
            result_ok = (
                result_step.status == StepStatus.SUCCEEDED
                and refreshed.status in {"confirmed", "revised"}
                and substantive_fact_count > 0
                and historical_source_count > 0
            )
            print(f"combined_validation_passed={schedule_ok and result_ok}")
            return 0 if schedule_ok and result_ok else 4
    finally:
        adapter.close()
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
