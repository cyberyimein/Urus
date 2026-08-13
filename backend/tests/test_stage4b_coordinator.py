from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings
from app.core.database import Base, create_database
from app.integrations.decision import DecisionRequest, UrusDecisionAdapter
from app.main import create_app
from app.models import AIDecisionSessionModel, AIDecisionRunModel, AIModelTurnModel, AITraceNodeModel, RunModel
from app.urus_agent.providers import FakeLLMProvider
from app.urus_agent.providers.openrouter import ProviderResponse
from app.urus_agent.coordinator import (
    MAX_THEME_TASKS,
    CoordinatorRequest,
    DecisionCoordinator,
    _theme_scopes,
)
from app.urus_agent.packet import build_stage_decision_packet
from app.urus_agent.prompts import load_agent_profile
from fastapi.testclient import TestClient


def test_theme_scopes_keep_cross_themes_as_metadata_without_duplicate_calls() -> None:
    from types import SimpleNamespace

    evidence = SimpleNamespace(
        current_phase="pre_close",
        packet={
            "observations": {
                "pre_close": {
                    "instruments": [
                        {
                            "symbol": "AMD",
                            "asset_type": "equity",
                            "theme": "半导体",
                            "themes": ["半导体", "AI 基础设施"],
                        },
                    ],
                },
            },
        },
    )

    assert _theme_scopes(evidence, ["AMD"]) == [
        ("半导体", ["AMD"]),
    ]


def test_theme_scopes_bound_custom_theme_task_count() -> None:
    from types import SimpleNamespace

    instruments = [
        {
            "symbol": f"S{index}",
            "asset_type": "equity",
            "theme": f"自定义主题 {index}",
            "themes": [f"自定义主题 {index}"],
        }
        for index in range(MAX_THEME_TASKS + 5)
    ]
    evidence = SimpleNamespace(
        current_phase="pre_close",
        packet={"observations": {"pre_close": {"instruments": instruments}}},
    )

    scopes = _theme_scopes(evidence, [item["symbol"] for item in instruments])

    assert len(scopes) == MAX_THEME_TASKS
    assert scopes[-1][0] == "其他关注"
    assert len(scopes[-1][1]) == 6


def _equity_output() -> dict:
    return {
        "schema_version": "urus.equity_decision.v3",
        "decision_phase": "pre_close",
        "agent_profile": "urus-preclose-strategist",
        "forecast_horizon": "final_hour",
        "forecast": {
            "direction": "mixed",
            "confidence": 0.5,
            "expected_path": "A bounded test path.",
        },
        "review": None,
        "as_of": None,
        "status": "decision",
        "market_regime": {"classification": "selective", "confidence": 0.8, "evidence": []},
        "rankings": [
            {
                "rank": 1,
                "symbol": "QQQ",
                "themes": ["ETF"],
                "action": "watch",
                "strict_sepa_completeness": "partial",
                "score": 0.8,
                "confidence": 0.8,
                "thesis": "A bounded test thesis.",
                "evidence": [],
                "risks": ["test risk"],
                "missing_fields": [],
                "invalidation_conditions": ["below support"],
                "instrument_forecast": {
                    "direction": "up",
                    "probability": 0.65,
                    "expected_return_range_percent": {
                        "minimum_percent": 0.1,
                        "maximum_percent": 1.0,
                    },
                    "relative_to": "SPY",
                    "relative_direction": "outperform",
                    "horizon": "final_hour",
                },
                "if_cash": {
                    "action": "buy",
                    "conviction": "medium",
                    "reason": "The bounded fixture clears the entry gate.",
                    "entry_condition": "Hold above support.",
                },
                "if_held": {
                    "action": "hold",
                    "conviction": "medium",
                    "reason": "The fixture remains above invalidation.",
                    "take_profit_condition": "At resistance.",
                    "stop_loss_condition": "Below support.",
                },
            }
        ],
        "portfolio_warnings": [],
        "disclaimer": "Research output only; no order was placed.",
    }


