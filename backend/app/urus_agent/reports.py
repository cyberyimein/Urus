from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

from app.analytics.options import build_post_close_option_alignment
from app.urus_agent.evidence import EvidenceStore


TECHNICAL_REPORT_SCHEMA = "urus.technical_report.v1"
DECISION_REPORT_SCHEMA = "urus.ai_decision_report.v5"
FLAT_RETURN_THRESHOLD_PERCENT = 0.15
OBJECTIVE_EVALUATION_METHOD = "programmatic_session_multidimensional_v4"


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
    post_close_option_alignment = (
        build_post_close_option_alignment(
            post_close.get("options") or {},
            _post_close_close_quotes(post_close),
        )
        if post_close
        else None
    )
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
            "post_close_alignment": post_close_option_alignment,
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
        "capital_flows": {
            "pre_market": pre_market.get("capital_flows") or {},
            "pre_close": pre_close.get("capital_flows") or {},
            "post_close_review": post_close.get("capital_flows") or {},
            "current_state": current_state.get("capital_flows") or {},
            "current_phase": current_phase,
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
    is_premarket_composite = (
        (equity_output or {}).get("schema_version")
        == "urus.premarket_composite_decision.v1"
    )
    if is_premarket_composite:
        forecast_rows = list((equity_output or {}).get("instrument_forecasts") or [])
        forecast_by_symbol = {
            str(item.get("symbol") or "").upper(): item.get("instrument_forecast")
            for item in forecast_rows
            if isinstance(item, dict)
        }
        rankings = [
            {
                "rank": index,
                "symbol": item.get("symbol"),
                "themes": list(item.get("themes") or []),
                "instrument_forecast": item.get("instrument_forecast"),
            }
            for index, item in enumerate(forecast_rows, start=1)
            if isinstance(item, dict)
        ]
        attention_rankings = [
            {
                **item,
                "instrument_forecast": forecast_by_symbol.get(
                    str(item.get("symbol") or "").upper()
                ),
            }
            for item in (equity_output or {}).get("attention_rankings") or []
            if isinstance(item, dict)
        ]
    else:
        rankings = list((equity_output or {}).get("rankings") or [])
        attention_rankings = (
            rankings[:5] if decision_phase == "pre_market" else rankings
        )
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
    focused_review = (
        equity_output
        if (equity_output or {}).get("schema_version") == "urus.post_close_review.v1"
        else None
    )
    review = copy.deepcopy((equity_output or {}).get("review"))
    if isinstance(focused_review, dict):
        structured_lessons = [
            str(candidate.get("statement") or "").strip()
            for candidate in focused_review.get("experience_candidates") or []
            if isinstance(candidate, dict) and str(candidate.get("statement") or "").strip()
        ]
        review = {
            "session_summary": focused_review.get("session_summary") or "",
            "market_outcome": focused_review.get("market_outcome") or "",
            "theme_outcomes": list(focused_review.get("material_changes") or []),
            "pre_market_evaluation": {
                "report_id": None,
                "verdict": "unscorable",
                "score": None,
                "explanation": focused_review.get("pre_market_explanation") or "",
            },
            "pre_close_evaluation": None,
            "forecast_errors": _objective_forecast_errors(objective_evaluation),
            "lessons": structured_lessons
            or list(focused_review.get("lessons") or []),
            "next_session_carry": list(focused_review.get("next_session_carry") or []),
        }
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
        # Keep the complete table for deterministic scoring and audit. The
        # research workspace renders the bounded attention projection.
        "rankings": rankings,
        "attention_rankings": attention_rankings,
        "candidate_gate": candidate_gate,
        "option_decisions": option_decisions,
        "equity_option_context": equity_option_context or [],
        "portfolio_warnings": (equity_output or {}).get("portfolio_warnings") or [],
        "forecast_horizon": (equity_output or {}).get("forecast_horizon"),
        "forecast": (equity_output or {}).get("forecast"),
        "review": review,
        "objective_evaluation": objective_evaluation,
        "experience_candidates": (equity_output or {}).get("experience_candidates") or [],
        "quality": quality,
        "execution_ready": False,
        "generated_at": datetime.now(UTC).isoformat(),
        "disclaimer": "Research output only; no order was placed.",
    }


