from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_settings
from app.core.config import Settings
from app.schemas.read_model import (
    FrontendReadModel,
    RunCreateRequest,
    RunCreateResponse,
    RunDetailResponse,
    RunListItem,
    SnapshotResponse,
    WatchlistResponse,
)
from app.services import RunService

router = APIRouter()


@router.post("/runs", response_model=RunCreateResponse, status_code=201)
def create_run(
    request: RunCreateRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RunCreateResponse:
    return RunService(db, settings).create_run(request)


@router.get("/runs", response_model=list[RunListItem])
def list_runs(
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[RunListItem]:
    return RunService(db, settings).list_runs(limit)


@router.get("/runs/{run_id}", response_model=RunDetailResponse)
def get_run(
    run_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RunDetailResponse:
    return RunService(db, settings).get_run(run_id)


@router.get("/snapshots/{snapshot_id}", response_model=SnapshotResponse)
def get_snapshot(
    snapshot_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SnapshotResponse:
    return RunService(db, settings).get_snapshot(snapshot_id)


@router.get("/snapshots/{snapshot_id}/frontend", response_model=FrontendReadModel)
def get_frontend_read_model(
    snapshot_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FrontendReadModel:
    return RunService(db, settings).get_frontend_read_model(snapshot_id)


@router.get("/watchlist", response_model=WatchlistResponse)
def get_watchlist(settings: Settings = Depends(get_settings)) -> WatchlistResponse:
    return WatchlistResponse(symbols=settings.enabled_symbol_list)