def _evidence() -> dict[str, object]:
    return {
        "1a": {"symbol": "QQQ", "regular_price": 700.0, "is_mock": False},
        "1b": {"events": []},
        "2": {
            "available": True,
            "is_mock": False,
            "provider": "moomoo_openapi",
            "source_mode": "snapshot",
            "symbols": [
                {
                    "symbol": "QQQ",
                    "spot": 700.0,
                    "overview": {},
                    "expirations": [
                        {
                            "expiration": "2026-08-07",
                            "days_to_expiry": 4,
                            "contract_count": 10,
                            "max_pain": 700.0,
                            "expected_move": {"amount": 8.0, "percent": 1.14},
                            "exposure": {
                                "totals": {"net_dex": 120.0, "modeled_net_gex": 20.0},
                                "walls": {
                                    "call_wall": {"strike": 710.0},
                                    "put_wall": {"strike": 690.0},
                                },
                            },
                            "spot_gamma_profile": {
                                "current_spot": 700.0,
                                "primary_gamma_flip": 698.0,
                                "current_spot_net_gex": 20.0,
                            },
                        }
                    ],
                }
            ],
        },
        "3a": {"instruments": [{"symbol": "QQQ", "asset_type": "etf", "theme": "ETF"}]},
        "3b": {"events": []},
    }


def _current_state_output() -> dict[str, Any]:
    output = _equity_output()
    output.update(
        decision_phase="current_state",
        agent_profile="urus-current-state-analyst",
        forecast_horizon="current_state",
        forecast=None,
        review=None,
    )
    for ranking in output["rankings"]:
        ranking["instrument_forecast"] = None
        ranking["if_cash"] = None
        ranking["if_held"] = None
    return output


def test_manual_current_state_uses_one_model_invocation_and_is_not_scoreable(tmp_path) -> None:
    engine, factory = create_database(f"sqlite:///{tmp_path / 'manual-current.db'}")
    Base.metadata.create_all(bind=engine)
    cutoff = datetime(2026, 8, 3, 15, tzinfo=UTC)
    provider = FakeLLMProvider(
        [{"message": {"role": "assistant", "content": json.dumps(_current_state_output())}}]
    )
    with factory() as session:
        session.add(
            RunModel(
                id="manual-workflow-1",
                run_type="manual_analysis",
                status="succeeded",
                cutoff_time=cutoff,
            )
        )
        session.commit()
        payload = {
            "schema_version": "1.0",
            "run_id": "manual-workflow-1",
            "snapshot_id": "manual-snapshot-1",
            "run_type": "manual_analysis",
            "cutoff_time": cutoff.isoformat(),
            "is_mock": False,
            "market": _evidence()["1a"],
            "instrument_cards": _evidence()["3a"]["instruments"],
            "options": _evidence()["2"],
            "systematic_flows": {},
            "data_quality": {"status": "ok", "warnings": [], "errors": []},
        }
        observation = {
            "run": {
                "id": "manual-workflow-1",
                "run_type": "manual_analysis",
                "status": "succeeded",
                "cutoff_time": cutoff.isoformat(),
            },
            "snapshot": {
                "id": "manual-snapshot-1",
                "schema_version": "1.0",
                "cutoff_time": cutoff.isoformat(),
                "created_at": cutoff.isoformat(),
                "quality_status": "ok",
                "payload": payload,
            },
        }
        packet = build_stage_decision_packet(
            dataset_key="manual-analysis:2026-08-03:current_state:manual-workflow-1",
            label="manual current state",
            captured_at=cutoff,
            decision_phase="current_state",
            trading_date="2026-08-03",
            observations={"current_state": observation},
            prior_reports={},
            events=[],
            agent_profile=load_agent_profile("current_state"),
        )
        metadata = {
            "trigger_type": "manual",
            "analysis_mode": "current_state",
            "session_context": "intraday",
            "report_scope": ["technical_report", "ai_state_analysis"],
            "official_cycle": False,
            "eligible_for_scoring": False,
            "updates_official_cta_state": False,
        }
        packet["decision_context"].update(metadata)
        result = DecisionCoordinator(
            session,
            Settings(urus_agent_enforce_stage_tools=True),
            provider=provider,
        ).execute(
            CoordinatorRequest(
                workflow_run_id="manual-workflow-1",
                cutoff_time=cutoff,
                evidence={},
                symbols=["QQQ"],
                dataset_key=str(packet["source"]["dataset_key"]),
                source_snapshot_ids=["manual-snapshot-1"],
                source_run_ids=["manual-workflow-1"],
                decision_packet=packet,
                decision_phase="current_state",
                trading_date="2026-08-03",
                analysis_metadata=metadata,
            )
        )

        assert session.query(AIDecisionRunModel).count() == 1
        assert session.query(AIModelTurnModel).count() == 1
        assert result.decision_report["trigger_type"] == "manual"
        assert result.decision_report["eligible_for_scoring"] is False
        assert result.decision_report["updates_official_cta_state"] is False
        assert result.decision_report["objective_evaluation"]["status"] == "not_applicable"
    engine.dispose()


