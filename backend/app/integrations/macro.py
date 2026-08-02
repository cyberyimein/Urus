from __future__ import annotations

from datetime import datetime
from typing import Protocol


class MacroContextAdapter(Protocol):
    def daily_context(self, cutoff_time: datetime) -> dict[str, object]: ...


class FallbackDailyMacroAdapter:
    """Collect both sources and prefer Yahoo for indicators it provides.

    Yahoo is intentionally requested on every run because VIX and the Yahoo
    10Y/30Y proxies are required research inputs; those observations become
    selected values and the FRED values are retained under ``cross_checks``.
    FRED stays authoritative for the 2Y constant-maturity series.
    """

    _required_keys = {"vix", "us_2y_yield", "us_10y_yield", "us_30y_yield"}
    _yahoo_preferred_keys = {"vix", "us_10y_yield", "us_30y_yield"}

    def __init__(self, primary: MacroContextAdapter, fallback: MacroContextAdapter) -> None:
        self.primary = primary
        self.fallback = fallback

    def close(self) -> None:
        for adapter in (self.primary, self.fallback):
            close = getattr(adapter, "close", None)
            if close:
                close()

    def daily_context(self, cutoff_time: datetime) -> dict[str, object]:
        primary_result = self.primary.daily_context(cutoff_time)
        fallback_result = self.fallback.daily_context(cutoff_time)
        primary_observations = dict(primary_result.get("observations", {}))
        fallback_observations = dict(fallback_result.get("observations", {}))
        merged_observations = dict(primary_observations)
        cross_checks: dict[str, dict[str, object]] = {}
        for key, value in fallback_observations.items():
            if key in self._yahoo_preferred_keys:
                if primary_observations.get(key):
                    cross_checks[f"{key}_fred"] = primary_observations[key]
                merged_observations[key] = value
            elif key in primary_observations:
                cross_checks[f"{key}_yahoo"] = value
            else:
                merged_observations[key] = value

        derived = dict(primary_result.get("derived", {}))
        two_year = merged_observations.get("us_2y_yield")
        ten_year = merged_observations.get("us_10y_yield")
        if two_year and ten_year:
            derived["us_2s10s_spread"] = {
                "label": "10Y-2Y Treasury spread",
                "unit": "percentage_points",
                "value": round(float(ten_year["value"]) - float(two_year["value"]), 4),
                "as_of": min(str(two_year["as_of"]), str(ten_year["as_of"])),
                "source": "derived_from_selected_sources",
            }

        missing = sorted(self._required_keys - set(merged_observations))
        yahoo_preferred_missing = sorted(
            self._yahoo_preferred_keys - set(fallback_observations)
        )
        if not merged_observations:
            quality_status = "unavailable"
        elif missing or yahoo_preferred_missing:
            quality_status = "partial"
        else:
            quality_status = "ok"

        warnings = [str(item) for item in primary_result.get("quality_warnings", [])]
        if yahoo_preferred_missing:
            warnings.append(
                "Yahoo 优先宏观指标获取不完整，已回退到 FRED（如可用）："
                f"{', '.join(yahoo_preferred_missing)}。"
            )
        if missing:
            warnings.append(f"宏观上下文仍缺失：{', '.join(missing)}。")
        for warning in fallback_result.get("quality_warnings", []):
            value = str(warning)
            if "2Y Treasury" in value and "us_2y_yield" in primary_observations:
                continue
            warnings.append(value)
        errors = [str(item) for item in primary_result.get("quality_errors", [])]
        errors.extend(f"Yahoo: {item}" for item in fallback_result.get("quality_errors", []))

        return {
            "is_mock": False,
            "data_mode": "mixed",
            "source": "fred+yahoo",
            "market_date": primary_result.get("market_date") or fallback_result.get("market_date"),
            "collected_at": primary_result.get("collected_at") or fallback_result.get("collected_at"),
            "observations": merged_observations,
            "derived": derived,
            "quality_status": quality_status,
            "quality_warnings": warnings,
            "quality_errors": errors,
            "cross_checks": cross_checks,
            "yahoo": {
                "required": True,
                "vix_available": "vix" in fallback_observations,
                "preferred_keys": sorted(self._yahoo_preferred_keys),
                "selected_keys": sorted(
                    self._yahoo_preferred_keys & set(fallback_observations)
                ),
                "source": fallback_result.get("source", "yahoo_chart"),
                "quality_status": fallback_result.get("quality_status"),
                "observations": sorted(fallback_observations),
            },
        }
