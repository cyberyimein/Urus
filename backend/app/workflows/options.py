from __future__ import annotations

from app.models import StepStatus
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
        try:
            if context.options_adapter is None:
                raise RuntimeError("options adapter is not configured")
            payload = context.options_adapter.options_snapshot()
            is_mock = bool(payload.get("is_mock", True))
            return StepResult(
                status=StepStatus.SUCCEEDED,
                summary=(
                    "已生成 Moomoo 快照式 DEX/GEX、Gamma Wall 与 Max Pain。"
                    if not is_mock
                    else "期权数据源未启用，保留明确的 mock 状态。"
                ),
                payload=payload,
            )
        except Exception as exc:
            return StepResult(
                status=StepStatus.FAILED,
                summary="期权结构采集或计算失败。",
                error_message=str(exc),
            )
