from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
import yaml
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.core.time import utc_now
from app.events.service import ScheduledEventCoordinator
from app.events.contracts import (
    EventCandidate,
    EventDiscoveryOutput,
    EventResultOutput,
    EventSourceEvidence,
)
from app.events.definitions import DEFAULT_EVENT_DEFINITIONS
from app.integrations.anomalo import AnomaloRequest, AnomaloResponse, HttpAnomaloAdapter
from app.models import (
    EventAgentRunModel,
    EventMarketReactionModel,
    EventModel,
    EventResultModel,
    StepStatus,
)
from app.repositories.events import EventRepository
from app.workflows.base import StepResult
from app.workflows.context import RunContext


def request_event_key(message: str) -> str:
    payload = yaml.safe_load(message)
    return str(payload["request"]["event_key"])


class FixtureEventAdapter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def investigate(self, request):
        self.calls.append(request.response_format["json_schema"]["name"])
        now = utc_now()
        if request.response_format["json_schema"]["name"] == "scheduled_event_discovery":
            return AnomaloResponse(
                final_text=None,
                is_mock=True,
                output={
                    "operation": "discover_schedule",
                    "generated_at": now.isoformat(),
                    "events": [
                        {
                            "definition_key": "instrument:earnings",
                            "event_key": "instrument:earnings:INTC:2026Q3",
                            "category": "instrument",
                            "subject_type": "symbol",
                            "subject": "INTC",
                            "event_type": "earnings",
                            "title": "INTC Q3 earnings",
                            "period": "2026Q3",
                            "discovery_mode": "scheduled",
                            "status": "scheduled",
                            "scheduled_at": (now - timedelta(hours=1)).isoformat(),
                            "time_precision": "exact",
                            "timezone": "America/New_York",
                            "is_estimated": False,
                            "result_expected_at": (now - timedelta(minutes=1)).isoformat(),
                            "next_check_at": (now - timedelta(minutes=1)).isoformat(),
                            "confidence": 0.9,
                            "sources": [
                                {
                                    "publisher": "IR",
                                    "url": "https://example.com/intc",
                                    "source_type": "primary",
                                    "is_primary": True,
                                }
                            ],
                        }
                    ],
                    "missing_definitions": [],
                    "notes": [],
                },
            )
        return AnomaloResponse(
            final_text=None,
            is_mock=True,
            output={
                "operation": "collect_result",
                "event_key": "instrument:earnings:INTC:2026Q3",
                "result_status": "confirmed",
                "occurred_at": now.isoformat(),
                "released_at": now.isoformat(),
                "facts": [
                    {"name": "diluted_eps", "actual": 1.0, "consensus": 0.8},
                    {"name": "revenue", "actual": 20.0, "consensus": 19.0},
                ],
                "summary": "beat",
                "guidance": "positive",
                "confidence": 0.9,
                "needs_follow_up": False,
                "sources": [
                    {
                        "publisher": "Intel Investor Relations",
                        "url": "https://example.com/intc-result",
                        "source_type": "primary",
                        "is_primary": True,
                    }
                ],
            },
        )


