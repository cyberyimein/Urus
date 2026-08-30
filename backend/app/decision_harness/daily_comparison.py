"""Build explicit T-1 to T context for daily-close AI decisions.

The daily evidence chart contains a long history, but a model should not be
expected to infer which two completed sessions form the decision baseline.
These helpers create a small, auditable comparison containing the last bar,
the latest indicator-series values, and prior deterministic strategy state.
"""

from __future__ import annotations

from math import isfinite
from typing import Any


TEMPORAL_CONTEXT_SCHEMA = "urus.daily_temporal_context.v1"
_BAR_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
    "turnover_rate",
)
_SYNTHESIS_FIELDS = (
    "consensus_state",
    "suggested_action",
    "bullish_count",
    "bearish_count",
    "neutral_count",
    "not_applicable_count",
    "error_count",
    "strongest_supporting_strategy_ids",
    "strongest_conflicting_strategy_ids",
)


def previous_trading_date_from_chart(
    chart: dict[str, Any] | None,
    symbol: str,
    current_date: str | None,
) -> str | None:
    """Return the completed bar immediately before the current target bar."""

    bars = _bars(_chart_instrument(chart, symbol))
    if len(bars) < 2:
        return None
    dates = [str(item.get("date") or "") for item in bars]
    if current_date:
        if current_date not in dates:
            return None
        current_index = dates.index(current_date)
    else:
        current_index = len(dates) - 1
    if current_index <= 0:
        return None
    return dates[current_index - 1] or None


