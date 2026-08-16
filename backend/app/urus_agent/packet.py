"""Build a compact, provider-neutral Stage 4B AI decision packet."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any


PAIR_SCHEMA = "urus.stage4b_strategy_pair.v1"
PACKET_SCHEMA = "urus.stage4b_decision_packet.v1"
DAILY_PHASES = ("pre_market", "pre_close", "post_close_review", "current_state")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _change(before: Any, after: Any) -> dict[str, float | None]:
    left = _number(before)
    right = _number(after)
    absolute = right - left if left is not None and right is not None else None
    percent = absolute / abs(left) * 100 if absolute is not None and left else None
    return {
        "before": left,
        "after": right,
        "absolute": round(absolute, 6) if absolute is not None else None,
        "percent": round(percent, 6) if percent is not None else None,
    }


def _pick(source: dict[str, Any] | None, keys: tuple[str, ...]) -> dict[str, Any]:
    source = source or {}
    return {key: source.get(key) for key in keys}


def _technical(card: dict[str, Any]) -> dict[str, Any]:
    history = card.get("history") or {}
    indicators = history.get("technical_indicators") or {}
    return {
        "available": history.get("available"),
        "as_of": indicators.get("as_of") or history.get("latest_completed_bar"),
        "sample_count": indicators.get("sample_count"),
        "returns_percent": indicators.get("returns_percent") or history.get("returns_percent"),
        "moving_average": indicators.get("moving_average") or history.get("moving_average"),
        "high_low_distance_percent": indicators.get("high_low_distance_percent"),
        "realized_volatility": {
            "10d": indicators.get("realized_volatility_10d"),
            "20d": indicators.get("realized_volatility_20d"),
            "60d": indicators.get("realized_volatility_60d"),
        },
        "atr14": indicators.get("atr14"),
        "atr14_percent": indicators.get("atr14_percent"),
        "bollinger": {
            "1_sigma": indicators.get("bollinger_20_1"),
            "2_sigma": indicators.get("bollinger_20_2"),
            "3_sigma": indicators.get("bollinger_20_3"),
            "bandwidth_20": indicators.get("bollinger_bandwidth_20"),
        },
        "macd_12_26_9": indicators.get("macd_12_26_9"),
        "rsi14": indicators.get("rsi14"),
        "rsi_context": indicators.get("rsi_context"),
        "volume_effort_result": indicators.get("volume_effort_result"),
        "quality_status": indicators.get("quality_status") or card.get("quality_status"),
        "warnings": indicators.get("warnings") or history.get("warnings") or [],
    }


def _quote(source: dict[str, Any]) -> dict[str, Any]:
    return _pick(
        source,
        (
            "symbol",
            "label",
            "last_price",
            "regular_price",
            "change_percent",
            "regular_change_percent",
            "previous_close",
            "open_price",
            "high_price",
            "low_price",
            "volume",
            "premarket_price",
            "premarket_volume",
            "premarket_change_percent",
            "afterhours_price",
            "afterhours_volume",
            "afterhours_change_percent",
            "quote_time",
            "session",
            "session_price_source",
            "source",
            "data_mode",
            "quality_status",
            "quality_warnings",
        ),
    )


def _instrument(card: dict[str, Any]) -> dict[str, Any]:
    quote = _quote(card)
    return {
        "symbol": card.get("symbol"),
        "asset_type": card.get("asset_type"),
        "theme": card.get("theme"),
        "themes": card.get("themes") or [],
        "quote": quote,
        # Keep the compact packet aligned with the tool's flattened snapshot
        # view so model evidence paths remain canonical in either form.
        "last_price": quote.get("last_price"),
        "regular_price": quote.get("regular_price"),
        "change_percent": quote.get("change_percent"),
        "volume": quote.get("volume"),
        "quote_time": quote.get("quote_time"),
        "session": quote.get("session"),
        "trend": card.get("trend"),
        "technical": _technical(card),
        "relative_strength": card.get("relative_strength") or {},
        "quality_status": card.get("quality_status"),
        "quality_warnings": card.get("quality_warnings") or [],
    }


def _nearest_levels(items: list[dict[str, Any]], spot: float | None, limit: int = 8) -> list[dict[str, Any]]:
    if len(items) <= limit or spot is None:
        return items

    def distance(item: dict[str, Any]) -> float:
        level = _number(item.get("level"))
        if level is not None:
            return abs(level - spot)
        start = _number(item.get("start_strike"))
        end = _number(item.get("end_strike"))
        if start is None or end is None:
            return float("inf")
        if start <= spot <= end:
            return 0.0
        return min(abs(start - spot), abs(end - spot))

    selected = sorted(items, key=distance)[:limit]
    return sorted(selected, key=lambda item: item.get("level") or item.get("start_strike") or 0)


def _expiration(expiration: dict[str, Any]) -> dict[str, Any]:
    exposure = expiration.get("exposure") or {}
    profile = expiration.get("spot_gamma_profile") or {}
    spot = _number(profile.get("current_spot"))
    zones = exposure.get("gamma_zones") or []
    sign_changes = exposure.get("strike_gex_sign_changes") or []
    return {
        "expiration": expiration.get("expiration"),
        "days_to_expiry": expiration.get("days_to_expiry"),
        "contract_count": expiration.get("contract_count"),
        "max_pain": expiration.get("max_pain"),
        "expected_move": expiration.get("expected_move"),
        "exposure_totals": exposure.get("totals") or {},
        "walls": exposure.get("walls") or {},
        "gamma_zone_count": len(zones),
        "gamma_zones": _nearest_levels(zones, spot),
        "strike_gex_sign_change_count": len(sign_changes),
        "strike_gex_sign_changes": _nearest_levels(sign_changes, spot),
        "gamma_noise_threshold": exposure.get("gamma_noise_threshold"),
        "usable_delta_contracts": exposure.get("usable_delta_contracts"),
        "usable_gamma_contracts": exposure.get("usable_gamma_contracts"),
        "spot_gamma_profile": _pick(
            profile,
            (
                "available",
                "gamma_flip_levels",
                "primary_gamma_flip",
                "current_spot",
                "current_spot_net_gex",
                "usable_iv_contracts",
                "range_percent",
                "point_count",
                "time_years",
                "risk_free_rate_percent",
                "dividend_yield_percent",
                "model",
            ),
        ),
    }


def _option_symbol(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": item.get("symbol"),
        "spot": item.get("spot"),
        "spot_time": item.get("spot_time"),
        "overview": item.get("overview") or {},
        "expirations": [_expiration(value) for value in item.get("expirations") or []],
    }


def _observation(
    observation: dict[str, Any], *, include_daily_analysis: bool
) -> dict[str, Any]:
    run = observation.get("run") or {}
    snapshot = observation.get("snapshot") or {}
    payload = snapshot.get("payload") or {}
    market = payload.get("market") or {}
    market_snapshot = market.get("market_snapshot") or {}
    options = payload.get("options") or {}
    return {
        "run": _pick(run, ("id", "run_type", "status", "cutoff_time", "completed_at")),
        "snapshot": {
            **_pick(snapshot, ("id", "schema_version", "cutoff_time", "created_at", "quality_status")),
            "data_mode": payload.get("data_mode"),
            "is_mock": payload.get("is_mock"),
        },
        "market": {
            "primary": _quote(market),
            "trend": market.get("trend"),
            "technical": _technical(market) if include_daily_analysis else {
                "omitted": "Daily technical state is carried in pre_close to avoid duplicate model input."
            },
            "cross_asset_quotes": market_snapshot.get("quotes") or [],
            "vix": market_snapshot.get("vix"),
            "quality_status": market_snapshot.get("quality_status") or market.get("quality_status"),
            "quality_warnings": (market.get("quality_warnings") or [])
            + (market_snapshot.get("quality_warnings") or []),
        },
        "instruments": [
            _instrument(card)
            if include_daily_analysis
            else {
                **_instrument(card),
                "technical": {
                    "omitted": "Daily technical state is carried in pre_close to avoid duplicate model input."
                },
                "relative_strength": {
                    "omitted": "Daily relative strength is carried in pre_close to avoid duplicate model input."
                },
            }
            for card in payload.get("instrument_cards") or []
        ],
        "options": {
            **_pick(options, ("status", "available", "provider", "source_mode", "captured_at")),
            "unavailable_symbols": options.get("unavailable_symbols") or [],
            "model_assumptions": options.get("model_assumptions") or {},
            "warnings": options.get("warnings") or [],
            "symbols": [_option_symbol(item) for item in options.get("symbols") or []],
        },
        "systematic_flows": payload.get("systematic_flows") or {},
        "capital_flows": payload.get("capital_flows") or {},
        "data_quality": payload.get("data_quality") or {},
    }


def _indexed(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("symbol")): item for item in items if item.get("symbol")}


def _paired_changes(
    before: dict[str, Any],
    after: dict[str, Any],
    before_phase: str = "pre_market",
    after_phase: str = "pre_close",
) -> dict[str, Any]:
    before_market = before["market"]["primary"]
    after_market = after["market"]["primary"]
    instrument_before = _indexed(before["instruments"])
    instrument_after = _indexed(after["instruments"])
    option_before = _indexed(before["options"]["symbols"])
    option_after = _indexed(after["options"]["symbols"])

    instruments = []
    for symbol in sorted(instrument_before.keys() & instrument_after.keys()):
        left = instrument_before[symbol]
        right = instrument_after[symbol]
        instruments.append(
            {
                "symbol": symbol,
                "regular_price": _change(left["quote"].get("regular_price"), right["quote"].get("regular_price")),
                "last_price": _change(left["quote"].get("last_price"), right["quote"].get("last_price")),
                "change_percent_delta": _change(left["quote"].get("change_percent"), right["quote"].get("change_percent")),
                "volume": _change(left["quote"].get("volume"), right["quote"].get("volume")),
                "volume_comparison_note": "Session volume is cumulative; use this delta as context, not as like-for-like volume intensity.",
                "technical_confirmation": {
                    before_phase: left.get("trend"),
                    after_phase: right.get("trend"),
                },
            }
        )

    options = []
    for symbol in sorted(option_before.keys() & option_after.keys()):
        left = option_before[symbol]
        right = option_after[symbol]
        left_exp = {item.get("expiration"): item for item in left["expirations"]}
        right_exp = {item.get("expiration"): item for item in right["expirations"]}
        expiration_changes = []
        for expiration in sorted(left_exp.keys() & right_exp.keys()):
            left_item = left_exp[expiration]
            right_item = right_exp[expiration]
            left_totals = left_item.get("exposure_totals") or {}
            right_totals = right_item.get("exposure_totals") or {}
            left_profile = left_item.get("spot_gamma_profile") or {}
            right_profile = right_item.get("spot_gamma_profile") or {}
            expiration_changes.append(
                {
                    "expiration": expiration,
                    "days_to_expiry": right_item.get("days_to_expiry"),
                    "max_pain": _change(left_item.get("max_pain"), right_item.get("max_pain")),
                    "expected_move_amount": _change(
                        (left_item.get("expected_move") or {}).get("amount"),
                        (right_item.get("expected_move") or {}).get("amount"),
                    ),
                    "expected_move_percent": _change(
                        (left_item.get("expected_move") or {}).get("percent"),
                        (right_item.get("expected_move") or {}).get("percent"),
                    ),
                    "net_dex": _change(left_totals.get("net_dex"), right_totals.get("net_dex")),
                    "modeled_net_gex": _change(
                        left_totals.get("modeled_net_gex"), right_totals.get("modeled_net_gex")
                    ),
                    "primary_gamma_flip": _change(
                        left_profile.get("primary_gamma_flip"), right_profile.get("primary_gamma_flip")
                    ),
                    "current_spot_net_gex": _change(
                        left_profile.get("current_spot_net_gex"), right_profile.get("current_spot_net_gex")
                    ),
                }
            )
        options.append(
            {
                "symbol": symbol,
                "spot": _change(left.get("spot"), right.get("spot")),
                "overview": {
                    key: _change(left["overview"].get(key), right["overview"].get(key))
                    for key in sorted(left["overview"].keys() | right["overview"].keys())
                },
                "expirations": expiration_changes,
            }
        )

    return {
        "market": {
            "regular_price": _change(before_market.get("regular_price"), after_market.get("regular_price")),
            "last_price": _change(before_market.get("last_price"), after_market.get("last_price")),
            "change_percent_delta": _change(
                before_market.get("change_percent"), after_market.get("change_percent")
            ),
        },
        "instruments": instruments,
        "options": options,
        "systematic_flows": _systematic_flow_changes(
            before.get("systematic_flows") or {}, after.get("systematic_flows") or {}
        ),
    }


def _systematic_flow_changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    left = _indexed(before.get("assets") or [])
    right = _indexed(after.get("assets") or [])
    assets = []
    for symbol in sorted(left.keys() & right.keys()):
        assets.append(
            {
                "symbol": symbol,
                "target_exposure": _change(
                    left[symbol].get("target_exposure"), right[symbol].get("target_exposure")
                ),
                "pressure_index": _change(
                    left[symbol].get("pressure_index"), right[symbol].get("pressure_index")
                ),
                "mechanical_action": {
                    "before": left[symbol].get("mechanical_action"),
                    "after": right[symbol].get("mechanical_action"),
                },
            }
        )
    return {
        "from_model_state": before.get("model_state"),
        "to_model_state": after.get("model_state"),
        "assets": assets,
        "portfolio": {
            key: _change(
                (before.get("portfolio") or {}).get(key),
                (after.get("portfolio") or {}).get(key),
            )
            for key in ("unweighted_net_exposure", "unweighted_gross_exposure")
        },
    }


def _event(event: dict[str, Any]) -> dict[str, Any]:
    sources = event.get("sources") or []
    return {
        **_pick(
            event,
            (
                "id",
                "event_key",
                "definition_key",
                "category",
                "subject",
                "event_type",
                "title",
                "period",
                "status",
                "scheduled_at",
                "occurred_at",
                "result_expected_at",
                "result_available_at",
                "confidence",
                "result",
            ),
        ),
        "source_count": len(sources),
        "sources": [
            _pick(source, ("publisher", "url", "source_type", "is_primary")) for source in sources[:3]
        ],
        "market_reactions": event.get("market_reactions") or [],
    }


def build_decision_packet(pair: dict[str, Any]) -> dict[str, Any]:
    if pair.get("backup_schema") != PAIR_SCHEMA:
        raise ValueError(f"Expected {PAIR_SCHEMA!r}, got {pair.get('backup_schema')!r}.")
    observations = (pair.get("pair") or {}).get("observations") or {}
    if "pre_market" not in observations or "pre_close" not in observations:
        raise ValueError("Pair must contain pre_market and pre_close observations.")

    compact = {
        "pre_market": _observation(observations["pre_market"], include_daily_analysis=False),
        "pre_close": _observation(observations["pre_close"], include_daily_analysis=True),
    }
    warnings: list[str] = []
    for name, observation in compact.items():
        if observation["snapshot"].get("is_mock"):
            warnings.append(f"{name} snapshot contains mock data.")
        if observation["snapshot"].get("quality_status") not in (None, "ok", "complete"):
            warnings.append(
                f"{name} snapshot quality is {observation['snapshot'].get('quality_status')!r}."
            )
    packet = {
        "schema_version": PACKET_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            "dataset_key": pair.get("dataset_key"),
            "label": pair.get("label"),
            "captured_at": pair.get("captured_at"),
            "content_sha256": pair.get("content_sha256"),
        },
        "quality": {
            "status": "warning" if warnings else "ok",
            "warnings": warnings,
            "blocking_errors": [],
        },
        "observations": compact,
        "paired_changes": _paired_changes(compact["pre_market"], compact["pre_close"]),
        "events": {
            "captured_at": (pair.get("events") or {}).get("captured_at"),
            "records": [_event(event) for event in (pair.get("events") or {}).get("records") or []],
        },
        "omissions": [
            "raw history bars",
            "option exposure by_strike rows",
            "spot gamma profile points",
            "duplicate workflow step payloads",
            "duplicate pre-market daily technical and relative-strength calculations",
            "gamma zones and strike sign changes beyond the eight nearest spot",
        ],
        "execution_ready": False,
        "execution_blockers": [
            "No option contract bid/ask or leg premiums are included.",
            "The packet supports research decisions only and cannot place orders.",
        ],
    }
    packet["content_sha256"] = hashlib.sha256(_canonical(packet)).hexdigest()
    return packet


def build_online_decision_packet(
    *,
    dataset_key: str,
    label: str,
    captured_at: datetime,
    pre_market_observation: dict[str, Any],
    pre_close_observation: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the same frozen packet used by the offline pair CLI.

    The pre-market observation is already persisted.  The pre-close
    observation may still be in memory because Step 4 runs before Step 5
    persists the current workflow snapshot.
    """

    pair = {
        "backup_schema": PAIR_SCHEMA,
        "dataset_key": dataset_key,
        "label": label,
        "captured_at": captured_at.isoformat(),
        "pair": {
            "observation_order": ["pre_market", "pre_close"],
            "observations": {
                "pre_market": pre_market_observation,
                "pre_close": pre_close_observation,
            },
        },
        "events": {
            "captured_at": captured_at.isoformat(),
            "records": events,
        },
    }
    pair["content_sha256"] = hashlib.sha256(_canonical(pair)).hexdigest()
    return build_decision_packet(pair)