class HistoricalFomcRetryAdapter:
    """Deterministic adapter for the missed-last-week FOMC lifecycle check."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.result_attempts = 0

    def investigate(self, request):
        schema_name = request.response_format["json_schema"]["name"]
        self.calls.append(schema_name)
        now = utc_now()
        if schema_name == "scheduled_event_discovery":
            return AnomaloResponse(
                final_text=None,
                is_mock=True,
                output={
                    "operation": "discover_schedule",
                    "generated_at": now.isoformat(),
                    "events": [],
                    "missing_definitions": ["macro:fomc_decision"],
                    "notes": ["验证夹具：未来窗口没有新的 FOMC 会议；历史事件由补录数据提供。"],
                },
            )

        self.result_attempts += 1
        event_key = request_event_key(request.message)
        if self.result_attempts == 1:
            return AnomaloResponse(
                final_text=None,
                is_mock=True,
                output={
                    "operation": "collect_result",
                    "event_key": event_key,
                    "result_status": "not_released",
                    "facts": [],
                    "summary": "验证夹具：官方结果暂未发布或尚未检索到。",
                    "guidance": "next_check_at 到期后重试。",
                    "confidence": 0.4,
                    "needs_follow_up": True,
                    "next_check_at": (now + timedelta(days=1)).isoformat(),
                    "sources": [],
                },
            )
        return AnomaloResponse(
            final_text=None,
            is_mock=True,
            output={
                "operation": "collect_result",
                "event_key": event_key,
                "result_status": "confirmed",
                "occurred_at": now.isoformat(),
                "released_at": now.isoformat(),
                "facts": [
                    {
                        "name": "federal_funds_target_range",
                        "actual": "验证夹具已发布",
                        "consensus": None,
                        "previous": None,
                        "unit": None,
                        "note": "仅用于生命周期验证，不代表真实 FOMC 数据。",
                    }
                ],
                "summary": "验证夹具：结果已确认。",
                "guidance": "进入市场反应复盘。",
                "confidence": 0.9,
                "needs_follow_up": False,
                "sources": [
                    {
                        "publisher": "Federal Reserve (fixture)",
                        "url": "https://example.com/fomc-result",
                        "source_type": "primary",
                        "is_primary": True,
                    }
                ],
            },
        )


class HistoricalFomcWithFutureScheduleAdapter(HistoricalFomcRetryAdapter):
    """Return a complete future calendar on the first schedule lookup."""

    def investigate(self, request):
        schema_name = request.response_format["json_schema"]["name"]
        if schema_name != "scheduled_event_discovery":
            return super().investigate(request)
        self.calls.append(schema_name)
        now = utc_now()
        events = []
        for index, spec in enumerate(
            (item for item in DEFAULT_EVENT_DEFINITIONS if item.category == "macro"),
            start=1,
        ):
            scheduled_at = now + timedelta(days=index * 7)
            events.append(
                {
                    "definition_key": spec.key,
                    "event_key": f"{spec.key}:future-{index}",
                    "category": spec.category,
                    "subject_type": spec.subject_type,
                    "subject": "market",
                    "event_type": spec.event_type,
                    "title": spec.title,
                    "discovery_mode": "scheduled",
                    "status": "scheduled",
                    "scheduled_at": scheduled_at.isoformat(),
                    "time_precision": "date",
                    "timezone": "America/New_York",
                    "is_estimated": True,
                    "result_expected_at": scheduled_at.isoformat(),
                    "next_check_at": scheduled_at.isoformat(),
                    "confidence": 0.8,
                    "sources": [
                        {
                            "publisher": "Federal Reserve (fixture)",
                            "url": f"https://example.com/future-calendar-{index}",
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
                "notes": ["验证夹具：已返回未来事件日历。"],
            },
        )


class ScheduleFailureResultSuccessAdapter:
    """Prove that a failed calendar call does not block result collection."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def investigate(self, request):
        schema_name = request.response_format["json_schema"]["name"]
        self.calls.append(schema_name)
        if schema_name == "scheduled_event_discovery":
            return AnomaloResponse(
                final_text=None,
                is_mock=False,
                error_code="schedule_fixture_failure",
                error_message="验证夹具：日历 API 失败。",
            )
        now = utc_now()
        event_key = request_event_key(request.message)
        return AnomaloResponse(
            final_text=None,
            is_mock=False,
            output={
                "operation": "collect_result",
                "event_key": event_key,
                "result_status": "confirmed",
                "occurred_at": now.isoformat(),
                "released_at": now.isoformat(),
                "facts": [
                    {"name": "federal_funds_target_range", "actual": "confirmed"}
                ],
                "summary": "验证夹具：历史结果已补全。",
                "confidence": 0.9,
                "needs_follow_up": False,
                "sources": [
                    {
                        "publisher": "Federal Reserve (fixture)",
                        "url": "https://example.com/independent-result",
                        "source_type": "primary",
                        "is_primary": True,
                    }
                ],
            },
        )


def make_context(session: Session, adapter: FixtureEventAdapter) -> RunContext:
    context = RunContext(
        run_id="run-events",
        run_type="post_close_review",
        cutoff_time=utc_now(),
        symbols=["QQQ", "INTC"],
        instrument_symbols=["INTC"],
        anomalo_adapter=adapter,
        event_repository=EventRepository(session),
        expected_events_enabled=True,
        scheduled_event_agent="scheduled-event-investigator",
    )
    context.results["1a"] = StepResult(
        status=StepStatus.SUCCEEDED,
        summary="market",
        payload={"regular_price": 100.0, "previous_close": 99.0, "change_percent": 1.01, "source": "mock"},
    )
    context.results["3a"] = StepResult(
        status=StepStatus.SUCCEEDED,
        summary="instrument",
        payload={
            "instruments": [
                {
                    "symbol": "INTC",
                    "regular_price": 20.0,
                    "previous_close": 19.0,
                    "change_percent": 5.26,
                    "source": "mock",
                }
            ]
        },
    )
    return context