def test_two_stage_coordinator_persists_session_trace_and_raw_turns(tmp_path) -> None:
    engine, factory = create_database(f"sqlite:///{tmp_path / 'coordinator.db'}")
    Base.metadata.create_all(bind=engine)
    provider = FakeLLMProvider(
        [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(_equity_output()),
                    # A provider may return reasoning-like metadata even
                    # though Urus does not request or display it by default.
                    "reasoning_content": "provider-returned diagnostic text",
                },
                "reasoning_details": [{"type": "summary", "text": "diagnostic"}],
            },
            {"message": {"role": "assistant", "content": json.dumps(_equity_output())}},
        ]
    )
    settings = Settings(
        urus_agent_enforce_stage_tools=False,
    )
    report_id = ""
    raw_node_id = ""
    invocation_node_id = ""
    with factory() as session:
        session.add(
            RunModel(
                id="workflow-1",
                run_type="pre_close",
                status="succeeded",
                cutoff_time=datetime(2026, 8, 3, 20, tzinfo=UTC),
            )
        )
        session.commit()
        response = UrusDecisionAdapter(session, settings, provider=provider).decide(
            DecisionRequest(
                session_id="urus-workflow-1-step-4",
                workflow_run_id="workflow-1",
                evidence=_evidence(),
                symbols=["QQQ"],
                cutoff_time=datetime(2026, 8, 3, 20, tzinfo=UTC),
            )
        )
        assert response.session_id
        assert response.decision_report is not None
        assert response.decision_report["status"] == "succeeded"
        assert response.decision_report["candidate_gate"] == []
        assert response.decision_report["option_decisions"] == []
        assert response.decision_report["equity_option_context"][0]["symbol"] == "QQQ"
        assert response.decision_report["equity_option_context"][0]["available"] is True
        assert response.decision_report["equity_option_context"][0]["entry_context"] == "near_gamma_flip"
        assert response.decision_report["equity_option_context"][0]["call_wall"] == 710.0
        assert session.query(AIDecisionSessionModel).count() == 1
        assert session.query(AIDecisionRunModel).count() == 2
        assert session.query(AITraceNodeModel).count() >= 5
        assert session.query(AIModelTurnModel).count() == 2
        report_id = response.session_id or ""
        raw_node_id = session.query(AIModelTurnModel).first().trace_node_id or ""
        invocation_node_id = (
            session.query(AITraceNodeModel)
            .filter_by(label="urus-preclose-strategist · Market")
            .first()
            .id
        )
    app = create_app(
        Settings(
            app_env="test",
            database_url=f"sqlite:///{tmp_path / 'coordinator.db'}",
            urus_agent_enabled=False,
        )
    )
    with TestClient(app) as client:
        index = client.get("/api/runs/workflow-1/research-reports")
        assert index.status_code == 200
        assert index.json()[0]["report_id"] == report_id
        global_index = client.get("/api/research-reports?limit=10")
        assert global_index.status_code == 200
        assert global_index.json()[0]["report_id"] == report_id
        payload = client.get(f"/api/research-reports/{report_id}")
        assert payload.status_code == 200
        assert payload.json()["decision_report"] is None
        assert payload.json()["run_summary"]["tool_call_count"] >= 0
        decision_payload = client.get(f"/api/research-reports/{report_id}/decision")
        assert decision_payload.status_code == 200
        assert decision_payload.json()["schema_version"] == "urus.ai_decision_report.v5"
        trace = client.get(f"/api/research-reports/{report_id}/trace")
        assert trace.status_code == 200
        assert len(trace.json()["nodes"]) >= 6
        node_detail = client.get(
            f"/api/research-reports/{report_id}/trace/nodes/{raw_node_id}"
        )
        assert node_detail.status_code == 200
        # Raw provider material is not part of the ordinary node response.
        assert "raw_provider_response" not in node_detail.json()
        raw = client.get(f"/api/research-reports/{report_id}/trace/nodes/{raw_node_id}/raw-response")
        assert raw.status_code == 200
        assert raw.json()["unvalidated"] is True
        assert len(raw.json()["model_turns"]) == 1
        assert raw.json()["model_turns"][0]["returned_reasoning_fields"] == [
            "reasoning_content",
            "reasoning_details",
        ]
        assert raw.json()["model_turns"][0]["raw_provider_response"]["reasoning_details"]
        raw_invocation = client.get(f"/api/research-reports/{report_id}/trace/nodes/{invocation_node_id}/raw-response")
        assert raw_invocation.status_code == 200
        assert len(raw_invocation.json()["model_turns"]) == 1
    engine.dispose()


