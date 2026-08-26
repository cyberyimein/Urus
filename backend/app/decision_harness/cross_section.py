from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import median
from typing import Any

from app.core.errors import AppError
from app.decision_harness.contracts import FEATURE_VERSION, content_sha256
from app.decision_harness.strategies import StrategyRegistry
from app.models.observation import GroupDailySnapshotModel, ObservationRunModel
from app.repositories.observation import ObservationRepository


CROSS_SECTION_SCHEMA = "urus.cross_section_projection.v1"
AI_DISABLED = {
    "available": False,
    "status": "disabled",
    "reason": "Phase E 才启用 AI 横向评估；当前页面只展示确定性投影。",
}


INDICATOR_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "rsi14",
        "name": "RSI 14",
        "description": "14 日相对强弱指标；用于识别超卖、平衡和超买状态。",
        "feature_version": FEATURE_VERSION,
        "unit": "index",
        "source_path": "symbols[].rsi14",
        "thresholds": {"oversold": 30, "overbought": 70},
    },
    {
        "id": "macd_histogram",
        "name": "MACD Histogram",
        "description": "MACD 柱体相对零轴的位置；用于观察动能方向。",
        "feature_version": FEATURE_VERSION,
        "unit": "index",
        "source_path": "symbols[].macd_histogram",
        "thresholds": {"zero": 0},
    },
    {
        "id": "relative_strength_20d",
        "name": "Relative Strength 20D",
        "description": "个股相对该观察组基准的 20 日超额收益。",
        "feature_version": FEATURE_VERSION,
        "unit": "percent",
        "source_path": "symbols[].relative_excess_percent.20d",
        "thresholds": {"leading": 0.5, "lagging": -0.5},
    },
    {
        "id": "volume_ratio_20d",
        "name": "Volume Ratio 20D",
        "description": "最新成交量与 20 日成交量均值的比值。",
        "feature_version": FEATURE_VERSION,
        "unit": "ratio",
        "source_path": "symbols[].volume_ratio_20d",
        "thresholds": {"expansion": 1.2, "contraction": 0.8},
    },
    {
        "id": "return_20d",
        "name": "Return 20D",
        "description": "个股最近 20 个交易日的价格变化。",
        "feature_version": FEATURE_VERSION,
        "unit": "percent",
        "source_path": "symbols[].returns_percent.20",
        "thresholds": {"positive": 0, "negative": 0},
    },
    {
        "id": "above_ma20",
        "name": "Above MA20",
        "description": "收盘价是否站上 20 日移动平均线。",
        "feature_version": FEATURE_VERSION,
        "unit": "boolean",
        "source_path": "symbols[].trend.above_ma20",
        "thresholds": {},
    },
    {
        "id": "above_ma50",
        "name": "Above MA50",
        "description": "收盘价是否站上 50 日移动平均线。",
        "feature_version": FEATURE_VERSION,
        "unit": "boolean",
        "source_path": "symbols[].trend.above_ma50",
        "thresholds": {},
    },
    {
        "id": "above_ma200",
        "name": "Above MA200",
        "description": "收盘价是否站上 200 日移动平均线。",
        "feature_version": FEATURE_VERSION,
        "unit": "boolean",
        "source_path": "symbols[].trend.above_ma200",
        "thresholds": {},
    },
)


@dataclass(frozen=True)
class _SnapshotContext:
    run_item: dict[str, Any]
    snapshot: GroupDailySnapshotModel
    payload: dict[str, Any]
    previous_snapshot: GroupDailySnapshotModel | None
    previous_payload: dict[str, Any] | None


