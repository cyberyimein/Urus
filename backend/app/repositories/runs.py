from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    OptionAnalysisBatchModel,
    OptionContractSnapshotModel,
    OptionExpirationAnalysisModel,
    OptionGammaFlipModel,
    OptionGammaProfilePointModel,
    OptionSymbolSnapshotModel,
    RunModel,
    SnapshotModel,
    StepRunModel,
)


class RunRepository:
    """Persistence boundary for workflow runs, steps, and snapshots."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_run(
        self,
        *,
        run_id: str,
        run_type: str,
        cutoff_time: datetime,
    ) -> RunModel:
        run = RunModel(
            id=run_id,
            run_type=run_type,
            status="pending",
            cutoff_time=cutoff_time,
        )
        self.session.add(run)
        self.session.commit()
        return run

    def create_steps(self, run_id: str, steps: list[tuple[str, int, str, str]]) -> list[StepRunModel]:
        models = [
            StepRunModel(
                id=step_id,
                run_id=run_id,
                position=position,
                step_code=step_code,
                status=status,
            )
            for step_id, position, step_code, status in steps
        ]
        self.session.add_all(models)
        self.session.commit()
        return models

    def get_run(self, run_id: str) -> RunModel | None:
        statement = (
            select(RunModel)
            .options(selectinload(RunModel.steps))
            .where(RunModel.id == run_id)
        )
        return self.session.scalar(statement)

    def list_runs(self, limit: int = 50) -> list[RunModel]:
        statement = select(RunModel).order_by(RunModel.cutoff_time.desc()).limit(limit)
        return list(self.session.scalars(statement))

    def get_snapshot(self, snapshot_id: str) -> SnapshotModel | None:
        return self.session.get(SnapshotModel, snapshot_id)

    def save_snapshot(
        self,
        *,
        snapshot_id: str,
        run_id: str,
        schema_version: str,
        cutoff_time: datetime,
        created_at: datetime,
        quality_status: str,
        payload: dict[str, Any],
    ) -> SnapshotModel:
        snapshot = SnapshotModel(
            id=snapshot_id,
            run_id=run_id,
            schema_version=schema_version,
            cutoff_time=cutoff_time,
            created_at=created_at,
            quality_status=quality_status,
            payload=payload,
        )
        self.session.add(snapshot)
        self.session.commit()
        return snapshot

    def save_snapshot_with_options(
        self,
        *,
        snapshot_id: str,
        run_id: str,
        schema_version: str,
        cutoff_time: datetime,
        created_at: datetime,
        quality_status: str,
        payload: dict[str, Any],
        options_payload: dict[str, Any] | None,
        persistence_payload: dict[str, Any] | None,
    ) -> SnapshotModel:
        """Atomically save the read model and normalized option inputs/analytics."""
        snapshot = SnapshotModel(
            id=snapshot_id,
            run_id=run_id,
            schema_version=schema_version,
            cutoff_time=cutoff_time,
            created_at=created_at,
            quality_status=quality_status,
            payload=payload,
        )
        try:
            self.session.add(snapshot)
            if options_payload and persistence_payload and options_payload.get("is_mock") is False:
                self._add_option_analysis(
                    run_id=run_id,
                    snapshot_id=snapshot_id,
                    persisted_at=created_at,
                    options_payload=options_payload,
                    persistence_payload=persistence_payload,
                )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return snapshot

    def _add_option_analysis(
        self,
        *,
        run_id: str,
        snapshot_id: str,
        persisted_at: datetime,
        options_payload: dict[str, Any],
        persistence_payload: dict[str, Any],
    ) -> None:
        batch_id = str(uuid4())
        captured_raw = str(
            persistence_payload.get("captured_at") or options_payload.get("captured_at")
        )
        captured_at = datetime.fromisoformat(captured_raw.replace("Z", "+00:00"))
        public_symbols = {
            str(item.get("symbol")): item
            for item in options_payload.get("symbols", [])
            if isinstance(item, dict)
        }
        raw_symbols = {
            str(item.get("symbol")): item
            for item in persistence_payload.get("symbols", [])
            if isinstance(item, dict)
        }
        batch = OptionAnalysisBatchModel(
            id=batch_id,
            run_id=run_id,
            snapshot_id=snapshot_id,
            provider=str(options_payload.get("provider") or "unknown"),
            source_mode=str(options_payload.get("source_mode") or "snapshot"),
            captured_at=captured_at,
            persisted_at=persisted_at,
            model_version="spot_gamma_v1",
            risk_free_rate_percent=float(
                self._first_profile_value(public_symbols, "risk_free_rate_percent", 0.0)
            ),
            dividend_yield_percent=float(
                self._first_profile_value(public_symbols, "dividend_yield_percent", 0.0)
            ),
            gamma_profile_range_percent=float(
                self._first_profile_value(public_symbols, "range_percent", 0.0)
            ),
            gamma_profile_points=int(
                self._first_profile_value(public_symbols, "point_count", 0)
            ),
        )
        self.session.add(batch)

        for symbol, public_symbol in public_symbols.items():
            symbol_id = str(uuid4())
            symbol_model = OptionSymbolSnapshotModel(
                id=symbol_id,
                batch_id=batch_id,
                symbol=symbol,
                spot=float(public_symbol.get("spot") or 0.0),
                spot_time=public_symbol.get("spot_time"),
                overview=dict(public_symbol.get("overview") or {}),
            )
            self.session.add(symbol_model)
            raw_expirations = {
                str(item.get("expiration")): item
                for item in raw_symbols.get(symbol, {}).get("expirations", [])
                if isinstance(item, dict)
            }
            for analysis in public_symbol.get("expirations", []):
                if not isinstance(analysis, dict):
                    continue
                expiration_id = str(uuid4())
                expiration_text = str(analysis["expiration"])
                expected_move = dict(analysis.get("expected_move") or {})
                exposure = dict(analysis.get("exposure") or {})
                profile = dict(analysis.get("spot_gamma_profile") or {})
                metadata = {
                    key: value
                    for key, value in profile.items()
                    if key not in {"points", "gamma_flip_levels"}
                }
                expiration_model = OptionExpirationAnalysisModel(
                    id=expiration_id,
                    symbol_snapshot_id=symbol_id,
                    expiration=date.fromisoformat(expiration_text),
                    days_to_expiry=int(analysis.get("days_to_expiry") or 0),
                    contract_count=int(analysis.get("contract_count") or 0),
                    max_pain=analysis.get("max_pain"),
                    expected_move_amount=expected_move.get("amount"),
                    expected_move_percent=expected_move.get("percent"),
                    expected_move_atm_strike=expected_move.get("atm_strike"),
                    exposure_totals=dict(exposure.get("totals") or {}),
                    exposure_walls=dict(exposure.get("walls") or {}),
                    profile_available=bool(profile.get("available", False)),
                    primary_gamma_flip=profile.get("primary_gamma_flip"),
                    current_spot_net_gex=profile.get("current_spot_net_gex"),
                    usable_iv_contracts=int(profile.get("usable_iv_contracts") or 0),
                    profile_metadata=metadata,
                )
                self.session.add(expiration_model)
                contracts = raw_expirations.get(expiration_text, {}).get("contracts", [])
                self.session.add_all(
                    [
                        OptionContractSnapshotModel(
                            expiration_analysis_id=expiration_id,
                            code=str(contract["code"]),
                            option_type=str(contract["option_type"]),
                            strike=float(contract["strike"]),
                            spot=float(contract["spot"]),
                            multiplier=float(contract["multiplier"]),
                            bid=contract.get("bid"),
                            ask=contract.get("ask"),
                            last=contract.get("last"),
                            volume=int(contract.get("volume") or 0),
                            open_interest=int(contract.get("open_interest") or 0),
                            implied_volatility=contract.get("implied_volatility"),
                            delta=contract.get("delta"),
                            gamma=contract.get("gamma"),
                            quote_time=contract.get("quote_time"),
                        )
                        for contract in contracts
                        if isinstance(contract, dict)
                    ]
                )
                self.session.add_all(
                    [
                        OptionGammaProfilePointModel(
                            expiration_analysis_id=expiration_id,
                            point_index=index,
                            hypothetical_spot=float(point["spot"]),
                            call_gex=float(point["call_gex"]),
                            put_gex=float(point["put_gex"]),
                            net_gex=float(point["net_gex"]),
                        )
                        for index, point in enumerate(profile.get("points", []))
                        if isinstance(point, dict)
                    ]
                )
                primary = profile.get("primary_gamma_flip")
                self.session.add_all(
                    [
                        OptionGammaFlipModel(
                            expiration_analysis_id=expiration_id,
                            position=index,
                            level=float(level),
                            is_primary=primary is not None
                            and abs(float(level) - float(primary)) < 0.0001,
                        )
                        for index, level in enumerate(profile.get("gamma_flip_levels", []))
                    ]
                )

    @staticmethod
    def _first_profile_value(
        public_symbols: dict[str, dict[str, Any]], key: str, default: object
    ) -> object:
        for symbol in public_symbols.values():
            expirations = symbol.get("expirations", [])
            if expirations and isinstance(expirations[0], dict):
                profile = expirations[0].get("spot_gamma_profile", {})
                if isinstance(profile, dict) and profile.get(key) is not None:
                    return profile[key]
        return default

    def update_run(self, run: RunModel, **values: Any) -> RunModel:
        for field, value in values.items():
            setattr(run, field, value)
        self.session.add(run)
        self.session.commit()
        return run

    def update_step(self, step: StepRunModel, **values: Any) -> StepRunModel:
        for field, value in values.items():
            setattr(step, field, value)
        self.session.add(step)
        self.session.commit()
        return step
