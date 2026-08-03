from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.time import to_iso, utc_now
from app.events.contracts import EventCandidate, EventResultOutput
from app.events.definitions import EventDefinitionSpec
from app.models import (
    EventAgentRunModel,
    EventDefinitionModel,
    EventMarketReactionModel,
    EventModel,
    EventResultModel,
    EventScheduleInitializationModel,
    EventSourceModel,
)


class EventRepository:
    """SQLite persistence boundary for the scheduled-event ledger."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def ensure_definitions(
        self, specs: tuple[EventDefinitionSpec, ...], *, now: datetime | None = None
    ) -> None:
        timestamp = now or utc_now()
        for spec in specs:
            model = self.session.get(EventDefinitionModel, spec.key)
            if model is None:
                model = EventDefinitionModel(
                    key=spec.key,
                    category=spec.category,
                    subject_type=spec.subject_type,
                    event_type=spec.event_type,
                    title=spec.title,
                    discovery_mode=spec.discovery_mode,
                    enabled=True,
                    horizon_days=spec.horizon_days,
                    result_schema_name=spec.result_schema_name,
                    metadata_payload={
                        "description": spec.description,
                        "cadence": spec.cadence,
                        "preferred_sources": list(spec.preferred_sources),
                    },
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                self.session.add(model)
            else:
                model.category = spec.category
                model.subject_type = spec.subject_type
                model.event_type = spec.event_type
                model.title = spec.title
                model.discovery_mode = spec.discovery_mode
                model.horizon_days = spec.horizon_days
                model.result_schema_name = spec.result_schema_name
                model.metadata_payload = {
                    "description": spec.description,
                    "cadence": spec.cadence,
                    "preferred_sources": list(spec.preferred_sources),
                }
                model.updated_at = timestamp
        self.session.commit()

    def create_schedule_initialization(
        self,
        *,
        initialization_id: str,
        horizon_days: int,
        categories: list[str],
        definitions: list[str],
        targets: list[dict[str, Any]],
        started_at: datetime | None = None,
    ) -> EventScheduleInitializationModel:
        model = EventScheduleInitializationModel(
            id=initialization_id,
            status="running",
            horizon_days=horizon_days,
            requested_categories=categories,
            requested_definitions=definitions,
            requested_targets=targets,
            started_at=started_at or utc_now(),
        )
        self.session.add(model)
        self.session.commit()
        return model

    def update_schedule_initialization(
        self,
        model: EventScheduleInitializationModel,
        *,
        status: str,
        discovered_count: int,
        missing_count: int,
        api_call_count: int,
        metadata_payload: dict[str, Any] | None = None,
        error_message: str | None = None,
        completed_at: datetime | None = None,
    ) -> EventScheduleInitializationModel:
        model.status = status
        model.discovered_count = discovered_count
        model.missing_count = missing_count
        model.api_call_count = api_call_count
        if metadata_payload is not None:
            model.metadata_payload = metadata_payload
        model.error_message = error_message
        model.completed_at = completed_at
        self.session.commit()
        return model

    def upsert_candidate(
        self, candidate: EventCandidate, *, now: datetime | None = None
    ) -> EventModel:
        timestamp = now or utc_now()
        model = self.session.scalar(
            select(EventModel).where(EventModel.event_key == candidate.event_key)
        )
        terminal = model is not None and model.status in {"confirmed", "revised"}
        values = {
            "definition_key": candidate.definition_key,
            "category": candidate.category,
            "subject_type": candidate.subject_type,
            "subject": candidate.subject.upper() if candidate.subject_type == "symbol" else candidate.subject,
            "event_type": candidate.event_type,
            "title": candidate.title,
            "period": candidate.period,
            "discovery_mode": candidate.discovery_mode,
            "status": model.status if terminal else candidate.status,
            "scheduled_at": candidate.scheduled_at,
            "time_precision": candidate.time_precision,
            "timezone": candidate.timezone,
            "is_estimated": candidate.is_estimated,
            "announced_at": candidate.announced_at,
            "result_expected_at": candidate.result_expected_at,
            "next_check_at": (
                model.next_check_at
                if terminal
                else candidate.next_check_at or candidate.result_expected_at
            ),
            "confidence": candidate.confidence,
            "updated_at": timestamp,
        }
        if model is None:
            model = EventModel(
                id=str(uuid4()),
                event_key=candidate.event_key,
                latest_result_version=0,
                metadata_payload={"period": candidate.period} if candidate.period else {},
                created_at=timestamp,
                **values,
            )
            self.session.add(model)
            self.session.flush()
        else:
            for key, value in values.items():
                setattr(model, key, value)
        self._upsert_sources(model, candidate.sources)
        self.session.commit()
        return model

    def _upsert_sources(self, event: EventModel, sources: list[Any]) -> None:
        if not sources:
            return
        existing = {
            source.url
            for source in self.session.scalars(
                select(EventSourceModel).where(EventSourceModel.event_id == event.id)
            )
        }
        for source in sources:
            if source.url in existing:
                continue
            self.session.add(
                EventSourceModel(
                    event_id=event.id,
                    publisher=source.publisher,
                    url=source.url,
                    source_type=source.source_type,
                    published_at=source.published_at,
                    is_primary=source.is_primary,
                    evidence_payload={"evidence_note": source.evidence_note}
                    if source.evidence_note
                    else {},
                )
            )
            existing.add(source.url)

    def due_events(
        self, category: str, *, now: datetime | None = None, limit: int = 20
    ) -> list[EventModel]:
        timestamp = now or utc_now()
        statement = (
            select(EventModel)
            .options(
                selectinload(EventModel.sources),
                selectinload(EventModel.results),
                selectinload(EventModel.market_reactions),
            )
            .where(
                EventModel.category == category,
                EventModel.discovery_mode == "scheduled",
                EventModel.status.not_in(("cancelled", "confirmed", "revised")),
                or_(
                    EventModel.result_expected_at <= timestamp,
                    and_(
                        EventModel.result_expected_at.is_(None),
                        EventModel.scheduled_at.is_not(None),
                        EventModel.scheduled_at <= timestamp,
                    ),
                ),
                (EventModel.next_check_at.is_(None) | (EventModel.next_check_at <= timestamp)),
            )
            .order_by(EventModel.result_expected_at.asc())
            .limit(limit)
        )
        return list(self.session.scalars(statement))

    def has_future_event(
        self,
        definition_key: str,
        *,
        subject: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Return whether this target has usable future schedule coverage.

        A schedule investigation can legitimately return an expected event
        without a concrete date.  In that case ``next_check_at`` is the
        investigation's retry deadline.  Treating that row as covered until
        the deadline prevents the daily workflow from repeating the expensive
        calendar search on every run.  Rows with a concrete ``scheduled_at``
        still use that timestamp; a past event never counts as future
        coverage, even when its result retry is scheduled later.
        """

        timestamp = now or utc_now()
        filters = [
            EventModel.definition_key == definition_key,
            EventModel.discovery_mode == "scheduled",
            EventModel.status != "cancelled",
            or_(
                and_(
                    EventModel.scheduled_at.is_not(None),
                    EventModel.scheduled_at > timestamp,
                ),
                and_(
                    EventModel.scheduled_at.is_(None),
                    EventModel.next_check_at.is_not(None),
                    EventModel.next_check_at > timestamp,
                ),
            ),
        ]
        if subject is not None:
            filters.append(func.upper(EventModel.subject) == subject.upper())
        statement = select(EventModel.id).where(*filters).limit(1)
        return self.session.scalar(statement) is not None

    def list_events(self, category: str, *, limit: int = 100) -> list[EventModel]:
        statement = (
            select(EventModel)
            .options(
                selectinload(EventModel.sources),
                selectinload(EventModel.results),
                selectinload(EventModel.market_reactions),
            )
            .where(EventModel.category == category)
            .order_by(EventModel.scheduled_at.asc().nullslast(), EventModel.updated_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(statement))

    def save_agent_run(
        self,
        *,
        run_id: str,
        event_id: str | None,
        operation: str,
        agent: str,
        session_id: str,
        status: str,
        request_payload: dict[str, Any],
        response_payload: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> EventAgentRunModel:
        model = EventAgentRunModel(
            id=str(uuid4()),
            event_id=event_id,
            run_id=run_id,
            operation=operation,
            agent=agent,
            session_id=session_id,
            status=status,
            request_payload=request_payload,
            response_payload=response_payload or {},
            error_code=error_code,
            error_message=error_message,
            started_at=started_at or utc_now(),
            completed_at=completed_at,
        )
        self.session.add(model)
        self.session.commit()
        return model

    def save_result(
        self,
        event: EventModel,
        result: EventResultOutput,
        *,
        captured_at: datetime | None = None,
    ) -> EventResultModel:
        timestamp = captured_at or utc_now()
        version = int(event.latest_result_version or 0) + 1
        model = EventResultModel(
            id=str(uuid4()),
            event_id=event.id,
            version=version,
            result_status=result.result_status,
            released_at=result.released_at,
            captured_at=timestamp,
            facts=[fact.model_dump(mode="json") for fact in result.facts],
            summary=result.summary,
            guidance=result.guidance,
            confidence=result.confidence,
            needs_follow_up=result.needs_follow_up,
            next_check_at=result.next_check_at,
            source_count=len(result.sources),
        )
        event.latest_result_version = version
        event.result_available_at = result.released_at or (
            timestamp if result.result_status in {"confirmed", "revised"} else None
        )
        event.occurred_at = result.occurred_at or event.occurred_at
        event.next_check_at = result.next_check_at
        event.status = result.result_status
        event.updated_at = timestamp
        self._upsert_sources(event, result.sources)
        self.session.add(model)
        event.results.append(model)
        self.session.commit()
        return model

    def save_market_reaction(
        self,
        *,
        event_id: str,
        run_id: str,
        window: str,
        status: str,
        payload: dict[str, Any],
        measured_at: datetime | None = None,
    ) -> EventMarketReactionModel:
        statement = select(EventMarketReactionModel).where(
            EventMarketReactionModel.event_id == event_id,
            EventMarketReactionModel.run_id == run_id,
            EventMarketReactionModel.window == window,
        )
        model = self.session.scalar(statement)
        if model is None:
            model = EventMarketReactionModel(
                id=str(uuid4()),
                event_id=event_id,
                run_id=run_id,
                window=window,
                measured_at=measured_at or utc_now(),
                status=status,
                payload=payload,
            )
            self.session.add(model)
        else:
            model.measured_at = measured_at or utc_now()
            model.status = status
            model.payload = payload
        self.session.commit()
        return model

    @staticmethod
    def event_payload(event: EventModel) -> dict[str, Any]:
        latest_result = event.results[-1] if event.results else None
        return {
            "id": event.id,
            "event_key": event.event_key,
            "definition_key": event.definition_key,
            "category": event.category,
            "subject_type": event.subject_type,
            "subject": event.subject,
            "event_type": event.event_type,
            "title": event.title,
            "period": event.period,
            "status": event.status,
            "discovery_mode": event.discovery_mode,
            "scheduled_at": to_iso(event.scheduled_at),
            "time_precision": event.time_precision,
            "timezone": event.timezone,
            "is_estimated": event.is_estimated,
            "announced_at": to_iso(event.announced_at),
            "occurred_at": to_iso(event.occurred_at),
            "result_expected_at": to_iso(event.result_expected_at),
            "result_available_at": to_iso(event.result_available_at),
            "next_check_at": to_iso(event.next_check_at),
            "confidence": event.confidence,
            "result": (
                {
                    "version": latest_result.version,
                    "status": latest_result.result_status,
                    "released_at": to_iso(latest_result.released_at),
                    "captured_at": to_iso(latest_result.captured_at),
                    "facts": latest_result.facts,
                    "summary": latest_result.summary,
                    "guidance": latest_result.guidance,
                    "confidence": latest_result.confidence,
                    "needs_follow_up": latest_result.needs_follow_up,
                    "next_check_at": to_iso(latest_result.next_check_at),
                    "source_count": latest_result.source_count,
                }
                if latest_result
                else None
            ),
            "sources": [
                {
                    "publisher": source.publisher,
                    "url": source.url,
                    "source_type": source.source_type,
                    "published_at": to_iso(source.published_at),
                    "is_primary": source.is_primary,
                }
                for source in event.sources
            ],
            "market_reactions": [
                {
                    "window": reaction.window,
                    "measured_at": to_iso(reaction.measured_at),
                    "status": reaction.status,
                    **reaction.payload,
                }
                for reaction in event.market_reactions
            ],
        }
