from __future__ import annotations

from math import fsum, isfinite, log, sqrt
from statistics import median, pstdev
from typing import Any

from app.analytics.technical import calculate_relative_strength, calculate_technical_indicators
from app.decision_harness.contracts import FEATURE_VERSION, content_sha256
from app.decision_harness.group_phase import evaluate_group_phase, sample_support
from app.decision_harness.setup_projection import project_symbol_setups


GROUP_SNAPSHOT_SCHEMA = "urus.group_daily_snapshot.v4"


def build_group_snapshot(
    group: dict[str, Any],
    evidence: dict[str, Any],
    *,
    previous_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    dataset = evidence["dataset"]
    if dataset.get("feature_version") != FEATURE_VERSION:
        raise ValueError(
            "组快照要求与当前计算实现绑定的 Daily Evidence feature version。"
        )
    chart = evidence["chart"]
    instruments = chart.get("instruments") or {}
    symbols = list(group.get("symbols") or [])
    benchmark_symbols = list(group.get("benchmark_symbols") or [])
    benchmark_symbol = next(
        (
            symbol
            for symbol in benchmark_symbols
            if instruments.get(symbol)
            and (instruments.get(symbol).get("quality") or {}).get("status") == "ok"
        ),
        None,
    )
    benchmark = instruments.get(benchmark_symbol) if benchmark_symbol else None
    benchmark_bars = list((benchmark.get("price") or {}).get("bars") or []) if benchmark else []
    symbol_rows: list[dict[str, Any]] = []
    for symbol in symbols:
        instrument = instruments.get(symbol) or {}
        bars = list((instrument.get("price") or {}).get("bars") or [])
        quality = dict((instrument.get("quality") or {}))
        indicators = calculate_technical_indicators(bars, source="daily_bars")
        relative = (
            calculate_relative_strength(
                bars,
                list((benchmark.get("price") or {}).get("bars") or []),
                benchmark=benchmark_symbol or "",
                source="daily_bars",
            )
            if benchmark and benchmark_symbol
            else {"available": False, "excess_returns_percent": {}}
        )
        symbol_rows.append(
            _symbol_row(symbol, bars, quality, indicators, relative, benchmark_bars)
        )

    valid_rows = [row for row in symbol_rows if row["valid"]]
    features = _features(valid_rows, len(symbol_rows), benchmark_symbol)
    charts = _charts(symbol_rows, valid_rows, instruments, benchmark_symbol)
    group_strategy_decision_rows = [
        item
        for item in evidence.get("strategy_decisions", [])
        if str((item.get("scope") or {}).get("symbol") or item.get("symbol") or "") in symbols
    ]
    symbol_setups = project_symbol_setups(group_strategy_decision_rows)
    support = sample_support(len(valid_rows), len(symbol_rows))
    group_decision = evaluate_group_phase(
        features,
        (previous_snapshot or {}).get("features"),
        symbol_setups,
        support,
    )
    changes = _changes(previous_snapshot, features, group_decision)
    group_strategy_decisions = _group_strategy_decisions(group, features, group_decision)
    group_synthesis = _group_synthesis(
        dict(evidence.get("deterministic_synthesis") or {}),
        group,
        symbols,
    )
    payload = {
        "schema_version": GROUP_SNAPSHOT_SCHEMA,
        "feature_version": FEATURE_VERSION,
        "dataset_id": dataset["dataset_id"],
        "indicator_snapshot_ids": list(dataset.get("indicator_snapshot_ids") or []),
        "group": {
            "group_id": group["group_id"],
            "version_id": group["version_id"],
            "version": group["version"],
            "display_name": group["display_name"],
            "symbols": symbols,
            "benchmark_symbols": benchmark_symbols,
        },
        "trading_date": dataset["trading_date"],
        "quality": {
            "requested_symbol_count": len(symbols),
            "valid_symbol_count": len(valid_rows),
            "missing_symbol_count": len(symbols) - len(valid_rows),
            "status": (
                "ok"
                if symbol_rows and len(valid_rows) == len(symbol_rows)
                else "partial"
                if valid_rows
                else "missing"
            ),
            "warnings": [
                f"{row['symbol']}: {warning}"
                for row in symbol_rows
                for warning in row.get("warnings", [])
            ],
        },
        "features": features,
        "symbols": symbol_rows,
        "charts": charts,
        "group_decision": group_decision,
        "symbol_setups": symbol_setups,
        "group_strategy_decisions": group_strategy_decisions,
        "changes": changes,
        "strategy_decisions": group_strategy_decision_rows,
        "deterministic_synthesis": group_synthesis,
    }
    payload["content_sha256"] = content_sha256(
        {key: value for key, value in payload.items() if key != "content_sha256"}
    )
    return payload


def _group_synthesis(
    synthesis: dict[str, Any],
    group: dict[str, Any],
    symbols: list[str],
) -> dict[str, Any]:
    """Project a dataset synthesis onto one Observation Group."""

    if not synthesis:
        return {}
    available = dict(synthesis.get("symbol_summaries") or {})
    selected = {symbol: dict(available[symbol]) for symbol in symbols if symbol in available}
    if not available:
        # Historical synthesis payloads predate symbol summaries. Preserve
        # them rather than pretending the dataset-level vote is group-local.
        return synthesis

    bullish_count = sum(int(item.get("bullish_count") or 0) for item in selected.values())
    bearish_count = sum(int(item.get("bearish_count") or 0) for item in selected.values())
    neutral_count = sum(int(item.get("neutral_count") or 0) for item in selected.values())
    not_applicable_count = sum(
        int(item.get("not_applicable_count") or 0) for item in selected.values()
    )
    error_count = sum(int(item.get("error_count") or 0) for item in selected.values())
    states = {str(item.get("consensus_state") or "no_signal") for item in selected.values()}
    actions = {str(item.get("suggested_action") or "no_action") for item in selected.values()}
    if states == {"insufficient_data"} and not (bullish_count or bearish_count or neutral_count):
        consensus_state, suggested_action = "insufficient_data", "no_action"
    elif "conflicted" in states:
        consensus_state, suggested_action = "conflicted", "watch"
    elif states == {"aligned"} and actions == {"prioritize"}:
        consensus_state, suggested_action = "aligned", "prioritize"
    elif states == {"aligned"} and actions == {"avoid"}:
        consensus_state, suggested_action = "aligned", "avoid"
    elif states <= {"no_signal"}:
        consensus_state, suggested_action = "no_signal", "no_action"
    else:
        consensus_state, suggested_action = "mixed", "watch"

    projected = {
        key: value
        for key, value in synthesis.items()
        if key
        not in {
            "scope",
            "consensus_state",
            "suggested_action",
            "bullish_count",
            "bearish_count",
            "neutral_count",
            "not_applicable_count",
            "error_count",
            "strongest_supporting_strategy_ids",
            "strongest_conflicting_strategy_ids",
            "conflict_summary",
            "symbol_summaries",
            "content_sha256",
        }
    }
    projected.update(
        {
            "scope": {
                "scope_type": "group",
                "scope_id": group["group_id"],
                "scope_version": group.get("version"),
                "symbols": symbols,
            },
            "consensus_state": consensus_state,
            "suggested_action": suggested_action,
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "neutral_count": neutral_count,
            "not_applicable_count": not_applicable_count,
            "error_count": error_count,
            "strongest_supporting_strategy_ids": [
                decision_id
                for item in selected.values()
                for decision_id in item.get("strongest_supporting_strategy_ids") or []
            ],
            "strongest_conflicting_strategy_ids": [
                decision_id
                for item in selected.values()
                for decision_id in item.get("strongest_conflicting_strategy_ids") or []
            ],
            "conflict_summary": (
                "组内至少一个标的存在策略方向冲突。"
                if consensus_state == "conflicted"
                else "组内标的方向分化。"
                if consensus_state == "mixed" and bullish_count and bearish_count
                else "组级结论由组内标的的独立合成结果汇总。"
            ),
            "symbol_summaries": selected,
        }
    )
    projected["content_sha256"] = content_sha256(
        {
            key: value
            for key, value in projected.items()
            if key not in {"generated_at", "content_sha256"}
        }
    )
    return projected


def _symbol_row(
    symbol: str,
    bars: list[dict[str, Any]],
    quality: dict[str, Any],
    indicators: dict[str, Any],
    relative: dict[str, Any],
    benchmark_bars: list[dict[str, Any]],
) -> dict[str, Any]:
    closes = [_number(item.get("close")) for item in bars]
    closes = [value for value in closes if value is not None]
    latest = closes[-1] if closes else None
    averages = indicators.get("moving_average") or {}
    rsi = _metric(indicators.get("rsi14"))
    macd = _metric(indicators.get("macd_12_26_9"), "histogram")
    volume = indicators.get("volume_effort_result") or {}
    excess = (relative.get("excess_returns_percent") or {}) if isinstance(relative, dict) else {}
    return {
        "symbol": symbol,
        "valid": quality.get("status") == "ok" and bool(closes),
        "quality_status": quality.get("status", "missing"),
        "latest_close": latest,
        "returns_percent": {
            str(window): _lookback_return(closes, window)
            for window in (1, 5, 20, 60)
        },
        "trend": _trend_state(latest, averages),
        "rsi14": rsi,
        "macd_histogram": macd,
        "relative_excess_percent": {
            key: _number(value) for key, value in excess.items() if key in {"5d", "20d", "60d"}
        },
        "volume_ratio_20d": _number(volume.get("volume_ratio_20d")),
        "volume_signal": volume.get("signal"),
        "realized_volatility_20d_annualized": _metric(
            indicators.get("realized_volatility_20d")
        ),
        "tracking_error_20d_annualized": _tracking_error_20d(bars, benchmark_bars),
        "warnings": list(quality.get("warnings") or []),
    }


def _features(rows: list[dict[str, Any]], requested_count: int, benchmark: str | None) -> dict[str, Any]:
    def values(path: tuple[str, ...]) -> list[float]:
        result: list[float] = []
        for row in rows:
            value: Any = row
            for key in path:
                value = value.get(key) if isinstance(value, dict) else None
            if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value)):
                result.append(float(value))
        return result

    returns = {
        f"{window}d": _distribution(values(("returns_percent", str(window))))
        for window in (1, 5, 20, 60)
    }
    breadth = {
        f"above_ma{window}": _ratio(
            sum(
                1
                for row in rows
                if row.get("trend", {}).get(f"above_ma{window}") is True
            ),
            len(rows),
        )
        for window in (20, 50, 200)
    }
    rsi_values = values(("rsi14",))
    relative_20 = values(("relative_excess_percent", "20d"))
    volume_values = values(("volume_ratio_20d",))
    dispersion_values = values(("returns_percent", "1"))
    realized_volatility = values(("realized_volatility_20d_annualized",))
    tracking_error = values(("tracking_error_20d_annualized",))
    return {
        "valid_symbol_count": len(rows),
        "requested_symbol_count": requested_count,
        "missing_symbol_count": max(0, requested_count - len(rows)),
        "returns_percent": returns,
        "breadth": breadth,
        "rsi_distribution": _distribution(rsi_values),
        "rsi_extremes": {
            "oversold_percent": _ratio(sum(value <= 30 for value in rsi_values), len(rsi_values)),
            "overbought_percent": _ratio(sum(value >= 70 for value in rsi_values), len(rsi_values)),
        },
        "macd_positive_percent": _ratio(
            sum(value > 0 for value in values(("macd_histogram",))), len(rows)
        ),
        "volume_expansion_percent": _ratio(
            sum(value >= 1.2 for value in volume_values), len(volume_values)
        ),
        "relative_strength": {
            "benchmark": benchmark,
            "median_excess_20d": _round(median(relative_20)) if relative_20 else None,
            "positive_excess_20d_percent": _ratio(sum(value > 0 for value in relative_20), len(relative_20)),
        },
        "cross_sectional_dispersion_1d": _round(_stdev(dispersion_values)),
        "volatility": {
            "median_realized_volatility_20d_annualized": _round(median(realized_volatility))
            if realized_volatility
            else None,
            "median_tracking_error_20d_annualized": _round(median(tracking_error))
            if tracking_error
            else None,
            "tracking_error_source": "daily_log_return_difference",
        },
        "leaders": _ranked(rows, "20", reverse=True),
        "laggards": _ranked(rows, "20", reverse=False),
        "leader_concentration": _leader_concentration(rows),
    }


