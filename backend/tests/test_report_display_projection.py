from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import select

from app.core.database import Base, create_database
from app.models import AIDecisionRunModel, AIDecisionSessionModel, ReportDisplayProjectionModel, RunModel
from app.repositories import RunRepository
from app.repositories.agent import AIDecisionRepository
from app.repositories.report_display import ReportDisplayRepository
from app.services.run_service import RunService
from app.urus_agent.display_projection import (
    DISPLAY_PROJECTION_SCHEMA,
    _strike_rows,
    build_report_display_projection,
    projection_content_sha256,
)
from app.core.config import Settings


def _option_payload(now: datetime) -> tuple[dict, dict]:
    strikes = [float(50 + index) for index in range(139)]
    points = [
        {
            "spot": 50.0 + index * 0.5,
            "call_gex": float(index),
            "put_gex": float(-index - 1),
            "net_gex": -1.0 if index < 60 else 1.0,
        }
        for index in range(121)
    ]
    contracts = []
    for strike in strikes:
        for option_type, delta, gamma in (("CALL", 0.5, 0.02), ("PUT", -0.5, 0.015)):
            contracts.append(
                {
                    "code": f"US.QQQ260821{option_type[0]}{strike}",
                    "option_type": option_type,
                    "expiration": "2026-08-21",
                    "strike": strike,
                    "spot": 100.0,
                    "multiplier": 100.0,
                    "bid": 1.0,
                    "ask": 1.2,
                    "last": 1.1,
                    "volume": 10,
                    "open_interest": 100,
                    "implied_volatility": 25.0,
                    "delta": delta,
                    "gamma": gamma,
                    "quote_time": now.isoformat(),
                }
            )
    options_payload = {
        "is_mock": False,
        "provider": "test",
        "source_mode": "snapshot",
        "captured_at": now.isoformat(),
        "symbols": [
            {
                "symbol": "QQQ",
                "spot": 100.0,
                "spot_time": now.isoformat(),
                "overview": {"iv": 25.0, "hv_30d": 26.0},
                "expirations": [
                    {
                        "expiration": "2026-08-21",
                        "days_to_expiry": 8,
                        "contract_count": len(contracts),
                        "max_pain": 100.0,
                        "expected_move": {"amount": 5.0, "percent": 5.0, "atm_strike": 100.0},
                        "exposure": {"totals": {}, "walls": {}},
                        "spot_gamma_profile": {
                            "available": True,
                            "points": points,
                            "gamma_flip_levels": [80.0],
                            "primary_gamma_flip": 80.0,
                            "current_spot_net_gex": 1.0,
                            "point_count": 121,
                        },
                    }
                ],
            }
        ],
    }
    persistence_payload = {
        "captured_at": now.isoformat(),
        "symbols": [{"symbol": "QQQ", "expirations": [{"expiration": "2026-08-21", "contracts": contracts}]}],
    }
    return options_payload, persistence_payload


def test_display_projection_keeps_full_strike_rows_and_gamma_profile(tmp_path) -> None:
    engine, session_factory = create_database(f"sqlite:///{tmp_path / 'display.db'}")
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)
    options_payload, persistence_payload = _option_payload(now)

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
        )
        payload = build_report_display_projection(
            session,
            report_id="report-1",
            source_snapshot_ids=["snapshot-1"],
            source_run_ids=["run-1"],
            captured_at=now,
        )
        expiry = payload["options"]["symbols"]["QQQ"]["expirations"]["2026-08-21"]
        assert payload["schema_version"] == DISPLAY_PROJECTION_SCHEMA
        assert payload["data_quality"]["source_available"] is True
        assert expiry["strike_structure"]["row_count"] == 139
        assert expiry["strike_structure"]["is_complete"] is True
        assert len(expiry["strike_structure"]["rows"]) == 139
        assert expiry["gamma_profile"]["point_count"] == 121
        assert expiry["gamma_profile"]["is_complete"] is True
        assert len(expiry["gamma_profile"]["points"]) == 121
        assert expiry["gamma_profile"]["flips"][0]["direction"] == "negative_to_positive"
        assert projection_content_sha256(payload) == payload["source"]["content_sha256"]

    engine.dispose()


