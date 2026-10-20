from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_settings
from app.core.config import Settings
from app.core.errors import AppError
from app.core.time import utc_now
from app.decision_harness.market_evidence import DailyMarketEvidenceService
from app.integrations.moomoo import OpenDMarketAdapter
from app.services.history_quota import HistoryAdmission
from app.services.market_data_collection import MoomooCollectionCoordinator
from app.services.opportunity_views import DailyOpportunityViewService
from app.services.trading_calendar import build_moomoo_calendar
from app.schemas.daily_evidence import (
    DailyDatasetCreateRequest,
    DailyEvidenceResponse,
    StrategyBundleResponse,
)


router = APIRouter(prefix="/daily-evidence", tags=["daily-evidence"])


@router.post(
    "/datasets",
    response_model=DailyEvidenceResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_daily_dataset(
    payload: DailyDatasetCreateRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DailyEvidenceResponse:
    adapter = None
    history_admission = None
    collection_coordinator = None
    calendar_source = None
    try:
        if settings.moomoo_enabled:
            collection_coordinator = MoomooCollectionCoordinator(settings)
            history_admission = HistoryAdmission(
                db, settings, rate_limiter=collection_coordinator
            )
            adapter = OpenDMarketAdapter(
                settings.moomoo_host,
                settings.moomoo_port,
                market_timezone=settings.market_timezone,
                history_days=max(settings.moomoo_history_days, settings.daily_min_history_bars),
                sdk_home=Path(settings.moomoo_sdk_home),
                market_symbols=[item.strip() for item in settings.moomoo_market_symbols.split(",") if item.strip()],
                history_admission=history_admission,
                rate_limiter=collection_coordinator,
                history_request_interval_seconds=settings.moomoo_history_request_interval_seconds,
                snapshot_request_interval_seconds=settings.moomoo_snapshot_request_interval_seconds,
                trading_calendar_request_interval_seconds=(
                    settings.moomoo_trading_calendar_request_interval_seconds
                ),
            )
            calendar_source = build_moomoo_calendar(adapter, settings.market_calendar)
            history_admission.bind_calendar_source(calendar_source)
            history_admission.bind_quota_reader(adapter.quota_snapshot)
            history_admission.prepare_symbols(
                [*payload.symbols, *payload.benchmark_symbols],
                now=payload.cutoff_time,
            )
        result = DailyMarketEvidenceService(
            db,
            settings,
            calendar_source=calendar_source,
        ).freeze(
            scope_type=payload.scope_type,
            scope_id=payload.scope_id.upper() if payload.scope_type == "instrument" else payload.scope_id,
            symbols=payload.symbols,
            benchmark_symbols=payload.benchmark_symbols,
            scope_version=payload.scope_version,
            trading_date=payload.trading_date,
            cutoff_time=payload.cutoff_time,
            bar_source=adapter,
            allow_live_collection_after_cutoff=payload.cutoff_time is None,
        )
    except ValueError as exc:
        raise AppError(str(exc), code="daily_evidence_invalid", status_code=422) from exc
    finally:
        if history_admission is not None:
            try:
                history_admission.release_unfinished()
            except Exception:
                db.rollback()
        if adapter is not None:
            adapter.close()
    return DailyEvidenceResponse(**result)


@router.get("/datasets/{dataset_id}", response_model=dict[str, object])
def get_daily_dataset(
    dataset_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    payload = DailyMarketEvidenceService(db, settings).get_dataset(dataset_id)
    if payload is None:
        raise AppError("找不到 Daily Decision Dataset", code="daily_dataset_not_found", status_code=404)
    return payload


@router.get("/datasets/{dataset_id}/chart", response_model=dict[str, object])
def get_daily_chart_projection(
    dataset_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    payload = DailyMarketEvidenceService(db, settings).get_chart(dataset_id)
    if payload is None:
        raise AppError("找不到 Decision Chart Projection", code="daily_chart_not_found", status_code=404)
    return payload


@router.get(
    "/datasets/{dataset_id}/instruments/{symbol}/evidence",
    response_model=DailyEvidenceResponse,
)
def get_daily_instrument_evidence(
    dataset_id: str,
    symbol: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DailyEvidenceResponse:
    try:
        payload = DailyMarketEvidenceService(db, settings).get_instrument_evidence(dataset_id, symbol)
    except ValueError as exc:
        raise AppError(str(exc), code="daily_dataset_symbol_invalid", status_code=422) from exc
    if payload is None:
        raise AppError("找不到指定标的的冻结图表", code="daily_chart_not_found", status_code=404)
    return DailyEvidenceResponse(**payload)


@router.get(
    "/datasets/{dataset_id}/opportunity-view",
    response_model=dict[str, object],
)
async def get_daily_opportunity_view(
    request: Request,
    dataset_id: str,
    symbol: str | None = Query(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    cancel_event = threading.Event()
    service = DailyOpportunityViewService(db, settings)

    def _read_view() -> dict[str, object] | None:
        return service.get_dataset_opportunity_view(
            dataset_id,
            viewed_at=utc_now(),
            symbol=symbol,
            cancel_event=cancel_event,
        )

    task = asyncio.create_task(asyncio.to_thread(_read_view))
    disconnected = False
    try:
        while not task.done():
            if request is not None and await request.is_disconnected():
                disconnected = True
                cancel_event.set()
                # Let the coordinator observe the cancellation and terminate
                # its child before the request-scoped DB session is closed.
                try:
                    await asyncio.shield(task)
                except Exception:
                    pass
                raise AppError(
                    "机会视图请求已断开",
                    code="daily_opportunity_view_cancelled",
                    status_code=499,
                )
            await asyncio.sleep(0.05)
        payload = await task
    except ValueError as exc:
        raise AppError(
            str(exc),
            code="daily_dataset_symbol_invalid",
            status_code=422,
        ) from exc
    finally:
        if disconnected or not task.done():
            cancel_event.set()
    if payload is None:
        raise AppError("找不到 Daily Decision Dataset", code="daily_dataset_not_found", status_code=404)
    return payload


@router.get(
    "/datasets/{dataset_id}/strategies",
    response_model=StrategyBundleResponse,
)
def get_daily_strategy_bundle(
    dataset_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StrategyBundleResponse:
    service = DailyMarketEvidenceService(db, settings)
    dataset = service.get_dataset(dataset_id)
    if dataset is None:
        raise AppError("找不到 Daily Decision Dataset", code="daily_dataset_not_found", status_code=404)
    payload = service.get_strategy_bundle(dataset_id)
    if not payload["strategy_decisions"] and not payload["deterministic_synthesis"]:
        raise AppError(
            "该数据集尚未生成 Strategy Decision",
            code="strategy_bundle_not_found",
            status_code=404,
        )
    return StrategyBundleResponse(**payload)
