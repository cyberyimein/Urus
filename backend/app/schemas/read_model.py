from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import RunStatusValue, RunTypeValue, StepCodeValue, StepStatusValue


class MockBase(BaseModel):
    is_mock: bool = True


class MarketCard(MockBase):
    symbol: str
    label: str
    last_price: float | None = None
    change_percent: float | None = None
    trend: str | None = None
    session_note: str | None = None
    note: str


class InstrumentCard(MockBase):
    symbol: str
    label: str
    last_price: float | None = None
    change_percent: float | None = None
    trend: str | None = None
    technical_note: str | None = None
    note: str


class EventSummary(MockBase):
    category: str
    status: StepStatusValue
    title: str | None = None
    summary: str | None = None
    reason: str | None = None


class OptionsPlaceholder(MockBase):
    status: str
    available: bool = False
    note: str


class DecisionPlaceholder(MockBase):
    status: str
    stance: str | None = None
    confidence: float | None = None
    summary: str
    note: str


class ReadModelStep(BaseModel):
    code: StepCodeValue
    label: str
    status: StepStatusValue
    summary: str | None = None
    error_message: str | None = None


class DataQuality(MockBase):
    status: str
    message: str
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class FrontendReadModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    run_id: str
    snapshot_id: str
    run_type: RunTypeValue
    run_status: RunStatusValue
    cutoff_time: datetime
    generated_at: datetime
    is_mock: bool = True
    market: MarketCard | None = None
    instrument: InstrumentCard | None = None
    macro_event: EventSummary
    options: OptionsPlaceholder
    instrument_event: EventSummary
    decision: DecisionPlaceholder
    steps: list[ReadModelStep]
    data_quality: DataQuality


class RunCreateRequest(BaseModel):
    run_type: RunTypeValue
    symbols: list[str] | None = None
    simulate_macro_event: bool = False
    simulate_instrument_event: bool = False
    fail_step: StepCodeValue | None = None


class StepRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    position: int
    step_code: StepCodeValue
    status: StepStatusValue
    started_at: datetime | None = None
    completed_at: datetime | None = None
    summary: str | None = None
    error_message: str | None = None
    payload: dict[str, Any] | None = None


class RunListItem(BaseModel):
    id: str
    run_type: RunTypeValue
    status: RunStatusValue
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cutoff_time: datetime
    snapshot_id: str | None = None
    error_message: str | None = None


class RunDetailResponse(RunListItem):
    steps: list[StepRunResponse]


class RunCreateResponse(BaseModel):
    run_id: str
    status: RunStatusValue
    snapshot_id: str | None = None


class SnapshotResponse(BaseModel):
    id: str
    run_id: str
    schema_version: str
    cutoff_time: datetime
    created_at: datetime
    quality_status: str
    payload: dict[str, Any]


class HealthResponse(BaseModel):
    status: str
    environment: str
    database: str


class VersionResponse(BaseModel):
    app_name: str
    app_version: str
    api_schema_version: str


class WatchlistResponse(BaseModel):
    symbols: list[str]
    is_development_allowlist: bool = True
    is_mock: bool = True
