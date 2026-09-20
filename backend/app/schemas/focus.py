from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from app.schemas.universe import SYMBOL_PATTERN


def normalize_focus_symbol(value: object) -> str:
    symbol = str(value or "").strip().upper()
    if not SYMBOL_PATTERN.fullmatch(symbol):
        raise ValueError("Symbol 只能包含大写字母、数字、点和连字符")
    return symbol


class FocusSymbolResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    created_at: datetime


class FocusSymbolDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    removed: bool
