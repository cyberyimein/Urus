from __future__ import annotations

from math import isfinite
from typing import Any, Iterable


_STAGE_PRIORITY = {
    "confirmed": 0,
    "near_confirmation": 1,
    "armed": 1,
    "watching": 2,
    "forming": 2,
    "invalidated": 3,
}


def project_symbol_setups(
    decisions: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Project one deterministic, machine-readable primary setup per symbol."""

    unique: dict[str, dict[str, Any]] = {}
    for decision in decisions:
        decision_id = str(decision.get("decision_id") or "")
        symbol = str((decision.get("scope") or {}).get("symbol") or decision.get("symbol") or "")
        plan = decision.get("setup_plan")
        if not symbol or not decision_id or not isinstance(plan, dict):
            continue
        unique.setdefault(decision_id, decision)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for decision in unique.values():
        symbol = str((decision.get("scope") or {}).get("symbol") or decision.get("symbol"))
        plan = dict(decision["setup_plan"])
        strategy = dict(decision.get("strategy") or {})
        progress = dict(decision.get("setup_progress") or {})
        grouped.setdefault(symbol, []).append(
            {
                "decision_id": decision.get("decision_id"),
                "strategy_name": strategy.get("name"),
                "strategy_version": strategy.get("version"),
                "direction": plan.get("direction") or decision.get("stance"),
                "stage": plan.get("stage") or progress.get("stage"),
                "confirmation_distance_atr": _number(
                    plan.get("remaining_atr", progress.get("confirmation_distance_atr"))
                ),
                "score": _number(decision.get("score")),
                "setup_plan": plan,
            }
        )

    result: dict[str, dict[str, Any]] = {}
    for symbol, candidates in sorted(grouped.items()):
        ordered = sorted(candidates, key=_setup_order)
        result[symbol] = {
            "symbol": symbol,
            "candidate_count": len(ordered),
            "primary_setup": ordered[0] if ordered else None,
            "alternatives": ordered[1:],
        }
    return result


def build_setup_plan(
    *,
    direction: str,
    stage: str,
    observation_price: float | None,
    confirmation_price: float | None,
    confirmation_operator: str | None,
    confirmation_reference: str | None,
    invalidation_price: float | None,
    invalidation_operator: str | None,
    invalidation_reference: str | None,
    atr: float | None,
) -> dict[str, Any]:
    remaining = _remaining_distance(observation_price, confirmation_price, confirmation_operator)
    invalidation_distance = _absolute_distance(observation_price, invalidation_price)
    return {
        "direction": direction,
        "stage": stage,
        "confirmation_price": _rounded(confirmation_price),
        "confirmation_operator": confirmation_operator,
        "confirmation_reference": confirmation_reference,
        "observation_price": _rounded(observation_price),
        "remaining_price": _rounded(remaining),
        "remaining_percent": _percent(remaining, observation_price),
        "remaining_atr": _atr_distance(remaining, atr),
        "invalidation_price": _rounded(invalidation_price),
        "invalidation_operator": invalidation_operator,
        "invalidation_reference": invalidation_reference,
        "invalidation_distance_price": _rounded(invalidation_distance),
        "invalidation_distance_percent": _percent(invalidation_distance, observation_price),
        "invalidation_distance_atr": _atr_distance(invalidation_distance, atr),
    }


def _setup_order(item: dict[str, Any]) -> tuple[Any, ...]:
    direction_priority = 0 if item.get("direction") == "bullish" else 1
    stage_priority = _STAGE_PRIORITY.get(str(item.get("stage")), 9)
    distance = _number(item.get("confirmation_distance_atr"))
    score = abs(_number(item.get("score")) or 0)
    return (
        direction_priority,
        stage_priority,
        distance if distance is not None else float("inf"),
        -score,
        str(item.get("strategy_name") or ""),
        str(item.get("decision_id") or ""),
    )


def _remaining_distance(
    observed: float | None, reference: float | None, operator: str | None
) -> float | None:
    if observed is None or reference is None:
        return None
    if operator in {">", ">="}:
        return max(0.0, reference - observed)
    if operator in {"<", "<="}:
        return max(0.0, observed - reference)
    return abs(observed - reference)


def _absolute_distance(left: float | None, right: float | None) -> float | None:
    return abs(left - right) if left is not None and right is not None else None


def _percent(distance: float | None, observed: float | None) -> float | None:
    if distance is None or observed is None or observed == 0:
        return None
    return round(distance / abs(observed) * 100, 4)


def _atr_distance(distance: float | None, atr: float | None) -> float | None:
    if distance is None or atr is None or atr <= 0:
        return None
    return round(distance / atr, 4)


def _number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value)):
        return float(value)
    return None


def _rounded(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None