def test_display_projection_marks_empty_normalized_batch_unavailable(tmp_path) -> None:
    engine, session_factory = create_database(f"sqlite:///{tmp_path / 'empty-display.db'}")
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)
    empty_options = {
        "is_mock": False,
        "provider": "test",
        "source_mode": "snapshot",
        "captured_at": now.isoformat(),
        "symbols": [],
    }
    empty_persistence = {"captured_at": now.isoformat(), "symbols": []}

    with session_factory() as session:
        repository = RunRepository(session)
        repository.create_run(run_id="run-empty", run_type="pre_market", cutoff_time=now)
        repository.save_snapshot_with_options(
            snapshot_id="snapshot-empty",
            run_id="run-empty",
            schema_version="1.0",
            cutoff_time=now,
            created_at=now,
            quality_status="warning",
            payload={"schema_version": "1.0"},
            options_payload=empty_options,
            persistence_payload=empty_persistence,
        )

        payload = build_report_display_projection(
            session,
            report_id="report-empty",
            source_snapshot_ids=["snapshot-empty"],
            source_run_ids=["run-empty"],
            captured_at=now,
        )

        assert payload["data_quality"]["source_available"] is False
        assert "options" in payload["data_quality"]["missing_sections"]
        assert payload["data_quality"]["warnings"]

    engine.dispose()


def test_display_projection_uses_canonical_gamma_noise_threshold() -> None:
    def contract(strike: float, gamma: float):
        return SimpleNamespace(
            strike=strike,
            option_type="CALL",
            open_interest=100,
            volume=10,
            multiplier=100.0,
            delta=None,
            gamma=gamma,
        )

    rows = _strike_rows([contract(100.0, 0.1), contract(101.0, 0.001)], spot=100.0)

    assert rows[0]["gamma_regime"] == "positive"
    assert rows[1]["gamma_regime"] == "neutral"


def test_display_projection_replaces_expiration_set_with_latest_snapshot(tmp_path) -> None:
    engine, session_factory = create_database(f"sqlite:///{tmp_path / 'display-expiration-replace.db'}")
    Base.metadata.create_all(engine)
    first_capture = datetime(2026, 8, 13, 1, 0, tzinfo=UTC)
    second_capture = datetime(2026, 8, 13, 4, 0, tzinfo=UTC)

    def save_snapshot(session, snapshot_id: str, run_id: str, capture: datetime, expirations: list[str]) -> None:
        contracts_by_expiration = {
            expiration: [
                {
                    "code": f"US.QQQ{expiration.replace('-', '')}C100",
                    "option_type": "CALL",
                    "expiration": expiration,
                    "strike": 100.0,
                    "spot": 100.0,
                    "multiplier": 100.0,
                    "volume": 1,
                    "open_interest": 10,
                    "delta": 0.5,
                    "gamma": 0.02,
                    "quote_time": capture.isoformat(),
                }
            ]
            for expiration in expirations
        }
        options_payload = {
            "is_mock": False,
            "provider": "test",
            "source_mode": "snapshot",
            "captured_at": capture.isoformat(),
            "symbols": [
                {
                    "symbol": "QQQ",
                    "spot": 100.0,
                    "spot_time": capture.isoformat(),
                    "overview": {"iv": 25.0, "hv_30d": 26.0},
                    "expirations": [
                        {
                            "expiration": expiration,
                            "days_to_expiry": 8,
                            "contract_count": len(contracts_by_expiration[expiration]),
                            "spot_gamma_profile": {
                                "available": False,
                                "points": [],
                                "point_count": 0,
                            },
                        }
                        for expiration in expirations
                    ],
                }
            ],
        }
        persistence_payload = {
            "captured_at": capture.isoformat(),
            "symbols": [
                {
                    "symbol": "QQQ",
                    "expirations": [
                        {"expiration": expiration, "contracts": contracts_by_expiration[expiration]}
                        for expiration in expirations
                    ],
                }
            ],
        }
        repository = RunRepository(session)
        repository.create_run(run_id=run_id, run_type="pre_market", cutoff_time=capture)
        repository.save_snapshot_with_options(
            snapshot_id=snapshot_id,
            run_id=run_id,
            schema_version="1.0",
            cutoff_time=capture,
            created_at=capture,
            quality_status="live",
            payload={"schema_version": "1.0"},
            options_payload=options_payload,
            persistence_payload=persistence_payload,
        )

    with session_factory() as session:
        save_snapshot(session, "snapshot-old", "run-old", first_capture, ["2026-08-21", "2026-09-18"])
        save_snapshot(session, "snapshot-new", "run-new", second_capture, ["2026-09-18", "2026-10-16"])

        payload = build_report_display_projection(
            session,
            report_id="report-expiration-replace",
            source_snapshot_ids=["snapshot-old", "snapshot-new"],
            source_run_ids=["run-old", "run-new"],
            captured_at=second_capture,
        )

        expirations = payload["options"]["symbols"]["QQQ"]["expirations"]
        assert sorted(expirations) == ["2026-09-18", "2026-10-16"]
        assert "2026-08-21" not in expirations

    engine.dispose()


