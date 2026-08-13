from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from app.urus_agent.contracts import (
    AgentTask,
    AgentToolResult,
    BusinessValidationError,
    DecisionResult,
    ToolError,
    response_schema_for,
    validate_business_output,
    validate_evidence_references,
    validate_task_output_scope,
)
from app.urus_agent.evidence import EvidenceStore
from app.urus_agent.providers.openrouter import LLMProvider
from app.urus_agent.prompts import load_agent_profile, load_system_prompt, load_task_prompt
from app.urus_agent.skill_loader import SkillLoader
from app.urus_agent.tools.base import ToolContext
from app.urus_agent.tools.registry import ToolRegistry
from app.urus_agent.trace import InMemoryTraceSink, TraceSink


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()


def _daily_cycle_repair_constraints(task: AgentTask) -> str:
    if task.metadata.get("daily_cycle") is not True:
        return ""
    profiles = {
        "pre_market": "urus-premarket-strategist",
        "pre_close": "urus-preclose-strategist",
        "post_close_review": "urus-postclose-reviewer",
        "current_state": "urus-current-state-analyst",
    }
    base = (
        "Required daily-cycle constants: "
        f'decision_phase="{task.decision_phase}"; '
        f'agent_profile="{profiles[task.decision_phase]}"; '
    )
    if task.task_type == "options_structure":
        expiration = task.metadata.get("required_expiration")
        expiration_rule = (
            f' horizon.expiration="{expiration}".' if expiration else ""
        )
        return base + 'schema_version="urus.options_decision.v2".' + expiration_rule
    horizons = {
        "pre_market": "regular_session",
        "pre_close": "final_hour",
        "post_close_review": "completed_session",
        "current_state": "current_state",
    }
    payload_rule = (
        "forecast=null; review=null."
        if task.stage == "theme" or task.decision_phase == "current_state"
        else
        "forecast=null; review must be a non-null object."
        if task.decision_phase == "post_close_review"
        else "forecast must be a non-null object; review=null."
    )
    return (
        base
        + 'schema_version="urus.equity_decision.v3"; '
        + f'forecast_horizon="{horizons[task.decision_phase]}"; '
        + payload_rule
    )


