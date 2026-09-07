from __future__ import annotations

from datetime import date, datetime

from pydantic import Field, field_validator

from .breakout import (
    IntradayObservation,
    build_s1_setup,
    can_rebuild_s1_setup,
    evaluate_s1_trigger,
)
from .contracts import (
    FrozenEvidence,
    OpportunityModel,
    SetupRecord,
    TriggerEvaluation,
    TriggerRecord,
)


class TradingSessionCalendar(OpportunityModel):
    session_dates: list[date] = Field(min_length=1)

    @field_validator("session_dates")
    @classmethod
    def validate_session_dates(cls, value: list[date]) -> list[date]:
        if value != sorted(set(value)):
            raise ValueError("交易日历必须按日期升序且不能重复")
        return value

    def session_number(self, *, valid_from: date, observed_date: date) -> int | None:
        if observed_date not in self.session_dates:
            return None
        future_sessions = [item for item in self.session_dates if item >= valid_from]
        try:
            return future_sessions.index(observed_date) + 1
        except ValueError:
            return None


class TradingSessionCursor(OpportunityModel):
    session_date: date | None = None
    session_number: int = Field(default=0, ge=0)


class KnowledgeCursor(OpportunityModel):
    known_at: datetime | None = None
    last_available_at: datetime | None = None

    @field_validator("known_at", "last_available_at")
    @classmethod
    def validate_timestamps(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("knowledge cursor 时间必须包含时区")
        return value


class S1LifecycleState(OpportunityModel):
    setup: SetupRecord
    trigger: TriggerRecord | None = None
    last_observed_bar_id: str | None = None
    last_known_at: datetime | None = None
    completed_sessions: int = Field(default=0, ge=0)
    market_cursor: TradingSessionCursor = Field(default_factory=TradingSessionCursor)
    knowledge_cursor: KnowledgeCursor = Field(default_factory=KnowledgeCursor)
    transition_hash: str = Field(min_length=64, max_length=64)

    @field_validator("last_known_at")
    @classmethod
    def validate_last_known_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("lifecycle last_known_at 必须包含时区")
        return value


def start_s1_lifecycle(setup: SetupRecord) -> S1LifecycleState:
    from app.decision_harness.contracts import content_sha256

    payload = {
        "setup_id": setup.setup_id,
        "status": setup.status,
        "completed_sessions": 0,
    }
    return S1LifecycleState(
        setup=setup,
        market_cursor=TradingSessionCursor(),
        knowledge_cursor=KnowledgeCursor(),
        transition_hash=content_sha256(payload),
    )


def advance_s1_lifecycle(
    *,
    state: S1LifecycleState,
    observation: IntradayObservation,
    previous_close: float | None,
    known_at: datetime,
    calendar: TradingSessionCalendar | None = None,
    crossed_while_unobserved: bool = False,
) -> tuple[S1LifecycleState, TriggerEvaluation]:
    from app.decision_harness.contracts import content_sha256

    if state.last_observed_bar_id == observation.bar_id and state.last_known_at == known_at:
        evaluation = evaluate_s1_trigger(
            setup=state.setup,
            observation=observation,
            previous_close=previous_close,
            known_at=known_at,
            session_number=max(1, state.completed_sessions),
            crossed_while_unobserved=crossed_while_unobserved,
        )
        return state, evaluation
    if state.trigger is not None or state.setup.status != "armed":
        evaluation = TriggerEvaluation(
            status="no_trigger",
            setup_id=state.setup.setup_id,
            observed_at=observation.available_at,
            reason_code="setup_lifecycle_resolved",
        )
        return state, evaluation
    session_number = (
        calendar.session_number(
            valid_from=state.setup.valid_from,
            observed_date=observation.end_at.date(),
        )
        if calendar is not None
        else state.completed_sessions + 1
    )
    if session_number is None:
        evaluation = TriggerEvaluation(
            status="no_trigger",
            setup_id=state.setup.setup_id,
            observed_at=observation.available_at,
            reason_code="non_trading_session",
        )
        return state, evaluation
    evaluation = evaluate_s1_trigger(
        setup=state.setup,
        observation=observation,
        previous_close=previous_close,
        known_at=known_at,
        session_number=session_number,
        crossed_while_unobserved=crossed_while_unobserved,
    )
    next_status = state.setup.status
    next_trigger = state.trigger
    if evaluation.status == "triggered" and evaluation.trigger is not None:
        next_status = "resolved"
        next_trigger = evaluation.trigger
    elif evaluation.status == "invalidated":
        next_status = "invalidated"
    elif evaluation.status == "expired":
        next_status = "expired"
    next_setup = state.setup.model_copy(update={"status": next_status})
    transition = {
        "setup_id": next_setup.setup_id,
        "status": next_status,
        "trigger_id": next_trigger.trigger_id if next_trigger else None,
        "bar_id": observation.bar_id,
        "known_at": known_at.isoformat(),
        "session_number": session_number,
    }
    return (
        S1LifecycleState(
            setup=next_setup,
            trigger=next_trigger,
            last_observed_bar_id=observation.bar_id,
            last_known_at=known_at,
            completed_sessions=max(state.completed_sessions, session_number),
            market_cursor=TradingSessionCursor(
                session_date=observation.end_at.date(),
                session_number=max(state.completed_sessions, session_number),
            ),
            knowledge_cursor=KnowledgeCursor(
                known_at=known_at,
                last_available_at=observation.available_at,
            ),
            transition_hash=content_sha256(transition),
        ),
        evaluation,
    )


def rebuild_s1_lifecycle(
    *,
    state: S1LifecycleState,
    evidence: FrozenEvidence,
    origin_scan_id: str,
) -> S1LifecycleState | None:
    if not can_rebuild_s1_setup(
        previous_setup=state.setup,
        evidence=evidence,
        completed_bars_since_previous_setup=state.completed_sessions,
    ):
        return None
    return start_s1_lifecycle(
        build_s1_setup(
            evidence=evidence,
            origin_scan_id=origin_scan_id,
            stream=state.setup.stream,
            trigger_timeframe=state.setup.trigger_timeframe,
        )
    )
