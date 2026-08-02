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
            return StepResult(
                status=StepStatus.SUCCEEDED,
                summary="期权模块保留 mock 占位结构，真实 IV/GEX 尚未实现。",
                payload=context.moomoo_adapter.options_placeholder("QQQ"),
            )
        except Exception as exc:
            return StepResult(
                status=StepStatus.FAILED,
                summary="期权占位状态生成失败。",
                error_message=str(exc),
            )
