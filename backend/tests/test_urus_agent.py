from datetime import UTC, datetime
import json

from app.urus_agent.contracts import (
    AgentTask,
    BusinessValidationError,
    response_schema_for,
    validate_business_output,
    validate_task_output_scope,
)
from app.urus_agent.contracts import validate_evidence_references
from app.urus_agent.providers import FakeLLMProvider
from app.urus_agent.runtime import (
    UrusAgentRuntime,
    _missing_stage_tool_requirements,
    _stage_prefetch_plan,
)
from app.urus_agent.tools import ToolRegistry
from app.urus_agent.tools.base import RegisteredTool, ToolContext
from app.urus_agent.contracts import ToolSpec
from app.urus_agent.evidence import EvidenceStore
from app.urus_agent.reports import build_objective_evaluation, select_option_candidates
from app.integrations.decision import DecisionRequest, UrusDecisionAdapter
from app.core.config import Settings
from app.core.database import Base, create_database
from app.models import AIDecisionRunModel
from app.repositories.agent import AIDecisionRepository
from app.urus_agent.prompts import load_task_prompt
from app.urus_agent.packet import build_stage_decision_packet
from app.urus_agent.prompts import load_agent_profile
import pytest


def _packet() -> dict:
    phase = {
        "run": {"id": "run-1", "run_type": "pre_close", "cutoff_time": "2026-08-03T20:00:00Z"},
        "snapshot": {"id": "snapshot-1", "quality_status": "ok"},
        "market": {
            "primary": {"symbol": "QQQ", "regular_price": 700.0, "is_mock": False},
            "cross_asset_quotes": [{"symbol": "QQQ", "regular_price": 700.0}],
            "technical": {},
            "quality_status": "ok",
        },
        "instruments": [{"symbol": "QQQ", "asset_type": "etf", "theme": "ETF", "themes": [], "is_mock": False}],
        "options": {"available": False, "symbols": [], "warnings": []},
        "data_quality": {"status": "ok"},
    }
    return {
        "schema_version": "urus.stage4b_decision_packet.v1",
        "source": {"dataset_key": "test-packet"},
        "quality": {"status": "ok", "warnings": [], "blocking_errors": []},
        "observations": {
            "pre_market": json.loads(json.dumps(phase)),
            "pre_close": json.loads(json.dumps(phase)),
        },
        "events": {"records": []},
        "content_sha256": "test-hash",
    }


def _task() -> AgentTask:
    return AgentTask(
        task_type="equity_ranking",
        dataset_key="test-packet",
        cutoff_time=datetime(2026, 8, 3, 20, tzinfo=UTC),
        symbols=["QQQ"],
        requested_skill="urus-equity-decision",
    )


def _daily_task(phase: str = "post_close_review") -> AgentTask:
    return _task().model_copy(
        update={
            "decision_phase": phase,
            "stage": "market",
            "metadata": {"daily_cycle": True},
        }
    )


def test_daily_cycle_response_schema_requires_exact_phase_contract() -> None:
    schema = response_schema_for(_daily_task())

    assert schema["properties"]["schema_version"]["enum"] == ["urus.equity_decision.v3"]
    assert schema["properties"]["decision_phase"]["enum"] == ["post_close_review"]
    assert schema["properties"]["agent_profile"]["enum"] == ["urus-postclose-reviewer"]
    assert schema["properties"]["forecast_horizon"]["enum"] == ["completed_session"]
    assert schema["properties"]["forecast"] == {"type": "null"}
    assert schema["properties"]["review"] == {"$ref": "#/$defs/DailyReview"}
    assert {
        "schema_version",
        "decision_phase",
        "agent_profile",
        "forecast_horizon",
        "forecast",
        "review",
    }.issubset(schema["required"])

    pre_close_schema = response_schema_for(_daily_task("pre_close"))
    assert pre_close_schema["properties"]["forecast"] == {"$ref": "#/$defs/PhaseForecast"}
    assert pre_close_schema["properties"]["review"] == {"type": "null"}
    ranking = pre_close_schema["$defs"]["EquityRanking"]
    assert {"instrument_forecast", "if_cash", "if_held"}.issubset(ranking["required"])

    current_schema = response_schema_for(_daily_task("current_state"))
    assert current_schema["properties"]["decision_phase"]["enum"] == ["current_state"]
    assert current_schema["properties"]["agent_profile"]["enum"] == [
        "urus-current-state-analyst"
    ]
    assert current_schema["properties"]["forecast_horizon"]["enum"] == ["current_state"]
    assert current_schema["properties"]["forecast"] == {"type": "null"}
    assert current_schema["properties"]["review"] == {"type": "null"}
    current_ranking = current_schema["$defs"]["EquityRanking"]
    for field in ("instrument_forecast", "if_cash", "if_held"):
        assert current_ranking["properties"][field] == {"type": "null"}

    option_task = _daily_task("pre_close").model_copy(update={
        "task_type": "options_structure",
        "stage": "options",
        "target_symbol": "QQQ",
        "requested_skill": "urus-options-decision",
        "metadata": {"daily_cycle": True, "required_expiration": "2026-08-07"},
    })
    option_schema = response_schema_for(option_task)
    horizon = option_schema["$defs"]["OptionHorizon"]
    assert horizon["properties"]["expiration"]["enum"] == ["2026-08-07"]
    assert "expiration" in horizon["required"]


