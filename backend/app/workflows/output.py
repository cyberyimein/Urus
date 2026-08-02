from __future__ import annotations

from app.core.time import utc_now
from app.models import StepStatus
from app.schemas.enums import RunStatusValue
from app.workflows.base import StepResult
from app.workflows.context import RunContext


STEP_LABELS = {
    "1a": "1A · 大盘采集",
    "1b": "1B · 宏观事件摘要",
    "2": "2 · 期权结构",
    "3a": "3A · 个股采集",
    "3b": "3B · 个股事件摘要",
    "4": "4 · 决策占位",
    "5": "5 · 输出 read model",
}


def _event_payload(result: StepResult | None, category: str) -> dict[str, object]:
    if result is None:
        return {
            "is_mock": True,
            "category": category,
            "status": StepStatus.FAILED.value,
            "reason": "步骤结果不可用。",
        }
    payload = dict(result.payload)
    payload.setdefault("is_mock", True)
    payload.setdefault("category", category)
    payload.setdefault("status", result.status.value)
    if result.error_message:
        payload["reason"] = result.error_message
    return payload


def _data_payload(result: StepResult | None) -> dict[str, object] | None:
    if result is None or result.status in {StepStatus.FAILED, StepStatus.SKIPPED}:
        return None
    return dict(result.payload)


class OutputStep:
    code = "5"
    label = "5 · 输出 read model"

    def execute(self, context: RunContext) -> StepResult:
        if context.should_fail(self.code):
            return StepResult(
                status=StepStatus.FAILED,
                summary="模拟失败：输出 read model 未完成。",
                error_message="requested mock failure at step 5",
            )
        if context.snapshot_id is None:
            return StepResult(
                status=StepStatus.FAILED,
                summary="输出 read model 未关联 snapshot_id。",
                error_message="snapshot_id was not allocated before output step",
            )

        steps: list[dict[str, object]] = []
        warnings: list[str] = []
        errors: list[str] = []
        for code, result in context.results.items():
            steps.append(
                {
                    "code": code,
                    "label": STEP_LABELS.get(code, code),
                    "status": result.status.value,
                    "summary": result.summary,
                    "error_message": result.error_message,
                }
            )
            if result.status == StepStatus.SKIPPED:
                warnings.append(result.summary)
            if result.status == StepStatus.FAILED and result.error_message:
                errors.append(f"{code}: {result.error_message}")

        market = _data_payload(context.results.get("1a"))
        instrument = _data_payload(context.results.get("3a"))
        options = _data_payload(context.results.get("2")) or {
            "is_mock": True,
            "status": "unavailable",
            "available": False,
            "note": "期权结构结果不可用。",
        }
        options_is_mock = bool(options.get("is_mock", True))
        decision = _data_payload(context.results.get("4")) or {
            "is_mock": True,
            "status": "unavailable",
            "stance": None,
            "confidence": None,
            "summary": "决策占位结果不可用。",
            "note": "框架阶段不执行真实决策 AI。",
        }
        read_model = {
            "schema_version": "1.0",
            "run_id": context.run_id,
            "snapshot_id": context.snapshot_id,
            "run_type": context.run_type,
            "run_status": RunStatusValue.SUCCEEDED.value,
            "cutoff_time": context.cutoff_time.isoformat(),
            "generated_at": utc_now().isoformat(),
            "is_mock": True,
            "market": market,
            "instrument": instrument,
            "macro_event": _event_payload(context.results.get("1b"), "macro"),
            "options": options,
            "instrument_event": _event_payload(context.results.get("3b"), "instrument"),
            "decision": decision,
            "steps": steps + [
                {
                    "code": "5",
                    "label": STEP_LABELS["5"],
                    "status": StepStatus.SUCCEEDED.value,
                    "summary": "已组合 mock 步骤结果并生成前端 read model。",
                    "error_message": None,
                }
            ],
            "data_quality": {
                "is_mock": True,
                "status": ("error" if errors else "mock" if options_is_mock else "mixed"),
                "message": (
                    "市场、事件和决策仍含 mock；期权字段来自 Moomoo LV1 快照。"
                    if not options_is_mock
                    else "所有市场、事件、期权和决策字段均为框架 mock/read-model 占位。"
                ),
                "warnings": warnings,
                "errors": errors,
            },
        }
        return StepResult(
            status=StepStatus.SUCCEEDED,
            summary="已组合 mock 步骤结果并生成前端 read model。",
            payload=read_model,
        )
