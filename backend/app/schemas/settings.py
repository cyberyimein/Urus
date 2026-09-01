from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScheduleSlotSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True


class ScheduleSettings(BaseModel):
    # Runtime settings are persisted across releases. Ignore removed schedule
    # slots when reading an existing row, while keeping new writes canonical.
    model_config = ConfigDict(extra="ignore")

    post_close_observation: ScheduleSlotSettings = Field(
        default_factory=ScheduleSlotSettings
    )

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_post_close_slot(cls, value: object) -> object:
        """Preserve the old post-close switch while reading persisted settings."""

        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        legacy = payload.pop("post_close_review", None)
        if "post_close_observation" not in payload and isinstance(legacy, Mapping):
            payload["post_close_observation"] = {
                "enabled": legacy.get("enabled", True),
            }
        return payload


class RuntimeSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=0)
    schedule: ScheduleSettings


class RuntimeSettingsResponse(RuntimeSettingsUpdate):
    revision: int = Field(ge=0)
    source: Literal["environment", "runtime"]
    updated_at: datetime | None = None
