from __future__ import annotations

from math import isfinite
from typing import Any


def sample_support(valid_count: int, requested_count: int) -> dict[str, Any]:
    coverage = valid_count / requested_count if requested_count else 0.0
    if valid_count == 0 or (valid_count < requested_count and coverage <= 0.5):
        kind = "insufficient_data"
    elif valid_count == 1:
        kind = "singleton"
    elif valid_count < 4:
        kind = "small"
    else:
        kind = "adequate"
    rank_eligible = valid_count >= 4 and coverage >= 0.75
    return {
        "kind": kind,
        "valid_symbol_count": valid_count,
        "requested_symbol_count": requested_count,
        "coverage": round(coverage, 4),
        "evidence_strength": "standard" if rank_eligible else "low",
        "rank_eligible": rank_eligible,
    }


def evaluate_group_phase(
    current_features: dict[str, Any],
    previous_features: dict[str, Any] | None,
    symbol_setups: dict[str, dict[str, Any]],
    support: dict[str, Any],
) -> dict[str, Any]:
    """Combine current level, marginal impulse and actionable setup evidence."""

    breadth = _nested_number(current_features, "breadth", "above_ma20")
    relative = _nested_number(current_features, "relative_strength", "median_excess_20d")
    concentration = _number(current_features.get("leader_concentration"))
    state, level, stance, base_action, reasons = _level(
        breadth, relative, concentration, support
    )
    impulse = _impulse(current_features, previous_features)
    setup_stage, setup_participation = _setup_summary(symbol_setups, support)

    if level == "strong" and impulse == "deteriorating":
        category, action = "deteriorating", "watch"
        reasons.append("当前仍强，但边际证据恶化。")
    elif (
        level == "strong"
        and impulse in {"improving", "stable", "unavailable"}
        and setup_stage in {"confirmed", "near_confirmation"}
    ):
        category, action = "leader", "prioritize"
    elif (
        level in {"weak", "mixed"}
        and impulse == "improving"
        and setup_stage in {"confirmed", "near_confirmation", "forming"}
    ):
        category, action = "reversal_watch", "watch"
        reasons.append("弱势或分化仍在，但边际改善且出现可执行 setup。")
    elif level == "weak":
        category = "weak"
        action = "watch" if impulse == "improving" else "avoid"
    else:
        category, action = level, base_action

    if not support.get("rank_eligible"):
        if action == "prioritize":
            action = "watch"
        if category == "leader":
            category = "small_sample"
        reasons.append("有效成员少于 4 个或覆盖不足，组级证据降级。")

    return {
        "state": state,
        "level": level,
        "impulse": impulse,
        "phase": state if impulse == "unavailable" else f"{state}_{impulse}",
        "setup": setup_stage,
        "setup_participation": setup_participation,
        "category": category,
        "stance": stance,
        "action": action,
        "reasons": reasons,
        "participation": breadth,
        "leader_concentration": concentration,
        "sample_support": dict(support),
        "evidence_strength": support.get("evidence_strength"),
        "rank_eligible": bool(support.get("rank_eligible")),
    }


def _level(
    breadth: float | None,
    relative: float | None,
    concentration: float | None,
    support: dict[str, Any],
) -> tuple[str, str, str, str, list[str]]:
    if support.get("kind") == "insufficient_data":
        return (
            "insufficient_data",
            "insufficient_data",
            "insufficient_data",
            "no_action",
            ["组内有效样本不足。"],
        )
    if breadth is not None and relative is not None and breadth >= 0.65 and relative > 0:
        return "broad_strength", "strong", "bullish", "prioritize", ["组内多数标的站上 MA20，且中位数相对基准为正。"]
    if breadth is not None and relative is not None and breadth < 0.35 and relative < 0:
        return "broad_weakness", "weak", "bearish", "avoid", ["组内多数标的低于 MA20，且中位数相对基准为负。"]
    if concentration is not None and breadth is not None and concentration >= 0.65 and breadth < 0.55:
        return "narrow_leadership", "mixed", "neutral", "watch", ["强势集中在少数领涨标的，内部参与不足。"]
    return "mixed", "mixed", "neutral", "watch", ["组内强弱与广度尚未形成一致方向。"]


def _impulse(current: dict[str, Any], previous: dict[str, Any] | None) -> str:
    if not previous:
        return "unavailable"
    deltas = (
        _delta(
            _nested_number(current, "returns_percent", "20d", "median"),
            _nested_number(previous, "returns_percent", "20d", "median"),
        ),
        _delta(
            _nested_number(current, "relative_strength", "median_excess_20d"),
            _nested_number(previous, "relative_strength", "median_excess_20d"),
        ),
        _delta(
            _nested_number(current, "breadth", "above_ma20"),
            _nested_number(previous, "breadth", "above_ma20"),
        ),
    )
    improving = sum(value is not None and value > 0 for value in deltas)
    deteriorating = sum(value is not None and value < 0 for value in deltas)
    return "improving" if improving >= 2 else "deteriorating" if deteriorating >= 2 else "stable"


def _setup_summary(
    symbol_setups: dict[str, dict[str, Any]], support: dict[str, Any]
) -> tuple[str, float]:
    stages: list[str] = []
    for projection in symbol_setups.values():
        primary = projection.get("primary_setup") or {}
        stage = str(primary.get("stage") or "")
        if stage in {"confirmed", "near_confirmation", "armed", "watching", "forming", "invalidated"}:
            stages.append(stage)
    priority = ("confirmed", "near_confirmation", "armed", "watching", "forming", "invalidated")
    selected = next((stage for stage in priority if stage in stages), "absent")
    if selected in {"armed"}:
        selected = "near_confirmation"
    elif selected == "watching":
        selected = "forming"
    requested = int(support.get("requested_symbol_count") or 0)
    return selected, round(len(stages) / requested, 4) if requested else 0.0


def _nested_number(value: dict[str, Any], *path: str) -> float | None:
    current: Any = value
    for key in path:
        current = current.get(key) if isinstance(current, dict) else None
    return _number(current)


def _delta(current: float | None, previous: float | None) -> float | None:
    return current - previous if current is not None and previous is not None else None


def _number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value)):
        return float(value)
    return None
