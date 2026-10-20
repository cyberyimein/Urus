from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
import hashlib
import json
import math
from statistics import fmean
from typing import Any
from zoneinfo import ZoneInfo

from app.intraday.equities import _continuous_suffix, _wilder_atr_series
from app.intraday.structure import (
    detect_opening_range_breakout,
    detect_range_breakout,
    detect_reversal_candidate,
    previous_atr_and_buffer,
    setup_exhaustion_streak,
)


_STATE_SCHEMA = "urus.intraday.setup-state.v1"
_DEFAULT_RULE_VERSION = "urus.intraday.events.v1"
_DEFAULT_TIMEFRAME = "5m"
_FIVE_MINUTES = timedelta(minutes=5)


def evaluate(
    projection: Mapping[str, Any],
    prior_state: Mapping[str, Any] | None,
    spec: Mapping[str, Any] | None,
    *,
    evaluated_at: datetime,
) -> dict[str, Any]:
    """Evaluate P4 rules for one immutable P3 projection and prior state.

    This function is deliberately independent of persistence, provider access,
    and the system clock.  The caller supplies the evaluation time explicitly.
    """

    evaluated_at_utc = _as_utc(evaluated_at)
    rule_spec = _normalized_spec(spec)
    source = _mapping(projection.get("source"))
    task_name = _text(source.get("task_name"), default="")
    capture_id = _text(source.get("capture_id"), default="")
    content_hash = _text(source.get("content_sha256"), default="")
    scheduled_at = _parse_time(source.get("scheduled_at"))
    trading_date = _text(source.get("trading_date"), default="")
    if not trading_date:
        trading_date = _text(projection.get("trading_date"), default="")

    input_state = _copy_state(prior_state)
    state_setups = input_state["setups"]
    history = _history_from_state(input_state)
    current_cutoff = scheduled_at or _projection_cutoff(projection)
    symbols = _mapping(projection.get("symbols"))
    capabilities: dict[str, dict[str, Any]] = {}
    transitions: list[dict[str, Any]] = []
    history_eligible_symbols: set[str] = set()
    unavailable_setup_scopes: set[tuple[str, str]] = set()

    boundary_verified = _minute_boundary_verified(projection)
    if not boundary_verified:
        reason = "minute_timestamp_boundary_unverified"
        message = "分钟线时间边界未经验证，不能生成正式盘中事件。"
        for symbol in symbols:
            for strategy in (
                "s1_relative_strength",
                "s2_ma5",
                "s3_impulse",
                "s4_orb",
                "s5_range_breakout",
                "s6_reversal",
                "s7_failed_breakout",
                "s8_simplified_setup",
            ):
                capability = _capability(
                    False,
                    reason,
                    message,
                    symbol=str(symbol),
                    strategy=strategy,
                )
                capability["task_name"] = task_name
                capabilities[f"{task_name}:{symbol}:{capability['strategy_id']}"] = capability
        expiry_context = {
            "task_name": task_name,
            "capture_id": capture_id,
            "content_sha256": content_hash,
            "scheduled_at": current_cutoff,
            "trading_date": trading_date,
            "evaluated_at": evaluated_at_utc,
        }
        _expire_gap_candidates(
            state_setups,
            transitions,
            evaluated_at=evaluated_at_utc,
            source_context=expiry_context,
            force_gap=True,
        )
        _expire_timed_setups(
            state_setups,
            transitions,
            evaluated_at=evaluated_at_utc,
            source_context=expiry_context,
            spec=rule_spec,
        )
        _prune_terminal_setups(
            state_setups,
            evaluated_at=evaluated_at_utc,
            cooldown_minutes=rule_spec["cooldown_minutes"],
        )
        return _result(input_state, history, transitions, capabilities)

    for symbol, raw_item in symbols.items():
        item = _mapping(raw_item)
        normalized_symbol = str(symbol).strip().upper()
        features = _mapping(item.get("features"))
        item_bars = _valid_bars(item.get("bars"), cutoff=current_cutoff)
        item_as_of = _parse_time(item.get("as_of"))
        item_status = _text(item.get("status"), default="unavailable")
        expected_latest_bar_end = _parse_time(
            _mapping(projection.get("freshness")).get("expected_latest_bar_end")
        ) or current_cutoff
        current_bar_end = (
            _parse_time(item_bars[-1].get("bar_end_at")) if item_bars else None
        )
        freshness_reason = _symbol_freshness_reason(
            item_as_of=item_as_of,
            current_cutoff=current_cutoff,
            expected_latest_bar_end=expected_latest_bar_end,
            evaluated_at=evaluated_at_utc,
            max_age_seconds=rule_spec["max_source_age_seconds"],
            status=item_status,
        )
        lifecycle_input_available = (
            freshness_reason is None
            and current_bar_end is not None
            and expected_latest_bar_end is not None
            and current_bar_end == expected_latest_bar_end
        )
        source_context = {
            "task_name": task_name,
            "capture_id": capture_id,
            "content_sha256": content_hash,
            "captured_at": _parse_time(source.get("captured_at")),
            "scheduled_at": current_cutoff,
            "trading_date": trading_date,
            "evaluated_at": evaluated_at_utc,
        }

        rs_capability = _relative_strength_capability(
            normalized_symbol,
            features,
            history,
            current_cutoff,
            task_name,
            freshness_reason,
            item_bars,
            comparison_bars_ago=rule_spec["s1"]["comparison_bars_ago"],
        )
        rs_capability["task_name"] = task_name
        capabilities[f"{task_name}:{normalized_symbol}:{rs_capability['strategy_id']}"] = rs_capability
        if rs_capability.get("available") is not True:
            unavailable_setup_scopes.add(("S1", normalized_symbol))
        if (
            freshness_reason is None
            and len(item_bars) >= 7
            and _number(features.get("rs15")) is not None
            and _number(features.get("rs30")) is not None
        ):
            history_eligible_symbols.add(normalized_symbol)
        if rs_capability["available"]:
            relative_transition = _evaluate_relative_strength(
                symbol=normalized_symbol,
                features=features,
                history=history,
                current_cutoff=current_cutoff,
                task_name=task_name,
                source_context=source_context,
                state_setups=state_setups,
                spec=rule_spec,
            )
            if relative_transition:
                transitions.extend(relative_transition)

        ma5_values = _ma5_inputs(item_bars)
        s2_reason = freshness_reason
        if s2_reason is None and not ma5_values.get("available"):
            s2_reason = str(ma5_values.get("reason") or "MA5 必要输入不足。")
        s2_capability = _capability(
            s2_reason is None,
            _reason_code_from_message(s2_reason) if s2_reason else "available",
            s2_reason or "MA5 的 ATR 缓冲输入可用。",
            symbol=normalized_symbol,
            strategy="s2_ma5",
        )
        s2_capability["task_name"] = task_name
        capabilities[f"{task_name}:{normalized_symbol}:{s2_capability['strategy_id']}"] = s2_capability
        if s2_capability.get("available") is not True:
            unavailable_setup_scopes.add(("S2", normalized_symbol))
        if s2_capability["available"]:
            transitions.extend(
                _evaluate_ma5(
                    symbol=normalized_symbol,
                    values=ma5_values,
                    current_cutoff=current_cutoff,
                    task_name=task_name,
                    source_context=source_context,
                    state_setups=state_setups,
                    spec=rule_spec,
                )
            )

        s3_values = _impulse_inputs(
            features,
            item_bars,
            trading_date=trading_date,
        )
        s3_reason = freshness_reason
        if s3_reason is None and not s3_values.get("available"):
            s3_reason = str(s3_values.get("reason") or "价格异动必要输入不足。")
        s3_capability = _capability(
            s3_reason is None,
            _reason_code_from_message(s3_reason) if s3_reason else "available",
            s3_reason or "价格异动输入可用；RVOL 量价能力单独判断。",
            symbol=normalized_symbol,
            strategy="s3_impulse",
        )
        s3_capability["volume_impulse_available"] = (
            s3_values.get("volume_impulse_available") is True
        )
        s3_capability["task_name"] = task_name
        capabilities[f"{task_name}:{normalized_symbol}:{s3_capability['strategy_id']}"] = s3_capability
        volume_capability = _capability(
            s3_values.get("volume_impulse_available") is True,
            str(s3_values.get("volume_reason_code") or "rvol_context_unavailable"),
            str(s3_values.get("volume_reason") or "缺少合格的历史同时间段成交量基线。"),
            symbol=normalized_symbol,
            strategy="s3_volume_impulse",
        )
        volume_capability["task_name"] = task_name
        capabilities[f"{task_name}:{normalized_symbol}:{volume_capability['strategy_id']}"] = volume_capability
        if s3_capability["available"]:
            transitions.extend(
                _evaluate_impulse(
                    symbol=normalized_symbol,
                    values=s3_values,
                    current_cutoff=current_cutoff,
                    task_name=task_name,
                    source_context=source_context,
                    state_setups=state_setups,
                    spec=rule_spec,
                )
            )

        structure_buffer = previous_atr_and_buffer(
            item_bars,
            atr_buffer_multiple=rule_spec["s2"]["atr_buffer_multiple"],
            price_buffer_bps=rule_spec["s2"]["price_buffer_bps"],
        )
        previous_atr = (
            float(structure_buffer["previous_atr14"])
            if structure_buffer is not None
            else None
        )
        price_buffer = float(structure_buffer["buffer"]) if structure_buffer else None
        session_open = _session_open_utc(
            trading_date,
            calendar_name=rule_spec["market_calendar"],
        )
        structure_detections = {
            "S4": detect_opening_range_breakout(
                item_bars,
                session_open=session_open,
                previous_atr14=previous_atr,
                buffer=price_buffer,
            ),
            "S5": detect_range_breakout(
                item_bars,
                previous_atr14=previous_atr,
                buffer=price_buffer,
                lookback_bars=rule_spec["s5"]["lookback_bars"],
                maximum_range_atr=rule_spec["s5"]["maximum_range_atr"],
            ),
            "S6": detect_reversal_candidate(
                item_bars,
                previous_atr14=previous_atr,
                buffer=price_buffer,
                ma5_values=ma5_values,
                lookback_bars=rule_spec["s6"]["lookback_bars"],
                minimum_swing_atr=rule_spec["s6"]["minimum_swing_atr"],
            ),
        }

        for strategy, detector in structure_detections.items():
            capability = _structure_capability(
                detector,
                freshness_reason=freshness_reason,
                symbol=normalized_symbol,
                task_name=task_name,
                strategy=strategy,
            )
            capabilities[f"{task_name}:{normalized_symbol}:{capability['strategy_id']}"] = capability
            if not lifecycle_input_available:
                unavailable_setup_scopes.add((strategy, normalized_symbol))

        s7_capability, s7_transitions = _evaluate_failed_breakouts(
            symbol=normalized_symbol,
            bars=item_bars,
            current_cutoff=current_cutoff,
            task_name=task_name,
            source_context=source_context,
            state_setups=state_setups,
            spec=rule_spec,
            freshness_reason=freshness_reason,
        )
        capabilities[f"{task_name}:{normalized_symbol}:{s7_capability['strategy_id']}"] = s7_capability
        transitions.extend(s7_transitions)

        for strategy, detector in structure_detections.items():
            capability_id = f"{task_name}:{normalized_symbol}:{_structure_strategy_id(strategy)}"
            capability = capabilities[capability_id]
            transitions.extend(
                _evaluate_structure_setup(
                    strategy=strategy,
                    symbol=normalized_symbol,
                    detector=detector,
                    bars=item_bars,
                    current_cutoff=current_cutoff,
                    task_name=task_name,
                    source_context=source_context,
                    state_setups=state_setups,
                    spec=rule_spec,
                    input_available=lifecycle_input_available,
                    allow_new_candidate=capability["available"] is True,
                )
            )

        s8_capability, s8_transitions = _evaluate_setup_exhaustion(
            symbol=normalized_symbol,
            bars=item_bars,
            current_cutoff=current_cutoff,
            task_name=task_name,
            source_context=source_context,
            state=input_state,
            state_setups=state_setups,
            spec=rule_spec,
            freshness_reason=freshness_reason,
        )
        capabilities[f"{task_name}:{normalized_symbol}:{s8_capability['strategy_id']}"] = s8_capability
        transitions.extend(s8_transitions)

    current_symbols = {str(symbol).strip().upper() for symbol in symbols}
    for setup in state_setups:
        if (
            setup.get("status") == "candidate"
            and (not task_name or setup.get("task_name") == task_name)
            and str(setup.get("symbol") or "").upper() not in current_symbols
        ):
            unavailable_setup_scopes.add(
                (str(setup.get("strategy") or "").upper(), str(setup.get("symbol") or "").upper())
            )
    _expire_gap_candidates(
        state_setups,
        transitions,
        evaluated_at=evaluated_at_utc,
        source_context={
            "task_name": task_name,
            "capture_id": capture_id,
            "content_sha256": content_hash,
            "scheduled_at": current_cutoff,
            "trading_date": trading_date,
            "evaluated_at": evaluated_at_utc,
        },
        unavailable_scopes=unavailable_setup_scopes,
    )
    _expire_timed_setups(
        state_setups,
        transitions,
        evaluated_at=evaluated_at_utc,
        source_context={
            "task_name": task_name,
            "capture_id": capture_id,
            "content_sha256": content_hash,
            "scheduled_at": current_cutoff,
            "trading_date": trading_date,
            "evaluated_at": evaluated_at_utc,
        },
        spec=rule_spec,
    )

    eligible_symbols = {
        str(symbol): raw_item
        for symbol, raw_item in symbols.items()
        if str(symbol).strip().upper() in history_eligible_symbols
    }
    current_history_slot = _history_slot(
        task_name=task_name,
        cutoff=current_cutoff,
        symbols=eligible_symbols,
    )
    if current_history_slot and current_cutoff is not None:
        _append_history(history, current_history_slot)

    _prune_terminal_setups(
        state_setups,
        evaluated_at=evaluated_at_utc,
        cooldown_minutes=rule_spec["cooldown_minutes"],
    )

    return _result(input_state, history, transitions, capabilities)


