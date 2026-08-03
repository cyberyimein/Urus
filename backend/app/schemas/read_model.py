from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.core.time import as_utc
from app.schemas.enums import RunStatusValue, RunTypeValue, StepCodeValue, StepStatusValue


class MockBase(BaseModel):
    is_mock: bool = True


class MarketCard(MockBase):
    symbol: str
    label: str
    data_mode: str = "mock"
    source: str = "mock_adapter"
    quote_code: str | None = None
    last_price: float | None = None
    change_percent: float | None = None
    regular_change_percent: float | None = None
    previous_close: float | None = None
    volume: int | None = None
    quote_time: str | None = None
    session: str | None = None
    session_label: str | None = None
    session_price_source: str | None = None
    premarket_price: float | None = None
    premarket_volume: int | None = None
    premarket_change_percent: float | None = None
    afterhours_price: float | None = None
    afterhours_volume: int | None = None
    afterhours_change_percent: float | None = None
    trend: str | None = None
    session_note: str | None = None
    history: dict[str, Any] = Field(default_factory=dict)
    market_snapshot: dict[str, Any] = Field(default_factory=dict)
    macro_context: dict[str, Any] = Field(default_factory=dict)
    data_state: str = "mock"
    quality_status: str = "mock"
    quality_warnings: list[str] = Field(default_factory=list)
    note: str


class InstrumentCard(MockBase):
    symbol: str
    label: str
    last_price: float | None = None
    change_percent: float | None = None
    trend: str | None = None
    technical_note: str | None = None
    data_state: str = "unavailable"
    note: str


class EventSummary(MockBase):
    category: str
    status: StepStatusValue
    title: str | None = None
    summary: str | None = None
    reason: str | None = None
    data_state: str = "skipped"


class OptionsPlaceholder(MockBase):
    is_mock: Literal[True] = True
    status: str
    available: bool = False
    data_state: str = "placeholder"
    note: str


class OptionsSnapshotSymbol(BaseModel):
    symbol: str
    spot: float
    spot_time: str | None = None
    overview: dict[str, float | None]
    expirations: list[dict[str, Any]]


class OptionsAnalysis(BaseModel):
    is_mock: Literal[False]
    status: str
    available: bool
    data_state: str = "live"
    provider: str
    source_mode: str
    captured_at: datetime
    requested_symbols: list[str] = Field(default_factory=list)
    unavailable_symbols: list[str] = Field(default_factory=list)
    symbols: list[OptionsSnapshotSymbol]
    subscription_quota: dict[str, int | None]
    model_assumptions: list[str]
    warnings: list[str]
    note: str


class DecisionPlaceholder(MockBase):
    status: str
    stance: str | None = None
    confidence: float | None = None
    summary: str
    data_state: str = "placeholder"
    note: str


class ReadModelStep(BaseModel):
    code: StepCodeValue
    label: str
    status: StepStatusValue
    data_state: str = "unavailable"
    summary: str | None = None
    error_message: str | None = None


class DataQuality(MockBase):
    data_state: str = "mixed"
    status: str
    message: str
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class FrontendReadModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    data_mode: str = "mock"
    run_id: str
    snapshot_id: str
    run_type: RunTypeValue
    run_status: RunStatusValue
    cutoff_time: datetime
    generated_at: datetime
    data_state: str = "mixed"
    is_mock: bool = True
    market: MarketCard | None = None
    instrument: InstrumentCard | None = None
    macro_event: EventSummary
    options: Annotated[OptionsPlaceholder | OptionsAnalysis, Field(discriminator="is_mock")]
    instrument_event: EventSummary
    decision: DecisionPlaceholder
    steps: list[ReadModelStep]
    data_quality: DataQuality

    @field_validator("cutoff_time", "generated_at", mode="before")
    @classmethod
    def normalize_timestamps(cls, value: datetime | str) -> datetime:
        return as_utc(value)

    @field_serializer("cutoff_time", "generated_at")
    def serialize_timestamps(self, value: datetime) -> str:
        return as_utc(value).isoformat()


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
    data_state: str = "unavailable"
    summary: str | None = None
    error_message: str | None = None
    payload: dict[str, Any] | None = None

    @field_validator("started_at", "completed_at", mode="before")
    @classmethod
    def normalize_timestamps(cls, value: datetime | str | None) -> datetime | None:
        return as_utc(value) if value is not None else None

    @field_serializer("started_at", "completed_at")
    def serialize_timestamps(self, value: datetime | None) -> str | None:
        return as_utc(value).isoformat() if value is not None else None


class RunListItem(BaseModel):
    id: str
    run_type: RunTypeValue
    status: RunStatusValue
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cutoff_time: datetime
    snapshot_id: str | None = None
    error_message: str | None = None

    @field_validator("started_at", "completed_at", "cutoff_time", mode="before")
    @classmethod
    def normalize_timestamps(cls, value: datetime | str | None) -> datetime | None:
        return as_utc(value) if value is not None else None

    @field_serializer("started_at", "completed_at", "cutoff_time")
    def serialize_timestamps(self, value: datetime | None) -> str | None:
        return as_utc(value).isoformat() if value is not None else None


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

    @field_validator("cutoff_time", "created_at", mode="before")
    @classmethod
    def normalize_timestamps(cls, value: datetime | str) -> datetime:
        return as_utc(value)

    @field_serializer("cutoff_time", "created_at")
    def serialize_timestamps(self, value: datetime) -> str:
        return as_utc(value).isoformat()


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
    option_symbols: list[str] = Field(default_factory=list)
    option_excluded_symbols: list[str] = Field(default_factory=list)
    is_development_allowlist: bool = True
    is_mock: bool = True
