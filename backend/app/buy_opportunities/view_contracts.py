"""Contracts for the read-only, user-initiated opportunity view.

The view layer deliberately has no order, delivery, portfolio, or research
outcome concepts.  It describes what a frozen evidence package says at one
point in time and keeps the evidence provenance next to the conclusion.
"""

from __future__ import annotations

from datetime import date, datetime
from math import isfinite
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.decision_harness.contracts import content_sha256, normalise_symbol

from .contracts import EvidenceReference, OpportunityModel


VIEW_STATUSES = {
    "unavailable",
    "stale",
    "invalidated",
    "expired",
    "preparing",
    "triggered_unverified",
    "in_entry_zone",
    "extended",
    "waiting",
}
VIEW_REFERENCE_KINDS = {"daily_close", "intraday_quote", "none"}
VIEW_FRESHNESS = {"fresh", "stale", "unknown"}
VIEW_QUALITY = {"ok", "partial", "unavailable", "unknown"}
RESEARCH_SUPPORT = {"unknown", "research_only", "supported"}


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("view 时间戳必须包含时区")
    return value


def _finite(value: object, *, name: str, minimum: float | None = None) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} 必须是有限数字")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是有限数字") from exc
    if not isfinite(result):
        raise ValueError(f"{name} 必须是有限数字")
    if minimum is not None and result < minimum:
        raise ValueError(f"{name} 不能小于 {minimum}")
    return result


class ViewQuote(OpportunityModel):
    """A quote usable by a read-only view projection.

    It is intentionally a local contract instead of an import of the legacy
    research execution quote.  Callers may convert their quote object to this
    shape, while the projector also accepts mapping/object adapters.
    """

    quote_id: str = Field(min_length=1, max_length=128)
    symbol: str
    source_mode: Literal[
        "observed_pit", "historical_reconstruction", "daily_execution_proxy"
    ]
    data_source: Literal["real", "synthetic", "unknown"] = "unknown"
    price_basis: Literal["raw", "adjusted"]
    price_mapping_id: str | None = None
    timestamp: datetime
    received_at: datetime
    bid: float
    ask: float
    quality: Literal["ok", "partial", "unknown"] = "ok"
    source: str | None = None
    source_revision: str | None = None

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        return normalise_symbol(value)

    @field_validator("timestamp", "received_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _aware(value)

    @field_validator("bid", "ask")
    @classmethod
    def validate_price(cls, value: object, info) -> float:
        result = _finite(value, name=info.field_name, minimum=0.0)
        if result <= 0:
            raise ValueError(f"{info.field_name} 必须大于 0")
        return result

    @model_validator(mode="after")
    def validate_quote(self) -> "ViewQuote":
        if self.received_at < self.timestamp:
            raise ValueError("quote received_at 不能早于 timestamp")
        if self.bid > self.ask:
            raise ValueError("quote bid 不能高于 ask")
        return self


class ViewEvent(OpportunityModel):
    """A scheduled or actual event that is known at the view cutoff."""

    event_id: str = Field(min_length=1, max_length=128)
    symbol: str | None = None
    event_type: str = Field(min_length=1, max_length=64)
    event_at: datetime | date
    status: Literal["scheduled", "actual"] = "scheduled"
    published_at: datetime | None = None
    available_at: datetime | None = None
    source: str = Field(min_length=1, max_length=128)
    source_revision: str = Field(min_length=1, max_length=128)
    content_sha256: str = Field(min_length=64, max_length=64)

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str | None) -> str | None:
        return normalise_symbol(value) if value else None

    @field_validator("published_at", "available_at")
    @classmethod
    def validate_optional_time(cls, value: datetime | None) -> datetime | None:
        return _aware(value) if value is not None else None

    @field_validator("event_at")
    @classmethod
    def validate_event_at(cls, value: datetime | date) -> datetime | date:
        if isinstance(value, datetime):
            return _aware(value)
        return value

    @field_validator("content_sha256")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        if len(value) != 64 or any(item not in "0123456789abcdef" for item in value.lower()):
            raise ValueError("event content_sha256 必须是十六进制 SHA-256")
        return value.lower()

    @model_validator(mode="after")
    def validate_event(self) -> "ViewEvent":
        if self.available_at is None:
            raise ValueError("event 必须提供可见时间 available_at")
        if self.status == "actual" and self.published_at is None:
            raise ValueError("actual event 必须提供 published_at")
        return self


class ViewEventRisk(OpportunityModel):
    status: Literal["clear", "near_term", "holding_window", "unknown", "not_applicable"]
    codes: list[str] = Field(default_factory=list)
    known_events: list[str] = Field(default_factory=list)
    horizon_sessions: int | None = Field(default=None, ge=0)


