from __future__ import annotations

import logging
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.core.time import utc_now
from app.integrations.anomalo import MockAnomaloAdapter
from app.integrations.decision import MockDecisionAdapter
from app.integrations.moomoo import DisabledMoomooAdapter
from app.models import RunModel, RunStatus, StepStatus
from app.repositories import RunRepository
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

logger = logging.getLogger(__name__)


class RunService:
    """Application service that coordinates the offline framework workflow."""

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
        self.repository.update_run(run, status=RunStatus.RUNNING.value, started_at=utc_now())

        market_adapter = self._build_market_adapter()
        context = RunContext(
            run_id=run_id,
            run_type=request.run_type.value,
            cutoff_time=cutoff_time,
            symbols=symbols,
            simulate_macro_event=request.simulate_macro_event,
            simulate_instrument_event=request.simulate_instrument_event,
            fail_step=request.fail_step.value if request.fail_step else None,
            market_adapter=market_adapter,
            moomoo_adapter=market_adapter,
            anomalo_adapter=MockAnomaloAdapter(),
            decision_adapter=MockDecisionAdapter(),
        )

        snapshot_payload: dict[str, object] | None = None
        snapshot_id: str | None = None
        for step in self._build_pipeline().steps:
            model = step_by_code[step.code]
            self.repository.update_step(model, status=StepStatus.RUNNING.value, started_at=utc_now())
            if step.code == "5":
                context.snapshot_id = str(uuid4())
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
            self.repository.update_step(
                model,
                status=result.status.value,
                completed_at=utc_now(),
                summary=result.summary,
                error_message=result.error_message,
                payload=result.payload or None,
            )

        final_status = self._final_run_status(context.results)
        output_result = context.results.get("5")
        if output_result and output_result.status == StepStatus.SUCCEEDED:
            snapshot_payload = dict(output_result.payload)
        elif context.snapshot_id is not None:
            original_fail_step = context.fail_step
            context.fail_step = None
            fallback = OutputStep().execute(context)
            context.fail_step = original_fail_step
            snapshot_payload = dict(fallback.payload)
            quality = snapshot_payload.get("data_quality")
            if isinstance(quality, dict):
                errors = quality.setdefault("errors", [])
                if isinstance(errors, list) and output_result and output_result.error_message:
                    errors.append(f"5: {output_result.error_message}")
                quality["status"] = "error"

        if snapshot_payload is not None and context.snapshot_id is not None:
            snapshot_id = context.snapshot_id
            snapshot_payload["run_status"] = final_status.value
            snapshot_payload["snapshot_id"] = snapshot_id
            if isinstance(snapshot_payload.get("steps"), list) and output_result and output_result.status != StepStatus.SUCCEEDED:
                for item in snapshot_payload["steps"]:
                    if isinstance(item, dict) and item.get("code") == "5":
                        item["status"] = output_result.status.value
                        item["summary"] = output_result.summary
                        item["error_message"] = output_result.error_message
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
            self.repository.update_step(step_by_code["5"], payload=snapshot_payload)

        close = getattr(market_adapter, "close", None)
        if close:
            close()
        errors = [
            f"{code}: {result.error_message}"
            for code, result in context.results.items()
            if result.status == StepStatus.FAILED and result.error_message
        ]
        self.repository.update_run(
            run,
            status=final_status.value,
            completed_at=utc_now(),
            snapshot_id=snapshot_id,
            error_message="; ".join(errors) if errors else None,
        )
        return RunCreateResponse(run_id=run_id, status=final_status.value, snapshot_id=snapshot_id)

    def list_runs(self, limit: int = 50) -> list[RunListItem]:
        bounded_limit = max(1, min(limit, 100))
        return [RunListItem.model_validate(run, from_attributes=True) for run in self.repository.list_runs(bounded_limit)]

    def get_run(self, run_id: str) -> RunDetailResponse:
        run = self.repository.get_run(run_id)
        if run is None:
            raise AppError("找不到指定运行", code="run_not_found", status_code=404)
        item = RunListItem.model_validate(run, from_attributes=True)
        steps = [StepRunResponse.model_validate(step, from_attributes=True) for step in run.steps]
        return RunDetailResponse(**item.model_dump(), steps=steps)

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
            raise AppError("snapshot read model 无法读取", code="invalid_snapshot", status_code=500) from exc

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

    def _build_market_adapter(self) -> DisabledMoomooAdapter:
        return DisabledMoomooAdapter()

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
            return RunStatus.PARTIAL if failed_codes.issubset({"1b", "3b"}) else RunStatus.FAILED
        return RunStatus.SUCCEEDED

    @staticmethod
    def _quality_status(payload: dict[str, object]) -> str:
        quality = payload.get("data_quality")
        if isinstance(quality, dict) and isinstance(quality.get("status"), str):
            return str(quality["status"])
        return "mock"
