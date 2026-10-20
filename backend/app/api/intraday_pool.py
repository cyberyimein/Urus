from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_settings
from app.core.config import Settings
from app.core.errors import AppError
from app.repositories.intraday_pool import (
    IntradayPoolRepository,
    IntradayPoolUnavailable,
    market_today,
    next_trading_session,
)
from app.schemas.intraday_pool import (
    IntradayCollectionPlanResponse,
    IntradayPoolSettingsResponse,
    IntradayPoolUpdate,
)


router = APIRouter(tags=["intraday-pool"])


@router.get(
    "/settings/intraday-pool",
    response_model=IntradayPoolSettingsResponse,
)
def get_intraday_pool(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> IntradayPoolSettingsResponse:
    try:
        return IntradayPoolRepository(db, settings).settings_response(
            today=market_today(settings)
        )
    except IntradayPoolUnavailable as exc:
        raise AppError(
            str(exc), code="intraday_pool_unavailable", status_code=409
        ) from exc


@router.put(
    "/settings/intraday-pool",
    response_model=IntradayPoolSettingsResponse,
)
def update_intraday_pool(
    payload: IntradayPoolUpdate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> IntradayPoolSettingsResponse:
    try:
        return IntradayPoolRepository(db, settings).update(
            payload, today=market_today(settings)
        )
    except IntradayPoolUnavailable as exc:
        raise AppError(
            str(exc), code="intraday_pool_unavailable", status_code=409
        ) from exc


@router.get(
    "/intraday/collection-plan",
    response_model=IntradayCollectionPlanResponse,
)
def get_intraday_collection_plan(
    trading_date: date = Query(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> IntradayCollectionPlanResponse:
    try:
        return IntradayPoolRepository(db, settings).collection_plan(trading_date)
    except IntradayPoolUnavailable as exc:
        raise AppError(
            str(exc), code="intraday_pool_unavailable", status_code=409
        ) from exc


__all__ = ["market_today", "next_trading_session", "router"]
