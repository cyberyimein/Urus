from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


TaskType = Literal["equity_ranking", "options_structure"]
DecisionPhase = Literal["pre_market", "pre_close", "post_close_review", "current_state"]
Phase = DecisionPhase
AgentRunStatus = Literal["pending", "running", "succeeded", "failed", "timed_out"]


class AgentTask(BaseModel):
    """Immutable description of one bounded research decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_type: TaskType
    dataset_key: str
    source_run_ids: list[str] = Field(default_factory=list)
    source_snapshot_ids: list[str] = Field(default_factory=list)
    cutoff_time: datetime
    symbols: list[str] = Field(default_factory=list)
    target_symbol: str | None = None
    requested_skill: str
    requested_horizon: str = "swing"
    workflow_run_id: str | None = None
    decision_session_id: str | None = None
    decision_run_id: str | None = None
    parent_decision_run_id: str | None = None
    decision_phase: DecisionPhase = "pre_close"
    stage: Literal["equity", "market", "theme", "synthesis", "options"] = "equity"
    sequence: int = 1
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbols", mode="before")
    @classmethod
    def normalize_symbols(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = value.split(",")
        return list(dict.fromkeys(str(item).strip().upper() for item in value if str(item).strip()))

    @field_validator("target_symbol", mode="before")
    @classmethod
    def normalize_target_symbol(cls, value: Any) -> str | None:
        if value is None or not str(value).strip():
            return None
        return str(value).strip().upper()

    @model_validator(mode="after")
    def validate_task_shape(self) -> "AgentTask":
        if not self.dataset_key.strip():
            raise ValueError("dataset_key is required")
        if self.task_type == "options_structure" and not self.target_symbol:
            raise ValueError("options_structure requires target_symbol")
        return self


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_key: str
    run_id: str | None = None
    snapshot_id: str | None = None
    phase: str | None = None
    path: str | None = None
    as_of: str | None = None
    cutoff_time: str | None = None
    provider: str | None = None
    source_mode: str | None = None


class QualityInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "unknown"
    warnings: list[str] = Field(default_factory=list)
    is_mock: bool = False


class ToolError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    retryable: bool = False


class AgentToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_schema_version: str = "urus.agent_tool_result.v1"
    ok: bool
    tool: str
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: EvidenceRef | None = None
    quality: QualityInfo = Field(default_factory=QualityInfo)
    error: ToolError | None = None
    truncated: bool = False
    next_cursor: str | None = None


class ToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str
    parameters: dict[str, Any]

    def as_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class DecisionResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: AgentRunStatus
    output: dict[str, Any] = Field(default_factory=dict)
    raw_output: str | None = None
    provider: str = "unknown"
    model: str | None = None
    skill_name: str | None = None
    skill_hash: str | None = None
    tool_call_count: int = 0
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    model_turns: list[dict[str, Any]] = Field(default_factory=list)
    duration_ms: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    input_hash: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    temperature: float | None = None
    estimated_cost: float | None = None


class BusinessValidationError(ValueError):
    """The JSON shape is valid but violates a Urus research invariant."""


class EquityEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    observation: str


class MarketRegime(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification: Literal["risk_on", "selective", "neutral", "risk_off", "unknown"]
    confidence: float = Field(ge=0, le=1)
    evidence: list[EquityEvidence] = Field(default_factory=list)


class EquityRanking(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1)
    symbol: str
    themes: list[str] = Field(default_factory=list)
    action: Literal["setup_ready", "watch", "observe", "avoid", "insufficient_data"]
    strict_sepa_completeness: Literal["complete", "partial", "not_evaluable"]
    score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    thesis: str
    evidence: list[EquityEvidence] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    invalidation_conditions: list[str] = Field(default_factory=list)
    instrument_forecast: InstrumentForecast | None = None
    if_cash: CashScenarioDecision | None = None
    if_held: HeldScenarioDecision | None = None


class ExpectedReturnRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_percent: float
    maximum_percent: float

    @model_validator(mode="after")
    def validate_order(self) -> "ExpectedReturnRange":
        if self.minimum_percent > self.maximum_percent:
            raise ValueError("minimum_percent must not exceed maximum_percent")
        return self


class InstrumentForecast(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: Literal["up", "down", "flat", "mixed", "unknown"]
    probability: float = Field(ge=0, le=1)
    expected_return_range_percent: ExpectedReturnRange
    relative_to: Literal["SPY", "QQQ", "theme_benchmark", "none"]
    relative_direction: Literal["outperform", "underperform", "inline", "unknown"]
    horizon: Literal["regular_session", "final_hour"]


class CashScenarioDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["buy", "wait", "avoid", "cash"]
    conviction: Literal["high", "medium", "low"]
    reason: str
    entry_condition: str | None = None


class HeldScenarioDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["add", "hold", "take_profit", "reduce", "stop_loss", "exit"]
    conviction: Literal["high", "medium", "low"]
    reason: str
    take_profit_condition: str | None = None
    stop_loss_condition: str | None = None


class PhaseScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    direction: Literal["bullish", "bearish", "range", "mixed", "unknown"]
    probability: float = Field(ge=0, le=1)
    conditions: list[str] = Field(default_factory=list)


class PhaseForecast(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: Literal["bullish", "bearish", "range", "mixed", "unknown"]
    confidence: float = Field(ge=0, le=1)
    expected_path: str
    leading_themes: list[str] = Field(default_factory=list)
    lagging_themes: list[str] = Field(default_factory=list)
    catalysts: list[str] = Field(default_factory=list)
    confirmation_conditions: list[str] = Field(default_factory=list)
    invalidation_conditions: list[str] = Field(default_factory=list)
    scenarios: list[PhaseScenario] = Field(default_factory=list)


class ForecastEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: str | None = None
    verdict: Literal["hit", "partial", "miss", "unscorable"]
    score: float | None = Field(default=None, ge=0, le=1)
    explanation: str


class DailyReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_summary: str
    market_outcome: str
    theme_outcomes: list[str] = Field(default_factory=list)
    pre_market_evaluation: ForecastEvaluation
    pre_close_evaluation: ForecastEvaluation | None = None
    forecast_errors: list[str] = Field(default_factory=list)
    lessons: list[str] = Field(default_factory=list)
    next_session_carry: list[str] = Field(default_factory=list)


class EquityDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[
        "urus.equity_decision.v1",
        "urus.equity_decision.v2",
        "urus.equity_decision.v3",
    ]
    decision_phase: DecisionPhase = "pre_close"
    agent_profile: Literal[
        "urus-premarket-strategist",
        "urus-preclose-strategist",
        "urus-postclose-reviewer",
        "urus-current-state-analyst",
    ] = "urus-preclose-strategist"
    forecast_horizon: Literal[
        "regular_session", "final_hour", "completed_session", "current_state"
    ] = "final_hour"
    forecast: PhaseForecast | None = None
    review: DailyReview | None = None
    as_of: str | None = None
    status: Literal["decision", "insufficient_data"]
    market_regime: MarketRegime
    rankings: list[EquityRanking] = Field(default_factory=list)
    portfolio_warnings: list[str] = Field(default_factory=list)
    disclaimer: str


class OptionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    observation: str


class OptionHorizon(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expiration: str | None = None
    days_to_expiry: int | None = None


class OptionLeg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    side: Literal["buy", "sell"]
    option_type: Literal["call", "put"]
    strike: float
    expiration: str
    quantity: int = Field(gt=0)
    premium: float | None = Field(default=None, ge=0)


class OptionStructure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "call_vertical",
        "put_vertical",
        "long_call_butterfly",
        "long_put_butterfly",
        "iron_condor",
        "calendar",
        "none",
    ]
    execution_ready: bool = False
    legs: list[OptionLeg] = Field(default_factory=list)
    net_debit_or_credit: float | None = None
    max_profit: float | None = None
    max_loss: float | None = None
    breakevens: list[float] = Field(default_factory=list)


class ScenarioAnchors(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spot: float | None = None
    expected_move: dict[str, Any] | None = None
    max_pain: float | None = None
    primary_gamma_flip: float | None = None
    call_wall: float | None = None
    put_wall: float | None = None


class OptionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["urus.options_decision.v1", "urus.options_decision.v2"]
    decision_phase: DecisionPhase = "pre_close"
    agent_profile: Literal[
        "urus-premarket-strategist",
        "urus-preclose-strategist",
        "urus-postclose-reviewer",
        "urus-current-state-analyst",
    ] = "urus-preclose-strategist"
    symbol: str
    as_of: str | None = None
    status: Literal["decision", "no_trade", "insufficient_data"]
    gamma_regime: Literal[
        "positive_gamma", "negative_gamma", "near_flip", "mixed", "unknown"
    ]
    thesis: str
    horizon: OptionHorizon
    structure: OptionStructure
    scenario_anchors: ScenarioAnchors
    confidence: float = Field(ge=0, le=1)
    evidence: list[OptionEvidence] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    invalidation_conditions: list[str] = Field(default_factory=list)
    disclaimer: str


def output_model_for(task_type: TaskType) -> type[BaseModel]:
    return EquityDecision if task_type == "equity_ranking" else OptionDecision


def response_schema_for(task: AgentTask) -> dict[str, Any]:
    """Build the response schema for one invocation.

    The persisted contracts retain v1 compatibility, but a daily-cycle invocation
    must not advertise legacy/defaulted fields to the model. Narrowing the schema
    here keeps provider-side structured output aligned with business validation.
    """

    schema = output_model_for(task.task_type).model_json_schema()
    if (
        task.task_type == "equity_ranking"
        and task.decision_phase == "current_state"
        and task.metadata.get("scope_kind") == "manual_current_state"
    ):
        # Manual current-state analysis is deliberately not an official daily
        # decision.  Advertising forecast/review/position scenario definitions
        # makes the model fill or repair fields that this workflow must discard.
        # Keep the persisted model compatible while presenting a narrow output
        # contract that matches what the report actually renders.
        top_level_fields = (
            "schema_version", "as_of", "status", "market_regime", "rankings",
            "portfolio_warnings", "disclaimer",
        )
        schema["properties"] = {
            name: schema["properties"][name]
            for name in top_level_fields
        }
        schema["properties"]["schema_version"] = {
            "type": "string",
            "enum": ["urus.equity_decision.v1"],
        }
        schema["required"] = list(top_level_fields)
        schema["properties"]["rankings"]["maxItems"] = 8
        schema["properties"]["portfolio_warnings"]["maxItems"] = 8
        schema["properties"]["portfolio_warnings"]["items"]["maxLength"] = 300
        evidence = schema["$defs"]["EquityEvidence"]
        evidence["properties"]["path"]["maxLength"] = 240
        evidence["properties"]["observation"]["maxLength"] = 500
        market_regime = schema["$defs"]["MarketRegime"]
        market_regime["properties"]["evidence"]["maxItems"] = 6
        ranking = schema["$defs"]["EquityRanking"]
        ranking_fields = (
            "rank", "symbol", "themes", "action", "strict_sepa_completeness",
            "score", "confidence", "thesis", "evidence", "risks",
            "missing_fields", "invalidation_conditions",
        )
        ranking["properties"] = {
            name: ranking["properties"][name]
            for name in ranking_fields
        }
        ranking["properties"]["themes"]["maxItems"] = 4
        ranking["properties"]["thesis"]["maxLength"] = 800
        ranking["properties"]["evidence"]["maxItems"] = 5
        for name in ("risks", "missing_fields", "invalidation_conditions"):
            ranking["properties"][name]["maxItems"] = 5
            ranking["properties"][name]["items"]["maxLength"] = 300
        ranking["required"] = list(ranking_fields)
        return schema
    if task.metadata.get("daily_cycle") is not True:
        return schema

    profiles = {
        "pre_market": "urus-premarket-strategist",
        "pre_close": "urus-preclose-strategist",
        "post_close_review": "urus-postclose-reviewer",
        "current_state": "urus-current-state-analyst",
    }
    properties = schema["properties"]
    required = list(schema.get("required") or [])

    def require(name: str) -> None:
        if name not in required:
            required.append(name)

    def fixed_string(name: str, value: str) -> None:
        title = properties.get(name, {}).get("title")
        properties[name] = {"type": "string", "enum": [value]}
        if title:
            properties[name]["title"] = title
        require(name)

    fixed_string(
        "schema_version",
        "urus.equity_decision.v3"
        if task.task_type == "equity_ranking"
        else "urus.options_decision.v2",
    )
    fixed_string("decision_phase", task.decision_phase)
    fixed_string("agent_profile", profiles[task.decision_phase])

    if task.task_type == "equity_ranking":
        horizons = {
            "pre_market": "regular_session",
            "pre_close": "final_hour",
            "post_close_review": "completed_session",
            "current_state": "current_state",
        }
        fixed_string("forecast_horizon", horizons[task.decision_phase])
        require("forecast")
        require("review")
        if task.stage == "theme":
            properties["forecast"] = {"type": "null"}
            properties["review"] = {"type": "null"}
        elif task.decision_phase == "post_close_review":
            properties["forecast"] = {"type": "null"}
            properties["review"] = {"$ref": "#/$defs/DailyReview"}
            evaluation = schema["$defs"]["ForecastEvaluation"]
            evaluation["properties"]["verdict"] = {
                "type": "string",
                "enum": ["unscorable"],
            }
            evaluation["properties"]["score"] = {"type": "null"}
            for name in ("verdict", "score"):
                if name not in evaluation["required"]:
                    evaluation["required"].append(name)
        elif task.decision_phase == "current_state":
            properties["forecast"] = {"type": "null"}
            properties["review"] = {"type": "null"}
        else:
            properties["forecast"] = {"$ref": "#/$defs/PhaseForecast"}
            properties["review"] = {"type": "null"}

        ranking = schema["$defs"]["EquityRanking"]
        ranking_required = list(ranking.get("required") or [])
        scenario_fields = {
            "instrument_forecast": "InstrumentForecast",
            "if_cash": "CashScenarioDecision",
            "if_held": "HeldScenarioDecision",
        }
        for name, definition in scenario_fields.items():
            ranking["properties"][name] = (
                {"type": "null"}
                if task.decision_phase in {"post_close_review", "current_state"}
                else {"$ref": f"#/$defs/{definition}"}
            )
            if name not in ranking_required:
                ranking_required.append(name)
        ranking["required"] = ranking_required
        if task.decision_phase not in {"post_close_review", "current_state"}:
            instrument_forecast = schema["$defs"]["InstrumentForecast"]
            instrument_forecast["properties"]["horizon"] = {
                "type": "string",
                "enum": [horizons[task.decision_phase]],
            }

    elif task.metadata.get("required_expiration"):
        expiration = str(task.metadata["required_expiration"])
        horizon = schema["$defs"]["OptionHorizon"]
        horizon["properties"]["expiration"] = {
            "type": "string",
            "enum": [expiration],
        }
        horizon_required = horizon.setdefault("required", [])
        if "expiration" not in horizon_required:
            horizon_required.append("expiration")

    schema["required"] = required
    return schema


def validate_business_output(task: AgentTask, output: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(output, dict):
        raise BusinessValidationError("structured output must be one JSON object")
    if task.metadata.get("daily_cycle") is True:
        required_fields = {"schema_version", "decision_phase", "agent_profile"}
        if task.task_type == "equity_ranking":
            required_fields.update({"forecast_horizon", "forecast", "review"})
        missing_fields = sorted(required_fields - output.keys())
        if missing_fields:
            raise BusinessValidationError(
                "daily-cycle output is missing required fields: " + ", ".join(missing_fields)
            )
    model_type = output_model_for(task.task_type)
    validated = model_type.model_validate(output)
    normalized = validated.model_dump(mode="json")
    if task.task_type == "equity_ranking":
        phase_profiles = {
            "pre_market": ("urus-premarket-strategist", "regular_session"),
            "pre_close": ("urus-preclose-strategist", "final_hour"),
            "post_close_review": ("urus-postclose-reviewer", "completed_session"),
            "current_state": ("urus-current-state-analyst", "current_state"),
        }
        if task.metadata.get("daily_cycle") is True:
            if normalized.get("schema_version") != "urus.equity_decision.v3":
                raise BusinessValidationError("daily-cycle equity output requires schema v3")
            expected_profile, expected_horizon = phase_profiles[task.decision_phase]
            if normalized.get("decision_phase") != task.decision_phase:
                raise BusinessValidationError("decision_phase does not match the Agent task")
            if normalized.get("agent_profile") != expected_profile:
                raise BusinessValidationError("agent_profile does not match decision_phase")
            if normalized.get("forecast_horizon") != expected_horizon:
                raise BusinessValidationError("forecast_horizon does not match decision_phase")
            if task.stage == "theme":
                if normalized.get("forecast") is not None or normalized.get("review") is not None:
                    raise BusinessValidationError(
                        "theme outputs must keep top-level forecast and review null"
                    )
            elif task.decision_phase == "post_close_review":
                if normalized.get("forecast") is not None or normalized.get("review") is None:
                    raise BusinessValidationError(
                        "post_close_review requires review and must not emit a same-day forecast"
                    )
            elif task.decision_phase == "current_state":
                if normalized.get("forecast") is not None or normalized.get("review") is not None:
                    raise BusinessValidationError(
                        "current_state must not emit an official forecast or completed review"
                    )
            elif normalized.get("forecast") is None or normalized.get("review") is not None:
                raise BusinessValidationError(
                    "pre_market and pre_close require forecast and must not emit a completed review"
                )
        symbols = set(task.symbols)
        rankings = normalized.get("rankings", [])
        if task.metadata.get("daily_cycle") is True:
            raw_rankings = output.get("rankings") or []
            required_scenario_fields = {"instrument_forecast", "if_cash", "if_held"}
            for raw_ranking in raw_rankings:
                if not isinstance(raw_ranking, dict):
                    raise BusinessValidationError("each ranking must be one JSON object")
                missing = sorted(required_scenario_fields - raw_ranking.keys())
                if missing:
                    raise BusinessValidationError(
                        "daily-cycle ranking is missing required fields: " + ", ".join(missing)
                    )
            for ranking in rankings:
                scenarios = [
                    ranking.get("instrument_forecast"),
                    ranking.get("if_cash"),
                    ranking.get("if_held"),
                ]
                if task.decision_phase in {"post_close_review", "current_state"}:
                    if any(value is not None for value in scenarios):
                        raise BusinessValidationError(
                            "post_close_review rankings must not emit same-day position decisions"
                        )
                    continue
                if any(value is None for value in scenarios):
                    raise BusinessValidationError(
                        "pre_market and pre_close rankings require forecast, cash and held decisions"
                    )
                if ranking["instrument_forecast"]["horizon"] != expected_horizon:
                    raise BusinessValidationError(
                        "instrument forecast horizon does not match decision_phase"
                    )
                if (
                    ranking["if_cash"]["action"] == "buy"
                    and ranking["instrument_forecast"]["direction"] != "up"
                ):
                    raise BusinessValidationError(
                        "cash buy decisions require an up instrument forecast"
                    )
        ranked_symbols = [str(item["symbol"]).upper() for item in rankings]
        if len(ranked_symbols) != len(set(ranked_symbols)):
            raise BusinessValidationError("rankings must not contain duplicate symbols")
        if symbols and not set(ranked_symbols).issubset(symbols):
            raise BusinessValidationError("ranking contains a symbol outside the task universe")
        # Rank is a presentation index; the model's array order is the source
        # of truth. Normalize gaps or stale rank numbers deterministically.
        for index, item in enumerate(rankings, start=1):
            item["rank"] = index
        requires_full_coverage = (
            task.stage in {"market", "theme", "synthesis"}
            and task.decision_phase != "current_state"
        )
        if requires_full_coverage and symbols and set(ranked_symbols) != symbols:
            missing = sorted(symbols - set(ranked_symbols))
            raise BusinessValidationError(
                f"{task.stage} rankings must cover every task symbol; missing={missing}"
            )
    else:
        if task.metadata.get("daily_cycle") is True and normalized.get("schema_version") != "urus.options_decision.v2":
            raise BusinessValidationError("daily-cycle option output requires schema v2")
        if task.metadata.get("daily_cycle") is True and normalized.get("decision_phase") != task.decision_phase:
            raise BusinessValidationError("option decision_phase does not match the Agent task")
        expected_profile = {
            "pre_market": "urus-premarket-strategist",
            "pre_close": "urus-preclose-strategist",
            "post_close_review": "urus-postclose-reviewer",
            "current_state": "urus-current-state-analyst",
        }[task.decision_phase]
        if task.metadata.get("daily_cycle") is True and normalized.get("agent_profile") != expected_profile:
            raise BusinessValidationError("option agent_profile does not match decision_phase")
        required_expiration = task.metadata.get("required_expiration")
        if required_expiration and normalized["horizon"].get("expiration") != str(required_expiration):
            raise BusinessValidationError("option expiration does not match the controller-selected expiration")
        symbol = str(normalized["symbol"]).upper()
        if task.target_symbol and symbol != task.target_symbol:
            raise BusinessValidationError("option decision symbol does not match target_symbol")
        structure = normalized["structure"]
        if normalized["status"] in {"no_trade", "insufficient_data"} and structure["kind"] != "none":
            raise BusinessValidationError("no_trade and insufficient_data decisions must use structure=none")
        if structure["execution_ready"] and any(leg.get("premium") is None for leg in structure["legs"]):
            raise BusinessValidationError("execution-ready option structures require premiums for every leg")
        if not structure["execution_ready"] and any(
            structure[field] is not None for field in ("net_debit_or_credit", "max_profit", "max_loss")
        ):
            raise BusinessValidationError("non-executable option structures cannot claim exact economics")
        if not structure["execution_ready"] and structure.get("breakevens"):
            raise BusinessValidationError("non-executable option structures cannot claim exact breakevens")
    return normalized


def validate_task_output_scope(task: AgentTask, output: dict[str, Any], evidence: Any) -> None:
    """Validate target and expiration claims against the frozen option view."""

    if task.task_type != "options_structure":
        return
    target = str(task.target_symbol or "").upper()
    observations = evidence.packet.get("observations") or {}
    close = observations.get(getattr(evidence, "current_phase", task.decision_phase)) or {}
    options = close.get("options") or {}
    item = next(
        (
            value
            for value in (options.get("symbols") or [])
            if isinstance(value, dict) and str(value.get("symbol") or "").upper() == target
        ),
        None,
    )
    if item is None:
        raise BusinessValidationError(f"option target symbol is not in frozen evidence: {target}")
    available = {
        str(value.get("expiration") or "")
        for value in (item.get("expirations") or [])
        if isinstance(value, dict) and value.get("expiration")
    }
    quality = evidence.packet.get("quality") or {}
    if (
        output.get("status") == "insufficient_data"
        and available
        and not quality.get("blocking_errors")
    ):
        raise BusinessValidationError(
            "option evidence contains available expirations; insufficient_data must explain a "
            "specific blocking deficiency or provide a non-executable research template"
        )
    decision = output.get("horizon") or {}
    expiration = decision.get("expiration")
    if expiration and str(expiration) not in available:
        raise BusinessValidationError(f"option expiration is not in frozen evidence: {expiration}")
    for leg in (output.get("structure") or {}).get("legs") or []:
        leg_expiration = leg.get("expiration")
        if leg_expiration and str(leg_expiration) not in available:
            raise BusinessValidationError(f"option leg expiration is not in frozen evidence: {leg_expiration}")


def validate_evidence_references(
    task: AgentTask,
    output: dict[str, Any],
    evidence: Any,
    observed_tool_paths: set[str] | None = None,
) -> None:
    """Validate every model-provided evidence path against frozen inputs.

    The output contracts deliberately keep evidence references small, so this
    validator only walks the known evidence arrays.  A tool may expose a
    more-specific path during the same invocation; those paths are accepted
    in addition to paths resolvable in the frozen packet.
    """

    references: list[dict[str, Any]] = []
    if task.task_type == "equity_ranking":
        references.extend((output.get("market_regime") or {}).get("evidence") or [])
        for ranking in output.get("rankings") or []:
            references.extend(ranking.get("evidence") or [])
    else:
        references.extend(output.get("evidence") or [])
    observed = {str(path) for path in (observed_tool_paths or set()) if path}
    for reference in references:
        if not isinstance(reference, dict):
            raise BusinessValidationError("evidence reference must be an object")
        path = str(reference.get("path") or "").strip()
        observation = str(reference.get("observation") or "").strip()
        if not path or not observation:
            raise BusinessValidationError("evidence reference requires path and observation")
        canonical_path = getattr(evidence, "canonical_path", None)
        resolved_path = canonical_path(path) if callable(canonical_path) else (
            path if evidence.has_path(path) else None
        )
        if resolved_path:
            # Persist the canonical packet path so report links, replay and
            # subsequent validators do not retain the model's metadata alias.
            reference["path"] = resolved_path
            continue
        if path in observed:
            continue
        raise BusinessValidationError(f"evidence path does not resolve in frozen evidence: {path}")