def build_instrument_temporal_context(
    *,
    current_dataset: dict[str, Any],
    current_chart: dict[str, Any] | None,
    current_strategy_decisions: list[dict[str, Any]],
    current_synthesis: dict[str, Any],
    previous_dataset: dict[str, Any] | None,
    previous_chart: dict[str, Any] | None,
    previous_strategy_decisions: list[dict[str, Any]],
    previous_synthesis: dict[str, Any],
    symbol: str,
) -> dict[str, Any]:
    """Build a bounded, explicit comparison for one instrument.

    ``previous_dataset`` is preferred when it is the actual previous completed
    session.  When only the current chart history is available, the previous
    bar and indicator values are still exposed, but the context is marked
    ``partial`` because the previous day's deterministic strategy bundle is
    unavailable.
    """

    current_date = str(current_dataset.get("trading_date") or "")
    expected_previous_date = previous_trading_date_from_chart(
        current_chart, symbol, current_date
    )
    previous_dataset_date = str((previous_dataset or {}).get("trading_date") or "")
    dataset_mismatch = bool(
        expected_previous_date
        and previous_dataset_date
        and previous_dataset_date != expected_previous_date
    )
    usable_previous_dataset = None if dataset_mismatch else previous_dataset
    usable_previous_chart = None if dataset_mismatch else previous_chart
    usable_previous_decisions = [] if dataset_mismatch else previous_strategy_decisions
    usable_previous_synthesis = {} if dataset_mismatch else previous_synthesis

    current_snapshot = _day_snapshot(current_chart, symbol, current_date)
    previous_date = expected_previous_date or previous_dataset_date or None
    previous_snapshot = _day_snapshot(
        usable_previous_chart,
        symbol,
        previous_date,
    )
    source = "previous_dataset"
    if previous_snapshot is None and previous_date:
        # The current frozen chart is itself immutable and contains the prior
        # completed bar.  Use it as a raw-data fallback, but do not imply that
        # the prior day's strategy decision was also frozen.
        previous_snapshot = _day_snapshot(current_chart, symbol, previous_date)
        source = "current_dataset_history"

    warnings: list[str] = []
    if (
        usable_previous_dataset is not None
        and expected_previous_date is None
        and previous_dataset_date
    ):
        warnings.append(
            "当前图表没有足够历史来核验上一交易日；历史 dataset 仅作为最近可用基线，已降低完整度。"
        )
    if dataset_mismatch:
        warnings.append(
            "最近可用的历史 dataset 不是当前图表的上一交易日，已不作为 T-1 策略基线。"
        )
    if previous_snapshot is not None and usable_previous_dataset is None:
        warnings.append(
            "上一交易日没有同一 scope 的冻结 dataset；T-1 原始快照回退自当前 dataset 的历史序列。"
        )
    if previous_snapshot is None:
        warnings.append("无法从当前或上一 dataset 解析上一交易日收盘快照。")

    if previous_snapshot is None:
        status = "unavailable"
    elif (
        usable_previous_dataset is not None
        and source == "previous_dataset"
        and expected_previous_date is not None
    ):
        status = "ok"
    else:
        status = "partial"

    previous_payload = usable_previous_dataset or {}
    previous_quality = previous_payload.get("quality") or {}
    if not previous_quality:
        previous_quality = {
            "status": "partial" if previous_snapshot is not None else "missing",
            "warnings": list(warnings),
        }

    current_compact = _compact_dataset(current_dataset)
    previous_compact = (
        _compact_dataset(previous_payload) if usable_previous_dataset is not None else None
    )
    previous_dataset_id = str(previous_compact.get("dataset_id")) if previous_compact else None
    current_dataset_id = str(current_compact.get("dataset_id")) if current_compact else None

    reference_dataset_id = previous_dataset_id or current_dataset_id
    evidence_refs: list[dict[str, Any]] = []
    if previous_dataset_id:
        evidence_refs.append(
            {"kind": "daily_dataset", "dataset_id": previous_dataset_id, "symbol": symbol}
        )
    if previous_snapshot is not None and reference_dataset_id:
        evidence_refs.append(
            {
                "kind": "decision_chart",
                "dataset_id": reference_dataset_id,
                "symbol": symbol,
                "path": (
                    f"instruments[{symbol}].price.bars[-1]"
                    if source == "previous_dataset"
                    else f"instruments[{symbol}].price.bars[-2]"
                ),
            }
        )
    for decision in usable_previous_decisions:
        decision_id = decision.get("decision_id")
        if not decision_id:
            continue
        evidence_refs.append(
            {
                "kind": "strategy_decision",
                "dataset_id": str(decision.get("dataset_id") or previous_dataset_id or ""),
                "decision_id": str(decision_id),
                "symbol": str(
                    (decision.get("scope") or {}).get("symbol") or decision.get("symbol") or symbol
                ),
            }
        )
    if usable_previous_synthesis and previous_dataset_id:
        evidence_refs.append(
            {
                "kind": "deterministic_synthesis",
                "dataset_id": previous_dataset_id,
            }
        )

    return {
        "schema_version": TEMPORAL_CONTEXT_SCHEMA,
        "data_boundary": {
            "mode": "daily_close_only",
            "current_observation": "completed_trading_day_close",
            "previous_observation": "previous_completed_trading_day_close",
            "premarket_available": False,
        },
        "status": status,
        "symbol": symbol,
        "current": {
            "dataset": current_compact,
            "snapshot": current_snapshot,
            "strategy_states": [_decision_state(item) for item in current_strategy_decisions],
            "deterministic_synthesis": _synthesis_state(current_synthesis),
        },
        "previous": {
            "dataset": previous_compact,
            "source": source if previous_snapshot is not None else None,
            "trading_date": previous_date,
            "snapshot": previous_snapshot,
            "strategy_decisions": [dict(item) for item in usable_previous_decisions],
            "deterministic_synthesis": dict(usable_previous_synthesis),
            "quality": previous_quality,
        }
        if previous_snapshot is not None or previous_compact is not None
        else None,
        "changes": {
            "bar": _bar_changes(
                (previous_snapshot or {}).get("bar") if previous_snapshot else None,
                (current_snapshot or {}).get("bar") if current_snapshot else None,
            ),
            "series": _series_changes(
                (previous_snapshot or {}).get("series") if previous_snapshot else None,
                (current_snapshot or {}).get("series") if current_snapshot else None,
            ),
            "strategy_transitions": _strategy_transitions(
                usable_previous_decisions,
                current_strategy_decisions,
            ),
            "synthesis": _synthesis_transition(
                usable_previous_synthesis,
                current_synthesis,
            ),
        },
        "warnings": warnings,
        "evidence_refs": evidence_refs,
    }


def _chart_instrument(chart: dict[str, Any] | None, symbol: str) -> dict[str, Any]:
    if not isinstance(chart, dict):
        return {}
    instruments = chart.get("instruments")
    if isinstance(instruments, dict):
        for key, value in instruments.items():
            if str(key).upper() == symbol.upper() and isinstance(value, dict):
                return value
    if str(chart.get("symbol") or "").upper() == symbol.upper():
        return chart
    return {}