def test_manual_current_state_uses_compact_non_trading_response_schema() -> None:
    task = _task().model_copy(
        update={
            "decision_phase": "current_state",
            "stage": "synthesis",
            "metadata": {"scope_kind": "manual_current_state", "daily_cycle": False},
        }
    )

    schema = response_schema_for(task)

    assert schema["properties"]["schema_version"]["enum"] == [
        "urus.equity_decision.v1"
    ]
    assert "forecast" not in schema["properties"]
    assert "review" not in schema["properties"]
    ranking = schema["$defs"]["EquityRanking"]
    assert "instrument_forecast" not in ranking["properties"]
    assert "if_cash" not in ranking["properties"]
    assert "if_held" not in ranking["properties"]


def test_daily_cycle_validation_rejects_omitted_phase_fields_before_defaults() -> None:
    output = {
        "schema_version": "urus.equity_decision.v3",
        "status": "decision",
        "market_regime": {"classification": "neutral", "confidence": 0.5, "evidence": []},
        "rankings": [],
        "portfolio_warnings": [],
        "disclaimer": "Research output only; no order was placed.",
    }

    with pytest.raises(BusinessValidationError, match="daily-cycle output is missing required fields"):
        validate_business_output(_daily_task(), output)


def test_post_close_objective_evaluation_is_computed_from_prices() -> None:
    packet = _packet()
    baseline = packet["observations"]["pre_market"]
    baseline["market"]["primary"]["regular_price"] = 100.0
    baseline["instruments"] = [{
        "symbol": "INTC",
        "quote": {"regular_price": 20.0},
    }]
    close = json.loads(json.dumps(baseline))
    close["market"]["primary"]["regular_price"] = 101.0
    close["instruments"][0]["quote"]["regular_price"] = 19.0
    packet["observations"]["post_close_review"] = close
    packet["decision_context"] = {"current_observation": "post_close_review"}
    packet["prior_reports"] = {
        "same_day_pre_market": {
            "report_id": "report-pre",
            "forecast": {"direction": "bullish", "confidence": 0.8},
            "rankings": [{
                "symbol": "INTC",
                "instrument_forecast": {
                    "direction": "down",
                    "probability": 0.7,
                    "expected_return_range_percent": {
                        "minimum_percent": -6.0,
                        "maximum_percent": -4.0,
                    },
                    "relative_to": "QQQ",
                },
            }],
        },
        "same_day_pre_close": None,
    }

    result = build_objective_evaluation(
        EvidenceStore(packet), decision_phase="post_close_review"
    )

    assert result["status"] == "completed"
    assert result["phase_evaluations"][0]["verdict"] == "hit"
    assert result["phase_evaluations"][0]["actual_return_percent"] == 1.0
    assert len(result["phase_evaluations"]) == 1
    instrument = result["instrument_results"][0]
    assert instrument["verdict"] == "hit"
    assert instrument["actual_return_percent"] == -5.0
    assert instrument["expected_range_hit"] is True
    assert instrument["relative_return_percent"] == -6.0


def test_post_close_objective_evaluation_uses_phase_specific_prices() -> None:
    packet = _packet()
    baseline = packet["observations"]["pre_market"]
    baseline["market"]["primary"].update({
        "session": "premarket",
        "last_price": 90.0,
        "premarket_price": 90.0,
        "regular_price": 100.0,
    })
    baseline["instruments"] = [{
        "symbol": "INTC",
        "quote": {
            "last_price": 20.0,
            "premarket_price": 18.0,
            "regular_price": 20.0,
        },
    }]
    close = json.loads(json.dumps(baseline))
    close["market"]["primary"].update({
        "session": "afterhours",
        "last_price": 102.0,
        "afterhours_price": 102.0,
        "regular_price": 100.0,
    })
    close["instruments"][0]["quote"].update({
        "last_price": 21.0,
        "afterhours_price": 21.0,
        "regular_price": 20.0,
    })
    packet["observations"]["post_close_review"] = close
    packet["prior_reports"] = {
        "same_day_pre_market": {
            "report_id": "report-pre",
            "forecast": {"direction": "bullish", "confidence": 0.8},
            "rankings": [{
                "symbol": "INTC",
                "instrument_forecast": {
                    "direction": "up",
                    "probability": 0.7,
                    "expected_return_range_percent": {
                        "minimum_percent": 10.0,
                        "maximum_percent": 12.0,
                    },
                    "relative_to": "QQQ",
                },
            }],
        },
        "same_day_pre_close": None,
    }

    result = build_objective_evaluation(
        EvidenceStore(packet), decision_phase="post_close_review"
    )

    assert result["phase_evaluations"][0]["actual_return_percent"] == pytest.approx(11.111111)
    instrument = result["instrument_results"][0]
    assert instrument["start_price"] == 18.0
    assert instrument["end_price"] == 20.0
    assert instrument["actual_direction"] == "up"
    assert instrument["verdict"] == "hit"


