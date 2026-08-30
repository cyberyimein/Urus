from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.core.time import utc_now
from app.models.runtime_settings import RuntimeSettingsModel
from app.schemas.settings import RuntimeSettingsUpdate, ScheduleSettings


def environment_payload(settings: Settings) -> dict[str, Any]:
    return {
        "schedule": {
            "pre_market": {
                "enabled": settings.scheduled_pre_market_enabled,
                "skip_ai_decision": settings.scheduled_pre_market_skip_ai_decision,
            },
            "pre_close": {
                "enabled": settings.scheduled_pre_close_enabled,
                # Tail collection is a hard data-only boundary.
                "skip_ai_decision": True,
            },
            "post_close_observation": {
                "enabled": settings.scheduled_post_close_enabled,
                # Observation Run is deterministic-only; post_close_review is
                # reserved for the separate AI review session.
                "skip_ai_decision": True,
            },
        },
        "models": {
            "ai_decision_model": settings.urus_agent_model,
            "anomalo_retrieval_agent": settings.anomalo_scheduled_agent,
            "input_cost_per_million": settings.urus_agent_input_cost_per_million,
            "cached_input_cost_per_million": (
                settings.urus_agent_cached_input_cost_per_million
            ),
            "cache_write_cost_per_million": (
                settings.urus_agent_cache_write_cost_per_million
            ),
            "output_cost_per_million": settings.urus_agent_output_cost_per_million,
        },
    }


def apply_payload(settings: Settings, payload: dict[str, Any]) -> None:
    schedule = ScheduleSettings.model_validate(payload.get("schedule", {}))
    models = payload.get("models", {})
    settings.scheduled_pre_market_enabled = schedule.pre_market.enabled
    settings.scheduled_pre_market_skip_ai_decision = schedule.pre_market.skip_ai_decision
    settings.scheduled_pre_close_enabled = schedule.pre_close.enabled
    settings.scheduled_pre_close_skip_ai_decision = True
    settings.scheduled_post_close_enabled = schedule.post_close_observation.enabled
    settings.scheduled_post_close_skip_ai_decision = True
    if isinstance(models, dict):
        if isinstance(models.get("ai_decision_model"), str):
            settings.urus_agent_model = models["ai_decision_model"]
        if isinstance(models.get("anomalo_retrieval_agent"), str):
            settings.anomalo_scheduled_agent = models["anomalo_retrieval_agent"]
        # Runtime rows created before model pricing was introduced do not have
        # these keys. Preserve environment prices until the user explicitly
        # saves pricing values through Settings.
        price_fields = {
            "input_cost_per_million": "urus_agent_input_cost_per_million",
            "cached_input_cost_per_million": (
                "urus_agent_cached_input_cost_per_million"
            ),
            "cache_write_cost_per_million": (
                "urus_agent_cache_write_cost_per_million"
            ),
            "output_cost_per_million": "urus_agent_output_cost_per_million",
        }
        for payload_key, setting_name in price_fields.items():
            if payload_key in models:
                setattr(settings, setting_name, float(models[payload_key] or 0.0))


class RuntimeSettingsRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self) -> RuntimeSettingsModel | None:
        return self.session.get(RuntimeSettingsModel, 1)

    def save(self, update: RuntimeSettingsUpdate) -> RuntimeSettingsModel:
        current = self.get()
        current_revision = current.revision if current is not None else 0
        if update.revision != current_revision:
            raise AppError(
                "设置已被其他页面更新，请刷新后再保存。",
                code="settings_revision_conflict",
                status_code=409,
                details={"current_revision": current_revision},
            )

        payload = update.model_dump(mode="json", exclude={"revision"})
        if current is None:
            current = RuntimeSettingsModel(
                id=1,
                payload=payload,
                revision=1,
                updated_at=utc_now(),
            )
            self.session.add(current)
        else:
            current.payload = payload
            current.revision += 1
            current.updated_at = utc_now()
        self.session.commit()
        self.session.refresh(current)
        return current