def test_scheduled_event_is_idempotent_and_records_reaction() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        adapter = FixtureEventAdapter()
        context = make_context(session, adapter)
        first = ScheduledEventCoordinator(context.event_repository).execute(context, "instrument")
        assert first.status.value == "succeeded"
        assert first.payload["counts"] == {"confirmed": 1}
        assert first.payload["events"][0]["result"]["summary"] == "beat"
        assert first.payload["events"][0]["market_reactions"][0]["symbol"] == "INTC"

        context.run_id = "run-events-next-review"
        second = ScheduledEventCoordinator(context.event_repository).execute(context, "instrument")
        assert second.payload["counts"] == {"confirmed": 1}
        assert session.scalar(select(EventResultModel)) is not None
        assert len(list(session.scalars(select(EventResultModel)))) == 1
        assert len(list(session.scalars(select(EventMarketReactionModel)))) == 1
        assert len(list(session.scalars(select(EventAgentRunModel)))) == 3


def test_schedule_lookup_is_skipped_when_all_definitions_have_future_events() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        repository = EventRepository(session)
        now = utc_now()
        macro_definitions = tuple(
            spec for spec in DEFAULT_EVENT_DEFINITIONS if spec.category == "macro"
        )
        repository.ensure_definitions(DEFAULT_EVENT_DEFINITIONS, now=now)
        for index, spec in enumerate(macro_definitions, start=1):
            repository.upsert_candidate(
                EventCandidate(
                    definition_key=spec.key,
                    event_key=f"{spec.key}:future-{index}",
                    category=spec.category,
                    subject_type=spec.subject_type,
                    subject="market",
                    event_type=spec.event_type,
                    title=spec.title,
                    discovery_mode="scheduled",
                    status="scheduled",
                    scheduled_at=now + timedelta(days=index),
                    time_precision="date",
                    timezone="America/New_York",
                    is_estimated=True,
                    result_expected_at=now + timedelta(days=index),
                    next_check_at=now + timedelta(days=index),
                    confidence=0.8,
                    sources=[
                        EventSourceEvidence(
                            publisher="Federal Reserve (fixture)",
                            url=f"https://example.com/future-{index}",
                            source_type="official_calendar",
                            is_primary=True,
                        )
                    ],
                ),
                now=now,
            )
        adapter = HistoricalFomcRetryAdapter()
        context = RunContext(
            run_id="run-fomc-schedule-skip",
            run_type="scheduled_event_discovery",
            cutoff_time=now,
            symbols=["QQQ"],
            instrument_symbols=[],
            anomalo_adapter=adapter,
            event_repository=repository,
            expected_events_enabled=True,
            scheduled_event_agent="scheduled-event-investigator",
        )

        result = ScheduledEventCoordinator(repository).execute(context, "macro")

        assert result.status == StepStatus.SUCCEEDED
        assert result.payload["schedule_api_called"] is False
        assert result.payload["missing_future_definitions"] == []
        assert result.payload["discovered_count"] == 0
        assert result.payload["due_result_count"] == 0
        assert adapter.calls == []


