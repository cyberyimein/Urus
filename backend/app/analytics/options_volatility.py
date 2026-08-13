from __future__ import annotations

from typing import Any, Mapping


def enrich_option_overview(overview: Mapping[str, Any]) -> dict[str, Any]:
    """Add deterministic IV/HV research features to one provider overview.

    Moomoo currently supplies a composite IV and HV30 in percentage points.
    Until a point-in-time 30D ATM series is available, the composite IV is an
    explicitly labelled proxy rather than a matched-term volatility measure.
    """

    result = dict(overview)
    iv = _number(overview.get("iv"))
    hv30 = _number(overview.get("hv_30d"))
    warnings: list[str] = []
    spread = round(iv - hv30, 6) if iv is not None and hv30 is not None else None
    ratio = round(iv / hv30, 6) if iv is not None and hv30 is not None and hv30 > 0 else None
    if iv is None:
        warnings.append("composite_iv_unavailable")
    if hv30 is None or hv30 <= 0:
        warnings.append("hv30_unavailable_or_non_positive")

    result.update(
        {
            "matched_term_iv": iv,
            "matched_term_days": None,
            "term_match_method": "provider_composite_proxy",
            "annualization_basis": "provider_defined",
            "iv_hv_unit": "percentage_points",
            "iv_hv_spread": spread,
            "iv_hv_ratio": ratio,
            "iv_hv_regime": classify_iv_hv_ratio(ratio),
            "iv_hv_percentile": None,
            "iv_hv_history_count": 0,
            "hv_trend_10d": None,
            "hv_trend_20d": None,
            "hv_trend_60d": None,
            "event_adjusted_flag": "unknown",
            "long_vol_score": None,
            "short_vol_score": None,
            "score_type": "not_calibrated",
            "model_fidelity": "proxy",
            "iv_hv_warnings": warnings,
        }
    )
    return result


def classify_iv_hv_ratio(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 0.70:
        return "deep_discount"
    if value < 0.90:
        return "moderate_discount"
    if value < 1.10:
        return "matched"
    if value < 1.40:
        return "moderate_premium"
    return "large_premium"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


__all__ = ["classify_iv_hv_ratio", "enrich_option_overview"]
