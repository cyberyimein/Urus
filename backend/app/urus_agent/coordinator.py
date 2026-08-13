from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import utc_now
from app.repositories.agent import AIDecisionRepository
from app.urus_agent.contracts import AgentTask, DecisionResult
from app.urus_agent.evidence import EvidenceStore
from app.urus_agent.prompts import load_agent_profile
from app.urus_agent.providers.openrouter import LLMProvider, OpenRouterProvider
from app.urus_agent.reports import (
    build_ai_decision_report,
    build_equity_option_context,
    build_technical_report,
)
from app.urus_agent.runtime import UrusAgentRuntime
from app.urus_agent.tools.registry import ToolRegistry
from app.urus_agent.trace import InMemoryTraceSink, TraceNodeRecord


MARKET_SYMBOLS = ("SPY", "QQQ", "SMH", "IGV")
THEME_ORDER = ("半导体", "光概念", "SaaS", "大科技", "航天与新兴", "其他关注")
THEME_BENCHMARKS = {
    "半导体": ["SMH", "QQQ"],
    "光概念": ["SMH", "QQQ"],
    "SaaS": ["IGV", "QQQ"],
    "大科技": ["QQQ", "SPY"],
    "航天与新兴": ["SPY", "QQQ"],
    "其他关注": ["SPY", "QQQ"],
}
MAX_THEME_TASKS = 12
MANUAL_CURRENT_STATE_MAX_COMPLETION_TOKENS = 8_000
MANUAL_CURRENT_STATE_TIMEOUT_SECONDS = 240.0


@dataclass(frozen=True)
class CoordinatorRequest:
    workflow_run_id: str
    cutoff_time: datetime
    evidence: dict[str, object]
    symbols: list[str]
    dataset_key: str
    source_snapshot_ids: list[str]
    source_run_ids: list[str]
    decision_packet: dict[str, Any] | None = None
    decision_phase: str = "pre_close"
    trading_date: str = ""
    parent_session_id: str | None = None
    analysis_metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class CoordinatorResult:
    session_id: str
    technical_report: dict[str, Any]
    decision_report: dict[str, Any]
    equity_result: DecisionResult | None
    option_results: list[dict[str, Any]]


@dataclass(frozen=True)
class _InvocationJob:
    task: AgentTask
    run_id: str
    node_id: str


@dataclass(frozen=True)
class _InvocationOutcome:
    job: _InvocationJob
    result: DecisionResult
    trace_nodes: list[TraceNodeRecord]