def _objective_forecast_errors(evaluation: dict[str, Any]) -> list[str]:
    """Render only deterministic misses; explanations remain a separate AI field."""

    if evaluation.get("status") != "completed":
        return []
    errors: list[str] = []
    phase_result = next(
        (
            item
            for item in evaluation.get("phase_evaluations") or []
            if isinstance(item, dict) and item.get("phase") == "pre_market"
        ),
        None,
    )
    if isinstance(phase_result, dict) and phase_result.get("verdict") in {"miss", "partial"}:
        actual_return = _number(phase_result.get("actual_return_percent"))
        return_text = f", QQQ return {actual_return:+.2f}%" if actual_return is not None else ""
        errors.append(
            "Pre-market market forecast: "
            f"predicted {phase_result.get('predicted_direction') or 'unknown'}, "
            f"actual {phase_result.get('actual_direction') or 'unknown'}{return_text}; "
            f"verdict {phase_result.get('verdict')}, score {phase_result.get('score')}."
        )
        theme = (phase_result.get("dimension_results") or {}).get("theme_leadership") or {}
        if theme.get("verdict") in {"miss", "partial"}:
            errors.append(
                "Pre-market theme leadership: expected leaders "
                f"{', '.join(theme.get('expected_leaders') or []) or 'none'}, actual leaders "
                f"{', '.join(theme.get('actual_leaders') or []) or 'none'}; "
                f"verdict {theme.get('verdict')}, score {theme.get('score')}."
            )
    misses = [
        item
        for item in evaluation.get("instrument_results") or []
        if isinstance(item, dict) and item.get("verdict") in {"miss", "partial"}
    ]
    misses.sort(
        key=lambda item: abs(_number(item.get("actual_return_percent")) or 0.0),
        reverse=True,
    )
    for item in misses[: max(0, 10 - len(errors))]:
        actual_return = _number(item.get("actual_return_percent"))
        return_text = f" ({actual_return:+.2f}%)" if actual_return is not None else ""
        errors.append(
            f"{item.get('symbol')}: predicted {item.get('predicted_direction')}, "
            f"actual {item.get('actual_direction')}{return_text}; "
            f"verdict {item.get('verdict')}."
        )
    return errors