def test_unverified_schedule_uses_future_next_check_as_coverage() -> None:
    """A date-less estimate is not re-searched until its retry deadline."""

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        repository = EventRepository(session)
        now = utc_now()
        repository.ensure_definitions(DEFAULT_EVENT_DEFINITIONS, now=now)
        repository.upsert_candidate(
            EventCandidate(
                definition_key="instrument:earnings",
                event_key="instrument:earnings:AMZN:expected",
                category="instrument",
                subject_type="symbol",
                subject="AMZN",
                event_type="earnings",
                title="AMZN quarterly earnings",
                period="2026Q3",
                discovery_mode="scheduled",
                status="unverified",
                scheduled_at=None,
                time_precision="window",
                timezone="America/New_York",
                is_estimated=True,
                result_expected_at=None,
                next_check_at=now + timedelta(days=7),
                confidence=0.4,
                sources=[
                    EventSourceEvidence(
                        publisher="Calendar estimate",
                        url="https://example.com/amzn-earnings",
                        source_type="secondary",
                        is_primary=False,
                    )
                ],
            ),
            now=now,
        )
        context = RunContext(
            run_id="run-date-less-coverage",
            run_type="scheduled_event_discovery",
            cutoff_time=now,
            symbols=["QQQ", "INTC"],
            instrument_symbols=["AMZN"],
            event_instrument_symbols=["AMZN"],
            event_repository=repository,
            expected_events_enabled=True,
        )
        definitions = [
            item for item in DEFAULT_EVENT_DEFINITIONS if item.category == "instrument"
        ]

        assert ScheduledEventCoordinator(repository)._missing_schedule_targets(
            context,
            category="instrument",
            definitions=definitions,
            now=now,
        ) == []

        event = session.scalar(select(EventModel).where(EventModel.subject == "AMZN"))
        assert event is not None
        event.next_check_at = now - timedelta(seconds=1)
        session.commit()

        assert ScheduledEventCoordinator(repository)._missing_schedule_targets(
            context,
            category="instrument",
            definitions=definitions,
            now=now,
        ) == [
            {
                "definition_key": "instrument:earnings",
                "subject_type": "symbol",
                "subject": "AMZN",
            }
        ]


def test_instrument_schedule_coverage_is_checked_for_each_configured_symbol() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        repository = EventRepository(session)
        now = utc_now()
        repository.ensure_definitions(DEFAULT_EVENT_DEFINITIONS, now=now)
        repository.upsert_candidate(
            EventCandidate(
                definition_key="instrument:earnings",
                event_key="instrument:earnings:INTC:future",
                category="instrument",
                subject_type="symbol",
                subject="INTC",
                event_type="earnings",
                title="INTC future earnings",
                discovery_mode="scheduled",
                status="scheduled",
                scheduled_at=now + timedelta(days=30),
                time_precision="date",
                timezone="America/New_York",
                is_estimated=False,
                result_expected_at=now + timedelta(days=30),
                next_check_at=now + timedelta(days=30),
                confidence=0.9,
                sources=[
                    EventSourceEvidence(
                        publisher="Intel Investor Relations",
                        url="https://example.com/intc-future",
                        source_type="primary",
                        is_primary=True,
                    )
                ],
            ),
            now=now,
        )
        context = RunContext(
            run_id="run-per-symbol-coverage",
            run_type="scheduled_event_discovery",
            cutoff_time=now,
            symbols=["INTC", "NVDA"],
            instrument_symbols=["INTC", "NVDA"],
            event_instrument_symbols=["INTC", "NVDA"],
            event_repository=repository,
            expected_events_enabled=True,
        )
        definitions = [
            item for item in DEFAULT_EVENT_DEFINITIONS if item.category == "instrument"
        ]

        targets = ScheduledEventCoordinator(repository)._missing_schedule_targets(
            context,
            category="instrument",
            definitions=definitions,
            now=now,
        )

        assert targets == [
            {
                "definition_key": "instrument:earnings",
                "subject_type": "symbol",
                "subject": "NVDA",
            }
        ]


def test_schedule_failure_does_not_block_due_result_collection() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        repository = EventRepository(session)
        now = utc_now()
        repository.ensure_definitions(DEFAULT_EVENT_DEFINITIONS, now=now)
        historical = repository.upsert_candidate(
            EventCandidate(
                definition_key="macro:fomc_decision",
                event_key="macro:fomc_decision:independent-phases",
                category="macro",
                subject_type="market",
                subject="market",
                event_type="fomc_decision",
                title="FOMC 利率决议（步骤独立性验证）",
                discovery_mode="scheduled",
                status="scheduled",
                scheduled_at=now - timedelta(days=5),
                time_precision="exact",
                timezone="America/New_York",
                is_estimated=False,
                result_expected_at=None,
                next_check_at=now - timedelta(hours=1),
                confidence=1.0,
                sources=[
                    EventSourceEvidence(
                        publisher="Federal Reserve (fixture)",
                        url="https://example.com/independent-phases",
                        source_type="official_calendar",
                        is_primary=True,
                    )
                ],
            ),
            now=now,
        )
        adapter = ScheduleFailureResultSuccessAdapter()
        context = RunContext(
            run_id="run-independent-event-phases",
            run_type="scheduled_event_discovery",
            cutoff_time=now,
            symbols=["QQQ"],
            instrument_symbols=[],
            anomalo_adapter=adapter,
            event_repository=repository,
            expected_events_enabled=True,
            scheduled_event_agent="scheduled-event-investigator",
        )

        result = ScheduledEventCoordinator(repository).execute(context, "macro")

        assert result.status == StepStatus.FAILED
        assert result.data_state == "mixed"
        assert result.payload["schedule_step"]["status"] == "failed"
        assert result.payload["schedule_step"]["api_called"] is True
        assert result.payload["result_step"]["status"] == "succeeded"
        assert result.payload["result_step"]["api_call_count"] == 1
        assert adapter.calls == [
            "scheduled_event_discovery",
            "scheduled_event_result",
        ]
        persisted = session.get(EventModel, historical.id)
        assert persisted is not None
        assert persisted.status == "confirmed"
        assert persisted.latest_result_version == 1


