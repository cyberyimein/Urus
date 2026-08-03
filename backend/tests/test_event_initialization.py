from __future__ import annotations

from datetime import timedelta

import yaml
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.core.time import utc_now
from app.events.initializer import EventScheduleInitializer
from app.integrations.anomalo import AnomaloResponse
from app.integrations.anomalo import AnomaloRequest, HttpAnomaloAdapter
import httpx
from app.models import (
    EventAgentRunModel,
    EventModel,
    EventScheduleInitializationModel,
)
from app.repositories.events import EventRepository
from app.events.definitions import DEFAULT_EVENT_DEFINITIONS


class FullCalendarFixtureAdapter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def investigate(self, request):
        payload = yaml.safe_load(request.message)
        category = payload["request"]["category"]
        self.calls.append(category)
        now = utc_now()
        definitions = {
            item.key: item for item in DEFAULT_EVENT_DEFINITIONS if item.category == category
        }
        events = []
        for index, target in enumerate(payload["request"]["targets"], start=1):
            definition = definitions[target["definition_key"]]
            scheduled_at = now + timedelta(days=30 + index)
            events.append(
                {
                    "definition_key": definition.key,
                    "event_key": f"{definition.key}:{target['subject']}:fixture",
                    "category": definition.category,
                    "subject_type": target["subject_type"],
                    "subject": target["subject"],
                    "event_type": definition.event_type,
                    "title": definition.title,
                    "discovery_mode": "scheduled",
                    "status": "scheduled",
                    "scheduled_at": scheduled_at.isoformat(),
                    "time_precision": "date",
                    "timezone": "America/New_York",
                    "is_estimated": False,
                    "result_expected_at": scheduled_at.isoformat(),
                    "next_check_at": scheduled_at.isoformat(),
                    "confidence": 0.9,
                    "sources": [
                        {
                            "publisher": "Official fixture",
                            "url": f"https://example.com/{definition.key}/{target['subject']}",
                            "source_type": "official_calendar",
                            "is_primary": True,
                        }
                    ],
                }
            )
        return AnomaloResponse(
            final_text=None,
            is_mock=True,
            output={
                "operation": "discover_schedule",
                "generated_at": now.isoformat(),
                "events": events,
                "missing_definitions": [],
                "notes": [],
            },
            output_format="json_schema",
        )


class IncompleteCalendarFixtureAdapter(FullCalendarFixtureAdapter):
    def investigate(self, request):
        response = super().investigate(request)
        output = dict(response.output)
        output["events"] = output["events"][:1]
        return AnomaloResponse(
            final_text=None,
            is_mock=True,
            output=output,
            output_format="json_schema",
        )


class OneSuccessOneFailureFixtureAdapter(FullCalendarFixtureAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    def investigate(self, request):
        self.attempts += 1
        if self.attempts == 2:
            return AnomaloResponse(
                final_text=None,
                is_mock=False,
                error_code="fixture_timeout",
                error_message="fixture request failed",
            )
        return super().investigate(request)


def test_full_schedule_initialization_persists_and_is_idempotent() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        adapter = FullCalendarFixtureAdapter()
        progress_events: list[dict[str, object]] = []
        initializer = EventScheduleInitializer(
            EventRepository(session),
            adapter,
            agent="scheduled-event-investigator",
            horizon_days=120,
            batch_size=20,
        )

        first = initializer.initialize(
            categories=("macro", "instrument"),
            instrument_symbols=("INTC", "NVDA"),
            progress=progress_events.append,
        )

        assert first.status == "succeeded"
        assert first.api_call_count == 2
        assert first.discovered_count == 9
        assert adapter.calls == ["macro", "instrument"]
        assert [event["event"] for event in progress_events] == [
            "started",
            "request_started",
            "request_finished",
            "request_started",
            "request_finished",
            "finished",
        ]
        assert session.scalar(select(func.count(EventModel.id))) == 9

        batch = session.get(EventScheduleInitializationModel, first.initialization_id)
        assert batch is not None
        assert batch.status == "succeeded"
        assert batch.requested_targets

        second = initializer.initialize(
            categories=("macro", "instrument"),
            instrument_symbols=("INTC", "NVDA"),
        )

        assert second.status == "succeeded"
        assert second.api_call_count == 0
        assert [item["status"] for item in second.categories] == ["skipped", "skipped"]
        assert len(list(session.scalars(select(EventAgentRunModel)))) == 2
        assert session.scalar(select(func.count(EventModel.id))) == 9

        refreshed = initializer.initialize(
            categories=("macro", "instrument"),
            instrument_symbols=("INTC", "NVDA"),
            force=True,
        )

        assert refreshed.status == "succeeded"
        assert refreshed.api_call_count == 2
        assert len(list(session.scalars(select(EventAgentRunModel)))) == 4
        assert session.scalar(select(func.count(EventModel.id))) == 9


def test_multi_target_initialization_reports_uncovered_targets_as_partial() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        initializer = EventScheduleInitializer(
            EventRepository(session),
            IncompleteCalendarFixtureAdapter(),
            agent="scheduled-event-investigator",
            horizon_days=120,
            batch_size=20,
        )

        result = initializer.initialize(
            categories=("instrument",),
            instrument_symbols=("INTC", "NVDA"),
        )

        assert result.status == "partial"
        assert result.discovered_count == 1
        assert result.categories[0]["status"] == "partial"
        assert "NVDA" in result.categories[0]["errors"][0]


def test_single_category_mixed_batches_keep_partial_top_level_status() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        initializer = EventScheduleInitializer(
            EventRepository(session),
            OneSuccessOneFailureFixtureAdapter(),
            agent="scheduled-event-investigator",
            horizon_days=120,
            batch_size=1,
        )

        result = initializer.initialize(
            categories=("instrument",),
            instrument_symbols=("INTC", "NVDA"),
        )

        assert result.status == "partial"
        assert result.discovered_count == 1
        assert result.categories[0]["status"] == "partial"


def test_http_adapter_surfaces_run_error_from_event_stream() -> None:
    adapter = HttpAnomaloAdapter("https://anomalo.test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output": None,
                "output_format": "text",
                "events": [
                    {
                        "type": "run.error",
                        "data": {
                            "error_code": "run_timeout",
                            "error": "Agent run exceeded the configured timeout of 300 seconds.",
                        },
                    }
                ],
            },
        )

    adapter.client.close()
    adapter.client = httpx.Client(transport=httpx.MockTransport(handler))
    response = adapter.investigate(
        AnomaloRequest(
            session_id="initialization-timeout",
            agent="scheduled-event-investigator",
            message="discover",
            response_format={"type": "json_schema"},
        )
    )
    adapter.close()

    assert response.error_code == "run_timeout"
    assert response.error_message == "Agent run exceeded the configured timeout of 300 seconds."