def _bars(instrument: dict[str, Any]) -> list[dict[str, Any]]:
    price = instrument.get("price")
    if isinstance(price, dict) and isinstance(price.get("bars"), list):
        return [item for item in price["bars"] if isinstance(item, dict)]
    value = instrument.get("bars")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _day_snapshot(
    chart: dict[str, Any] | None,
    symbol: str,
    trading_date: str | None,
) -> dict[str, Any] | None:
    instrument = _chart_instrument(chart, symbol)
    if not instrument:
        return None
    bars = _bars(instrument)
    bar = next(
        (item for item in bars if trading_date and str(item.get("date")) == trading_date),
        None,
    )
    if bar is None and trading_date is None and bars:
        bar = bars[-1]
    if bar is None:
        return None
    resolved_date = str(bar.get("date") or trading_date or "")
    series_values: dict[str, Any] = {}
    series = instrument.get("series")
    if not isinstance(series, list):
        technical_series = instrument.get("technical_series")
        series = technical_series.get("series") if isinstance(technical_series, dict) else []
    for item in series if isinstance(series, list) else []:
        if not isinstance(item, dict) or not item.get("series_id"):
            continue
        points = item.get("points")
        if not isinstance(points, list):
            continue
        point = next(
            (
                value
                for value in reversed(points)
                if isinstance(value, dict)
                and str(value.get("time") or "") == resolved_date
            ),
            None,
        )
        if point is not None:
            series_values[str(item["series_id"])] = point.get("value")
    return {
        "symbol": symbol,
        "trading_date": resolved_date,
        "bar": {key: bar.get(key) for key in _BAR_FIELDS if key in bar},
        "series": series_values,
        "indicator_snapshot_id": instrument.get("indicator_snapshot_id"),
        "quality": dict(instrument.get("quality") or {}),
    }


def _compact_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    return {
        key: dataset.get(key)
        for key in (
            "dataset_id",
            "schema_version",
            "feature_version",
            "trading_date",
            "cutoff_time",
            "market_timezone",
            "bar_completion_policy",
            "content_sha256",
            "status",
            "scope",
            "quality",
        )
        if key in dataset
    }


def _number(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    return number if isfinite(number) else None


def _change(previous: Any, current: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"previous": previous, "current": current}
    previous_number = _number(previous)
    current_number = _number(current)
    if previous_number is None or current_number is None:
        return result
    absolute = current_number - previous_number
    result["absolute"] = round(absolute, 6)
    result["percent"] = (
        round(absolute / abs(previous_number) * 100, 6) if previous_number else None
    )
    return result


def _bar_changes(
    previous: dict[str, Any] | None,
    current: dict[str, Any] | None,
) -> dict[str, Any]:
    previous = previous or {}
    current = current or {}
    return {key: _change(previous.get(key), current.get(key)) for key in _BAR_FIELDS}


def _series_changes(
    previous: dict[str, Any] | None,
    current: dict[str, Any] | None,
) -> dict[str, Any]:
    previous = previous or {}
    current = current or {}
    keys = sorted(set(previous) | set(current))
    return {key: _change(previous.get(key), current.get(key)) for key in keys}


def _decision_name(decision: dict[str, Any]) -> str:
    strategy = decision.get("strategy") or {}
    if isinstance(strategy, dict) and strategy.get("name"):
        return str(strategy["name"])
    return str(decision.get("strategy_name") or "unknown")


def _decision_state(decision: dict[str, Any]) -> dict[str, Any]:
    progress = decision.get("setup_progress") or {}
    return {
        "strategy_name": _decision_name(decision),
        "decision_id": decision.get("decision_id"),
        "stance": decision.get("stance"),
        "action": decision.get("action"),
        "score": decision.get("score"),
        "confidence": decision.get("confidence"),
        "setup_stage": progress.get("stage"),
        "confirmation_distance_atr": progress.get("confirmation_distance_atr"),
        "invalidation_distance_atr": progress.get("invalidation_distance_atr"),
        "changed_from_previous_stage": progress.get("changed_from_previous_stage"),
    }


def _strategy_transitions(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    previous_by_name = {_decision_name(item): _decision_state(item) for item in previous}
    current_by_name = {_decision_name(item): _decision_state(item) for item in current}
    transitions: list[dict[str, Any]] = []
    for name in sorted(set(previous_by_name) | set(current_by_name)):
        before = previous_by_name.get(name)
        after = current_by_name.get(name)
        transitions.append(
            {
                "strategy_name": name,
                "previous": before,
                "current": after,
                "changed": before != after,
                "action_changed": (before or {}).get("action") != (after or {}).get("action"),
                "stance_changed": (before or {}).get("stance") != (after or {}).get("stance"),
                "stage_changed": (before or {}).get("setup_stage") != (after or {}).get("setup_stage"),
                "score": _change(
                    (before or {}).get("score"),
                    (after or {}).get("score"),
                ),
            }
        )
    return transitions


def _synthesis_state(synthesis: dict[str, Any]) -> dict[str, Any]:
    return {key: synthesis.get(key) for key in _SYNTHESIS_FIELDS if key in synthesis}


def _synthesis_transition(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    before = _synthesis_state(previous)
    after = _synthesis_state(current)
    return {"previous": before, "current": after, "changed": before != after}
