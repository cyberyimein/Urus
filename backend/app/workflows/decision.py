from __future__ import annotations

from app.integrations.decision import DecisionRequest
from app.models import StepStatus
from app.workflows.base import StepResult
from app.workflows.context import RunContext


class DecisionStep:
    code = "4"
    label = "4 · 决策占位"

    def execute(self, context: RunContext) -> StepResult:
        if context.should_fail(self.code):
            return StepResult(
                status=StepStatus.FAILED,
                summary="模拟失败：决策占位步骤未完成。",
                error_message="requested mock failure at step 4",
            )
        try:
            if context.decision_adapter is None:
                raise RuntimeError("decision adapter is not configured")
            evidence = {code: result.payload for code, result in context.results.items()}
            response = context.decision_adapter.decide(
                DecisionRequest(
                    session_id=f"urus-{context.run_id}-step-4",
                    evidence=evidence,
                )
            )
            return StepResult(
                status=StepStatus.PLACEHOLDER,
                summary="决策 AI 尚未接入；当前仅保留占位结果，不能作为决策证据。",
                payload={
                    "is_mock": True,
                    "status": StepStatus.PLACEHOLDER.value,
                    "data_state": "placeholder",
                    "stance": response.stance,
                    "confidence": response.confidence,
                    "summary": response.summary,
                    "note": "框架阶段不设计或执行真实决策 prompt。",
                },
                data_state="placeholder",
            )
        except Exception as exc:
            return StepResult(
                status=StepStatus.FAILED,
                summary="决策占位生成失败。",
                error_message=str(exc),
                data_state="unavailable",
            )
