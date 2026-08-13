from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.analytics.cta import aggregate_cta_proxy_signals, calculate_cta_proxy_signal
from app.models import StepStatus
from app.workflows.base import StepResult
from app.workflows.context import RunContext


CTA_PROXY_MAP = {
    "SPY": "ES equity-index futures",
    "QQQ": "NQ equity-index futures",
    "IWM": "RTY equity-index futures",
    "IEF": "intermediate Treasury futures",
    "TLT": "long Treasury futures",
    "UUP": "US dollar futures basket",
    "GLD": "GC gold futures",
    "USO": "CL crude-oil futures",
    "HYG": "high-yield credit risk",
    "LQD": "investment-grade credit risk",
    "SMH": "semiconductor risk appetite",
    "IGV": "software risk appetite",
}

CTA_ASSET_CLASS_MAP = {
    "SPY": "equity",
    "QQQ": "equity",
    "IWM": "equity",
    "SMH": "equity",
    "IGV": "equity",
    "IEF": "duration",
    "TLT": "duration",
    "UUP": "usd",
    "GLD": "commodity",
    "USO": "commodity",
    "HYG": "credit",
    "LQD": "credit",
}


class MarketCTAProxyStep:
    code = "1b"
    label = "1B · CTA 市场压力"

    def execute(self, context: RunContext) -> StepResult:
        if context.should_fail(self.code):
            return StepResult(
                status=StepStatus.FAILED,
                summary="模拟失败：CTA 市场压力未完成。",
                error_message="requested mock failure at step 1b",
                data_state="unavailable",
            )
        signals = _signals_from_input(context.cta_market_input)
        return _result(
            category="macro",
            scope="market",
            signals=signals,
            expected_symbols=["QQQ"],
            summary_prefix="CTA 市场代理",
        )


class InstrumentCTAProxyStep:
    code = "3b"
    label = "3B · 系统化资金压力"

    def execute(self, context: RunContext) -> StepResult:
        if context.should_fail(self.code):
            return StepResult(
                status=StepStatus.FAILED,
                summary="模拟失败：系统化资金压力未完成。",
                error_message="requested mock failure at step 3b",
                data_state="unavailable",
            )
        signals = _signals_from_input(context.instrument_persistence_input)
        expected = [symbol for symbol in context.cta_proxy_symbols if symbol in CTA_PROXY_MAP]
        return _result(
            category="instrument",
            scope="cross_asset",
            signals=signals,
            expected_symbols=expected,
            summary_prefix="CTA 跨资产代理",
        )


def _signals_from_input(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, Mapping):
        return []
    raw_symbols = payload.get("symbols", [])
    if not isinstance(raw_symbols, Sequence) or isinstance(raw_symbols, (str, bytes)):
        return []
    signals: list[dict[str, object]] = []
    for item in raw_symbols:
        if not isinstance(item, Mapping):
            continue
        symbol = str(item.get("symbol") or "").upper()
        if symbol not in CTA_PROXY_MAP:
            continue
        history = item.get("history", {})
        bars = history.get("bars", []) if isinstance(history, Mapping) else []
        if not isinstance(bars, Sequence) or isinstance(bars, (str, bytes)):
            bars = []
        signals.append(
            calculate_cta_proxy_signal(
                bars,
                symbol=symbol,
                source=str(payload.get("provider") or "moomoo_opend_history"),
                proxy_for=CTA_PROXY_MAP[symbol],
            )
        )
    return signals


def _result(
    *,
    category: str,
    scope: str,
    signals: list[dict[str, object]],
    expected_symbols: list[str],
    summary_prefix: str,
) -> StepResult:
    available = [item for item in signals if item.get("available") is True]
    returned = {str(item.get("symbol")) for item in available}
    missing = [symbol for symbol in expected_symbols if symbol not in returned]
    aggregate = aggregate_cta_proxy_signals(signals)
    payload: dict[str, Any] = {
        "schema_version": "urus.cta_proxy_overlay.v1",
        "is_mock": False,
        "category": category,
        "status": StepStatus.SUCCEEDED.value if available else StepStatus.UNAVAILABLE.value,
        "mode": "cta_proxy",
        "variant": "cta",
        "scope": scope,
        "signals": signals,
        "aggregate": aggregate,
        "expected_symbols": expected_symbols,
        "missing_symbols": missing,
        "quality_status": "ok" if available and not missing else "partial" if available else "unavailable",
        "warnings": [
            "这是基于 ETF 日线的典型 CTA 模型估算，不是已观察到的基金仓位或真实资金流。",
            *([f"缺少 CTA 代理日线：{', '.join(missing)}"] if missing else []),
        ],
        "summary": f"{summary_prefix}已生成 {len(available)}/{len(expected_symbols)} 个有效信号。",
    }
    if not available:
        return StepResult(
            status=StepStatus.UNAVAILABLE,
            summary=f"{summary_prefix}缺少可用历史日线。",
            payload=payload,
            data_state="unavailable",
        )
    return StepResult(
        status=StepStatus.SUCCEEDED,
        summary=str(payload["summary"]),
        payload=payload,
        data_state="derived",
    )