class DecisionCoordinator:
    """Run dependency layers with bounded parallelism inside each layer."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        provider: LLMProvider | None = None,
        provider_factory: Callable[[], LLMProvider] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.provider = provider
        self.provider_factory = provider_factory
        self.repository = AIDecisionRepository(session)

    def execute(self, request: CoordinatorRequest) -> CoordinatorResult:
        agent_profile = load_agent_profile(request.decision_phase)
        evidence = (
            EvidenceStore(request.decision_packet)
            if request.decision_packet is not None
            else EvidenceStore.from_workflow_results(
                run_id=request.workflow_run_id,
                cutoff_time=request.cutoff_time,
                results=request.evidence,
                snapshot_id=request.source_snapshot_ids[0]
                if request.source_snapshot_ids
                else None,
            )
        )
        technical_report = build_technical_report(evidence)
        configured_theme_concurrency = max(
            1,
            min(int(self.settings.urus_agent_theme_max_concurrency), MAX_THEME_TASKS),
        )
        # A caller-provided provider may carry mutable test/client state. Only
        # a factory guarantees an independent Adapter for each worker.
        concurrent_providers = self.provider is None or self.provider_factory is not None
        theme_concurrency = configured_theme_concurrency if concurrent_providers else 1
        policy = {
            "decision_phase": request.decision_phase,
            "agent_profile": agent_profile["agent_name"],
            "option_analysis_mode": "equity_entry_context",
            "event_limit": self.settings.urus_agent_event_limit,
            "theme_max_concurrency": theme_concurrency,
            **(request.analysis_metadata or {}),
        }
        decision_session = self.repository.create_session(
            workflow_run_id=request.workflow_run_id,
            dataset_key=request.dataset_key,
            cutoff_time=request.cutoff_time,
            policy=policy,
            technical_report=technical_report,
            decision_phase=request.decision_phase,
            trading_date=request.trading_date,
            parent_session_id=request.parent_session_id,
        )
        trace = InMemoryTraceSink()
        evidence_node = trace.start_node(
            node_type="evidence",
            label="Frozen evidence + Technical Report",
            lane="Preparation",
            input_summary={
                "dataset_key": request.dataset_key,
                "cutoff_time": request.cutoff_time.isoformat(),
                "decision_phase": request.decision_phase,
                "agent_profile": agent_profile["agent_name"],
            },
        )
        trace.finish_node(
            evidence_node,
            output_summary={
                "technical_schema": technical_report.get("schema_version"),
                "option_symbols": evidence.overview().get("option_symbols", []),
            },
        )
        self.repository.save_trace_nodes(decision_session.id, trace.nodes[:])

        equity_result: DecisionResult | None = None
        synthesis_run_id: str | None = None
        option_results: list[dict[str, Any]] = []
        market_summary: dict[str, Any] = {}
        theme_summaries: list[dict[str, Any]] = []
        try:
            available_symbols = _available_symbols(evidence)
            requested_symbols = _ordered_unique(request.symbols)
            if request.decision_phase == "current_state":
                current = evidence.packet.get("observations", {}).get(evidence.current_phase) or {}
                equity_option_context = build_equity_option_context(evidence, requested_symbols)
                current_task = self._task(
                    request,
                    decision_session.id,
                    symbols=requested_symbols,
                    stage="synthesis",
                    sequence=1,
                    metadata={
                        "scope_kind": "manual_current_state",
                        "current_state_evidence": {
                            "market": _compact_current_market(current.get("market") or {}),
                            "instruments": _compact_current_instruments(
                                current.get("instruments") or [], requested_symbols
                            ),
                            "systematic_flows": current.get("systematic_flows") or {},
                            "equity_option_context": equity_option_context,
                            "events": _compact_current_events(evidence.packet.get("events") or {}),
                            "quality": evidence.packet.get("quality") or {},
                            "prior_reports": _compact_prior_reports(
                                evidence.packet.get("prior_reports") or {}
                            ),
                        },
                    },
                )
                current_job = self._begin_job(
                    current_task,
                    trace,
                    label=f"{agent_profile['agent_name']} · Current State",
                    lane="Current State",
                    parent_node_id=evidence_node,
                    depends_on_node_ids=[evidence_node],
                )
                current_outcome = self._run_job(current_job, evidence)
                self._persist_outcome(current_outcome, trace)
                equity_result = current_outcome.result
                synthesis_run_id = current_job.run_id
                report = build_ai_decision_report(
                    session_id=decision_session.id,
                    run_id=request.workflow_run_id,
                    cutoff_time=request.cutoff_time,
                    equity_run_id=synthesis_run_id
                    if equity_result.status == "succeeded"
                    else None,
                    equity_output=equity_result.output
                    if equity_result.status == "succeeded"
                    else None,
                    market_analysis=_result_summary(current_outcome),
                    theme_analyses=[],
                    candidate_gate=[],
                    option_decisions=[],
                    equity_option_context=equity_option_context,
                    quality=technical_report.get("quality") or {},
                    decision_phase=request.decision_phase,
                    agent_profile=str(agent_profile["agent_name"]),
                    trading_date=request.trading_date,
                    parent_report_id=None,
                    evidence=evidence,
                    analysis_metadata=request.analysis_metadata or {},
                )
                assembly_node = trace.start_node(
                    node_type="assembly",
                    label="Manual Current-State Report Assembly",
                    lane="Assembly",
                    parent_node_id=current_job.node_id,
                    depends_on_node_ids=[current_job.node_id],
                    input_summary={"model_invocation_count": 1},
                )
                trace.finish_node(
                    assembly_node,
                    output_summary={
                        "schema_version": report["schema_version"],
                        "status": report["status"],
                    },
                )
                self.repository.save_trace_nodes(decision_session.id, trace.nodes)
                self.repository.update_session(
                    decision_session.id,
                    status=report["status"],
                    decision_report_schema_version=report["schema_version"],
                    decision_report_json=report,
                    equity_decision_run_id=synthesis_run_id,
                    completed_at=utc_now(),
                )
                return CoordinatorResult(
                    session_id=decision_session.id,
                    technical_report=technical_report,
                    decision_report=report,
                    equity_result=equity_result,
                    option_results=[],
                )
            market_symbols = [symbol for symbol in MARKET_SYMBOLS if symbol in available_symbols]

            market_task = self._task(
                request,
                decision_session.id,
                symbols=market_symbols,
                stage="market",
                sequence=1,
                metadata={"scope_kind": "market", "benchmark_symbols": market_symbols},
            )
            market_job = self._begin_job(
                market_task,
                trace,
                label=f"{agent_profile['agent_name']} · Market",
                lane="Market",
                parent_node_id=evidence_node,
            )
            market_outcome = self._run_job(market_job, evidence)
            self._persist_outcome(market_outcome, trace)
            market_summary = _result_summary(market_outcome)

            theme_jobs: list[_InvocationJob] = []
            for offset, (theme, symbols) in enumerate(
                _theme_scopes(evidence, requested_symbols), start=2
            ):
                benchmarks = [
                    symbol
                    for symbol in THEME_BENCHMARKS.get(theme, ["QQQ"])
                    if symbol in available_symbols
                ]
                theme_task = self._task(
                    request,
                    decision_session.id,
                    symbols=symbols,
                    stage="theme",
                    sequence=offset,
                    parent_run_id=market_job.run_id,
                    metadata={
                        "scope_kind": "theme",
                        "theme": theme,
                        "benchmark_symbols": benchmarks,
                        "market_result": market_summary,
                    },
                )
                theme_jobs.append(
                    self._begin_job(
                        theme_task,
                        trace,
                        label=f"{agent_profile['agent_name']} · Theme · {theme}",
                        lane="Themes",
                        parent_node_id=market_job.node_id,
                        depends_on_node_ids=[market_job.node_id],
                    )
                )

            theme_outcomes = self._run_jobs(
                theme_jobs,
                evidence,
                max_concurrency=theme_concurrency,
                thread_name_prefix="urus-theme",
            )
            for outcome in theme_outcomes:
                self._persist_outcome(outcome, trace)
                theme_summaries.append(_result_summary(outcome))

            equity_option_context = build_equity_option_context(evidence, requested_symbols)
            systematic_flow_context = evidence.systematic_flows(evidence.current_phase)["data"]
            synthesis_sequence = 2 + len(theme_jobs)
            synthesis_task = self._task(
                request,
                decision_session.id,
                symbols=requested_symbols,
                stage="synthesis",
                sequence=synthesis_sequence,
                parent_run_id=market_job.run_id,
                metadata={
                    "scope_kind": "synthesis",
                    "market_result": market_summary,
                    "theme_results": theme_summaries,
                    "equity_option_context": equity_option_context,
                    "systematic_flow_context": systematic_flow_context,
                    "prior_reports": evidence.packet.get("prior_reports") or {},
                },
            )
            synthesis_job = self._begin_job(
                synthesis_task,
                trace,
                label=f"{agent_profile['agent_name']} · Synthesis",
                lane="Synthesis",
                parent_node_id=market_job.node_id,
                depends_on_node_ids=[market_job.node_id, *[job.node_id for job in theme_jobs]],
            )
            synthesis_run_id = synthesis_job.run_id
            synthesis_outcome = self._run_job(synthesis_job, evidence)
            self._persist_outcome(synthesis_outcome, trace)
            equity_result = synthesis_outcome.result

            report = build_ai_decision_report(
                session_id=decision_session.id,
                run_id=request.workflow_run_id,
                cutoff_time=request.cutoff_time,
                equity_run_id=synthesis_run_id if equity_result.status == "succeeded" else None,
                equity_output=equity_result.output if equity_result.status == "succeeded" else None,
                market_analysis=market_summary,
                theme_analyses=theme_summaries,
                candidate_gate=[],
                option_decisions=option_results,
                equity_option_context=equity_option_context,
                quality=technical_report.get("quality") or {},
                decision_phase=request.decision_phase,
                agent_profile=str(agent_profile["agent_name"]),
                trading_date=request.trading_date,
                parent_report_id=request.parent_session_id,
                evidence=evidence,
                analysis_metadata=request.analysis_metadata or {},
            )
            assembly_node = trace.start_node(
                node_type="assembly",
                label="AI Decision Report Assembly",
                lane="Assembly",
                parent_node_id=synthesis_job.node_id,
                depends_on_node_ids=[synthesis_job.node_id],
                input_summary={
                    "theme_result_count": len(theme_summaries),
                    "equity_option_context_count": sum(
                        1 for item in equity_option_context if item.get("available")
                    ),
                },
            )
            trace.finish_node(
                assembly_node,
                output_summary={"schema_version": report["schema_version"], "status": report["status"]},
            )
            self.repository.save_trace_nodes(decision_session.id, trace.nodes)
            self.repository.update_session(
                decision_session.id,
                status=report["status"],
                decision_report_schema_version=report["schema_version"],
                decision_report_json=report,
                equity_decision_run_id=synthesis_run_id,
                completed_at=utc_now(),
            )
            return CoordinatorResult(
                session_id=decision_session.id,
                technical_report=technical_report,
                decision_report=report,
                equity_result=equity_result,
                option_results=option_results,
            )
        except Exception as exc:
            self.repository.save_trace_nodes(decision_session.id, trace.nodes)
            self.repository.update_session(
                decision_session.id,
                status="failed",
                error_code="decision_coordinator_error",
                error_message=str(exc),
                completed_at=utc_now(),
            )
            raise

    def _task(
        self,
        request: CoordinatorRequest,
        session_id: str,
        *,
        symbols: list[str],
        stage: str,
        sequence: int,
        metadata: dict[str, Any],
        parent_run_id: str | None = None,
    ) -> AgentTask:
        return AgentTask(
            task_type="equity_ranking",
            dataset_key=request.dataset_key,
            source_run_ids=request.source_run_ids,
            source_snapshot_ids=request.source_snapshot_ids,
            cutoff_time=request.cutoff_time,
            symbols=symbols,
            requested_skill="urus-equity-decision",
            decision_phase=request.decision_phase,  # type: ignore[arg-type]
            workflow_run_id=request.workflow_run_id,
            decision_session_id=session_id,
            parent_decision_run_id=parent_run_id,
            stage=stage,  # type: ignore[arg-type]
            sequence=sequence,
            metadata={**metadata, **_phase_metadata(load_agent_profile(request.decision_phase), request)},
        )

    def _begin_job(
        self,
        task: AgentTask,
        trace: InMemoryTraceSink,
        *,
        label: str,
        lane: str,
        parent_node_id: str,
        depends_on_node_ids: list[str] | None = None,
    ) -> _InvocationJob:
        run = self.repository.begin_run(task)
        task = task.model_copy(update={"decision_run_id": run.id})
        node_id = trace.start_node(
            node_type="model",
            label=label,
            lane=lane,
            parent_node_id=parent_node_id,
            depends_on_node_ids=depends_on_node_ids,
            input_summary={
                "stage": task.stage,
                "symbols": task.symbols,
                "theme": task.metadata.get("theme"),
            },
            decision_run_id=run.id,
        )
        return _InvocationJob(task=task, run_id=run.id, node_id=node_id)

    def _run_jobs(
        self,
        jobs: list[_InvocationJob],
        evidence: EvidenceStore,
        *,
        max_concurrency: int,
        thread_name_prefix: str,
    ) -> list[_InvocationOutcome]:
        if not jobs:
            return []
        with ThreadPoolExecutor(
            max_workers=min(max_concurrency, len(jobs)),
            thread_name_prefix=thread_name_prefix,
        ) as executor:
            # executor.map preserves task order while still allowing the model
            # requests to overlap, which keeps persistence and trace ordering deterministic.
            return list(executor.map(lambda job: self._run_job(job, evidence), jobs))

    def _run_job(self, job: _InvocationJob, evidence: EvidenceStore) -> _InvocationOutcome:
        worker_trace = InMemoryTraceSink()
        max_completion_tokens = (
            MANUAL_CURRENT_STATE_MAX_COMPLETION_TOKENS
            if job.task.decision_phase == "current_state"
            else None
        )
        result = self._runtime(
            worker_trace,
            max_completion_tokens_override=max_completion_tokens,
            timeout_seconds_override=(
                MANUAL_CURRENT_STATE_TIMEOUT_SECONDS
                if job.task.decision_phase == "current_state"
                else None
            ),
            provider_max_retries_override=(
                0 if job.task.decision_phase == "current_state" else None
            ),
            reasoning_override=(
                {"enabled": False, "exclude": True}
                if job.task.decision_phase == "current_state"
                else None
            ),
            max_tool_iterations_override=(
                2 if job.task.decision_phase == "current_state" else None
            ),
        ).decide(
            job.task,
            evidence,
            trace_parent_node_id=job.node_id,
        )
        return _InvocationOutcome(job=job, result=result, trace_nodes=worker_trace.nodes)

    def _persist_outcome(self, outcome: _InvocationOutcome, trace: InMemoryTraceSink) -> None:
        if outcome.result.status == "succeeded":
            trace.finish_node(
                outcome.job.node_id,
                output_summary={
                    "status": outcome.result.status,
                    "tool_call_count": outcome.result.tool_call_count,
                },
                metrics={"duration_ms": outcome.result.duration_ms},
            )
        else:
            trace.fail_node(
                outcome.job.node_id,
                error_code=outcome.result.error_code or "agent_invocation_failed",
                error_message=outcome.result.error_message or "Agent Invocation failed",
                output_summary={"status": outcome.result.status},
            )
        trace.extend(outcome.trace_nodes)
        self.repository.complete_run(outcome.job.run_id, outcome.job.task, outcome.result)

    def _runtime(
        self,
        trace: InMemoryTraceSink,
        *,
        max_completion_tokens_override: int | None = None,
        timeout_seconds_override: float | None = None,
        provider_max_retries_override: int | None = None,
        reasoning_override: dict[str, Any] | None = None,
        max_tool_iterations_override: int | None = None,
    ) -> UrusAgentRuntime:
        provider = self._new_provider(
            max_completion_tokens_override=max_completion_tokens_override,
            timeout_seconds_override=timeout_seconds_override,
            provider_max_retries_override=provider_max_retries_override,
            reasoning_override=reasoning_override,
        )
        return UrusAgentRuntime(
            provider,
            registry=ToolRegistry(event_limit=self.settings.urus_agent_event_limit),
            max_tool_iterations=(
                max_tool_iterations_override
                if max_tool_iterations_override is not None
                else self.settings.urus_agent_max_tool_iterations
            ),
            max_output_bytes=None,
            max_tool_result_bytes=self.settings.urus_agent_max_tool_result_bytes,
            max_total_tool_result_bytes=self.settings.urus_agent_max_total_tool_result_bytes,
            max_context_bytes=self.settings.urus_agent_max_context_bytes,
            max_raw_response_bytes=self.settings.urus_agent_max_raw_response_bytes,
            max_total_tool_calls=self.settings.urus_agent_max_total_tool_calls,
            enforce_stage_tool_requirements=self.settings.urus_agent_enforce_stage_tools,
            trace_sink=trace,
        )

    def _new_provider(
        self,
        *,
        max_completion_tokens_override: int | None = None,
        timeout_seconds_override: float | None = None,
        provider_max_retries_override: int | None = None,
        reasoning_override: dict[str, Any] | None = None,
    ) -> LLMProvider:
        if self.provider_factory is not None:
            return self.provider_factory()
        if self.provider is not None:
            return self.provider
        return OpenRouterProvider(
            api_key=self.settings.openrouter_api_key or "",
            model=self.settings.urus_agent_model,
            base_url=self.settings.openrouter_base_url,
            timeout_seconds=(
                timeout_seconds_override
                if timeout_seconds_override is not None
                else self.settings.urus_agent_timeout_seconds
            ),
            max_completion_tokens=(
                max_completion_tokens_override
                if max_completion_tokens_override is not None
                else self.settings.urus_agent_max_completion_tokens
            ),
            temperature=self.settings.urus_agent_temperature,
            input_cost_per_million=self.settings.urus_agent_input_cost_per_million,
            output_cost_per_million=self.settings.urus_agent_output_cost_per_million,
            max_retries=(
                provider_max_retries_override
                if provider_max_retries_override is not None
                else 1
            ),
            reasoning=reasoning_override,
        )


def _available_symbols(evidence: EvidenceStore) -> set[str]:
    return {
        str(item.get("symbol") or "").upper()
        for item in evidence.overview().get("symbols", [])
        if isinstance(item, dict) and item.get("symbol")
    }


def _ordered_unique(symbols: list[str]) -> list[str]:
    return list(dict.fromkeys(str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()))


def _pick(source: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: source.get(field) for field in fields if field in source}


def _compact_quote(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return _pick(
        value,
        (
            "symbol", "label", "last_price", "regular_price", "change_percent",
            "regular_change_percent", "previous_close", "open_price", "high_price",
            "low_price", "volume", "premarket_price", "premarket_volume",
            "premarket_change_percent", "afterhours_price", "afterhours_volume",
            "afterhours_change_percent", "quote_time", "session", "session_price_source",
            "source", "data_mode", "quality_status", "quality_warnings",
        ),
    )


def _compact_current_market(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "primary": _compact_quote(value.get("primary")),
        "trend": value.get("trend"),
        "technical": value.get("technical") or {},
        "cross_asset_quotes": [
            _compact_quote(item)
            for item in value.get("cross_asset_quotes") or []
            if isinstance(item, dict)
        ],
        "vix": value.get("vix") or {},
        "quality_status": value.get("quality_status"),
        "quality_warnings": value.get("quality_warnings") or [],
    }


def _compact_current_instruments(values: Any, symbols: list[str]) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    allowed = set(symbols)
    compact: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        symbol = str(value.get("symbol") or "").upper()
        if not symbol or (allowed and symbol not in allowed):
            continue
        compact.append(
            {
                **_pick(value, ("symbol", "asset_type", "theme", "themes")),
                "quote": _compact_quote(value.get("quote")),
                "trend": value.get("trend"),
                "technical": value.get("technical") or {},
                "relative_strength": value.get("relative_strength") or {},
                "quality_status": value.get("quality_status"),
                "quality_warnings": value.get("quality_warnings") or [],
            }
        )
    return compact


def _compact_current_events(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"records": []}
    fields = (
        "id", "category", "subject", "title", "status", "scheduled_at",
        "released_at", "importance", "actual", "consensus", "previous", "source",
    )
    records = [
        _pick(item, fields)
        for item in value.get("records") or []
        if isinstance(item, dict)
    ]
    return {"captured_at": value.get("captured_at"), "records": records[:30]}


def _compact_prior_reports(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    fields = (
        "report_id", "cutoff_time", "decision_phase", "status", "market_regime",
        "forecast", "review", "portfolio_warnings",
    )
    return {
        key: _pick(report, fields) if isinstance(report, dict) else None
        for key, report in value.items()
    }


def _theme_scopes(evidence: EvidenceStore, requested_symbols: list[str]) -> list[tuple[str, list[str]]]:
    requested = set(requested_symbols)
    observations = evidence.packet.get("observations") or {}
    close = observations.get(evidence.current_phase) or {}
    items = [item for item in close.get("instruments", []) if isinstance(item, dict)]
    # Themes are descriptive tags carried inside each instrument record. They
    # must not multiply paid Agent calls: one symbol belongs to one primary
    # analysis scope while the model can still inspect all of its tags.
    primary_by_symbol: dict[str, str] = {}
    custom_order: list[str] = []
    for item in items:
        symbol = str(item.get("symbol") or "").upper()
        if not symbol or symbol not in requested:
            continue
        labels = [str(item.get("theme") or ""), *[str(value) for value in item.get("themes", [])]]
        candidates = [label for label in labels if label and label != "ETF"]
        if not candidates and str(item.get("asset_type") or "").lower() != "etf":
            candidates = ["其他关注"]
        candidates = list(dict.fromkeys(candidates))
        if not candidates:
            continue
        primary = candidates[0]
        primary_by_symbol[symbol] = primary
        if primary not in THEME_ORDER and primary not in custom_order:
            custom_order.append(primary)

    used = set(primary_by_symbol.values())
    theme_order = [theme for theme in THEME_ORDER if theme in used]
    theme_order.extend(theme for theme in custom_order if theme in used)
    if len(theme_order) > MAX_THEME_TASKS:
        if "其他关注" in theme_order[:MAX_THEME_TASKS]:
            allowed = theme_order[:MAX_THEME_TASKS]
        else:
            allowed = [*theme_order[: MAX_THEME_TASKS - 1], "其他关注"]
    else:
        allowed = theme_order
    allowed_set = set(allowed)
    owned: dict[str, list[str]] = {theme: [] for theme in allowed}
    for symbol, primary in primary_by_symbol.items():
        owner = primary if primary in allowed_set else "其他关注"
        owned.setdefault(owner, []).append(symbol)
    order = {symbol: index for index, symbol in enumerate(requested_symbols)}
    return [
        (theme, sorted(symbols, key=lambda symbol: order.get(symbol, len(order))))
        for theme, symbols in owned.items()
        if symbols
    ]


def _phase_metadata(profile: dict[str, Any], request: CoordinatorRequest) -> dict[str, Any]:
    comparison = profile.get("comparison_observations")
    return {
        # current_state is a manual, non-scoreable observation and must not
        # inherit the official v3 daily-decision response contract.
        "daily_cycle": request.decision_phase != "current_state",
        "decision_phase": request.decision_phase,
        "agent_profile": profile.get("agent_name"),
        "forecast_horizon": profile.get("forecast_horizon"),
        "current_observation": profile.get("current_observation") or request.decision_phase,
        "comparison_observations": list(comparison) if isinstance(comparison, list) else [],
        "trading_date": request.trading_date,
        "parent_report_id": request.parent_session_id,
    }


def _result_summary(outcome: _InvocationOutcome) -> dict[str, Any]:
    return {
        "stage": outcome.job.task.stage,
        "theme": outcome.job.task.metadata.get("theme"),
        "decision_run_id": outcome.job.run_id,
        "status": outcome.result.status,
        "output": outcome.result.output if outcome.result.status == "succeeded" else None,
        "error_code": outcome.result.error_code,
        "error_message": outcome.result.error_message,
    }
