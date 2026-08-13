from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

from app.urus_agent.evidence import EvidenceStore


TECHNICAL_REPORT_SCHEMA = "urus.technical_report.v1"
DECISION_REPORT_SCHEMA = "urus.ai_decision_report.v5"
FLAT_RETURN_THRESHOLD_PERCENT = 0.15
OBJECTIVE_EVALUATION_METHOD = "programmatic_session_price_direction_v3"


def build_technical_report(evidence: EvidenceStore) -> dict[str, Any]:
    """Create a deterministic, presentation-oriented report from frozen evidence."""

    packet = evidence.packet
    observations = packet.get("observations") or {}
    pre_market = observations.get("pre_market") or {}
    pre_close = observations.get("pre_close") or {}
    post_close = observations.get("post_close_review") or {}
    current_state = observations.get("current_state") or {}
    decision_context = packet.get("decision_context") or {}
    current_phase = str(decision_context.get("current_observation") or "pre_close")
    current = observations.get(current_phase) or pre_close or pre_market
    instruments = [
        item for item in (current.get("instruments") or []) if isinstance(item, dict)
    ]
    themes: dict[str, list[dict[str, Any]]] = {}
    for item in instruments:
        names = list(item.get("themes") or [])
        if item.get("theme") and item["theme"] not in names:
            names.append(str(item["theme"]))
        if not names:
            names = ["其他关注"]
        for theme in names:
            themes.setdefault(str(theme), []).append(item)
    source = dict(packet.get("source") or {})
    run_ids = {
        str(((observations.get(phase) or {}).get("run") or {}).get("id"))
        for phase in observations
        if ((observations.get(phase) or {}).get("run") or {}).get("id")
    }
    snapshot_ids = {
        str(((observations.get(phase) or {}).get("snapshot") or {}).get("id"))
        for phase in observations
        if ((observations.get(phase) or {}).get("snapshot") or {}).get("id")
    }
    source.setdefault("run_count", len(run_ids))
    source.setdefault("snapshot_count", len(snapshot_ids))
    source.setdefault("evidence_scope", "paired" if len(run_ids) > 1 else "single_workflow")
    return {
        "schema_version": TECHNICAL_REPORT_SCHEMA,
        "dataset_key": evidence.dataset_key,
        "cutoff_time": ((current.get("run") or {}).get("cutoff_time") or packet.get("generated_at")),
        "decision_phase": decision_context.get("decision_phase") or current_phase,
        "trading_date": decision_context.get("trading_date"),
        "agent_profile": decision_context.get("agent_name"),
        "trigger_type": decision_context.get("trigger_type", "scheduled"),
        "analysis_mode": decision_context.get("analysis_mode", "official_cycle"),
        "session_context": decision_context.get("session_context", current_phase),
        "report_scope": decision_context.get("report_scope") or ["technical_report"],
        "official_cycle": bool(decision_context.get("official_cycle", True)),
        "eligible_for_scoring": bool(decision_context.get("eligible_for_scoring", True)),
        "updates_official_cta_state": bool(
            decision_context.get("updates_official_cta_state", True)
        ),
        "generated_at": datetime.now(UTC).isoformat(),
        "source": source,
        "quality": packet.get("quality") or {},
        "market": {
            "pre_market": (pre_market.get("market") or {}),
            "pre_close": (pre_close.get("market") or {}),
            "post_close_review": (post_close.get("market") or {}),
            "current_state": (current_state.get("market") or {}),
            "current_phase": current_phase,
            "paired_changes": (packet.get("paired_changes") or {}).get("market") or {},
        },
        "instruments": {
            "pre_market": [
                item for item in (pre_market.get("instruments") or []) if isinstance(item, dict)
            ],
            "pre_close": [
                item for item in (pre_close.get("instruments") or []) if isinstance(item, dict)
            ],
            "post_close_review": [
                item for item in (post_close.get("instruments") or []) if isinstance(item, dict)
            ],
            "current_state": [
                item for item in (current_state.get("instruments") or []) if isinstance(item, dict)
            ],
            "current_phase": current_phase,
            "themes": themes,
            "paired_changes": (packet.get("paired_changes") or {}).get("instruments") or [],
        },
        "options": {
            "pre_market": (pre_market.get("options") or {}),
            "pre_close": (pre_close.get("options") or {}),
            "post_close_review": (post_close.get("options") or {}),
            "current_state": (current_state.get("options") or {}),
            "current_phase": current_phase,
            "paired_changes": (packet.get("paired_changes") or {}).get("options") or [],
        },
        "systematic_flows": {
            "pre_market": pre_market.get("systematic_flows") or {},
            "pre_close": pre_close.get("systematic_flows") or {},
            "post_close_review": post_close.get("systematic_flows") or {},
            "current_state": current_state.get("systematic_flows") or {},
            "current_phase": current_phase,
            "paired_changes": (packet.get("paired_changes") or {}).get("systematic_flows") or {},
        },
        "events": packet.get("events") or {"records": []},
        "prior_reports": packet.get("prior_reports") or {},
        "omissions": packet.get("omissions") or [],
        "execution_ready": False,
    }