class CrossSectionService:
    """Build read-only indicator/strategy views from one frozen Observation Run.

    The service deliberately consumes ``GroupDailySnapshotModel.payload_json``
    rather than recalculating indicators or strategies.  This keeps a scan page
    tied to the exact group versions and datasets that the run froze.
    """

    def __init__(self, session) -> None:
        self.repository = ObservationRepository(session)

    @staticmethod
    def indicator_catalog() -> list[dict[str, Any]]:
        return [_catalog_item(item) for item in INDICATOR_DEFINITIONS]

    @staticmethod
    def strategy_catalog() -> list[dict[str, Any]]:
        registry = StrategyRegistry()
        return [
            _catalog_item(
                {
                    "id": adapter.name,
                    "name": adapter.name,
                    "description": "确定性策略输出的横向比较视图。",
                    "version": adapter.version,
                    "feature_version": FEATURE_VERSION,
                    "implementation_sha256": adapter.implementation_sha256,
                    "unit": "decision",
                    "source_path": "strategy_decisions[]",
                },
                kind="strategy",
            )
            for adapter in registry.adapters
        ]

    def indicator_projection(self, run_id: str, indicator_id: str) -> dict[str, Any]:
        definition = _find_catalog_item(self.indicator_catalog(), indicator_id, "指标")
        run, contexts, failed_groups = self._load_contexts(run_id)
        _ensure_feature_version(contexts)
        if indicator_id == "relative_strength_20d":
            _ensure_benchmark_identity(contexts)
        rows: list[dict[str, Any]] = []
        groups: list[dict[str, Any]] = []
        transitions: list[dict[str, Any]] = []

        for context in contexts:
            group = _group_identity(context.snapshot, context.payload)
            previous_rows = _symbol_map(context.previous_payload)
            group_rows: list[dict[str, Any]] = []
            for symbol_row in context.payload.get("symbols") or []:
                current_value = _indicator_value(definition["id"], symbol_row)
                previous_value = _indicator_value(definition["id"], previous_rows.get(str(symbol_row.get("symbol"))))
                row = _indicator_row(
                    definition,
                    group,
                    context,
                    symbol_row,
                    current_value,
                    previous_value,
                )
                group_rows.append(row)
                rows.append(row)
                if row["transition"] is not None:
                    transitions.append(_transition_row(row))
            groups.append(_group_summary(group, context, group_rows, value_key="value"))

        rows.sort(key=_row_sort_key)
        transitions.sort(key=_transition_sort_key)
        payload = _base_projection(run, contexts, failed_groups, lens={
            "type": "indicator",
            "id": definition["id"],
            "version": definition["feature_version"],
            "feature_version": definition["feature_version"],
        })
        payload.update(
            {
                "indicator": definition,
                "groups": groups,
                "rows": rows,
                "transitions": transitions,
                "quality": _projection_quality(run, contexts, failed_groups, rows),
            }
        )
        return _with_digest(payload)

    def strategy_projection(self, run_id: str, strategy_id: str) -> dict[str, Any]:
        definition = _find_catalog_item(self.strategy_catalog(), strategy_id, "策略")
        run, contexts, failed_groups = self._load_contexts(run_id)
        _ensure_feature_version(contexts)
        rows: list[dict[str, Any]] = []
        groups: list[dict[str, Any]] = []
        transitions: list[dict[str, Any]] = []

        for context in contexts:
            group = _group_identity(context.snapshot, context.payload)
            current_decisions = _strategy_map(context.payload, strategy_id)
            _ensure_strategy_identity(definition, current_decisions, group["group_id"])
            previous_decisions = _strategy_map(context.previous_payload, strategy_id)
            _ensure_strategy_identity(definition, previous_decisions, group["group_id"])
            group_rows: list[dict[str, Any]] = []
            for symbol_row in context.payload.get("symbols") or []:
                symbol = str(symbol_row.get("symbol") or "")
                decision = current_decisions.get(symbol)
                previous_decision = previous_decisions.get(symbol)
                row = _strategy_row(
                    definition,
                    group,
                    context,
                    symbol_row,
                    decision,
                    previous_decision,
                )
                group_rows.append(row)
                rows.append(row)
                if row["transition"] is not None:
                    transitions.append(_transition_row(row))
            groups.append(_group_summary(group, context, group_rows, value_key="score"))

        rows.sort(key=_row_sort_key)
        transitions.sort(key=_transition_sort_key)
        payload = _base_projection(run, contexts, failed_groups, lens={
            "type": "strategy",
            "id": definition["id"],
            "version": definition.get("version"),
            "feature_version": definition.get("feature_version"),
            "implementation_sha256": definition.get("implementation_sha256"),
        })
        payload.update(
            {
                "strategy": definition,
                "groups": groups,
                "rows": rows,
                "transitions": transitions,
                "quality": _projection_quality(run, contexts, failed_groups, rows),
            }
        )
        return _with_digest(payload)

    def _load_contexts(
        self, run_id: str
    ) -> tuple[ObservationRunModel, list[_SnapshotContext], list[dict[str, Any]]]:
        run = self.repository.get_run(run_id)
        if run is None:
            raise AppError("找不到 Observation Run", code="observation_run_not_found", status_code=404)
        if run.status not in {"succeeded", "mixed"}:
            raise AppError(
                "该 Observation Run 尚未生成可用的冻结快照。",
                code="observation_run_not_ready",
                status_code=409,
                details={"run_id": run.id, "status": run.status},
            )

        contexts: list[_SnapshotContext] = []
        failed_groups: list[dict[str, Any]] = []
        for item in list((run.payload_json or {}).get("group_snapshots") or []):
            if item.get("status") != "succeeded" or not item.get("snapshot_id"):
                failed_groups.append(
                    {
                        "group_id": item.get("group_id"),
                        "group_version_id": item.get("group_version_id"),
                        "status": item.get("status", "failed"),
                        "error_message": item.get("error_message"),
                    }
                )
                continue
            snapshot = self.repository.get_snapshot(str(item["snapshot_id"]))
            if snapshot is None:
                failed_groups.append(
                    {
                        "group_id": item.get("group_id"),
                        "group_version_id": item.get("group_version_id"),
                        "status": "missing_snapshot",
                        "snapshot_id": item.get("snapshot_id"),
                    }
                )
                continue
            previous = self.repository.previous_snapshot(
                group_id=snapshot.group_id,
                group_version_id=snapshot.group_version_id,
                trading_date=snapshot.trading_date,
            )
            payload = dict(snapshot.payload_json or {})
            previous_payload = dict(previous.payload_json or {}) if previous else None
            # A feature implementation change starts a new comparison chain.
            # Do not compare a v3 snapshot with an older payload that did not
            # use the same indicator calculation contract.
            if previous_payload and previous_payload.get("feature_version") != payload.get("feature_version"):
                previous = None
                previous_payload = None
            contexts.append(
                _SnapshotContext(
                    run_item=dict(item),
                    snapshot=snapshot,
                    payload=payload,
                    previous_snapshot=previous,
                    previous_payload=previous_payload,
                )
            )
        if not contexts:
            raise AppError(
                "该 Observation Run 没有可投影的成功组快照。",
                code="cross_section_snapshot_unavailable",
                status_code=409,
                details={"run_id": run.id, "failed_groups": failed_groups},
            )
        return run, contexts, failed_groups


