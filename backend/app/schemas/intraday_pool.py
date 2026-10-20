from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.universe import SYMBOL_PATTERN


class IntradayPoolUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_revision: int = Field(ge=0)
    selected_symbols: list[str] = Field(default_factory=list, max_length=250)

    @field_validator("selected_symbols")
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for raw in values:
            symbol = str(raw).strip().upper()
            if not symbol:
                continue
            if not SYMBOL_PATTERN.fullmatch(symbol):
                raise ValueError(f"无效的日内标的代码：{symbol}")
            if symbol not in result:
                result.append(symbol)
        return result


class IntradayPoolMember(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    display_name: str
    asset_type: str
    themes: list[str] = Field(default_factory=list)
    display_group: str
    fixed: bool = False


class IntradayPoolVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=0)
    source: Literal["legacy_capture", "runtime"]
    effective_date: date | None = None
    selected_symbols: list[str]
    member_symbols: list[str]
    display_group_by_symbol: dict[str, str]
    source_capture_id: str | None = None


class IntradayPoolSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["configured", "pending_only", "unconfigured"]
    revision: int = Field(ge=0)
    benchmark_symbols: list[str]
    active: IntradayPoolVersion | None = None
    pending: IntradayPoolVersion | None = None
    available_symbols: list[IntradayPoolMember] = Field(default_factory=list)
    warning: str | None = None


class IntradayCollectionPlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["urus.intraday.collection_plan.v1"]
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["configured"]
    trading_date: date
    pool_revision: int = Field(ge=0)
    selected_symbols: list[str]
    benchmark_symbols: list[str]
    member_symbols: list[str]
    quote_symbols: list[str]
    minute_symbols: list[str]
    display_group_by_symbol: dict[str, str]
    asset_type_by_symbol: dict[str, str] = Field(default_factory=dict)