class UrusAgentRuntime:
    def __init__(
        self,
        provider: LLMProvider,
        *,
        registry: ToolRegistry | None = None,
        skill_loader: SkillLoader | None = None,
        max_tool_iterations: int = 8,
        max_output_bytes: int | None = None,
        max_tool_result_bytes: int = 100_000,
        max_total_tool_result_bytes: int = 500_000,
        max_context_bytes: int = 500_000,
        max_raw_response_bytes: int = 200_000,
        max_total_tool_calls: int = 24,
        enforce_stage_tool_requirements: bool = True,
        trace_sink: TraceSink | None = None,
    ) -> None:
        self.provider = provider
        self.registry = registry or ToolRegistry()
        self.skill_loader = skill_loader or SkillLoader()
        self.max_tool_iterations = max(1, max_tool_iterations)
        self.max_output_bytes = (
            max(1_000, int(max_output_bytes)) if max_output_bytes is not None else None
        )
        self.max_tool_result_bytes = max(1_000, int(max_tool_result_bytes))
        self.max_total_tool_result_bytes = max(10_000, max_total_tool_result_bytes)
        self.max_context_bytes = max(10_000, max_context_bytes)
        self.max_raw_response_bytes = max(1_000, max_raw_response_bytes)
        self.max_total_tool_calls = max(1, max_total_tool_calls)
        self.enforce_stage_tool_requirements = enforce_stage_tool_requirements
        self.trace_sink = trace_sink or InMemoryTraceSink()

    def decide(
        self,
        task: AgentTask,
        packet: dict[str, Any] | EvidenceStore,
        *,
        trace_parent_node_id: str | None = None,
    ) -> DecisionResult:
        started = time.monotonic()
        evidence = packet if isinstance(packet, EvidenceStore) else EvidenceStore(packet)
        input_hash = evidence.input_hash or hashlib.sha256(_canonical(evidence.packet)).hexdigest()
        tool_calls: list[dict[str, Any]] = []
        model_turns: list[dict[str, Any]] = []
        observed_tool_paths: set[str] = set()
        prompt_tokens = 0
        completion_tokens = 0
        total_tool_result_bytes = 0
        format_repair_attempted = False
        business_repair_attempted = False
        evidence_collection_retries = 0
        try:
            _validate_task_scope(task, evidence)
            skill = self.skill_loader.load(task.requested_skill)
            task_prompt = load_task_prompt(task.stage)
            agent_profile = load_agent_profile(task.decision_phase)
            phase_instructions = (
                agent_profile.get("options_instructions")
                if task.task_type == "options_structure"
                else agent_profile.get("instructions")
            )
            skill_node = self.trace_sink.start_node(
                node_type="skill",
                label=skill.name,
                lane="Preparation",
                parent_node_id=trace_parent_node_id,
                input_summary={
                    "skill_hash": skill.content_hash,
                    "task_prompt_stage": task.stage,
                    "task_prompt_hash": hashlib.sha256(task_prompt.encode("utf-8")).hexdigest(),
                },
                decision_run_id=task.decision_run_id,
            )
            self.trace_sink.finish_node(
                skill_node,
                output_summary={"name": skill.name, "description": skill.description},
            )
            schema = response_schema_for(task)
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "urus_equity_decision" if task.task_type == "equity_ranking" else "urus_options_decision",
                    "strict": True,
                    "schema": schema,
                },
            }
            system = (
                f"{load_system_prompt()}\n\n"
                f"Active daily-cycle Agent: {agent_profile['agent_name']}.\n"
                f"Agent description: {agent_profile['description']}\n"
                f"Daily-cycle instructions:\n{phase_instructions}\n\n"
                f"Current task type: {task.task_type}. Current cutoff time: {task.cutoff_time.isoformat()}. "
                f"Dataset key: {task.dataset_key}.\n"
                "For daily-cycle tasks, use urus.equity_decision.v3 or "
                "urus.options_decision.v2 as selected by task type.\n"
                f"Current invocation stage: {task.stage}.\n"
                f"Task instructions:\n{task_prompt}\n\n"
                f"Activated Skill: {skill.name}\n{skill.instructions}"
            )
            allowed_tools = self.registry.openai_tools(task.requested_skill, task=task)
            seen_calls: set[tuple[str, str]] = set()
            required_evidence: list[dict[str, Any]] = []
            if self.enforce_stage_tool_requirements:
                for prefetch_sequence, (name, arguments) in enumerate(
                    _stage_prefetch_plan(task, evidence), start=1
                ):
                    key = (name, json.dumps(arguments, sort_keys=True, default=str))
                    seen_calls.add(key)
                    tool_node = self.trace_sink.start_node(
                        node_type="tool",
                        label=f"Required evidence · {name}",
                        lane=_task_lane(task),
                        parent_node_id=trace_parent_node_id,
                        input_summary={"arguments": arguments, "prefetched": True},
                        decision_run_id=task.decision_run_id,
                    )
                    call_started = time.monotonic()
                    result_model = self.registry.call(
                        name,
                        arguments,
                        ToolContext(task=task, evidence=evidence),
                    )
                    result = result_model.model_dump(mode="json")
                    result_bytes = len(_canonical(result))
                    total_tool_result_bytes += result_bytes
                    if total_tool_result_bytes > self.max_total_tool_result_bytes:
                        self.trace_sink.fail_node(
                            tool_node,
                            error_code="tool_result_budget_exceeded",
                            error_message="Required evidence exceeded the cumulative tool-result budget.",
                        )
                        raise RuntimeError(
                            "tool_result_budget_exceeded: required evidence exceeded the configured limit"
                        )
                    if result_model.ok:
                        self.trace_sink.finish_node(
                            tool_node,
                            output_summary={"ok": True, "prefetched": True},
                            evidence_refs=[result_model.evidence.model_dump(mode="json")]
                            if result_model.evidence
                            else [],
                            metrics={"duration_ms": int((time.monotonic() - call_started) * 1000)},
                        )
                        if result_model.evidence and result_model.evidence.path:
                            observed_tool_paths.add(result_model.evidence.path)
                    else:
                        self.trace_sink.fail_node(
                            tool_node,
                            error_code=result_model.error.code if result_model.error else "tool_error",
                            error_message=result_model.error.message if result_model.error else "Required evidence tool failed.",
                        )
                    call_record = {
                        "tool_call_id": f"prefetch-{prefetch_sequence}",
                        "name": name,
                        "arguments": arguments,
                        "result": result,
                        "duration_ms": int((time.monotonic() - call_started) * 1000),
                        "prefetched": True,
                    }
                    tool_calls.append(call_record)
                    required_evidence.append(call_record)
            user = json.dumps(
                {
                    "task": task.model_dump(mode="json"),
                    "overview": evidence.overview(),
                    "required_evidence": required_evidence,
                },
                ensure_ascii=False,
            )
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
            raw_output: str | None = None
            for iteration in range(self.max_tool_iterations + 1):
                context_bytes = len(_canonical(messages))
                if context_bytes > self.max_context_bytes:
                    raise RuntimeError(
                        f"context_budget_exceeded: message context is {context_bytes} bytes; "
                        f"limit is {self.max_context_bytes}"
                    )
                model_node = self.trace_sink.start_node(
                    node_type="model",
                    label=f"{task.requested_skill} · model turn {iteration + 1}",
                    lane=_task_lane(task),
                    parent_node_id=trace_parent_node_id,
                    input_summary={
                        "iteration": iteration + 1,
                        "message_count": len(messages),
                        "context_bytes": context_bytes,
                    },
                    decision_run_id=task.decision_run_id,
                )
                model_started = time.monotonic()
                try:
                    response = self.provider.complete(messages, tools=allowed_tools, response_format=response_format)
                except Exception as exc:
                    self.trace_sink.fail_node(
                        model_node,
                        error_code="provider_error",
                        error_message=str(exc),
                    )
                    raise
                usage = response.usage or {}
                prompt_tokens += int(usage.get("prompt_tokens") or 0)
                completion_tokens += int(usage.get("completion_tokens") or 0)
                message = response.message
                raw_provider = _bounded_raw(response.raw, self.max_raw_response_bytes)
                model_turns.append(
                    {
                        "sequence": iteration + 1,
                        "trace_node_id": model_node,
                        "response_message": message,
                        "raw_provider_response": raw_provider["value"],
                        "raw_response_bytes": raw_provider["bytes"],
                        "raw_response_truncated": raw_provider["truncated"],
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                    }
                )
                calls = message.get("tool_calls") or []
                self.trace_sink.finish_node(
                    model_node,
                    output_summary={
                        "has_tool_calls": bool(calls),
                        "tool_call_count": len(calls),
                        "content_preview": _preview(message.get("content")),
                    },
                    metrics={
                        "duration_ms": int((time.monotonic() - model_started) * 1000),
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                    },
                )
                if calls:
                    messages.append(message)
                    for call in calls:
                        if len(tool_calls) >= self.max_total_tool_calls:
                            raise RuntimeError(
                                "tool_call_budget_exceeded: total tool-call budget was reached"
                            )
                        call_started = time.monotonic()
                        function = call.get("function") or {}
                        name = str(function.get("name") or "")
                        raw_arguments = function.get("arguments") or "{}"
                        try:
                            arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                        except json.JSONDecodeError as exc:
                            result = {"ok": False, "tool": name, "error": {"code": "tool_arguments_invalid", "message": str(exc), "retryable": False}}
                            arguments = {}
                        budget_error: RuntimeError | None = None
                        key = (name, json.dumps(arguments, sort_keys=True, default=str))
                        if key in seen_calls:
                            result = {"ok": False, "tool": name, "error": {"code": "duplicate_tool_call", "message": "The same tool arguments were already called.", "retryable": False}}
                        else:
                            seen_calls.add(key)
                            tool_node = self.trace_sink.start_node(
                                node_type="tool",
                                label=name,
                                lane=_task_lane(task),
                                parent_node_id=model_node,
                                input_summary={"arguments": arguments},
                                decision_run_id=task.decision_run_id,
                            )
                            try:
                                result_model = self.registry.call(name, arguments, ToolContext(task=task, evidence=evidence))
                                result = result_model.model_dump(mode="json")
                                if len(_canonical(result)) > self.max_tool_result_bytes:
                                    result = AgentToolResult(
                                        ok=False,
                                        tool=name,
                                        error=ToolError(
                                            code="tool_result_too_large",
                                            message="Tool result exceeded the configured byte limit.",
                                            retryable=False,
                                        ),
                                        truncated=True,
                                    ).model_dump(mode="json")
                                result_bytes = len(_canonical(result))
                                if total_tool_result_bytes + result_bytes > self.max_total_tool_result_bytes:
                                    self.trace_sink.fail_node(
                                        tool_node,
                                        error_code="tool_result_budget_exceeded",
                                        error_message=(
                                            "Cumulative tool-result budget exceeded: "
                                            f"{total_tool_result_bytes + result_bytes} bytes > "
                                            f"{self.max_total_tool_result_bytes} bytes"
                                        ),
                                    )
                                    result = AgentToolResult(
                                        ok=False,
                                        tool=name,
                                        error=ToolError(
                                            code="tool_result_budget_exceeded",
                                            message="Cumulative tool-result budget exceeded.",
                                            retryable=False,
                                        ),
                                    ).model_dump(mode="json")
                                    budget_error = RuntimeError(
                                        "tool_result_budget_exceeded: cumulative tool-result bytes exceeded "
                                        "the configured limit"
                                    )
                                total_tool_result_bytes += result_bytes
                                evidence_ref = result.get("evidence")
                                if isinstance(evidence_ref, dict) and evidence_ref.get("path"):
                                    observed_tool_paths.add(str(evidence_ref["path"]))
                                if result.get("ok"):
                                    self.trace_sink.finish_node(
                                        tool_node,
                                        output_summary={
                                            "tool": name,
                                            "ok": True,
                                            "result_bytes": len(_canonical(result)),
                                        },
                                        evidence_refs=[result["evidence"]] if result.get("evidence") else [],
                                    )
                                else:
                                    error = result.get("error") or {}
                                    self.trace_sink.fail_node(
                                        tool_node,
                                        error_code=str(error.get("code") or "tool_error"),
                                        error_message=str(error.get("message") or "tool failed"),
                                    )
                            except RuntimeError as exc:
                                if "tool_result_budget_exceeded" in str(exc):
                                    raise
                                result = AgentToolResult(
                                    ok=False,
                                    tool=name,
                                    error=ToolError(
                                        code="tool_adapter_error",
                                        message=str(exc),
                                        retryable=False,
                                    ),
                                ).model_dump(mode="json")
                                self.trace_sink.fail_node(
                                    tool_node,
                                    error_code="tool_adapter_error",
                                    error_message=str(exc),
                                )
                            except Exception as exc:  # keep the graph honest if a tool adapter crashes
                                result = AgentToolResult(
                                    ok=False,
                                    tool=name,
                                    error=ToolError(
                                        code="tool_adapter_error",
                                        message=str(exc),
                                        retryable=False,
                                    ),
                                ).model_dump(mode="json")
                                self.trace_sink.fail_node(
                                    tool_node,
                                    error_code="tool_adapter_error",
                                    error_message=str(exc),
                                )
                        tool_calls.append({"tool_call_id": call.get("id"), "name": name, "arguments": arguments, "result": result, "duration_ms": int((time.monotonic() - call_started) * 1000)})
                        messages.append({"role": "tool", "tool_call_id": call.get("id", f"tool-{len(tool_calls)}"), "name": name, "content": json.dumps(result, ensure_ascii=False)})
                        if budget_error is not None:
                            raise budget_error
                    continue
                content = message.get("content")
                raw_output = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                if (
                    self.max_output_bytes is not None
                    and len(raw_output.encode("utf-8")) > self.max_output_bytes
                ):
                    raise ValueError("structured_output_invalid: final output is too large")
                missing_tool_evidence = (
                    _missing_stage_tool_requirements(task, tool_calls, raw_output)
                    if self.enforce_stage_tool_requirements
                    else []
                )
                if missing_tool_evidence:
                    if evidence_collection_retries >= 2 or iteration >= self.max_tool_iterations:
                        raise BusinessValidationError(
                            "required stage evidence was not collected: "
                            + "; ".join(missing_tool_evidence)
                        )
                    evidence_collection_retries += 1
                    messages.append({"role": "assistant", "content": raw_output})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Required evidence collection is incomplete. Call the allowed tools "
                                "for each missing requirement below, then return one corrected JSON "
                                "object. Do not use outside knowledge. Missing requirements: "
                                + "; ".join(missing_tool_evidence)
                            ),
                        }
                    )
                    continue
                validation_node: str | None = None
                try:
                    validation_node = self.trace_sink.start_node(
                        node_type="validation",
                        label="Schema + business validation",
                        lane=_task_lane(task),
                        parent_node_id=model_node,
                        input_summary={"schema": response_format["json_schema"]["name"]},
                    )
                    output = json.loads(raw_output)
                    normalized = validate_business_output(task, output)
                    validate_task_output_scope(task, normalized, evidence)
                    validate_evidence_references(task, normalized, evidence, observed_tool_paths)
                except BusinessValidationError as exc:
                    if validation_node is not None:
                        self.trace_sink.fail_node(
                            validation_node,
                            error_code="business_validation_failed",
                            error_message=str(exc),
                        )
                    if business_repair_attempted or iteration >= self.max_tool_iterations:
                        raise
                    business_repair_attempted = True
                    messages.append({"role": "assistant", "content": raw_output})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Business validation correction only. Preserve the same factual "
                                "conclusions and return exactly one corrected JSON object. Do not add "
                                "facts or use tools. Fix this validation error: "
                                f"{exc}. {_daily_cycle_repair_constraints(task)} "
                                "Evidence paths must resolve in the frozen packet and must not "
                                "start with overview."
                            ),
                        }
                    )
                    allowed_tools = []
                    continue
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    if validation_node is not None:
                        self.trace_sink.fail_node(
                            validation_node,
                            error_code="structured_output_invalid",
                            error_message=str(exc),
                        )
                    if format_repair_attempted or iteration >= self.max_tool_iterations:
                        raise ValueError(f"structured_output_invalid: {exc}") from exc
                    format_repair_attempted = True
                    messages.append({"role": "assistant", "content": raw_output})
                    messages.append({"role": "user", "content": "Format correction only: return the same facts as exactly one JSON object matching the schema. Do not add facts."})
                    allowed_tools = []
                    continue
                self.trace_sink.finish_node(
                    validation_node,
                    output_summary={"valid": True, "schema": response_format["json_schema"]["name"]},
                )
                # Keep a concise, schema-derived rationale on the bounded
                # trace node. This is deliberately different from provider
                # chain-of-thought: it is a deterministic summary of fields
                # that survived validation and is safe to show by default.
                self.trace_sink.finish_node(
                    model_node,
                    output_summary={
                        "has_tool_calls": False,
                        "tool_call_count": len(tool_calls),
                        "content_preview": _preview(message.get("content")),
                        "decision_rationale": _decision_rationale(task.task_type, normalized),
                    },
                    metrics={
                        "duration_ms": int((time.monotonic() - model_started) * 1000),
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                    },
                )
                duration = int((time.monotonic() - started) * 1000)
                estimated_cost = _estimated_cost(self.provider, prompt_tokens, completion_tokens)
                return DecisionResult(status="succeeded", output=normalized, raw_output=raw_output, provider=self.provider.provider_name, model=self.provider.model, skill_name=skill.name, skill_hash=skill.content_hash, tool_call_count=len(tool_calls), tool_calls=tool_calls, model_turns=model_turns, duration_ms=duration, input_hash=input_hash, prompt_tokens=prompt_tokens or None, completion_tokens=completion_tokens or None, temperature=getattr(self.provider, "temperature", None), estimated_cost=estimated_cost)
            raise RuntimeError("max_tool_iterations: tool loop exceeded the configured limit")
        except BusinessValidationError as exc:
            return self._failure("failed", "business_validation_failed", str(exc), started, input_hash, tool_calls, model_turns, prompt_tokens, completion_tokens)
        except TimeoutError as exc:
            return self._failure("timed_out", "provider_timeout", str(exc), started, input_hash, tool_calls, model_turns, prompt_tokens, completion_tokens)
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            code = (
                "structured_output_invalid" if "structured_output_invalid" in message
                else "business_validation_failed" if "business_validation_failed" in message or "evidence path" in message
                else "context_budget_exceeded" if "context_budget_exceeded" in message
                else "tool_call_budget_exceeded" if "tool_call_budget_exceeded" in message
                else "tool_result_budget_exceeded" if "tool_result_budget_exceeded" in message
                else "task_scope_invalid" if "task_scope_invalid" in message
                else "symbol_not_found" if "symbol_not_found" in message
                else "expiration_not_found" if "expiration_not_found" in message
                else "provider_rate_limited" if "provider_rate_limited" in message
                else "provider_error" if "provider_error" in message
                else "agent_error"
            )
            return self._failure("failed", code, message, started, input_hash, tool_calls, model_turns, prompt_tokens, completion_tokens)

    def _failure(self, status: str, code: str, message: str, started: float, input_hash: str, tool_calls: list[dict[str, Any]], model_turns: list[dict[str, Any]], prompt_tokens: int, completion_tokens: int) -> DecisionResult:
        return DecisionResult(status=status, provider=self.provider.provider_name, model=self.provider.model, error_code=code, error_message=message, tool_calls=tool_calls, model_turns=model_turns, tool_call_count=len(tool_calls), duration_ms=int((time.monotonic() - started) * 1000), input_hash=input_hash, prompt_tokens=prompt_tokens or None, completion_tokens=completion_tokens or None, temperature=getattr(self.provider, "temperature", None), estimated_cost=_estimated_cost(self.provider, prompt_tokens, completion_tokens))


