from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.core.time import as_utc, utc_now
from app.integrations.anomalo import (
    DisabledAnomaloAdapter,
    HttpAnomaloAdapter,
    MockAnomaloAdapter,
)
from app.integrations.decision import MockDecisionAdapter, UrusDecisionAdapter
from app.integrations.fred import FredDailyAdapter
from app.integrations.macro import FallbackDailyMacroAdapter
from app.integrations.moomoo import DisabledMoomooAdapter, OpenDMarketAdapter
from app.integrations.moomoo_options import (
    DisabledOptionsAdapter,
    MoomooOptionsAdapter,
    OptionsCollectorAdapter,
)
from app.integrations.yahoo import YahooDailyAdapter
from app.models import RunModel, RunStatus, StepStatus
from app.repositories import EventRepository, InstrumentUniverseRepository, RunRepository
from app.repositories.agent import AIDecisionRepository
from app.repositories.capital_flows import CapitalFlowRepository
from app.repositories.report_display import ReportDisplayRepository
from app.schemas.enums import StepCodeValue
from app.schemas.read_model import (
    FrontendReadModel,
    RunCreateRequest,
    RunCreateResponse,
    RunDetailResponse,
    RunListItem,
    SnapshotResponse,
    StepRunResponse,
)
from app.schemas.universe import InstrumentConfig
from app.urus_agent.packet import build_stage_decision_packet
from app.urus_agent.coordinator import CoordinatorRequest, DecisionCoordinator
from app.urus_agent.display_projection import build_report_display_projection, projection_content_sha256
from app.urus_agent.prompts import load_agent_profile
from app.workflows import (
    DEFAULT_STEP_CODES,
    DecisionStep,
    InstrumentCollectorStep,
    InstrumentCTAProxyStep,
    InstrumentEventSummaryStep,
    MarketCollectorStep,
    MarketCTAProxyStep,
    MarketEventSummaryStep,
    OptionsCollectorStep,
    OutputStep,
    RunContext,
    StepResult,
    WorkflowPipeline,
)
from app.workflows.base import data_state_for
from app.workflows.cta import build_systematic_flows
from app.services.capital_flow import CapitalFlowService

logger = logging.getLogger(__name__)


