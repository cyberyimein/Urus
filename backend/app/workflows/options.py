from __future__ import annotations

from app.models import StepStatus
from app.services.options_collection import OptionsCollectionService
from app.workflows.base import StepResult
from app.workflows.context import RunContext


class OptionsCollectorStep:
    code = "2"
    label = "2 · 期权结构"

    def execute(self, context: RunContext) -> StepResult:
        if context.should_fail(self.code):
            return StepResult(
                status=StepStatus.FAILED,
                summary="模拟失败：期权结构步骤未完成。",
                error_message="requested mock failure at step 2",
            )
        result = OptionsCollectionService().collect(
            context.options_adapter,
            context.option_symbols,
        )
        payload = dict(result.payload)
        if result.persistence_payload is not None:
            # RunService still owns the legacy normalized option tables for
            # Workflow snapshots. Observation Runs intentionally persist only
            # the public immutable payload through their own boundary.
            payload["_persistence"] = result.persistence_payload
        return StepResult(
            status=result.status,
            summary=result.summary,
            payload=payload,
            error_message=result.error_message,
            data_state=result.data_state,
        )