def _preview(value: Any, limit: int = 500) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def _task_lane(task: AgentTask) -> str:
    return {
        "market": "Market",
        "theme": "Themes",
        "synthesis": "Synthesis",
        "options": "Options",
    }.get(task.stage, "Equity")


def _decision_rationale(task_type: str, output: dict[str, Any]) -> str:
    """Build a short replay summary from validated output fields only."""
    if task_type == "equity_ranking":
        regime = output.get("market_regime") or {}
        rankings = output.get("rankings") or []
        parts = [
            f"phase={output.get('decision_phase', 'unknown')}",
            f"market={regime.get('classification', 'unknown')}",
            f"confidence={regime.get('confidence', 'unknown')}",
        ]
        if rankings:
            top = rankings[0]
            parts.append(
                f"top={top.get('symbol', 'unknown')} ({top.get('action', 'unknown')}): "
                f"{_preview(top.get('thesis'), 240) or 'no thesis'}"
            )
        return " · ".join(parts)
    return _preview(output.get("thesis") or output.get("status") or "No structured rationale.", 400) or "No structured rationale."


def _validate_task_scope(task: AgentTask, evidence: EvidenceStore) -> None:
    overview = evidence.overview()
    if task.task_type == "options_structure":
        target = str(task.target_symbol or "").upper()
        available = {str(value or "").upper() for value in overview.get("option_symbols", [])}
        if target not in available:
            raise RuntimeError(f"task_scope_invalid: option target symbol is absent from frozen dataset: {target}")
        return
    available = {
        str(item.get("symbol") or "").upper()
        for item in (overview.get("symbols") or [])
        if isinstance(item, dict)
    }
    missing = [symbol for symbol in task.symbols if str(symbol).upper() not in available]
    if missing:
        raise RuntimeError(
            "task_scope_invalid: symbols are absent from frozen dataset: "
            + ", ".join(sorted(set(missing)))
        )


