from __future__ import annotations

from datetime import date, datetime
from math import isfinite
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.decision_harness.contracts import content_sha256, normalise_symbol


SOURCE_MODES = {
    "observed_pit",
    "historical_reconstruction",
    "daily_execution_proxy",
}
PRICE_BASES = {"raw", "adjusted"}
ASSET_TYPES = {"equity", "etf"}


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间戳必须包含时区")
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


class OpportunityModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class EvidenceReference(OpportunityModel):
    symbol: str
    bar_date: date
    source: str = Field(min_length=1, max_length=64)
    source_revision: str = Field(min_length=1, max_length=128)
    available_at: datetime
    content_sha256: str = Field(min_length=64, max_length=64)
    session_id: str | None = None

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return normalise_symbol(value)

    @field_validator("available_at")
    @classmethod
    def validate_available_at(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @field_validator("content_sha256")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        if any(character not in "0123456789abcdef" for character in value.lower()):
            raise ValueError("content_sha256 必须是十六进制 SHA-256")
        return value.lower()


class EvidenceBar(OpportunityModel):
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float = Field(ge=0)
    turnover: float | None = None
    turnover_rate: float | None = None
    adjustment: str = Field(default="QFQ", min_length=1, max_length=16)
    available_at: datetime
    collected_at: datetime
    source: str = Field(min_length=1, max_length=64)
    source_revision: str = Field(min_length=1, max_length=128)
    session_id: str | None = None
    session_open_at: datetime | None = None
    session_close_at: datetime | None = None

    @field_validator("open", "high", "low", "close")
    @classmethod
    def validate_price(cls, value: object, info) -> float:
        return _finite(value, name=info.field_name, minimum=0.0)

    @field_validator("volume", "turnover", "turnover_rate")
    @classmethod
    def validate_non_negative(cls, value: object, info) -> float | None:
        if value is None:
            return None
        return _finite(value, name=info.field_name, minimum=0.0)

    @field_validator("available_at", "collected_at", "session_open_at", "session_close_at")
    @classmethod
    def validate_timestamp(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value) if value is not None else None

    @model_validator(mode="after")
    def validate_ohlc(self) -> "EvidenceBar":
        if self.high < max(self.open, self.close):
            raise ValueError("high 必须不低于 open 和 close")
        if self.low > min(self.open, self.close):
            raise ValueError("low 必须不高于 open 和 close")
        if self.available_at < self.collected_at:
            raise ValueError("available_at 不能早于 collected_at")
        if self.session_open_at is not None and self.session_open_at.date() != self.date:
            raise ValueError("session_open_at 必须属于 bar date")
        if self.session_close_at is not None and self.session_close_at.date() != self.date:
            raise ValueError("session_close_at 必须属于 bar date")
        if (
            self.session_open_at is not None
            and self.session_close_at is not None
            and self.session_open_at >= self.session_close_at
        ):
            raise ValueError("session_open_at 必须早于 session_close_at")
        if self.session_close_at is not None and self.available_at < self.session_close_at:
            raise ValueError("完整日线的 available_at 不能早于 session_close_at")
        return self

    @property
    def content_sha256(self) -> str:
        return content_sha256(
            {
                "date": self.date.isoformat(),
                "open": self.open,
                "high": self.high,
                "low": self.low,
                "close": self.close,
                "volume": self.volume,
                "turnover": self.turnover,
                "turnover_rate": self.turnover_rate,
                "adjustment": self.adjustment,
                "source": self.source,
                "source_revision": self.source_revision,
                "session_id": self.session_id,
                "session_open_at": (
                    self.session_open_at.isoformat() if self.session_open_at is not None else None
                ),
                "session_close_at": (
                    self.session_close_at.isoformat() if self.session_close_at is not None else None
                ),
            }
        )


class FrozenEvidence(OpportunityModel):
    schema_version: Literal["urus.buy_opportunity.evidence.v1"] = (
        "urus.buy_opportunity.evidence.v1"
    )
    symbol: str
    asset_type: Literal["equity", "etf"]
    benchmark: str | None = None
    source_mode: Literal["observed_pit", "historical_reconstruction", "daily_execution_proxy"]
    data_source: Literal["real", "synthetic", "unknown"] = "unknown"
    price_basis: Literal["raw", "adjusted"] = "adjusted"
    price_mapping_id: str | None = None
    knowledge_at: datetime
    trading_date: date | None = None
    quality_status: Literal["ok", "partial", "unavailable", "failed"] = "ok"
    warnings: list[str] = Field(default_factory=list)
    bars: list[EvidenceBar] = Field(default_factory=list)
    source_manifest: list[EvidenceReference] = Field(default_factory=list)

    @field_validator("symbol", "benchmark")
    @classmethod
    def normalize_symbols(cls, value: str | None) -> str | None:
        return normalise_symbol(value) if value else None

    @field_validator("knowledge_at")
    @classmethod
    def validate_knowledge_at(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @model_validator(mode="after")
    def validate_evidence(self) -> "FrozenEvidence":
        if self.benchmark == self.symbol:
            raise ValueError("benchmark 不能与 symbol 相同")
        if any(bar.available_at > self.knowledge_at for bar in self.bars):
            raise ValueError("冻结证据不能包含 knowledge_at 之后才可见的日线")
        dates = [bar.date for bar in self.bars]
        if dates != sorted(set(dates)):
            raise ValueError("日线必须按日期升序且不能重复")
        adjustments = {bar.adjustment for bar in self.bars}
        if len(adjustments) > 1:
            raise ValueError("同一冻结证据不能混用 adjustment")
        if self.trading_date is not None and any(day > self.trading_date for day in dates):
            raise ValueError("日线不能晚于 trading_date")
        manifest_symbols = {item.symbol for item in self.source_manifest}
        if manifest_symbols and manifest_symbols != {self.symbol}:
            raise ValueError("source_manifest 必须只引用当前 symbol")
        if self.source_manifest:
            refs_by_date = {item.bar_date: item for item in self.source_manifest}
            if set(refs_by_date) != set(dates):
                raise ValueError("source_manifest 必须逐根引用冻结日线")
            for bar in self.bars:
                ref = refs_by_date[bar.date]
                if (
                    ref.source != bar.source
                    or ref.source_revision != bar.source_revision
                    or ref.available_at != bar.available_at
                    or ref.content_sha256 != bar.content_sha256
                ):
                    raise ValueError("source_manifest 与冻结日线内容不一致")
        return self

    @property
    def covered_through(self) -> date | None:
        return self.bars[-1].date if self.bars else None

    @property
    def input_bar_hash(self) -> str:
        return content_sha256([bar.model_dump(mode="json") for bar in self.bars])

    def chart_bars(self) -> list[dict[str, Any]]:
        return [
            {
                "date": bar.date.isoformat(),
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "turnover": bar.turnover,
                "turnover_rate": bar.turnover_rate,
                "adjustment": bar.adjustment,
            }
            for bar in self.bars
        ]


class ScanRecord(OpportunityModel):
    schema_version: Literal["urus.buy_opportunity.scan.v1"] = "urus.buy_opportunity.scan.v1"
    scan_id: str = Field(min_length=1, max_length=128)
    pool_version: str = Field(min_length=1, max_length=128)
    knowledge_at: datetime
    symbol: str
    asset_type: Literal["market", "equity", "etf"]
    source_mode: Literal["observed_pit", "historical_reconstruction", "daily_execution_proxy"]
    price_basis: Literal["raw", "adjusted"]
    adjustment: str = Field(min_length=1, max_length=16)
    status: Literal["eligible", "rejected", "coverage_miss", "unavailable"]
    bar_count: int = Field(ge=0)
    covered_through: date | None = None
    source_manifest: list[EvidenceReference] = Field(default_factory=list)
    eligibility_by_dimension: dict[str, bool | str | None] = Field(default_factory=dict)
    features: dict[str, Any] = Field(default_factory=dict)
    rejection_codes: list[str] = Field(default_factory=list)
    setup_id: str | None = None
    content_sha256: str = Field(min_length=64, max_length=64)

    @field_validator("knowledge_at")
    @classmethod
    def validate_knowledge_at(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        return normalise_symbol(value)

    @field_validator("content_sha256")
    @classmethod
    def validate_content_hash(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value.lower()):
            raise ValueError("content_sha256 必须是十六进制 SHA-256")
        return value.lower()

    @model_validator(mode="after")
    def validate_status(self) -> "ScanRecord":
        if self.status == "eligible" and self.rejection_codes:
            raise ValueError("eligible scan 不能包含 rejection_codes")
        if self.status != "eligible" and not self.rejection_codes:
            raise ValueError("非 eligible scan 必须保留拒绝或覆盖原因")
        if self.status == "eligible" and not self.setup_id:
            raise ValueError("eligible scan 必须关联 setup_id")
        return self


class SetupRecord(OpportunityModel):
    schema_version: Literal["urus.buy_opportunity.setup.v1"] = "urus.buy_opportunity.setup.v1"
    setup_id: str = Field(min_length=1, max_length=128)
    fingerprint: str = Field(min_length=64, max_length=64)
    stream: str = Field(min_length=1, max_length=64)
    symbol: str
    family_version: str = Field(min_length=1, max_length=64)
    parameter_hash: str = Field(min_length=64, max_length=64)
    benchmark_mapping: str | None = None
    trigger_timeframe: Literal["daily", "15m", "1m"] = "15m"
    anchor_pivot_bar_id: str = Field(min_length=1, max_length=128)
    base_start_bar_id: str = Field(min_length=1, max_length=128)
    observed_at: date
    knowledge_at: datetime | None = None
    valid_from: date
    valid_sessions: int = Field(ge=1, le=30)
    direction: Literal["long"] = "long"
    upper_trigger: float
    invalidation_stop: float
    atr: float
    upper_limit: float
    price_basis: Literal["raw", "adjusted"] = "adjusted"
    price_mapping_id: str | None = None
    status: Literal["armed", "resolved", "invalidated", "expired"] = "armed"
    origin_scan_id: str = Field(min_length=1, max_length=128)
    content_sha256: str = Field(min_length=64, max_length=64)

    @field_validator("symbol", "benchmark_mapping")
    @classmethod
    def validate_symbols(cls, value: str | None) -> str | None:
        return normalise_symbol(value) if value else None

    @field_validator("knowledge_at")
    @classmethod
    def validate_knowledge_at(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value) if value is not None else None

    @field_validator("upper_trigger", "invalidation_stop", "atr", "upper_limit")
    @classmethod
    def validate_prices(cls, value: object, info) -> float:
        return _finite(value, name=info.field_name, minimum=0.0)

    @model_validator(mode="after")
    def validate_structure(self) -> "SetupRecord":
        if not self.invalidation_stop < self.upper_trigger <= self.upper_limit:
            raise ValueError("setup 必须满足 invalidation_stop < upper_trigger <= upper_limit")
        if self.atr <= 0:
            raise ValueError("setup 的 ATR 必须大于 0")
        return self


class TriggerRecord(OpportunityModel):
    schema_version: Literal["urus.buy_opportunity.trigger.v1"] = "urus.buy_opportunity.trigger.v1"
    trigger_id: str = Field(min_length=1, max_length=128)
    setup_id: str = Field(min_length=1, max_length=128)
    symbol: str
    kind: Literal["continuous_cross", "open_gap_cross", "late_discovery", "cross_unobserved"]
    observed_bar_id: str | None = None
    occurred_at: datetime | None = None
    known_at: datetime
    previous_close: float | None = None
    current_close: float | None = None
    extension_status: Literal["within_limit", "missed_extension", "unknown"] = "unknown"
    content_sha256: str = Field(min_length=64, max_length=64)

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        return normalise_symbol(value)

    @field_validator("known_at", "occurred_at")
    @classmethod
    def validate_timestamps(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value) if value is not None else None

    @field_validator("previous_close", "current_close")
    @classmethod
    def validate_optional_prices(cls, value: object, info) -> float | None:
        return _finite(value, name=info.field_name, minimum=0.0) if value is not None else None

    @model_validator(mode="after")
    def validate_temporal_order(self) -> "TriggerRecord":
        if self.occurred_at is not None and self.known_at < self.occurred_at:
            raise ValueError("trigger known_at 不能早于 occurred_at")
        return self


class TriggerEvaluation(OpportunityModel):
    schema_version: Literal["urus.buy_opportunity.trigger_evaluation.v1"] = (
        "urus.buy_opportunity.trigger_evaluation.v1"
    )
    status: Literal["triggered", "invalidated", "no_trigger", "unobserved", "expired"]
    setup_id: str
    observed_at: datetime
    reason_code: str | None = None
    trigger: TriggerRecord | None = None

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        return _require_aware(value)


class EntryIntentRecord(OpportunityModel):
    schema_version: Literal["urus.buy_opportunity.entry_intent.v1"] = (
        "urus.buy_opportunity.entry_intent.v1"
    )
    intent_id: str = Field(min_length=1, max_length=128)
    trigger_id: str = Field(min_length=1, max_length=128)
    symbol: str
    source_mode: Literal["observed_pit", "historical_reconstruction", "daily_execution_proxy"] = (
        "historical_reconstruction"
    )
    price_basis: Literal["raw", "adjusted"] = "adjusted"
    price_mapping_id: str | None = None
    user_policy: str = Field(min_length=1, max_length=64)
    trigger_occurred_at: datetime | None = None
    trigger_known_at: datetime
    created_at: datetime
    delivered_at: datetime | None = None
    ready_at: datetime | None = None
    recheck_at: datetime | None = None
    sent_at: datetime | None = None
    expires_at: datetime
    q0_quote_id: str | None = None
    q0_timestamp: datetime | None = None
    q0_received_at: datetime | None = None
    q0_known_at: datetime | None = None
    q0_bid: float | None = None
    q0_ask: float | None = None
    limit_price: float
    planned_quantity: float = Field(ge=0)
    risk_budget: float = Field(default=0, ge=0)
    planned_risk: float = Field(default=0, ge=0)
    status: Literal["pending", "ready", "submitted", "filled", "cancelled", "expired"]
    reason_code: str | None = None
    content_sha256: str = Field(min_length=64, max_length=64)

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        return normalise_symbol(value)

    @field_validator(
        "created_at",
        "trigger_occurred_at",
        "trigger_known_at",
        "delivered_at",
        "ready_at",
        "recheck_at",
        "sent_at",
        "expires_at",
        "q0_timestamp",
        "q0_received_at",
        "q0_known_at",
    )
    @classmethod
    def validate_timestamps(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value) if value is not None else None

    @field_validator("q0_bid", "q0_ask", "limit_price")
    @classmethod
    def validate_prices(cls, value: object, info) -> float | None:
        return _finite(value, name=info.field_name, minimum=0.0) if value is not None else None

    @model_validator(mode="after")
    def validate_intent(self) -> "EntryIntentRecord":
        if self.expires_at <= self.created_at:
            raise ValueError("entry intent 必须在 created_at 之后过期")
        if self.trigger_occurred_at is not None and self.trigger_known_at < self.trigger_occurred_at:
            raise ValueError("trigger_known_at 不能早于 trigger_occurred_at")
        if self.created_at < self.trigger_known_at:
            raise ValueError("entry intent created_at 不能早于 trigger_known_at")
        if self.delivered_at is not None and self.delivered_at < self.created_at:
            raise ValueError("delivered_at 不能早于 created_at")
        if self.ready_at is not None:
            if self.delivered_at is None or self.ready_at < self.delivered_at:
                raise ValueError("ready_at 必须在 delivered_at 之后")
        if self.q0_received_at is not None and self.ready_at is not None:
            if self.q0_received_at < self.ready_at:
                raise ValueError("Q0 received_at 不能早于 ready_at")
        if self.q0_known_at is not None and self.q0_received_at is not None:
            if self.q0_known_at < self.q0_received_at:
                raise ValueError("q0_known_at 不能早于 q0_received_at")
        if self.q0_bid is not None and self.q0_ask is not None and self.q0_bid > self.q0_ask:
            raise ValueError("q0_bid 不能高于 q0_ask")
        if self.status in {"submitted", "filled"} and self.planned_quantity <= 0:
            raise ValueError("已发送或成交的 entry intent 必须冻结正数量")
        return self


class ExecutionRecord(OpportunityModel):
    schema_version: Literal["urus.buy_opportunity.execution.v1"] = (
        "urus.buy_opportunity.execution.v1"
    )
    execution_id: str = Field(min_length=1, max_length=128)
    intent_id: str = Field(min_length=1, max_length=128)
    policy_hash: str = Field(min_length=64, max_length=64)
    source_mode: Literal["observed_pit", "historical_reconstruction", "daily_execution_proxy"]
    price_mapping_id: str | None = None
    fill_at: datetime | None = None
    fill_price: float | None = None
    fill_quantity: float = Field(ge=0)
    requested_quantity: float | None = None
    fill_status: Literal["none", "full", "partial", "ambiguous"] = "none"
    requested_price: float | None = None
    slippage: float = 0.0
    fees: float = 0.0
    gross_pnl: float | None = None
    net_pnl: float | None = None
    cost: float | None = None
    planned_risk: float | None = None
    status: Literal["filled", "not_filled", "ambiguous", "cancelled"]
    reason_code: str | None = None
    content_sha256: str = Field(min_length=64, max_length=64)

    @field_validator("fill_at")
    @classmethod
    def validate_fill_at(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value) if value is not None else None

    @field_validator(
        "fill_price",
        "requested_quantity",
        "requested_price",
        "slippage",
        "fees",
        "gross_pnl",
        "net_pnl",
        "cost",
        "planned_risk",
    )
    @classmethod
    def validate_optional_numbers(cls, value: object, info) -> float | None:
        return _finite(value, name=info.field_name) if value is not None else None

    @model_validator(mode="after")
    def validate_execution(self) -> "ExecutionRecord":
        if self.slippage < 0 or self.fees < 0:
            raise ValueError("execution 的 slippage 和 fees 不能为负")
        if self.status == "filled" and (self.fill_at is None or self.fill_price is None):
            raise ValueError("filled execution 必须包含 fill_at 和 fill_price")
        if self.status == "filled" and self.fill_status not in {"full", "partial"}:
            raise ValueError("filled execution 必须明确 full 或 partial")
        if self.fill_status == "partial" and (
            self.requested_quantity is None
            or not 0 < self.fill_quantity < self.requested_quantity
        ):
            raise ValueError("partial execution 必须保留 requested_quantity 和部分成交数量")
        if self.fill_status == "full" and self.requested_quantity is not None:
            if self.fill_quantity != self.requested_quantity:
                raise ValueError("full execution 的 fill_quantity 必须等于 requested_quantity")
        if self.status != "filled" and self.fill_quantity != 0:
            raise ValueError("未成交 execution 的 fill_quantity 必须为 0")
        if self.status == "ambiguous" and self.fill_status != "ambiguous":
            raise ValueError("ambiguous execution 必须明确 fill_status=ambiguous")
        return self


class ExitRecord(OpportunityModel):
    schema_version: Literal["urus.buy_opportunity.exit.v1"] = "urus.buy_opportunity.exit.v1"
    exit_id: str = Field(min_length=1, max_length=128)
    execution_id: str = Field(min_length=1, max_length=128)
    status: Literal[
        "stop",
        "gap_stop",
        "structure_failed",
        "time_exit",
        "pending",
        "censored",
        "ambiguous",
    ]
    exit_at: datetime | None = None
    exit_price: float | None = None
    quantity: float = Field(ge=0)
    gross_pnl: float | None = None
    net_pnl: float | None = None
    reason_code: str
    content_sha256: str = Field(min_length=64, max_length=64)

    @field_validator("exit_at")
    @classmethod
    def validate_exit_at(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value) if value is not None else None

    @field_validator("exit_price", "gross_pnl", "net_pnl")
    @classmethod
    def validate_exit_numbers(cls, value: object, info) -> float | None:
        return _finite(value, name=info.field_name) if value is not None else None


class AssessmentRecord(OpportunityModel):
    schema_version: Literal["urus.buy_opportunity.assessment.v1"] = (
        "urus.buy_opportunity.assessment.v1"
    )
    assessment_id: str = Field(min_length=1, max_length=128)
    manifest_hash: str = Field(min_length=64, max_length=64)
    primary_policy: str = Field(min_length=1, max_length=64)
    metrics: dict[str, Any] = Field(default_factory=dict)
    baseline: dict[str, Any] = Field(default_factory=dict)
    applicability: Literal["unknown", "research_only", "supported"] = "unknown"
    limitations: list[str] = Field(default_factory=list)
    content_sha256: str = Field(min_length=64, max_length=64)
