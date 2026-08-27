from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_settings
from app.core.config import Settings
from app.core.errors import AppError
from app.decision_harness.cross_section import CrossSectionService
from app.integrations.moomoo import OpenDMarketAdapter
from app.repositories.observation import ObservationGroupRepository, ObservationRepository
from app.schemas.observation import (
    ObservationGroupCreateRequest,
    ObservationGroupDetailResponse,
    ObservationGroupResponse,
    ObservationGroupSyncResponse,
    ObservationRunCreateRequest,
    ObservationRunResponse,
    ObservationRunGroupSnapshotResponse,
)
from app.services.observation import ObservationGroupSyncService, ObservationRunService
from app.services.history_quota import HistoryAdmission
from app.services.market_data_collection import MoomooCollectionCoordinator
from app.repositories.universe import InstrumentUniverseRepository


router = APIRouter(prefix="/observation", tags=["observation"])


def _ensure_groups(db: Session, settings: Settings) -> ObservationGroupRepository:
    repository = ObservationGroupRepository(db)
    universe = InstrumentUniverseRepository(db).ensure_default(settings)
    items = [
        {
            "symbol": item.symbol,
            "asset_type": item.asset_type,
            "enabled": item.enabled,
            "roles": item.roles.model_dump(mode="json"),
        }
        for item in InstrumentUniverseRepository.response(universe).items
    ]
    repository.ensure_default(items)
    return repository


def _market_adapter(
    settings: Settings,
    *,
    history_admission: HistoryAdmission | None = None,
    rate_limiter: object | None = None,
):
    if not settings.moomoo_enabled:
        return None
    return OpenDMarketAdapter(
        settings.moomoo_host,
        settings.moomoo_port,
        market_timezone=settings.market_timezone,
        history_days=max(settings.moomoo_history_days, settings.daily_min_history_bars),
        sdk_home=Path(settings.moomoo_sdk_home),
        market_symbols=[item.strip() for item in settings.moomoo_market_symbols.split(",") if item.strip()],
        history_admission=history_admission,
        rate_limiter=rate_limiter,
        history_request_interval_seconds=settings.moomoo_history_request_interval_seconds,
        snapshot_request_interval_seconds=settings.moomoo_snapshot_request_interval_seconds,
    )