def _normalized_spec(spec: Mapping[str, Any] | None) -> dict[str, Any]:
    if spec is not None and not isinstance(spec, Mapping):
        raise ValueError("spec must be an object")
    supplied = dict(spec or {})
    s1 = _mapping(supplied.get("s1"))
    s2 = _mapping(supplied.get("s2"))
    s3 = _mapping(supplied.get("s3"))
    s4 = _mapping(supplied.get("s4"))
    s5 = _mapping(supplied.get("s5"))
    s6 = _mapping(supplied.get("s6"))
    s7 = _mapping(supplied.get("s7"))
    s8 = _mapping(supplied.get("s8"))
    timeframe = _text(supplied.get("timeframe"), _DEFAULT_TIMEFRAME)
    if timeframe != "5m":
        raise ValueError("spec.timeframe must be 5m")
    rule_version = _text(supplied.get("rule_version"), _DEFAULT_RULE_VERSION)
    if "rule_version" in supplied and not str(supplied["rule_version"]).strip():
        raise ValueError("spec.rule_version must not be empty")
    cooldown_minutes = _spec_integer(supplied, "cooldown_minutes", 15, minimum=0)
    max_source_age = _spec_integer(supplied, "max_source_age_seconds", 600, minimum=0)
    expire_at_session_close = supplied.get("expire_at_session_close", True)
    if not isinstance(expire_at_session_close, bool):
        raise ValueError("spec.expire_at_session_close must be boolean")
    market_calendar = _text(supplied.get("market_calendar"), "XNYS")
    if not market_calendar:
        raise ValueError("spec.market_calendar must not be empty")
    market_timezone = _text(supplied.get("market_timezone"), "America/New_York")
    if not market_timezone:
        raise ValueError("spec.market_timezone must not be empty")
    return {
        "rule_version": rule_version,
        "timeframe": timeframe,
        "s1": {
            "min_delta_pp": _spec_number(s1, "min_delta_pp", 0.05, minimum=0.0, strict=True),
            "comparison_bars_ago": _spec_integer(s1, "comparison_bars_ago", 2, minimum=1),
            "confirm_slots": _spec_integer(s1, "confirm_slots", 2, minimum=1),
        },
        "s2": {
            "atr_buffer_multiple": _spec_number(s2, "atr_buffer_multiple", 0.10, minimum=0.0, strict=True),
            "price_buffer_bps": _spec_number(s2, "price_buffer_bps", 2.0, minimum=0.0, strict=True),
            "candidate_ttl_bars": _spec_integer(s2, "candidate_ttl_bars", 3, minimum=1),
        },
        "s3": {
            "price_shock_atr_multiple": _spec_number(s3, "price_shock_atr_multiple", 2.0, minimum=0.0, strict=True),
            "impulse_atr_multiple": _spec_number(s3, "impulse_atr_multiple", 1.2, minimum=0.0, strict=True),
            "rvol_min": _spec_number(s3, "rvol_min", 1.8, minimum=0.0, strict=True),
        },
        "s4": {
            "candidate_ttl_bars": _spec_integer(s4, "candidate_ttl_bars", 3, minimum=1),
        },
        "s5": {
            "lookback_bars": _spec_integer(s5, "lookback_bars", 6, minimum=2),
            "maximum_range_atr": _spec_number(
                s5, "maximum_range_atr", 2.5, minimum=0.0, strict=True
            ),
            "candidate_ttl_bars": _spec_integer(s5, "candidate_ttl_bars", 3, minimum=1),
        },
        "s6": {
            "lookback_bars": _spec_integer(s6, "lookback_bars", 6, minimum=2),
            "minimum_swing_atr": _spec_number(
                s6, "minimum_swing_atr", 2.0, minimum=0.0, strict=True
            ),
            "candidate_ttl_bars": _spec_integer(s6, "candidate_ttl_bars", 3, minimum=1),
        },
        "s7": {
            "failure_window_bars": _spec_integer(s7, "failure_window_bars", 3, minimum=1),
        },
        "s8": {
            "comparison_bars_ago": _spec_integer(s8, "comparison_bars_ago", 4, minimum=1),
            "count_threshold": _spec_integer(s8, "count_threshold", 9, minimum=1),
            "rule_version": _text(s8.get("rule_version"), "simplified_setup_session_v1"),
        },
        "cooldown_minutes": cooldown_minutes,
        "max_source_age_seconds": max_source_age,
        "expire_at_session_close": expire_at_session_close,
        "market_calendar": market_calendar,
        "market_timezone": market_timezone,
    }


def _spec_number(
    values: Mapping[str, Any],
    key: str,
    default: float,
    *,
    minimum: float,
    strict: bool,
) -> float:
    if key not in values:
        return default
    number = _number(values[key])
    if number is None or (number <= minimum if strict else number < minimum):
        comparison = "greater than" if strict else "at least"
        raise ValueError(f"spec.{key} must be finite and {comparison} {minimum}")
    return number


def _spec_integer(values: Mapping[str, Any], key: str, default: int, *, minimum: int) -> int:
    if key not in values:
        return default
    value = values[key]
    if isinstance(value, bool):
        raise ValueError(f"spec.{key} must be an integer")
    number = _number(value)
    if number is None or not number.is_integer() or number < minimum:
        raise ValueError(f"spec.{key} must be an integer >= {minimum}")
    return int(number)


def _copy_state(prior_state: Mapping[str, Any] | None) -> dict[str, Any]:
    copied = deepcopy(dict(prior_state or {}))
    copied.setdefault("schema_version", _STATE_SCHEMA)
    copied.setdefault("setups", [])
    if not isinstance(copied.get("setups"), list):
        copied["setups"] = []
    if not isinstance(copied.get("history"), list):
        copied["history"] = []
    if not isinstance(copied.get("active_setups"), Mapping):
        copied["active_setups"] = {}
    if not isinstance(copied.get("structure_sequences"), Mapping):
        copied["structure_sequences"] = {}
    if not copied["setups"] and isinstance(copied["active_setups"], Mapping):
        for setup_id, raw_setup in copied["active_setups"].items():
            if not isinstance(raw_setup, Mapping):
                continue
            setup = dict(raw_setup)
            setup.setdefault("setup_id", str(setup_id))
            setup.setdefault("symbol", str(setup_id).split(":", 1)[0])
            setup.setdefault("task_name", "")
            setup.setdefault("strategy", "S2")
            setup.setdefault("setup_type", "ma5_reclaim")
            setup.setdefault("last_cutoff", setup.get("candidate_cutoff"))
            setup.setdefault("transition_seq", 0)
            setup.setdefault("bars_elapsed", setup.get("bars_since_candidate", 0))
            copied["setups"].append(setup)
    return copied