def test_post_close_objective_evaluation_rejects_premarket_snapshot() -> None:
    packet = _packet()
    baseline = packet["observations"]["pre_market"]
    baseline["market"]["primary"]["session"] = "premarket"
    close = json.loads(json.dumps(baseline))
    close["market"]["primary"]["session"] = "premarket"
    packet["observations"]["post_close_review"] = close
    packet["prior_reports"] = {
        "same_day_pre_market": {"report_id": "report-pre"},
        "same_day_pre_close": {"report_id": "report-close"},
    }

    result = build_objective_evaluation(
        EvidenceStore(packet), decision_phase="post_close_review"
    )

    assert result["status"] == "unavailable"
    assert "premarket" in result["reason"]
    assert result["instrument_results"] == []
    assert all(item["verdict"] == "unscorable" for item in result["phase_evaluations"])


def test_three_daily_schedules_preserve_expected_report_lineage() -> None:
    observation = _packet()["observations"]["pre_close"]
    report_1 = {
        "report_id": "report-1",
        "decision_phase": "pre_market",
        "status": "succeeded",
    }
    report_2 = {
        "report_id": "report-2",
        "decision_phase": "pre_close",
        "status": "succeeded",
    }
    common = {
        "label": "daily test",
        "captured_at": datetime(2026, 8, 3, 21, tzinfo=UTC),
        "trading_date": "2026-08-03",
        "events": [],
    }

    pre_market = build_stage_decision_packet(
        dataset_key="daily:pre-market",
        decision_phase="pre_market",
        observations={"pre_market": observation},
        prior_reports={"previous_post_close": None},
        agent_profile=load_agent_profile("pre_market"),
        **common,
    )
    assert pre_market["decision_context"]["missing_lineage"] == ["previous_post_close"]
    assert pre_market["quality"]["blocking_errors"] == []

    pre_close = build_stage_decision_packet(
        dataset_key="daily:pre-close",
        decision_phase="pre_close",
        observations={"pre_market": observation, "pre_close": observation},
        prior_reports={"previous_post_close": None, "same_day_pre_market": report_1},
        agent_profile=load_agent_profile("pre_close"),
        **common,
    )
    assert pre_close["prior_reports"]["same_day_pre_market"]["report_id"] == "report-1"

    post_close = build_stage_decision_packet(
        dataset_key="daily:post-close",
        decision_phase="post_close_review",
        observations={
            "pre_market": observation,
            "pre_close": observation,
            "post_close_review": observation,
        },
        prior_reports={
            "previous_post_close": None,
            "same_day_pre_market": report_1,
            "same_day_pre_close": report_2,
        },
        agent_profile=load_agent_profile("post_close_review"),
        **common,
    )
    assert post_close["prior_reports"]["same_day_pre_market"]["report_id"] == "report-1"
    assert post_close["prior_reports"]["same_day_pre_close"]["report_id"] == "report-2"


def test_agent_runtime_executes_allowed_tool_and_validates_json() -> None:
    provider = FakeLLMProvider(
        [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "get_market_regime", "arguments": '{"phase":"pre_close","symbols":["QQQ"]}'},
                        }
                    ],
                }
            },
            {
                "message": {
                    "role": "assistant",
                    "content": '{"schema_version":"urus.equity_decision.v1","as_of":null,"status":"decision","market_regime":{"classification":"neutral","confidence":0.5,"evidence":[]},"rankings":[{"rank":1,"symbol":"QQQ","themes":["ETF"],"action":"observe","strict_sepa_completeness":"not_evaluable","score":0.5,"confidence":0.5,"thesis":"Evidence is limited.","evidence":[],"risks":[],"missing_fields":["fundamentals"],"invalidation_conditions":[]}],"portfolio_warnings":[],"disclaimer":"Research output only; no order was placed."}',
                }
            },
        ]
    )
    result = UrusAgentRuntime(provider).decide(_task(), _packet())

    assert result.status == "succeeded"
    assert result.output["rankings"][0]["symbol"] == "QQQ"
    assert result.tool_call_count == 1
    assert provider.requests[0]["tools"]


