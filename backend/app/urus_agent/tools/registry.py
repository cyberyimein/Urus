from __future__ import annotations

from typing import Any

from app.urus_agent.contracts import AgentTask, AgentToolResult, ToolError, ToolSpec
from app.urus_agent.tools.base import RegisteredTool, ToolContext
from app.urus_agent.tools.math import (
    level_distances,
    option_payoff,
    position_size,
    risk_reward,
    statistics,
)


EQUITY_SKILL = "urus-equity-decision"
OPTIONS_SKILL = "urus-options-decision"


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


class ToolRegistry:
    def __init__(self, *, event_limit: int = 10) -> None:
        # A tool invocation is deliberately bounded more tightly than the
        # underlying repository query.  This keeps the agent's context small
        # and makes the per-call contract auditable.
        self.event_limit = max(1, min(event_limit, 20))
        self._tools = self._build_tools()

    def list_specs(self, skill_name: str, *, task: AgentTask | None = None) -> list[ToolSpec]:
        if task is not None and task.stage in {"synthesis", "review"}:
            return []
        return [item.spec for item in self._tools.values() if skill_name in item.skills]

    def openai_tools(self, skill_name: str, *, task: AgentTask | None = None) -> list[dict[str, Any]]:
        return [spec.as_openai_tool() for spec in self.list_specs(skill_name, task=task)]

    def call(self, name: str, arguments: dict[str, Any], context: ToolContext) -> AgentToolResult:
        registered = self._tools.get(name)
        if registered is None:
            return AgentToolResult(ok=False, tool=name, error=ToolError(code="tool_not_found", message=f"Unknown tool: {name}"))
        if context.task.requested_skill not in registered.skills:
            return AgentToolResult(ok=False, tool=name, error=ToolError(code="tool_not_allowed", message=f"Tool is not enabled for {context.task.requested_skill}"))
        scope_error = _tool_scope_error(name, arguments, context.task)
        if scope_error:
            return AgentToolResult(ok=False, tool=name, error=ToolError(code="tool_scope_violation", message=scope_error, retryable=False))
        try:
            _validate_arguments(registered.spec.parameters, arguments)
            value = registered.handler(context, arguments)
            return AgentToolResult.model_validate(value)
        except KeyError as exc:
            code, _, detail = str(exc).partition(":")
            return AgentToolResult(ok=False, tool=name, error=ToolError(code=code or "not_found", message=detail or str(exc), retryable=False))
        except (TypeError, ValueError) as exc:
            return AgentToolResult(ok=False, tool=name, error=ToolError(code="tool_arguments_invalid", message=str(exc), retryable=False))
        except Exception as exc:  # noqa: BLE001
            return AgentToolResult(ok=False, tool=name, error=ToolError(code="tool_error", message=str(exc), retryable=False))

    def _build_tools(self) -> dict[str, RegisteredTool]:
        phase_schema = {
            "type": "string",
            "enum": ["pre_market", "pre_close", "post_close_review", "current_state"],
        }
        payoff_leg_schema = _object(
            {
                "side": {"type": "string", "enum": ["buy", "sell"]},
                "option_type": {"type": "string", "enum": ["call", "put"]},
                "strike": {"type": "number"},
                "expiration": {"type": "string"},
                "quantity": {"type": "integer", "minimum": 1},
                # A missing premium is meaningful: the math tool must return
                # complete=false instead of fabricating exact economics.
                "premium": {"type": ["number", "null"], "minimum": 0},
            },
            ["side", "option_type", "strike", "quantity"],
        )
        tools: list[RegisteredTool] = [
            RegisteredTool(
                ToolSpec(name="get_market_regime", description="Get the bounded market and theme ETF regime for one observation phase.", parameters=_object({"phase": phase_schema, "symbols": {"type": "array", "items": {"type": "string"}, "maxItems": 20}}, ["phase"])),
                lambda context, args: context.evidence.market_regime(args["phase"], _market_symbols(context.task, args.get("symbols", []))),
                frozenset({EQUITY_SKILL, OPTIONS_SKILL}),
            ),
            RegisteredTool(
                ToolSpec(name="get_systematic_flows", description="Get deterministic CTA proxy positions, marginal pressure, mechanical actions, asset-class aggregation and limitations for one phase.", parameters=_object({"phase": phase_schema}, ["phase"])),
                lambda context, args: context.evidence.systematic_flows(args["phase"]),
                frozenset({EQUITY_SKILL, OPTIONS_SKILL}),
            ),
            RegisteredTool(
                ToolSpec(name="get_instrument_snapshot", description="Get one symbol's quote, technical, relative-strength, theme and quality view.", parameters=_object({"symbol": {"type": "string"}, "phase": phase_schema, "sections": {"type": "array", "items": {"type": "string", "enum": ["quote", "technical", "relative_strength", "theme", "quality"]}, "maxItems": 5}}, ["symbol", "phase"])),
                lambda context, args: context.evidence.instrument_snapshot(args["symbol"], args["phase"], args.get("sections", [])),
                frozenset({EQUITY_SKILL, OPTIONS_SKILL}),
            ),
            RegisteredTool(
                ToolSpec(name="compare_instrument_observations", description="Compare one instrument between the packet's baseline and current observations without treating volume windows as equivalent.", parameters=_object({"symbol": {"type": "string"}}, ["symbol"])),
                lambda context, args: context.evidence.compare_instrument(args["symbol"]),
                frozenset({EQUITY_SKILL}),
            ),
            RegisteredTool(
                ToolSpec(name="get_watchlist_candidates", description="List bounded candidate symbols from the frozen instrument universe.", parameters=_object({"themes": {"type": "array", "items": {"type": "string"}, "maxItems": 20}, "asset_types": {"type": "array", "items": {"type": "string"}, "maxItems": 10}, "quality_status": {"type": "array", "items": {"type": "string"}, "maxItems": 10}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}})),
                lambda context, args: context.evidence.candidates(args.get("themes", []), args.get("asset_types", []), args.get("limit", 50), args.get("quality_status", []), _allowed_symbols(context.task)),
                frozenset({EQUITY_SKILL}),
            ),
            RegisteredTool(
                ToolSpec(name="get_option_overview", description="Get one symbol's option overview and available expirations for a phase.", parameters=_object({"symbol": {"type": "string"}, "phase": phase_schema}, ["symbol", "phase"])),
                lambda context, args: context.evidence.option_overview(args["symbol"], args["phase"]),
                frozenset({OPTIONS_SKILL}),
            ),
            RegisteredTool(
                ToolSpec(name="get_option_expiration_structure", description="Get one expiration's Max Pain, Expected Move, DEX/GEX, walls, gamma zones and Gamma Flip.", parameters=_object({"symbol": {"type": "string"}, "phase": phase_schema, "expiration": {"type": "string"}}, ["symbol", "phase", "expiration"])),
                lambda context, args: context.evidence.option_expiration(args["symbol"], args["phase"], args["expiration"]),
                frozenset({OPTIONS_SKILL}),
            ),
            RegisteredTool(
                ToolSpec(name="compare_option_observations", description="Compare one option expiration and its symbol-level IV/HV overview between two frozen observations.", parameters=_object({"symbol": {"type": "string"}, "expiration": {"type": "string"}, "from_phase": phase_schema, "to_phase": phase_schema}, ["symbol", "expiration"])),
                lambda context, args: context.evidence.compare_option(args["symbol"], args["expiration"], args.get("from_phase"), args.get("to_phase")),
                frozenset({OPTIONS_SKILL}),
            ),
            RegisteredTool(
                ToolSpec(name="get_events", description="Read a small bounded list of scheduled event records and results already collected by Urus/Anomalo. Use get_event_result for one event's details.", parameters=_object({"category": {"type": "string", "enum": ["macro", "instrument", "all"]}, "subject": {"type": ["string", "null"]}, "status": {"type": "array", "items": {"type": "string"}, "maxItems": 10}, "result_state": {"type": "string", "enum": ["any", "missing", "available"]}, "from_time": {"type": ["string", "null"]}, "to_time": {"type": ["string", "null"]}, "limit": {"type": "integer", "minimum": 1, "maximum": self.event_limit}})),
                lambda context, args: context.evidence.events(args.get("category", "all"), args.get("subject"), args.get("status", []), args.get("result_state", "any"), min(int(args.get("limit", self.event_limit)), self.event_limit), args.get("from_time"), args.get("to_time"), context.task.cutoff_time, _allowed_symbols(context.task)),
                frozenset({EQUITY_SKILL, OPTIONS_SKILL}),
            ),
            RegisteredTool(
                ToolSpec(name="get_event_result", description="Get the stored result and sources for one already collected event; never performs a web search.", parameters=_object({"event_id": {"type": "string"}}, ["event_id"])),
                lambda context, args: context.evidence.event_result(args["event_id"], _allowed_symbols(context.task)),
                frozenset({EQUITY_SKILL, OPTIONS_SKILL}),
            ),
            RegisteredTool(
                ToolSpec(name="get_data_quality", description="Inspect quality, mock state, warnings and missing data for the frozen packet.", parameters=_object({"scope": {"type": "string", "enum": ["all", "market", "instruments", "options", "events", "systematic_flows"]}, "symbol": {"type": ["string", "null"]}})),
                lambda context, args: context.evidence.quality(args.get("scope", "all"), args.get("symbol")),
                frozenset({EQUITY_SKILL, OPTIONS_SKILL}),
            ),
            RegisteredTool(
                ToolSpec(name="get_prior_stage_reports", description="Read the bounded previous-day or same-day Agent reports inherited by this daily-cycle decision.", parameters=_object({})),
                lambda context, _args: context.evidence.prior_stage_reports(),
                frozenset({EQUITY_SKILL, OPTIONS_SKILL}),
            ),
            RegisteredTool(
                ToolSpec(name="calculate_level_distances", description="Calculate signed and percent distance from spot to named price levels.", parameters=_object({"spot": {"type": "number"}, "levels": {"type": "object", "maxProperties": 32, "additionalProperties": {"type": "number"}}}, ["spot", "levels"])),
                lambda _context, args: {"tool_schema_version": "urus.agent_tool_result.v1", "ok": True, "tool": "calculate_level_distances", "data": level_distances(args["spot"], args["levels"]), "truncated": False},
                frozenset({EQUITY_SKILL, OPTIONS_SKILL}),
            ),
            RegisteredTool(
                ToolSpec(name="calculate_option_payoff", description="Calculate deterministic multi-leg option payoff scenarios. Missing premiums or multiplier return complete=false.", parameters=_object({"prices": {"type": "array", "items": {"type": "number"}, "maxItems": 200}, "legs": {"type": "array", "items": payoff_leg_schema, "maxItems": 12}, "multiplier": {"type": ["number", "null"], "exclusiveMinimum": 0}} , ["prices", "legs"])),
                lambda _context, args: {"tool_schema_version": "urus.agent_tool_result.v1", "ok": True, "tool": "calculate_option_payoff", "data": option_payoff(args["prices"], args["legs"], args.get("multiplier")), "truncated": False},
                frozenset({OPTIONS_SKILL}),
            ),
            RegisteredTool(
                ToolSpec(name="calculate_risk_reward", description="Calculate deterministic risk, reward and ratio for a directional setup.", parameters=_object({"entry": {"type": "number"}, "stop": {"type": "number"}, "target": {"type": "number"}, "direction": {"type": "string", "enum": ["long", "short"]}}, ["entry", "stop", "target", "direction"])),
                lambda _context, args: {"tool_schema_version": "urus.agent_tool_result.v1", "ok": True, "tool": "calculate_risk_reward", "data": risk_reward(args["entry"], args["stop"], args["target"], args["direction"]), "truncated": False},
                frozenset({EQUITY_SKILL, OPTIONS_SKILL}),
            ),
            RegisteredTool(
                ToolSpec(name="calculate_position_size", description="Calculate theoretical bounded position size from a fixed risk budget. It does not read an account or place an order.", parameters=_object({"account_value": {"type": "number"}, "max_risk_percent": {"type": "number"}, "entry": {"type": "number"}, "stop": {"type": "number"}, "multiplier": {"type": "number"}, "max_position_percent": {"type": ["number", "null"]}}, ["account_value", "max_risk_percent", "entry", "stop"])),
                lambda _context, args: {"tool_schema_version": "urus.agent_tool_result.v1", "ok": True, "tool": "calculate_position_size", "data": position_size(args["account_value"], args["max_risk_percent"], args["entry"], args["stop"], args.get("multiplier", 1), args.get("max_position_percent")), "truncated": False},
                frozenset({EQUITY_SKILL, OPTIONS_SKILL}),
            ),
            RegisteredTool(
                ToolSpec(name="calculate_statistics", description="Calculate one allow-listed statistic over bounded numeric arrays.", parameters=_object({"operation": {"type": "string", "enum": ["mean", "median", "standard_deviation", "z_score", "correlation", "linear_regression"]}, "values": {"type": "array", "items": {"type": "number"}, "maxItems": 5000}, "value": {"type": ["number", "null"]}, "other": {"type": ["array", "null"], "items": {"type": "number"}, "maxItems": 5000}}, ["operation", "values"])),
                lambda _context, args: {"tool_schema_version": "urus.agent_tool_result.v1", "ok": True, "tool": "calculate_statistics", "data": statistics(args["operation"], args["values"], args.get("value"), args.get("other")), "truncated": False},
                frozenset({EQUITY_SKILL, OPTIONS_SKILL}),
            ),
        ]
        return {item.spec.name: item for item in tools}


