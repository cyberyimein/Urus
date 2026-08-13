from __future__ import annotations

from app.models import StepStatus
from app.workflows.base import StepResult
from app.workflows.context import RunContext


class InstrumentCollectorStep:
    code = "3a"
    label = "3A · 个股采集"

    def execute(self, context: RunContext) -> StepResult:
        if context.should_fail(self.code):
            return StepResult(
                status=StepStatus.FAILED,
                summary="模拟失败：个股采集步骤未完成。",
                error_message="requested mock failure at step 3a",
            )
        try:
            if context.moomoo_adapter is None:
                raise RuntimeError("instrument adapter is not configured")
            collector = getattr(context.moomoo_adapter, "instrument_cards", None)
            if callable(collector):
                collected = dict(collector(context.instrument_symbols))
            else:
                cards = [
                    dict(context.moomoo_adapter.instrument_card(symbol))
                    for symbol in context.instrument_symbols
                ]
                collected = {
                    "is_mock": all(bool(card.get("is_mock", True)) for card in cards),
                    "status": "available",
                    "available": bool(cards),
                    "data_state": "live" if cards else "unavailable",
                    "requested_symbols": context.instrument_symbols,
                    "unavailable_symbols": [],
                    "instruments": cards,
                    "quality_status": "ok",
                    "quality_warnings": [],
                    "note": "3A 个股/ETF 数据已采集。",
                }
            cards = [item for item in collected.get("instruments", []) if isinstance(item, dict)]
            provider = str(collected.get("provider") or "unknown")
            source_mode = str(collected.get("source_mode") or "snapshot")
            captured_at = collected.get("captured_at")
            # Keep collection metadata on every card. The collector payload is
            # the source of truth; otherwise InstrumentCard's mock-oriented
            # schema defaults can make live data look like a mock card.
            normalized_cards = []
            for item in cards:
                config = context.universe_items_by_symbol.get(str(item.get("symbol")), {})
                configured_themes = [
                    str(theme).strip()
                    for theme in (config.get("themes") or [])
                    if str(theme).strip()
                ]
                if not configured_themes and config.get("theme"):
                    configured_themes = [str(config["theme"]).strip()]
                provider_themes = [
                    str(theme).strip()
                    for theme in (item.get("themes") or [])
                    if str(theme).strip()
                ]
                themes = configured_themes or provider_themes or [str(item.get("theme") or "其他关注")]
                normalized_cards.append({
                    **item,
                    "asset_type": config.get("asset_type", item.get("asset_type", "equity")),
                    "theme": themes[0],
                    "themes": list(dict.fromkeys(themes)),
                    "provider": item.get("provider") or provider,
                    "source_mode": item.get("source_mode") or source_mode,
                    "captured_at": item.get("captured_at") or captured_at,
                })
            cards = normalized_cards
            primary = next((item for item in cards if item.get("symbol") == "INTC"), None)
            primary = primary or (cards[0] if cards else {
                "is_mock": False,
                "symbol": "INTC",
                "label": "INTC · unavailable",
                "last_price": None,
                "change_percent": None,
                "trend": None,
                "technical_note": "本轮没有返回 3A 数据。",
                "note": "本轮没有返回配置的 3A 标的数据。",
            })
            is_live = any(item.get("is_mock") is False for item in cards)
            payload = {
                **primary,
                "is_mock": not is_live,
                "status": collected.get("status", "unavailable"),
                "available": bool(collected.get("available", False)),
                "data_state": "live" if is_live else "unavailable",
                "requested_symbols": collected.get("requested_symbols", context.instrument_symbols),
                "unavailable_symbols": collected.get("unavailable_symbols", []),
                "provider": provider,
                "source_mode": source_mode,
                "captured_at": captured_at,
                "instruments": cards,
                "quota_audit": collected.get("quota_audit", {}),
                "quality_status": collected.get("quality_status", "unavailable"),
                "quality_warnings": collected.get("quality_warnings", []),
                "note": collected.get("note", "3A 个股/ETF 数据已采集。"),
            }
            persistence = collected.get("_persistence")
            if isinstance(persistence, dict):
                payload["_persistence"] = persistence
            if not cards:
                return StepResult(
                    status=StepStatus.UNAVAILABLE,
                    summary="3A 未返回配置的行情/日线数据。",
                    payload=payload,
                    data_state="unavailable",
                )
            return StepResult(
                status=StepStatus.SUCCEEDED if is_live else StepStatus.UNAVAILABLE,
                summary=(
                    f"3A 已采集 {', '.join(str(item.get('symbol')) for item in cards)}；"
                    "使用 Moomoo 快照和历史日线计算技术指标。"
                    if is_live
                    else "3A 个股/ETF 仍为 mock 结构，不能作为行情证据。"
                ),
                payload=payload,
                data_state="live" if is_live else "unavailable",
            )
        except Exception as exc:
            return StepResult(
                status=StepStatus.FAILED,
                summary="个股 mock 卡生成失败。",
                error_message=str(exc),
                data_state="unavailable",
            )