def build_post_close_review_projection(
    evidence: EvidenceStore,
    objective_evaluation: dict[str, Any],
) -> dict[str, Any]:
    """Build the compact, read-only input for completed-session review.

    The projection contains only forecast claims, their deterministic outcomes,
    market-level observations and bounded inherited experience. It deliberately
    omits full Technical Report sections, option chains and unforecasted symbol
    narration.
    """

    packet = evidence.packet
    observations = packet.get("observations") or {}
    prior = (packet.get("prior_reports") or {}).get("same_day_pre_market") or {}
    instrument_results = [
        item
        for item in objective_evaluation.get("instrument_results") or []
        if isinstance(item, dict)
    ]
    focus_symbols = [
        str(item.get("symbol") or "").upper()
        for item in sorted(
            instrument_results,
            key=lambda item: (
                0 if item.get("verdict") == "miss" else 1,
                0 if item.get("verdict") == "hit" else 1,
            ),
        )
        if item.get("symbol")
    ][:12]
    claims = []
    prior_attention = prior.get("attention_rankings") or prior.get("rankings") or []
    for ranking in prior_attention:
        if not isinstance(ranking, dict):
            continue
        symbol = str(ranking.get("symbol") or "").upper()
        if symbol not in focus_symbols:
            continue
        claims.append(
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
            }
        )

    def compact_quote(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return {
            key: value.get(key)
            for key in (
                "symbol",
                "regular_price",
                "premarket_price",
                "change_percent",
                "regular_change_percent",
                "previous_close",
                "session",
                "quote_time",
                "quality_status",
            )
            if key in value
        }

    market_snapshots: dict[str, Any] = {}
    for phase in ("pre_market", "pre_close", "post_close_review"):
        market = (observations.get(phase) or {}).get("market") or {}
        cross_asset = market.get("cross_asset_quotes") or {}
        if isinstance(cross_asset, list):
            cross_asset = {
                str(item.get("symbol") or "").upper(): item
                for item in cross_asset
                if isinstance(item, dict) and item.get("symbol")
            }
        market_snapshots[phase] = {
            "primary": compact_quote(market.get("primary") or {}),
            "cross_asset_quotes": {
                symbol: compact_quote(value)
                for symbol, value in cross_asset.items()
                if symbol in {"SPY", "QQQ", "SMH", "SOXX", "IGV", "IWM", "RSP", "VIX", "TLT"}
            }
            if isinstance(cross_asset, dict)
            else {},
        }

    events = [
        {
            key: item.get(key)
            for key in ("id", "category", "subject", "title", "scheduled_at", "status", "result_state")
            if key in item
        }
        for item in (packet.get("events") or {}).get("records") or []
        if isinstance(item, dict)
    ][:10]
    return {
        "schema_version": "urus.post_close_review_projection.v1",
        "trading_date": (packet.get("decision_context") or {}).get("trading_date"),
        "pre_market_report": {
            "report_id": prior.get("report_id") or prior.get("session_id"),
            "forecast": prior.get("forecast") or {},
            "portfolio_warnings": list(prior.get("portfolio_warnings") or [])[:10],
            "focused_instrument_claims": claims,
        },
        "objective_evaluation": objective_evaluation,
        "market_snapshots": market_snapshots,
        "systematic_flows": (
            (observations.get("post_close_review") or {}).get("systematic_flows") or {}
        ),
        "events": events,
        "quality": packet.get("quality") or {},
        "prior_experiences": list(packet.get("prior_experiences") or [])[:8],
    }


def build_premarket_decision_projection(
    evidence: EvidenceStore,
    symbols: list[str],
    *,
    equity_option_context: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compile the complete bounded input for the single pre-market invocation.

    This is the deep evidence seam for the pre-market workflow. The model sees
    one immutable projection instead of learning the ordering and arguments of
    market, theme and synthesis tool calls. Raw option chains, prior rankings
    and unrelated events are deliberately excluded.
    """

    packet = evidence.packet
    observations = packet.get("observations") or {}
    current = observations.get(evidence.current_phase) or {}
    allowed = {str(symbol).upper() for symbol in symbols if symbol}

    def pick(value: Any, fields: tuple[str, ...]) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return {field: value.get(field) for field in fields if field in value}

    quote_fields = (
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
        "quote_time",
        "session",
        "session_price_source",
        "source",
        "data_mode",
        "quality_status",
        "quality_warnings",
    )
    event_fields = (
        "id",
        "category",
        "subject",
        "title",
        "status",
        "scheduled_at",
        "released_at",
        "importance",
        "actual",
        "consensus",
        "previous",
        "source",
    )
    instruments: list[dict[str, Any]] = []
    for item in current.get("instruments") or []:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").upper()
        if not symbol or symbol not in allowed:
            continue
        instruments.append(
            {
                **pick(item, ("symbol", "asset_type", "theme", "themes")),
                "quote": pick(item.get("quote"), quote_fields),
                "trend": item.get("trend"),
                "technical": item.get("technical") or {},
                "relative_strength": item.get("relative_strength") or {},
                "quality_status": item.get("quality_status"),
                "quality_warnings": list(item.get("quality_warnings") or [])[:8],
                "evidence_paths": {
                    "quote": f"observations.pre_market.instruments[{symbol}].quote",
                    "technical": f"observations.pre_market.instruments[{symbol}].technical",
                    "relative_strength": (
                        f"observations.pre_market.instruments[{symbol}].relative_strength"
                    ),
                    "options": f"observations.pre_market.options.symbols[{symbol}]",
                },
            }
        )

    market = current.get("market") or {}
    compact_market = {
        "primary": pick(market.get("primary"), quote_fields),
        "trend": market.get("trend"),
        "technical": market.get("technical") or {},
        "cross_asset_quotes": [
            pick(item, quote_fields)
            for item in market.get("cross_asset_quotes") or []
            if isinstance(item, dict)
        ],
        "vix": market.get("vix") or {},
        "quality_status": market.get("quality_status"),
        "quality_warnings": list(market.get("quality_warnings") or [])[:8],
        "evidence_paths": {
            "market": "observations.pre_market.market",
            "systematic_flows": "observations.pre_market.systematic_flows",
        },
    }

    event_records = []
    for item in (packet.get("events") or {}).get("records") or []:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "")
        subject = str(item.get("subject") or "").upper()
        if category == "macro" or not subject or subject in allowed:
            event_records.append(pick(item, event_fields))
        if len(event_records) >= 30:
            break

    prior_reports = packet.get("prior_reports") or {}
    previous_review = prior_reports.get("previous_post_close")
    compact_previous_review = (
        pick(
            previous_review,
            (
                "report_id",
                "cutoff_time",
                "decision_phase",
                "status",
                "market_regime",
                "forecast",
                "review",
                "portfolio_warnings",
            ),
        )
        if isinstance(previous_review, dict)
        else None
    )
    return {
        "projection_version": "urus.premarket_decision_projection.v1",
        "phase": evidence.current_phase,
        "market": compact_market,
        "instruments": instruments,
        "systematic_flows": current.get("systematic_flows") or {},
        "capital_flows": current.get("capital_flows") or {},
        "events": event_records,
        "quality": packet.get("quality") or {},
        "previous_post_close": compact_previous_review,
        "prior_experiences": [
            item
            for item in (packet.get("prior_experiences") or [])[:8]
            if isinstance(item, dict)
        ],
        "equity_option_context": list(equity_option_context or []),
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
    forecast = report.get("forecast") or {}
    predicted = {
        "bullish": "up",
        "bearish": "down",
        "range": "flat",
        "mixed": "mixed",
    }.get(str(forecast.get("direction") or ""), "unscorable")
    before = _symbol_price(baseline, "QQQ", baseline_phase)
    after = _symbol_price(close, "QQQ", "post_close_review")
    if predicted == "mixed":
        returns = {
            symbol: _return_percent(
                _symbol_price(baseline, symbol, baseline_phase),
                _symbol_price(close, symbol, "post_close_review"),
            )
            for symbol in ("SPY", "QQQ", "SMH", "SOXX", "IGV")
        }
        directions = {
            _direction(value) for value in returns.values() if value is not None
        }
        actual_direction = "mixed" if len(directions) >= 2 else next(iter(directions), "unknown")
        hit = actual_direction == "mixed" if actual_direction != "unknown" else None
        confidence = _number(forecast.get("confidence"))
        direction_result = {
            "predicted_direction": predicted,
            "actual_direction": actual_direction,
            "start_price": before,
            "end_price": after,
            "actual_return_percent": _return_percent(before, after),
            "verdict": "hit" if hit is True else "miss" if hit is False else "unscorable",
            "score": 1.0 if hit is True else 0.0 if hit is False else None,
            "brier_score": round((confidence - (1.0 if hit else 0.0)) ** 2, 6)
            if hit is not None and confidence is not None
            else None,
            "constituent_returns": returns,
        }
    else:
        direction_result = _score_direction(
            predicted=predicted,
            probability=_number(forecast.get("confidence")),
            before=before,
            after=after,
        )
    theme_result = _score_theme_leadership(
        forecast,
        baseline,
        close,
        baseline_phase,
    )
    scores = [
        float(value)
        for value in (direction_result.get("score"), theme_result.get("score"))
        if value is not None
    ]
    overall_score = round(sum(scores) / len(scores), 6) if scores else None
    overall_verdict = (
        "hit"
        if overall_score is not None and overall_score >= 0.75
        else "partial"
        if overall_score is not None and overall_score >= 0.4
        else "miss"
        if overall_score is not None
        else "unscorable"
    )
    return {
        **direction_result,
        "verdict": overall_verdict,
        "score": overall_score,
        "dimension_results": {
            "market_direction": direction_result,
            "theme_leadership": theme_result,
            "path_conditions": {
                "verdict": "unscorable",
                "reason": "Free-text path and conditions are retained for explanation but are not parsed into deterministic scores.",
            },
        },
    }


def _score_theme_leadership(
    forecast: dict[str, Any],
    baseline: dict[str, Any],
    close: dict[str, Any],
    baseline_phase: str,
) -> dict[str, Any]:
    aliases = {
        "SPY": {"spy", "broad market", "large cap", "美国大盘", "大盘"},
        "QQQ": {"qqq", "big tech", "mega cap", "大科技", "科技大盘"},
        "SMH": {"smh", "semiconductor", "semiconductors", "半导体"},
        "SOXX": {"soxx", "semiconductor", "semiconductors", "半导体"},
        "IGV": {"igv", "software", "saas", "软件"},
    }

    def symbols(values: Any) -> set[str]:
        result: set[str] = set()
        for raw in values if isinstance(values, list) else []:
            normalized = str(raw).strip().lower()
            for symbol, names in aliases.items():
                if normalized in names or any(name in normalized for name in names):
                    result.add(symbol)
        return result

    expected_leaders = symbols(forecast.get("leading_themes"))
    expected_laggards = symbols(forecast.get("lagging_themes"))
    if not expected_leaders and not expected_laggards:
        return {"verdict": "unscorable", "score": None, "reason": "No scoreable ETF theme labels."}
    returns = {
        symbol: _return_percent(
            _symbol_price(baseline, symbol, baseline_phase),
            _symbol_price(close, symbol, "post_close_review"),
        )
        for symbol in aliases
    }
    ranked = [
        symbol
        for symbol, value in sorted(
            returns.items(), key=lambda item: item[1] if item[1] is not None else float("-inf"), reverse=True
        )
        if value is not None
    ]
    if len(ranked) < 2:
        return {"verdict": "unscorable", "score": None, "reason": "Theme ETF prices are incomplete."}
    actual_leaders = set(ranked[:2])
    actual_laggards = set(ranked[-2:])
    available = {symbol for symbol, value in returns.items() if value is not None}
    scoreable_leaders = expected_leaders & available
    scoreable_laggards = expected_laggards & available
    checks = [
        *(1.0 if symbol in actual_leaders else 0.0 for symbol in scoreable_leaders),
        *(1.0 if symbol in actual_laggards else 0.0 for symbol in scoreable_laggards),
    ]
    score = round(sum(checks) / len(checks), 6) if checks else None
    return {
        "verdict": "hit" if score is not None and score >= 0.75 else "partial" if score else "miss",
        "score": score,
        "expected_leaders": sorted(expected_leaders),
        "expected_laggards": sorted(expected_laggards),
        "actual_leaders": sorted(actual_leaders),
        "actual_laggards": sorted(actual_laggards),
        "unavailable_expected": sorted(
            (expected_leaders | expected_laggards) - available
        ),
        "returns": returns,
    }


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
    range_result = _binary_dimension(
        range_hit,
        reason="Expected return range or actual return is unavailable.",
    )
    relative_to = str(forecast.get("relative_to") or "none")
    benchmark_symbol = _relative_benchmark_symbol(ranking, relative_to)
    benchmark_return = _return_percent(
        _symbol_price(baseline, benchmark_symbol, phase),
        _symbol_price(close, benchmark_symbol, "post_close_review"),
    ) if benchmark_symbol else None
    relative_return = (
        round(actual_return - benchmark_return, 6)
        if actual_return is not None and benchmark_return is not None
        else None
    )
    predicted_relative = str(forecast.get("relative_direction") or "unknown")
    actual_relative = _relative_direction(relative_return)
    relative_scorable = (
        predicted_relative in {"outperform", "underperform", "inline"}
        and actual_relative != "unknown"
    )
    relative_hit = predicted_relative == actual_relative if relative_scorable else None
    relative_result = _binary_dimension(
        relative_hit,
        reason="Relative benchmark, forecast, or close return is unavailable.",
    )
    dimension_results = {
        "direction": {
            key: scored.get(key)
            for key in (
                "verdict",
                "score",
                "predicted_direction",
                "actual_direction",
                "brier_score",
            )
        },
        "expected_return_range": {
            **range_result,
            "minimum_percent": minimum,
            "maximum_percent": maximum,
            "actual_return_percent": actual_return,
        },
        "relative_performance": {
            **relative_result,
            "relative_to": relative_to,
            "benchmark_symbol": benchmark_symbol,
            "predicted_direction": predicted_relative,
            "actual_direction": actual_relative,
            "relative_return_percent": relative_return,
        },
    }
    dimension_scores = [
        float(item["score"])
        for item in dimension_results.values()
        if item.get("score") is not None
    ]
    overall_score = (
        round(sum(dimension_scores) / len(dimension_scores), 6)
        if dimension_scores
        else None
    )
    overall_verdict = _aggregate_verdict(overall_score)
    return {
        "phase": phase,
        "symbol": symbol,
        **scored,
        "verdict": overall_verdict,
        "score": overall_score,
        "expected_range_hit": range_hit,
        "relative_to": relative_to,
        "relative_direction": predicted_relative,
        "actual_relative_direction": actual_relative,
        "relative_benchmark_symbol": benchmark_symbol,
        "relative_return_percent": relative_return,
        "dimension_results": dimension_results,
    }


def _binary_dimension(hit: bool | None, *, reason: str) -> dict[str, Any]:
    if hit is None:
        return {"verdict": "unscorable", "score": None, "reason": reason}
    return {"verdict": "hit" if hit else "miss", "score": 1.0 if hit else 0.0}


def _aggregate_verdict(score: float | None) -> str:
    if score is None:
        return "unscorable"
    if score >= 0.75:
        return "hit"
    if score >= 0.4:
        return "partial"
    return "miss"


def _relative_benchmark_symbol(ranking: dict[str, Any], relative_to: str) -> str | None:
    if relative_to in {"QQQ", "SPY"}:
        return relative_to
    if relative_to != "theme_benchmark":
        return None
    symbol = str(ranking.get("symbol") or "").upper()
    themes = " ".join(
        str(value).strip().lower() for value in ranking.get("themes") or []
    )
    if any(value in themes for value in ("半导体", "semiconductor", "光概念", "optical")):
        return "SOXX" if symbol == "SMH" else "SMH"
    if any(value in themes for value in ("saas", "software", "软件")):
        return "IGV"
    if any(value in themes for value in ("大科技", "big tech", "mega cap")):
        return "QQQ"
    return "SPY"


def _relative_direction(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value > FLAT_RETURN_THRESHOLD_PERCENT:
        return "outperform"
    if value < -FLAT_RETURN_THRESHOLD_PERCENT:
        return "underperform"
    return "inline"


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


def _post_close_close_quotes(
    observation: dict[str, Any],
) -> dict[str, dict[str, object]]:
    """Index the best post-close quote for each symbol.

    Regular-session close is preferred over the raw last price because the
    latter can be an after-hours print.  The source and fallback kind are
    retained for the report so the UI can make the comparison auditable.
    """

    selected: dict[str, dict[str, object]] = {}

    def consider(value: Any, path: str) -> None:
        if not isinstance(value, dict):
            return
        nested_quote = value.get("quote")
        quote = nested_quote if isinstance(nested_quote, dict) else value
        symbol = str(value.get("symbol") or quote.get("symbol") or "").upper()
        if not symbol:
            return
        regular_price = _first_number(quote.get("regular_price"))
        last_price = _first_number(quote.get("last_price"))
        if regular_price is not None:
            candidate = {
                "price": regular_price,
                "price_kind": "regular_price",
                "source_path": f"{path}.regular_price",
                "quote_time": quote.get("quote_time"),
            }
            rank = 2
        elif last_price is not None:
            candidate = {
                "price": last_price,
                "price_kind": "last_price_fallback",
                "source_path": f"{path}.last_price",
                "quote_time": quote.get("quote_time"),
            }
            rank = 1
        else:
            return
        existing = selected.get(symbol)
        if existing is None or rank > int(existing.get("_rank") or 0):
            candidate["_rank"] = rank
            selected[symbol] = candidate

    market = observation.get("market") or {}
    if isinstance(market, dict):
        consider(market.get("primary"), "observations.post_close_review.market.primary")
        cross_asset_quotes = market.get("cross_asset_quotes") or []
        if isinstance(cross_asset_quotes, list):
            for item in cross_asset_quotes:
                if isinstance(item, dict):
                    symbol = str(item.get("symbol") or "").upper()
                    if symbol:
                        consider(
                            item,
                            f"observations.post_close_review.market.cross_asset_quotes[{symbol}]",
                        )
        elif isinstance(cross_asset_quotes, dict):
            for symbol, item in cross_asset_quotes.items():
                if isinstance(item, dict):
                    candidate = dict(item)
                    candidate.setdefault("symbol", symbol)
                    normalized = str(symbol or "").upper()
                    if normalized:
                        consider(
                            candidate,
                            f"observations.post_close_review.market.cross_asset_quotes[{normalized}]",
                        )

    for item in observation.get("instruments") or []:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").upper()
        if symbol:
            path = (
                f"observations.post_close_review.instruments[{symbol}].quote"
                if isinstance(item.get("quote"), dict)
                else f"observations.post_close_review.instruments[{symbol}]"
            )
            consider(item, path)

    for item in selected.values():
        item.pop("_rank", None)
    return selected


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
