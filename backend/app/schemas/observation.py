from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ObservationGroupCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=1000)
    symbols: list[str] = Field(min_length=1, max_length=200)
    benchmark_symbols: list[str] = Field(default_factory=list, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=20)
    display_order: int = Field(default=0, ge=0, le=10000)
    base_version_id: str | None = None

    @field_validator("group_id", "display_name", "description", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("group_id")
    @classmethod
    def normalize_group_id(cls, value: str) -> str:
        return value.lower()

    @field_validator("symbols", "benchmark_symbols")
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            symbol = str(value).strip().upper()
            if symbol and symbol not in normalized:
                normalized.append(symbol)
        return normalized

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            tag = str(value).strip()
            if tag and tag not in normalized:
                normalized.append(tag)
        return normalized


class ObservationGroupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str
    group_id: str
    version: int
    status: Literal["active", "retired", "draft"] | str
    source: Literal["manual", "universe"] | str
    universe_revision_id: str | None = None
    display_name: str
    description: str
    symbols: list[str]
    benchmark_symbols: list[str]
    tags: list[str]
    display_order: int
    content_sha256: str
    created_at: datetime
    activated_at: datetime | None


class ObservationGroupDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group: ObservationGroupResponse
    latest_snapshot: dict[str, Any] | None = None


class ObservationRunGroupSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_run_id: str
    group_id: str
    snapshot_id: str
    dataset_id: str
    group_version_id: str
    snapshot: dict[str, Any]


class ObservationGroupSyncResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    source_url: str | None = None
    universe_revision_id: str
    universe_version_id: str
    universe_revision: int
    universe_freshness: Literal["fresh", "stale", "local"] | str
    warnings: list[str] = Field(default_factory=list)
    sync_error: str | None = None
    upstream_universe_version_id: str | None = None
    upstream_universe_revision: int | None = None
    symbol_count: int
    group_count: int
    groups: list[ObservationGroupResponse]


class ObservationRunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_ids: list[str] = Field(default_factory=list, max_length=100)
    trading_date: date | None = None
    cutoff_time: datetime | None = None
    trigger_mode: Literal["manual", "scheduled"] = "manual"
    request_intent_id: str | None = Field(default=None, max_length=128)
    universe_revision_id: str | None = Field(default=None, max_length=36)
    universe_freshness: str | None = Field(default=None, max_length=16)
    universe_source_url: str | None = Field(default=None, max_length=512)

    @field_validator("group_ids")
    @classmethod
    def normalize_group_ids(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            group_id = str(value).strip().lower()
            if group_id and group_id not in normalized:
                normalized.append(group_id)
        return normalized


class ObservationRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: str
    trigger_mode: str
    trading_date: date
    universe_revision_id: str | None = None
    universe_freshness: str = "unknown"
    universe_source_url: str | None = None
    idempotency_key: str
    group_ids: list[str]
    group_version_ids: list[str]
    group_snapshots: list[dict[str, Any]] = Field(default_factory=list)
    report: dict[str, Any] = Field(default_factory=dict)
    group_count: int
    successful_group_count: int = 0
    failed_group_count: int = 0
    content_sha256: str
    created_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None
