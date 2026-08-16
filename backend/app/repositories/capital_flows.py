from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.capital_flows import CapitalFlowDailyModel


class CapitalFlowRepository:
    """Persist and read the reusable daily capital-flow cache."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(
        self,
        *,
        provider: str,
        symbol: str,
        trading_date: date,
        period_type: str = "DAY",
    ) -> CapitalFlowDailyModel | None:
        return self.session.get(
            CapitalFlowDailyModel,
            {
                "provider": provider,
                "symbol": symbol.upper(),
                "trading_date": trading_date,
                "period_type": period_type,
            },
        )

    def add_if_missing(self, payload: dict[str, Any]) -> CapitalFlowDailyModel:
        key = {
            "provider": str(payload["provider"]),
            "symbol": str(payload["symbol"]).upper(),
            "trading_date": payload["trading_date"],
            "period_type": str(payload.get("period_type") or "DAY"),
        }
        existing = self.get(**key)
        if existing is not None:
            return existing
        model = CapitalFlowDailyModel(
            **key,
            in_flow=payload.get("in_flow"),
            main_in_flow=payload.get("main_in_flow"),
            super_in_flow=payload.get("super_in_flow"),
            big_in_flow=payload.get("big_in_flow"),
            mid_in_flow=payload.get("mid_in_flow"),
            sml_in_flow=payload.get("sml_in_flow"),
            source_time=payload.get("source_time"),
            fetched_at=payload["fetched_at"],
            quality_status=str(payload.get("quality_status") or "unknown"),
            quality_warnings=list(payload.get("quality_warnings") or []),
            raw_payload=dict(payload.get("raw_payload") or {}),
        )
        self.session.add(model)
        self.session.flush()
        return model

    def recent(
        self,
        *,
        provider: str,
        symbol: str,
        through_date: date,
        limit: int,
        period_type: str = "DAY",
    ) -> list[CapitalFlowDailyModel]:
        statement = (
            select(CapitalFlowDailyModel)
            .where(
                CapitalFlowDailyModel.provider == provider,
                CapitalFlowDailyModel.symbol == symbol.upper(),
                CapitalFlowDailyModel.period_type == period_type,
                CapitalFlowDailyModel.trading_date <= through_date,
            )
            .order_by(CapitalFlowDailyModel.trading_date.desc())
            .limit(max(1, limit))
        )
        return list(reversed(list(self.session.scalars(statement))))