def test_tool_registry_blocks_option_tool_for_equity_skill() -> None:
    task = _task()
    context = ToolContext(task=task, evidence=EvidenceStore(_packet()))
    result = ToolRegistry().call("get_option_overview", {"symbol": "QQQ", "phase": "pre_close"}, context)

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "tool_not_allowed"


def test_stage_prompts_define_distinct_analysis_responsibilities() -> None:
    market = load_task_prompt("market")
    theme = load_task_prompt("theme")
    synthesis = load_task_prompt("synthesis")
    options = load_task_prompt("options")

    assert "SPY/QQQ participation" in market
    assert "relative strength versus QQQ" in theme
    assert "no data tools are available" in synthesis
    assert "do not mix DEX, GEX" in options
    assert len({market, theme, synthesis, options}) == 4


def test_market_stage_requires_two_phases_quality_and_macro_events() -> None:
    task = _task().model_copy(update={
        "stage": "market",
        "metadata": {
            "current_observation": "pre_close",
            "comparison_observations": ["pre_market", "pre_close"],
        },
    })
    calls = [
        {"name": "get_market_regime", "arguments": {"phase": "pre_market"}, "result": {"ok": True}},
        {"name": "get_market_regime", "arguments": {"phase": "pre_close"}, "result": {"ok": True}},
        {"name": "get_systematic_flows", "arguments": {"phase": "pre_market"}, "result": {"ok": True}},
        {"name": "get_systematic_flows", "arguments": {"phase": "pre_close"}, "result": {"ok": True}},
        {"name": "get_data_quality", "arguments": {"scope": "market"}, "result": {"ok": True}},
        {"name": "get_prior_stage_reports", "arguments": {}, "result": {"ok": True}},
        {"name": "get_events", "arguments": {"category": "macro"}, "result": {"ok": True}},
        {
            "name": "get_instrument_snapshot",
            "arguments": {"symbol": "QQQ", "phase": "pre_close"},
            "result": {"ok": True},
        },
    ]

    assert _missing_stage_tool_requirements(task, [], "{}") == [
        "get_market_regime phase=pre_market",
        "get_market_regime phase=pre_close",
        "get_systematic_flows phase=pre_market",
        "get_systematic_flows phase=pre_close",
        "get_data_quality",
        "get_prior_stage_reports",
        "get_events category=macro",
        "get_instrument_snapshot symbol=QQQ phase=pre_close",
    ]
    assert _missing_stage_tool_requirements(task, calls, "{}") == []
    assert [name for name, _args in _stage_prefetch_plan(task)] == [
        "get_market_regime",
        "get_market_regime",
        "get_systematic_flows",
        "get_systematic_flows",
        "get_data_quality",
        "get_prior_stage_reports",
        "get_events",
        "get_instrument_snapshot",
    ]


def test_options_prefetch_selects_nearest_positive_dte_structure() -> None:
    task = _task().model_copy(
        update={
            "task_type": "options_structure",
            "stage": "options",
            "requested_skill": "urus-options-decision",
            "symbols": ["QQQ"],
            "target_symbol": "QQQ",
            "metadata": {
                "current_observation": "pre_close",
                "comparison_observations": ["pre_market", "pre_close"],
            },
        }
    )
    packet = _packet()
    packet["observations"]["pre_close"]["options"] = {
        "symbols": [{
            "symbol": "QQQ",
            "expirations": [
                {"expiration": "2026-08-03", "days_to_expiry": 0},
                {"expiration": "2026-08-10", "days_to_expiry": 7},
            ],
        }],
    }
    plan = _stage_prefetch_plan(task, EvidenceStore(packet))
    assert plan[-3:] == [
        ("get_option_expiration_structure", {"symbol": "QQQ", "phase": "pre_close", "expiration": "2026-08-10"}),
        ("get_option_expiration_structure", {"symbol": "QQQ", "phase": "pre_market", "expiration": "2026-08-10"}),
        ("compare_option_observations", {"symbol": "QQQ", "expiration": "2026-08-10"}),
    ]


def test_available_option_expiration_rejects_empty_insufficient_data() -> None:
    task = _task().model_copy(
        update={
            "task_type": "options_structure",
            "stage": "options",
            "requested_skill": "urus-options-decision",
            "symbols": ["QQQ"],
            "target_symbol": "QQQ",
        }
    )
    packet = _packet()
    packet["observations"]["pre_close"]["options"] = {
        "symbols": [{"symbol": "QQQ", "expirations": [{"expiration": "2026-08-10"}]}],
    }
    output = {
        "schema_version": "urus.options_decision.v1",
        "symbol": "QQQ",
        "as_of": None,
        "status": "insufficient_data",
        "gamma_regime": "unknown",
        "thesis": "",
        "horizon": {"expiration": None, "days_to_expiry": None},
        "structure": {"kind": "none", "execution_ready": False, "legs": [], "net_debit_or_credit": None, "max_profit": None, "max_loss": None, "breakevens": []},
        "scenario_anchors": {"spot": None, "expected_move": None, "max_pain": None, "primary_gamma_flip": None, "call_wall": None, "put_wall": None},
        "confidence": 0.0,
        "evidence": [],
        "uncertainties": [],
        "invalidation_conditions": [],
        "disclaimer": "Research output only; no order was placed.",
    }
    with pytest.raises(BusinessValidationError, match="available expirations"):
        validate_task_output_scope(task, output, EvidenceStore(packet))


