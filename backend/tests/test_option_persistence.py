from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from app.core.database import Base, create_database
from app.models import (
    InstrumentAnalysisBatchModel,
    InstrumentDailyBarModel,
    InstrumentSnapshotModel,
    OptionAnalysisBatchModel,
    OptionContractSnapshotModel,
    OptionExpirationAnalysisModel,
    OptionGammaFlipModel,
    OptionGammaProfilePointModel,
    OptionSymbolSnapshotModel,
)
from app.repositories import RunRepository


def test_normalized_option_inputs_and_profile_are_saved_with_snapshot(tmp_path) -> None:
    engine, session_factory = create_database(f"sqlite:///{tmp_path / 'options.db'}")
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)
    options_payload = {
        "is_mock": False,
        "provider": "test_provider",
        "source_mode": "snapshot",
        "captured_at": now.isoformat(),
        "symbols": [
            {
                "symbol": "QQQ",
                "spot": 100.0,
                "spot_time": now.isoformat(),
                "overview": {"iv": 25.0},
                "expirations": [
                    {
                        "expiration": "2026-08-21",
                        "days_to_expiry": 18,
                        "contract_count": 1,
                        "max_pain": 100.0,
                        "expected_move": {"amount": 4.0, "percent": 4.0, "atm_strike": 100.0},
                        "exposure": {
                            "totals": {"modeled_net_gex": 1200.0},
                            "walls": {"call_gamma": {"strike": 100.0, "exposure": 1200.0}},
                        },
                        "spot_gamma_profile": {
                            "available": True,
                            "points": [
                                {"spot": 90.0, "call_gex": 10.0, "put_gex": -20.0, "net_gex": -10.0},
                                {"spot": 100.0, "call_gex": 20.0, "put_gex": -10.0, "net_gex": 10.0},
                            ],
                            "gamma_flip_levels": [95.0],
                            "primary_gamma_flip": 95.0,
                            "current_spot_net_gex": 10.0,
                            "usable_iv_contracts": 1,
                            "range_percent": 30.0,
                            "point_count": 121,
                            "risk_free_rate_percent": 4.0,
                            "dividend_yield_percent": 0.0,
                        },
                    }
                ],
            }
        ],
    }
    persistence_payload = {
        "captured_at": now.isoformat(),
        "symbols": [
            {
                "symbol": "QQQ",
                "expirations": [
                    {
                        "expiration": "2026-08-21",
                        "contracts": [
                            {
                                "code": "US.QQQ260821C100000",
                                "option_type": "CALL",
                                "expiration": "2026-08-21",
                                "strike": 100.0,
                                "spot": 100.0,
                                "multiplier": 100.0,
                                "bid": 2.0,
                                "ask": 2.2,
                                "last": 2.1,
                                "volume": 10,
                                "open_interest": 100,
                                "implied_volatility": 25.0,
                                "delta": 0.5,
                                "gamma": 0.02,
                                "quote_time": now.isoformat(),
                            }
                        ],
                    }
                ],
            }
        ],
    }
    instrument_payload = {
        "is_mock": False,
        "provider": "moomoo_openapi",
        "source_mode": "snapshot",
        "captured_at": now.isoformat(),
        "requested_symbols": ["QQQ", "INTC", "SMH"],
        "quota_audit": {"subscription_unchanged": True},
        "instruments": [
            {
                "symbol": "INTC",
                "last_price": 100.0,
                "previous_close": 99.0,
                "quote_time": now.isoformat(),
                "quality_status": "ok",
                "history": {
                    "available": True,
                    "technical_indicators": {"returns_percent": {"20d": 5.0}},
                },
                "relative_strength": {"benchmark": "QQQ", "available": True},
            }
        ],
    }
    instrument_persistence_payload = {
        "captured_at": now.isoformat(),
        "symbols": [
            {
                "symbol": "INTC",
                "asset_type": "equity",
                "quote": {"symbol": "INTC", "last_price": 100.0},
                "history": {
                    "bars": [
                        {
                            "date": "2026-08-01",
                            "open": 98.0,
                            "high": 101.0,
                            "low": 97.0,
                            "close": 100.0,
                            "volume": 1000,
                            "turnover": 100000.0,
                            "turnover_rate": 1.0,
                        }
                    ]
                },
            }
        ],
    }

    with session_factory() as session:
        repository = RunRepository(session)
        repository.create_run(run_id="run-1", run_type="pre_market", cutoff_time=now)
        repository.save_snapshot_with_options(
            snapshot_id="snapshot-1",
            run_id="run-1",
            schema_version="1.0",
            cutoff_time=now,
            created_at=now,
            quality_status="live",
            payload={"schema_version": "1.0"},
            options_payload=options_payload,
            persistence_payload=persistence_payload,
            instrument_payload=instrument_payload,
            instrument_persistence_payload=instrument_persistence_payload,
        )

        assert session.scalar(select(func.count()).select_from(OptionAnalysisBatchModel)) == 1
        assert session.scalar(select(func.count()).select_from(OptionSymbolSnapshotModel)) == 1
        assert session.scalar(select(func.count()).select_from(OptionExpirationAnalysisModel)) == 1
        assert session.scalar(select(func.count()).select_from(OptionContractSnapshotModel)) == 1
        assert session.scalar(select(func.count()).select_from(OptionGammaProfilePointModel)) == 2
        assert session.scalar(select(func.count()).select_from(OptionGammaFlipModel)) == 1

        contract = session.scalar(select(OptionContractSnapshotModel))
        assert contract is not None
        assert contract.open_interest == 100
        assert contract.implied_volatility == 25.0
        flip = session.scalar(select(OptionGammaFlipModel))
        assert flip is not None and flip.is_primary is True
        assert session.scalar(select(func.count()).select_from(InstrumentAnalysisBatchModel)) == 1
        assert session.scalar(select(func.count()).select_from(InstrumentSnapshotModel)) == 1
        assert session.scalar(select(func.count()).select_from(InstrumentDailyBarModel)) == 1
        batch = session.scalar(select(InstrumentAnalysisBatchModel))
        assert batch is not None and batch.feature_version == "technical_v3"

    engine.dispose()