def test_projection_failure_rolls_back_shared_session(tmp_path) -> None:
    engine, session_factory = create_database(f"sqlite:///{tmp_path / 'projection-rollback.db'}")
    Base.metadata.create_all(engine)
    with session_factory() as session:
        service = RunService(session, Settings())
        with (
            patch.object(session, "rollback", wraps=session.rollback) as rollback,
            patch.object(ReportDisplayRepository, "save", side_effect=RuntimeError("write failed")),
        ):
            service._persist_report_display_projection_for_report(
                "report-1",
                source_snapshot_ids=[],
                source_run_ids=[],
                captured_at=datetime.now(UTC),
            )

            rollback.assert_called_once()
    engine.dispose()


def test_report_deletion_removes_display_projection_but_not_source_snapshot(tmp_path) -> None:
    engine, session_factory = create_database(f"sqlite:///{tmp_path / 'delete-display.db'}")
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)
    with session_factory() as session:
        session.add(
            AIDecisionSessionModel(
                id="report-1",
                workflow_run_id="run-1",
                dataset_key="dataset",
                cutoff_time=now,
                decision_phase="current_state",
                trading_date="2026-08-13",
                status="succeeded",
                policy_json={},
                technical_report_schema_version="v1",
                technical_report_json={},
                started_at=now,
                created_at=now,
            )
        )
        session.add(
            AIDecisionRunModel(
                id="decision-1",
                decision_session_id="report-1",
                workflow_run_id="run-1",
                stage="synthesis",
                sequence=1,
                task_type="equity_ranking",
                status="succeeded",
                dataset_key="dataset",
                source_run_ids=["run-1"],
                source_snapshot_ids=["snapshot-1"],
                cutoff_time=now,
                requested_symbols=["QQQ"],
                skill_name="urus-equity-decision",
                skill_hash="hash",
                provider="test",
                input_schema_version="v1",
                input_hash="hash",
                output_schema_version="v1",
                parsed_output={},
                started_at=now,
                created_at=now,
            )
        )
        session.commit()
        ReportDisplayRepository(session).save(
            report_id="report-1",
            payload={"schema_version": DISPLAY_PROJECTION_SCHEMA},
            source_snapshot_ids=["snapshot-1"],
            source_run_ids=["run-1"],
            content_sha256="hash",
            schema_version=DISPLAY_PROJECTION_SCHEMA,
            created_at=now,
        )
        repository = AIDecisionRepository(session)
        assert repository.delete_session("report-1") is True
        assert session.scalar(select(ReportDisplayProjectionModel)) is None
    engine.dispose()


def test_display_manifest_and_lazy_options_routes(client, app) -> None:
    now = datetime.now(UTC)
    projection = {
        "schema_version": DISPLAY_PROJECTION_SCHEMA,
        "source": {"snapshot_ids": ["snapshot-1"]},
        "options": {
            "symbols": {
                "QQQ": {
                    "spot": 100.0,
                    "as_of": now.isoformat(),
                    "expirations": {
                        "2026-08-21": {
                            "strike_structure": {"rows": [{"strike": 100.0}], "row_count": 1},
                            "gamma_profile": {"points": [], "point_count": 0},
                        }
                    },
                }
            }
        },
        "chart_specs": [],
        "data_quality": {"source_available": True, "warnings": [], "missing_sections": []},
    }
    with app.state.session_factory() as session:
        session.add(
            RunModel(id="run-1", run_type="manual_analysis", status="succeeded", cutoff_time=now)
        )
        session.add(
            AIDecisionSessionModel(
                id="report-1",
                workflow_run_id="run-1",
                dataset_key="dataset",
                cutoff_time=now,
                decision_phase="current_state",
                trading_date="2026-08-13",
                status="succeeded",
                policy_json={},
                technical_report_schema_version="v1",
                technical_report_json={},
                started_at=now,
                created_at=now,
            )
        )
        session.commit()
        ReportDisplayRepository(session).save(
            report_id="report-1",
            payload=projection,
            source_snapshot_ids=["snapshot-1"],
            source_run_ids=["run-1"],
            content_sha256="hash",
            schema_version=DISPLAY_PROJECTION_SCHEMA,
            created_at=now,
        )

    manifest_response = client.get("/api/research-reports/report-1/display/manifest")
    assert manifest_response.status_code == 200
    assert manifest_response.json()["available"] is True
    option_response = client.get(
        "/api/research-reports/report-1/display/options/QQQ?expiration=2026-08-21"
    )
    assert option_response.status_code == 200
    assert option_response.json()["data"]["strike_structure"]["row_count"] == 1