def test_theme_and_synthesis_outputs_must_cover_their_entire_scope() -> None:
    task = _task().model_copy(update={"stage": "theme", "symbols": ["QQQ", "INTC"]})
    output = {
        "schema_version": "urus.equity_decision.v1",
        "as_of": None,
        "status": "decision",
        "market_regime": {"classification": "neutral", "confidence": 0.5, "evidence": []},
        "rankings": [{
            "rank": 1, "symbol": "QQQ", "themes": [], "action": "observe",
            "strict_sepa_completeness": "not_evaluable", "score": 0.5, "confidence": 0.5,
            "thesis": "bounded", "evidence": [], "risks": [], "missing_fields": [],
            "invalidation_conditions": [],
        }],
        "portfolio_warnings": [],
        "disclaimer": "Research output only; no order was placed.",
    }

    with pytest.raises(BusinessValidationError, match="must cover every task symbol"):
        validate_business_output(task, output)

    market_task = task.model_copy(update={"stage": "market"})
    with pytest.raises(BusinessValidationError, match="must cover every task symbol"):
        validate_business_output(market_task, output)


def test_manual_current_state_rankings_may_be_a_focused_subset() -> None:
    task = _task().model_copy(
        update={
            "stage": "synthesis",
            "decision_phase": "current_state",
            "symbols": ["QQQ", "AAPL"],
            "metadata": {"daily_cycle": True, "scope_kind": "manual_current_state"},
        }
    )
    output = {
        "schema_version": "urus.equity_decision.v3",
        "decision_phase": "current_state",
        "agent_profile": "urus-current-state-analyst",
        "forecast_horizon": "current_state",
        "forecast": None,
        "review": None,
        "as_of": None,
        "status": "decision",
        "market_regime": {"classification": "neutral", "confidence": 0.5, "evidence": []},
        "rankings": [{
            "rank": 1, "symbol": "QQQ", "themes": [], "action": "observe",
            "strict_sepa_completeness": "not_evaluable", "score": 0.5, "confidence": 0.5,
            "thesis": "material current signal", "evidence": [], "risks": [],
            "missing_fields": [], "invalidation_conditions": [],
            "instrument_forecast": None, "if_cash": None, "if_held": None,
        }],
        "portfolio_warnings": [],
        "disclaimer": "Research output only; no order was placed.",
    }

    validated = validate_business_output(task, output)

    assert [item["symbol"] for item in validated["rankings"]] == ["QQQ"]


def test_ranking_numbers_are_normalized_to_model_order() -> None:
    task = _task().model_copy(update={"stage": "market", "symbols": ["QQQ"]})
    output = {
        "schema_version": "urus.equity_decision.v1",
        "as_of": None,
        "status": "decision",
        "market_regime": {"classification": "neutral", "confidence": 0.5, "evidence": []},
        "rankings": [{
            "rank": 7, "symbol": "QQQ", "themes": [], "action": "observe",
            "strict_sepa_completeness": "not_evaluable", "score": 0.5, "confidence": 0.5,
            "thesis": "bounded", "evidence": [], "risks": [], "missing_fields": [],
            "invalidation_conditions": [],
        }],
        "portfolio_warnings": [],
        "disclaimer": "Research output only; no order was placed.",
    }

    assert validate_business_output(task, output)["rankings"][0]["rank"] == 1