def _bounded_raw(value: dict[str, Any], limit: int) -> dict[str, Any]:
    encoded = _canonical(value)
    if len(encoded) <= limit:
        return {"value": value, "bytes": len(encoded), "truncated": False}
    return {
        "value": {"truncated_preview": encoded[:limit].decode("utf-8", errors="replace")},
        "bytes": len(encoded),
        "truncated": True,
    }


def _estimated_cost(provider: LLMProvider, prompt_tokens: int, completion_tokens: int) -> float | None:
    input_rate = float(getattr(provider, "input_cost_per_million", 0.0) or 0.0)
    output_rate = float(getattr(provider, "output_cost_per_million", 0.0) or 0.0)
    if not (input_rate or output_rate) or not (prompt_tokens or completion_tokens):
        return None
    return round((prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000, 8)


def _missing_stage_tool_requirements(
    task: AgentTask,
    tool_calls: list[dict[str, Any]],
    raw_output: str,
) -> list[str]:
    """Return deterministic evidence calls still required by a stage."""

    calls = [
        (str(call.get("name") or ""), call.get("arguments") or {})
        for call in tool_calls
        if isinstance(call, dict)
        and isinstance(call.get("result"), dict)
        and call["result"].get("ok") is True
    ]
    missing: list[str] = []
    current_phase, comparison_phases = _task_observation_phases(task)
    if task.stage == "market":
        phases = {
            str(args.get("phase"))
            for name, args in calls
            if name == "get_market_regime" and isinstance(args, dict)
        }
        for phase in comparison_phases:
            if phase not in phases:
                missing.append(f"get_market_regime phase={phase}")
        flow_phases = {
            str(args.get("phase"))
            for name, args in calls
            if name == "get_systematic_flows" and isinstance(args, dict)
        }
        for phase in comparison_phases:
            if phase not in flow_phases:
                missing.append(f"get_systematic_flows phase={phase}")
        if not any(name == "get_data_quality" for name, _args in calls):
            missing.append("get_data_quality")
        if not any(name == "get_prior_stage_reports" for name, _args in calls):
            missing.append("get_prior_stage_reports")
        if not any(
            name == "get_events" and str(args.get("category")) in {"macro", "all"}
            for name, args in calls
            if isinstance(args, dict)
        ):
            missing.append("get_events category=macro")
        snapshots = {
            str(args.get("symbol") or "").upper()
            for name, args in calls
            if name == "get_instrument_snapshot"
            and isinstance(args, dict)
            and args.get("phase") == current_phase
        }
        for symbol in task.symbols:
            if symbol not in snapshots:
                missing.append(f"get_instrument_snapshot symbol={symbol} phase={current_phase}")
    elif task.stage == "theme":
        snapshots = {
            str(args.get("symbol") or "").upper()
            for name, args in calls
            if name == "get_instrument_snapshot"
            and isinstance(args, dict)
            and args.get("phase") == current_phase
        }
        comparisons = {
            str(args.get("symbol") or "").upper()
            for name, args in calls
            if name == "compare_instrument_observations" and isinstance(args, dict)
        }
        for symbol in task.symbols:
            if symbol not in snapshots:
                missing.append(f"get_instrument_snapshot symbol={symbol} phase={current_phase}")
            if len(comparison_phases) > 1 and symbol not in comparisons:
                missing.append(f"compare_instrument_observations symbol={symbol}")
    elif task.stage == "options":
        phases = {
            str(args.get("phase"))
            for name, args in calls
            if name == "get_option_overview" and isinstance(args, dict)
        }
        for phase in comparison_phases:
            if phase not in phases:
                missing.append(f"get_option_overview phase={phase}")
        try:
            output = json.loads(raw_output)
        except (json.JSONDecodeError, TypeError):
            output = {}
        expiration = (output.get("horizon") or {}).get("expiration")
        if output.get("status") == "decision" and expiration:
            if not any(
                name == "get_option_expiration_structure"
                and args.get("phase") == current_phase
                and str(args.get("expiration")) == str(expiration)
                for name, args in calls
                if isinstance(args, dict)
            ):
                missing.append(f"get_option_expiration_structure expiration={expiration}")
            if len(comparison_phases) > 1 and not any(
                name == "compare_option_observations"
                and str(args.get("expiration")) == str(expiration)
                for name, args in calls
                if isinstance(args, dict)
            ):
                missing.append(f"compare_option_observations expiration={expiration}")
    return missing


def _stage_prefetch_plan(
    task: AgentTask, evidence: EvidenceStore | None = None
) -> list[tuple[str, dict[str, Any]]]:
    """Deterministic minimum evidence loaded before the first model turn."""

    current_phase, comparison_phases = _task_observation_phases(task)
    if task.stage == "market":
        plan: list[tuple[str, dict[str, Any]]] = [
            *(('get_market_regime', {'phase': phase, 'symbols': task.symbols}) for phase in comparison_phases),
            *(("get_systematic_flows", {"phase": phase}) for phase in comparison_phases),
            ("get_data_quality", {"scope": "market", "symbol": None}),
            ("get_prior_stage_reports", {}),
            (
                "get_events",
                {
                    "category": "macro",
                    "subject": "market",
                    "status": [],
                    "result_state": "any",
                    "from_time": None,
                    "to_time": None,
                    "limit": 10,
                },
            ),
        ]
        plan.extend(
            (
                "get_instrument_snapshot",
                {
                    "symbol": symbol,
                    "phase": current_phase,
                    "sections": ["quote", "technical", "relative_strength", "theme", "quality"],
                },
            )
            for symbol in task.symbols
        )
        return plan
    if task.stage == "theme":
        plan: list[tuple[str, dict[str, Any]]] = []
        for symbol in task.symbols:
            plan.extend(
                [
                    (
                        "get_instrument_snapshot",
                        {
                            "symbol": symbol,
                            "phase": current_phase,
                            "sections": ["quote", "technical", "relative_strength", "theme", "quality"],
                        },
                    ),
                    *(
                        [("compare_instrument_observations", {"symbol": symbol})]
                        if len(comparison_phases) > 1
                        else []
                    ),
                ]
            )
        return plan
    if task.stage == "options" and task.target_symbol:
        plan = [
            ("get_option_overview", {"symbol": task.target_symbol, "phase": phase})
            for phase in comparison_phases
        ]
        expiration = (
            str(task.metadata["required_expiration"])
            if task.metadata.get("required_expiration")
            else _preferred_option_expiration(evidence, task.target_symbol)
        )
        if expiration:
            plan.append(
                (
                    "get_option_expiration_structure",
                    {
                        "symbol": task.target_symbol,
                        "phase": current_phase,
                        "expiration": expiration,
                    },
                )
            )
            if len(comparison_phases) > 1:
                plan.extend(
                    [
                        (
                            "get_option_expiration_structure",
                            {
                                "symbol": task.target_symbol,
                                "phase": comparison_phases[0],
                                "expiration": expiration,
                            },
                        ),
                        (
                            "compare_option_observations",
                            {"symbol": task.target_symbol, "expiration": expiration},
                        ),
                    ]
                )
        return plan
    return []


def _task_observation_phases(task: AgentTask) -> tuple[str, list[str]]:
    metadata = task.metadata if isinstance(task.metadata, dict) else {}
    current = str(metadata.get("current_observation") or task.decision_phase)
    raw = metadata.get("comparison_observations")
    phases = [str(value) for value in raw if value] if isinstance(raw, list) else []
    if not phases:
        phases = [current]
    if current not in phases:
        phases.append(current)
    return current, list(dict.fromkeys(phases))


def _preferred_option_expiration(
    evidence: EvidenceStore | None, symbol: str
) -> str | None:
    if evidence is None:
        return None
    options = (evidence._observation(evidence.current_phase).get("options") or {}).get("symbols") or []
    item = next(
        (
            value
            for value in options
            if isinstance(value, dict)
            and str(value.get("symbol") or "").upper() == symbol.upper()
        ),
        None,
    )
    expirations = [
        value
        for value in (item or {}).get("expirations") or []
        if isinstance(value, dict) and value.get("expiration")
    ]
    if not expirations:
        return None
    positive = [value for value in expirations if float(value.get("days_to_expiry") or 0) > 0]
    selected = sorted(
        positive or expirations,
        key=lambda value: (float(value.get("days_to_expiry") or 0), str(value.get("expiration"))),
    )[0]
    return str(selected["expiration"])
