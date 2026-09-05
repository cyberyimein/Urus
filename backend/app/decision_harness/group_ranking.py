from __future__ import annotations

from math import isfinite
from typing import Any, Iterable


MINIMUM_ELIGIBLE_GROUPS = 5
FACTOR_WEIGHTS = {
    "relative_efficiency": 0.45,
    "breadth_ma20": 0.25,
    "breadth_ma50": 0.20,
    "diversification": 0.10,
}


def rank_observation_groups(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cross-sectionally rank eligible groups without imputing missing factors."""

    unique: dict[str, dict[str, Any]] = {}
    for item in rows:
        group_id = str(item.get("group_id") or "")
        if group_id and group_id not in unique:
            unique[group_id] = dict(item)
    eligible = [item for item in unique.values() if item.get("rank_eligible") is True]
    if len(eligible) < MINIMUM_ELIGIBLE_GROUPS:
        return []
    eligible.sort(key=lambda item: str(item.get("group_id")))

    prepared: list[dict[str, Any]] = []
    for item in eligible:
        relative = _number(item.get("relative_20d"))
        expected_move = _number(item.get("expected_tracking_move_20d"))
        efficiency = (
            relative / max(expected_move, 0.5)
            if relative is not None and expected_move is not None
            else None
        )
        prepared.append({**item, "relative_efficiency": _rounded(efficiency)})

    factor_scores = {
        factor: _midrank_standardized([_number(item.get(factor)) for item in prepared])
        for factor in FACTOR_WEIGHTS
    }
    result: list[dict[str, Any]] = []
    for index, item in enumerate(prepared):
        contributions = [
            (FACTOR_WEIGHTS[factor], factor_scores[factor][index])
            for factor in FACTOR_WEIGHTS
            if factor_scores[factor][index] is not None
        ]
        total_weight = sum(weight for weight, _ in contributions)
        composite = (
            sum(weight * float(value) for weight, value in contributions) / total_weight
            if total_weight
            else None
        )
        factors = {
            factor: {
                "raw": _number(item.get(factor)),
                "cross_sectional": factor_scores[factor][index],
                "weight": weight,
            }
            for factor, weight in FACTOR_WEIGHTS.items()
        }
        result.append(
            {
                **item,
                "ranking_status": "available" if composite is not None else "factor_unavailable",
                "ranking_model": "cross_sectional_risk_adjusted_v1",
                "factors": factors,
                "rank_normalized": _rounded(composite),
                "technical_rank_score": _rounded(composite),
            }
        )
    return result


def _midrank_standardized(values: list[float | None]) -> list[float | None]:
    valid = [(index, value) for index, value in enumerate(values) if value is not None]
    result: list[float | None] = [None] * len(values)
    if len(valid) < MINIMUM_ELIGIBLE_GROUPS:
        return result
    ordered = sorted(valid, key=lambda item: (float(item[1]), item[0]))
    position = 0
    while position < len(ordered):
        end = position + 1
        while end < len(ordered) and ordered[end][1] == ordered[position][1]:
            end += 1
        midrank = ((position + 1) + end) / 2
        standardized = -1.0 if len(ordered) == 1 else 2 * (midrank - 1) / (len(ordered) - 1) - 1
        for original_index, _ in ordered[position:end]:
            result[original_index] = _rounded(standardized)
        position = end
    return result


def _number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value)):
        return float(value)
    return None


def _rounded(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None