def _catalog_item(item: dict[str, Any], *, kind: str = "indicator") -> dict[str, Any]:
    result = {
        "id": item["id"],
        "name": item.get("name", item["id"]),
        "kind": kind,
        "description": item.get("description", ""),
        "version": item.get("version") or item.get("feature_version"),
        "feature_version": item.get("feature_version"),
        "unit": item.get("unit"),
        "source_path": item.get("source_path"),
        "thresholds": dict(item.get("thresholds") or {}),
    }
    for key in ("implementation_sha256",):
        if item.get(key):
            result[key] = item[key]
    result["content_sha256"] = content_sha256(result)
    return result


def _find_catalog_item(catalog: list[dict[str, Any]], item_id: str, label: str) -> dict[str, Any]:
    result = next((item for item in catalog if item["id"] == item_id), None)
    if result is None:
        raise AppError(
            f"找不到{label}横向扫描项：{item_id}",
            code="cross_section_lens_not_found",
            status_code=404,
            details={"id": item_id},
        )
    return result


def _group_identity(snapshot: GroupDailySnapshotModel, payload: dict[str, Any]) -> dict[str, Any]:
    group = dict(payload.get("group") or {})
    display_name = str(group.get("display_name") or group.get("group_id") or snapshot.group_id)
    return {
        "group_id": str(group.get("group_id") or snapshot.group_id),
        "group_version_id": str(group.get("version_id") or snapshot.group_version_id),
        "group_version": group.get("version", snapshot.group_version),
        "display_name": display_name,
        "group_name": display_name,
        "benchmark_symbols": list(group.get("benchmark_symbols") or []),
    }


