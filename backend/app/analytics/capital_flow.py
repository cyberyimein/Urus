from __future__ import annotations

from math import isclose
from typing import Any


SIGNAL_LABELS = {
    "large_order_absorption_candidate": "大额单连续流出后转为流入，而中小额单仍流出：吸收候选",
    "large_order_distribution_risk": "中小额单流入但大额单流出：派发风险",
    "broad_accumulation": "各订单规模同步净流入",
    "broad_distribution": "各订单规模同步净流出",
    "mixed": "订单规模方向分化",
    "insufficient_data": "历史样本不足",
}


def extract_capital_flow_signal(
    observations: list[dict[str, Any]], *, projection_days: int = 5
) -> dict[str, Any]:
    """Turn an ascending daily cache window into bounded, identity-safe evidence."""

    ordered = sorted(observations, key=lambda item: str(item.get("trading_date") or ""))
    valid = [item for item in ordered if _flow(item, "block") is not None]
    quality_warnings = _quality_warnings(ordered)
    recent = ordered[-max(1, projection_days) :]
    if not valid:
        return _result(
            signal="insufficient_data",
            confidence=0.0,
            observations=recent,
            quality_warnings=quality_warnings + ["没有可用的大额单资金流"],
        )

    today = valid[-1]
    prior = valid[:-1][-4:]
    block = _flow(today, "block") or 0.0
    mid_small = _flow(today, "mid_small")
    prior_blocks = [_flow(item, "block") or 0.0 for item in prior]
    prior_outflow_days = sum(value < 0 for value in prior_blocks)
    prior_inflow_days = sum(value > 0 for value in prior_blocks)
    prior_30_blocks = [_flow(item, "block") or 0.0 for item in valid[:-1]]
    prior_outflow_streak = _trailing_streak(prior_30_blocks, positive=False)
    prior_inflow_streak = _trailing_streak(prior_30_blocks, positive=True)

    if len(valid) < 5:
        signal = "insufficient_data"
    elif block > 0 and mid_small is not None and mid_small < 0 and prior_outflow_streak >= 3:
        signal = "large_order_absorption_candidate"
    elif block < 0 and mid_small is not None and mid_small > 0:
        signal = "large_order_distribution_risk"
    elif _all_buckets(today, positive=True):
        signal = "broad_accumulation"
    elif _all_buckets(today, positive=False):
        signal = "broad_distribution"
    else:
        signal = "mixed"

    magnitude_percentile = _magnitude_percentile(
        abs(block), [abs(_flow(item, "block") or 0.0) for item in valid]
    )
    coverage = min(1.0, len(valid) / 5)
    pattern_strength = max(prior_outflow_days, prior_inflow_days) / 4 if prior else 0.0
    confidence = round(min(0.9, 0.35 + coverage * 0.2 + pattern_strength * 0.2 + magnitude_percentile * 0.15), 2)
    if signal in {"mixed", "insufficient_data"}:
        confidence = min(confidence, 0.5)
    if quality_warnings:
        confidence = round(max(0.0, confidence - 0.15), 2)

    return _result(
        signal=signal,
        confidence=confidence,
        observations=recent,
        quality_warnings=quality_warnings,
        features={
            "latest_trading_date": today.get("trading_date"),
            "latest_block_flow": block,
            "latest_mid_small_flow": mid_small,
            "prior_4_block_outflow_days": prior_outflow_days,
            "prior_4_block_inflow_days": prior_inflow_days,
            "prior_4_block_flow_sum": round(sum(prior_blocks), 4),
            "prior_block_outflow_streak_30d": prior_outflow_streak,
            "prior_block_inflow_streak_30d": prior_inflow_streak,
            "latest_block_magnitude_percentile_30d": magnitude_percentile,
            "available_trading_days": len(valid),
        },
    )


def _result(
    *,
    signal: str,
    confidence: float,
    observations: list[dict[str, Any]],
    quality_warnings: list[str],
    features: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "signal": signal,
        "signal_label": SIGNAL_LABELS[signal],
        "confidence": confidence,
        "features": features or {"available_trading_days": len(observations)},
        "recent_5d": [_compact_observation(item) for item in observations],
        "quality_status": "ok" if not quality_warnings else "partial",
        "quality_warnings": quality_warnings,
        "interpretation_guardrail": (
            "字段是按成交订单金额划分的主动净流量，不能等同于机构、散户或账户身份。"
        ),
    }


def _compact_observation(item: dict[str, Any]) -> dict[str, Any]:
    block = _flow(item, "block")
    mid_small = _flow(item, "mid_small")
    return {
        "trading_date": item.get("trading_date"),
        "block_flow": block,
        "mid_small_flow": mid_small,
        "super_in_flow": item.get("super_in_flow"),
        "big_in_flow": item.get("big_in_flow"),
        "mid_in_flow": item.get("mid_in_flow"),
        "sml_in_flow": item.get("sml_in_flow"),
    }


def _flow(item: dict[str, Any], group: str) -> float | None:
    if group == "block":
        main = _number(item.get("main_in_flow"))
        if main is not None:
            return main
        return _sum_if_complete(item, "super_in_flow", "big_in_flow")
    if group == "mid_small":
        return _sum_if_complete(item, "mid_in_flow", "sml_in_flow")
    raise ValueError(f"unknown flow group: {group}")


def _sum_if_complete(item: dict[str, Any], *keys: str) -> float | None:
    values = [_number(item.get(key)) for key in keys]
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _all_buckets(item: dict[str, Any], *, positive: bool) -> bool:
    values = [_number(item.get(key)) for key in ("super_in_flow", "big_in_flow", "mid_in_flow", "sml_in_flow")]
    if any(value is None for value in values):
        return False
    return all(value > 0 for value in values) if positive else all(value < 0 for value in values)


def _magnitude_percentile(value: float, population: list[float]) -> float:
    if not population:
        return 0.0
    return round(sum(candidate <= value for candidate in population) / len(population), 4)


def _trailing_streak(values: list[float], *, positive: bool) -> int:
    streak = 0
    for value in reversed(values):
        if (value > 0) is not positive or value == 0:
            break
        streak += 1
    return streak


def _quality_warnings(observations: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    dates = [str(item.get("trading_date") or "") for item in observations]
    if len(dates) != len(set(dates)):
        warnings.append("存在重复交易日")
    for item in observations:
        day = item.get("trading_date") or "未知日期"
        main = _number(item.get("main_in_flow"))
        super_big = _sum_if_complete(item, "super_in_flow", "big_in_flow")
        total = _number(item.get("in_flow"))
        buckets = _sum_if_complete(
            item, "super_in_flow", "big_in_flow", "mid_in_flow", "sml_in_flow"
        )
        if main is not None and super_big is not None and not isclose(main, super_big, rel_tol=1e-6, abs_tol=1.0):
            warnings.append(f"{day} main 与 super+big 不一致")
        if total is not None and buckets is not None and not isclose(total, buckets, rel_tol=1e-6, abs_tol=1.0):
            warnings.append(f"{day} total 与四档合计不一致")
    return warnings
