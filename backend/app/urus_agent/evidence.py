from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Iterable

from app.core.time import as_utc


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()


def _pick(value: dict[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    return {key: value.get(key) for key in keys if key in value}


def _as_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _quote(value: dict[str, Any]) -> dict[str, Any]:
    return _pick(
        value,
        (
            "symbol", "label", "last_price", "regular_price", "change_percent",
            "regular_change_percent", "previous_close", "open_price", "high_price",
            "low_price", "volume", "premarket_price", "premarket_volume",
            "premarket_change_percent", "afterhours_price", "afterhours_volume",
            "afterhours_change_percent", "quote_time", "session", "session_label",
            "session_price_source", "source", "provider", "data_mode", "source_mode",
            "quality_status", "quality_warnings", "is_mock",
        ),
    )


_PATH_TOKEN = re.compile(r"([^\.\[\]]+)|\[([^\]]+)\]")

# Models occasionally copy the compact ``current_state_evidence`` shape from
# task metadata into an evidence reference.  That shape exposes the market
# quote as ``primary`` alongside technical/cross-asset fields, while the
# frozen packet uses the same sibling layout.  These aliases cover a model
# accidentally nesting a sibling field under ``primary``; each candidate is
# still checked against the packet before it can be used.
_PATH_ALIASES = (
    (".market.primary.technical", ".market.technical"),
    (".market.primary.cross_asset_quotes", ".market.cross_asset_quotes"),
    (".market.primary.vix", ".market.vix"),
)


def _path_tokens(path: str) -> list[tuple[str | None, str | None]]:
    return [(match.group(1), match.group(2)) for match in _PATH_TOKEN.finditer(path)]


class EvidenceStore:
    """Read-only, bounded view over a frozen Stage 4B decision packet."""

    def __init__(self, packet: dict[str, Any]) -> None:
        if packet.get("schema_version") != "urus.stage4b_decision_packet.v1":
            raise ValueError("unsupported decision packet schema")
        self.packet = packet
        source = packet.get("source") or {}
        self.dataset_key = str(source.get("dataset_key") or "unknown")
        self.input_hash = str(packet.get("content_sha256") or "")

    @property
    def current_phase(self) -> str:
        context = self.packet.get("decision_context") or {}
        return str(context.get("current_observation") or "pre_close")

    @property
    def comparison_phases(self) -> list[str]:
        context = self.packet.get("decision_context") or {}
        values = context.get("comparison_observations")
        phases = [str(value) for value in values if value] if isinstance(values, list) else []
        return phases or [self.current_phase]

    def has_path(self, path: str) -> bool:
        """Return whether an evidence reference resolves inside this packet.

        References are intentionally a small, read-only path language.  It
        supports dotted object keys and selectors such as
        ``instruments[QQQ]`` or ``events.records[event-key]``; it does not
        evaluate expressions or access arbitrary Python objects.
        """

        normalized = self._normalize_path(path)
        if not normalized:
            return False
        current: Any = self.packet
        for token, selector in _path_tokens(normalized):
            if token:
                if not isinstance(current, dict) or token not in current:
                    return False
                current = current[token]
            if selector is not None:
                if not isinstance(current, list):
                    return False
                if selector.isdigit():
                    index = int(selector)
                    if index < 0 or index >= len(current):
                        return False
                    current = current[index]
                else:
                    match = next(
                        (
                            item
                            for item in current
                            if isinstance(item, dict)
                            and selector
                            in {
                                str(item.get("symbol", "")),
                                str(item.get("id", "")),
                                str(item.get("event_key", "")),
                                str(item.get("definition_key", "")),
                                str(item.get("expiration", "")),
                                str(item.get("strike", "")),
                                str(item.get("contract_id", "")),
                            }
                        ),
                        None,
                    )
                    if match is None:
                        return False
                    current = match
        return True

    @staticmethod
    def _normalize_path(path: str) -> str:
        normalized = str(path or "").strip()
        if normalized.startswith("packet."):
            normalized = normalized.removeprefix("packet.")
        if normalized.startswith("$."):
            normalized = normalized.removeprefix("$.")
        return normalized

    def canonical_path(self, path: str) -> str | None:
        """Return the packet path for an exact or safe structural alias.

        The returned path is always the path that exists in the frozen packet;
        aliases are never accepted merely because their text looks plausible.
        This keeps report links and replay traces navigable when a model uses
        the compact metadata shape instead of the packet shape.
        """

        normalized = self._normalize_path(path)
        if not normalized:
            return None
        if self.has_path(normalized):
            return normalized
        for source, target in _PATH_ALIASES:
            if source in normalized:
                candidate = normalized.replace(source, target, 1)
                if self.has_path(candidate):
                    return candidate
        return None

    @classmethod
    def from_workflow_results(
        cls,
        *,
        run_id: str,
        cutoff_time: datetime,
        results: dict[str, Any],
        snapshot_id: str | None = None,
    ) -> "EvidenceStore":
        def payload(code: str) -> dict[str, Any]:
            result = results.get(code)
            value = getattr(result, "payload", None)
            if value is None and isinstance(result, dict):
                value = result
            return value if isinstance(value, dict) else {}

        market = payload("1a")
        instrument = payload("3a")
        options = payload("2")
        macro_events = payload("1b").get("events", [])
        instrument_events = payload("3b").get("events", [])
        # Local import avoids coupling the Agent evidence module to workflow
        # initialization while preserving the legacy from-results adapter.
        from app.workflows.cta import build_systematic_flows

        systematic_flows = build_systematic_flows(
            payload("1b"), payload("3b"), run_type="pre_close"
        )
        quality_warnings: list[str] = []
        blocking_errors: list[str] = []
        for code, value in (("1a", market), ("2", options), ("3a", instrument)):
            if not isinstance(value, dict):
                continue
            for key in ("quality_warnings", "warnings"):
                entries = value.get(key)
                if isinstance(entries, list):
                    quality_warnings.extend(f"{code}: {item}" for item in entries if item)
            for key in ("quality_errors", "errors", "blocking_errors"):
                entries = value.get(key)
                if isinstance(entries, list):
                    blocking_errors.extend(f"{code}: {item}" for item in entries if item)
            nested_quality = value.get("data_quality")
            if isinstance(nested_quality, dict):
                entries = nested_quality.get("warnings")
                if isinstance(entries, list):
                    quality_warnings.extend(f"{code}: {item}" for item in entries if item)
                entries = nested_quality.get("errors") or nested_quality.get("blocking_errors")
                if isinstance(entries, list):
                    blocking_errors.extend(f"{code}: {item}" for item in entries if item)
        source = {
            "dataset_key": f"run:{run_id}",
            "label": "live workflow decision evidence",
            "captured_at": cutoff_time.isoformat(),
        }
        phase = {
            "run": {"id": run_id, "run_type": "workflow", "cutoff_time": cutoff_time.isoformat()},
            "snapshot": {"id": snapshot_id, "data_mode": market.get("data_mode"), "is_mock": market.get("is_mock", True), "cutoff_time": cutoff_time.isoformat()},
            "market": {
                "primary": _quote(market),
                "trend": market.get("trend"),
                "technical": market.get("history", {}).get("technical_indicators", {})
                if isinstance(market.get("history"), dict) else {},
                "cross_asset_quotes": (market.get("market_snapshot") or {}).get("quotes", []),
                "vix": (market.get("market_snapshot") or {}).get("vix"),
                "quality_status": market.get("quality_status"),
                "quality_warnings": market.get("quality_warnings", []),
            },
            "instruments": _as_list(instrument.get("instruments")),
            "options": {
                "status": options.get("status"),
                "available": options.get("available"),
                "provider": options.get("provider"),
                "source_mode": options.get("source_mode"),
                "warnings": options.get("warnings", []),
                "symbols": _as_list(options.get("symbols")),
            },
            "systematic_flows": systematic_flows,
            "data_quality": {},
        }
        packet = {
            "schema_version": "urus.stage4b_decision_packet.v1",
            "generated_at": cutoff_time.isoformat(),
            "source": source,
            "quality": {
                "status": "blocked" if blocking_errors else "warning" if quality_warnings else "ok",
                "warnings": list(dict.fromkeys(quality_warnings)),
                "blocking_errors": list(dict.fromkeys(blocking_errors)),
            },
            "observations": {"pre_market": phase, "pre_close": phase},
            "paired_changes": {"market": {}, "instruments": [], "options": [], "systematic_flows": {}},
            "events": {"captured_at": cutoff_time.isoformat(), "records": [
                *(_as_list(macro_events)), *(_as_list(instrument_events))
            ]},
            "execution_ready": False,
            "execution_blockers": ["workflow evidence does not contain executable order data"],
        }
        packet["content_sha256"] = hashlib.sha256(_canonical(packet)).hexdigest()
        return cls(packet)

    def _observation(self, phase: str) -> dict[str, Any]:
        observations = self.packet.get("observations") or {}
        value = observations.get(phase) or observations.get(self.current_phase) or {}
        return value if isinstance(value, dict) else {}

    def _evidence(self, phase: str, path: str, value: dict[str, Any] | None = None) -> dict[str, Any]:
        observation = self._observation(phase)
        run = observation.get("run") or {}
        snapshot = observation.get("snapshot") or {}
        source = self.packet.get("source") or {}
        return {
            "dataset_key": self.dataset_key,
            "run_id": run.get("id"),
            "snapshot_id": snapshot.get("id"),
            "phase": phase,
            "path": path,
            "as_of": (value or {}).get("quote_time") if value else snapshot.get("created_at"),
            "cutoff_time": run.get("cutoff_time") or snapshot.get("cutoff_time"),
            "provider": (value or {}).get("provider") or source.get("provider"),
            "source_mode": (value or {}).get("source_mode") or source.get("source_mode"),
        }

    def _result(self, tool: str, data: dict[str, Any], phase: str, path: str, *, warnings: list[str] | None = None, is_mock: bool = False) -> dict[str, Any]:
        quality = self._observation(phase).get("market", {}).get("quality_status", "ok")
        return {
            "tool_schema_version": "urus.agent_tool_result.v1",
            "ok": True,
            "tool": tool,
            "data": data,
            "evidence": self._evidence(phase, path, data),
            "quality": {"status": quality, "warnings": warnings or [], "is_mock": is_mock},
            "truncated": False,
            "next_cursor": None,
        }

    def overview(self, phase: str | None = None) -> dict[str, Any]:
        phase = phase or self.current_phase
        observation = self._observation(phase)
        market = observation.get("market") or {}
        instruments = _as_list(observation.get("instruments"))
        options = observation.get("options") or {}
        return {
            "schema_version": "urus.agent_overview.v1",
            "task_source": self.packet.get("source") or {},
            "phase": phase,
            "market": _quote(market.get("primary") or {}),
            "cross_asset_quotes": [
                _quote(item) for item in _as_list(market.get("cross_asset_quotes"))[:20]
            ],
            "symbols": [
                _pick(item, ("symbol", "asset_type", "theme", "themes", "quality_status", "is_mock"))
                for item in instruments[:100]
            ],
            "option_symbols": [item.get("symbol") for item in _as_list(options.get("symbols"))],
            "event_count": len(_as_list((self.packet.get("events") or {}).get("records"))),
            "quality": self.packet.get("quality") or {},
        }

    def market_regime(self, phase: str, symbols: list[str]) -> dict[str, Any]:
        observation = self._observation(phase)
        market = observation.get("market") or {}
        all_quotes = _as_list(market.get("cross_asset_quotes"))
        requested = {symbol.upper() for symbol in symbols} or {"SPY", "QQQ", "SMH", "IGV"}
        primary = _quote(market.get("primary") or {})
        primary_symbol = str(primary.get("symbol", "")).upper()
        quotes: list[dict[str, Any]] = []
        seen: set[str] = set()
        if primary_symbol in requested:
            quotes.append(primary)
            seen.add(primary_symbol)
        for item in all_quotes:
            symbol = str(item.get("symbol", "")).upper()
            if symbol in requested and symbol not in seen:
                quotes.append(_quote(item))
                seen.add(symbol)
        return self._result("get_market_regime", {"quotes": quotes, "trend": market.get("trend"), "technical": market.get("technical", {}), "vix": market.get("vix")}, phase, "observations.%s.market" % phase, warnings=market.get("quality_warnings") or [], is_mock=bool((market.get("primary") or {}).get("is_mock", False)))

    def instrument_snapshot(self, symbol: str, phase: str, sections: list[str]) -> dict[str, Any]:
        symbol = symbol.upper()
        items = _as_list(self._observation(phase).get("instruments"))
        item = next((item for item in items if str(item.get("symbol", "")).upper() == symbol), None)
        if item is None:
            raise KeyError(f"symbol_not_found:{symbol}")
        quote = item.get("quote") if isinstance(item.get("quote"), dict) else item
        selected = {
            **_pick(item, ("symbol", "asset_type", "theme", "themes", "trend", "technical_note", "quality_status", "quality_warnings", "is_mock", "provider", "source_mode", "captured_at")),
            **_quote(quote),
        }
        if not sections or "technical" in sections:
            selected["technical"] = item.get("technical") or item.get("history") or {}
        if not sections or "relative_strength" in sections:
            selected["relative_strength"] = item.get("relative_strength") or {}
        return self._result("get_instrument_snapshot", selected, phase, f"observations.{phase}.instruments[{symbol}]", warnings=item.get("quality_warnings") or [], is_mock=bool(item.get("is_mock", False)))

    def compare_instrument(self, symbol: str) -> dict[str, Any]:
        symbol = symbol.upper()
        left_phase, right_phase = self.comparison_phases[0], self.current_phase
        left = self.instrument_snapshot(symbol, left_phase, ["quote"])["data"]
        right = self.instrument_snapshot(symbol, right_phase, ["quote"])["data"]
        def delta(field: str) -> dict[str, Any]:
            before, after = left.get(field), right.get(field)
            if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
                return {"before": before, "after": after, "absolute": None, "percent": None}
            absolute = after - before
            return {"before": before, "after": after, "absolute": absolute, "percent": absolute / abs(before) * 100 if before else None}
        return {"tool_schema_version": "urus.agent_tool_result.v1", "ok": True, "tool": "compare_instrument_observations", "data": {"symbol": symbol, "from_phase": left_phase, "to_phase": right_phase, "regular_price": delta("regular_price"), "last_price": delta("last_price"), "change_percent": delta("change_percent"), "volume": delta("volume"), "session": {left_phase: left.get("session") or left.get("session_label"), right_phase: right.get("session") or right.get("session_label")}, "quote_time": {left_phase: left.get("quote_time"), right_phase: right.get("quote_time")}, "technical_confirmation": {left_phase: left.get("trend"), right_phase: right.get("trend")}, "volume_comparison_note": "Session volume is cumulative; observation windows are not like-for-like."}, "evidence": self._evidence(right_phase, f"paired_changes.instruments[{symbol}]"), "quality": {"status": "ok", "warnings": [], "is_mock": bool(left.get("is_mock") or right.get("is_mock"))}, "truncated": False, "next_cursor": None}

    def candidates(
        self,
        themes: list[str],
        asset_types: list[str],
        limit: int,
        quality_status: list[str] | None = None,
        allowed_symbols: set[str] | None = None,
    ) -> dict[str, Any]:
        items = _as_list(self._observation(self.current_phase).get("instruments"))
        theme_set = {str(value).lower() for value in themes}
        type_set = {str(value).lower() for value in asset_types}
        quality_set = {str(value).lower() for value in (quality_status or [])}
        selected = []
        for item in items:
            symbol = str(item.get("symbol") or "").upper()
            if allowed_symbols is not None and symbol not in allowed_symbols:
                continue
            item_themes = {str(value).lower() for value in item.get("themes", [])}
            if item.get("theme"):
                item_themes.add(str(item["theme"]).lower())
            if theme_set and not item_themes.intersection(theme_set):
                continue
            if type_set and str(item.get("asset_type", "")).lower() not in type_set:
                continue
            if quality_set and str(item.get("quality_status", "unknown")).lower() not in quality_set:
                continue
            selected.append(_pick(item, ("symbol", "asset_type", "theme", "themes", "quality_status", "is_mock")))
        return self._result("get_watchlist_candidates", {"items": selected[:max(1, min(limit, 100))]}, self.current_phase, f"observations.{self.current_phase}.instruments")

    def option_overview(self, symbol: str, phase: str) -> dict[str, Any]:
        symbol = symbol.upper()
        options = self._observation(phase).get("options") or {}
        item = next((item for item in _as_list(options.get("symbols")) if str(item.get("symbol", "")).upper() == symbol), None)
        if item is None:
            raise KeyError(f"symbol_not_found:{symbol}")
        data = {"symbol": symbol, "spot": item.get("spot"), "spot_time": item.get("spot_time"), "overview": item.get("overview") or {}, "expirations": [_pick(exp, ("expiration", "days_to_expiry", "contract_count")) for exp in _as_list(item.get("expirations"))], "provider": options.get("provider"), "source_mode": options.get("source_mode"), "warnings": options.get("warnings") or []}
        return self._result("get_option_overview", data, phase, f"observations.{phase}.options.symbols[{symbol}]", warnings=data["warnings"], is_mock=bool(options.get("is_mock", False)))

    def systematic_flows(self, phase: str) -> dict[str, Any]:
        flows = self._observation(phase).get("systematic_flows") or {}
        if not flows:
            return self._result(
                "get_systematic_flows",
                {"available": False, "assets": [], "reason": "systematic_flows_unavailable"},
                phase,
                f"observations.{phase}.systematic_flows",
                warnings=["CTA systematic-flow evidence is unavailable for this observation."],
            )
        quality = flows.get("quality") or {}
        return self._result(
            "get_systematic_flows",
            flows,
            phase,
            f"observations.{phase}.systematic_flows",
            warnings=quality.get("warnings") or [],
        )

    def option_expiration(self, symbol: str, phase: str, expiration: str) -> dict[str, Any]:
        overview = self.option_overview(symbol, phase)
        options = self._observation(phase).get("options") or {}
        item = next(item for item in _as_list(options.get("symbols")) if str(item.get("symbol", "")).upper() == symbol.upper())
        exp = next((exp for exp in _as_list(item.get("expirations")) if exp.get("expiration") == expiration), None)
        if exp is None:
            raise KeyError(f"expiration_not_found:{symbol}:{expiration}")
        data = _pick(exp, ("expiration", "days_to_expiry", "contract_count", "max_pain", "expected_move", "exposure_totals", "walls", "gamma_zone_count", "gamma_zones", "strike_gex_sign_change_count", "strike_gex_sign_changes", "gamma_noise_threshold", "usable_delta_contracts", "usable_gamma_contracts", "spot_gamma_profile"))
        return self._result("get_option_expiration_structure", data, phase, f"observations.{phase}.options.symbols[{symbol}].expirations[{expiration}]", warnings=overview["data"].get("warnings", []), is_mock=bool(options.get("is_mock", False)))

    def compare_option(
        self,
        symbol: str,
        expiration: str,
        from_phase: str | None = None,
        to_phase: str | None = None,
    ) -> dict[str, Any]:
        left_phase = from_phase or self.comparison_phases[0]
        right_phase = to_phase or self.current_phase
        left_overview = self.option_overview(symbol, left_phase)["data"]
        right_overview = self.option_overview(symbol, right_phase)["data"]
        left = self.option_expiration(symbol, left_phase, expiration)["data"]
        right = self.option_expiration(symbol, right_phase, expiration)["data"]
        def delta(before: Any, after: Any) -> dict[str, Any]:
            if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
                return {"before": before, "after": after, "absolute": None, "percent": None}
            absolute = after - before
            return {"before": before, "after": after, "absolute": absolute, "percent": absolute / abs(before) * 100 if before else None}
        left_totals, right_totals = left.get("exposure_totals", {}), right.get("exposure_totals", {})
        left_profile, right_profile = left.get("spot_gamma_profile", {}), right.get("spot_gamma_profile", {})
        left_values = left_overview.get("overview") or {}
        right_values = right_overview.get("overview") or {}
        return {"tool_schema_version": "urus.agent_tool_result.v1", "ok": True, "tool": "compare_option_observations", "data": {"symbol": symbol.upper(), "expiration": expiration, "from_phase": left_phase, "to_phase": right_phase, "spot": delta(left_overview.get("spot"), right_overview.get("spot")), "iv": delta(left_values.get("iv"), right_values.get("iv")), "iv_rank": delta(left_values.get("iv_rank"), right_values.get("iv_rank")), "hv_30d": delta(left_values.get("hv_30d"), right_values.get("hv_30d")), "iv_hv_spread": delta(left_values.get("iv_hv_spread"), right_values.get("iv_hv_spread")), "iv_hv_ratio": delta(left_values.get("iv_hv_ratio"), right_values.get("iv_hv_ratio")), "iv_hv_regime": {left_phase: left_values.get("iv_hv_regime"), right_phase: right_values.get("iv_hv_regime"), "changed": left_values.get("iv_hv_regime") != right_values.get("iv_hv_regime")}, "max_pain": delta(left.get("max_pain"), right.get("max_pain")), "expected_move": delta((left.get("expected_move") or {}).get("amount"), (right.get("expected_move") or {}).get("amount")), "net_dex": delta(left_totals.get("net_dex"), right_totals.get("net_dex")), "modeled_net_gex": delta(left_totals.get("modeled_net_gex"), right_totals.get("modeled_net_gex")), "primary_gamma_flip": delta(left_profile.get("primary_gamma_flip"), right_profile.get("primary_gamma_flip")), "current_spot_net_gex": delta(left_profile.get("current_spot_net_gex"), right_profile.get("current_spot_net_gex")), "walls": {left_phase: left.get("walls"), right_phase: right.get("walls"), "changed": left.get("walls") != right.get("walls")}}, "evidence": self._evidence(right_phase, f"paired_changes.options[{symbol.upper()}].expirations[{expiration}]"), "quality": {"status": "ok", "warnings": [], "is_mock": False}, "truncated": False, "next_cursor": None}

    def prior_stage_reports(self) -> dict[str, Any]:
        reports = self.packet.get("prior_reports") or {}
        return self._result(
            "get_prior_stage_reports",
            {
                "reports": reports,
                "experiences": list(self.packet.get("prior_experiences") or [])[:8],
                "decision_context": self.packet.get("decision_context") or {},
            },
            self.current_phase,
            "prior_reports",
        )

    def events(
        self,
        category: str,
        subject: str | None,
        status: list[str],
        result_state: str,
        limit: int,
        from_time: str | None = None,
        to_time: str | None = None,
        cutoff_time: datetime | None = None,
        allowed_subjects: set[str] | None = None,
    ) -> dict[str, Any]:
        records = _as_list((self.packet.get("events") or {}).get("records"))
        lower = _parse_event_time(from_time) if from_time else None
        upper = _parse_event_time(to_time) if to_time else None
        # The packet is already frozen at task cutoff. A future scheduled_at
        # is therefore information known at the cutoff, not future evidence.
        # Do not hide upcoming earnings or macro releases from risk analysis.
        if cutoff_time is not None:
            cutoff_time = as_utc(cutoff_time)
        selected = []
        for event in records:
            if (
                allowed_subjects is not None
                and str(event.get("category") or "").lower() == "instrument"
                and str(event.get("subject") or "").upper() not in allowed_subjects
            ):
                continue
            if category != "all" and event.get("category") != category:
                continue
            if subject and str(event.get("subject", "")).upper() != subject.upper():
                continue
            if status and event.get("status") not in status:
                continue
            event_time = _parse_event_time(event.get("scheduled_at") or event.get("occurred_at"))
            if lower and (event_time is None or event_time < lower):
                continue
            if upper and (event_time is None or event_time > upper):
                continue
            has_result = bool(event.get("result")) or bool(event.get("result_available_at"))
            if result_state == "missing" and has_result:
                continue
            if result_state == "available" and not has_result:
                continue
            item = _pick(event, ("id", "event_key", "definition_key", "category", "subject", "event_type", "title", "period", "status", "scheduled_at", "occurred_at", "result_expected_at", "result_available_at", "confidence", "result", "sources", "market_reactions"))
            item["sources"] = _bounded_sources(event.get("sources"))
            selected.append(item)
        return self._result("get_events", {"records": selected[:max(1, min(limit, 100))]}, self.current_phase, "events.records")

    def event_result(self, event_id: str, allowed_subjects: set[str] | None = None) -> dict[str, Any]:
        records = _as_list((self.packet.get("events") or {}).get("records"))
        event = next((item for item in records if str(item.get("id")) == str(event_id) or str(item.get("event_key")) == str(event_id)), None)
        if event is None:
            raise KeyError(f"event_not_found:{event_id}")
        if (
            allowed_subjects is not None
            and str(event.get("category") or "").lower() == "instrument"
            and str(event.get("subject") or "").upper() not in allowed_subjects
        ):
            raise KeyError(f"event_scope_violation:{event_id}")
        return self._result(
            "get_event_result",
            {**_pick(event, ("id", "event_key", "title", "subject", "status", "scheduled_at", "occurred_at", "result_expected_at", "result_available_at", "result", "sources", "market_reactions")), "sources": _bounded_sources(event.get("sources"))},
            self.current_phase,
            f"events.records[{event_id}]",
        )

    def quality(self, scope: str, symbol: str | None) -> dict[str, Any]:
        quality = self.packet.get("quality") or {}
        observations = {}
        for phase in (self.packet.get("observations") or {}):
            observation = self._observation(phase)
            observations[phase] = {
                "snapshot": observation.get("snapshot") or {},
                "market": (observation.get("market") or {}).get("quality_warnings", []),
                "options": (observation.get("options") or {}).get("warnings", []),
            }
        return self._result("get_data_quality", {"scope": scope, "symbol": symbol, "packet": quality, "observations": observations}, self.current_phase, "quality")


def _parse_event_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return as_utc(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _bounded_sources(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value[:3]
    if value is None:
        return []
    return [value]