def test_historical_fomc_schedule_is_discovered_once_then_result_retries() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        repository = EventRepository(session)
        now = utc_now()
        repository.ensure_definitions(DEFAULT_EVENT_DEFINITIONS, now=now)
        meeting_at = now - timedelta(days=5)
        historical = repository.upsert_candidate(
            EventCandidate(
                definition_key="macro:fomc_decision",
                event_key=f"macro:fomc_decision:{meeting_at.date().isoformat()}",
                category="macro",
                subject_type="market",
                subject="market",
                event_type="fomc_decision",
                title="FOMC 利率决议（完整流程验证夹具）",
                period=str(meeting_at.date()),
                discovery_mode="scheduled",
                status="scheduled",
                scheduled_at=meeting_at,
                time_precision="exact",
                timezone="America/New_York",
                is_estimated=False,
                result_expected_at=None,
                next_check_at=now - timedelta(hours=1),
                confidence=1.0,
                sources=[
                    EventSourceEvidence(
                        publisher="Federal Reserve (fixture)",
                        url="https://example.com/fomc-history",
                        source_type="official_calendar",
                        is_primary=True,
                    )
                ],
            ),
            now=now,
        )
        adapter = HistoricalFomcWithFutureScheduleAdapter()
        context = RunContext(
            run_id="run-fomc-full-1",
            run_type="scheduled_event_discovery",
            cutoff_time=now,
            symbols=["QQQ"],
            instrument_symbols=[],
            anomalo_adapter=adapter,
            event_repository=repository,
            expected_events_enabled=True,
            scheduled_event_agent="scheduled-event-investigator",
        )

        first = ScheduledEventCoordinator(repository).execute(context, "macro")

        assert first.status == StepStatus.SUCCEEDED
        assert first.payload["schedule_api_called"] is True
        assert first.payload["discovered_count"] == len(
            [item for item in DEFAULT_EVENT_DEFINITIONS if item.category == "macro"]
        )
        assert first.payload["due_result_count"] == 1
        assert first.payload["events"][0]["result"]["status"] == "not_released"

        persisted = session.get(EventModel, historical.id)
        assert persisted is not None
        persisted.next_check_at = utc_now() - timedelta(seconds=1)
        session.commit()
        context.run_id = "run-fomc-full-2"

        second = ScheduledEventCoordinator(repository).execute(context, "macro")

        assert second.status == StepStatus.SUCCEEDED
        assert second.payload["schedule_api_called"] is False
        assert second.payload["missing_future_definitions"] == []
        assert second.payload["discovered_count"] == 0
        assert second.payload["due_result_count"] == 1
        confirmed = next(
            event
            for event in second.payload["events"]
            if event["event_key"] == historical.event_key
        )
        assert confirmed["result"]["version"] == 2
        assert confirmed["result"]["status"] == "confirmed"
        assert adapter.calls == [
            "scheduled_event_discovery",
            "scheduled_event_result",
            "scheduled_event_result",
        ]
        assert len(list(session.scalars(select(EventResultModel)))) == 2