def build_systematic_flows(
    market_payload: object,
    instrument_payload: object,
    *,
    run_type: str,
) -> dict[str, Any]:
    """Combine 1B/3B CTA payloads into one first-class Stage 4 evidence view."""

    sources = [
        value for value in (market_payload, instrument_payload)
        if isinstance(value, Mapping) and value.get("variant") == "cta"
    ]
    if not sources:
        return {}
    # Prefer the cross-asset 3B copy when a symbol is present in both overlays.
    indexed: dict[str, dict[str, Any]] = {}
    for source in sources:
        for raw in source.get("signals", []):
            if not isinstance(raw, Mapping):
                continue
            symbol = str(raw.get("symbol") or "").upper()
            if not symbol:
                continue
            item = dict(raw)
            item["asset_class"] = CTA_ASSET_CLASS_MAP.get(symbol, "other")
            item["mechanical_action"] = _mechanical_action(
                item.get("previous_target_exposure"), item.get("target_exposure")
            )
            indexed[symbol] = item
    assets = [indexed[symbol] for symbol in sorted(indexed)]
    available = [item for item in assets if item.get("available") is True]
    asset_classes: dict[str, dict[str, Any]] = {}
    for name in sorted({str(item["asset_class"]) for item in available}):
        group = [item for item in available if item["asset_class"] == name]
        exposures = [float(item["target_exposure"]) for item in group]
        pressures = [float(item["pressure_index"]) for item in group]
        asset_classes[name] = {
            "symbols": [str(item["symbol"]) for item in group],
            "signal_count": len(group),
            "unweighted_net_exposure": round(sum(exposures), 6),
            "unweighted_gross_exposure": round(sum(abs(value) for value in exposures), 6),
            "average_pressure_index": round(sum(pressures) / len(pressures), 2),
        }
    exposures = [float(item["target_exposure"]) for item in available]
    warnings = list(
        dict.fromkeys(
            str(warning)
            for source in sources
            for warning in source.get("warnings", [])
            if warning
        )
    )
    warnings.append(
        "Portfolio exposure is unweighted and not correlation-adjusted; use asset-class groups, not a cross-asset arithmetic average."
    )
    model_state = {
        "post_close_review": "official_close_model",
        "pre_close": "intraday_estimate",
        "pre_market": "pre_market_context",
    }.get(run_type, "research_estimate")
    return {
        "schema_version": "urus.systematic_flows.v1",
        "model_version": "cta_proxy_v1",
        "source_mode": "etf_proxy",
        "run_type": run_type,
        "model_state": model_state,
        "provisional": run_type != "post_close_review",
        "as_of": max((str(item.get("as_of") or "") for item in available), default=None),
        "available": bool(available),
        "assets": assets,
        "portfolio": {
            "signal_count": len(available),
            "unweighted_net_exposure": round(sum(exposures), 6),
            "unweighted_gross_exposure": round(sum(abs(value) for value in exposures), 6),
            "asset_classes": asset_classes,
            "correlation_adjusted_risk": None,
            "risk_contribution": None,
        },
        "quality": {
            "status": "ok" if available and not any(source.get("missing_symbols") for source in sources) else "partial",
            "missing_symbols": sorted(
                {
                    str(symbol)
                    for source in sources
                    for symbol in source.get("missing_symbols", [])
                    if symbol
                }
            ),
            "warnings": warnings,
        },
        "limitations": [
            "ETF proxy estimates are not observed CTA positions or fund flows.",
            "CFTC positioning confirmation is not yet connected.",
        ],
    }


def _mechanical_action(previous: object, current: object) -> str:
    if not isinstance(previous, (int, float)) or not isinstance(current, (int, float)):
        return "unknown"
    delta = float(current) - float(previous)
    if abs(delta) < 0.05:
        return "hold"
    if previous <= 0 < current:
        return "flip_long"
    if previous >= 0 > current:
        return "flip_short"
    if delta > 0 and current > 0:
        return "add_long"
    if delta < 0 and current >= 0:
        return "reduce_long"
    if delta < 0 and current < 0:
        return "add_short"
    if delta > 0 and current <= 0:
        return "cover_short"
    return "hold"
