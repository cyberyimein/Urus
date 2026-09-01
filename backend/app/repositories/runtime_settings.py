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
            "post_close_observation": {
                "enabled": settings.scheduled_post_close_enabled,
            },
        },
    }


def apply_payload(settings: Settings, payload: dict[str, Any]) -> None:
    schedule = ScheduleSettings.model_validate(payload.get("schedule", {}))
    settings.scheduled_post_close_enabled = schedule.post_close_observation.enabled


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
