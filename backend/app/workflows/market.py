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
            macro_context = (
                context.macro_adapter.daily_context(context.cutoff_time)
                if context.macro_adapter is not None
                else {
                    "is_mock": True,
                    "data_mode": "mock",
                    "source": "fred_not_enabled",
                    "observations": {},
                    "derived": {},
                    "quality_status": "unavailable",
                    "quality_warnings": ["FRED 日频宏观数据源尚未启用。"],
                    "quality_errors": [],
                }
            )
            payload["macro_context"] = macro_context
            if context.capital_flow_service is not None:
                try:
                    payload["capital_flows"] = context.capital_flow_service.collect(
                        context.cutoff_time
                    )
                except Exception as exc:
                    payload["capital_flows"] = {
                        "schema_version": "urus.capital_flow_cache.v1",
                        "quality_status": "unavailable",
                        "quality_warnings": [f"订单金额分档资金流采集失败：{exc}"],
                        "symbols": [],
                    }
                    payload["quality_status"] = "partial"
            if macro_context.get("quality_status") != "ok":
                payload["quality_status"] = "partial"
            is_mock = bool(payload.get("is_mock", True))
            data_state = "mock" if is_mock else "live"
            payload["data_state"] = data_state
            macro_mode = str(macro_context.get("data_mode", "mock"))
            market_snapshot = payload.get("market_snapshot", {})
            returned_symbols = (
                market_snapshot.get("returned_symbols", [])
                if isinstance(market_snapshot, dict)
                else []
            )
            unavailable_symbols = (
                market_snapshot.get("unavailable_symbols", [])
                if isinstance(market_snapshot, dict)
                else []
            )
            return StepResult(
                status=StepStatus.SUCCEEDED,
                summary=(
                    f"已通过 Moomoo OpenD 批量采集 {len(returned_symbols)} 个大盘/跨资产快照，"
                    f"并保留 QQQ 日线摘要；{macro_mode} 日频宏观上下文已接入。"
                    + (f" 未返回：{', '.join(map(str, unavailable_symbols))}。" if unavailable_symbols else "")
                    if not is_mock
                    else "已生成 QQQ mock 大盘卡；未请求真实行情。"
                ),
                payload=payload,
                data_state=data_state,
            )
        except Exception as exc:
            return StepResult(
                status=StepStatus.FAILED,
                summary="QQQ 大盘采集失败。",
                error_message=str(exc),
                data_state="unavailable",
            )
