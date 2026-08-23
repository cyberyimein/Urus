from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DailyDatasetCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_type: Literal["instrument", "group", "observation_run"] = "instrument"
    scope_id: str = Field(min_length=1, max_length=128)
    scope_version: int | None = Field(default=None, ge=1)
    symbols: list[str] = Field(min_length=1, max_length=500)
    benchmark_symbols: list[str] = Field(default_factory=list, max_length=20)
    trading_date: date | None = None
    cutoff_time: datetime | None = None

    @field_validator("scope_id")
    @classmethod
    def normalize_scope_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("scope_id 不能为空")
        return value

    @field_validator("symbols", "benchmark_symbols")
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            symbol = value.strip().upper()
            if symbol and symbol not in normalized:
                normalized.append(symbol)
        return normalized


class DailyEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: dict[str, Any]
    chart: dict[str, Any]
