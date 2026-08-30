from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import Base
from app.decision_harness.remote_workflow import RemoteDecisionCompiler
from app.models.daily_evidence import DailyDecisionDatasetModel, DecisionChartProjectionModel
from app.repositories.remote_decision import RemoteDecisionRepository
from app.schemas.remote_decision import RemoteDecisionIntent


def _chart(dataset_id: str, trading_date: str, close: float, ma20: float) -> dict[str, object]:
    return {
        "schema_version": "urus.decision_chart_projection.v1",
        "dataset_id": dataset_id,
        "instruments": {
            "INTC": {
                "symbol": "INTC",
                "price": {
                    "bars": [
                        {
                            "date": trading_date,
                            "open": close - 1,
                            "high": close + 1,
                            "low": close - 1,
                            "close": close,
                            "volume": 1000,
                        }
                    ]
                },
                "series": [
                    {
                        "series_id": "ma20",
                        "points": [{"time": trading_date, "value": ma20}],
                    }
                ],
                "indicator_snapshot_id": f"indicator-{trading_date}",
                "quality": {"status": "ok", "warnings": []},
            }
        },
    }


def _dataset(dataset_id: str, trading_date: date) -> DailyDecisionDatasetModel:
    return DailyDecisionDatasetModel(
        id=dataset_id,
        schema_version="urus.daily_decision_dataset.v1",
        scope_type="instrument",
        scope_id="INTC",
        scope_version=1,
        trading_date=trading_date,
        cutoff_time=datetime(2026, 8, trading_date.day, 20, tzinfo=timezone.utc),
        market_timezone="America/New_York",
        bar_completion_policy="official_exchange_close_only_v1",
        status="ok",
        scope_json={
            "scope_type": "instrument",
            "scope_id": "INTC",
            "scope_version": 1,
            "symbols": ["INTC"],
            "trading_date": trading_date.isoformat(),
        },
        bar_manifest_json=[{"symbol": "INTC", "end_date": trading_date.isoformat()}],
        indicator_snapshot_ids=[],
        group_snapshot_ids=[],
        quality_json={"status": "ok", "warnings": []},
        payload_json={
            "dataset_id": dataset_id,
            "schema_version": "urus.daily_decision_dataset.v1",
            "feature_version": "technical_v5",
            "trading_date": trading_date.isoformat(),
            "scope": {
                "scope_type": "instrument",
                "scope_id": "INTC",
                "scope_version": 1,
                "symbols": ["INTC"],
            },
            "status": "ok",
            "quality": {"status": "ok", "warnings": []},
        },
        content_sha256=sha256(dataset_id.encode("utf-8")).hexdigest(),
        created_at=datetime(2026, 8, trading_date.day, 20, 1, tzinfo=timezone.utc),
    )


def test_instrument_compiler_sends_exact_previous_daily_baseline(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'temporal.db'}")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        previous = _dataset("dataset-previous", date(2026, 8, 21))
        current = _dataset("dataset-current", date(2026, 8, 25))
        session.add_all([previous, current])
        session.add_all(
            [
                DecisionChartProjectionModel(
                    id="chart-previous",
                    dataset_id="dataset-previous",
                    schema_version="urus.decision_chart_projection.v1",
                    scope_type="instrument",
                    scope_id="INTC",
                    payload_json=_chart("dataset-previous", "2026-08-21", 20.0, 19.0),
                    content_sha256="p" * 64,
                    created_at=previous.created_at,
                ),
                DecisionChartProjectionModel(
                    id="chart-current",
                    dataset_id="dataset-current",
                    schema_version="urus.decision_chart_projection.v1",
                    scope_type="instrument",
                    scope_id="INTC",
                    payload_json={
                        "schema_version": "urus.decision_chart_projection.v1",
                        "dataset_id": "dataset-current",
                        "instruments": {
                            "INTC": {
                                "symbol": "INTC",
                                "price": {
                                    "bars": [
                                        {
                                            "date": "2026-08-21",
                                            "open": 19.0,
                                            "high": 21.0,
                                            "low": 18.0,
                                            "close": 20.0,
                                            "volume": 1000,
                                        },
                                        {
                                            "date": "2026-08-25",
                                            "open": 20.0,
                                            "high": 22.0,
                                            "low": 19.0,
                                            "close": 21.0,
                                            "volume": 1200,
                                        },
                                    ]
                                },
                                "series": [
                                    {
                                        "series_id": "ma20",
                                        "points": [
                                            {"time": "2026-08-21", "value": 19.0},
                                            {"time": "2026-08-25", "value": 19.5},
                                        ],
                                    }
                                ],
                                "indicator_snapshot_id": "indicator-current",
                                "quality": {"status": "ok", "warnings": []},
                            }
                        },
                    },
                    content_sha256="c" * 64,
                    created_at=current.created_at,
                ),
            ]
        )
        RemoteDecisionRepository(session).save_binding(
            {
                "intent_type": "instrument_arbitration",
                "workflow_ref": "urus-instrument-arbitration@3",
                "status": "active",
                "definition_hash": "1" * 64,
                "compiled_hash": "2" * 64,
                "capability_manifest_hash": "3" * 64,
                "output_schema_version": "urus.remote_decision_artifact.v1",
                "published_at": datetime.now(timezone.utc),
                "verified_at": datetime.now(timezone.utc),
            }
        )

        compiler = RemoteDecisionCompiler(
            session,
            Settings(
                app_env="test",
                anomalo_workflow_enabled=True,
                anomalo_workflow_fake_adapter=True,
            ),
        )
        compiled = compiler.compile(
            RemoteDecisionIntent.INSTRUMENT_ARBITRATION,
            {"dataset_id": "dataset-current", "symbol": "INTC"},
        )

        assert compiled.blockers == []
        context = compiled.input_payload["evidence"]["temporal_context"]
        assert context["status"] == "ok"
        assert context["previous"]["dataset"]["dataset_id"] == "dataset-previous"
        assert context["previous"]["trading_date"] == "2026-08-21"
        assert context["changes"]["bar"]["close"]["percent"] == 5.0
        assert any(
            ref.get("dataset_id") == "dataset-previous"
            for ref in compiled.input_payload["evidence_refs"]
        )