def _charts(
    rows: list[dict[str, Any]],
    valid_rows: list[dict[str, Any]],
    instruments: dict[str, Any],
    benchmark_symbol: str | None,
) -> dict[str, Any]:
    dates = _shared_window_dates(instruments, [row["symbol"] for row in valid_rows], 60)
    relative_series: list[dict[str, Any]] = []
    breadth_series: dict[str, list[dict[str, Any]]] = {key: [] for key in ("above_ma20", "above_ma50", "above_ma200")}
    dispersion_series: list[dict[str, Any]] = []
    benchmark = instruments.get(benchmark_symbol) if benchmark_symbol else None
    for date in dates:
        normalized: list[float] = []
        daily_returns: list[float] = []
        for row in valid_rows:
            bars = list((instruments[row["symbol"]].get("price") or {}).get("bars") or [])
            values = {str(item["date"]): _number(item.get("close")) for item in bars}
            close = values.get(date)
            if close is None:
                continue
            first = next((values.get(day) for day in dates if values.get(day) is not None), None)
            if first:
                normalized.append(close / first * 100)
            prior_date = dates[max(0, dates.index(date) - 1)]
            prior = values.get(prior_date)
            if prior:
                daily_returns.append((close / prior - 1) * 100)
        relative_series.append({"time": date, "value": _round(median(normalized)) if normalized else None})
        for window in (20, 50, 200):
            above = 0
            available = 0
            for row in valid_rows:
                bars = list((instruments[row["symbol"]].get("price") or {}).get("bars") or [])
                index = next((i for i, item in enumerate(bars) if str(item["date"]) == date), None)
                if index is None or index + 1 < window:
                    continue
                closes = [_number(item.get("close")) for item in bars[index - window + 1 : index + 1]]
                closes = [value for value in closes if value is not None]
                current = _number(bars[index].get("close"))
                if len(closes) == window and current is not None:
                    available += 1
                    above += current > sum(closes) / window
            breadth_series[f"above_ma{window}"].append({"time": date, "value": _ratio(above, available)})
        dispersion_series.append({"time": date, "value": _round(_stdev(daily_returns))})
    if benchmark:
        bars = list((benchmark.get("price") or {}).get("bars") or [])
        values = {str(item["date"]): _number(item.get("close")) for item in bars}
        first = next((values.get(day) for day in dates if values.get(day) is not None), None)
        for point in relative_series:
            close = values.get(point["time"])
            point["benchmark_value"] = _round(close / first * 100) if close is not None and first else None
    rotation = []
    for row in valid_rows:
        relative = row.get("relative_excess_percent") or {}
        x = _number(relative.get("20d"))
        y = None
        five = _number(relative.get("5d"))
        if five is not None and x is not None:
            y = five - x
        rotation.append({
            "symbol": row["symbol"],
            "x_relative_20d": x,
            "y_relative_change": _round(y),
            "size": _number(row.get("volume_ratio_20d")),
            "stance": "bullish" if (x or 0) > 0 else "bearish" if (x or 0) < 0 else "neutral",
            "trend": row.get("trend", {}).get("state"),
        })
    heatmap = [
        {
            "symbol": row["symbol"],
            "trend": row.get("trend", {}).get("state"),
            "momentum": "overbought" if (row.get("rsi14") or 50) >= 70 else "oversold" if (row.get("rsi14") or 50) <= 30 else "neutral",
            "volume": row.get("volume_signal"),
            "relative": "leading" if (_number((row.get("relative_excess_percent") or {}).get("20d")) or 0) > 0 else "lagging",
            "return_20d": row.get("returns_percent", {}).get("20"),
        }
        for row in sorted(valid_rows, key=lambda item: _sort_number(item.get("returns_percent", {}).get("20")), reverse=True)
    ]
    return {
        "relative_strength": {"benchmark": benchmark_symbol, "series": relative_series, "dispersion": dispersion_series},
        "breadth": {"series": breadth_series},
        "rotation": rotation,
        "heatmap": heatmap,
        "small_multiples": _small_multiples(valid_rows, instruments),
    }


