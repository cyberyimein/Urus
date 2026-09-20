from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.focus import FocusSymbolModel


class FocusSymbolRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list(self) -> list[FocusSymbolModel]:
        return list(
            self.session.scalars(
                select(FocusSymbolModel).order_by(
                    FocusSymbolModel.created_at.asc(),
                    FocusSymbolModel.symbol.asc(),
                )
            )
        )

    def add(self, symbol: str) -> FocusSymbolModel:
        existing = self.session.get(FocusSymbolModel, symbol)
        if existing is not None:
            return existing
        model = FocusSymbolModel(symbol=symbol, created_at=utc_now())
        self.session.add(model)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.session.get(FocusSymbolModel, symbol)
            if existing is None:
                raise
            return existing
        self.session.refresh(model)
        return model

    def remove(self, symbol: str) -> bool:
        model = self.session.get(FocusSymbolModel, symbol)
        if model is None:
            return False
        self.session.delete(model)
        self.session.commit()
        return True