def _allowed_symbols(task: AgentTask) -> set[str] | None:
    if task.stage == "equity" and not task.symbols:
        return None
    values = {str(symbol).upper() for symbol in task.symbols if symbol}
    metadata = task.metadata if isinstance(task.metadata, dict) else {}
    for key in ("benchmark_symbols", "reference_symbols"):
        raw = metadata.get(key)
        if isinstance(raw, list):
            values.update(str(symbol).upper() for symbol in raw if symbol)
    if task.target_symbol:
        values.add(task.target_symbol.upper())
    return values


def _market_symbols(task: AgentTask, requested: list[str]) -> list[str]:
    allowed = _allowed_symbols(task)
    if requested:
        return [str(symbol).upper() for symbol in requested]
    if allowed is not None:
        return sorted(allowed)
    return []


def _tool_scope_error(name: str, arguments: dict[str, Any], task: AgentTask) -> str | None:
    if task.stage in {"synthesis", "review"}:
        return f"{task.stage} cannot call data tools."
    metadata = task.metadata if isinstance(task.metadata, dict) else {}
    configured_phases = metadata.get("comparison_observations")
    allowed_phases = {
        str(value)
        for value in configured_phases
        if value
    } if isinstance(configured_phases, list) else set()
    allowed_phases.add(
        str(metadata.get("current_observation") or task.decision_phase)
    )
    requested_phases = {
        str(arguments[key])
        for key in ("phase", "from_phase", "to_phase")
        if arguments.get(key)
    }
    outside_phases = requested_phases - allowed_phases
    if outside_phases:
        return "Observation phase is outside the task scope: " + ", ".join(
            sorted(outside_phases)
        )
    allowed = _allowed_symbols(task)
    if allowed is None:
        return None
    option_tools = {"get_option_overview", "get_option_expiration_structure", "compare_option_observations"}
    if name in option_tools:
        symbol = str(arguments.get("symbol") or "").upper()
        target = str(task.target_symbol or "").upper()
        if not target or symbol != target:
            return f"Option tool symbol {symbol or '<missing>'} is outside target_symbol {target or '<missing>'}."
        return None
    if name == "get_market_regime":
        requested = {str(symbol).upper() for symbol in arguments.get("symbols", []) if symbol}
        outside = requested - allowed
        if outside:
            return f"Market symbols are outside the task scope: {', '.join(sorted(outside))}."
        return None
    if name in {"get_instrument_snapshot", "compare_instrument_observations"}:
        symbol = str(arguments.get("symbol") or "").upper()
        if symbol not in allowed:
            return f"Instrument symbol {symbol or '<missing>'} is outside the task scope."
    if name == "get_data_quality" and arguments.get("symbol"):
        symbol = str(arguments["symbol"]).upper()
        if symbol not in allowed:
            return f"Quality symbol {symbol} is outside the task scope."
    if name in {"get_events", "get_event_result"} and name == "get_events" and arguments.get("subject"):
        subject = str(arguments["subject"]).upper()
        if subject not in allowed and subject != "MARKET":
            return f"Event subject {subject} is outside the task scope."
    return None