def test_tool_registry_enforces_invocation_symbol_scope() -> None:
    theme_task = _task().model_copy(update={"stage": "theme", "symbols": ["QQQ"]})
    context = ToolContext(task=theme_task, evidence=EvidenceStore(_packet()))
    result = ToolRegistry().call(
        "get_instrument_snapshot",
        {"symbol": "INTC", "phase": "pre_close"},
        context,
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "tool_scope_violation"

    option_task = _task().model_copy(
        update={
            "task_type": "options_structure",
            "stage": "options",
            "requested_skill": "urus-options-decision",
            "symbols": ["QQQ"],
            "target_symbol": "QQQ",
        }
    )
    option_context = ToolContext(task=option_task, evidence=EvidenceStore(_packet()))
    option_result = ToolRegistry().call(
        "get_option_overview",
        {"symbol": "INTC", "phase": "pre_close"},
        option_context,
    )

    assert option_result.ok is False
    assert option_result.error is not None
    assert option_result.error.code == "tool_scope_violation"


def test_instrument_tools_read_nested_quote_from_compact_packet() -> None:
    packet = _packet()
    packet["decision_context"] = {
        "current_observation": "pre_close",
        "comparison_observations": ["pre_market", "pre_close"],
    }
    packet["observations"]["pre_market"]["instruments"][0]["quote"] = {
        "symbol": "QQQ", "regular_price": 690.0, "volume": 100,
    }
    packet["observations"]["pre_close"]["instruments"][0]["quote"] = {
        "symbol": "QQQ", "regular_price": 700.0, "volume": 200,
    }
    store = EvidenceStore(packet)

    snapshot = store.instrument_snapshot("QQQ", "pre_close", ["quote"])
    comparison = store.compare_instrument("QQQ")

    assert snapshot["data"]["regular_price"] == 700.0
    assert comparison["data"]["regular_price"]["absolute"] == 10.0


def test_evidence_reference_must_resolve_to_frozen_packet() -> None:
    valid = {
        "schema_version": "urus.equity_decision.v1",
        "as_of": None,
        "status": "decision",
        "market_regime": {
            "classification": "neutral",
            "confidence": 0.5,
            "evidence": [{"path": "observations.pre_close.market.primary.regular_price", "observation": "QQQ spot"}],
        },
        "rankings": [{
            "rank": 1, "symbol": "QQQ", "themes": [], "action": "observe",
            "strict_sepa_completeness": "not_evaluable", "score": 0.5, "confidence": 0.5,
            "thesis": "bounded", "evidence": [], "risks": [], "missing_fields": [],
            "invalidation_conditions": [],
        }],
        "portfolio_warnings": [],
        "disclaimer": "Research output only; no order was placed.",
    }
    result = UrusAgentRuntime(FakeLLMProvider([{"message": {"role": "assistant", "content": json.dumps(valid)}}])).decide(_task(), _packet())
    assert result.status == "succeeded"

    invalid = json.loads(json.dumps(valid))
    invalid["market_regime"]["evidence"][0]["path"] = "observations.pre_close.market.primary.not_a_field"
    result = UrusAgentRuntime(FakeLLMProvider([
        {"message": {"role": "assistant", "content": json.dumps(invalid)}},
        {"message": {"role": "assistant", "content": json.dumps(invalid)}},
    ])).decide(_task(), _packet())
    assert result.status == "failed"
    assert result.error_code == "business_validation_failed"

    with pytest.raises(BusinessValidationError, match="does not resolve"):
        validate_evidence_references(
            _task(),
            invalid,
            EvidenceStore(_packet()),
            {"observations.pre_close.market.primary"},
        )


def test_business_validation_gets_one_bounded_correction_turn() -> None:
    invalid = {
        "schema_version": "urus.equity_decision.v1",
        "as_of": None,
        "status": "decision",
        "market_regime": {
            "classification": "neutral",
            "confidence": 0.5,
            "evidence": [{"path": "overview.market.regular_price", "observation": "QQQ spot"}],
        },
        "rankings": [{
            "rank": 1, "symbol": "QQQ", "themes": [], "action": "observe",
            "strict_sepa_completeness": "not_evaluable", "score": 0.5, "confidence": 0.5,
            "thesis": "bounded", "evidence": [], "risks": [], "missing_fields": [],
            "invalidation_conditions": [],
        }],
        "portfolio_warnings": [],
        "disclaimer": "Research output only; no order was placed.",
    }
    corrected = json.loads(json.dumps(invalid))
    corrected["market_regime"]["evidence"][0]["path"] = (
        "observations.pre_close.market.primary.regular_price"
    )
    provider = FakeLLMProvider([
        {"message": {"role": "assistant", "content": json.dumps(invalid)}},
        {"message": {"role": "assistant", "content": json.dumps(corrected)}},
    ])

    result = UrusAgentRuntime(provider).decide(_task(), _packet())

    assert result.status == "succeeded"
    assert len(provider.requests) == 2
    assert provider.requests[1]["tools"] == []


def test_candidate_gate_blocks_options_when_packet_has_blocking_quality_error() -> None:
    packet = _packet()
    packet["quality"] = {"status": "blocked", "warnings": [], "blocking_errors": ["missing quote"]}
    candidates = select_option_candidates(
        {
            "rankings": [{"rank": 1, "symbol": "QQQ", "action": "setup_ready", "score": 0.9, "confidence": 0.9}],
        },
        EvidenceStore(packet),
        policy="etf_plus_ranked",
        max_symbols=2,
        min_score=0.55,
        min_confidence=0.5,
        etf_symbols=["QQQ"],
    )

    assert candidates == [{
        "symbol": "QQQ",
        "source_rank": 1,
        "score": 0.9,
        "confidence": 0.9,
        "selected": False,
        "reason": "data_quality_blocked",
    }]


def test_candidate_gate_requires_cash_scenario_to_buy() -> None:
    packet = _packet()
    packet["observations"]["pre_close"]["options"] = {
        "symbols": [{"symbol": "QQQ", "expirations": []}],
    }
    candidates = select_option_candidates(
        {
            "rankings": [{
                "rank": 1,
                "symbol": "QQQ",
                "action": "setup_ready",
                "score": 0.9,
                "confidence": 0.9,
                "if_cash": {"action": "avoid"},
            }],
        },
        EvidenceStore(packet),
        policy="ranked",
        max_symbols=1,
        min_score=0.55,
        min_confidence=0.5,
        etf_symbols=["QQQ"],
    )

    assert candidates[0]["selected"] is False
    assert candidates[0]["reason"] == "cash_scenario_not_buy"


def test_runtime_enforces_cumulative_tool_result_budget() -> None:
    class LargeToolRegistry(ToolRegistry):
        def __init__(self) -> None:
            self.event_limit = 10
            self._tools = {
                "large": RegisteredTool(
                    ToolSpec(name="large", description="test", parameters={"type": "object", "properties": {}, "additionalProperties": False}),
                    lambda _context, _args: {"tool_schema_version": "urus.agent_tool_result.v1", "ok": True, "tool": "large", "data": {"blob": "x" * 20_000}, "truncated": False},
                    frozenset({"urus-equity-decision"}),
                )
            }

    provider = FakeLLMProvider([{
        "message": {
            "role": "assistant",
            "tool_calls": [{"id": "large-1", "type": "function", "function": {"name": "large", "arguments": "{}"}}],
        }
    }])
    result = UrusAgentRuntime(provider, registry=LargeToolRegistry(), max_total_tool_result_bytes=10_000).decide(_task(), _packet())

    assert result.status == "failed"
    assert result.error_code == "tool_result_budget_exceeded"
    assert result.tool_calls[0]["result"]["error"]["code"] == "tool_result_budget_exceeded"


def test_option_payoff_tool_is_incomplete_without_premiums() -> None:
    task = _task().model_copy(update={"task_type": "options_structure", "requested_skill": "urus-options-decision", "target_symbol": "QQQ"})
    context = ToolContext(task=task, evidence=EvidenceStore(_packet()))
    result = ToolRegistry().call(
        "calculate_option_payoff",
        {"prices": [95, 100, 105], "legs": [{"side": "buy", "option_type": "call", "strike": 100, "quantity": 1}]},
        context,
    )

    assert result.ok is True
    assert result.data["complete"] is False
    assert result.data["max_profit"] is None


def test_option_payoff_requires_multiplier_and_does_not_fake_calendar_economics() -> None:
    task = _task().model_copy(update={"task_type": "options_structure", "requested_skill": "urus-options-decision", "target_symbol": "QQQ"})
    context = ToolContext(task=task, evidence=EvidenceStore(_packet()))
    registry = ToolRegistry()
    missing_multiplier = registry.call(
        "calculate_option_payoff",
        {
            "prices": [95, 100, 105],
            "legs": [
                {"side": "buy", "option_type": "call", "strike": 100, "quantity": 1, "premium": 2},
            ],
        },
        context,
    )
    assert missing_multiplier.ok is True
    assert missing_multiplier.data["complete"] is False
    assert "multiplier" in missing_multiplier.data["missing_fields"]
    calendar = registry.call(
        "calculate_option_payoff",
        {
            "prices": [95, 100, 105],
            "multiplier": 100,
            "legs": [
                {"side": "buy", "option_type": "call", "strike": 100, "quantity": 1, "premium": 2, "expiration": "2026-08-07"},
                {"side": "sell", "option_type": "call", "strike": 100, "quantity": 1, "premium": 3, "expiration": "2026-09-18"},
            ],
        },
        context,
    )
    assert calendar.ok is True
    assert calendar.data["complete"] is False
    assert "calendar_valuation" in calendar.data["missing_fields"]
    assert calendar.data["max_profit"] is None


def test_decision_audit_persists_result_and_tool_trace(tmp_path) -> None:
    engine, factory = create_database(f"sqlite:///{tmp_path / 'agent.db'}")
    Base.metadata.create_all(bind=engine)
    provider = FakeLLMProvider([{"message": {"role": "assistant", "content": '{"schema_version":"urus.equity_decision.v1","as_of":null,"status":"decision","market_regime":{"classification":"neutral","confidence":0.5,"evidence":[]},"rankings":[{"rank":1,"symbol":"QQQ","themes":[],"action":"observe","strict_sepa_completeness":"not_evaluable","score":0.5,"confidence":0.5,"thesis":"limited","evidence":[],"risks":[],"missing_fields":[],"invalidation_conditions":[]}],"portfolio_warnings":[],"disclaimer":"Research output only; no order was placed."}'}}])
    result = UrusAgentRuntime(provider).decide(_task(), _packet())
    with factory() as session:
        model = AIDecisionRepository(session).record(_task(), result)
        assert model.status == "succeeded"
        assert model.input_hash == "test-hash"
    engine.dispose()


def test_workflow_evidence_accepts_serialized_step_payloads() -> None:
    store = EvidenceStore.from_workflow_results(
        run_id="run-serialized",
        cutoff_time=datetime(2026, 8, 3, 20, tzinfo=UTC),
        results={
            "1a": {"symbol": "QQQ", "regular_price": 700.0, "is_mock": False},
            "3a": {"instruments": [{"symbol": "INTC", "last_price": 30.0}]},
            "2": {"symbols": []},
            "1b": {"events": []},
            "3b": {"events": []},
        },
    )

    assert store.overview()["market"]["symbol"] == "QQQ"
    assert store.overview()["symbols"][0]["symbol"] == "INTC"


def test_frozen_event_tool_keeps_future_scheduled_risk() -> None:
    packet = _packet()
    packet["events"] = {
        "records": [
            {
                "id": "future-earnings",
                "category": "instrument",
                "subject": "QQQ",
                "status": "scheduled",
                "scheduled_at": "2026-08-10T20:00:00Z",
                "title": "Future event known at cutoff",
            }
        ]
    }
    result = EvidenceStore(packet).events(
        "instrument",
        "QQQ",
        [],
        "any",
        10,
        cutoff_time=datetime(2026, 8, 3, 20, tzinfo=UTC),
        allowed_subjects={"QQQ"},
    )

    assert [event["id"] for event in result["data"]["records"]] == ["future-earnings"]


def test_urus_decision_adapter_records_a_real_fake_decision(tmp_path) -> None:
    engine, factory = create_database(f"sqlite:///{tmp_path / 'adapter.db'}")
    Base.metadata.create_all(bind=engine)
    output = {
        "schema_version": "urus.equity_decision.v3",
        "decision_phase": "pre_close",
        "agent_profile": "urus-preclose-strategist",
        "forecast_horizon": "final_hour",
        "forecast": {
            "direction": "mixed",
            "confidence": 0.0,
            "expected_path": "Test fixture.",
        },
        "review": None,
        "as_of": None,
        "status": "decision",
        "market_regime": {"classification": "unknown", "confidence": 0.0, "evidence": []},
        "rankings": [{
            "rank": 1,
            "symbol": "INTC",
            "themes": [],
            "action": "observe",
            "strict_sepa_completeness": "not_evaluable",
            "score": 0.0,
            "confidence": 0.0,
            "thesis": "fake",
            "evidence": [],
            "risks": [],
            "missing_fields": [],
            "invalidation_conditions": [],
            "instrument_forecast": {
                "direction": "flat",
                "probability": 0.5,
                "expected_return_range_percent": {
                    "minimum_percent": -0.2,
                    "maximum_percent": 0.2,
                },
                "relative_to": "QQQ",
                "relative_direction": "inline",
                "horizon": "final_hour",
            },
            "if_cash": {
                "action": "wait",
                "conviction": "low",
                "reason": "No entry edge in the fixture.",
                "entry_condition": "Wait for confirmation.",
            },
            "if_held": {
                "action": "hold",
                "conviction": "low",
                "reason": "No exit trigger in the fixture.",
                "take_profit_condition": None,
                "stop_loss_condition": "Break support.",
            },
        }],
        "portfolio_warnings": [],
        "disclaimer": "Research output only; no order was placed.",
    }
    theme_output = json.loads(json.dumps(output))
    theme_output["forecast"] = None
    provider = FakeLLMProvider([
        {"message": {"role": "assistant", "content": json.dumps(output)}},
        {"message": {"role": "assistant", "content": json.dumps(theme_output)}},
        {"message": {"role": "assistant", "content": json.dumps(output)}},
    ])
    with factory() as session:
        adapter = UrusDecisionAdapter(
            session,
            Settings(urus_agent_enforce_stage_tools=False),
            provider=provider,
        )
        response = adapter.decide(DecisionRequest(session_id="urus-run-step-4", evidence={"3a": {"instruments": [{"symbol": "INTC"}]}}, symbols=["INTC"], cutoff_time=datetime(2026, 8, 3, 20, tzinfo=UTC)))
        assert response.is_mock is False
        assert response.result.status == "succeeded"
        assert session.query(AIDecisionRunModel).count() == 3
    engine.dispose()