def _symbol_map(payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("symbol")): dict(item)
        for item in list((payload or {}).get("symbols") or [])
        if item.get("symbol")
    }


def _indicator_value(indicator_id: str, row: dict[str, Any] | None) -> float | None:
    if not row or not row.get("valid"):
        return None
    if indicator_id == "rsi14":
        return _number(row.get("rsi14"))
    if indicator_id == "macd_histogram":
        return _number(row.get("macd_histogram"))
    if indicator_id == "relative_strength_20d":
        return _number((row.get("relative_excess_percent") or {}).get("20d"))
    if indicator_id == "volume_ratio_20d":
        return _number(row.get("volume_ratio_20d"))
    if indicator_id == "return_20d":
        return _number((row.get("returns_percent") or {}).get("20"))
    if indicator_id == "above_ma20":
        return _boolean_number((row.get("trend") or {}).get("above_ma20"))
    if indicator_id == "above_ma50":
        return _boolean_number((row.get("trend") or {}).get("above_ma50"))
    if indicator_id == "above_ma200":
        return _boolean_number((row.get("trend") or {}).get("above_ma200"))
    return None


def _indicator_row(
    definition: dict[str, Any],
    group: dict[str, Any],
    context: _SnapshotContext,
    symbol_row: dict[str, Any],
    value: float | None,
    previous_value: float | None,
) -> dict[str, Any]:
    indicator_id = str(definition["id"])
    valid = bool(symbol_row.get("valid")) and value is not None
    state = _indicator_state(indicator_id, value) if valid else "missing"
    previous_state = _indicator_state(indicator_id, previous_value) if previous_value is not None else "missing"
    transition = _state_transition(previous_state, state)
    symbol = str(symbol_row.get("symbol") or "")
    row = {
        "id": f"{group['group_id']}:{symbol}:{indicator_id}",
        "group_id": group["group_id"],
        "group_name": group["display_name"],
        "group_version_id": group["group_version_id"],
        "snapshot_id": context.snapshot.id,
        "dataset_id": context.snapshot.dataset_id,
        **_comparison_fields(context),
        "symbol": symbol,
        "valid": valid,
        "status": "ok" if valid else "missing",
        "quality_status": symbol_row.get("quality_status", "missing"),
        "value": value,
        "previous_value": previous_value,
        "change": _delta(value, previous_value),
        "display_value": _display_indicator_value(indicator_id, value),
        "previous_display_value": _display_indicator_value(indicator_id, previous_value),
        "state": state,
        "state_label": _state_label(indicator_id, state),
        "previous_state": previous_state,
        "previous_state_label": _state_label(indicator_id, previous_state),
        "threshold_distance": _threshold_distance(indicator_id, value),
        "unit": definition.get("unit"),
        "thresholds": dict(definition.get("thresholds") or {}),
        "benchmark_symbols": group["benchmark_symbols"],
        "transition": transition,
        "transition_date": context.snapshot.trading_date.isoformat() if transition else None,
        "evidence_refs": [
            {
                "kind": "group_snapshot",
                "snapshot_id": context.snapshot.id,
                "dataset_id": context.snapshot.dataset_id,
                "symbol": symbol,
                "path": definition.get("source_path"),
            }
        ],
        "warnings": list(symbol_row.get("warnings") or []),
    }
    return row


