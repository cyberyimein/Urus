from __future__ import annotations

from app.integrations.decision import DecisionRequest
from app.models import StepStatus
from app.workflows.base import StepResult
from app.workflows.context import RunContext


class DecisionStep:
    code = "4"
    label = "4 · Urus Agent 决策"

    def execute(self, context: RunContext) -> StepResult:
        if context.should_fail(self.code):
            return StepResult(
                status=StepStatus.FAILED,
                summary="模拟失败：决策占位步骤未完成。",
                error_message="requested mock failure at step 4",
            )
        if context.run_type == "pre_close":
            return StepResult(
                status=StepStatus.SUCCEEDED,
                summary="尾盘按 collection-only 策略完成数据冻结；未调用决策 AI。",
                payload={
                    "is_mock": False,
                    "status": "collection_only",
                    "availability_status": "not_applicable",
                    "data_state": "derived",
                    "provider": "not_called",
                    "decision_policy": "collection_only",
                    "decision_phase": "pre_close",
                    "dataset_key": context.decision_dataset_key,
                    "source_run_ids": context.decision_source_run_ids,
                    "source_snapshot_ids": context.decision_source_snapshot_ids,
                    "technical_report": {},
                    "decision_report": {},
                    "decision": {},
                    "note": "尾盘只采集、校验和冻结数据；AI 每日仅在盘前与收盘复盘运行。",
                },
                data_state="derived",
            )
        try:
            if context.decision_adapter is None:
                raise RuntimeError("decision adapter is not configured")
            if context.decision_packet is None:
                return StepResult(
                    status=StepStatus.UNAVAILABLE,
                    summary="当前阶段未形成冻结的 Daily Decision Dataset，未调用决策 AI。",
                    payload={
                        "is_mock": False,
                        "status": "dataset_unavailable",
                        "availability_status": "dataset_unavailable",
                        "data_state": "unavailable",
                        "provider": "not_called",
                        "pair_status": context.decision_pair_status,
                        "reason": context.decision_pair_reason,
                        "decision_phase": context.decision_phase,
                        "note": "Daily Decision Dataset 未完成，因此没有调用模型。",
                    },
                    data_state="unavailable",
                )
            if context.decision_enabled:
                preflight_error = _preflight(context)
                if preflight_error:
                    return StepResult(
                        status=StepStatus.FAILED,
                        summary="决策输入未通过数据质量预检。",
                        payload={"status": "failed", "data_state": "unavailable", "error_code": "data_quality_blocked"},
                        error_message=preflight_error,
                        data_state="unavailable",
                    )
            evidence = {code: result.payload for code, result in context.results.items()}
            response = context.decision_adapter.decide(
                DecisionRequest(
                    session_id=f"urus-{context.run_id}-step-4",
                    evidence=evidence,
                    task_type="equity_ranking",
                    symbols=context.symbols or context.instrument_symbols,
                    cutoff_time=context.cutoff_time,
                    dataset_key=context.decision_dataset_key,
                    workflow_run_id=context.run_id,
                    source_snapshot_ids=context.decision_source_snapshot_ids,
                    source_run_ids=context.decision_source_run_ids,
                    decision_packet=context.decision_packet,
                    decision_phase=context.decision_phase,
                    trading_date=context.decision_trading_date,
                    parent_session_id=context.decision_parent_session_id,
                    analysis_metadata={
                        "trigger_type": context.trigger_type,
                        "analysis_mode": context.analysis_mode,
                        "session_context": context.session_context,
                        "report_scope": ["technical_report", "ai_state_analysis"]
                        if context.run_type == "manual_analysis"
                        else ["technical_report", "ai_decision", "ai_review"],
                        "official_cycle": context.official_cycle,
                        "eligible_for_scoring": context.eligible_for_scoring,
                        "updates_official_cta_state": context.updates_official_cta_state,
                    },
                )
            )
            if not response.is_mock and response.result is not None:
                result = response.result
                if getattr(result, "status", None) != "succeeded":
                    return StepResult(
                        status=StepStatus.FAILED,
                        summary="Urus Agent 决策失败。",
                        payload={
                            "is_mock": False,
                            "status": getattr(result, "status", "failed"),
                            "data_state": "unavailable",
                            "provider": response.result.provider,
                            "decision_session_id": response.session_id,
                            "technical_report": response.technical_report,
                            "decision_report": response.decision_report,
                            "error_code": result.error_code,
                            "error_message": result.error_message,
                        },
                        error_message=result.error_message or result.error_code or "agent decision failed",
                        data_state="unavailable",
                    )
                return StepResult(
                    status=StepStatus.SUCCEEDED,
                    summary="Urus Agent 已生成结构化研究决策；不执行交易。",
                    payload={
                        "is_mock": False,
                        "status": "succeeded",
                        "data_state": "derived",
                        "provider": result.provider,
                        "model": result.model,
                        "skill_name": result.skill_name,
                        "skill_hash": result.skill_hash,
                        "tool_call_count": result.tool_call_count,
                        "decision_session_id": response.session_id,
                        "dataset_key": context.decision_dataset_key,
                        "decision_phase": context.decision_phase,
                        "trigger_type": context.trigger_type,
                        "analysis_mode": context.analysis_mode,
                        "session_context": context.session_context,
                        "official_cycle": context.official_cycle,
                        "eligible_for_scoring": context.eligible_for_scoring,
                        "updates_official_cta_state": context.updates_official_cta_state,
                        "trading_date": context.decision_trading_date,
                        "parent_report_id": context.decision_parent_session_id,
                        "source_run_ids": context.decision_source_run_ids,
                        "source_snapshot_ids": context.decision_source_snapshot_ids,
                        "technical_report": response.technical_report,
                        "decision_report": response.decision_report,
                        "input_hash": result.input_hash,
                        "decision": result.output,
                        "note": "Urus Agent research output only; no order was placed.",
                    },
                    data_state="derived",
                )
            return StepResult(
                status=StepStatus.PLACEHOLDER,
                summary="决策 AI 未启用；未展示或生成任何 AI 决策内容。",
                payload={
                    "is_mock": True,
                    # Keep the legacy read-model status for clients that
                    # validate the Step 4 enum; availability_status/data_state
                    # carries the unambiguous disabled semantics.
                    "status": StepStatus.PLACEHOLDER.value,
                    "availability_status": "disabled",
                    "data_state": "disabled",
                    "stance": response.stance,
                    "confidence": response.confidence,
                    "summary": response.summary,
                    "dataset_key": context.decision_dataset_key,
                    "source_run_ids": context.decision_source_run_ids,
                    "source_snapshot_ids": context.decision_source_snapshot_ids,
                    "note": "URUS_AGENT_ENABLED=false；此工作流没有可复盘的模型节点。",
                },
                data_state="disabled",
            )
        except Exception as exc:
            return StepResult(
                status=StepStatus.FAILED,
                summary="决策占位生成失败。",
                error_message=str(exc),
                data_state="unavailable",
            )


def _preflight(context: RunContext) -> str | None:
    """Block Stage 4B when required upstream evidence is missing or explicitly bad.

    A normal mock/partial warning is still visible to the Agent through the
    packet.  Only explicit blocking errors or failed upstream steps prevent a
    model invocation.
    """
    for code in ("1a", "3a"):
        result = context.results.get(code)
        if result is None:
            return f"decision_input_missing: upstream step {code} has no result"
        if result.status in {StepStatus.FAILED, StepStatus.UNAVAILABLE}:
            return f"decision_input_unavailable: upstream step {code} status={result.status.value}"
        payload = result.payload if isinstance(result.payload, dict) else {}
        blocking = _blocking_errors(payload)
        if blocking:
            return f"data_quality_blocked: upstream step {code}: " + "; ".join(blocking[:3])
    return None


def _blocking_errors(payload: dict[str, object]) -> list[str]:
    values: list[str] = []
    for key in ("data_quality", "quality"):
        value = payload.get(key)
        if isinstance(value, dict):
            errors = value.get("blocking_errors")
            if isinstance(errors, list):
                values.extend(str(item) for item in errors if item)
            if value.get("status") in {"blocked", "error"} and not errors:
                values.append(f"quality status={value.get('status')}")
    return values
