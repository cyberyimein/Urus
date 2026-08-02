from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_settings
from app.core.config import Settings
from app.schemas.read_model import HealthResponse, VersionResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health(settings: Settings = Depends(get_settings), db: Session = Depends(get_db)) -> HealthResponse:
    db.execute(text("SELECT 1"))
    return HealthResponse(status="ok", environment=settings.app_env, database="ok")


@router.get("/version", response_model=VersionResponse)
def version(settings: Settings = Depends(get_settings)) -> VersionResponse:
    return VersionResponse(
        app_name=settings.app_name,
        app_version=settings.app_version,
        api_schema_version=settings.api_schema_version,
    )

