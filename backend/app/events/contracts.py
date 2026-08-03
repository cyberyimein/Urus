from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class EventSourceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publisher: str
    url: str
    source_type: Literal["primary", "secondary", "official_calendar", "filing", "unknown"] = "unknown"
    published_at: datetime | None = None
    is_primary: bool = False
    evidence_note: str | None = None


class EventCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition_key: str
    event_key: str
    category: Literal["macro", "instrument", "theme"]
    subject_type: Literal["market", "symbol", "theme"]
    subject: str
    event_type: str
    title: str
    period: str | None = None
    discovery_mode: Literal["scheduled", "breaking"] = "scheduled"
    status: Literal["expected", "scheduled", "unverified", "postponed", "cancelled"] = "scheduled"
    scheduled_at: datetime | None = None
    time_precision: Literal["exact", "session", "date", "window", "unknown"] = "unknown"
    timezone: str | None = None
    is_estimated: bool = True
    announced_at: datetime | None = None
    result_expected_at: datetime | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    next_check_at: datetime | None = None
    sources: list[EventSourceEvidence]


class EventDiscoveryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["discover_schedule"]
    generated_at: datetime
    events: list[EventCandidate]
    missing_definitions: list[str]
    notes: list[str]


class EventFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    actual: str | float | int | bool | None = None
    consensus: str | float | int | bool | None = None
    previous: str | float | int | bool | None = None
    unit: str | None = None
    note: str | None = None


class EventResultOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["collect_result"]
    event_key: str
    result_status: Literal["not_released", "partial", "confirmed", "revised"]
    occurred_at: datetime | None = None
    released_at: datetime | None = None
    facts: list[EventFact]
    summary: str | None = None
    guidance: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    needs_follow_up: bool = False
    next_check_at: datetime | None = None
    sources: list[EventSourceEvidence]


def _response_format(name: str, model: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": model.model_json_schema(),
        },
    }


def discovery_response_format() -> dict[str, Any]:
    return _response_format("scheduled_event_discovery", EventDiscoveryOutput)


def result_response_format() -> dict[str, Any]:
    return _response_format("scheduled_event_result", EventResultOutput)
