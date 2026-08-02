from __future__ import annotations

from app.models import StepStatus
from app.workflows.base import StepResult
from app.workflows.context import RunContext


class InstrumentCollectorStep:
    code = "3a"
    label = "3A · 个股采集"

    def execute(self, context: RunContext) -> StepResult:
        if context.should_fail(self.code):
            return StepResult(
                status=StepStatus.FAILED,
                summary="模拟失败：个股采集步骤未完成。",
                error_message="requested mock failure at step 3a",
            )
        try:
            if context.moomoo_adapter is None:
                raise RuntimeError("instrument adapter is not configured")
            return StepResult(
                status=StepStatus.SUCCEEDED,
                summary="已生成 INTC mock 个股卡；未请求真实行情。",
                payload=context.moomoo_adapter.instrument_card("INTC"),
            )
        except Exception as exc:
            return StepResult(
                status=StepStatus.FAILED,
                summary="个股 mock 卡生成失败。",
                error_message=str(exc),
            )
