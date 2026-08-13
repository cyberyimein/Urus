from __future__ import annotations

from app.core.time import utc_now
from app.models import StepStatus
from app.urus_agent.evidence import EvidenceStore
from app.urus_agent.reports import build_technical_report
from app.workflows.base import StepResult, data_state_for
from app.workflows.context import RunContext
from app.workflows.cta import build_systematic_flows


STEP_LABELS = {
    "1a": "1A · 大盘采集",
    "1b": "1B · 宏观事件摘要",
    "2": "2 · 期权结构",
    "3a": "3A · 个股采集",
    "3b": "3B · 个股事件摘要",
    "4": "4 · Urus Agent 决策",
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


def _decision_payload(result: StepResult | None) -> dict[str, object] | None:
    """Keep explicit waiting/blocked states even when Step 4 did not run a model."""

    if result is None or not result.payload:
        return None
    payload = dict(result.payload)
    payload.setdefault("is_mock", False)
    payload.setdefault("data_state", data_state_for(result))
    # A failed Step 4 can carry only an error code/message.  Keep that
    # diagnostic state readable by the discriminated read-model schema rather
    # than letting it fail a second time because DecisionAnalysis requires the
    # provider and note fields.
    if payload.get("is_mock") is False:
        payload.setdefault("provider", "not_called")
        payload.setdefault(
            "note",
            result.error_message or result.summary or "决策步骤未生成模型结果。",
        )
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
            label = STEP_LABELS.get(code, code)
            if result.payload.get("variant") == "cta":
                label = (
                    "1B · CTA 市场压力"
                    if code == "1b"
                    else "3B · 系统化资金压力"
                    if code == "3b"
                    else label
                )
            result_steps.append(
                {
                    "code": code,
                    "label": label,
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
        instrument_cards = [
            item
            for item in (instrument or {}).get("instruments", [])
            if isinstance(item, dict)
        ]
        systematic_flows = build_systematic_flows(
            context.results.get("1b").payload if context.results.get("1b") else {},
            context.results.get("3b").payload if context.results.get("3b") else {},
            run_type=context.run_type,
        )
        options = _data_payload(context.results.get("2")) or {
            "is_mock": True,
            "status": "unavailable",
            "available": False,
            "data_state": "unavailable",
            "note": "期权结构结果不可用。",
        }
        decision = _decision_payload(context.results.get("4")) or {
            "is_mock": True,
            "status": "unavailable",
            "stance": None,
            "confidence": None,
            "summary": "决策占位结果不可用。",
            "note": "框架阶段不执行真实决策 AI。",
        }
        technical_report = decision.get("technical_report") if isinstance(decision, dict) else None
        if not isinstance(technical_report, dict) or not technical_report:
            try:
                if context.decision_packet is None:
                    raise ValueError(context.decision_pair_reason or "Decision Dataset is not ready.")
                technical_report = build_technical_report(EvidenceStore(context.decision_packet))
            except Exception as exc:  # preserve an otherwise readable read model
                technical_report = {
                    "schema_version": "urus.technical_report.v1",
                    "status": "waiting_for_pair"
                    if context.decision_pair_status.startswith("waiting")
                    else "unavailable",
                    "error": str(exc),
                }
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
        if instrument:
            instrument_warnings = instrument.get("quality_warnings", [])
            if isinstance(instrument_warnings, list):
                warnings.extend(f"3a: {item}" for item in instrument_warnings)
        has_live_market = bool(market and market.get("is_mock") is False)
        has_live_instrument = bool(instrument and instrument.get("is_mock") is False)
        has_live_options = bool(options.get("is_mock") is False and options.get("available"))
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
        elif (has_live_market or has_live_instrument or has_live_options) and contains_mock:
            data_quality_status = "mixed"
        elif has_live_market or has_live_instrument or has_live_options:
            data_quality_status = "live"
        else:
            data_quality_status = "mock"
        if has_live_market and has_live_instrument and has_live_options:
            data_quality_message = (
                "大盘、3A 个股/ETF 与期权来自 Moomoo OpenD/LV1 快照；"
                "宏观数据按 Stage 1A 来源策略采集，事件或决策中仍可能包含 mock/placeholder。"
            )
        elif has_live_market and has_live_instrument:
            data_quality_message = (
                "大盘与 3A 个股/ETF 来自 Moomoo OpenD；宏观、期权、事件和决策中仍可能包含 mock/placeholder。"
            )
        elif has_live_market and has_live_options:
            data_quality_message = (
                "大盘与期权来自 Moomoo OpenD 快照；宏观数据按 Stage 1A 来源策略采集，"
                "个股、事件或决策中仍可能包含 mock/placeholder。"
            )
        elif has_live_market:
            data_quality_message = (
                "大盘代理批量快照来自 Moomoo OpenD；Yahoo/FRED 提供宏观上下文，"
                "期权及其余未接入流程仍为 mock/placeholder。"
            )
        elif has_live_options:
            data_quality_message = (
                "期权字段来自 Moomoo LV1 快照；市场、事件和决策仍含 mock/placeholder。"
            )
        else:
            data_quality_message = "所有市场、事件、期权和决策字段均为框架 mock/read-model 占位。"
        data_mode = (
            "mixed"
            if sum([has_live_market, has_live_instrument, has_live_options]) > 1
            else str(market.get("data_mode", "mock"))
            if has_live_market and market
            else "opend"
            if has_live_instrument
            else str(options.get("source_mode", "mock"))
            if has_live_options
            else "mock"
        )
        data_state = (
            "mixed"
            if (has_live_market or has_live_instrument or has_live_options) and contains_mock
            else "live"
            if has_live_market or has_live_instrument or has_live_options
            else "mock"
            if contains_mock
            else "unavailable"
        )
        if has_live_market and has_live_instrument and has_live_options:
            output_summary = "已组合 Stage 1A 大盘、3A 个股/ETF 与 Stage 2 期权快照，生成统一前端 read model。"
        elif has_live_market and has_live_options:
            output_summary = "已组合 Stage 1A 大盘与 Stage 2 期权快照，生成统一前端 read model。"
        elif has_live_market and has_live_instrument:
            output_summary = "已组合 Stage 1A 大盘与 3A 个股/ETF，生成统一前端 read model。"
        elif has_live_market or has_live_instrument or has_live_options:
            output_summary = "已组合真实采集结果与其余 mock/placeholder，生成前端 read model。"
        else:
            output_summary = "已组合 mock 步骤结果并生成前端 read model。"
        read_model = {
            "schema_version": "1.0",
            "data_mode": data_mode,
            "run_id": context.run_id,
            "snapshot_id": context.snapshot_id,
            "run_type": context.run_type,
            "trigger_type": context.trigger_type,
            "analysis_mode": context.analysis_mode,
            "session_context": context.session_context,
            "official_cycle": context.official_cycle,
            "eligible_for_scoring": context.eligible_for_scoring,
            "updates_official_cta_state": context.updates_official_cta_state,
            "universe": {
                "version_id": context.universe_version_id,
                "content_sha256": context.universe_content_sha256,
                "requested_symbols": context.symbols,
                "items": list(context.universe_items_by_symbol.values()),
            },
            "run_status": data_state if data_state == "mixed" else "succeeded",
            "cutoff_time": context.cutoff_time.isoformat(),
            "generated_at": utc_now().isoformat(),
            "data_state": data_state,
            "is_mock": contains_mock,
            "market": market,
            "instrument": instrument,
            "instrument_cards": instrument_cards,
            "macro_event": _event_payload(context.results.get("1b"), "macro"),
            "options": options,
            "instrument_event": _event_payload(context.results.get("3b"), "instrument"),
            "systematic_flows": systematic_flows,
            "decision": decision,
            "technical_report": technical_report,
            "steps": result_steps + [
                {
                    "code": "5",
                    "label": STEP_LABELS["5"],
                    "status": StepStatus.SUCCEEDED.value,
                    "data_state": data_state,
                    "summary": output_summary,
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
            summary=output_summary,
            payload=read_model,
            data_state=data_state,
        )
