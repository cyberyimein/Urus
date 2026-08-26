from pathlib import Path

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_settings
from app.core.config import Settings
from app.core.errors import AppError
from app.integrations.moomoo import OpenDMarketAdapter
from app.repositories.universe import InstrumentUniverseRepository, derive_scopes
from app.services.history_quota import HistoryCapacityService
from app.schemas.universe import (
    HistoryCollectionProjectionResponse,
    UniverseImpactResponse,
    UniverseCapacityPlanResponse,
    UniverseResponse,
    UniverseUpdate,
    UniverseValidationResponse,
)

router = APIRouter()


def _capacity_plan(
    db: Session,
    settings: Settings,
    *,
    items,
    universe_version_id: str | None = None,
    universe_content_sha256: str = "",
) -> dict[str, object]:
    adapter = None
    reader = None
    if settings.moomoo_enabled:
        adapter = OpenDMarketAdapter(
            settings.moomoo_host,
            settings.moomoo_port,
            market_timezone=settings.market_timezone,
            history_days=max(settings.moomoo_history_days, settings.daily_min_history_bars),
            sdk_home=Path(settings.moomoo_sdk_home),
            market_symbols=[item.strip() for item in settings.moomoo_market_symbols.split(",") if item.strip()],
            history_request_interval_seconds=settings.moomoo_history_request_interval_seconds,
            snapshot_request_interval_seconds=settings.moomoo_snapshot_request_interval_seconds,
        )
        reader = adapter.quota_snapshot
    try:
        service = HistoryCapacityService(db, settings)
        plan = service.build_plan(
            items,
            universe_version_id=universe_version_id,
            universe_content_sha256=universe_content_sha256,
            reader=reader,
        )
        db.commit()
        return plan
    finally:
        if adapter is not None:
            adapter.close()


def _active_response(
    db: Session,
    settings: Settings,
    version,
) -> UniverseResponse:
    response = InstrumentUniverseRepository.response(version)
    projection = HistoryCapacityService(db, settings).projection()
    response.capacity = dict(projection.get("capacity") or {})
    response.collection_states = dict(projection.get("states") or {})
    return response


@router.get("/settings/universe", response_model=UniverseResponse)
def get_universe(settings: Settings = Depends(get_settings), db: Session = Depends(get_db)) -> UniverseResponse:
    repository = InstrumentUniverseRepository(db)
    return _active_response(db, settings, repository.ensure_default(settings))


@router.get("/settings/universe/versions", response_model=list[UniverseResponse])
def list_universe_versions(
    limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)
) -> list[UniverseResponse]:
    repository = InstrumentUniverseRepository(db)
    return [repository.response(version) for version in repository.list_versions(limit)]


@router.post("/settings/universe/validate", response_model=UniverseValidationResponse)
def validate_universe(
    payload: UniverseUpdate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UniverseValidationResponse:
    derived = derive_scopes(payload.items)
    plan = _capacity_plan(
        db,
        settings,
        items=payload.items,
        universe_content_sha256=InstrumentUniverseRepository.content_digest(payload.items),
    )
    return UniverseValidationResponse(
        item_count=len(payload.items),
        enabled_count=sum(item.enabled for item in payload.items),
        derived=derived,
        capacity=dict(plan.get("quota") or {}),
        collection_states={
            str(item.get("symbol")): item
            for item in plan.get("symbols", [])
            if isinstance(item, dict) and item.get("symbol")
        },
    )


@router.post(
    "/settings/universe/capacity-plan",
    response_model=UniverseCapacityPlanResponse,
)
def create_capacity_plan(
    payload: UniverseUpdate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UniverseCapacityPlanResponse:
    plan = _capacity_plan(
        db,
        settings,
        items=payload.items,
        universe_content_sha256=InstrumentUniverseRepository.content_digest(payload.items),
    )
    return UniverseCapacityPlanResponse(**plan)


@router.get(
    "/settings/universe/history-status",
    response_model=HistoryCollectionProjectionResponse,
)
def get_history_status(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HistoryCollectionProjectionResponse:
    return HistoryCollectionProjectionResponse(**HistoryCapacityService(db, settings).projection())


@router.post(
    "/settings/universe/history-capacity/refresh",
    response_model=HistoryCollectionProjectionResponse,
)
def refresh_history_capacity(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HistoryCollectionProjectionResponse:
    repository = InstrumentUniverseRepository(db)
    version = repository.ensure_default(settings)
    response = repository.response(version)
    plan = _capacity_plan(
        db,
        settings,
        items=response.items,
        universe_version_id=version.id,
        universe_content_sha256=version.content_sha256,
    )
    HistoryCapacityService(db, settings).apply_plan(plan, universe_version_id=version.id)
    db.commit()
    return HistoryCollectionProjectionResponse(**HistoryCapacityService(db, settings).projection())


@router.put("/settings/universe", response_model=UniverseResponse)
def update_universe(
    payload: UniverseUpdate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UniverseResponse:
    repository = InstrumentUniverseRepository(db)
    version = repository.save(payload)
    plan = _capacity_plan(
        db,
        settings,
        items=payload.items,
        universe_version_id=version.id,
        universe_content_sha256=version.content_sha256,
    )
    HistoryCapacityService(db, settings).apply_plan(
        plan,
        universe_version_id=version.id,
    )
    db.commit()
    return _active_response(db, settings, version)


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