def _strategy_map(payload: dict[str, Any] | None, strategy_id: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in list((payload or {}).get("strategy_decisions") or []):
        strategy = item.get("strategy") or {}
        if strategy.get("name") != strategy_id:
            continue
        scope = item.get("scope") or {}
        symbol = str(scope.get("symbol") or item.get("symbol") or "")
        if symbol:
            result[symbol] = dict(item)
    return result


def _ensure_strategy_identity(
    definition: dict[str, Any], decisions: dict[str, dict[str, Any]], group_id: str
) -> None:
    """Never present a historical decision under a different implementation."""

    if not decisions:
        return
    expected = (
        str(definition["id"]),
        str(definition.get("version") or ""),
        str(definition.get("implementation_sha256") or ""),
    )
    observed = {
        (
            str((item.get("strategy") or {}).get("name") or ""),
            str((item.get("strategy") or {}).get("version") or ""),
            str((item.get("strategy") or {}).get("implementation_sha256") or ""),
        )
        for item in decisions.values()
    }
    if observed != {expected}:
        raise AppError(
            "冻结 Run 中的策略实现版本与当前策略目录不一致，不能静默混合。",
            code="cross_section_strategy_version_conflict",
            status_code=409,
            details={"strategy_id": definition["id"], "group_id": group_id, "expected": expected, "observed": sorted(observed)},
        )


def _strategy_row(
    definition: dict[str, Any],
    group: dict[str, Any],
    context: _SnapshotContext,
    symbol_row: dict[str, Any],
    decision: dict[str, Any] | None,
    previous_decision: dict[str, Any] | None,
) -> dict[str, Any]:
    symbol = str(symbol_row.get("symbol") or "")
    current = decision or {}
    previous = previous_decision or {}
    progress = dict(current.get("setup_progress") or {})
    previous_progress = dict(previous.get("setup_progress") or {})
    stage = str(progress.get("stage") or "missing") if decision else "missing"
    previous_stage = str(previous_progress.get("stage") or "missing") if previous_decision else None
    previous_stance = str(previous.get("stance")) if previous_decision and previous.get("stance") else None
    previous_action = str(previous.get("action")) if previous_decision and previous.get("action") else None
    stance = str(current.get("stance") or "insufficient_data") if decision else "insufficient_data"
    action = str(current.get("action") or "no_action") if decision else "no_action"
    status = str(current.get("status") or "missing") if decision else "missing"
    score = _number(current.get("score")) if decision else None
    previous_score = _number(previous.get("score")) if previous_decision else None
    valid = bool(symbol_row.get("valid")) and decision is not None and status not in {"error", "not_applicable"}
    if not valid:
        stage = "insufficient_data"
        stance = "insufficient_data"
        action = "no_action"
        score = None
        previous_score = None
    transition = _state_transition(previous_stage, stage) if valid else None
    evidence_refs = list(current.get("evidence_refs") or []) if decision else []
    evidence_refs.append(
        {
            "kind": "group_snapshot",
            "snapshot_id": context.snapshot.id,
            "dataset_id": context.snapshot.dataset_id,
            "symbol": symbol,
            "path": "strategy_decisions[]",
        }
    )
    return {
        "id": f"{group['group_id']}:{symbol}:{definition['id']}",
        "group_id": group["group_id"],
        "group_name": group["display_name"],
        "group_version_id": group["group_version_id"],
        "snapshot_id": context.snapshot.id,
        "dataset_id": context.snapshot.dataset_id,
        **_comparison_fields(context),
        "symbol": symbol,
        "valid": valid,
        "status": status,
        "quality_status": symbol_row.get("quality_status", "missing"),
        "value": score,
        "previous_value": previous_score,
        "change": _delta(score, previous_score),
        "display_value": action,
        "previous_display_value": previous_action,
        "state": stage,
        "state_label": stage,
        "previous_state": previous_stage,
        "previous_state_label": _state_label("strategy", previous_stage or "missing"),
        "stance": stance,
        "action": action,
        "previous_stance": previous_stance,
        "previous_action": previous_action,
        "score": score,
        "strategy_version": (current.get("strategy") or {}).get("version") or definition.get("version"),
        "implementation_sha256": (current.get("strategy") or {}).get("implementation_sha256") or definition.get("implementation_sha256"),
        "decision_id": current.get("decision_id"),
        "setup_progress": progress,
        "horizon": current.get("horizon"),
        "confirmation_conditions": list(current.get("confirmation_conditions") or []),
        "invalidation_conditions": list(current.get("invalidation_conditions") or []),
        "decision_quality": dict(current.get("quality") or {}),
        "reasons": list(current.get("reasons") or []),
        "transition": transition,
        "transition_date": context.snapshot.trading_date.isoformat() if transition else None,
        "evidence_refs": evidence_refs,
        "warnings": list(symbol_row.get("warnings") or []),
    }


def _indicator_state(indicator_id: str, value: float | None) -> str:
    if value is None:
        return "missing"
    if indicator_id == "rsi14":
        if value < 30:
            return "oversold"
        if value > 70:
            return "overbought"
        return "balanced"
    if indicator_id == "macd_histogram":
        if value > 0:
            return "positive"
        if value < 0:
            return "negative"
        return "zero"
    if indicator_id == "relative_strength_20d":
        if value > 0.5:
            return "leading"
        if value < -0.5:
            return "lagging"
        return "inline"
    if indicator_id == "volume_ratio_20d":
        if value >= 1.2:
            return "expanding"
        if value <= 0.8:
            return "contracting"
        return "normal"
    if indicator_id == "return_20d":
        if value > 0:
            return "positive"
        if value < 0:
            return "negative"
        return "flat"
    if indicator_id.startswith("above_ma"):
        return "above" if value >= 1 else "below"
    return "unknown"


def _state_label(indicator_id: str, state: str) -> str:
    labels = {
        "oversold": "超卖",
        "overbought": "超买",
        "balanced": "平衡",
        "positive": "正值",
        "negative": "负值",
        "zero": "零轴",
        "leading": "相对领先",
        "lagging": "相对落后",
        "inline": "接近基准",
        "expanding": "放量",
        "contracting": "缩量",
        "normal": "正常",
        "flat": "持平",
        "above": "线上",
        "below": "线下",
        "missing": "缺失",
        "forming": "形成中",
        "near_confirmation": "接近确认",
        "watching": "观察中",
        "armed": "待确认",
        "confirmed": "已确认",
        "invalidated": "已失效",
        "no_setup": "无形态",
        "ineligible": "不适用",
        "insufficient_data": "数据不足",
        "bullish": "偏多",
        "bearish": "偏空",
        "neutral": "中性",
    }
    return labels.get(state, state)


def _state_transition(previous: str | None, current: str) -> dict[str, Any] | None:
    if previous is None or previous == "missing" or current == "missing" or previous == current:
        return None
    return {"type": "state_changed", "from": previous, "to": current}


def _threshold_distance(indicator_id: str, value: float | None) -> float | None:
    if value is None:
        return None
    thresholds: dict[str, tuple[float, ...]] = {
        "rsi14": (30.0, 50.0, 70.0),
        "macd_histogram": (0.0,),
        "relative_strength_20d": (0.0,),
        "volume_ratio_20d": (0.8, 1.2),
        "return_20d": (0.0,),
    }
    candidates = thresholds.get(indicator_id)
    if not candidates:
        return None
    return _round(min(abs(value - threshold) for threshold in candidates))


def _transition_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"{row['id']}:transition",
        "group_id": row["group_id"],
        "group_name": row["group_name"],
        "symbol": row["symbol"],
        "state": row["state"],
        "state_label": row["state_label"],
        "value": row.get("value"),
        "change": row.get("change"),
        "transition": row["transition"],
        "snapshot_id": row["snapshot_id"],
        "dataset_id": row["dataset_id"],
        "previous_trading_date": row.get("previous_trading_date"),
    }