def test_theme_invocations_overlap_but_persist_in_stable_order(tmp_path) -> None:
    class ConcurrencyState:
        def __init__(self) -> None:
            self.lock = threading.Lock()
            self.barrier = threading.Barrier(2)
            self.active = 0
            self.max_active = 0

    class RoutedProvider:
        provider_name = "routed-fake"
        model = "routed-fake-model"
        temperature = 0.0

        def __init__(self, state: ConcurrencyState) -> None:
            self.state = state

        def complete(
            self,
            messages: list[dict[str, Any]],
            *,
            tools: list[dict[str, Any]],
            response_format: dict[str, Any],
        ) -> ProviderResponse:
            payload = json.loads(str(messages[1]["content"]))
            task = payload["task"]
            if task["stage"] == "theme":
                with self.state.lock:
                    self.state.active += 1
                    self.state.max_active = max(self.state.max_active, self.state.active)
                try:
                    self.state.barrier.wait(timeout=2)
                    time.sleep(0.02)
                finally:
                    with self.state.lock:
                        self.state.active -= 1
            rankings = []
            for rank, symbol in enumerate(task["symbols"], start=1):
                rankings.append(
                    {
                        "rank": rank,
                        "symbol": symbol,
                        "themes": [task.get("metadata", {}).get("theme")]
                        if task.get("metadata", {}).get("theme")
                        else [],
                        "action": "observe",
                        "strict_sepa_completeness": "not_evaluable",
                        "score": 0.5,
                        "confidence": 0.5,
                        "thesis": f"Bounded {task['stage']} result.",
                        "evidence": [],
                        "risks": [],
                        "missing_fields": [],
                        "invalidation_conditions": [],
                        "instrument_forecast": {
                            "direction": "up",
                            "probability": 0.55,
                            "expected_return_range_percent": {
                                "minimum_percent": 0.0,
                                "maximum_percent": 0.8,
                            },
                            "relative_to": "theme_benchmark",
                            "relative_direction": "inline",
                            "horizon": "final_hour",
                        },
                        "if_cash": {
                            "action": "wait",
                            "conviction": "low",
                            "reason": "The bounded fixture needs confirmation.",
                            "entry_condition": "Confirm strength.",
                        },
                        "if_held": {
                            "action": "hold",
                            "conviction": "low",
                            "reason": "No deterministic exit trigger fired.",
                            "take_profit_condition": None,
                            "stop_loss_condition": "Break support.",
                        },
                    }
                )
            output = {
                "schema_version": "urus.equity_decision.v3",
                "decision_phase": "pre_close",
                "agent_profile": "urus-preclose-strategist",
                "forecast_horizon": "final_hour",
                "forecast": None if task["stage"] == "theme" else {
                    "direction": "mixed",
                    "confidence": 0.5,
                    "expected_path": "A bounded test path.",
                },
                "review": None,
                "as_of": None,
                "status": "decision",
                "market_regime": {"classification": "neutral", "confidence": 0.5, "evidence": []},
                "rankings": rankings,
                "portfolio_warnings": [],
                "disclaimer": "Research output only; no order was placed.",
            }
            message = {"role": "assistant", "content": json.dumps(output)}
            return ProviderResponse(message=message, raw={"message": message}, model=self.model, usage={})

    evidence = _evidence()
    evidence["3a"] = {
        "instruments": [
            {"symbol": "QQQ", "asset_type": "etf", "theme": "ETF", "themes": ["ETF"]},
            {"symbol": "INTC", "asset_type": "equity", "theme": "半导体", "themes": ["半导体"]},
            {"symbol": "LITE", "asset_type": "equity", "theme": "光概念", "themes": ["光概念"]},
        ]
    }
    state = ConcurrencyState()
    engine, factory = create_database(f"sqlite:///{tmp_path / 'parallel.db'}")
    Base.metadata.create_all(bind=engine)
    with factory() as session:
        session.add(
            RunModel(
                id="workflow-parallel",
                run_type="pre_close",
                status="succeeded",
                cutoff_time=datetime(2026, 8, 3, 20, tzinfo=UTC),
            )
        )
        session.commit()
        response = UrusDecisionAdapter(
            session,
            Settings(
                urus_agent_theme_max_concurrency=2,
                urus_agent_enforce_stage_tools=False,
            ),
            provider_factory=lambda: RoutedProvider(state),
        ).decide(
            DecisionRequest(
                session_id="urus-workflow-parallel-step-4",
                workflow_run_id="workflow-parallel",
                evidence=evidence,
                symbols=["QQQ", "INTC", "LITE"],
                cutoff_time=datetime(2026, 8, 3, 20, tzinfo=UTC),
            )
        )

        assert state.max_active == 2
        assert response.decision_report is not None
        assert response.decision_report["status"] == "succeeded"
        assert [item["theme"] for item in response.decision_report["theme_analyses"]] == ["半导体", "光概念"]
        runs = session.query(AIDecisionRunModel).order_by(AIDecisionRunModel.sequence).all()
        assert [run.stage for run in runs] == ["market", "theme", "theme", "synthesis"]
        theme_nodes = (
            session.query(AITraceNodeModel)
                .filter(AITraceNodeModel.label.like("urus-preclose-strategist · Theme%"))
            .order_by(AITraceNodeModel.sequence)
            .all()
        )
        assert [node.label for node in theme_nodes] == [
            "urus-preclose-strategist · Theme · 半导体",
            "urus-preclose-strategist · Theme · 光概念",
        ]
    engine.dispose()