def test_historical_fomc_without_result_is_retried_after_next_check() -> None:
    """A missed historical event remains queryable even when discovery is empty."""

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        repository = EventRepository(session)
        now = utc_now()
        repository.ensure_definitions(DEFAULT_EVENT_DEFINITIONS, now=now)
        meeting_at = now - timedelta(days=5)
        event_key = f"macro:fomc_decision:{meeting_at.date().isoformat()}"
        historical = repository.upsert_candidate(
            EventCandidate(
                definition_key="macro:fomc_decision",
                event_key=event_key,
                category="macro",
                subject_type="market",
                subject="market",
                event_type="fomc_decision",
                title="FOMC 利率决议（上周验证夹具）",
                period=str(meeting_at.date()),
                discovery_mode="scheduled",
                status="scheduled",
                scheduled_at=meeting_at,
                time_precision="exact",
                timezone="America/New_York",
                is_estimated=False,
                result_expected_at=None,
                next_check_at=now - timedelta(hours=1),
                confidence=1.0,
                sources=[
                    EventSourceEvidence(
                        publisher="Federal Reserve (fixture)",
                        url="https://example.com/fomc-calendar",
                        source_type="official_calendar",
                        is_primary=True,
                    )
                ],
            ),
            now=now,
        )
        adapter = HistoricalFomcRetryAdapter()
        context = RunContext(
            run_id="run-fomc-history-1",
            run_type="scheduled_event_discovery",
            cutoff_time=now,
            symbols=["QQQ"],
            instrument_symbols=[],
            anomalo_adapter=adapter,
            event_repository=repository,
            expected_events_enabled=True,
            scheduled_event_agent="scheduled-event-investigator",
        )

        first = ScheduledEventCoordinator(repository).execute(context, "macro")

        assert first.status == StepStatus.SUCCEEDED
        assert first.payload["schedule_api_called"] is True
        assert "macro:fomc_decision" in first.payload["missing_future_definitions"]
        assert first.payload["discovered_count"] == 0
        assert first.payload["missing_definitions"] == ["macro:fomc_decision"]
        assert first.payload["due_result_count"] == 1
        assert first.payload["counts"] == {"not_released": 1}
        assert first.payload["events"][0]["event_key"] == event_key
        assert first.payload["events"][0]["result"]["version"] == 1
        assert first.payload["events"][0]["result"]["status"] == "not_released"

        persisted = session.get(EventModel, historical.id)
        assert persisted is not None
        persisted.next_check_at = utc_now() - timedelta(seconds=1)
        session.commit()
        context.run_id = "run-fomc-history-2"

        second = ScheduledEventCoordinator(repository).execute(context, "macro")

        assert second.status == StepStatus.SUCCEEDED
        assert second.payload["schedule_api_called"] is True
        assert second.payload["discovered_count"] == 0
        assert second.payload["due_result_count"] == 1
        assert second.payload["counts"] == {"confirmed": 1}
        assert second.payload["events"][0]["result"]["version"] == 2
        assert second.payload["events"][0]["result"]["status"] == "confirmed"
        assert adapter.calls == [
            "scheduled_event_discovery",
            "scheduled_event_result",
            "scheduled_event_discovery",
            "scheduled_event_result",
        ]
        assert len(list(session.scalars(select(EventResultModel)))) == 2
        assert len(list(session.scalars(select(EventAgentRunModel)))) == 4


def test_http_anomalo_adapter_reads_structured_output() -> None:
    adapter = HttpAnomaloAdapter("https://anomalo.test")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/agents/scheduled-event-investigator/chat"
        body = request.read().decode()
        assert "response_format" in body
        return httpx.Response(
            200,
            json={"output": {"operation": "discover_schedule"}, "output_format": "json_schema"},
        )

    adapter.client.close()
    adapter.client = httpx.Client(transport=httpx.MockTransport(handler))
    response = adapter.investigate(
        request=AnomaloRequest(
            session_id="session",
            agent="scheduled-event-investigator",
            message="discover",
            response_format={"type": "json_schema"},
        )
    )
    adapter.close()
    assert response.is_mock is False
    assert response.output == {"operation": "discover_schedule"}


def test_discovery_contract_requires_result_collections() -> None:
    with pytest.raises(Exception):
        EventDiscoveryOutput.model_validate(
            {"operation": "discover_schedule", "generated_at": utc_now().isoformat()}
        )


def test_create_all_enforces_unique_event_result_versions() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    constraints = inspect(engine).get_unique_constraints("event_results")

    assert any(
        constraint["name"] == "uq_event_results_event_version"
        and constraint["column_names"] == ["event_id", "version"]
        for constraint in constraints
    )


