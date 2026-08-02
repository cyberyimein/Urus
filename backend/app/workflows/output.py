from __future__ import annotations

from app.core.time import utc_now
from app.models import StepStatus
from app.workflows.base import StepResult, data_state_for
from app.workflows.context import RunContext


STEP_LABELS = {
    "1a": "1A · 大盘采集",
    "1b": "1B · 宏观事件摘要",
    "2": "2 · 期权占位",
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
    payload.setdefault("data_state", data_state_for(result))
    if result.error_message:
        payload["reason"] = result.error_message
    return payload


def _data_payload(result: StepResult | None) -> dict[str, object] | None:
    if result is None or result.status in {StepStatus.FAILED, StepStatus.SKIPPED} or not result.payload:
        return None
    payload = dict(result.payload)
    payload.setdefault("is_mock", True)
    payload.setdefault("data_state", data_state_for(result))
    return payload


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

        result_steps = []
        errors: list[str] = []
        warnings: list[str] = []
        for code, result in context.results.items():
            result_steps.append(
                {
                    "code": code,
                    "label": STEP_LABELS.get(code, code),
                    "status": result.status.value,
                    "data_state": data_state_for(result),
                    "summary": result.summary,
                    "error_message": result.error_message,
                }
            )
            if result.status in {
                StepStatus.SKIPPED,
                StepStatus.PLACEHOLDER,
                StepStatus.UNAVAILABLE,
            }:
                warnings.append(result.summary)
            if result.status == StepStatus.FAILED and result.error_message:
                errors.append(f"{code}: {result.error_message}")

        market = _data_payload(context.results.get("1a"))
        instrument = _data_payload(context.results.get("3a"))
        options = (
            context.results.get("2").payload
            if context.results.get("2") and context.results.get("2").payload
            else {
                "is_mock": True,
                "status": "unavailable",
                "available": False,
                "note": "期权占位结果不可用。",
            }
        )
        decision = (
            context.results.get("4").payload
            if context.results.get("4") and context.results.get("4").payload
            else {
                "is_mock": True,
                "status": "unavailable",
                "stance": None,
                "confidence": None,
                "summary": "决策占位结果不可用。",
                "note": "框架阶段不执行真实决策 AI。",
            }
        )
        if market:
            market_warnings = market.get("quality_warnings", [])
            if isinstance(market_warnings, list):
                warnings.extend(str(item) for item in market_warnings)
            market_snapshot = market.get("market_snapshot", {})
            if isinstance(market_snapshot, dict):
                snapshot_errors = market_snapshot.get("quality_errors", [])
                if isinstance(snapshot_errors, list):
                    errors.extend(f"market_snapshot: {item}" for item in snapshot_errors)
            macro_context = market.get("macro_context", {})
            if isinstance(macro_context, dict):
                macro_warnings = macro_context.get("quality_warnings", [])
                if isinstance(macro_warnings, list):
                    warnings.extend(str(item) for item in macro_warnings)
                macro_errors = macro_context.get("quality_errors", [])
                if isinstance(macro_errors, list):
                    errors.extend(f"macro: {item}" for item in macro_errors)
        has_live_market = bool(market and market.get("is_mock") is False)
        market_quality_status = str(market.get("quality_status", "mock")) if market else "unavailable"
        macro_quality_status = "unavailable"
        if market and isinstance(market.get("macro_context"), dict):
            macro_quality_status = str(market["macro_context"].get("quality_status", "unavailable"))
        contains_mock = any(
            not isinstance(section, dict) or bool(section.get("is_mock", True))
            for section in (market, instrument, options, decision)
        )
        if errors or (
            has_live_market
            and (market_quality_status not in {"ok"} or macro_quality_status not in {"ok"})
        ):
            data_quality_status = "degraded"
        elif has_live_market:
            data_quality_status = "mixed"
        else:
            data_quality_status = "mock"
        data_quality_message = (
            "大盘代理批量快照来自 Moomoo OpenD；Yahoo 可用宏观指标每次采集并优先使用，"
            "FRED 提供 2Y 并保留交叉校验，"
            "其余尚未进入阶段 1A 的流程仍是 mock/placeholder。"
            if has_live_market
            else "所有市场、事件、期权和决策字段均为框架 mock/read-model 占位。"
        )
        data_mode = str(market.get("data_mode", "mock")) if market else "mock"
        data_state = (
            "mixed"
            if has_live_market and contains_mock
            else "live"
            if has_live_market
            else "mock"
            if contains_mock
            else "unavailable"
        )
        read_model = {
            "schema_version": "1.0",
            "data_mode": data_mode,
            "run_id": context.run_id,
            "snapshot_id": context.snapshot_id,
            "run_type": context.run_type,
            "run_status": data_state if data_state == "mixed" else "succeeded",
            "cutoff_time": context.cutoff_time.isoformat(),
            "generated_at": utc_now().isoformat(),
            "data_state": data_state,
            "is_mock": contains_mock,
            "market": market,
            "instrument": instrument,
            "macro_event": _event_payload(context.results.get("1b"), "macro"),
            "options": options,
            "instrument_event": _event_payload(context.results.get("3b"), "instrument"),
            "decision": decision,
            "steps": result_steps + [
                {
                            "code": "5",
                            "label": STEP_LABELS["5"],
                            "status": StepStatus.SUCCEEDED.value,
                            "data_state": "mixed" if market and not market.get("is_mock", True) else "mock",
                            "summary": (
                        "已组合 Moomoo 大盘批量快照、宏观上下文和其余 mock 步骤，生成前端 read model。"
                        if has_live_market
                        else "已组合 mock 步骤结果并生成前端 read model。"
                    ),
                    "error_message": None,
                }
            ],
            "data_quality": {
                "is_mock": contains_mock,
                "data_state": data_state,
                "status": data_quality_status,
                "message": data_quality_message,
                "warnings": warnings,
                "errors": errors,
            },
        }
        return StepResult(
            status=StepStatus.SUCCEEDED,
            summary=(
                "已组合 Moomoo 大盘批量快照、宏观上下文和其余 mock 步骤，生成前端 read model。"
                if has_live_market
                else "已组合 mock 步骤结果并生成前端 read model。"
            ),
            payload=read_model,
            data_state=data_state,
        )
