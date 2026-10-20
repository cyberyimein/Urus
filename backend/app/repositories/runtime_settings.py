from __future__ import annotations

from typing import Any

from sqlalchemy import update as sql_update
from sqlalchemy.exc import IntegrityError
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
        if current is not None and isinstance(current.payload, dict):
            # Feature-specific settings share this singleton JSON row. A
            # schedule form write must preserve settings owned by other APIs.
            preserved = {
                key: value
                for key, value in current.payload.items()
                if key not in payload
            }
            payload = {**preserved, **payload}
        if current is None:
            current = RuntimeSettingsModel(
                id=1,
                payload=payload,
                revision=1,
                updated_at=utc_now(),
            )
            self.session.add(current)
            try:
                self.session.commit()
            except IntegrityError as exc:
                self.session.rollback()
                latest = self.get()
                latest_revision = latest.revision if latest is not None else 0
                raise AppError(
                    "设置已被其他页面更新，请刷新后再保存。",
                    code="settings_revision_conflict",
                    status_code=409,
                    details={"current_revision": latest_revision},
                ) from exc
        else:
            result = self.session.execute(
                sql_update(RuntimeSettingsModel)
                .where(
                    RuntimeSettingsModel.id == 1,
                    RuntimeSettingsModel.revision == current_revision,
                )
                .values(
                    payload=payload,
                    revision=current_revision + 1,
                    updated_at=utc_now(),
                )
            )
            if result.rowcount != 1:
                self.session.rollback()
                self.session.expire_all()
                latest = self.get()
                latest_revision = latest.revision if latest is not None else 0
                raise AppError(
                    "设置已被其他页面更新，请刷新后再保存。",
                    code="settings_revision_conflict",
                    status_code=409,
                    details={"current_revision": latest_revision},
                )
            self.session.commit()
        self.session.expire_all()
        current = self.get()
        if current is None:
            raise RuntimeError("runtime settings row missing after save")
        self.session.refresh(current)
        return current