class OpportunityView(OpportunityModel):
    """The immutable payload consumed by a future read-only UI/API."""

    schema_version: Literal["urus.buy_opportunity.view.v1"] = (
        "urus.buy_opportunity.view.v1"
    )
    view_id: str = Field(min_length=1, max_length=160)
    dataset_id: str | None = None
    scan_id: str
    setup_id: str
    trigger_id: str | None = None
    symbol: str
    family_version: str
    rules_hash: str = Field(min_length=64, max_length=64)
    viewed_at: datetime
    knowledge_at: datetime
    market_as_of: datetime | date | None = None
    source_mode: Literal[
        "observed_pit", "historical_reconstruction", "daily_execution_proxy"
    ]
    data_source: Literal["real", "synthetic", "unknown"]
    price_basis: Literal["raw", "adjusted"]
    price_mapping_id: str | None = None
    quality: Literal["ok", "partial", "unavailable", "unknown"]
    quality_warnings: list[str] = Field(default_factory=list)
    freshness: Literal["fresh", "stale", "unknown"]
    status: Literal[
        "unavailable",
        "stale",
        "invalidated",
        "expired",
        "preparing",
        "triggered_unverified",
        "in_entry_zone",
        "extended",
        "waiting",
    ]
    upper_trigger: float
    invalidation_stop: float
    atr: float
    upper_limit: float
    reference_price: float | None = None
    reference_kind: Literal["daily_close", "intraday_quote", "none"] = "none"
    reference_quote: ViewQuote | None = None
    distance_to_trigger_atr: float | None = None
    condition: str
    invalidation: str
    event_risk: ViewEventRisk
    research_support: Literal["unknown", "research_only", "supported"] = "unknown"
    reason_codes: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceReference] = Field(default_factory=list)
    content_sha256: str = Field(min_length=64, max_length=64)

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        return normalise_symbol(value)

    @field_validator("viewed_at", "knowledge_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _aware(value)

    @field_validator("rules_hash", "content_sha256")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        if len(value) != 64 or any(item not in "0123456789abcdef" for item in value.lower()):
            raise ValueError("view hash 必须是十六进制 SHA-256")
        return value.lower()

    @field_validator("upper_trigger", "invalidation_stop", "atr", "upper_limit", "reference_price")
    @classmethod
    def validate_number(cls, value: object, info) -> float | None:
        if value is None:
            return None
        result = _finite(value, name=info.field_name, minimum=0.0)
        if result <= 0:
            raise ValueError(f"{info.field_name} 必须大于 0")
        return result

    @field_validator("distance_to_trigger_atr")
    @classmethod
    def validate_distance(cls, value: object) -> float | None:
        return _finite(value, name="distance_to_trigger_atr") if value is not None else None

    @model_validator(mode="after")
    def validate_view(self) -> "OpportunityView":
        if self.knowledge_at > self.viewed_at:
            raise ValueError("view knowledge_at 不能晚于 viewed_at")
        if not self.invalidation_stop < self.upper_trigger <= self.upper_limit:
            raise ValueError("view 的 S/L/U 价格关系非法")
        if self.atr <= 0:
            raise ValueError("view 的 ATR 必须大于 0")
        if self.status == "in_entry_zone":
            if self.reference_kind != "intraday_quote":
                raise ValueError("只有新鲜盘中 quote 才能标记 in_entry_zone")
            if self.freshness != "fresh":
                raise ValueError("stale quote 不能标记 in_entry_zone")
            if self.reference_price is None or not (
                self.upper_trigger <= self.reference_price <= self.upper_limit
            ):
                raise ValueError("in_entry_zone 的参考价必须位于 [L, U]")
            if self.quality not in {"ok", "partial"}:
                raise ValueError("不可用质量不能标记 in_entry_zone")
            if self.event_risk.status not in {"clear", "not_applicable"}:
                raise ValueError("事件风险未知或临近时不能标记 in_entry_zone")
        expected_hash = view_content_hash(self.model_dump(mode="python"))
        if self.content_sha256 != expected_hash:
            raise ValueError("view content_sha256 与内容不一致")
        return self

    @property
    def L(self) -> float:
        return self.upper_trigger

    @property
    def S(self) -> float:
        return self.invalidation_stop

    @property
    def A(self) -> float:
        return self.atr

    @property
    def U(self) -> float:
        return self.upper_limit

    @property
    def opportunity_id(self) -> str:
        """Stable economic identity, deliberately independent of view time."""

        return self.setup_id


def view_content_hash(payload: dict[str, Any]) -> str:
    """Hash a view payload after excluding its derived content hash."""

    def _jsonable(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return _jsonable(value.model_dump(mode="python"))
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Mapping):
            return {key: _jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [_jsonable(item) for item in value]
        return value

    return content_sha256(
        {
            key: _jsonable(value)
            for key, value in payload.items()
            if key != "content_sha256"
        }
    )


__all__ = [
    "OpportunityView",
    "ViewEvent",
    "ViewEventRisk",
    "ViewQuote",
    "VIEW_FRESHNESS",
    "VIEW_QUALITY",
    "VIEW_STATUSES",
    "view_content_hash",
]
