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
            payload = context.moomoo_adapter.instrument_card("INTC")
            payload["is_mock"] = True
            payload["data_state"] = "unavailable"
            return StepResult(
                status=StepStatus.UNAVAILABLE,
                summary="3A 个股行情尚未接入；当前 mock 结构不能作为 INTC 行情证据。",
                payload=payload,
                data_state="unavailable",
            )
        except Exception as exc:
            return StepResult(
                status=StepStatus.FAILED,
                summary="个股 mock 卡生成失败。",
                error_message=str(exc),
                data_state="unavailable",
            )