def _group_summary(
    group: dict[str, Any],
    context: _SnapshotContext,
    rows: list[dict[str, Any]],
    *,
    value_key: str,
) -> dict[str, Any]:
    values = [_number(row.get(value_key)) for row in rows]
    numeric_values = [value for value in values if value is not None]
    previous_values = [_number(row.get("previous_value")) for row in rows]
    previous_numeric_values = [value for value in previous_values if value is not None]
    state_counts: dict[str, int] = {}
    stance_counts: dict[str, int] = {}
    for row in rows:
        state = str(row.get("state") or "missing")
        state_counts[state] = state_counts.get(state, 0) + 1
        stance = row.get("stance")
        if stance:
            stance_key = str(stance)
            stance_counts[stance_key] = stance_counts.get(stance_key, 0) + 1
    quality = dict(context.payload.get("quality") or {})
    valid_count = sum(bool(row.get("valid")) for row in rows)
    distribution = _distribution(numeric_values)
    previous_distribution = _distribution(previous_numeric_values)
    return {
        **group,
        "snapshot_id": context.snapshot.id,
        "dataset_id": context.snapshot.dataset_id,
        **_comparison_fields(context),
        "trading_date": context.snapshot.trading_date.isoformat(),
        "symbol_count": len(rows),
        "valid_symbol_count": valid_count,
        "missing_symbol_count": max(0, len(rows) - valid_count),
        "quality_status": "ok" if valid_count == len(rows) and rows else "partial" if valid_count else "missing",
        "state_counts": state_counts,
        "stance_counts": stance_counts,
        "distribution": distribution,
        "previous_distribution": previous_distribution,
        "distribution_median_change": _delta(distribution.get("median"), previous_distribution.get("median")),
        "previous_valid_symbol_count": len(previous_numeric_values),
        "previous_symbol_count": len(_symbol_map(context.previous_payload)),
        "warnings": list(quality.get("warnings") or []),
    }