def select_option_candidates(
    equity_output: dict[str, Any] | None,
    evidence: EvidenceStore,
    *,
    policy: str,
    max_symbols: int,
    min_score: float,
    min_confidence: float,
    etf_symbols: list[str],
) -> list[dict[str, Any]]:
    """Deterministically select option invocations from validated equity output."""

    available = {str(value).upper() for value in evidence.overview().get("option_symbols", [])}
    rankings = list((equity_output or {}).get("rankings") or [])
    candidates: list[dict[str, Any]] = []
    selected_symbols: set[str] = set()
    allowed_etfs = {value.upper() for value in etf_symbols}
    quality = evidence.packet.get("quality") or {}
    blocking_errors = [item for item in (quality.get("blocking_errors") or []) if item]

    for item in rankings:
        symbol = str(item.get("symbol") or "").upper()
        if not symbol or symbol in selected_symbols:
            continue
        action = str(item.get("action") or "")
        cash_action = str((item.get("if_cash") or {}).get("action") or "")
        score = _number(item.get("score"))
        confidence = _number(item.get("confidence"))
        eligible = (
            not blocking_errors
            and action in {"setup_ready", "watch"}
            and cash_action in {"", "buy"}
            and (score or 0) >= min_score
            and (confidence or 0) >= min_confidence
        )
        if blocking_errors:
            reason = "data_quality_blocked"
        elif policy == "etf_only" and symbol not in allowed_etfs:
            eligible = False
            reason = "not_in_etf_policy"
        elif symbol not in available:
            eligible = False
            reason = "option_data_unavailable"
        elif not eligible:
            reason = (
                "cash_scenario_not_buy"
                if cash_action and cash_action != "buy"
                else "equity_threshold_not_met"
            )
        else:
            reason = "selected_by_equity_rank"
        candidates.append(
            {
                "symbol": symbol,
                "source_rank": item.get("rank"),
                "score": score,
                "confidence": confidence,
                "selected": False,
                "reason": reason,
            }
        )
        if eligible and len(selected_symbols) < max_symbols:
            candidates[-1]["selected"] = True
            selected_symbols.add(symbol)

    if policy == "etf_plus_ranked":
        for symbol in etf_symbols:
            symbol = symbol.upper()
            if symbol in {str(item.get("symbol") or "").upper() for item in candidates} or len(selected_symbols) >= max_symbols:
                continue
            if blocking_errors:
                candidates.append({"symbol": symbol, "selected": False, "reason": "data_quality_blocked"})
                continue
            if symbol not in available:
                candidates.append({"symbol": symbol, "selected": False, "reason": "option_data_unavailable"})
                continue
            candidates.append({"symbol": symbol, "selected": True, "reason": "fixed_etf_policy"})
            selected_symbols.add(symbol)

    if policy == "disabled":
        for item in candidates:
            item["selected"] = False
            item["reason"] = "option_policy_disabled"
    return candidates


