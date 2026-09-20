from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.core.errors import AppError
from app.repositories.focus import FocusSymbolRepository
from app.schemas.focus import (
    FocusSymbolDeleteResponse,
    FocusSymbolResponse,
    normalize_focus_symbol,
)


router = APIRouter(prefix="/focus-symbols", tags=["focus-symbols"])


def _symbol_or_validation_error(symbol: str) -> str:
    try:
        return normalize_focus_symbol(symbol)
    except ValueError as exc:
        raise AppError(str(exc), code="validation_error", status_code=422) from exc


@router.get("", response_model=list[FocusSymbolResponse])
def list_focus_symbols(db: Session = Depends(get_db)) -> list[FocusSymbolResponse]:
    return [
        FocusSymbolResponse.model_validate(item, from_attributes=True)
        for item in FocusSymbolRepository(db).list()
    ]


@router.put("/{symbol}", response_model=FocusSymbolResponse)
def add_focus_symbol(symbol: str, db: Session = Depends(get_db)) -> FocusSymbolResponse:
    normalized = _symbol_or_validation_error(symbol)
    model = FocusSymbolRepository(db).add(normalized)
    return FocusSymbolResponse.model_validate(model, from_attributes=True)


@router.delete("/{symbol}", response_model=FocusSymbolDeleteResponse)
def remove_focus_symbol(symbol: str, db: Session = Depends(get_db)) -> FocusSymbolDeleteResponse:
    normalized = _symbol_or_validation_error(symbol)
    removed = FocusSymbolRepository(db).remove(normalized)
    return FocusSymbolDeleteResponse(symbol=normalized, removed=removed)
