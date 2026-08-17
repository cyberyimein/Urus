from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ScheduleSlotSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    skip_ai_decision: bool = False


class ScheduleSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pre_market: ScheduleSlotSettings = Field(default_factory=ScheduleSlotSettings)
    pre_close: ScheduleSlotSettings = Field(
        default_factory=lambda: ScheduleSlotSettings(skip_ai_decision=True)
    )
    post_close_review: ScheduleSlotSettings = Field(default_factory=ScheduleSlotSettings)

    @model_validator(mode="after")
    def tail_collection_is_data_only(self) -> "ScheduleSettings":
        if not self.pre_close.skip_ai_decision:
            raise ValueError("尾盘采集固定只采集数据，不启动 AI 决策")
        return self


class RuntimeModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ai_decision_model: str = Field(min_length=1, max_length=160)
    anomalo_retrieval_agent: str = Field(min_length=1, max_length=160)
    input_cost_per_million: float = Field(default=0.0, ge=0, le=1_000_000)
    cached_input_cost_per_million: float = Field(default=0.0, ge=0, le=1_000_000)
    cache_write_cost_per_million: float = Field(default=0.0, ge=0, le=1_000_000)
    output_cost_per_million: float = Field(default=0.0, ge=0, le=1_000_000)

    @field_validator("ai_decision_model", "anomalo_retrieval_agent")
    @classmethod
    def compact_identifier(cls, value: str) -> str:
        value = value.strip()
        if not value or any(char in value for char in "\r\n"):
            raise ValueError("模型或 Agent 标识不能包含空行")
        return value


class RuntimeSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=0)
    schedule: ScheduleSettings
    models: RuntimeModelSettings


class RuntimeSettingsNotes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anomalo_model_control: Literal["preset_agent"] = "preset_agent"
    anomalo_model_note: str = "底层检索模型由 Anomalo 预设 Agent 配置；此处选择预设 Agent。"
    credentials_note: str = "API Key 等凭据仍由环境变量管理，不在页面保存。"


class RuntimeSettingsCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ai_decision_enabled: bool
    openrouter_configured: bool
    provider: Literal["openrouter"] = "openrouter"


class RuntimeSettingsResponse(RuntimeSettingsUpdate):
    revision: int = Field(ge=0)
    source: Literal["environment", "runtime"]
    updated_at: datetime | None = None
    notes: RuntimeSettingsNotes = Field(default_factory=RuntimeSettingsNotes)
    capabilities: RuntimeSettingsCapabilities