def build_equity_option_context(
    evidence: EvidenceStore,
    symbols: list[str],
) -> list[dict[str, Any]]:
    """Reduce option-chain structure to deterministic equity entry context.

    This is deliberately not an option trade recommendation.  Gamma regime,
    expected move and nearby walls describe entry stability/risk for the
    underlying equity; the synthesis model remains responsible for combining
    that context with trend, relative strength and event evidence.
    """

    observation = (evidence.packet.get("observations") or {}).get(evidence.current_phase) or {}
    option_symbols = {
        str(item.get("symbol") or "").upper(): item
        for item in (observation.get("options") or {}).get("symbols") or []
        if isinstance(item, dict) and item.get("symbol")
    }
    contexts: list[dict[str, Any]] = []
    for symbol in dict.fromkeys(str(value).upper() for value in symbols if value):
        item = option_symbols.get(symbol)
        expirations = [
            value
            for value in (item or {}).get("expirations") or []
            if isinstance(value, dict) and value.get("expiration")
        ]
        if not expirations:
            contexts.append(
                {
                    "symbol": symbol,
                    "available": False,
                    "entry_context": "unknown",
                    "risk_flags": ["option_structure_unavailable"],
                    "interpretation": "Do not penalize the equity solely because option data is unavailable.",
                }
            )
            continue

        positive_dte = [value for value in expirations if (_number(value.get("days_to_expiry")) or 0) > 0]
        selected = min(
            positive_dte or expirations,
            key=lambda value: (
                _number(value.get("days_to_expiry")) or 0,
                str(value.get("expiration") or ""),
            ),
        )
        profile = selected.get("spot_gamma_profile") or {}
        overview = (item or {}).get("overview") or {}
        totals = selected.get("exposure_totals") or (selected.get("exposure") or {}).get("totals") or {}
        walls = selected.get("walls") or (selected.get("exposure") or {}).get("walls") or {}
        spot = _first_number(profile.get("current_spot"), (item or {}).get("spot"))
        gamma_flip = _number(profile.get("primary_gamma_flip"))
        net_gex = _first_number(
            profile.get("current_spot_net_gex"),
            totals.get("modeled_net_gex"),
            totals.get("net_gex"),
        )
        flip_distance_percent = (
            round((spot - gamma_flip) / spot * 100, 4)
            if spot not in (None, 0) and gamma_flip is not None
            else None
        )
        near_flip = flip_distance_percent is not None and abs(flip_distance_percent) <= 0.5
        if near_flip:
            entry_context = "near_gamma_flip"
        elif net_gex is not None and net_gex < 0:
            entry_context = "unstable_negative_gamma"
        elif gamma_flip is not None and spot is not None and spot < gamma_flip:
            entry_context = "fragile_below_gamma_flip"
        elif net_gex is not None and net_gex > 0 and (
            gamma_flip is None or spot is None or spot > gamma_flip
        ):
            entry_context = "stable_positive_gamma"
        else:
            entry_context = "neutral_or_unknown"

        expected_move = selected.get("expected_move") or {}
        expected_move_percent = _first_number(
            expected_move.get("percent") if isinstance(expected_move, dict) else None,
            expected_move.get("percent_of_spot") if isinstance(expected_move, dict) else None,
        )
        dte = _number(selected.get("days_to_expiry"))
        risk_flags: list[str] = []
        if near_flip:
            risk_flags.append("spot_near_gamma_flip")
        if net_gex is not None and net_gex < 0:
            risk_flags.append("negative_gamma_can_amplify_moves")
        if gamma_flip is not None and spot is not None and spot < gamma_flip:
            risk_flags.append("spot_below_gamma_flip")
        if dte is not None and dte <= 2:
            risk_flags.append("near_expiration_structure_changes_quickly")
        if expected_move_percent is not None and expected_move_percent >= 3:
            risk_flags.append("large_expected_move")
        iv_hv_regime = str(overview.get("iv_hv_regime") or "unknown")
        if iv_hv_regime in {"deep_discount", "moderate_discount"}:
            risk_flags.append("implied_volatility_below_hv30")
        if overview.get("event_adjusted_flag") == "unknown":
            risk_flags.append("iv_hv_not_event_adjusted")

        contexts.append(
            {
                "symbol": symbol,
                "available": True,
                "observation": evidence.current_phase,
                "expiration": selected.get("expiration"),
                "days_to_expiry": selected.get("days_to_expiry"),
                "spot": spot,
                "entry_context": entry_context,
                "gamma_regime": (
                    "positive_gamma" if net_gex is not None and net_gex > 0
                    else "negative_gamma" if net_gex is not None and net_gex < 0
                    else "unknown"
                ),
                "current_spot_net_gex": net_gex,
                "net_dex": _first_number(totals.get("net_dex"), totals.get("modeled_net_dex")),
                "primary_gamma_flip": gamma_flip,
                "flip_distance_percent": flip_distance_percent,
                "expected_move": expected_move,
                "volatility_pricing": {
                    "iv": overview.get("iv"),
                    "hv_30d": overview.get("hv_30d"),
                    "iv_hv_spread": overview.get("iv_hv_spread"),
                    "iv_hv_ratio": overview.get("iv_hv_ratio"),
                    "iv_hv_regime": iv_hv_regime,
                    "term_match_method": overview.get("term_match_method"),
                    "model_fidelity": overview.get("model_fidelity"),
                    "event_adjusted_flag": overview.get("event_adjusted_flag"),
                    "interpretation": "Relative volatility pricing only; not a directional signal.",
                },
                "max_pain": selected.get("max_pain"),
                "call_wall": _wall_level(walls, "call"),
                "put_wall": _wall_level(walls, "put"),
                "risk_flags": risk_flags,
                "interpretation": (
                    "Use only as an entry-timing and volatility filter for the underlying equity; "
                    "it is neither a standalone directional forecast nor an option strategy."
                ),
                "evidence_path": (
                    f"observations.{evidence.current_phase}.options.symbols[{symbol}]."
                    f"expirations[{selected.get('expiration')}]"
                ),
            }
        )
    return contexts