def _validate_arguments(schema: dict[str, Any], value: Any, path: str = "arguments") -> None:
    """Validate the small JSON-Schema subset used by registered tools.

    Keeping this dependency-free is intentional: tool arguments are an
    untrusted model output and must be rejected before an adapter is called,
    even in the minimal worker image.
    """

    if not isinstance(schema, dict):
        return
    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(_matches_type(value, expected_type) for expected_type in expected):
            raise ValueError(f"{path} must be one of {expected}")
    elif expected and not _matches_type(value, expected):
        raise ValueError(f"{path} must be {expected}")
    enum = schema.get("enum")
    if enum is not None and value not in enum:
        raise ValueError(f"{path} must be one of {enum}")
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        missing = [key for key in required if key not in value]
        if missing:
            raise ValueError(f"{path} is missing required field(s): {', '.join(missing)}")
        if schema.get("additionalProperties") is False:
            unknown = [key for key in value if key not in properties]
            if unknown:
                raise ValueError(f"{path} has unknown field(s): {', '.join(unknown)}")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            raise ValueError(f"{path} must contain at most {schema['maxProperties']} field(s)")
        for key, item in value.items():
            if key in properties:
                _validate_arguments(properties[key], item, f"{path}.{key}")
            elif isinstance(schema.get("additionalProperties"), dict):
                _validate_arguments(schema["additionalProperties"], item, f"{path}.{key}")
    elif isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise ValueError(f"{path} must contain at least {schema['minItems']} item(s)")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ValueError(f"{path} must contain at most {schema['maxItems']} item(s)")
        for index, item in enumerate(value):
            _validate_arguments(schema.get("items") or {}, item, f"{path}[{index}]")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{path} must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"{path} must be <= {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            raise ValueError(f"{path} must be > {schema['exclusiveMinimum']}")


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, True)
