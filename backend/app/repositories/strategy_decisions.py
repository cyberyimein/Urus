from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.strategy_decision import DeterministicSynthesisModel, StrategyDecisionModel


class StrategyDecisionRepository:
    """Persistence boundary for immutable Phase B strategy output."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def save_bundle(
        self,
        *,
        dataset_id: str,
        scope: dict[str, Any],
        strategy_set_sha256: str,
        decisions: Iterable[dict[str, Any]],
        synthesis: dict[str, Any],
        created_at: datetime | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        timestamp = created_at or utc_now()
        for decision in decisions:
            strategy = dict(decision["strategy"])
            existing = self.session.scalar(
                select(StrategyDecisionModel).where(
                    StrategyDecisionModel.dataset_id == dataset_id,
                    StrategyDecisionModel.strategy_set_sha256 == strategy_set_sha256,
                    StrategyDecisionModel.symbol == str(decision["scope"]["symbol"]),
                    StrategyDecisionModel.strategy_name == str(strategy["name"]),
                    StrategyDecisionModel.strategy_version == str(strategy["version"]),
                    StrategyDecisionModel.implementation_sha256
                    == str(strategy["implementation_sha256"]),
                )
            )
            if existing is not None:
                continue
            self.session.add(
                StrategyDecisionModel(
                    id=str(decision["decision_id"]),
                    dataset_id=dataset_id,
                    scope_type=str(scope["scope_type"]),
                    scope_id=str(scope["scope_id"]),
                    symbol=str(decision["scope"]["symbol"]),
                    strategy_set_sha256=strategy_set_sha256,
                    strategy_name=str(strategy["name"]),
                    strategy_version=str(strategy["version"]),
                    implementation_sha256=str(strategy["implementation_sha256"]),
                    status=str(decision.get("status") or "ok"),
                    content_sha256=str(decision["content_sha256"]),
                    payload_json=dict(decision),
                    generated_at=datetime.fromisoformat(
                        str(decision["generated_at"]).replace("Z", "+00:00")
                    ),
                    created_at=timestamp,
                )
            )

        existing_synthesis = self.session.scalar(
            select(DeterministicSynthesisModel).where(
                DeterministicSynthesisModel.dataset_id == dataset_id,
                DeterministicSynthesisModel.strategy_set_sha256 == strategy_set_sha256,
            )
        )
        if existing_synthesis is None:
            self.session.add(
                DeterministicSynthesisModel(
                    id=str(uuid4()),
                    dataset_id=dataset_id,
                    scope_type=str(scope["scope_type"]),
                    scope_id=str(scope["scope_id"]),
                    strategy_set_sha256=strategy_set_sha256,
                    content_sha256=str(synthesis["content_sha256"]),
                    payload_json=dict(synthesis),
                    created_at=timestamp,
                )
            )
        self.session.flush()
        return self.bundle(dataset_id, strategy_set_sha256=strategy_set_sha256)

    def bundle(
        self, dataset_id: str, *, strategy_set_sha256: str | None = None
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        synthesis_query = select(DeterministicSynthesisModel).where(
            DeterministicSynthesisModel.dataset_id == dataset_id
        )
        if strategy_set_sha256 is not None:
            synthesis_query = synthesis_query.where(
                DeterministicSynthesisModel.strategy_set_sha256 == strategy_set_sha256
            )
        synthesis_model = self.session.scalars(
            synthesis_query.order_by(DeterministicSynthesisModel.created_at.desc())
        ).first()
        if synthesis_model is None:
            return [], {}
        decision_models = list(
            self.session.scalars(
                select(StrategyDecisionModel)
                .where(
                    StrategyDecisionModel.dataset_id == dataset_id,
                    StrategyDecisionModel.strategy_set_sha256
                    == synthesis_model.strategy_set_sha256,
                )
                .order_by(
                    StrategyDecisionModel.symbol.asc(),
                    StrategyDecisionModel.strategy_name.asc(),
                )
            )
        )
        return [dict(model.payload_json) for model in decision_models], dict(
            synthesis_model.payload_json
        )