@router.get("/groups", response_model=list[ObservationGroupResponse])
def list_observation_groups(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[ObservationGroupResponse]:
    repository = _ensure_groups(db, settings)
    return [ObservationGroupResponse(**repository.response(item)) for item in repository.list_active()]


@router.post("/groups", response_model=ObservationGroupResponse, status_code=status.HTTP_201_CREATED)
def create_observation_group(
    payload: ObservationGroupCreateRequest,
    db: Session = Depends(get_db),
) -> ObservationGroupResponse:
    model = ObservationGroupRepository(db).save(payload)
    return ObservationGroupResponse(**ObservationGroupRepository.response(model))


@router.post("/groups/sync", response_model=ObservationGroupSyncResponse)
def sync_observation_groups(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ObservationGroupSyncResponse:
    try:
        result = ObservationGroupSyncService(db, settings).sync()
    except AppError:
        raise
    except ValueError as exc:
        raise AppError(str(exc), code="observation_group_sync_invalid", status_code=422) from exc
    return ObservationGroupSyncResponse(**result)


@router.put("/groups/{group_id}", response_model=ObservationGroupResponse)
def update_observation_group(
    group_id: str,
    payload: ObservationGroupCreateRequest,
    db: Session = Depends(get_db),
) -> ObservationGroupResponse:
    if payload.group_id != group_id:
        raise AppError("路径 group_id 与请求体不一致", code="observation_group_id_mismatch", status_code=422)
    model = ObservationGroupRepository(db).save(payload)
    return ObservationGroupResponse(**ObservationGroupRepository.response(model))


@router.get("/groups/{group_id}", response_model=ObservationGroupDetailResponse)
def get_observation_group(
    group_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ObservationGroupDetailResponse:
    repository = _ensure_groups(db, settings)
    group = repository.get(group_id)
    if group is None:
        raise AppError("找不到 observation group", code="observation_group_not_found", status_code=404)
    latest = ObservationRepository(db).latest_snapshot(group_id, group_version_id=group.id)
    return ObservationGroupDetailResponse(
        group=ObservationGroupResponse(**repository.response(group)),
        latest_snapshot=latest.payload_json if latest else None,
    )


@router.post("/runs", response_model=ObservationRunResponse, status_code=status.HTTP_201_CREATED)
def create_observation_run(
    payload: ObservationRunCreateRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ObservationRunResponse:
    universe_sync: dict[str, Any] | None = None
    if payload.trigger_mode == "scheduled" and not payload.universe_revision_id:
        # The scheduler normally supplies the revision returned by its
        # explicit sync call. Keep the API safe when a scheduled run is
        # submitted directly, so it cannot select an old active Universe.
        try:
            if settings.observation_universe_source_url:
                universe_sync = ObservationGroupSyncService(db, settings).sync()
        except AppError:
            raise
        except ValueError as exc:
            raise AppError(str(exc), code="observation_group_sync_invalid", status_code=422) from exc
    _ensure_groups(db, settings)
    collection_coordinator = (
        MoomooCollectionCoordinator(settings) if settings.moomoo_enabled else None
    )
    history_admission = (
        HistoryAdmission(db, settings, rate_limiter=collection_coordinator)
        if settings.moomoo_enabled
        else None
    )
    adapter = None
    try:
        adapter = _market_adapter(
            settings,
            history_admission=history_admission,
            rate_limiter=collection_coordinator,
        )
        result = ObservationRunService(db, settings).create_run(
            payload,
            bar_source=adapter,
            history_admission=history_admission,
            universe_sync=universe_sync,
        )
    except ValueError as exc:
        raise AppError(str(exc), code="observation_run_invalid", status_code=422) from exc
    finally:
        if history_admission is not None:
            try:
                history_admission.release_unfinished()
            except Exception:
                db.rollback()
        if adapter is not None:
            adapter.close()
    return ObservationRunResponse(**result)


@router.get("/runs", response_model=list[ObservationRunResponse])
def list_observation_runs(
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[ObservationRunResponse]:
    return [
        ObservationRunResponse(**ObservationRepository.run_response(item))
        for item in ObservationRepository(db).list_runs(limit)
    ]


@router.get("/indicator-catalog", response_model=list[dict[str, Any]])
def list_indicator_catalog() -> list[dict[str, Any]]:
    """List deterministic indicator lenses available to the Phase C scanner."""

    return CrossSectionService.indicator_catalog()


@router.get("/strategy-catalog", response_model=list[dict[str, Any]])
def list_strategy_catalog() -> list[dict[str, Any]]:
    """List deterministic strategy lenses available to the Phase C scanner."""

    return CrossSectionService.strategy_catalog()


@router.get("/runs/{run_id}/indicators/{indicator_id}", response_model=dict[str, Any])
def get_indicator_cross_section(
    run_id: str,
    indicator_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return CrossSectionService(db).indicator_projection(run_id, indicator_id)


@router.get("/runs/{run_id}/groups/{group_id}", response_model=ObservationRunGroupSnapshotResponse)
def get_observation_run_group_snapshot(
    run_id: str,
    group_id: str,
    db: Session = Depends(get_db),
) -> ObservationRunGroupSnapshotResponse:
    resolved = ObservationRepository(db).snapshot_for_run(run_id, group_id=group_id)
    if resolved is None:
        raise AppError("找不到该 Observation Run 中的精确组快照", code="observation_group_snapshot_not_found", status_code=404)
    run, snapshot = resolved
    return ObservationRunGroupSnapshotResponse(
        observation_run_id=run.id,
        group_id=snapshot.group_id,
        snapshot_id=snapshot.id,
        dataset_id=snapshot.dataset_id,
        group_version_id=snapshot.group_version_id,
        snapshot=dict(snapshot.payload_json or {}),
    )


@router.get("/runs/{run_id}/strategies/{strategy_id}", response_model=dict[str, Any])
def get_strategy_cross_section(
    run_id: str,
    strategy_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return CrossSectionService(db).strategy_projection(run_id, strategy_id)


@router.get("/runs/{run_id}", response_model=ObservationRunResponse)
def get_observation_run(
    run_id: str,
    db: Session = Depends(get_db),
) -> ObservationRunResponse:
    model = ObservationRepository(db).get_run(run_id)
    if model is None:
        raise AppError("找不到 Observation Run", code="observation_run_not_found", status_code=404)
    return ObservationRunResponse(**ObservationRepository.run_response(model))