def _group_strategy_decisions(
    group: dict[str, Any],
    features: dict[str, Any],
    group_decision: dict[str, Any],
) -> list[dict[str, Any]]:
    """Expose auditable group modules without making the group score a black box."""

    breadth = _number((features.get("breadth") or {}).get("above_ma20"))
    relative = _number((features.get("relative_strength") or {}).get("median_excess_20d"))
    concentration = _number(features.get("leader_concentration"))
    definitions = (
        (
            "group_breadth_regime_v1",
            breadth,
            "bullish" if breadth is not None and breadth >= 0.65 else "bearish" if breadth is not None and breadth < 0.35 else "neutral",
            "prioritize" if breadth is not None and breadth >= 0.65 else "avoid" if breadth is not None and breadth < 0.35 else "watch",
            "组内站上 MA20 的比例反映参与广度。",
        ),
        (
            "group_relative_strength_v1",
            relative,
            "bullish" if relative is not None and relative > 0 else "bearish" if relative is not None and relative < 0 else "neutral",
            "prioritize" if relative is not None and relative > 0 else "avoid" if relative is not None and relative < 0 else "watch",
            "组内 20D 中位数超额收益反映相对基准的改善或恶化。",
        ),
        (
            "group_leadership_concentration_v1",
            concentration,
            "neutral" if concentration is not None else "insufficient_data",
            "watch" if concentration is not None else "no_action",
            "前 1/3 标的的正收益贡献反映组内行情是否由少数标的支撑。",
        ),
    )
    results: list[dict[str, Any]] = []
    for name, metric, stance, action, explanation in definitions:
        status = "ok" if metric is not None else "not_applicable"
        body = {
            "schema_version": "urus.group_strategy_decision.v1",
            "scope": {
                "scope_type": "group",
                "scope_id": group["group_id"],
                "scope_version": group["version"],
            },
            "strategy": {
                "name": name,
                "version": "1.0.0",
                "implementation_sha256": content_sha256({"name": name, "version": "1.0.0"}),
            },
            "status": status,
            "stance": stance,
            "action": action,
            "score": _round(metric),
            "reasons": [{"code": name, "detail": explanation}],
            "metrics": {"value": _round(metric)},
            "group_decision_state": group_decision.get("state"),
        }
        digest = content_sha256(body)
        results.append(
            {
                "decision_id": f"{group['group_id']}:{name}:{digest[:16]}",
                **body,
                "content_sha256": digest,
            }
        )
    return results