def build_ai_decision_report(
    *,
    session_id: str,
    run_id: str,
    cutoff_time: datetime,
    equity_run_id: str | None,
    equity_output: dict[str, Any] | None,
    market_analysis: dict[str, Any],
    theme_analyses: list[dict[str, Any]],
    candidate_gate: list[dict[str, Any]],
    option_decisions: list[dict[str, Any]],
    equity_option_context: list[dict[str, Any]] | None = None,
    quality: dict[str, Any],
    decision_phase: str = "pre_close",
    agent_profile: str = "urus-preclose-strategist",
    trading_date: str = "",
    parent_report_id: str | None = None,
    evidence: EvidenceStore | None = None,
    analysis_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    equity_status = str((equity_output or {}).get("status") or "insufficient_data")
    option_failures = [item for item in option_decisions if item.get("status") != "succeeded"]
    analysis_failures = [
        item
        for item in [market_analysis, *theme_analyses]
        if item.get("status") != "succeeded"
    ]
    status = "failed" if equity_run_id is None else "partial" if option_failures or analysis_failures else "succeeded"
    objective_evaluation = build_objective_evaluation(
        evidence, decision_phase=decision_phase
    ) if evidence is not None else {"status": "unavailable", "phase_evaluations": []}
    review = copy.deepcopy((equity_output or {}).get("review"))
    if isinstance(review, dict) and objective_evaluation.get("status") == "completed":
        phase_results = {
            str(item.get("phase")): item
            for item in objective_evaluation.get("phase_evaluations") or []
            if isinstance(item, dict)
        }
        for phase, key in (("pre_market", "pre_market_evaluation"),):
            result = phase_results.get(phase)
            if result is None:
                continue
            reflection = review.get(key) if isinstance(review.get(key), dict) else {}
            review[key] = {
                "report_id": result.get("report_id"),
                "verdict": result.get("verdict"),
                "score": result.get("score"),
                "explanation": reflection.get("explanation") or "",
                "actual_return_percent": result.get("actual_return_percent"),
                "actual_direction": result.get("actual_direction"),
                "predicted_direction": result.get("predicted_direction"),
                "brier_score": result.get("brier_score"),
            }
        review["pre_close_evaluation"] = None
    metadata = analysis_metadata or {}
    return {
        "schema_version": DECISION_REPORT_SCHEMA,
        "report_id": session_id,
        "session_id": session_id,
        "run_id": run_id,
        "cutoff_time": cutoff_time.isoformat(),
        "decision_phase": decision_phase,
        "agent_profile": agent_profile,
        "trading_date": trading_date,
        "parent_report_id": parent_report_id,
        "trigger_type": metadata.get("trigger_type", "scheduled"),
        "analysis_mode": metadata.get("analysis_mode", "official_cycle"),
        "session_context": metadata.get("session_context", decision_phase),
        "report_scope": metadata.get(
            "report_scope", ["technical_report", "ai_decision", "ai_review"]
        ),
        "official_cycle": bool(metadata.get("official_cycle", True)),
        "eligible_for_scoring": bool(metadata.get("eligible_for_scoring", True)),
        "updates_official_cta_state": bool(
            metadata.get("updates_official_cta_state", True)
        ),
        "status": status,
        "equity_decision_run_id": equity_run_id,
        "equity_status": equity_status,
        "market_analysis": market_analysis,
        "theme_analyses": theme_analyses,
        "market_regime": (equity_output or {}).get("market_regime") or {},
        "rankings": (equity_output or {}).get("rankings") or [],
        "candidate_gate": candidate_gate,
        "option_decisions": option_decisions,
        "equity_option_context": equity_option_context or [],
        "portfolio_warnings": (equity_output or {}).get("portfolio_warnings") or [],
        "forecast_horizon": (equity_output or {}).get("forecast_horizon"),
        "forecast": (equity_output or {}).get("forecast"),
        "review": review,
        "objective_evaluation": objective_evaluation,
        "quality": quality,
        "execution_ready": False,
        "generated_at": datetime.now(UTC).isoformat(),
        "disclaimer": "Research output only; no order was placed.",
    }


def build_objective_evaluation(
    evidence: EvidenceStore,
    *,
    decision_phase: str,
) -> dict[str, Any]:
    """Score frozen forecasts with deterministic price math.

    The model may explain the outcome, but it does not assign its own verdict or
    score. This keeps daily evaluation reproducible and auditable.
    """

    if decision_phase == "current_state":
        return {
            "status": "not_applicable",
            "reason": "manual current-state analysis is not eligible for scoring",
            "method": OBJECTIVE_EVALUATION_METHOD,
            "flat_threshold_percent": FLAT_RETURN_THRESHOLD_PERCENT,
            "phase_evaluations": [],
        }
    if decision_phase != "post_close_review":
        return {
            "status": "pending",
            "method": OBJECTIVE_EVALUATION_METHOD,
            "flat_threshold_percent": FLAT_RETURN_THRESHOLD_PERCENT,
            "phase_evaluations": [],
        }
    packet = evidence.packet
    observations = packet.get("observations") or {}
    close = observations.get("post_close_review")
    if not isinstance(close, dict):
        return {
            "status": "unavailable",
            "reason": "post_close_review observation is missing",
            "method": OBJECTIVE_EVALUATION_METHOD,
            "phase_evaluations": [],
        }
    prior_reports = packet.get("prior_reports") or {}
    close_session_error = _phase_session_error(close, "post_close_review")
    if close_session_error:
        return {
            "status": "unavailable",
            "reason": close_session_error,
            "method": OBJECTIVE_EVALUATION_METHOD,
            "flat_threshold_percent": FLAT_RETURN_THRESHOLD_PERCENT,
            "phase_evaluations": [
                {
                    "phase": phase,
                    "report_id": (
                        (prior_reports.get(report_key) or {}).get("report_id")
                        if isinstance(prior_reports.get(report_key), dict)
                        else None
                    ),
                    "verdict": "unscorable",
                    "score": None,
                    "reason": close_session_error,
                }
                for phase, report_key in (("pre_market", "same_day_pre_market"),)
            ],
            "instrument_results": [],
            "overall_instrument_hit_rate": None,
        }
    phase_evaluations: list[dict[str, Any]] = []
    all_instrument_results: list[dict[str, Any]] = []
    for phase, report_key in (("pre_market", "same_day_pre_market"),):
        baseline = observations.get(phase)
        report = prior_reports.get(report_key)
        if not isinstance(baseline, dict) or not isinstance(report, dict):
            phase_evaluations.append(
                {
                    "phase": phase,
                    "report_id": (report or {}).get("report_id") if isinstance(report, dict) else None,
                    "verdict": "unscorable",
                    "score": None,
                    "reason": "baseline observation or prior report is missing",
                }
            )
            continue
        baseline_session_error = _phase_session_error(baseline, phase)
        if baseline_session_error:
            phase_evaluations.append(
                {
                    "phase": phase,
                    "report_id": report.get("report_id") or report.get("session_id"),
                    "verdict": "unscorable",
                    "score": None,
                    "reason": baseline_session_error,
                }
            )
            continue
        benchmark_result = _score_market_forecast(report, baseline, close, phase)
        instrument_results = [
            result
            for ranking in report.get("rankings") or []
            if isinstance(ranking, dict)
            for result in [_score_instrument_forecast(ranking, baseline, close, phase)]
            if result is not None
        ]
        all_instrument_results.extend(instrument_results)
        phase_evaluations.append(
            {
                "phase": phase,
                "report_id": report.get("report_id") or report.get("session_id"),
                **benchmark_result,
                "instrument_count": len(instrument_results),
                "instrument_hit_rate": _hit_rate(instrument_results),
            }
        )
    return {
        "status": "completed",
        "method": OBJECTIVE_EVALUATION_METHOD,
        "benchmark": "QQQ",
        "flat_threshold_percent": FLAT_RETURN_THRESHOLD_PERCENT,
        "phase_evaluations": phase_evaluations,
        "instrument_results": all_instrument_results,
        "overall_instrument_hit_rate": _hit_rate(all_instrument_results),
    }


def _score_market_forecast(
    report: dict[str, Any],
    baseline: dict[str, Any],
    close: dict[str, Any],
    baseline_phase: str,
) -> dict[str, Any]:
    predicted = {
        "bullish": "up",
        "bearish": "down",
        "range": "flat",
    }.get(str((report.get("forecast") or {}).get("direction") or ""), "unscorable")
    before = _symbol_price(baseline, "QQQ", baseline_phase)
    after = _symbol_price(close, "QQQ", "post_close_review")
    return _score_direction(
        predicted=predicted,
        probability=_number((report.get("forecast") or {}).get("confidence")),
        before=before,
        after=after,
    )


def _score_instrument_forecast(
    ranking: dict[str, Any],
    baseline: dict[str, Any],
    close: dict[str, Any],
    phase: str,
) -> dict[str, Any] | None:
    forecast = ranking.get("instrument_forecast")
    symbol = str(ranking.get("symbol") or "").upper()
    if not symbol or not isinstance(forecast, dict):
        return None
    scored = _score_direction(
        predicted=str(forecast.get("direction") or "unscorable"),
        probability=_number(forecast.get("probability")),
        before=_symbol_price(baseline, symbol, phase),
        after=_symbol_price(close, symbol, "post_close_review"),
    )
    expected_range = forecast.get("expected_return_range_percent") or {}
    actual_return = scored.get("actual_return_percent")
    minimum = _number(expected_range.get("minimum_percent"))
    maximum = _number(expected_range.get("maximum_percent"))
    range_hit = (
        minimum <= actual_return <= maximum
        if minimum is not None and maximum is not None and actual_return is not None
        else None
    )
    relative_to = str(forecast.get("relative_to") or "none")
    benchmark_symbol = relative_to if relative_to in {"QQQ", "SPY"} else None
    benchmark_return = _return_percent(
        _symbol_price(baseline, benchmark_symbol, phase),
        _symbol_price(close, benchmark_symbol, "post_close_review"),
    ) if benchmark_symbol else None
    relative_return = (
        round(actual_return - benchmark_return, 6)
        if actual_return is not None and benchmark_return is not None
        else None
    )
    return {
        "phase": phase,
        "symbol": symbol,
        **scored,
        "expected_range_hit": range_hit,
        "relative_to": relative_to,
        "relative_return_percent": relative_return,
    }


def _score_direction(
    *,
    predicted: str,
    probability: float | None,
    before: float | None,
    after: float | None,
) -> dict[str, Any]:
    actual_return = _return_percent(before, after)
    actual_direction = _direction(actual_return)
    scorable = predicted in {"up", "down", "flat"} and actual_direction != "unknown"
    hit = predicted == actual_direction if scorable else None
    confidence = probability if probability is not None else 0.0
    return {
        "predicted_direction": predicted,
        "actual_direction": actual_direction,
        "start_price": before,
        "end_price": after,
        "actual_return_percent": actual_return,
        "verdict": "hit" if hit is True else "miss" if hit is False else "unscorable",
        "score": 1.0 if hit is True else 0.0 if hit is False else None,
        "brier_score": round((confidence - (1.0 if hit else 0.0)) ** 2, 6)
        if hit is not None and probability is not None
        else None,
    }


def _symbol_price(
    observation: dict[str, Any], symbol: str | None, phase: str
) -> float | None:
    if not symbol:
        return None
    normalized = symbol.upper()
    if normalized == str(((observation.get("market") or {}).get("primary") or {}).get("symbol") or "").upper():
        quote = (observation.get("market") or {}).get("primary") or {}
        return _phase_price(quote, phase)
    item = next(
        (
            value
            for value in observation.get("instruments") or []
            if isinstance(value, dict)
            and str(value.get("symbol") or "").upper() == normalized
        ),
        None,
    )
    if not isinstance(item, dict):
        return None
    quote = item.get("quote") or {}
    return _phase_price(quote, phase)


def _phase_price(quote: dict[str, Any], phase: str) -> float | None:
    if phase == "pre_market":
        return _first_number(
            quote.get("premarket_price"),
            quote.get("last_price"),
            quote.get("regular_price"),
        )
    if phase == "post_close_review":
        # Daily forecasts end at the official regular close.  An after-hours
        # last price belongs to a different horizon and must not replace it.
        return _first_number(quote.get("regular_price"), quote.get("last_price"))
    return _first_number(quote.get("last_price"), quote.get("regular_price"))


def _phase_session_error(observation: dict[str, Any], phase: str) -> str | None:
    primary = (observation.get("market") or {}).get("primary") or {}
    session = str(primary.get("session") or "").strip().lower()
    if not session:
        # Older frozen fixtures predate session metadata.  They remain
        # scorable, while current packets with explicit contradictory session
        # metadata are rejected.
        return None
    expected = {
        "pre_market": {"premarket"},
        "pre_close": {"regular"},
        "post_close_review": {"afterhours", "overnight", "postmarket", "closed"},
    }.get(phase, set())
    if expected and session not in expected:
        return (
            f"{phase} observation has market.session={session!r}; "
            f"expected one of {sorted(expected)}"
        )
    return None


def _return_percent(before: float | None, after: float | None) -> float | None:
    if before is None or after is None or before == 0:
        return None
    return round((after - before) / abs(before) * 100, 6)


def _direction(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value > FLAT_RETURN_THRESHOLD_PERCENT:
        return "up"
    if value < -FLAT_RETURN_THRESHOLD_PERCENT:
        return "down"
    return "flat"


def _hit_rate(results: list[dict[str, Any]]) -> float | None:
    scores = [float(item["score"]) for item in results if item.get("score") is not None]
    return round(sum(scores) / len(scores), 6) if scores else None


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _first_number(*values: Any) -> float | None:
    return next((number for value in values if (number := _number(value)) is not None), None)


def _wall_level(walls: dict[str, Any], side: str) -> float | None:
    for key in (f"{side}_wall", f"{side}_gamma_wall", f"largest_{side}_gex"):
        value = walls.get(key)
        if isinstance(value, dict):
            level = _first_number(value.get("strike"), value.get("level"))
        else:
            level = _number(value)
        if level is not None:
            return level
    return None