def _base_projection(
    run: ObservationRunModel,
    contexts: list[_SnapshotContext],
    failed_groups: list[dict[str, Any]],
    *,
    lens: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": CROSS_SECTION_SCHEMA,
        "scope_type": "observation_run",
        "scope_id": run.id,
        "observation_run_id": run.id,
        "trading_date": run.trading_date.isoformat(),
        "cutoff_time": run.cutoff_time.isoformat(),
        "comparison": _comparison_summary(run, contexts),
        "lens": lens,
        "group_version_ids": list(run.group_version_ids or []),
        "failed_groups": failed_groups,
        "ai": dict(AI_DISABLED),
    }


def _comparison_fields(context: _SnapshotContext) -> dict[str, Any]:
    previous = context.previous_snapshot
    return {
        "previous_trading_date": previous.trading_date.isoformat() if previous else None,
        "previous_snapshot_id": previous.id if previous else None,
        "previous_dataset_id": previous.dataset_id if previous else None,
    }


def _comparison_summary(
    run: ObservationRunModel,
    contexts: list[_SnapshotContext],
) -> dict[str, Any]:
    previous_contexts = [context for context in contexts if context.previous_snapshot is not None]
    previous_dates = sorted({
        context.previous_snapshot.trading_date.isoformat()
        for context in previous_contexts
        if context.previous_snapshot is not None
    })
    if not previous_contexts:
        status = "unavailable"
    elif len(previous_contexts) == len(contexts) and len(previous_dates) == 1:
        status = "ok"
    else:
        status = "partial"
    return {
        "mode": "previous_trading_session",
        "status": status,
        "current_trading_date": run.trading_date.isoformat(),
        "previous_trading_date": previous_dates[0] if len(previous_dates) == 1 else None,
        "previous_trading_dates": previous_dates,
        "available_group_count": len(previous_contexts),
        "group_count": len(contexts),
        "previous_snapshot_ids": [context.previous_snapshot.id for context in previous_contexts if context.previous_snapshot],
        "previous_dataset_ids": [context.previous_snapshot.dataset_id for context in previous_contexts if context.previous_snapshot],
    }


def _ensure_feature_version(contexts: list[_SnapshotContext]) -> None:
    observed = {
        str(version)
        for context in contexts
        for payload in (context.payload, context.previous_payload)
        if payload and (version := payload.get("feature_version"))
    }
    if observed != {FEATURE_VERSION}:
        raise AppError(
            "冻结快照的指标计算版本与当前横向投影实现不一致。",
            code="cross_section_feature_version_conflict",
            status_code=409,
            details={"expected": FEATURE_VERSION, "observed": sorted(observed)},
        )


