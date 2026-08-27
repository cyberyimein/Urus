from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RemoteDecisionIntent(str, Enum):
    INSTRUMENT_ARBITRATION = "instrument_arbitration"
    GROUP_ARBITRATION = "group_arbitration"
    INDICATOR_ATTENTION = "indicator_attention"
    STRATEGY_ATTENTION = "strategy_attention"


class RemoteDecisionStatus(str, Enum):
    QUEUED = "queued"
    SUBMITTING = "submitting"
    RUNNING = "running"
    STOPPING = "stopping"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STOPPED = "stopped"
    ACCEPTED = "accepted"
    REJECTED_RESULT = "rejected_result"


class RemoteDecisionSource(BaseModel):
    """A locator for immutable evidence; never accepts evidence itself."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: str | None = Field(default=None, min_length=1, max_length=36)
    symbol: str | None = Field(default=None, min_length=1, max_length=32)
    observation_run_id: str | None = Field(default=None, min_length=1, max_length=36)
    snapshot_id: str | None = Field(default=None, min_length=1, max_length=36)
    group_version_id: str | None = Field(default=None, min_length=1, max_length=128)
    lens_id: str | None = Field(default=None, min_length=1, max_length=128)
    lens_type: str | None = Field(default=None, min_length=1, max_length=32)
    lens_version: str | None = Field(default=None, min_length=1, max_length=128)
    content_sha256: str | None = Field(default=None, min_length=64, max_length=64)

    @field_validator(
        "dataset_id",
        "symbol",
        "observation_run_id",
        "snapshot_id",
        "group_version_id",
        "lens_id",
        "lens_type",
        "lens_version",
        "content_sha256",
        mode="before",
    )
    @classmethod
    def strip_values(cls, value: object) -> str | None:
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    @field_validator("symbol")
    @classmethod
    def normalise_symbol(cls, value: str | None) -> str | None:
        return value.upper() if value else value

    @field_validator("content_sha256", mode="before")
    @classmethod
    def validate_hash(cls, value: object) -> str | None:
        if value is None:
            return None
        normalised = str(value).strip().removeprefix("sha256:")
        if not re.fullmatch(r"[0-9a-f]{64}", normalised):
            raise ValueError("content_sha256 必须是 64 位小写 SHA-256")
        return normalised

    @field_validator("dataset_id", "observation_run_id", "snapshot_id", "group_version_id", "lens_id")
    @classmethod
    def reject_latest_locator(cls, value: str | None) -> str | None:
        if value and value.lower() in {"latest", "current"}:
            raise ValueError("locator 必须指向不可变对象，不能使用 latest/current")
        return value


class RemoteDecisionPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_type: RemoteDecisionIntent
    source: RemoteDecisionSource


class RemoteDecisionSubmitRequest(RemoteDecisionPreflightRequest):
    preflight_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_intent_id: str = Field(min_length=1, max_length=128)

    @field_validator("request_intent_id", mode="before")
    @classmethod
    def strip_intent_id(cls, value: object) -> str:
        return str(value or "").strip()


class RemoteDecisionRerunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_intent_id: str | None = Field(default=None, min_length=1, max_length=128)


class RemoteDecisionIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class RemoteDecisionBindingSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_type: RemoteDecisionIntent
    workflow_ref: str
    status: str
    definition_hash: str
    compiled_hash: str
    capability_manifest_hash: str | None = None
    input_schema_version: str
    output_schema_version: str


class RemoteDecisionPreflightResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    blockers: list[RemoteDecisionIssue] = Field(default_factory=list)
    warnings: list[RemoteDecisionIssue] = Field(default_factory=list)
    intent_type: RemoteDecisionIntent
    source: RemoteDecisionSource
    source_summary: dict[str, Any] = Field(default_factory=dict)
    binding: RemoteDecisionBindingSummary | None = None
    input_sha256: str | None = None
    preflight_fingerprint: str | None = None


class RemoteDecisionArtifact(BaseModel):
    """Strict common result envelope; intent-specific checks run server-side."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(min_length=1, max_length=128)
    intent_type: RemoteDecisionIntent
    scope: dict[str, Any]
    # The key is required even for cross-section artifacts, where its value is
    # null because one projection may span multiple datasets.
    dataset_id: str | None
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    completeness: Literal["complete", "partial", "insufficient_evidence"]
    decision: dict[str, Any]
    summary: str | None = Field(default=None, max_length=1000)
    confidence: float | None = Field(default=None, ge=0, le=1)
    notable_cards: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    coverage_gaps: list[str] = Field(default_factory=list, max_length=50)
    warnings: list[str] = Field(default_factory=list, max_length=50)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    usage: dict[str, Any] = Field(default_factory=dict)
    trace_ref: str | None = Field(default=None, max_length=256)
    generated_at: datetime | None = None


class RemoteDecisionArtifactSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_schema_version: str
    completeness: str
    artifact_sha256: str
    validation_status: str
    accepted_at: datetime | None = None


class RemoteDecisionRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_run_id: str
    anomalo_run_id: str | None = None
    intent_type: RemoteDecisionIntent
    request_intent_id: str
    idempotency_key: str
    scope_type: str
    scope_id: str
    scope_version: str | None = None
    dataset_id: str | None = None
    lens_type: str | None = None
    lens_id: str | None = None
    lens_version: str | None = None
    source: RemoteDecisionSource
    workflow_ref: str
    input_schema_version: str
    input_sha256: str
    status: RemoteDecisionStatus
    remote_status: str | None = None
    validation_status: str
    latest_event_sequence: int
    error_code: str | None = None
    safe_error_message: str | None = None
    result: dict[str, Any] | None = None
    artifact: RemoteDecisionArtifactSummary | None = None
    created_at: datetime
    submitted_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class RemoteDecisionEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int
    event_type: str
    event_timestamp: datetime | None = None
    node_id: str | None = None
    attempt: int | None = None
    child_run_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