def build_stage_decision_packet(
    *,
    dataset_key: str,
    label: str,
    captured_at: datetime,
    decision_phase: str,
    trading_date: str,
    observations: dict[str, dict[str, Any]],
    prior_reports: dict[str, dict[str, Any] | None],
    events: list[dict[str, Any]],
    agent_profile: dict[str, Any],
) -> dict[str, Any]:
    """Build one frozen packet for a daily-cycle Agent invocation.

    Unlike the legacy two-snapshot packet, this packet may contain one, two,
    or three observations.  The current phase is explicit so no consumer has
    to infer time semantics from whichever snapshot happened to be latest.
    """

    if decision_phase not in DAILY_PHASES:
        raise ValueError(f"Unsupported decision phase: {decision_phase!r}")
    if decision_phase not in observations:
        raise ValueError(f"Current observation is missing: {decision_phase}")
    compact = {
        phase: _observation(value, include_daily_analysis=True)
        for phase, value in observations.items()
        if phase in DAILY_PHASES and isinstance(value, dict)
    }
    expected_comparison_phases = [
        str(value)
        for value in agent_profile.get("comparison_observations", [])
    ]
    missing_observations = [
        phase for phase in expected_comparison_phases if phase not in compact
    ]
    comparison_phases = [phase for phase in expected_comparison_phases if phase in compact]
    if decision_phase not in comparison_phases:
        comparison_phases.append(decision_phase)
    baseline_phase = comparison_phases[0]
    paired_changes = (
        _paired_changes(
            compact[baseline_phase], compact[decision_phase], baseline_phase, decision_phase
        )
        if baseline_phase != decision_phase
        else {"market": {}, "instruments": [], "options": []}
    )
    stage_changes: dict[str, Any] = {}
    for left, right in zip(comparison_phases, comparison_phases[1:]):
        stage_changes[f"{left}_to_{right}"] = _paired_changes(
            compact[left], compact[right], left, right
        )

    warnings: list[str] = []
    for phase, observation in compact.items():
        if observation["snapshot"].get("is_mock"):
            warnings.append(f"{phase} snapshot contains mock data.")
        if observation["snapshot"].get("quality_status") not in (None, "ok", "complete"):
            warnings.append(
                f"{phase} snapshot quality is {observation['snapshot'].get('quality_status')!r}."
            )
    missing_lineage = [key for key, value in prior_reports.items() if value is None]
    if missing_lineage:
        warnings.append("Missing prior decision reports: " + ", ".join(sorted(missing_lineage)))
    if missing_observations:
        warnings.append("Missing earlier observations: " + ", ".join(missing_observations))

    packet = {
        "schema_version": PACKET_SCHEMA,
        "generated_at": captured_at.isoformat(),
        "source": {
            "dataset_key": dataset_key,
            "label": label,
            "captured_at": captured_at.isoformat(),
        },
        "decision_context": {
            "decision_phase": decision_phase,
            "trading_date": trading_date,
            "agent_name": agent_profile["agent_name"],
            "forecast_horizon": agent_profile["forecast_horizon"],
            "current_observation": decision_phase,
            "comparison_observations": comparison_phases,
            "missing_observations": missing_observations,
            "missing_lineage": missing_lineage,
        },
        "quality": {
            "status": "warning" if warnings else "ok",
            "warnings": warnings,
            "blocking_errors": [],
        },
        "observations": compact,
        "paired_changes": paired_changes,
        "stage_changes": stage_changes,
        "prior_reports": {
            key: _compact_prior_report(value) if isinstance(value, dict) else None
            for key, value in prior_reports.items()
        },
        "events": {
            "captured_at": captured_at.isoformat(),
            "records": [_event(event) for event in events],
        },
        "omissions": [
            "raw history bars",
            "option exposure by_strike rows",
            "spot gamma profile points",
            "duplicate workflow step payloads",
        ],
        "execution_ready": False,
        "execution_blockers": [
            "No option contract bid/ask or leg premiums are included.",
            "The packet supports research decisions only and cannot place orders.",
        ],
    }
    packet["content_sha256"] = hashlib.sha256(_canonical(packet)).hexdigest()
    return packet


