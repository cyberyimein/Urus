from __future__ import annotations

from app.models import StepStatus
from app.workflows.base import StepResult
from app.workflows.context import RunContext


class MarketCollectorStep:
    code = "1a"
    label = "1A · 大盘采集"

    def execute(self, context: RunContext) -> StepResult:
        if context.should_fail(self.code):
            return StepResult(
                status=StepStatus.FAILED,
                summary="模拟失败：大盘采集步骤未完成。",
                error_message="requested mock failure at step 1a",
            )
        try:
            adapter = context.market_adapter or context.moomoo_adapter
            if adapter is None:
                raise RuntimeError("market adapter is not configured")
            payload = adapter.market_card("QQQ")
            payload.setdefault("is_mock", True)
            return StepResult(
                status=StepStatus.SUCCEEDED,
                summary="已生成 QQQ mock 大盘卡；未请求真实行情。",
                payload=payload,
            )
        except Exception as exc:
            return StepResult(
                status=StepStatus.FAILED,
                summary="QQQ 大盘 mock 采集失败。",
                error_message=str(exc),
            )