def test_not_released_result_requires_next_check_at() -> None:
    result = EventResultOutput.model_validate(
        {
            "operation": "collect_result",
            "event_key": "macro:fomc_decision:pending",
            "result_status": "not_released",
            "facts": [],
            "needs_follow_up": True,
            "sources": [],
        }
    )
    event = type("Event", (), {"event_type": "fomc_decision"})()

    error = ScheduledEventCoordinator._result_semantic_error(event, result)

    assert error is not None
    assert "next_check_at" in error


def test_confirmed_fomc_result_requires_actual_value_and_source() -> None:
    result = EventResultOutput.model_validate(
        {
            "operation": "collect_result",
            "event_key": "macro:fomc_decision:empty-actual",
            "result_status": "confirmed",
            "facts": [{"name": "target_rate", "actual": None}],
            "summary": "name only",
            "needs_follow_up": False,
            "sources": [
                {
                    "publisher": "Federal Reserve",
                    "url": "https://example.com/fomc-empty",
                    "source_type": "primary",
                    "is_primary": True,
                }
            ],
        }
    )
    event = type("Event", (), {"event_type": "fomc_decision"})()

    error = ScheduledEventCoordinator._result_semantic_error(event, result)

    assert error is not None
    assert "至少需要一个实际值" in error


def test_confirmed_cpi_result_requires_canonical_actual_facts() -> None:
    result = EventResultOutput.model_validate(
        {
            "operation": "collect_result",
            "event_key": "macro:cpi:2026-08",
            "result_status": "confirmed",
            "facts": [
                {"name": "headline_cpi_mom", "actual": 0.2},
                {"name": "headline_cpi_yoy", "actual": 2.8},
                {"name": "core_cpi_mom", "actual": 0.3},
            ],
            "needs_follow_up": False,
            "sources": [
                {
                    "publisher": "US Bureau of Labor Statistics",
                    "url": "https://example.com/cpi",
                    "source_type": "primary",
                    "is_primary": True,
                }
            ],
        }
    )
    event = type("Event", (), {"event_type": "cpi"})()

    error = ScheduledEventCoordinator._result_semantic_error(event, result)

    assert error is not None
    assert "core_cpi_yoy" in error


def test_confirmed_earnings_reports_optional_expected_fact_gaps() -> None:
    result = EventResultOutput.model_validate(
        {
            "operation": "collect_result",
            "event_key": "instrument:earnings:INTC:2026Q3",
            "result_status": "confirmed",
            "facts": [
                {"name": "diluted_eps", "actual": 0.2},
                {"name": "revenue", "actual": 13.0},
            ],
            "needs_follow_up": False,
            "sources": [
                {
                    "publisher": "Intel Investor Relations",
                    "url": "https://example.com/intc-earnings",
                    "source_type": "primary",
                    "is_primary": True,
                }
            ],
        }
    )
    event = type("Event", (), {"event_type": "earnings"})()

    warnings = ScheduledEventCoordinator._result_completeness_warnings(event, result)

    assert len(warnings) == 1
    assert "forward_guidance" in warnings[0]
    assert "gross_margin" in warnings[0]


@pytest.mark.parametrize(
    ("event_type", "facts"),
    [
        (
            "cpi",
            ["headline_cpi_mom", "headline_cpi_yoy", "core_cpi_mom", "core_cpi_yoy"],
        ),
        (
            "pce",
            ["headline_pce_mom", "headline_pce_yoy", "core_pce_mom", "core_pce_yoy"],
        ),
        (
            "nonfarm_payrolls",
            ["nonfarm_payrolls", "unemployment_rate", "average_hourly_earnings_mom"],
        ),
        ("gdp", ["real_gdp_annualized_qoq"]),
        ("ism_manufacturing", ["ism_manufacturing_pmi"]),
        ("ism_services", ["ism_services_pmi"]),
        ("earnings", ["diluted_eps", "revenue"]),
    ],
)
def test_confirmed_event_accepts_minimum_required_actual_facts(
    event_type: str, facts: list[str]
) -> None:
    result = EventResultOutput.model_validate(
        {
            "operation": "collect_result",
            "event_key": f"fixture:{event_type}",
            "result_status": "confirmed",
            "facts": [{"name": name, "actual": 1} for name in facts],
            "needs_follow_up": False,
            "sources": [
                {
                    "publisher": "Official source",
                    "url": f"https://example.com/{event_type}",
                    "source_type": "primary",
                    "is_primary": True,
                }
            ],
        }
    )
    event = type("Event", (), {"event_type": event_type})()

    assert ScheduledEventCoordinator._result_semantic_error(event, result) is None
