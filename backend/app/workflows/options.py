from __future__ import annotations

from app.models import StepStatus
from app.workflows.base import StepResult
from app.workflows.context import RunContext


class OptionsCollectorStep:
    code = "2"
    label = "2 · 期权占位"

    def execute(self, context: RunContext) -> StepResult:
        if context.should_fail(self.code):
            return StepResult(
                status=StepStatus.FAILED,
                summary="模拟失败：期权占位步骤未完成。",
                error_message="requested mock failure at step 2",
            )
        try:
            if context.moomoo_adapter is None:
                raise RuntimeError("options adapter is not configured")
            payload = context.moomoo_adapter.options_placeholder("QQQ")
            payload["is_mock"] = True
            payload["status"] = StepStatus.PLACEHOLDER.value
            payload["data_state"] = "placeholder"
            return StepResult(
                status=StepStatus.PLACEHOLDER,
                summary="期权数据未接入；当前仅保留占位结构，不能作为 IV/GEX 证据。",
                payload=payload,
                data_state="placeholder",
            )
        except Exception as exc:
            return StepResult(
                status=StepStatus.FAILED,
                summary="期权占位状态生成失败。",
                error_message=str(exc),
                data_state="unavailable",
            )