def _compact_prior_report(report: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: report.get(key)
        for key in (
            "report_id",
            "session_id",
            "decision_phase",
            "agent_profile",
            "trading_date",
            "cutoff_time",
            "status",
            "market_regime",
            "forecast",
            "review",
            "portfolio_warnings",
            "quality",
        )
    }
    compact["rankings"] = [
        {
            key: ranking.get(key)
            for key in (
                "rank",
                "symbol",
                "themes",
                "thesis",
                "instrument_forecast",
                "invalidation_conditions",
            )
            if key in ranking
        }
        for ranking in report.get("rankings") or []
        if isinstance(ranking, dict)
    ]
    return compact


def project_decision_packet(
    packet: dict[str, Any], *, mode: str, symbols: set[str] | None = None
) -> dict[str, Any]:
    if mode not in {"full", "equity", "options"}:
        raise ValueError(f"Unsupported projection mode: {mode!r}.")
    normalized_symbols = {symbol.upper() for symbol in symbols or set()}
    if mode == "options" and not normalized_symbols:
        raise ValueError("The options projection requires at least one symbol.")

    projected = json.loads(json.dumps(packet, ensure_ascii=False, default=str))
    projected["projection"] = {
        "mode": mode,
        "symbols": sorted(normalized_symbols),
    }
    for observation in projected["observations"].values():
        if normalized_symbols:
            observation["instruments"] = [
                item for item in observation["instruments"] if item.get("symbol") in normalized_symbols
            ]
        if mode == "equity":
            observation["options"]["symbols"] = []
            observation["options"]["projection_note"] = (
                "Option chains are omitted from the equity-ranking projection."
            )
        elif normalized_symbols:
            observation["options"]["symbols"] = [
                item
                for item in observation["options"]["symbols"]
                if item.get("symbol") in normalized_symbols
            ]

    changes = projected["paired_changes"]
    if normalized_symbols:
        changes["instruments"] = [
            item for item in changes["instruments"] if item.get("symbol") in normalized_symbols
        ]
        changes["options"] = [
            item for item in changes["options"] if item.get("symbol") in normalized_symbols
        ]
    if mode == "equity":
        changes["options"] = []

    if normalized_symbols:
        projected["events"]["records"] = [
            event
            for event in projected["events"]["records"]
            if event.get("category") == "macro" or event.get("subject") in normalized_symbols
        ]

    projected.pop("content_sha256", None)
    projected["content_sha256"] = hashlib.sha256(_canonical(projected)).hexdigest()
    return projected

__all__ = ["PAIR_SCHEMA", "PACKET_SCHEMA", "build_decision_packet", "project_decision_packet"]