def test_option_structure_is_passed_to_synthesis_without_option_agent_calls(tmp_path) -> None:
    class InvocationState:
        def __init__(self) -> None:
            self.stages: list[str] = []
            self.synthesis_option_context: list[dict[str, Any]] = []

    class RoutedProvider:
        provider_name = "equity-option-context-fake"
        model = "equity-option-context-fake-model"
        temperature = 0.0

        def __init__(self, state: InvocationState) -> None:
            self.state = state

        def complete(
            self,
            messages: list[dict[str, Any]],
            *,
            tools: list[dict[str, Any]],
            response_format: dict[str, Any],
        ) -> ProviderResponse:
            payload = json.loads(str(messages[1]["content"]))
            task = payload["task"]
            self.state.stages.append(task["stage"])
            if task["stage"] == "synthesis":
                self.state.synthesis_option_context = task["metadata"]["equity_option_context"]
            output = _equity_output()
            output["rankings"] = [
                {
                    **_equity_output()["rankings"][0],
                    "rank": rank,
                    "symbol": symbol,
                }
                for rank, symbol in enumerate(task["symbols"], start=1)
            ]
            message = {"role": "assistant", "content": json.dumps(output)}
            return ProviderResponse(
                message=message,
                raw={"message": message},
                model=self.model,
                usage={},
            )

    evidence = _evidence()
    assert isinstance(evidence["2"], dict)
    option_payload = dict(evidence["2"])
    option_symbols = list(option_payload["symbols"])
    evidence["2"] = {
        **option_payload,
        "symbols": [
            option_symbols[0],
            {
                "symbol": "SPY",
                "spot": 750.0,
                "overview": {},
                "expirations": [
                    {
                        "expiration": "2026-08-07",
                        "days_to_expiry": 4,
                        "contract_count": 10,
                        "max_pain": 750.0,
                        "expected_move": {},
                        "exposure": {},
                        "spot_gamma_profile": {},
                    }
                ],
            },
        ],
    }
    evidence["3a"] = {
        "instruments": [
            {"symbol": "SPY", "asset_type": "etf", "theme": "ETF"},
            {"symbol": "QQQ", "asset_type": "etf", "theme": "ETF"},
        ]
    }
    state = InvocationState()
    engine, factory = create_database(f"sqlite:///{tmp_path / 'parallel-options.db'}")
    Base.metadata.create_all(bind=engine)
    with factory() as session:
        session.add(
            RunModel(
                id="workflow-parallel-options",
                run_type="pre_close",
                status="succeeded",
                cutoff_time=datetime(2026, 8, 3, 20, tzinfo=UTC),
            )
        )
        session.commit()
        response = UrusDecisionAdapter(
            session,
            Settings(
                urus_agent_enforce_stage_tools=False,
            ),
            provider_factory=lambda: RoutedProvider(state),
        ).decide(
            DecisionRequest(
                session_id="urus-workflow-parallel-options-step-4",
                workflow_run_id="workflow-parallel-options",
                evidence=evidence,
                symbols=["SPY", "QQQ"],
                cutoff_time=datetime(2026, 8, 3, 20, tzinfo=UTC),
            )
        )

        assert state.stages == ["market", "synthesis"]
        assert [item["symbol"] for item in state.synthesis_option_context] == ["SPY", "QQQ"]
        assert response.decision_report is not None
        assert response.decision_report["status"] == "succeeded"
        assert response.decision_report["option_decisions"] == []
        assert response.decision_report["candidate_gate"] == []
        assert [item["symbol"] for item in response.decision_report["equity_option_context"]] == ["SPY", "QQQ"]
        runs = session.query(AIDecisionRunModel).order_by(AIDecisionRunModel.sequence).all()
        assert [run.stage for run in runs] == ["market", "synthesis"]
    engine.dispose()
