from __future__ import annotations

from app.integrations.anomalo import AnomaloRequest
from app.models import StepStatus
from app.workflows.base import StepResult
from app.workflows.context import RunContext


class MarketEventSummaryStep:
    code = "1b"
    label = "1B · 宏观事件摘要"

    def execute(self, context: RunContext) -> StepResult:
        if not context.simulate_macro_event:
            return StepResult(
                status=StepStatus.SKIPPED,
                summary="跳过：框架 mock 输入未标记重要宏观事件。",
                payload={
                    "is_mock": True,
                    "category": "macro",
                    "status": StepStatus.SKIPPED.value,
                    "reason": "未模拟重要宏观事件；框架阶段不查询外部日历。",
                },
            )
        if context.should_fail(self.code):
            return StepResult(
                status=StepStatus.FAILED,
                summary="模拟失败：宏观事件摘要未完成。",
                payload={"is_mock": True, "category": "macro", "status": StepStatus.FAILED.value},
                error_message="requested mock failure at step 1b",
            )
        try:
            if context.anomalo_adapter is None:
                raise RuntimeError("Anomalo adapter is not configured")
            session_id = f"urus-{context.run_id}-step-1b"
            response = context.anomalo_adapter.summarize(
                AnomaloRequest(
                    session_id=session_id,
                    message="请为框架 mock 的重要宏观事件生成一两句话摘要。",
                )
            )
            if response.disabled or not response.final_text:
                return StepResult(
                    status=StepStatus.SKIPPED,
                    summary="跳过：Anomalo 当前处于 disabled/mock 预留状态。",
                    payload={
                        "is_mock": True,
                        "category": "macro",
                        "status": StepStatus.SKIPPED.value,
                        "reason": "Anomalo 未启用真实调用。",
                    },
                )
            return StepResult(
                status=StepStatus.SUCCEEDED,
                summary="已生成宏观事件 mock 摘要；未访问 Anomalo 网络接口。",
                payload={
                    "is_mock": True,
                    "category": "macro",
                    "status": StepStatus.SUCCEEDED.value,
                    "title": "模拟重要宏观事件",
                    "summary": response.final_text,
                },
            )
        except Exception as exc:
            return StepResult(
                status=StepStatus.FAILED,
                summary="宏观事件摘要生成失败。",
                payload={"is_mock": True, "category": "macro", "status": StepStatus.FAILED.value},
                error_message=str(exc),
            )


class InstrumentEventSummaryStep:
    code = "3b"
    label = "3B · 个股事件摘要"

    def execute(self, context: RunContext) -> StepResult:
        if not context.simulate_instrument_event:
            return StepResult(
                status=StepStatus.SKIPPED,
                summary="跳过：框架 mock 输入未标记新财报或公司事件。",
                payload={
                    "is_mock": True,
                    "category": "instrument",
                    "status": StepStatus.SKIPPED.value,
                    "reason": "未模拟新财报/指引/重大公司事件；框架阶段不抓取外部来源。",
                },
            )
        if context.should_fail(self.code):
            return StepResult(
                status=StepStatus.FAILED,
                summary="模拟失败：个股事件摘要未完成。",
                payload={
                    "is_mock": True,
                    "category": "instrument",
                    "status": StepStatus.FAILED.value,
                },
                error_message="requested mock failure at step 3b",
            )
        try:
            if context.anomalo_adapter is None:
                raise RuntimeError("Anomalo adapter is not configured")
            session_id = f"urus-{context.run_id}-step-3b"
            response = context.anomalo_adapter.summarize(
                AnomaloRequest(
                    session_id=session_id,
                    message="请为框架 mock 的个股事件生成一两句话摘要。",
                )
            )
            if response.disabled or not response.final_text:
                return StepResult(
                    status=StepStatus.SKIPPED,
                    summary="跳过：Anomalo 当前处于 disabled/mock 预留状态。",
                    payload={
                        "is_mock": True,
                        "category": "instrument",
                        "status": StepStatus.SKIPPED.value,
                        "reason": "Anomalo 未启用真实调用。",
                    },
                )
            return StepResult(
                status=StepStatus.SUCCEEDED,
                summary="已生成个股事件 mock 摘要；未访问 Anomalo 网络接口。",
                payload={
                    "is_mock": True,
                    "category": "instrument",
                    "status": StepStatus.SUCCEEDED.value,
                    "title": "模拟 INTC 公司事件",
                    "summary": response.final_text,
                },
            )
        except Exception as exc:
            return StepResult(
                status=StepStatus.FAILED,
                summary="个股事件摘要生成失败。",
                payload={
                    "is_mock": True,
                    "category": "instrument",
                    "status": StepStatus.FAILED.value,
                },
                error_message=str(exc),
            )

