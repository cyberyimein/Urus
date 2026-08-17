from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_settings
from app.core.config import Settings
from app.core.time import as_utc
from app.models.runtime_settings import RuntimeSettingsModel
from app.repositories.runtime_settings import (
    RuntimeSettingsRepository,
    apply_payload,
    environment_payload,
)
from app.schemas.settings import (
    RuntimeSettingsResponse,
    RuntimeSettingsUpdate,
    RuntimeSettingsCapabilities,
    ScheduleSettings,
    RuntimeModelSettings,
)


router = APIRouter()


def _response(
    settings: Settings, persisted: RuntimeSettingsModel | None
) -> RuntimeSettingsResponse:
    payload = environment_payload(settings)
    if persisted is not None:
        persisted_payload = persisted.payload if isinstance(persisted.payload, dict) else {}
        payload = {
            **payload,
            **persisted_payload,
            # Backfill fields absent from pre-pricing runtime rows with the
            # active environment values instead of showing/saving zeroes.
            "models": {
                **payload["models"],
                **(
                    persisted_payload.get("models", {})
                    if isinstance(persisted_payload.get("models"), dict)
                    else {}
                ),
            },
        }
    schedule = ScheduleSettings.model_validate(payload.get("schedule", {}))
    models = RuntimeModelSettings.model_validate(payload.get("models", {}))
    return RuntimeSettingsResponse(
        revision=persisted.revision if persisted is not None else 0,
        source="runtime" if persisted is not None else "environment",
        updated_at=as_utc(persisted.updated_at) if persisted is not None else None,
        schedule=schedule,
        models=models,
        capabilities=RuntimeSettingsCapabilities(
            ai_decision_enabled=settings.urus_agent_enabled,
            openrouter_configured=bool(settings.openrouter_api_key),
        ),
    )


@router.get("/settings", response_model=RuntimeSettingsResponse)
def get_runtime_settings(
    settings: Settings = Depends(get_settings), db: Session = Depends(get_db)
) -> RuntimeSettingsResponse:
    return _response(settings, RuntimeSettingsRepository(db).get())


@router.put("/settings", response_model=RuntimeSettingsResponse)
def update_runtime_settings(
    payload: RuntimeSettingsUpdate,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> RuntimeSettingsResponse:
    persisted = RuntimeSettingsRepository(db).save(payload)
    apply_payload(settings, persisted.payload)
    return _response(settings, persisted)
