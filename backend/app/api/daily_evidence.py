from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_settings
from app.core.config import Settings
from app.core.errors import AppError
from app.decision_harness.market_evidence import DailyMarketEvidenceService
from app.integrations.moomoo import OpenDMarketAdapter
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
    try:
        service = DailyMarketEvidenceService(db, settings)
        if settings.moomoo_enabled:
            adapter = OpenDMarketAdapter(
                settings.moomoo_host,
                settings.moomoo_port,
                market_timezone=settings.market_timezone,
                history_days=max(settings.moomoo_history_days, settings.daily_min_history_bars),
                sdk_home=Path(settings.moomoo_sdk_home),
                market_symbols=[item.strip() for item in settings.moomoo_market_symbols.split(",") if item.strip()],
            )
        result = service.freeze(
            scope_type=payload.scope_type,
            scope_id=payload.scope_id.upper() if payload.scope_type == "instrument" else payload.scope_id,
            symbols=payload.symbols,
            benchmark_symbols=payload.benchmark_symbols,
            scope_version=payload.scope_version,
            trading_date=payload.trading_date,
            cutoff_time=payload.cutoff_time,
            bar_source=adapter,
        )
    except ValueError as exc:
        raise AppError(str(exc), code="daily_evidence_invalid", status_code=422) from exc
    finally:
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