def _changes(
    previous_snapshot: dict[str, Any] | None,
    features: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    previous_features = (previous_snapshot or {}).get("features") or {}
    previous_decision = (previous_snapshot or {}).get("group_decision") or {}
    current_returns = (features.get("returns_percent") or {}).get("20d") or {}
    previous_returns = (previous_features.get("returns_percent") or {}).get("20d") or {}
    current_relative = _number((features.get("relative_strength") or {}).get("median_excess_20d"))
    previous_relative = _number((previous_features.get("relative_strength") or {}).get("median_excess_20d"))
    current_breadth = _number((features.get("breadth") or {}).get("above_ma20"))
    previous_breadth = _number((previous_features.get("breadth") or {}).get("above_ma20"))
    current_leaders = {
        str(item.get("symbol")) for item in (features.get("leaders") or []) if item.get("symbol")
    }
    previous_leaders = {
        str(item.get("symbol")) for item in (previous_features.get("leaders") or []) if item.get("symbol")
    }
    return {
        "previous_trading_date": (previous_snapshot or {}).get("trading_date"),
        "group_state": {
            "from": previous_decision.get("state"),
            "to": decision.get("state"),
            "changed": previous_decision.get("state") not in {None, decision.get("state")},
        },
        "median_20d_delta_percent": _delta(current_returns.get("median"), previous_returns.get("median")),
        "breadth_ma20_delta": _delta(current_breadth, previous_breadth),
        "relative_20d_delta_percent": _delta(current_relative, previous_relative),
        "leaders_added": sorted(current_leaders - previous_leaders),
        "leaders_removed": sorted(previous_leaders - current_leaders),
    }


def _small_multiples(
    rows: list[dict[str, Any]],
    instruments: dict[str, Any],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        bars = list((instruments.get(row["symbol"], {}).get("price") or {}).get("bars") or [])
        if not bars:
            continue
        closes = [_number(item.get("close")) for item in bars]
        first = next((value for value in closes[-60:] if value is not None and value > 0), None)
        if first is None:
            continue
        points: list[dict[str, Any]] = []
        for index, bar in enumerate(bars[-60:], start=max(0, len(bars) - 60)):
            close = _number(bar.get("close"))
            if close is None:
                continue
            ma20 = _average(closes[index - 19 : index + 1]) if index >= 19 else None
            ma50 = _average(closes[index - 49 : index + 1]) if index >= 49 else None
            points.append(
                {
                    "time": str(bar.get("date")),
                    "value": _round(close / first * 100),
                    "ma20": _round(ma20 / first * 100) if ma20 is not None else None,
                    "ma50": _round(ma50 / first * 100) if ma50 is not None else None,
                }
            )
        result.append(
            {
                "symbol": row["symbol"],
                "points": points,
                "return_20d": row.get("returns_percent", {}).get("20"),
                "trend": row.get("trend", {}).get("state"),
            }
        )
    return result


def _trend_state(latest: float | None, averages: dict[str, Any]) -> dict[str, Any]:
    values = {window: _number(averages.get(f"{window}d")) for window in (20, 50, 200)}
    return {
        "state": "strong" if latest is not None and values[20] is not None and latest > values[20] else "weak" if latest is not None and values[20] is not None and latest < values[20] else "mixed",
        **{f"above_ma{window}": latest is not None and values[window] is not None and latest > values[window] for window in (20, 50, 200)},
    }


def _tracking_error_20d(
    bars: list[dict[str, Any]], benchmark_bars: list[dict[str, Any]]
) -> float | None:
    if not benchmark_bars:
        return None
    instrument = {str(item.get("date")): _number(item.get("close")) for item in bars}
    benchmark = {str(item.get("date")): _number(item.get("close")) for item in benchmark_bars}
    dates = sorted(set(instrument) & set(benchmark))
    differences: list[float] = []
    for previous, current in zip(dates, dates[1:]):
        left_previous, left_current = instrument.get(previous), instrument.get(current)
        right_previous, right_current = benchmark.get(previous), benchmark.get(current)
        if not all(
            value is not None and value > 0
            for value in (left_previous, left_current, right_previous, right_current)
        ):
            continue
        differences.append(
            log(float(left_current) / float(left_previous))
            - log(float(right_current) / float(right_previous))
        )
    if len(differences) < 20:
        return None
    return _round(pstdev(differences[-20:]) * sqrt(252) * 100)


def _shared_window_dates(instruments: dict[str, Any], symbols: list[str], window: int) -> list[str]:
    dates: set[str] = set()
    for symbol in symbols:
        bars = list((instruments.get(symbol, {}).get("price") or {}).get("bars") or [])
        dates.update(str(item["date"]) for item in bars)
    return sorted(dates)[-window:]


def _ranked(rows: list[dict[str, Any]], window: str, *, reverse: bool) -> list[dict[str, Any]]:
    return [
        {"symbol": row["symbol"], "return_percent": row.get("returns_percent", {}).get(window)}
        for row in sorted(
            rows,
            key=lambda item: _sort_number(item.get("returns_percent", {}).get(window)),
            reverse=reverse,
        )[: max(1, (len(rows) + 2) // 3)]
    ]


def _leader_concentration(rows: list[dict[str, Any]]) -> float | None:
    values = [
        _number(row.get("returns_percent", {}).get("20"))
        for row in rows
    ]
    values = [value for value in values if value is not None]
    if not values:
        return None
    positive = [max(value, 0) for value in values]
    total = sum(positive)
    if total == 0:
        return 0.0
    leaders = sorted(positive, reverse=True)[: max(1, (len(positive) + 2) // 3)]
    return _round(sum(leaders) / total)


def _distribution(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "median": None, "q1": None, "q3": None, "min": None, "max": None}
    ordered = sorted(values)
    return {
        "count": len(values),
        "median": _round(median(ordered)),
        "q1": _round(_percentile(ordered, 0.25)),
        "q3": _round(_percentile(ordered, 0.75)),
        "min": _round(ordered[0]),
        "max": _round(ordered[-1]),
    }


def _percentile(values: list[float], fraction: float) -> float:
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * fraction
    lower, upper = int(position), min(len(values) - 1, int(position) + 1)
    weight = position - lower
    return values[lower] * (1 - weight) + values[upper] * weight


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    return _round(float(numerator) / float(denominator)) if denominator else None


def _sort_number(value: object) -> float:
    number = _number(value)
    return number if number is not None else -999.0


def _delta(current: object, previous: object) -> float | None:
    current_value = _number(current)
    previous_value = _number(previous)
    if current_value is None or previous_value is None:
        return None
    return _round(current_value - previous_value)


def _average(values: list[float | None]) -> float | None:
    clean = [value for value in values if value is not None]
    return fsum(clean) / len(clean) if clean else None


def _stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    average = fsum(values) / len(values)
    return sqrt(fsum((value - average) ** 2 for value in values) / len(values))


def _lookback_return(values: list[float], periods: int) -> float | None:
    if len(values) <= periods or values[-periods - 1] == 0:
        return None
    return _round((values[-1] / values[-periods - 1] - 1) * 100)


def _metric(value: object, key: str = "value") -> float | None:
    if isinstance(value, dict):
        return _number(value.get(key))
    return None


def _number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value)):
        return float(value)
    return None


def _round(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None