def _history_from_state(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_history = state.get("history", [])
    if isinstance(raw_history, list):
        return [dict(item) for item in raw_history if isinstance(item, Mapping)]
    return []


def _result(
    state: dict[str, Any],
    history: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    capabilities: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    state["schema_version"] = _STATE_SCHEMA
    state["setups"] = [dict(item) for item in state.get("setups", []) if isinstance(item, Mapping)]
    state["history"] = history[-64:]
    state["active_setups"] = {
        str(item.get("setup_id")): dict(item)
        for item in state["setups"]
        if isinstance(item, Mapping)
        and item.get("setup_id")
        and item.get("status") in {"candidate", "confirmed"}
    }
    return {"state": state, "transitions": transitions, "capabilities": capabilities}


def _prune_terminal_setups(
    setups: list[dict[str, Any]],
    *,
    evaluated_at: datetime,
    cooldown_minutes: int,
) -> None:
    retention = timedelta(minutes=max(cooldown_minutes, 1))
    kept: list[dict[str, Any]] = []
    for setup in setups:
        if setup.get("status") in {"candidate", "confirmed"}:
            kept.append(setup)
            continue
        started_at = _parse_time(setup.get("started_at"))
        if started_at is not None and evaluated_at - started_at <= retention:
            kept.append(setup)
    setups[:] = kept


def _minute_boundary_verified(projection: Mapping[str, Any]) -> bool:
    quality = _mapping(projection.get("quality"))
    return quality.get("minute_timestamp_boundary_verified") is True


def _relative_strength_capability(
    symbol: str,
    features: Mapping[str, Any],
    history: Sequence[Mapping[str, Any]],
    current_cutoff: datetime | None,
    task_name: str,
    freshness_reason: str | None,
    bars: Sequence[Mapping[str, Any]],
    *,
    comparison_bars_ago: int,
) -> dict[str, Any]:
    if freshness_reason:
        return _capability(
            False,
            _reason_code_from_message(freshness_reason),
            freshness_reason,
            symbol=symbol,
            strategy="s1_relative_strength",
        )
    if len(bars) < 7:
        return _capability(
            False,
            "incomplete_bars",
            "RS15/RS30 需要至少 7 根连续完整 5 分钟 bar。",
            symbol=symbol,
            strategy="s1_relative_strength",
        )
    current_rs15 = _number(features.get("rs15"))
    current_rs30 = _number(features.get("rs30"))
    if current_rs15 is None or current_rs30 is None:
        return _capability(
            False,
            "relative_strength_unavailable",
            "缺少对齐的 RS15/RS30 基准数据。",
            symbol=symbol,
            strategy="s1_relative_strength",
        )
    if current_cutoff is None:
        return _capability(
            False,
            "cutoff_unavailable",
            "缺少当前证据 cutoff。",
            symbol=symbol,
            strategy="s1_relative_strength",
        )
    comparison_cutoff = current_cutoff - comparison_bars_ago * _FIVE_MINUTES
    previous = _find_history_slot(history, comparison_cutoff, task_name, symbol)
    if previous is None:
        return _capability(
            False,
            "relative_strength_comparison_unavailable",
            "缺少 10 分钟前同窗口 RS15/RS30。",
            symbol=symbol,
            strategy="s1_relative_strength",
        )
    prior_features = _mapping(_mapping(previous.get("symbols")).get(symbol)).get("features")
    prior_features = _mapping(prior_features)
    if _number(prior_features.get("rs15")) is None or _number(prior_features.get("rs30")) is None:
        return _capability(
            False,
            "relative_strength_comparison_unavailable",
            "10 分钟前同窗口 RS15/RS30 不完整。",
            symbol=symbol,
            strategy="s1_relative_strength",
        )
    return _capability(
        True,
        "available",
        "RS15/RS30 当前值与 10 分钟前窗口均可用。",
        symbol=symbol,
        strategy="s1_relative_strength",
    )


def _evaluate_relative_strength(
    *,
    symbol: str,
    features: Mapping[str, Any],
    history: Sequence[Mapping[str, Any]],
    current_cutoff: datetime | None,
    task_name: str,
    source_context: Mapping[str, Any],
    state_setups: list[dict[str, Any]],
    spec: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if current_cutoff is None:
        return []
    comparison_bars = int(_mapping(spec.get("s1")).get("comparison_bars_ago", 2))
    previous = _find_history_slot(
        history,
        current_cutoff - comparison_bars * _FIVE_MINUTES,
        task_name,
        symbol,
    )
    if previous is None:
        return []
    current_rs15 = _number(features.get("rs15"))
    current_rs30 = _number(features.get("rs30"))
    previous_features = _mapping(_mapping(previous.get("symbols")).get(symbol)).get("features")
    previous_features = _mapping(previous_features)
    previous_rs15 = _number(previous_features.get("rs15"))
    previous_rs30 = _number(previous_features.get("rs30"))
    if None in (current_rs15, current_rs30, previous_rs15, previous_rs30):
        return []

    minimum_delta = float(_mapping(spec.get("s1")).get("min_delta_pp", 0.05))
    delta15 = float(current_rs15) - float(previous_rs15)
    delta30 = float(current_rs30) - float(previous_rs30)
    if float(current_rs30) > 0 and delta15 >= minimum_delta and delta30 >= minimum_delta:
        direction = "strong_improving"
        setup_type = "relative_strength_improving"
    elif float(current_rs30) > 0 and delta15 <= -minimum_delta and delta30 <= -minimum_delta:
        direction = "strong_weakening"
        setup_type = "relative_strength_weakening"
    elif float(current_rs30) < 0 and delta15 >= minimum_delta and delta30 >= minimum_delta:
        direction = "weak_recovering"
        setup_type = "relative_strength_recovering"
    elif float(current_rs30) < 0 and delta15 <= -minimum_delta and delta30 <= -minimum_delta:
        direction = "weak_worsening"
        setup_type = "relative_strength_worsening"
    else:
        return []

    rule_version = _strategy_rule_version(spec, "s1")
    trading_date = str(source_context.get("trading_date") or "")
    existing = _find_setup_scope(
        state_setups,
        task_name=task_name,
        symbol=symbol,
        strategy="S1",
        setup_type=setup_type,
        trading_date=trading_date,
        rule_version=rule_version,
    )
    required_slots = int(_mapping(spec.get("s1")).get("confirm_slots", 2))
    consecutive = 1
    transitions: list[dict[str, Any]] = []
    if existing is not None:
        if existing.get("status") == "confirmed":
            return []
        previous_cutoff = _parse_time(existing.get("last_cutoff"))
        is_consecutive = previous_cutoff == current_cutoff - _FIVE_MINUTES
        if existing.get("direction") == direction and is_consecutive:
            consecutive = int(existing.get("consecutive_slots", 1)) + 1
        elif existing.get("status") == "candidate":
            existing["status"] = "expired"
            existing["transition_seq"] = int(existing.get("transition_seq", 0)) + 1
            existing["last_cutoff"] = _iso(current_cutoff)
            transitions.append(
                _transition(
                    existing,
                    transition="setup_expired",
                    status="expired",
                    reason_code="coverage_gap" if not is_consecutive else "direction_changed",
                    source_context=source_context,
                    evidence={"previous_direction": existing.get("direction"), "direction": direction},
                )
            )
            existing = None
        else:
            consecutive = required_slots
    status = "confirmed" if consecutive >= required_slots else "candidate"
    if existing and existing.get("status") == status and existing.get("last_cutoff") == _iso(current_cutoff):
        return transitions

    evidence = {
        "rs15": float(current_rs15),
        "rs30": float(current_rs30),
        "rs15_delta_pp": delta15,
        "rs30_delta_pp": delta30,
        "comparison_cutoff": _iso(current_cutoff - comparison_bars * _FIVE_MINUTES),
        "consecutive_slots": consecutive,
    }
    if existing is None:
        setup_id = _setup_id(
            task_name,
            symbol,
            "S1",
            rule_version,
            setup_type,
            _start_evidence_key(source_context, current_cutoff),
            trading_date=trading_date,
            timeframe=str(spec.get("timeframe", _DEFAULT_TIMEFRAME)),
        )
        existing = {
            "setup_id": setup_id,
            "task_name": task_name,
            "symbol": symbol,
            "strategy": "S1",
            "setup_type": setup_type,
            "direction": direction,
            "timeframe": spec.get("timeframe", _DEFAULT_TIMEFRAME),
            "rule_version": rule_version,
            "trading_date": str(source_context.get("trading_date") or ""),
            "status": status,
            "transition_seq": 0,
            "started_at": _iso(current_cutoff),
            "last_cutoff": _iso(current_cutoff),
            "bars_elapsed": 0,
            "consecutive_slots": consecutive,
            "confirmation_level": None,
            "invalidation_level": None,
            "expires_at": _iso(
                _expiry_at(
                    current_cutoff,
                    timedelta(minutes=15),
                    trading_date=trading_date,
                    spec=spec,
                )
            ),
            "cooldown_until": None,
            "frozen_evidence": evidence,
        }
        state_setups.append(existing)
        transition_name = "setup_confirmed" if status == "confirmed" else "candidate_created"
        existing["transition_seq"] = 1
    else:
        existing["status"] = status
        existing["last_cutoff"] = _iso(current_cutoff)
        existing["consecutive_slots"] = consecutive
        existing["bars_elapsed"] = int(existing.get("bars_elapsed", 0)) + 1
        existing["transition_seq"] = int(existing.get("transition_seq", 0)) + 1
        existing["frozen_evidence"] = existing.get("frozen_evidence") or evidence
        transition_name = "setup_confirmed" if status == "confirmed" else "candidate_updated"

    transitions.append(
        _transition(
            existing,
            transition=transition_name,
            status=status,
            reason_code=direction,
            source_context=source_context,
            evidence=evidence,
        )
    )
    return transitions


def _ma5_inputs(bars: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    closes = [_number(bar.get("close")) for bar in bars]
    if len(closes) < 6 or any(value is None for value in closes[-6:]):
        return {"available": False, "reason": "MA5 站回/失守需要连续完整的当前和前一 bar。"}
    atr_series = _wilder_atr_series(bars, period=14)
    previous_atr = atr_series[-2] if len(atr_series) >= 2 else None
    if previous_atr is None or not math.isfinite(previous_atr) or previous_atr <= 0:
        return {"available": False, "reason": "MA5 缓冲需要正值的前一根 Wilder ATR14。"}
    ma5_history: list[float | None] = []
    for end_index in range(len(closes)):
        if end_index < 4 or any(value is None for value in closes[end_index - 4 : end_index + 1]):
            ma5_history.append(None)
        else:
            ma5_history.append(
                fmean(float(value) for value in closes[end_index - 4 : end_index + 1] if value is not None)
            )
    prior_atr_before_previous = atr_series[-3] if len(atr_series) >= 3 else None
    current_close = float(closes[-1])
    previous_close = float(closes[-2])
    current_ma5 = float(ma5_history[-1]) if ma5_history[-1] is not None else None
    previous_ma5 = float(ma5_history[-2]) if ma5_history[-2] is not None else None
    previous_two_ma5 = float(ma5_history[-3]) if ma5_history[-3] is not None else None
    previous_distance_atr = (
        (previous_close - previous_ma5) / prior_atr_before_previous
        if previous_ma5 is not None and prior_atr_before_previous is not None and prior_atr_before_previous > 0
        else None
    )
    current_distance_atr = (
        (current_close - current_ma5) / previous_atr
        if current_ma5 is not None and previous_atr > 0
        else None
    )
    return {
        "available": True,
        "close": current_close,
        "previous_close": previous_close,
        "ma5": current_ma5,
        "previous_ma5": previous_ma5,
        "previous_two_ma5": previous_two_ma5,
        "atr14": atr_series[-1] if atr_series else None,
        "previous_atr14": previous_atr,
        "previous_two_atr14": prior_atr_before_previous,
        "ma5_slope_atr": (
            (current_ma5 - previous_two_ma5) / previous_atr
            if current_ma5 is not None and previous_two_ma5 is not None and previous_atr > 0
            else None
        ),
        "ma5_distance_atr": current_distance_atr,
        "previous_ma5_distance_atr": previous_distance_atr,
        "ma5_history": ma5_history,
        "high": _number(bars[-1].get("high")),
        "low": _number(bars[-1].get("low")),
        "bars": list(bars),
    }


def _detect_ma5_retest_or_extension(
    values: Mapping[str, Any],
    *,
    buffer: float,
) -> tuple[str | None, float | None, float | None]:
    current_close = float(values["close"])
    current_ma5 = float(values["ma5"])
    previous_ma5 = float(values["previous_ma5"])
    high = _number(values.get("high"))
    low = _number(values.get("low"))
    prior_two_ma5 = _number(values.get("previous_two_ma5"))

    bars = list(values.get("bars", []))
    ma5_history = list(values.get("ma5_history", []))
    recent_prior_indices = range(max(0, len(bars) - 4), len(bars) - 1)
    prior_closes_held = all(
        index < len(ma5_history)
        and ma5_history[index] is not None
        and _number(bars[index].get("close")) is not None
        and float(bars[index]["close"]) > float(ma5_history[index])
        for index in recent_prior_indices
    )

    if (
        len(values.get("bars", [])) >= 8
        and high is not None
        and low is not None
        and prior_two_ma5 is not None
        and previous_ma5 > prior_two_ma5
        and prior_closes_held
        and low <= current_ma5 + buffer
        and low >= current_ma5 - buffer
        and current_close > current_ma5
    ):
        return "ma5_retest_hold", high, low

    if (
        len(values.get("bars", [])) >= 8
        and high is not None
        and low is not None
        and prior_two_ma5 is not None
        and previous_ma5 < prior_two_ma5
        and high >= current_ma5 - buffer
        and current_close < current_ma5
    ):
        return "ma5_failed_reclaim", low, high

    distance = _number(values.get("ma5_distance_atr"))
    previous_distance = _number(values.get("previous_ma5_distance_atr"))
    if (
        distance is not None
        and previous_distance is not None
        and abs(distance) > 1.5
        and abs(distance) > abs(previous_distance)
    ):
        return "ma5_extension_risk", None, None

    return None, None, None


def _evaluate_ma5(
    *,
    symbol: str,
    values: Mapping[str, Any],
    current_cutoff: datetime | None,
    task_name: str,
    source_context: Mapping[str, Any],
    state_setups: list[dict[str, Any]],
    spec: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if current_cutoff is None:
        return []
    prior_close = float(values["previous_close"])
    current_close = float(values["close"])
    prior_ma5 = float(values["previous_ma5"])
    current_ma5 = float(values["ma5"])
    prior_atr = float(values["previous_atr14"])
    previous_price = prior_close
    buffer = max(
        float(_mapping(spec.get("s2")).get("atr_buffer_multiple", 0.10)) * prior_atr,
        float(_mapping(spec.get("s2")).get("price_buffer_bps", 2.0)) / 10_000.0 * previous_price,
    )
    setup_type: str | None = None
    confirmation_level: float | None = None
    invalidation_level: float | None = None
    active = _active_setup(state_setups, task_name, symbol, "S2")
    if active is not None and str(active.get("trading_date") or "") != str(
        source_context.get("trading_date") or ""
    ):
        active = None
    if active is not None:
        return _advance_ma5_candidate(
            active,
            values=values,
            current_cutoff=current_cutoff,
            source_context=source_context,
            spec=spec,
        )

    if prior_close <= prior_ma5 and current_close > current_ma5 + buffer:
        setup_type = "ma5_reclaim"
        confirmation_level = current_ma5 + buffer
        invalidation_level = current_ma5 - buffer
    elif prior_close >= prior_ma5 and current_close < current_ma5 - buffer:
        setup_type = "ma5_break"
        confirmation_level = current_ma5 - buffer
        invalidation_level = current_ma5 + buffer
    else:
        setup_type, confirmation_level, invalidation_level = _detect_ma5_retest_or_extension(
            values,
            buffer=buffer,
        )
        if setup_type is None:
            return []

    trading_date = str(source_context.get("trading_date") or "")
    rule_version = _strategy_rule_version(spec, "s2")
    setup_id = _setup_id(
        task_name,
        symbol,
        "S2",
        rule_version,
        setup_type,
        _start_evidence_key(source_context, current_cutoff),
        trading_date=trading_date,
        timeframe=str(spec.get("timeframe", _DEFAULT_TIMEFRAME)),
    )
    existing = _find_setup(state_setups, setup_id)
    if existing is not None and existing.get("last_cutoff") == _iso(current_cutoff):
        return []
    if existing is not None and existing.get("status") in {"candidate", "confirmed"}:
        return []
    direction = _ma5_direction(setup_type)
    if setup_type != "ma5_extension_risk" and _within_cooldown(
        state_setups,
        task_name,
        symbol,
        current_cutoff,
        spec,
        strategy="S2",
        setup_type=setup_type,
        direction=direction,
        rule_version=rule_version,
    ):
        return []

    setup = {
        "setup_id": setup_id,
        "task_name": task_name,
        "symbol": symbol,
        "strategy": "S2",
        "setup_type": setup_type,
        "direction": direction,
        "timeframe": spec.get("timeframe", _DEFAULT_TIMEFRAME),
        "rule_version": rule_version,
        "trading_date": str(source_context.get("trading_date") or ""),
        "status": "confirmed" if setup_type == "ma5_extension_risk" else "candidate",
        "transition_seq": 1,
        "started_at": _iso(current_cutoff),
        "last_cutoff": _iso(current_cutoff),
        "bars_elapsed": 1,
        "candidate_cutoff": _iso(current_cutoff),
        "last_bar_end_at": _parse_time(values["bars"][-1].get("bar_end_at")).isoformat(),
        "confirmation_level": confirmation_level,
        "invalidation_level": invalidation_level,
        "expires_at": _iso(
            _expiry_at(
                current_cutoff,
                int(_mapping(spec.get("s2")).get("candidate_ttl_bars", 3)) * _FIVE_MINUTES,
                trading_date=trading_date,
                spec=spec,
            )
        ),
        "cooldown_until": _iso(
            current_cutoff + timedelta(minutes=int(spec.get("cooldown_minutes", 15)))
        ),
        "frozen_evidence": {
            "previous_close": prior_close,
            "close": current_close,
            "previous_ma5": prior_ma5,
            "ma5": current_ma5,
            "previous_atr14": prior_atr,
            "buffer": buffer,
        },
    }
    state_setups.append(setup)
    if setup_type == "ma5_extension_risk":
        setup["confirmation_level"] = None
        setup["invalidation_level"] = None
        return [
            _transition(
                setup,
                transition="setup_confirmed",
                status="confirmed",
                reason_code=setup_type,
                source_context=source_context,
                evidence={
                    **setup["frozen_evidence"],
                    "ma5_distance_atr": values.get("ma5_distance_atr"),
                    "previous_ma5_distance_atr": values.get("previous_ma5_distance_atr"),
                },
            )
        ]
    return [
        _transition(
            setup,
            transition="candidate_created",
            status="candidate",
            reason_code=setup_type,
            source_context=source_context,
            evidence=setup["frozen_evidence"],
        )
    ]


def _advance_ma5_candidate(
    setup: dict[str, Any],
    *,
    values: Mapping[str, Any],
    current_cutoff: datetime,
    source_context: Mapping[str, Any],
    spec: Mapping[str, Any],
) -> list[dict[str, Any]]:
    previous_cutoff = _parse_time(setup.get("last_cutoff"))
    if previous_cutoff == current_cutoff:
        return []
    if previous_cutoff is None or current_cutoff != previous_cutoff + _FIVE_MINUTES:
        setup["status"] = "expired"
        setup["transition_seq"] = int(setup.get("transition_seq", 0)) + 1
        setup["last_cutoff"] = _iso(current_cutoff)
        return [
            _transition(
                setup,
                transition="setup_expired",
                status="expired",
                reason_code="coverage_gap",
                source_context=source_context,
                evidence={"prior_cutoff": _iso(previous_cutoff), "cutoff": _iso(current_cutoff)},
            )
        ]

    previous_bar_end = _parse_time(setup.get("last_bar_end_at"))
    current_bar_start = _parse_time(values["bars"][-1].get("bar_start_at")) if values.get("bars") else None
    if previous_bar_end is not None and current_bar_start != previous_bar_end:
        setup["status"] = "expired"
        setup["transition_seq"] = int(setup.get("transition_seq", 0)) + 1
        setup["last_cutoff"] = _iso(current_cutoff)
        return [
            _transition(
                setup,
                transition="setup_expired",
                status="expired",
                reason_code="coverage_gap",
                source_context=source_context,
                evidence={"prior_bar_end_at": _iso(previous_bar_end), "current_bar_start_at": _iso(current_bar_start)},
            )
        ]

    was_confirmed = setup.get("status") == "confirmed"
    elapsed = int(setup.get("bars_elapsed", 0)) + 1
    setup["bars_elapsed"] = elapsed
    setup["last_cutoff"] = _iso(current_cutoff)
    setup["last_bar_end_at"] = _parse_time(values["bars"][-1].get("bar_end_at")).isoformat() if values.get("bars") else None
    close = float(values["close"])
    high = _number(values.get("high"))
    low = _number(values.get("low"))
    confirmation_level = _number(setup.get("confirmation_level"))
    invalidation_level = _number(setup.get("invalidation_level"))
    setup_type = str(setup.get("setup_type") or "")
    long_setup = setup_type in {"ma5_reclaim", "ma5_retest_hold"}
    if long_setup and invalidation_level is not None and close <= invalidation_level:
        next_status = "invalidated"
        reason = "retest_failed" if setup_type == "ma5_retest_hold" else "reclaim_failed"
        transition = "setup_invalidated"
    elif not long_setup and invalidation_level is not None and close >= invalidation_level:
        next_status = "invalidated"
        reason = "breakdown_failed"
        transition = "setup_invalidated"
    elif was_confirmed:
        return []
    elif setup_type == "ma5_retest_hold" and high is not None and confirmation_level is not None and high > confirmation_level:
        next_status = "confirmed"
        reason = "retest_high_broken"
        transition = "setup_confirmed"
    elif setup_type == "ma5_failed_reclaim" and low is not None and confirmation_level is not None and low < confirmation_level:
        next_status = "confirmed"
        reason = "failed_reclaim_low_broken"
        transition = "setup_confirmed"
    elif confirmation_level is not None and (
        close > confirmation_level if long_setup else close < confirmation_level
    ):
        next_status = "confirmed"
        reason = "next_bar_held"
        transition = "setup_confirmed"
    elif elapsed >= int(_mapping(spec.get("s2")).get("candidate_ttl_bars", 3)):
        next_status = "expired"
        reason = "candidate_ttl"
        transition = "setup_expired"
    else:
        return []

    setup["status"] = next_status
    setup["transition_seq"] = int(setup.get("transition_seq", 0)) + 1
    return [
        _transition(
            setup,
            transition=transition,
            status=next_status,
            reason_code=reason,
            source_context=source_context,
            evidence={"close": close, "bars_elapsed": elapsed},
        )
    ]


def _structure_strategy_id(strategy: str) -> str:
    return {
        "S4": "s4_orb",
        "S5": "s5_range_breakout",
        "S6": "s6_reversal",
    }[strategy.upper()]


def _structure_capability(
    detector: Mapping[str, Any],
    *,
    freshness_reason: str | None,
    symbol: str,
    task_name: str,
    strategy: str,
) -> dict[str, Any]:
    detector_available = detector.get("available") is True
    if strategy.upper() == "S6" and detector.get("signal") is None:
        # S6 capability follows the current rolling formation.  Existing
        # setups still advance independently of this status using their
        # frozen levels in _evaluate_structure_setup.
        detector_available = False
    is_available = freshness_reason is None and detector_available
    reason_code = (
        _reason_code_from_message(freshness_reason)
        if freshness_reason
        else str(detector.get("reason_code") or "evidence_unavailable")
    )
    message = freshness_reason or str(detector.get("message") or "结构策略输入不可用。")
    capability = _capability(
        is_available,
        "available" if is_available else reason_code,
        "结构策略基础输入可用。" if is_available else message,
        symbol=symbol,
        strategy=_structure_strategy_id(strategy),
    )
    capability["task_name"] = task_name
    return capability


def _evaluate_structure_setup(
    *,
    strategy: str,
    symbol: str,
    detector: Mapping[str, Any],
    bars: Sequence[Mapping[str, Any]],
    current_cutoff: datetime | None,
    task_name: str,
    source_context: Mapping[str, Any],
    state_setups: list[dict[str, Any]],
    spec: Mapping[str, Any],
    input_available: bool,
    allow_new_candidate: bool,
) -> list[dict[str, Any]]:
    if current_cutoff is None:
        return []
    trading_date = str(source_context.get("trading_date") or "")
    active_setups = [
        setup
        for setup in state_setups
        if setup.get("task_name") == task_name
        and setup.get("symbol") == symbol
        and str(setup.get("strategy") or "").upper() == strategy.upper()
        and setup.get("status") in {"candidate", "confirmed"}
    ]
    if active_setups:
        if not input_available:
            return []
        transitions: list[dict[str, Any]] = []
        for setup in active_setups:
            if str(setup.get("trading_date") or "") != trading_date:
                setup["status"] = "expired"
                setup["transition_seq"] = int(setup.get("transition_seq", 0)) + 1
                setup["last_cutoff"] = _iso(current_cutoff)
                transitions.append(
                    _transition(
                        setup,
                        transition="setup_expired",
                        status="expired",
                        reason_code="session_expired",
                        source_context=source_context,
                        evidence={"prior_trading_date": setup.get("trading_date")},
                    )
                )
                continue
            transitions.extend(
                _advance_structure_setup(
                    setup,
                    bars=bars,
                    current_cutoff=current_cutoff,
                    source_context=source_context,
                    spec=spec,
                    strategy=strategy,
                )
            )
        return transitions

    if not allow_new_candidate:
        return []
    signal = detector.get("signal")
    if not isinstance(signal, Mapping):
        return []
    setup_type = str(signal.get("setup_type") or "")
    direction = str(signal.get("direction") or "")
    if not setup_type or direction not in {"bullish", "bearish"}:
        return []
    rule_version = _strategy_rule_version(spec, strategy.lower())
    if _within_cooldown(
        state_setups,
        task_name,
        symbol,
        current_cutoff,
        spec,
        strategy=strategy,
        setup_type=setup_type,
        direction=direction,
        rule_version=rule_version,
    ):
        return []

    setup_id = _setup_id(
        task_name,
        symbol,
        strategy,
        rule_version,
        setup_type,
        _start_evidence_key(source_context, current_cutoff),
        trading_date=trading_date,
        timeframe=str(spec.get("timeframe", _DEFAULT_TIMEFRAME)),
    )
    ttl_bars = int(_mapping(spec.get(strategy.lower())).get("candidate_ttl_bars", 3))
    last_bar_end = _parse_time(bars[-1].get("bar_end_at")) if bars else None
    setup = {
        "setup_id": setup_id,
        "task_name": task_name,
        "symbol": symbol,
        "strategy": strategy,
        "setup_type": setup_type,
        "direction": direction,
        "timeframe": spec.get("timeframe", _DEFAULT_TIMEFRAME),
        "rule_version": rule_version,
        "trading_date": trading_date,
        "status": "candidate",
        "transition_seq": 1,
        "started_at": _iso(current_cutoff),
        "last_cutoff": _iso(current_cutoff),
        "bars_elapsed": 1,
        "candidate_cutoff": _iso(current_cutoff),
        "last_bar_end_at": _iso(last_bar_end),
        "confirmation_level": _number(signal.get("confirmation_level")),
        "invalidation_level": _number(signal.get("invalidation_level")),
        "expires_at": _iso(
            _expiry_at(
                current_cutoff,
                ttl_bars * _FIVE_MINUTES,
                trading_date=trading_date,
                spec=spec,
            )
        ),
        "cooldown_until": _iso(
            current_cutoff + timedelta(minutes=int(spec.get("cooldown_minutes", 15)))
        ),
        "frozen_evidence": dict(signal.get("evidence") or {}),
    }
    state_setups.append(setup)
    return [
        _transition(
            setup,
            transition="candidate_created",
            status="candidate",
            reason_code=setup_type,
            source_context=source_context,
            evidence=setup["frozen_evidence"],
        )
    ]


def _advance_structure_setup(
    setup: dict[str, Any],
    *,
    bars: Sequence[Mapping[str, Any]],
    current_cutoff: datetime,
    source_context: Mapping[str, Any],
    spec: Mapping[str, Any],
    strategy: str,
) -> list[dict[str, Any]]:
    previous_cutoff = _parse_time(setup.get("last_cutoff"))
    if previous_cutoff is None or current_cutoff <= previous_cutoff:
        return []
    if not bars:
        return []
    current_bar = bars[-1]
    current_start = _parse_time(current_bar.get("bar_start_at"))
    current_end = _parse_time(current_bar.get("bar_end_at"))
    previous_end = _parse_time(setup.get("last_bar_end_at"))
    is_adjacent = (
        current_cutoff == previous_cutoff + _FIVE_MINUTES
        and current_start is not None
        and previous_end == current_start
    )
    if setup.get("status") == "candidate" and not is_adjacent:
        setup["status"] = "expired"
        setup["transition_seq"] = int(setup.get("transition_seq", 0)) + 1
        setup["last_cutoff"] = _iso(current_cutoff)
        return [
            _transition(
                setup,
                transition="setup_expired",
                status="expired",
                reason_code="coverage_gap",
                source_context=source_context,
                evidence={
                    "prior_cutoff": _iso(previous_cutoff),
                    "current_cutoff": _iso(current_cutoff),
                },
            )
        ]

    elapsed = int(setup.get("bars_elapsed", 1)) + 1 if is_adjacent else 1
    setup["last_cutoff"] = _iso(current_cutoff)
    setup["last_bar_end_at"] = _iso(current_end)
    setup["bars_elapsed"] = elapsed
    close = _number(current_bar.get("close"))
    if close is None:
        return []
    bullish = setup.get("direction") == "bullish"
    invalidation_level = _number(setup.get("invalidation_level"))
    confirmation_level = _number(setup.get("confirmation_level"))
    if invalidation_level is not None and (
        close <= invalidation_level if bullish else close >= invalidation_level
    ):
        return _transition_structure_terminal(
            setup,
            status="invalidated",
            transition="setup_invalidated",
            reason_code="frozen_level_invalidated",
            source_context=source_context,
            evidence={"close": close, "bars_elapsed": elapsed},
        )
    if setup.get("status") == "confirmed":
        return []
    if confirmation_level is not None and (
        _strictly_greater(close, confirmation_level)
        if bullish
        else _strictly_less(close, confirmation_level)
    ):
        return _transition_structure_terminal(
            setup,
            status="confirmed",
            transition="setup_confirmed",
            reason_code="next_bar_held",
            source_context=source_context,
            evidence={"close": close, "bars_elapsed": elapsed},
        )

    ttl_bars = int(_mapping(spec.get(strategy.lower())).get("candidate_ttl_bars", 3))
    if elapsed >= ttl_bars:
        return _transition_structure_terminal(
            setup,
            status="expired",
            transition="setup_expired",
            reason_code="candidate_ttl",
            source_context=source_context,
            evidence={"close": close, "bars_elapsed": elapsed},
        )
    return []


def _transition_structure_terminal(
    setup: dict[str, Any],
    *,
    status: str,
    transition: str,
    reason_code: str,
    source_context: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> list[dict[str, Any]]:
    setup["status"] = status
    setup["transition_seq"] = int(setup.get("transition_seq", 0)) + 1
    return [
        _transition(
            setup,
            transition=transition,
            status=status,
            reason_code=reason_code,
            source_context=source_context,
            evidence=evidence,
        )
    ]


def _evaluate_failed_breakouts(
    *,
    symbol: str,
    bars: Sequence[Mapping[str, Any]],
    current_cutoff: datetime | None,
    task_name: str,
    source_context: Mapping[str, Any],
    state_setups: list[dict[str, Any]],
    spec: Mapping[str, Any],
    freshness_reason: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if freshness_reason:
        capability = _capability(
            False,
            _reason_code_from_message(freshness_reason),
            freshness_reason,
            symbol=symbol,
            strategy="s7_failed_breakout",
        )
        capability["task_name"] = task_name
        return capability, []
    current_bar = bars[-1] if bars else {}
    current_close = _number(current_bar.get("close"))
    current_start = _parse_time(current_bar.get("bar_start_at"))
    current_end = _parse_time(current_bar.get("bar_end_at"))
    if current_cutoff is None or current_close is None or current_start is None or current_end is None:
        capability = _capability(
            False,
            "current_bar_unavailable",
            "假突破判断需要有效的当前完整 bar。",
            symbol=symbol,
            strategy="s7_failed_breakout",
        )
        capability["task_name"] = task_name
        return capability, []

    window_bars = int(_mapping(spec.get("s7")).get("failure_window_bars", 3))
    parents = [
        setup
        for setup in state_setups
        if setup.get("task_name") == task_name
        and setup.get("symbol") == symbol
        and str(setup.get("strategy") or "").upper() in {"S4", "S5"}
        and setup.get("status") in {"candidate", "confirmed"}
        and str(setup.get("trading_date") or "") == str(source_context.get("trading_date") or "")
    ]
    eligible_parents = []
    for parent in parents:
        started_at = _parse_time(parent.get("started_at"))
        previous_cutoff = _parse_time(parent.get("last_cutoff"))
        previous_end = _parse_time(parent.get("last_bar_end_at"))
        bars_after_start = (
            int((current_cutoff - started_at).total_seconds() // _FIVE_MINUTES.total_seconds())
            if started_at is not None and current_cutoff > started_at
            else 0
        )
        if (
            1 <= bars_after_start <= window_bars
            and previous_cutoff == current_cutoff - _FIVE_MINUTES
            and previous_end == current_start
        ):
            eligible_parents.append(parent)

    capability = _capability(
        bool(eligible_parents),
        "available" if eligible_parents else "parent_setup_unavailable",
        "存在仍在三根 bar 观察窗口内的冻结 S4/S5 突破父 setup。"
        if eligible_parents
        else "假突破只关联已记录且仍在观察窗口内的 S4/S5 setup。",
        symbol=symbol,
        strategy="s7_failed_breakout",
    )
    capability["task_name"] = task_name
    if not eligible_parents:
        return capability, []

    transitions: list[dict[str, Any]] = []
    for parent in eligible_parents:
        frozen = _mapping(parent.get("frozen_evidence"))
        started_at = _parse_time(parent.get("started_at"))
        bars_after_start = (
            int((current_cutoff - started_at).total_seconds() // _FIVE_MINUTES.total_seconds())
            if started_at is not None
            else 0
        )
        range_high = _number(
            frozen.get("opening_range_high", frozen.get("range_high"))
        )
        range_low = _number(frozen.get("opening_range_low", frozen.get("range_low")))
        buffer = _number(frozen.get("buffer"))
        if range_high is None or range_low is None or buffer is None:
            continue
        bullish_parent = parent.get("direction") == "bullish"
        returned_inside = (
            _strictly_less(current_close, range_high - buffer)
            if bullish_parent
            else _strictly_greater(current_close, range_low + buffer)
        )
        if not returned_inside:
            continue

        parent_id = str(parent.get("setup_id") or "")
        evidence = {
            "parent_setup_id": parent_id,
            "parent_strategy": parent.get("strategy"),
            "parent_setup_type": parent.get("setup_type"),
            "parent_direction": parent.get("direction"),
            "range_high": range_high,
            "range_low": range_low,
            "buffer": buffer,
            "close": current_close,
            "bars_after_breakout": bars_after_start,
        }
        parent["status"] = "invalidated"
        parent["transition_seq"] = int(parent.get("transition_seq", 0)) + 1
        parent["last_cutoff"] = _iso(current_cutoff)
        parent["last_bar_end_at"] = _iso(current_end)
        parent["bars_elapsed"] = int(parent.get("bars_elapsed", 1)) + 1
        transitions.append(
            _transition(
                parent,
                transition="setup_invalidated",
                status="invalidated",
                reason_code="failed_breakout",
                source_context=source_context,
                evidence=evidence,
            )
        )

        direction = "bearish" if bullish_parent else "bullish"
        rule_version = _strategy_rule_version(spec, "s7")
        setup_id = _setup_id(
            task_name,
            symbol,
            "S7",
            rule_version,
            "failed_breakout",
            _start_evidence_key(source_context, current_cutoff) + ":" + parent_id,
            trading_date=str(source_context.get("trading_date") or ""),
            timeframe=str(spec.get("timeframe", _DEFAULT_TIMEFRAME)),
        )
        child = {
            "setup_id": setup_id,
            "task_name": task_name,
            "symbol": symbol,
            "strategy": "S7",
            "setup_type": "failed_breakout",
            "direction": direction,
            "parent_setup_id": parent_id,
            "timeframe": spec.get("timeframe", _DEFAULT_TIMEFRAME),
            "rule_version": rule_version,
            "trading_date": str(source_context.get("trading_date") or ""),
            "status": "confirmed",
            "transition_seq": 1,
            "started_at": _iso(current_cutoff),
            "last_cutoff": _iso(current_cutoff),
            "bars_elapsed": 1,
            "candidate_cutoff": _iso(current_cutoff),
            "last_bar_end_at": _iso(current_end),
            "confirmation_level": None,
            "invalidation_level": None,
            "expires_at": _iso(
                _expiry_at(
                    current_cutoff,
                    timedelta(days=1),
                    trading_date=str(source_context.get("trading_date") or ""),
                    spec=spec,
                )
            ),
            "cooldown_until": _iso(
                current_cutoff + timedelta(minutes=int(spec.get("cooldown_minutes", 15)))
            ),
            "frozen_evidence": evidence,
        }
        state_setups.append(child)
        transitions.append(
            _transition(
                child,
                transition="setup_confirmed",
                status="confirmed",
                reason_code="failed_breakout",
                source_context=source_context,
                evidence=evidence,
            )
        )
    return capability, transitions


def _evaluate_setup_exhaustion(
    *,
    symbol: str,
    bars: Sequence[Mapping[str, Any]],
    current_cutoff: datetime | None,
    task_name: str,
    source_context: Mapping[str, Any],
    state: dict[str, Any],
    state_setups: list[dict[str, Any]],
    spec: Mapping[str, Any],
    freshness_reason: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if freshness_reason:
        capability = _capability(
            False,
            _reason_code_from_message(freshness_reason),
            freshness_reason,
            symbol=symbol,
            strategy="s8_simplified_setup",
        )
        capability["task_name"] = task_name
        return capability, []
    comparison_bars_ago = int(_mapping(spec.get("s8")).get("comparison_bars_ago", 4))
    if current_cutoff is None or len(bars) <= comparison_bars_ago:
        capability = _capability(
            False,
            "comparison_history_incomplete",
            "简化九转需要当前及此前四根连续完整的 5 分钟 bar。",
            symbol=symbol,
            strategy="s8_simplified_setup",
        )
        capability["task_name"] = task_name
        return capability, []

    capability = _capability(
        True,
        "available",
        "简化九转只依赖连续收盘价比较，不需要 ATR、RVOL 或 MACD 预热。",
        symbol=symbol,
        strategy="s8_simplified_setup",
    )
    capability["task_name"] = task_name
    threshold = int(_mapping(spec.get("s8")).get("count_threshold", 9))
    rule_version = _strategy_rule_version(spec, "s8")
    counts = setup_exhaustion_streak(
        bars,
        comparison_bars_ago=comparison_bars_ago,
    )
    direction = next(
        (
            value
            for value in ("bullish", "bearish")
            if int(_mapping(counts.get(value)).get("count", 0)) > 0
        ),
        "",
    )
    count = int(_mapping(counts.get(direction)).get("count", 0)) if direction else 0
    started_at = _mapping(counts.get(direction)).get("started_at") if direction else None
    sequence_started_at = _iso(started_at) if isinstance(started_at, datetime) else None
    sequence_key = f"{task_name}:{symbol}"
    sequences = dict(state.get("structure_sequences") or {})
    previous_sequence = _mapping(sequences.get(sequence_key))
    same_session = (
        str(previous_sequence.get("trading_date") or "")
        == str(source_context.get("trading_date") or "")
        and str(previous_sequence.get("rule_version") or "") == rule_version
        and str(previous_sequence.get("direction") or "") == direction
    )
    same_sequence = (
        same_session
        and sequence_started_at is not None
        and previous_sequence.get("sequence_started_at") == sequence_started_at
    )
    already_emitted = same_sequence and previous_sequence.get("emitted") is True
    sequences[sequence_key] = {
        "trading_date": str(source_context.get("trading_date") or ""),
        "rule_version": rule_version,
        "direction": direction,
        "count": count,
        "emitted": already_emitted,
        "last_cutoff": _iso(current_cutoff),
        "sequence_started_at": sequence_started_at,
    }
    state["structure_sequences"] = sequences
    if count < threshold or already_emitted:
        return capability, []

    setup_type = "simplified_setup_exhaustion"
    setup_id = _setup_id(
        task_name,
        symbol,
        "S8",
        rule_version,
        setup_type,
        _start_evidence_key(source_context, current_cutoff),
        trading_date=str(source_context.get("trading_date") or ""),
        timeframe=str(spec.get("timeframe", _DEFAULT_TIMEFRAME)),
    )
    evidence = {
        "count": count,
        "threshold": threshold,
        "direction": direction,
        "comparison": "close_vs_four_bars_ago",
        "comparison_bars_ago": comparison_bars_ago,
        "sequence_started_at": sequence_started_at,
        "interpretation": "衰竭观察，不代表反转确认。",
    }
    setup = {
        "setup_id": setup_id,
        "task_name": task_name,
        "symbol": symbol,
        "strategy": "S8",
        "setup_type": setup_type,
        "direction": direction,
        "timeframe": spec.get("timeframe", _DEFAULT_TIMEFRAME),
        "rule_version": rule_version,
        "trading_date": str(source_context.get("trading_date") or ""),
        "status": "confirmed",
        "transition_seq": 1,
        "started_at": _iso(current_cutoff),
        "last_cutoff": _iso(current_cutoff),
        "bars_elapsed": count,
        "confirmation_level": None,
        "invalidation_level": None,
        "expires_at": _iso(
            _expiry_at(
                current_cutoff,
                timedelta(days=1),
                trading_date=str(source_context.get("trading_date") or ""),
                spec=spec,
            )
        ),
        "cooldown_until": None,
        "frozen_evidence": evidence,
    }
    state_setups.append(setup)
    sequences[sequence_key]["emitted"] = True
    return capability, [
        _transition(
            setup,
            transition="setup_confirmed",
            status="confirmed",
            reason_code=setup_type,
            source_context=source_context,
            evidence=evidence,
        )
    ]


def _impulse_inputs(
    features: Mapping[str, Any],
    bars: Sequence[Mapping[str, Any]],
    *,
    trading_date: str,
) -> dict[str, Any]:
    current_close = _number(bars[-1].get("close")) if bars else None
    previous_close = _number(bars[-2].get("close")) if len(bars) >= 2 else None
    atr_series = _wilder_atr_series(bars, period=14)
    previous_atr = atr_series[-2] if len(atr_series) >= 2 else None
    if current_close is None or previous_close is None:
        return {"available": False, "reason": "价格急跌需要当前和前一根完整 bar 收盘价。"}
    if previous_atr is None or previous_atr <= 0:
        return {"available": False, "reason": "价格急跌需要正值的前一根 Wilder ATR14。"}
    if bars and not _bars_are_adjacent(bars[-2:]):
        return {"available": False, "reason": "价格急跌需要相邻的完整 5 分钟 bar。"}
    rvol, volume_reason_code, volume_reason = _validated_rvol(
        features,
        bars,
        trading_date=trading_date,
    )
    return {
        "available": True,
        "close": current_close,
        "previous_close": previous_close,
        "previous_atr14": previous_atr,
        "rvol": rvol,
        "volume_impulse_available": rvol is not None,
        "volume_reason_code": volume_reason_code,
        "volume_reason": volume_reason,
        "high": _number(bars[-1].get("high")) if bars else None,
        "low": _number(bars[-1].get("low")) if bars else None,
        "bars": list(bars),
    }


def _validated_rvol(
    features: Mapping[str, Any],
    bars: Sequence[Mapping[str, Any]],
    *,
    trading_date: str,
) -> tuple[float | None, str, str]:
    context = _mapping(features.get("rvol_context"))
    if not context:
        return None, "rvol_context_unavailable", "缺少合格的时段 RVOL 历史基线。"

    valid_days = _integer(context.get("valid_days"), -1)
    baseline_volume = _number(context.get("baseline_median_volume"))
    baseline_date = str(context.get("as_of_session_date") or "")
    slot_start = str(context.get("slot_start") or "")
    quality_status = str(context.get("quality_status") or "")
    if valid_days < 10 or baseline_volume is None or baseline_volume <= 0:
        return None, "rvol_history_insufficient", "RVOL 至少需要 10 个有效日及正值的同时间段成交量中位数。"
    if quality_status != "ready":
        return None, "rvol_quality_unready", "RVOL 历史基线质量尚未就绪。"
    if not baseline_date or not trading_date or baseline_date >= trading_date:
        return None, "rvol_baseline_not_prior", "RVOL 基线必须早于当前交易日。"
    if not bars:
        return None, "rvol_current_bar_missing", "缺少当前完整 5 分钟 bar 成交量。"

    bar_start = _parse_time(bars[-1].get("bar_start_at"))
    if bar_start is None:
        return None, "rvol_slot_time_missing", "当前 bar 缺少有效开始时间。"
    eastern_slot = bar_start.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M")
    normalized_slot = slot_start.strip()
    if len(normalized_slot) == 5 and normalized_slot[2] == ":":
        pass
    else:
        slot_time = _parse_time(normalized_slot)
        if slot_time is not None:
            normalized_slot = slot_time.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M")
    if normalized_slot != eastern_slot:
        return None, "rvol_slot_mismatch", "RVOL 基线时段与当前 bar 开始时刻不匹配。"

    current_volume = _number(bars[-1].get("volume"))
    if current_volume is None or current_volume < 0:
        return None, "rvol_current_volume_invalid", "当前 5 分钟 bar 成交量无效。"
    calculated_rvol = current_volume / baseline_volume
    displayed_rvol = _number(features.get("rvol"))
    if displayed_rvol is not None and not math.isclose(
        displayed_rvol,
        calculated_rvol,
        rel_tol=0.02,
        abs_tol=1e-6,
    ):
        return None, "rvol_projection_mismatch", "P3 RVOL 展示值与合格历史基线计算结果不一致。"
    return calculated_rvol, "available", "RVOL 基线和当前 bar 量能均通过质量检查。"


def _evaluate_impulse(
    *,
    symbol: str,
    values: Mapping[str, Any],
    current_cutoff: datetime | None,
    task_name: str,
    source_context: Mapping[str, Any],
    state_setups: list[dict[str, Any]],
    spec: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if current_cutoff is None:
        return []
    current_close = float(values["close"])
    previous_close = float(values["previous_close"])
    previous_atr = float(values["previous_atr14"])
    price_delta = current_close - previous_close
    threshold = float(_mapping(spec.get("s3")).get("price_shock_atr_multiple", 2.0)) * previous_atr
    if _strictly_less(price_delta, -threshold):
        setup_type = "price_anomaly_drop"
        trading_date = str(source_context.get("trading_date") or "")
        rule_version = _strategy_rule_version(spec, "s3")
        setup_id = _setup_id(
            task_name,
            symbol,
            "S3",
            rule_version,
            setup_type,
            _start_evidence_key(source_context, current_cutoff),
            trading_date=trading_date,
            timeframe=str(spec.get("timeframe", _DEFAULT_TIMEFRAME)),
        )
        if _find_setup(state_setups, setup_id) is not None:
            return []
        setup = {
            "setup_id": setup_id,
            "task_name": task_name,
            "symbol": symbol,
            "strategy": "S3",
            "setup_type": setup_type,
            "timeframe": spec.get("timeframe", _DEFAULT_TIMEFRAME),
            "rule_version": rule_version,
            "trading_date": str(source_context.get("trading_date") or ""),
            "status": "confirmed",
            "transition_seq": 1,
            "started_at": _iso(current_cutoff),
            "last_cutoff": _iso(current_cutoff),
            "bars_elapsed": 0,
            "confirmation_level": None,
            "invalidation_level": None,
            "expires_at": _iso(
                _expiry_at(
                    current_cutoff,
                    timedelta(minutes=30),
                    trading_date=trading_date,
                    spec=spec,
                )
            ),
            "cooldown_until": None,
            "frozen_evidence": {
                "previous_close": previous_close,
                "close": current_close,
                "price_delta": price_delta,
                "previous_atr14": previous_atr,
                "threshold": -threshold,
            },
        }
        state_setups.append(setup)
        return [
            _transition(
                setup,
                transition="price_anomaly_confirmed",
                status="confirmed",
                reason_code="price_drop_atr_threshold",
                source_context=source_context,
                evidence=setup["frozen_evidence"],
            )
        ]

    transitions: list[dict[str, Any]] = []
    rvol = _number(values.get("rvol"))
    if rvol is None:
        return transitions
    atr_multiple = float(_mapping(spec.get("s3")).get("impulse_atr_multiple", 1.2))
    volume_minimum = float(_mapping(spec.get("s3")).get("rvol_min", 1.8))
    high = _number(values.get("high"))
    low = _number(values.get("low"))
    if high is None or low is None or high <= low:
        return transitions
    position = (current_close - low) / (high - low)
    if (
        _strictly_greater(price_delta, atr_multiple * previous_atr)
        and rvol >= volume_minimum
        and position >= 0.75
    ):
        setup_type = "volume_impulse_up"
    elif (
        _strictly_less(price_delta, -atr_multiple * previous_atr)
        and rvol >= volume_minimum
        and position <= 0.25
    ):
        setup_type = "volume_impulse_down"
    else:
        return transitions
    trading_date = str(source_context.get("trading_date") or "")
    rule_version = _strategy_rule_version(spec, "s3")
    setup_id = _setup_id(
        task_name,
        symbol,
        "S3",
        rule_version,
        setup_type,
        _start_evidence_key(source_context, current_cutoff),
        trading_date=trading_date,
        timeframe=str(spec.get("timeframe", _DEFAULT_TIMEFRAME)),
    )
    if _find_setup(state_setups, setup_id) is not None:
        return transitions
    setup = {
        "setup_id": setup_id,
        "task_name": task_name,
        "symbol": symbol,
        "strategy": "S3",
        "setup_type": setup_type,
        "timeframe": spec.get("timeframe", _DEFAULT_TIMEFRAME),
        "rule_version": rule_version,
        "trading_date": str(source_context.get("trading_date") or ""),
        "status": "confirmed",
        "transition_seq": 1,
        "started_at": _iso(current_cutoff),
        "last_cutoff": _iso(current_cutoff),
        "bars_elapsed": 0,
        "confirmation_level": None,
        "invalidation_level": None,
        "expires_at": _iso(
            _expiry_at(
                current_cutoff,
                timedelta(minutes=30),
                trading_date=trading_date,
                spec=spec,
            )
        ),
        "cooldown_until": None,
        "frozen_evidence": {
            "price_delta": price_delta,
            "previous_atr14": previous_atr,
            "rvol": rvol,
            "close_position": position,
        },
    }
    state_setups.append(setup)
    transitions.append(
        _transition(
            setup,
            transition="volume_impulse_confirmed",
            status="confirmed",
            reason_code=setup_type,
            source_context=source_context,
            evidence=setup["frozen_evidence"],
        )
    )
    return transitions


def _expire_gap_candidates(
    setups: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    *,
    evaluated_at: datetime,
    source_context: Mapping[str, Any],
    unavailable_scopes: set[tuple[str, str]] | None = None,
    force_gap: bool = False,
) -> None:
    current_cutoff = source_context.get("scheduled_at")
    if not isinstance(current_cutoff, datetime):
        return
    task_name = str(source_context.get("task_name") or "")
    for setup in setups:
        if setup.get("status") != "candidate":
            continue
        if task_name and setup.get("task_name") != task_name:
            continue
        previous_cutoff = _parse_time(setup.get("last_cutoff"))
        if previous_cutoff is None or current_cutoff <= previous_cutoff:
            continue
        scope = (
            str(setup.get("strategy") or "").upper(),
            str(setup.get("symbol") or "").upper(),
        )
        has_gap = current_cutoff - previous_cutoff > _FIVE_MINUTES
        if not force_gap and not has_gap and scope not in (unavailable_scopes or set()):
            continue
        setup["status"] = "expired"
        setup["transition_seq"] = int(setup.get("transition_seq", 0)) + 1
        setup["last_cutoff"] = _iso(current_cutoff)
        transitions.append(
            _transition(
                setup,
                transition="setup_expired",
                status="expired",
                reason_code="coverage_gap",
                source_context=source_context,
                evidence={"prior_cutoff": _iso(previous_cutoff), "evaluated_at": _iso(evaluated_at)},
            )
        )


def _expire_timed_setups(
    setups: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    *,
    evaluated_at: datetime,
    source_context: Mapping[str, Any],
    spec: Mapping[str, Any],
) -> None:
    task_name = str(source_context.get("task_name") or "")
    current_date = str(source_context.get("trading_date") or "")
    for setup in setups:
        if setup.get("status") not in {"candidate", "confirmed"}:
            continue
        if task_name and setup.get("task_name") != task_name:
            continue
        expires_at = _parse_time(setup.get("expires_at"))
        setup_trading_date = str(setup.get("trading_date") or "")
        date_changed = bool(
            current_date and setup_trading_date and current_date != setup_trading_date
        )
        session_close = None
        if spec.get("expire_at_session_close") is True and setup_trading_date:
            session_close = _session_close_utc(
                setup_trading_date,
                calendar_name=str(spec.get("market_calendar") or "XNYS"),
            )
        session_closed = session_close is not None and evaluated_at >= session_close
        ttl_expired = expires_at is not None and evaluated_at >= expires_at
        if not date_changed and not session_closed and not ttl_expired:
            continue
        setup["status"] = "expired"
        setup["transition_seq"] = int(setup.get("transition_seq", 0)) + 1
        transitions.append(
            _transition(
                setup,
                transition="setup_expired",
                status="expired",
                reason_code=(
                    "session_expired"
                    if date_changed
                    else "session_close"
                    if session_closed
                    else "setup_ttl"
                ),
                source_context=source_context,
                evidence={
                    "expires_at": _iso(expires_at),
                    "session_close": _iso(session_close),
                    "evaluated_at": _iso(evaluated_at),
                },
            )
        )


def _history_slot(
    *,
    task_name: str,
    cutoff: datetime | None,
    symbols: Mapping[str, Any],
) -> dict[str, Any] | None:
    if cutoff is None:
        return None
    projected_symbols: dict[str, Any] = {}
    for symbol, raw_item in symbols.items():
        item = _mapping(raw_item)
        projected_symbols[str(symbol).strip().upper()] = {
            "features": {
                "rs15": _number(_mapping(item.get("features")).get("rs15")),
                "rs30": _number(_mapping(item.get("features")).get("rs30")),
            }
        }
    return {"task_name": task_name, "cutoff": _iso(cutoff), "symbols": projected_symbols}


def _append_history(history: list[dict[str, Any]], current: Mapping[str, Any]) -> None:
    cutoff = _parse_time(current.get("cutoff"))
    task_name = str(current.get("task_name") or "")
    if cutoff is None:
        return
    for index, item in enumerate(history):
        if _parse_time(item.get("cutoff")) == cutoff and str(item.get("task_name") or "") == task_name:
            history[index] = dict(current)
            return
    history.append(dict(current))
    history.sort(key=lambda item: (_parse_time(item.get("cutoff")) or datetime.min.replace(tzinfo=UTC), str(item.get("task_name") or "")))


def _find_history_slot(
    history: Sequence[Mapping[str, Any]],
    cutoff: datetime,
    task_name: str,
    symbol: str,
) -> Mapping[str, Any] | None:
    for item in reversed(history):
        if _parse_time(item.get("cutoff")) != cutoff:
            continue
        if str(item.get("task_name") or task_name) != task_name:
            continue
        if symbol in _mapping(item.get("symbols")):
            return item
    return None


def _symbol_freshness_reason(
    *,
    item_as_of: datetime | None,
    current_cutoff: datetime | None,
    expected_latest_bar_end: datetime | None,
    evaluated_at: datetime,
    max_age_seconds: int,
    status: str,
) -> str | None:
    if status in {"unavailable", "quote_only", "not_found", "invalid_source"}:
        return f"标的状态 {status} 不具备可用的分钟证据。"
    if item_as_of is None:
        return "缺少标的 as_of，无法判断分钟证据新鲜度。"
    age = (evaluated_at - item_as_of).total_seconds()
    if age < 0:
        return "标的 as_of 晚于 evaluated_at，证据时间顺序异常。"
    if max_age_seconds and age > max_age_seconds:
        return "标的分钟证据已过期。"
    if current_cutoff is not None and item_as_of > current_cutoff:
        return "标的 as_of 晚于当前 source cutoff。"
    if expected_latest_bar_end is not None and item_as_of != expected_latest_bar_end:
        return "标的 as_of 未覆盖当前预期的完整 5 分钟区间。"
    return None


def _expiry_at(
    started_at: datetime,
    duration: timedelta,
    *,
    trading_date: str,
    spec: Mapping[str, Any],
) -> datetime:
    natural_expiry = started_at + duration
    if spec.get("expire_at_session_close") is not True:
        return natural_expiry
    session_close = _session_close_utc(
        trading_date,
        calendar_name=str(spec.get("market_calendar") or "XNYS"),
    )
    if session_close is None:
        return natural_expiry
    return min(natural_expiry, session_close)


def _session_close_utc(trading_date: str, *, calendar_name: str) -> datetime | None:
    try:
        session_date = date.fromisoformat(str(trading_date))
        import exchange_calendars as xcals

        calendar = xcals.get_calendar(calendar_name)
        if not calendar.is_session(session_date):
            return None
        close = calendar.session_close(session_date)
        if hasattr(close, "to_pydatetime"):
            close = close.to_pydatetime()
        return _as_utc(close)
    except (TypeError, ValueError, KeyError, ImportError):
        return None


def _session_open_utc(trading_date: str, *, calendar_name: str) -> datetime | None:
    try:
        session_date = date.fromisoformat(str(trading_date))
        import exchange_calendars as xcals

        calendar = xcals.get_calendar(calendar_name)
        if not calendar.is_session(session_date):
            return None
        opening = calendar.session_open(session_date)
        if hasattr(opening, "to_pydatetime"):
            opening = opening.to_pydatetime()
        return _as_utc(opening)
    except (TypeError, ValueError, KeyError, ImportError):
        return None


def _valid_bars(value: object, *, cutoff: datetime | None) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    bars: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping) or item.get("quality_state", "ready") != "ready":
            continue
        start = _parse_time(item.get("bar_start_at"))
        end = _parse_time(item.get("bar_end_at"))
        prices = [_number(item.get(key)) for key in ("open", "high", "low", "close")]
        volume = _number(item.get("volume"))
        if start is None or end is None or end - start != _FIVE_MINUTES:
            continue
        if cutoff is not None and end > cutoff:
            continue
        if any(value is None for value in prices) or volume is None or volume < 0:
            continue
        open_price, high, low, close = (float(value) for value in prices if value is not None)
        if low <= 0 or low > min(open_price, close) or high < max(open_price, close):
            continue
        bars.append(dict(item))
    bars.sort(key=lambda item: _parse_time(item.get("bar_start_at")) or datetime.min.replace(tzinfo=UTC))
    return _continuous_suffix(bars)


def _bars_are_adjacent(bars: Sequence[Mapping[str, Any]]) -> bool:
    if len(bars) < 2:
        return False
    previous_end = _parse_time(bars[-2].get("bar_end_at"))
    current_start = _parse_time(bars[-1].get("bar_start_at"))
    return previous_end is not None and previous_end == current_start


def _strictly_greater(value: float, boundary: float) -> bool:
    return value > boundary and not math.isclose(value, boundary, rel_tol=1e-12, abs_tol=1e-12)


def _strictly_less(value: float, boundary: float) -> bool:
    return value < boundary and not math.isclose(value, boundary, rel_tol=1e-12, abs_tol=1e-12)


def _within_cooldown(
    setups: Sequence[Mapping[str, Any]],
    task_name: str,
    symbol: str,
    cutoff: datetime,
    spec: Mapping[str, Any],
    *,
    strategy: str,
    setup_type: str,
    direction: str,
    rule_version: str,
) -> bool:
    latest_start: datetime | None = None
    for setup in setups:
        if setup.get("task_name") != task_name or setup.get("symbol") != symbol:
            continue
        if str(setup.get("strategy") or "").upper() != strategy.upper():
            continue
        if str(setup.get("setup_type") or "") != setup_type:
            continue
        if str(setup.get("direction") or _ma5_direction(str(setup_type))) != direction:
            continue
        if str(setup.get("rule_version") or "") != rule_version:
            continue
        if setup.get("status") not in {"candidate", "confirmed", "invalidated"}:
            continue
        started = _parse_time(setup.get("started_at"))
        if started is not None and (latest_start is None or started > latest_start):
            latest_start = started
    if latest_start is None:
        return False
    cooldown = timedelta(minutes=int(spec.get("cooldown_minutes", 15)))
    cooldown_until = latest_start + cooldown
    return cutoff < cooldown_until


def _ma5_direction(setup_type: str) -> str:
    if setup_type in {"ma5_reclaim", "ma5_retest_hold"}:
        return "bullish"
    if setup_type in {"ma5_break", "ma5_failed_reclaim"}:
        return "bearish"
    return "risk"


def _active_setup(
    setups: Sequence[Mapping[str, Any]], task_name: str, symbol: str, strategy: str
) -> dict[str, Any] | None:
    for setup in reversed(setups):
        if (
            setup.get("task_name") == task_name
            and setup.get("symbol") == symbol
            and str(setup.get("strategy") or "").lower() == strategy.lower()
            and setup.get("status") in {"candidate", "confirmed"}
        ):
            return setup if isinstance(setup, dict) else dict(setup)
    return None


def _find_setup(setups: Sequence[Mapping[str, Any]], setup_id: str) -> dict[str, Any] | None:
    for setup in setups:
        if setup.get("setup_id") == setup_id:
            return setup if isinstance(setup, dict) else dict(setup)
    return None


def _find_setup_scope(
    setups: Sequence[Mapping[str, Any]],
    *,
    task_name: str,
    symbol: str,
    strategy: str,
    setup_type: str,
    trading_date: str,
    rule_version: str,
) -> dict[str, Any] | None:
    for setup in reversed(setups):
        if (
            setup.get("task_name") == task_name
            and setup.get("symbol") == symbol
            and str(setup.get("strategy") or "").upper() == strategy.upper()
            and setup.get("setup_type") == setup_type
            and str(setup.get("trading_date") or "") == trading_date
            and setup.get("rule_version") == rule_version
            and setup.get("status") in {"candidate", "confirmed"}
        ):
            return setup if isinstance(setup, dict) else dict(setup)
    return None


def _strategy_rule_version(spec: Mapping[str, Any], strategy: str) -> str:
    if strategy == "s2":
        prefix = "atr_price_buffer_v1"
    elif strategy == "s8":
        return str(_mapping(spec.get("s8")).get("rule_version") or "simplified_setup_session_v1")
    else:
        prefix = str(spec.get("rule_version", _DEFAULT_RULE_VERSION))
    payload = {
        "rule_version": spec.get("rule_version", _DEFAULT_RULE_VERSION),
        "timeframe": spec.get("timeframe", _DEFAULT_TIMEFRAME),
        strategy: spec.get(strategy, {}),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return f"{prefix}:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:12]}"


def _start_evidence_key(source_context: Mapping[str, Any], cutoff: datetime) -> str:
    return ":".join(
        (
            _iso(cutoff) or "",
            str(source_context.get("capture_id") or ""),
            str(source_context.get("content_sha256") or ""),
        )
    )


def _setup_id(
    task_name: str,
    symbol: str,
    strategy: str,
    rule_version: str,
    setup_type: str,
    start_key: str = "",
    *,
    trading_date: str,
    timeframe: str,
) -> str:
    identity = {
        "task_name": task_name,
        "symbol": symbol,
        "strategy": strategy,
        "timeframe": timeframe,
        "trading_date": trading_date,
        "rule_version": rule_version,
        "setup_type": setup_type,
        "start_evidence": start_key,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f"{task_name}:{symbol}:{strategy}:{trading_date}:{digest}"


def _transition(
    setup: Mapping[str, Any],
    *,
    transition: str,
    status: str,
    reason_code: str,
    source_context: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    cutoff = source_context.get("scheduled_at")
    return {
        "setup_id": str(setup.get("setup_id") or ""),
        "transition_seq": int(setup.get("transition_seq", 0)),
        "task_name": str(setup.get("task_name") or source_context.get("task_name") or ""),
        "symbol": str(setup.get("symbol") or ""),
        "strategy": str(setup.get("strategy") or ""),
        "strategy_id": (
            f"{str(setup.get('strategy') or '').lower()}_{str(setup.get('setup_type') or '')}"
        ),
        "setup_type": str(setup.get("setup_type") or ""),
        "direction": str(setup.get("direction") or ""),
        "parent_setup_id": str(setup.get("parent_setup_id") or "") or None,
        "transition": transition,
        "status": status,
        "timeframe": str(setup.get("timeframe") or _DEFAULT_TIMEFRAME),
        "rule_version": str(setup.get("rule_version") or _DEFAULT_RULE_VERSION),
        "trading_date": str(setup.get("trading_date") or source_context.get("trading_date") or ""),
        "evidence_cutoff": _iso(cutoff) if isinstance(cutoff, datetime) else str(cutoff or ""),
        "evaluated_at": _iso(source_context.get("evaluated_at")) if isinstance(source_context.get("evaluated_at"), datetime) else None,
        "source_captured_at": _iso(source_context.get("captured_at")) if isinstance(source_context.get("captured_at"), datetime) else None,
        "expires_at": setup.get("expires_at"),
        "source_capture_id": str(source_context.get("capture_id") or ""),
        "source_content_sha256": str(source_context.get("content_sha256") or ""),
        "reason_code": reason_code,
        "message": _reason_message(reason_code),
        "evidence": dict(evidence),
        "confirmation_level": setup.get("confirmation_level"),
        "invalidation_level": setup.get("invalidation_level"),
    }


def _capability(
    available: bool,
    reason: str,
    message: str,
    *,
    symbol: str,
    strategy: str,
) -> dict[str, Any]:
    capability_ids = {
        "s1_relative_strength": ("S1", "s1_relative_strength"),
        "s2_ma5": ("S2", "s2_ma5"),
        "s3_impulse": ("S3", "s3_impulse"),
        "s3_volume_impulse": ("S3", "s3_volume_impulse"),
        "s4_orb": ("S4", "s4_orb"),
        "s5_range_breakout": ("S5", "s5_range_breakout"),
        "s6_reversal": ("S6", "s6_reversal"),
        "s7_failed_breakout": ("S7", "s7_failed_breakout"),
        "s8_simplified_setup": ("S8", "s8_simplified_setup"),
    }
    canonical_strategy, strategy_id = capability_ids.get(
        strategy.lower(), (strategy.upper(), strategy.lower())
    )
    return {
        "available": available,
        "reason_code": reason,
        "message": message,
        "symbol": symbol,
        "strategy": canonical_strategy,
        "strategy_id": strategy_id,
    }


def _reason_message(reason_code: str) -> str:
    messages = {
        "coverage_gap": "数据覆盖出现缺口，候选已过期；不将覆盖缺口解释为策略失败。",
        "price_drop_atr_threshold": "完整 5 分钟 bar 的收盘跌幅超过前一根 Wilder ATR14 的策略阈值。",
        "ma5_reclaim": "价格站回 MA5 缓冲上方，等待后续 bar 确认。",
        "ma5_breakdown": "价格跌破 MA5 缓冲下方，等待后续 bar 确认。",
        "orb_breakout": "完整 bar 突破冻结的 09:30–09:45 开盘区间，等待后续确认。",
        "range_breakout": "压缩区间出现波幅扩张并突破冻结边界，等待后续确认。",
        "v_reversal": "有序下行结构后收复 MA5，记录 V 形回升候选。",
        "a_reversal": "有序上行结构后失守 MA5，记录 A 形回落候选。",
        "failed_breakout": "已记录的 S4/S5 突破在观察窗口内收回冻结区间。",
        "simplified_setup_exhaustion": "当日简化九转达到九次；这是衰竭观察，不代表反转确认。",
        "next_bar_held": "后续完整 bar 守住冻结确认位，setup 已确认。",
        "frozen_level_invalidated": "完整 bar 穿过冻结失效位，setup 已失效。",
        "minute_timestamp_boundary_unverified": "分钟线时间边界未经验证，不能生成正式事件。",
        "candidate_ttl": "候选超过允许的完整 bar 数，已过期。",
        "session_expired": "交易日已结束，setup 已过期。",
        "setup_ttl": "setup 已超过其有效时间。",
    }
    return messages.get(reason_code, reason_code.replace("_", " "))


def _reason_code_from_message(message: str | None) -> str:
    value = (message or "unavailable").lower()
    if "atr" in value:
        return "atr_unavailable"
    if "过期" in value or "fresh" in value:
        return "stale_evidence"
    if "边界" in value or "boundary" in value:
        return "minute_timestamp_boundary_unverified"
    if "连续" in value or "完整" in value:
        return "incomplete_bars"
    if "rs" in value or "基准" in value or "benchmark" in value:
        return "relative_strength_unavailable"
    return "evidence_unavailable"


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object, default: str) -> str:
    text = str(value or "").strip()
    return text or default


def _number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _positive_number(value: object, default: float) -> float:
    number = _number(value)
    return number if number is not None and number > 0 else default


def _nonnegative_number(value: object, default: float) -> float:
    number = _number(value)
    return number if number is not None and number >= 0 else default


def _integer(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_time(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("evaluated_at must include a timezone")
    return value.astimezone(UTC)


def _projection_cutoff(projection: Mapping[str, Any]) -> datetime | None:
    freshness = _mapping(projection.get("freshness"))
    return _parse_time(freshness.get("as_of"))


def _slot_key(cutoff: datetime | None, task_name: str) -> tuple[datetime, str] | None:
    return (cutoff, task_name) if cutoff is not None else None


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None