def _ensure_benchmark_identity(contexts: list[_SnapshotContext]) -> None:
    observed = {
        str(benchmark)
        for context in contexts
        if (
            benchmark := (
                (context.payload.get("features") or {}).get("relative_strength") or {}
            ).get("benchmark")
        )
    }
    if len(observed) > 1:
        raise AppError(
            "冻结 Run 中包含不同的相对强弱 benchmark，不能静默混排。",
            code="cross_section_benchmark_conflict",
            status_code=409,
            details={"observed": sorted(observed)},
        )


def _projection_quality(
    run: ObservationRunModel,
    contexts: list[_SnapshotContext],
    failed_groups: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    missing_rows = [row for row in rows if row.get("status") == "missing" or not row.get("valid")]
    failed_count = len(failed_groups)
    if failed_count or missing_rows:
        status = "partial"
    else:
        status = "ok"
    return {
        "status": status,
        "run_status": run.status,
        "requested_group_count": len(run.group_ids or []),
        "projected_group_count": len(contexts),
        "failed_group_count": failed_count,
        "projected_row_count": len(rows),
        "valid_row_count": len(rows) - len(missing_rows),
        "missing_row_count": len(missing_rows),
        "snapshot_ids": [context.snapshot.id for context in contexts],
        "dataset_ids": [context.snapshot.dataset_id for context in contexts],
        "warnings": [
            f"{item.get('group_id')}: {item.get('error_message') or item.get('status')}"
            for item in failed_groups
        ],
    }


def _distribution(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "median": None, "q1": None, "q3": None, "min": None, "max": None}
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "median": _round(median(ordered)),
        "q1": _round(_percentile(ordered, 0.25)),
        "q3": _round(_percentile(ordered, 0.75)),
        "min": _round(ordered[0]),
        "max": _round(ordered[-1]),
    }


def _percentile(values: list[float], fraction: float) -> float:
    if len(values) == 1:
        return values[0]
    index = (len(values) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    weight = index - lower
    return values[lower] * (1 - weight) + values[upper] * weight


def _display_indicator_value(indicator_id: str, value: float | None) -> str:
    if value is None:
        return "—"
    if indicator_id.startswith("above_ma"):
        return "线上" if value >= 1 else "线下"
    if indicator_id == "volume_ratio_20d":
        return f"{value:.2f}x"
    return f"{value:+.2f}%" if indicator_id in {"relative_strength_20d", "return_20d"} else f"{value:.2f}"


def _row_sort_key(row: dict[str, Any]) -> tuple[int, float, str, str]:
    """Put actionable state changes and threshold proximity before alphabetic order."""

    state = str(row.get("state") or "")
    transition = dict(row.get("transition") or {})
    changed_to = str(transition.get("to") or "")
    progress = dict(row.get("setup_progress") or {})
    distance = _number(progress.get("confirmation_distance_atr"))
    if changed_to == "confirmed":
        priority = 0
    elif changed_to == "invalidated":
        priority = 1
    elif state in {"armed", "watching"} and distance is not None and distance <= 1:
        priority = 2
    elif transition:
        priority = 3
    elif state == "invalidated":
        priority = 4
    elif not row.get("valid"):
        priority = 8
    else:
        priority = 5
    proximity = (
        abs(distance)
        if distance is not None
        else abs(_number(row.get("threshold_distance")) or 9999)
    )
    return (priority, proximity, str(row.get("group_name")), str(row.get("symbol")))


def _transition_sort_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("group_name")), str(row.get("symbol")))


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)) and isfinite(float(value)):
        return float(value)
    return None


def _boolean_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)) and value in {0, 1}:
        return float(value)
    return None


def _delta(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    return _round(current - previous)


def _round(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _with_digest(payload: dict[str, Any]) -> dict[str, Any]:
    payload["content_sha256"] = content_sha256(
        {key: value for key, value in payload.items() if key != "content_sha256"}
    )
    return payload
