from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")


class UniverseRoles(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market_benchmark: bool = False
    equity_watchlist: bool = False
    cta_proxy: bool = False
    options_collection: bool = False
    event_tracking: bool = False
    ai_candidate: bool = False


class UniverseBenchmarks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_strength: str | None = None
    cta_proxy_for: str | None = None

    @field_validator("relative_strength")
    @classmethod
    def normalize_benchmark(cls, value: str | None) -> str | None:
        return value.strip().upper() if value and value.strip() else None

    @field_validator("cta_proxy_for")
    @classmethod
    def normalize_proxy(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None


class UniverseCollection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote: bool = True
    daily_history: bool = True
    # Options are collected serially by the daily workflow. Enabling the intent
    # does not make an OpenD call during Universe validation or save.
    options: bool = True


class InstrumentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=16)
    display_name: str = Field(min_length=1, max_length=128)
    asset_type: Literal["market", "etf", "equity"]
    # ``theme`` remains the primary/backwards-compatible label. ``themes`` is
    # the canonical collection so a symbol can belong to several cross-cutting
    # concepts (for example, AI infrastructure and semiconductors).
    theme: str = Field(default="", max_length=64)
    themes: list[str] = Field(default_factory=list, max_length=12)
    enabled: bool = True
    roles: UniverseRoles = Field(default_factory=UniverseRoles)
    benchmarks: UniverseBenchmarks = Field(default_factory=UniverseBenchmarks)
    collection: UniverseCollection = Field(default_factory=UniverseCollection)
    notes: str = Field(default="", max_length=1000)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        symbol = value.strip().upper()
        if not SYMBOL_PATTERN.fullmatch(symbol):
            raise ValueError("Symbol 只能包含大写字母、数字、点和连字符")
        return symbol

    @field_validator("display_name")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("字段不能为空")
        return value

    @field_validator("theme", mode="before")
    @classmethod
    def strip_primary_theme(cls, value: str | None) -> str:
        return str(value or "").strip()

    @field_validator("themes", mode="before")
    @classmethod
    def normalize_themes(cls, value: object) -> list[str]:
        if value is None or value == "":
            return []
        values = [value] if isinstance(value, str) else value
        if not isinstance(values, (list, tuple, set)):
            raise ValueError("主题必须是字符串列表")
        normalized: list[str] = []
        for entry in values:
            theme = str(entry).strip()
            if not theme:
                continue
            if len(theme) > 64:
                raise ValueError("单个主题不能超过 64 个字符")
            if theme not in normalized:
                normalized.append(theme)
        return normalized

    @model_validator(mode="after")
    def validate_roles(self) -> "InstrumentConfig":
        # A legacy payload may only contain ``theme``. When the canonical
        # ``themes`` field is explicitly supplied, it is authoritative even
        # if a response round-trip carries a stale legacy ``theme`` value.
        themes = list(self.themes)
        explicit_themes = "themes" in self.model_fields_set
        if not explicit_themes and not themes and self.theme:
            themes = [self.theme]
        if not themes:
            raise ValueError("至少需要填写一个主题")
        self.themes = themes
        self.theme = themes[0]
        if self.roles.cta_proxy and not self.benchmarks.cta_proxy_for:
            raise ValueError("CTA proxy 必须填写代表的期货或风险因子")
        if self.roles.options_collection and not self.collection.options:
            raise ValueError("期权采集角色必须同时启用 collection.options")
        if self.roles.market_benchmark and not self.collection.quote:
            raise ValueError("市场基准必须采集报价")
        return self


class UniverseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_version_id: str | None = None
    items: list[InstrumentConfig] = Field(min_length=1, max_length=250)

    @model_validator(mode="after")
    def validate_universe(self) -> "UniverseUpdate":
        symbols = [item.symbol for item in self.items]
        if len(symbols) != len(set(symbols)):
            raise ValueError("Universe 中存在重复 Symbol")
        enabled = [item for item in self.items if item.enabled]
        qqq = next((item for item in enabled if item.symbol == "QQQ"), None)
        if qqq is None or not qqq.collection.daily_history:
            raise ValueError("当前相对强弱算法要求启用 QQQ 并采集日线")
        if not any(item.roles.market_benchmark for item in enabled):
            raise ValueError("至少需要一个启用的市场基准")
        if not any(item.roles.ai_candidate for item in enabled):
            raise ValueError("至少需要一个启用的 AI 候选标的")
        enabled_symbols = {item.symbol for item in enabled}
        missing_benchmarks = sorted({
            item.benchmarks.relative_strength
            for item in enabled
            if item.benchmarks.relative_strength
            and item.benchmarks.relative_strength not in enabled_symbols
        })
        if missing_benchmarks:
            raise ValueError("相对强弱基准未启用：" + ", ".join(missing_benchmarks))
        return self


class UniverseDerivedScopes(BaseModel):
    market_symbols: list[str]
    instrument_symbols: list[str]
    cta_proxy_symbols: list[str]
    option_symbols: list[str]
    event_symbols: list[str]
    ai_candidate_symbols: list[str]
    # History collection is intentionally independent from the protected
    # indicator-recommendation role.  Keep a default for legacy response
    # payloads and old tests that construct this schema directly.
    history_symbols: list[str] = Field(default_factory=list)


class UniverseResponse(BaseModel):
    version_id: str
    revision: int
    content_sha256: str
    source: str
    created_at: datetime
    items: list[InstrumentConfig]
    derived: UniverseDerivedScopes
    capacity: dict[str, object] = Field(default_factory=dict)
    collection_states: dict[str, dict[str, object]] = Field(default_factory=dict)


class UniverseValidationResponse(BaseModel):
    valid: bool = True
    item_count: int
    enabled_count: int
    derived: UniverseDerivedScopes
    capacity: dict[str, object] = Field(default_factory=dict)
    collection_states: dict[str, dict[str, object]] = Field(default_factory=dict)


class UniverseCapacityPlanResponse(BaseModel):
    schema_version: str = "urus.market_data_capacity_plan.v1"
    plan_id: str
    provider: str = "moomoo_openapi"
    universe_content_sha256: str
    captured_at: datetime
    expires_at: datetime | None = None
    quota: dict[str, object] = Field(default_factory=dict)
    summary: dict[str, int] = Field(default_factory=dict)
    symbols: list[dict[str, object]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class HistoryCollectionProjectionResponse(BaseModel):
    provider: str = "moomoo_openapi"
    captured_at: datetime | None = None
    capacity: dict[str, object] = Field(default_factory=dict)
    states: dict[str, dict[str, object]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class UniverseImpactResponse(BaseModel):
    symbol: str
    effects: list[str]
