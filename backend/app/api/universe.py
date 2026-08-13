from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_settings
from app.core.config import Settings
from app.core.errors import AppError
from app.repositories.universe import InstrumentUniverseRepository, derive_scopes
from app.schemas.universe import (
    UniverseImpactResponse,
    UniverseResponse,
    UniverseUpdate,
    UniverseValidationResponse,
)

router = APIRouter()


@router.get("/settings/universe", response_model=UniverseResponse)
def get_universe(settings: Settings = Depends(get_settings), db: Session = Depends(get_db)) -> UniverseResponse:
    repository = InstrumentUniverseRepository(db)
    return repository.response(repository.ensure_default(settings))


@router.get("/settings/universe/versions", response_model=list[UniverseResponse])
def list_universe_versions(
    limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)
) -> list[UniverseResponse]:
    repository = InstrumentUniverseRepository(db)
    return [repository.response(version) for version in repository.list_versions(limit)]


@router.post("/settings/universe/validate", response_model=UniverseValidationResponse)
def validate_universe(payload: UniverseUpdate) -> UniverseValidationResponse:
    derived = derive_scopes(payload.items)
    return UniverseValidationResponse(
        item_count=len(payload.items),
        enabled_count=sum(item.enabled for item in payload.items),
        derived=derived,
    )


@router.put("/settings/universe", response_model=UniverseResponse)
def update_universe(payload: UniverseUpdate, db: Session = Depends(get_db)) -> UniverseResponse:
    repository = InstrumentUniverseRepository(db)
    return repository.response(repository.save(payload))


@router.get("/settings/universe/symbols/{symbol}/impact", response_model=UniverseImpactResponse)
def get_symbol_impact(symbol: str, db: Session = Depends(get_db)) -> UniverseImpactResponse:
    repository = InstrumentUniverseRepository(db)
    current = repository.active()
    if current is None:
        raise AppError("标的设置尚未初始化", code="universe_not_initialized", status_code=404)
    response = repository.response(current)
    item = next((item for item in response.items if item.symbol == symbol.upper()), None)
    if item is None:
        raise AppError("找不到该标的", code="universe_symbol_not_found", status_code=404)
    return repository.impact(item)