class RunService:
    """Application service that coordinates workflow execution and persistence."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.repository = RunRepository(session)
        self.event_repository = EventRepository(session)
        self.universe_repository = InstrumentUniverseRepository(session)
        self.settings = settings

    def initialize_run(self, request: RunCreateRequest) -> RunCreateResponse:
        """Persist a pollable workflow shell before background execution."""

        universe = self.universe_repository.ensure_default(self.settings)
        universe_response = self.universe_repository.response(universe)
        self._validate_request(request, universe_response.items)
        run_id = str(uuid4())
        cutoff_time = utc_now()
        self.repository.create_run(
            run_id=run_id,
            run_type=request.run_type.value,
            cutoff_time=cutoff_time,
            universe_version_id=universe.id,
            universe_content_sha256=universe.content_sha256,
        )
        self.repository.create_steps(
            run_id,
            [
                (str(uuid4()), position, code, StepStatus.PENDING.value)
                for position, code in enumerate(DEFAULT_STEP_CODES, start=1)
            ],
        )
        return RunCreateResponse(
            run_id=run_id,
            status=RunStatus.PENDING.value,
            snapshot_id=None,
        )

    def create_run(
        self,
        request: RunCreateRequest,
        *,
        queued_run_id: str | None = None,
    ) -> RunCreateResponse:
        if queued_run_id:
            run = self.repository.get_run(queued_run_id)
            if run is None or run.run_type != request.run_type.value:
                raise AppError("找不到待执行的分析任务", code="queued_run_not_found", status_code=404)
            if run.status != RunStatus.PENDING.value:
                raise AppError("分析任务已经开始或完成", code="queued_run_not_pending", status_code=409)
            run_id = run.id
            cutoff_time = as_utc(run.cutoff_time)
            step_models = list(run.steps)
            universe = (
                self.universe_repository.get(run.universe_version_id)
                if run.universe_version_id
                else self.universe_repository.ensure_default(self.settings)
            )
        else:
            universe = self.universe_repository.ensure_default(self.settings)
            run_id = str(uuid4())
            cutoff_time = utc_now()
            run = self.repository.create_run(
                run_id=run_id,
                run_type=request.run_type.value,
                cutoff_time=cutoff_time,
                universe_version_id=universe.id,
                universe_content_sha256=universe.content_sha256,
            )
            step_models = self.repository.create_steps(
                run_id,
                [
                    (str(uuid4()), position, code, StepStatus.PENDING.value)
                    for position, code in enumerate(DEFAULT_STEP_CODES, start=1)
                ],
            )
        if universe is None:
            raise AppError("找不到本次任务冻结的标的设置", code="universe_version_not_found", status_code=500)
        universe_response = self.universe_repository.response(universe)
        symbols, decision_enabled = self._validate_request(request, universe_response.items)
        scopes = universe_response.derived
        step_by_code = {step.step_code: step for step in step_models}
        started_at = utc_now()
        self.repository.update_run(run, status=RunStatus.RUNNING.value, started_at=started_at)

        is_manual = request.run_type.value == "manual_analysis"
        session_context = self._session_context(cutoff_time) if is_manual else request.run_type.value

        market_adapter = self._build_market_adapter(scopes.market_symbols)
        capital_flow_service = (
            CapitalFlowService(
                CapitalFlowRepository(self.repository.session),
                market_adapter,
                symbols=self.settings.capital_flow_symbol_list,
                calendar_name=self.settings.market_calendar,
                cache_days=self.settings.capital_flow_cache_days,
                projection_days=self.settings.capital_flow_projection_days,
            )
            if self.settings.moomoo_enabled
            else None
        )
        options_adapter = self._build_options_adapter(scopes.option_symbols)
        macro_adapter = self._build_macro_adapter()
        anomalo_adapter = self._build_anomalo_adapter(
            simulate=request.simulate_macro_event or request.simulate_instrument_event
        )
        instrument_symbols = scopes.instrument_symbols
        context = RunContext(
            run_id=run_id,
            run_type=request.run_type.value,
            cutoff_time=cutoff_time,
            symbols=symbols,
            instrument_symbols=instrument_symbols,
            event_instrument_symbols=scopes.event_symbols,
            simulate_macro_event=request.simulate_macro_event,
            simulate_instrument_event=request.simulate_instrument_event,
            fail_step=request.fail_step.value if request.fail_step else None,
            market_adapter=market_adapter,
            macro_adapter=macro_adapter,
            moomoo_adapter=market_adapter,
            options_adapter=options_adapter,
            anomalo_adapter=anomalo_adapter,
            decision_adapter=self._build_decision_adapter(enabled=decision_enabled),
            decision_enabled=decision_enabled,
            event_repository=self.event_repository,
            capital_flow_service=capital_flow_service,
            expected_events_enabled=self.settings.expected_events_enabled,
            breaking_events_enabled=self.settings.breaking_events_enabled,
            scheduled_event_agent=self.settings.anomalo_scheduled_agent,
            breaking_event_agent=self.settings.anomalo_breaking_agent,
            event_horizon_days=self.settings.event_discovery_horizon_days,
            workflow_research_variant=self.settings.workflow_research_variant.lower(),
            cta_proxy_symbols=scopes.cta_proxy_symbols,
            universe_version_id=universe.id,
            universe_content_sha256=universe.content_sha256,
            universe_items_by_symbol={item.symbol: item.model_dump(mode="json") for item in universe_response.items},
            trigger_type="manual" if is_manual else "scheduled",
            analysis_mode="current_state" if is_manual else "official_cycle",
            session_context=session_context,
            official_cycle=not is_manual,
            eligible_for_scoring=not is_manual,
            updates_official_cta_state=not is_manual,
        )
        # Allocate the immutable workflow snapshot identity before any
        # decision step.  Stage 4B must reference the same frozen snapshot
        # that is persisted by the output step.
        context.snapshot_id = str(uuid4())
        pipeline = self._build_pipeline()
        snapshot_payload: dict[str, object] | None = None
        snapshot_id: str | None = None
        option_persistence_payload: dict[str, object] | None = None
        instrument_persistence_payload: dict[str, object] | None = None

        for step in pipeline.steps:
            if step.code == "4":
                self._prepare_decision_dataset(context)
            model = step_by_code[step.code]
            step_started = utc_now()
            self.repository.update_step(
                model,
                status=StepStatus.RUNNING.value,
                started_at=step_started,
            )
            if step.code == "5" and context.snapshot_id is None:
                context.snapshot_id = str(uuid4())
            logger.info(
                "workflow step started run_id=%s step_code=%s snapshot_id=%s",
                run_id,
                step.code,
                context.snapshot_id,
            )

            try:
                result = step.execute(context)
            except Exception as exc:  # defensive boundary for replaceable steps
                result = StepResult(
                    status=StepStatus.FAILED,
                    summary=f"步骤 {step.code} 未处理异常。",
                    error_message=str(exc),
                )
                logger.exception("workflow step crashed run_id=%s step_code=%s", run_id, step.code)
            if step.code == "2" and isinstance(result.payload.get("_persistence"), dict):
                option_persistence_payload = result.payload.pop("_persistence")
            if step.code == "1a" and isinstance(result.payload.get("_cta_input"), dict):
                context.cta_market_input = result.payload.pop("_cta_input")
            if step.code == "3a" and isinstance(result.payload.get("_persistence"), dict):
                instrument_persistence_payload = result.payload.pop("_persistence")
                context.instrument_persistence_input = dict(instrument_persistence_payload)
            context.results[step.code] = result
            completed_at = utc_now()
            self.repository.update_step(
                model,
                status=result.status.value,
                completed_at=completed_at,
                summary=result.summary,
                error_message=result.error_message,
                payload=result.payload or None,
            )
            logger.info(
                "workflow step completed run_id=%s step_code=%s status=%s snapshot_id=%s",
                run_id,
                step.code,
                result.status.value,
                context.snapshot_id,
            )

        for adapter in (market_adapter, macro_adapter, options_adapter, anomalo_adapter):
            close = getattr(adapter, "close", None)
            if close:
                close()

        final_status = self._final_run_status(context.results)
        output_result = context.results.get("5")
        if output_result and output_result.status == StepStatus.SUCCEEDED:
            snapshot_payload = dict(output_result.payload)
        elif context.snapshot_id is not None:
            # Even a critical failure leaves a readable error snapshot for the UI.
            fallback_context = context
            original_fail_step = fallback_context.fail_step
            fallback_context.fail_step = None
            fallback = OutputStep().execute(fallback_context)
            fallback_context.fail_step = original_fail_step
            snapshot_payload = dict(fallback.payload)
            snapshot_payload.setdefault("data_quality", {})
            quality = snapshot_payload["data_quality"]
            if isinstance(quality, dict):
                quality.setdefault("errors", [])
                if output_result and output_result.error_message:
                    quality["errors"].append(f"5: {output_result.error_message}")
                quality["status"] = "error"

        if snapshot_payload is not None and context.snapshot_id is not None:
            snapshot_id = context.snapshot_id
            snapshot_payload["run_status"] = final_status.value
            snapshot_payload["snapshot_id"] = snapshot_id
            if isinstance(snapshot_payload.get("steps"), list):
                output_step_present = any(
                    isinstance(item, dict) and item.get("code") == "5"
                    for item in snapshot_payload["steps"]
                )
                if output_result and output_result.status != StepStatus.SUCCEEDED:
                    for item in snapshot_payload["steps"]:
                        if isinstance(item, dict) and item.get("code") == "5":
                            item["status"] = output_result.status.value
                            item["summary"] = output_result.summary
                            item["error_message"] = output_result.error_message
                elif not output_step_present:
                    output_step = context.results.get("5")
                    snapshot_payload["steps"].append(
                        {
                            "code": "5",
                            "label": "5 · 输出 read model",
                            "status": output_step.status.value if output_step else StepStatus.FAILED.value,
                            "summary": output_step.summary if output_step else "输出结果不可用。",
                            "error_message": output_step.error_message if output_step else None,
                        }
                    )
            quality_status = self._quality_status(snapshot_payload)
            options_result = context.results.get("2")
            instrument_result = context.results.get("3a")
            self.repository.save_snapshot_with_options(
                snapshot_id=snapshot_id,
                run_id=run_id,
                schema_version=str(snapshot_payload.get("schema_version", "1.0")),
                cutoff_time=cutoff_time,
                created_at=utc_now(),
                quality_status=quality_status,
                payload=snapshot_payload,
                options_payload=(
                    dict(options_result.payload)
                    if options_result and options_result.payload
                    else None
                ),
                persistence_payload=(
                    dict(option_persistence_payload) if option_persistence_payload else None
                ),
                instrument_payload=(
                    dict(instrument_result.payload)
                    if instrument_result and instrument_result.payload
                    else None
                ),
                instrument_persistence_payload=(
                    dict(instrument_persistence_payload)
                    if instrument_persistence_payload
                    else None
                ),
            )

            # The normalized option tables are committed before this point.
            # Build the report-only chart projection from those complete rows;
            # the compact decision packet remains unchanged.
            self._persist_report_display_projection(context)

            output_model = step_by_code["5"]
            self.repository.update_step(output_model, payload=snapshot_payload)
        errors = [
            f"{code}: {result.error_message}"
            for code, result in context.results.items()
            if result.status == StepStatus.FAILED and result.error_message
        ]
        completed_at = utc_now()
        self.repository.update_run(
            run,
            status=final_status.value,
            completed_at=completed_at,
            snapshot_id=snapshot_id,
            error_message="; ".join(errors) if errors else None,
        )
        logger.info(
            "workflow completed run_id=%s status=%s snapshot_id=%s",
            run_id,
            final_status.value,
            snapshot_id,
        )
        return RunCreateResponse(run_id=run_id, status=final_status.value, snapshot_id=snapshot_id)

    def _prepare_decision_dataset(self, context: RunContext) -> None:
        """Build one phase-specific packet and attach its report lineage."""

        phase = "current_state" if context.run_type == "manual_analysis" else context.run_type
        if phase not in {"pre_market", "pre_close", "post_close_review", "current_state"}:
            context.decision_pair_status = "unsupported_phase"
            context.decision_pair_reason = f"Unsupported decision phase: {phase}"
            return
        market_tz = ZoneInfo(self.settings.market_timezone)
        local_cutoff = as_utc(context.cutoff_time).astimezone(market_tz)
        local_start = datetime.combine(local_cutoff.date(), time.min, tzinfo=market_tz)
        cutoff_start = local_start.astimezone(UTC)
        cutoff_end = (local_start + timedelta(days=1)).astimezone(UTC)
        current_payload = self._current_decision_snapshot_payload(context)
        current_observation = {
            "run": {
                "id": context.run_id,
                "run_type": phase,
                "status": "running",
                "cutoff_time": as_utc(context.cutoff_time).isoformat(),
                "completed_at": None,
            },
            "snapshot": {
                "id": context.snapshot_id,
                "schema_version": "1.0",
                "cutoff_time": as_utc(context.cutoff_time).isoformat(),
                "created_at": as_utc(context.cutoff_time).isoformat(),
                "quality_status": current_payload["data_quality"]["status"],
                "payload": current_payload,
            },
        }
        observations: dict[str, dict[str, object]] = {phase: current_observation}
        source_run_ids: list[str] = [context.run_id]
        source_snapshot_ids: list[str] = [context.snapshot_id] if context.snapshot_id else []

        required_observations = {
            "pre_market": [],
            "pre_close": ["pre_market"],
            "post_close_review": ["pre_market", "pre_close"],
            "current_state": [],
        }[phase]
        missing_observations: list[str] = []
        for earlier_phase in required_observations:
            match = self.repository.latest_snapshot_run(
                run_type=earlier_phase,
                cutoff_start=cutoff_start,
                cutoff_end=cutoff_end,
                before=context.cutoff_time,
            )
            if match is None:
                missing_observations.append(earlier_phase)
                continue
            run, snapshot = match
            observations[earlier_phase] = self._persisted_decision_observation(run, snapshot)
            source_run_ids.insert(0, run.id)
            source_snapshot_ids.insert(0, snapshot.id)

        trading_date = local_cutoff.date().isoformat()
        agent_repository = AIDecisionRepository(self.repository.session)
        previous_post_close = agent_repository.latest_session_before(
            trading_date, "post_close_review"
        )
        same_day_pre_market = agent_repository.session_for_trading_phase(
            trading_date, "pre_market", before=context.cutoff_time
        )
        prior_models = {
            "previous_post_close": previous_post_close,
            "same_day_pre_market": same_day_pre_market,
        }
        relevant_keys = {
            "pre_market": ("previous_post_close",),
            "pre_close": ("previous_post_close", "same_day_pre_market"),
            "post_close_review": (
                "previous_post_close",
                "same_day_pre_market",
            ),
            "current_state": ("previous_post_close", "same_day_pre_market"),
        }[phase]
        prior_reports = {
            key: (
                dict(prior_models[key].decision_report_json)
                if prior_models[key] is not None
                and isinstance(prior_models[key].decision_report_json, dict)
                else None
            )
            for key in relevant_keys
        }
        parent_model = (
            previous_post_close
            if phase == "pre_market"
            else same_day_pre_market
            if phase in {"pre_close", "post_close_review"}
            else None
        )
        events = [
            EventRepository.event_payload(event)
            for category in ("macro", "instrument")
            for event in self.event_repository.list_events(category)
        ]
        dataset_prefix = "manual-analysis" if phase == "current_state" else "daily-decision"
        dataset_key = f"{dataset_prefix}:{trading_date}:{phase}:{context.run_id}"
        profile = load_agent_profile(phase)
        context.decision_packet = build_stage_decision_packet(
            dataset_key=dataset_key,
            label=f"{trading_date} {phase} Daily Decision Dataset",
            captured_at=as_utc(context.cutoff_time),
            decision_phase=phase,
            trading_date=trading_date,
            observations=observations,
            prior_reports=prior_reports,
            events=events,
            agent_profile=profile,
        )
        context.decision_packet["prior_experiences"] = (
            agent_repository.active_experiences(limit=8)
            if phase in {"pre_market", "post_close_review"}
            else []
        )
        context.decision_packet["decision_context"].update(
            {
                "trigger_type": context.trigger_type,
                "analysis_mode": context.analysis_mode,
                "session_context": context.session_context,
                "report_scope": ["technical_report", "ai_state_analysis"]
                if phase == "current_state"
                else ["technical_report", "ai_decision", "ai_review"],
                "official_cycle": context.official_cycle,
                "eligible_for_scoring": context.eligible_for_scoring,
                "updates_official_cta_state": context.updates_official_cta_state,
                "universe_version_id": context.universe_version_id,
                "universe_content_sha256": context.universe_content_sha256,
                "universe_roles": {
                    symbol: item.get("roles", {}) for symbol, item in context.universe_items_by_symbol.items()
                },
            }
        )
        context.decision_dataset_key = dataset_key
        context.decision_source_run_ids = list(dict.fromkeys(source_run_ids))
        context.decision_source_snapshot_ids = list(dict.fromkeys(source_snapshot_ids))
        context.decision_phase = phase
        context.decision_trading_date = trading_date
        context.decision_parent_session_id = (
            parent_model.id if parent_model is not None and phase != "current_state" else None
        )
        context.decision_pair_status = "ready"
        context.decision_pair_reason = (
            "Missing earlier observations: " + ", ".join(missing_observations)
            if missing_observations
            else None
        )

    @staticmethod
    def _persisted_decision_observation(run, snapshot) -> dict[str, object]:
        return {
            "run": {
                "id": run.id,
                "run_type": run.run_type,
                "status": run.status,
                "cutoff_time": as_utc(run.cutoff_time).isoformat(),
                "started_at": as_utc(run.started_at).isoformat() if run.started_at else None,
                "completed_at": as_utc(run.completed_at).isoformat() if run.completed_at else None,
            },
            "snapshot": {
                "id": snapshot.id,
                "schema_version": snapshot.schema_version,
                "cutoff_time": as_utc(snapshot.cutoff_time).isoformat(),
                "created_at": as_utc(snapshot.created_at).isoformat(),
                "quality_status": snapshot.quality_status,
                "payload": snapshot.payload,
            },
        }

    @staticmethod
    def _current_decision_snapshot_payload(context: RunContext) -> dict[str, object]:
        def payload(code: str) -> dict[str, object]:
            result = context.results.get(code)
            return dict(result.payload) if result and isinstance(result.payload, dict) else {}

        market = payload("1a")
        instruments = payload("3a")
        options = payload("2")
        systematic_flows = build_systematic_flows(
            payload("1b"), payload("3b"), run_type=context.run_type
        )
        sections = (market, instruments, options)
        is_mock = any(bool(section.get("is_mock", True)) for section in sections)
        errors: list[str] = []
        warnings: list[str] = []
        for code, section in (("1a", market), ("2", options), ("3a", instruments)):
            for key in ("quality_errors", "errors", "blocking_errors"):
                values = section.get(key)
                if isinstance(values, list):
                    errors.extend(f"{code}: {value}" for value in values if value)
            for key in ("quality_warnings", "warnings"):
                values = section.get(key)
                if isinstance(values, list):
                    warnings.extend(f"{code}: {value}" for value in values if value)
        return {
            "schema_version": "1.0",
            "run_id": context.run_id,
            "snapshot_id": context.snapshot_id,
            "run_type": context.run_type,
            "trigger_type": context.trigger_type,
            "analysis_mode": context.analysis_mode,
            "session_context": context.session_context,
            "official_cycle": context.official_cycle,
            "eligible_for_scoring": context.eligible_for_scoring,
            "updates_official_cta_state": context.updates_official_cta_state,
            "universe": {
                "version_id": context.universe_version_id,
                "content_sha256": context.universe_content_sha256,
                "requested_symbols": context.symbols,
                "items": list(context.universe_items_by_symbol.values()),
            },
            "cutoff_time": as_utc(context.cutoff_time).isoformat(),
            "data_mode": market.get("data_mode") or options.get("source_mode") or "unknown",
            "is_mock": is_mock,
            "market": market,
            "instrument": instruments,
            "instrument_cards": [
                item for item in instruments.get("instruments", []) if isinstance(item, dict)
            ],
            "options": options,
            "systematic_flows": systematic_flows,
            "capital_flows": market.get("capital_flows") or {},
            "data_quality": {
                "status": "blocked" if errors else "warning" if warnings else "ok",
                "warnings": warnings,
                "errors": errors,
            },
        }

    def list_runs(self, limit: int = 50) -> list[RunListItem]:
        bounded_limit = max(1, min(limit, 100))
        return [self._to_list_item(run) for run in self.repository.list_runs(bounded_limit)]

    def get_run(self, run_id: str) -> RunDetailResponse:
        return self._to_detail(self._require_run(run_id))

    def get_snapshot(self, snapshot_id: str) -> SnapshotResponse:
        snapshot = self.repository.get_snapshot(snapshot_id)
        if snapshot is None:
            raise AppError("找不到指定 snapshot", code="snapshot_not_found", status_code=404)
        return SnapshotResponse.model_validate(snapshot, from_attributes=True)

    def get_frontend_read_model(self, snapshot_id: str) -> FrontendReadModel:
        snapshot = self.repository.get_snapshot(snapshot_id)
        if snapshot is None:
            raise AppError("找不到指定 snapshot", code="snapshot_not_found", status_code=404)
        try:
            return FrontendReadModel.model_validate(snapshot.payload)
        except Exception as exc:
            logger.exception("invalid snapshot read model snapshot_id=%s", snapshot_id)
            raise AppError(
                "snapshot read model 无法读取",
                code="invalid_snapshot",
                status_code=500,
            ) from exc

    def rerun_manual_ai(self, run_id: str) -> str:
        """Create a new current-state report version from an existing frozen snapshot."""

        if not self.settings.urus_agent_enabled or not self.settings.openrouter_api_key:
            raise AppError(
                "Urus Agent 未启用或未配置模型密钥",
                code="urus_agent_provider_not_configured",
                status_code=503,
            )
        run = self._require_run(run_id)
        if run.run_type != "manual_analysis" or not run.snapshot_id:
            raise AppError(
                "只有已冻结的手动分析可以重试 AI",
                code="manual_analysis_snapshot_required",
                status_code=409,
            )
        snapshot = self.repository.get_snapshot(run.snapshot_id)
        if snapshot is None:
            raise AppError("找不到手动分析 snapshot", code="snapshot_not_found", status_code=404)
        cutoff = as_utc(run.cutoff_time)
        trading_date = cutoff.astimezone(ZoneInfo(self.settings.market_timezone)).date().isoformat()
        agent_repository = AIDecisionRepository(self.repository.session)
        previous_post_close = agent_repository.latest_session_before(
            trading_date, "post_close_review"
        )
        same_day_pre_market = agent_repository.session_for_trading_phase(
            trading_date, "pre_market", before=cutoff
        )
        prior_reports = {
            "previous_post_close": (
                dict(previous_post_close.decision_report_json)
                if previous_post_close is not None
                and isinstance(previous_post_close.decision_report_json, dict)
                else None
            ),
            "same_day_pre_market": (
                dict(same_day_pre_market.decision_report_json)
                if same_day_pre_market is not None
                and isinstance(same_day_pre_market.decision_report_json, dict)
                else None
            ),
        }
        profile = load_agent_profile("current_state")
        dataset_key = f"manual-analysis:{trading_date}:current_state:{run.id}:retry:{uuid4()}"
        packet = build_stage_decision_packet(
            dataset_key=dataset_key,
            label=f"{trading_date} manual current-state AI retry · {run.id}",
            captured_at=cutoff,
            decision_phase="current_state",
            trading_date=trading_date,
            observations={
                "current_state": self._persisted_decision_observation(run, snapshot)
            },
            prior_reports=prior_reports,
            events=[
                EventRepository.event_payload(event)
                for category in ("macro", "instrument")
                for event in self.event_repository.list_events(category)
            ],
            agent_profile=profile,
        )
        analysis_metadata = {
            "trigger_type": "manual",
            "analysis_mode": "current_state",
            "session_context": self._session_context(cutoff),
            "report_scope": ["technical_report", "ai_state_analysis"],
            "official_cycle": False,
            "eligible_for_scoring": False,
            "updates_official_cta_state": False,
        }
        packet["decision_context"].update(analysis_metadata)
        payload = snapshot.payload if isinstance(snapshot.payload, dict) else {}
        symbols = [
            str(item.get("symbol") or "").upper()
            for item in payload.get("instrument_cards") or []
            if isinstance(item, dict) and item.get("symbol")
        ]
        result = DecisionCoordinator(self.repository.session, self.settings).execute(
            CoordinatorRequest(
                workflow_run_id=run.id,
                cutoff_time=cutoff,
                evidence={},
                symbols=list(dict.fromkeys(symbols)),
                dataset_key=dataset_key,
                source_snapshot_ids=[snapshot.id],
                source_run_ids=[run.id],
                decision_packet=packet,
                decision_phase="current_state",
                trading_date=trading_date,
                parent_session_id=None,
                analysis_metadata=analysis_metadata,
            )
        )
        self._persist_report_display_projection_for_report(
            result.session_id,
            source_snapshot_ids=[snapshot.id],
            source_run_ids=[run.id],
            captured_at=cutoff,
        )
        if result.decision_report.get("status") in {"succeeded", "partial"}:
            completed_at = utc_now()
            ai_step = next((step for step in run.steps if step.step_code == "4"), None)
            if ai_step is not None:
                self.repository.update_step(
                    ai_step,
                    status="succeeded",
                    completed_at=completed_at,
                    summary="AI 现状分析重试成功，最新报告已保存。",
                    error_message=None,
                )
            remaining_failed = any(
                step.step_code != "4" and step.status in {"failed", "timed_out"}
                for step in run.steps
            )
            if not remaining_failed:
                self.repository.update_run(
                    run,
                    status="succeeded",
                    completed_at=completed_at,
                    error_message=None,
                )
        return result.session_id

    def _persist_report_display_projection(self, context: RunContext) -> None:
        step = context.results.get("4")
        payload = step.payload if step is not None else {}
        report_id = payload.get("decision_session_id") if isinstance(payload, dict) else None
        if not report_id:
            return
        self._persist_report_display_projection_for_report(
            str(report_id),
            source_snapshot_ids=context.decision_source_snapshot_ids,
            source_run_ids=context.decision_source_run_ids,
            captured_at=context.cutoff_time,
        )

    def _persist_report_display_projection_for_report(
        self,
        report_id: str,
        *,
        source_snapshot_ids: list[str],
        source_run_ids: list[str],
        captured_at: datetime,
    ) -> None:
        try:
            payload = build_report_display_projection(
                self.repository.session,
                report_id=report_id,
                source_snapshot_ids=source_snapshot_ids,
                source_run_ids=source_run_ids,
                captured_at=captured_at,
            )
            ReportDisplayRepository(self.repository.session).save(
                report_id=report_id,
                payload=payload,
                source_snapshot_ids=source_snapshot_ids,
                source_run_ids=source_run_ids,
                content_sha256=projection_content_sha256(payload),
                schema_version=str(payload.get("schema_version") or "unknown"),
            )
        except Exception:
            # A chart projection is additive.  Never turn a valid technical or
            # AI report into a failed workflow because a display read model
            # could not be written; the manifest will expose the real gap.
            # A failed commit/query leaves SQLAlchemy's Session unusable until
            # rollback, while the report and frozen snapshot are already safe.
            self.repository.session.rollback()
            logger.exception("report display projection failed report_id=%s", report_id)

    def _build_pipeline(self) -> WorkflowPipeline:
        if self.settings.workflow_research_variant.lower() == "cta":
            step_1b = MarketCTAProxyStep()
            step_3b = InstrumentCTAProxyStep()
        else:
            step_1b = MarketEventSummaryStep()
            step_3b = InstrumentEventSummaryStep()
        return WorkflowPipeline(
            [
                MarketCollectorStep(),
                step_1b,
                OptionsCollectorStep(),
                InstrumentCollectorStep(),
                step_3b,
                DecisionStep(),
                OutputStep(),
            ]
        )

    def _build_market_adapter(self, market_symbols: list[str] | None = None):
        if not self.settings.moomoo_enabled:
            return DisabledMoomooAdapter()
        return OpenDMarketAdapter(
            host=self.settings.moomoo_host,
            port=self.settings.moomoo_port,
            market_timezone=self.settings.market_timezone,
            history_days=self.settings.moomoo_history_days,
            sdk_home=Path(self.settings.moomoo_sdk_home),
            market_symbols=market_symbols or self.settings.moomoo_market_symbols.split(","),
        )

    def _build_macro_adapter(self):
        fred_adapter = (
            FredDailyAdapter(
                base_url=self.settings.fred_base_url,
                timeout_seconds=self.settings.fred_timeout_seconds,
                lookback_days=self.settings.fred_lookback_days,
                market_timezone=self.settings.market_timezone,
            )
            if self.settings.fred_enabled
            else None
        )
        yahoo_adapter = (
            YahooDailyAdapter(
                base_url=self.settings.yahoo_base_url,
                timeout_seconds=self.settings.yahoo_timeout_seconds,
                lookback_days=self.settings.yahoo_lookback_days,
                market_timezone=self.settings.market_timezone,
            )
            if self.settings.yahoo_enabled
            else None
        )
        if fred_adapter and yahoo_adapter:
            return FallbackDailyMacroAdapter(fred_adapter, yahoo_adapter)
        return fred_adapter or yahoo_adapter

    def _build_options_adapter(self, target_symbols: list[str] | None = None) -> OptionsCollectorAdapter:
        if not self.settings.moomoo_enabled:
            return DisabledOptionsAdapter()
        target_symbols = target_symbols if target_symbols is not None else self.settings.options_collection_symbol_list
        return MoomooOptionsAdapter(
            host=self.settings.moomoo_host,
            port=self.settings.moomoo_port,
            symbols=target_symbols,
            target_dtes=self.settings.options_target_dte_list,
            max_dte=self.settings.options_max_dte,
            strike_range_percent=self.settings.options_strike_range_percent,
            batch_size=self.settings.options_snapshot_batch_size,
            snapshot_interval_seconds=self.settings.options_snapshot_interval_seconds,
            option_chain_interval_seconds=self.settings.options_chain_interval_seconds,
            gamma_profile_range_percent=self.settings.options_gamma_profile_range_percent,
            gamma_profile_points=self.settings.options_gamma_profile_points,
            risk_free_rate_percent=self.settings.options_risk_free_rate_percent,
            dividend_yield_percent=self.settings.options_dividend_yield_percent,
        )

    def _build_anomalo_adapter(self, *, simulate: bool):
        if simulate:
            return MockAnomaloAdapter()
        if self.settings.anomalo_enabled and self.settings.anomalo_base_url:
            return HttpAnomaloAdapter(
                base_url=self.settings.anomalo_base_url,
                timeout_seconds=self.settings.anomalo_timeout_seconds,
            )
        return DisabledAnomaloAdapter()

    def _build_decision_adapter(self, *, enabled: bool | None = None):
        decision_enabled = self.settings.urus_agent_enabled if enabled is None else enabled
        if decision_enabled:
            if not self.settings.openrouter_api_key:
                raise AppError(
                    "Urus Agent 已启用但未配置 OPENROUTER_API_KEY",
                    code="urus_agent_provider_not_configured",
                    status_code=503,
                )
            return UrusDecisionAdapter(self.repository.session, self.settings)
        return MockDecisionAdapter()

    def _validate_request(
        self, request: RunCreateRequest, universe_items: list[InstrumentConfig]
    ) -> tuple[list[str], bool]:
        symbols = self._validate_symbols(request.symbols, universe_items)
        decision_enabled = (
            self.settings.urus_agent_enabled
            and not request.skip_ai_decision
            and request.run_type.value != "pre_close"
        )
        if decision_enabled and not self.settings.openrouter_api_key:
            raise AppError(
                "Urus Agent 已启用但未配置 OPENROUTER_API_KEY",
                code="urus_agent_provider_not_configured",
                status_code=503,
            )
        if request.run_type.value == "manual_analysis" and not decision_enabled:
            raise AppError(
                "手动即时分析需要 AI 现状分析；请在运行设置中启用 AI 并配置 OpenRouter 凭据。",
                code="manual_analysis_ai_unavailable",
                status_code=503,
            )
        return symbols, decision_enabled

    def _session_context(self, cutoff_time: datetime) -> str:
        local = as_utc(cutoff_time).astimezone(ZoneInfo(self.settings.market_timezone))
        if local.weekday() >= 5:
            return "market_closed"
        clock = local.timetz().replace(tzinfo=None)
        if clock < time(9, 30):
            return "pre_market"
        if clock < time(16, 0):
            return "intraday"
        return "after_hours"

    def _validate_symbols(
        self, requested: list[str] | None, universe_items: list[InstrumentConfig]
    ) -> list[str]:
        enabled = [item for item in universe_items if item.enabled]
        allowed = {item.symbol for item in enabled if item.roles.ai_candidate}
        symbols = [item.upper() for item in (requested or [item.symbol for item in enabled if item.roles.ai_candidate])]
        unsupported = sorted(set(symbols) - allowed)
        if unsupported:
            raise AppError(
                f"标的不在当前 AI 候选范围：{', '.join(unsupported)}；请先在标的设置中启用 AI 候选角色。",
                code="unsupported_symbol",
                status_code=422,
            )
        return symbols

    @staticmethod
    def _final_run_status(results: dict[str, StepResult]) -> RunStatus:
        failed_codes = {code for code, result in results.items() if result.status == StepStatus.FAILED}
        if failed_codes:
            if failed_codes.issubset({"1b", "3b"}):
                return RunStatus.PARTIAL
            return RunStatus.FAILED
        if any(
            code != "5" and data_state_for(result) != "live"
            for code, result in results.items()
        ):
            return RunStatus.MIXED
        return RunStatus.SUCCEEDED

    @staticmethod
    def _quality_status(payload: dict[str, object]) -> str:
        quality = payload.get("data_quality")
        if isinstance(quality, dict):
            status = quality.get("status")
            if isinstance(status, str):
                return status
        return "mock"

    @staticmethod
    def _to_list_item(run: RunModel) -> RunListItem:
        return RunListItem.model_validate(run, from_attributes=True)

    @staticmethod
    def _to_detail(run: RunModel) -> RunDetailResponse:
        item = RunService._to_list_item(run)
        steps = []
        for step in run.steps:
            response = StepRunResponse.model_validate(step, from_attributes=True)
            step_result = StepResult(
                status=StepStatus(step.status),
                summary=step.summary or "",
                payload=step.payload or {},
                error_message=step.error_message,
            )
            steps.append(response.model_copy(update={"data_state": data_state_for(step_result)}))
        return RunDetailResponse(**item.model_dump(), steps=steps)

    def _require_run(self, run_id: str) -> RunModel:
        run = self.repository.get_run(run_id)
        if run is None:
            raise AppError("找不到指定运行", code="run_not_found", status_code=404)
        return run
