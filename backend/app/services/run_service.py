from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.core.time import utc_now
from app.integrations.anomalo import MockAnomaloAdapter
from app.integrations.decision import MockDecisionAdapter
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
from app.repositories import RunRepository
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
from app.workflows import (
    DEFAULT_STEP_CODES,
    DecisionStep,
    InstrumentCollectorStep,
    InstrumentEventSummaryStep,
    MarketCollectorStep,
    MarketEventSummaryStep,
    OptionsCollectorStep,
    OutputStep,
    RunContext,
    StepResult,
    WorkflowPipeline,
)
from app.workflows.base import data_state_for

logger = logging.getLogger(__name__)


class RunService:
    """Application service that coordinates workflow execution and persistence."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.repository = RunRepository(session)
        self.settings = settings

    def create_run(self, request: RunCreateRequest) -> RunCreateResponse:
        symbols = self._validate_symbols(request.symbols)
        run_id = str(uuid4())
        cutoff_time = utc_now()
        run = self.repository.create_run(
            run_id=run_id,
            run_type=request.run_type.value,
            cutoff_time=cutoff_time,
        )
        step_models = self.repository.create_steps(
            run_id,
            [
                (str(uuid4()), position, code, StepStatus.PENDING.value)
                for position, code in enumerate(DEFAULT_STEP_CODES, start=1)
            ],
        )
        step_by_code = {step.step_code: step for step in step_models}
        started_at = utc_now()
        self.repository.update_run(run, status=RunStatus.RUNNING.value, started_at=started_at)

        market_adapter = self._build_market_adapter()
        options_adapter = self._build_options_adapter()
        macro_adapter = self._build_macro_adapter()
        context = RunContext(
            run_id=run_id,
            run_type=request.run_type.value,
            cutoff_time=cutoff_time,
            symbols=symbols,
            simulate_macro_event=request.simulate_macro_event,
            simulate_instrument_event=request.simulate_instrument_event,
            fail_step=request.fail_step.value if request.fail_step else None,
            market_adapter=market_adapter,
            macro_adapter=macro_adapter,
            moomoo_adapter=DisabledMoomooAdapter(),
            options_adapter=options_adapter,
            anomalo_adapter=MockAnomaloAdapter(),
            decision_adapter=MockDecisionAdapter(),
        )
        pipeline = self._build_pipeline()
        snapshot_payload: dict[str, object] | None = None
        snapshot_id: str | None = None

        for step in pipeline.steps:
            model = step_by_code[step.code]
            step_started = utc_now()
            self.repository.update_step(
                model,
                status=StepStatus.RUNNING.value,
                started_at=step_started,
            )
            if step.code == "5":
                # A snapshot is allocated only when the output stage begins.
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

        for adapter in (market_adapter, macro_adapter, options_adapter):
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
            self.repository.save_snapshot(
                snapshot_id=snapshot_id,
                run_id=run_id,
                schema_version=str(snapshot_payload.get("schema_version", "1.0")),
                cutoff_time=cutoff_time,
                created_at=utc_now(),
                quality_status=quality_status,
                payload=snapshot_payload,
            )

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

    def _build_pipeline(self) -> WorkflowPipeline:
        return WorkflowPipeline(
            [
                MarketCollectorStep(),
                MarketEventSummaryStep(),
                OptionsCollectorStep(),
                InstrumentCollectorStep(),
                InstrumentEventSummaryStep(),
                DecisionStep(),
                OutputStep(),
            ]
        )

    def _build_market_adapter(self):
        if not self.settings.moomoo_enabled:
            return DisabledMoomooAdapter()
        return OpenDMarketAdapter(
            host=self.settings.moomoo_host,
            port=self.settings.moomoo_port,
            market_timezone=self.settings.market_timezone,
            history_days=self.settings.moomoo_history_days,
            sdk_home=Path(self.settings.moomoo_sdk_home),
            market_symbols=self.settings.moomoo_market_symbols.split(","),
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

    def _build_options_adapter(self) -> OptionsCollectorAdapter:
        if not self.settings.moomoo_enabled:
            return DisabledOptionsAdapter()
        target_symbols = self.settings.options_collection_symbol_list
        return MoomooOptionsAdapter(
            host=self.settings.moomoo_host,
            port=self.settings.moomoo_port,
            symbols=target_symbols,
            target_dtes=self.settings.options_target_dte_list,
            max_dte=self.settings.options_max_dte,
            strike_range_percent=self.settings.options_strike_range_percent,
            batch_size=self.settings.options_snapshot_batch_size,
        )

    def _validate_symbols(self, requested: list[str] | None) -> list[str]:
        allowed = set(self.settings.enabled_symbol_list)
        if not {"QQQ", "INTC"}.issubset(allowed):
            raise AppError(
                "当前框架白名单必须包含 QQQ 和 INTC",
                code="invalid_development_allowlist",
                status_code=500,
            )
        symbols = [item.upper() for item in (requested or sorted(allowed))]
        unsupported = sorted(set(symbols) - allowed)
        if unsupported:
            raise AppError(
                f"标的不在开发白名单中：{', '.join(unsupported)}；当前只允许 QQQ、INTC",
                code="unsupported_symbol",
                status_code=422,
            )
        if not {"QQQ", "INTC"}.issubset(set(symbols)):
            raise AppError(
                "框架运行必须同时包含 QQQ 和 INTC",
                code="incomplete_development_allowlist",
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
