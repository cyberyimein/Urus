"""Pure projection of a frozen opportunity into the user-facing read view.

This module intentionally does not import the research execution or outcome
packages.  A projection is a statement about visible evidence at a cutoff;
it is not a simulated fill, a notification, or a prediction.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from math import isfinite
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.decision_harness.contracts import content_sha256, normalise_symbol

from .contracts import EvidenceBar, EvidenceReference, FrozenEvidence, SetupRecord, TriggerRecord
from .lifecycle import TradingSessionCalendar
from .view_contracts import (
    OpportunityView,
    ViewEvent,
    ViewEventRisk,
    ViewQuote,
    view_content_hash,
)


DEFAULT_QUOTE_MAX_AGE_SECONDS = 60
EVENT_COVERAGE_STATES = {"complete", "partial", "unknown"}


def _value(item: object, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _aware(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} 必须包含时区")
    return value


def _event_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def _normalise_quote(
    quote: ViewQuote | Mapping[str, Any] | object,
    *,
    symbol: str,
    knowledge_at: datetime,
    source_mode: str,
    data_source: str,
    price_basis: str,
    price_mapping_id: str | None,
) -> tuple[ViewQuote | None, list[str]]:
    """Adapt a quote-like value without importing legacy research models."""

    reasons: list[str] = []
    if isinstance(quote, ViewQuote):
        candidate = quote
    else:
        required_fields = (
            "quote_id",
            "symbol",
            "source_mode",
            "data_source",
            "price_basis",
            "timestamp",
            "received_at",
            "bid",
            "ask",
        )
        missing = [field for field in required_fields if _value(quote, field) is None]
        if missing:
            return None, ["quote_metadata_missing", *[f"quote_missing_{item}" for item in missing]]
        payload = {
            "quote_id": str(_value(quote, "quote_id")),
            "symbol": str(_value(quote, "symbol")),
            "source_mode": _value(quote, "source_mode"),
            "data_source": _value(quote, "data_source"),
            "price_basis": _value(quote, "price_basis"),
            "price_mapping_id": _value(quote, "price_mapping_id", price_mapping_id),
            "timestamp": _value(quote, "timestamp"),
            "received_at": _value(quote, "received_at"),
            "bid": _value(quote, "bid"),
            "ask": _value(quote, "ask"),
            "quality": _value(quote, "quality", "ok") or "ok",
            "source": _value(quote, "source"),
            "source_revision": _value(quote, "source_revision"),
        }
        try:
            candidate = ViewQuote(**payload)
        except (TypeError, ValueError) as exc:
            return None, ["quote_invalid", str(exc)]

    if candidate.symbol != symbol:
        reasons.append("quote_symbol_mismatch")
    if candidate.source_mode != source_mode:
        reasons.append("quote_source_mode_mismatch")
    if candidate.data_source != data_source:
        reasons.append("quote_data_source_mismatch")
    if candidate.price_basis != price_basis:
        reasons.append("quote_price_basis_mismatch")
    if candidate.price_mapping_id != price_mapping_id:
        reasons.append("quote_price_mapping_mismatch")
    if candidate.received_at > knowledge_at:
        reasons.append("quote_after_knowledge_cutoff")
    if candidate.timestamp > candidate.received_at:
        reasons.append("quote_timestamp_after_receipt")
    return (candidate if not reasons else None), reasons


def _bars_and_metadata(
    evidence: FrozenEvidence | Sequence[EvidenceBar] | None,
    *,
    knowledge_at: datetime,
    symbol: str,
    source_mode: str | None,
    data_source: str | None,
    price_basis: str | None,
    price_mapping_id: str | None,
) -> tuple[list[EvidenceBar], dict[str, Any], list[str]]:
    """Return visible bars and the metadata used to build the view."""

    reasons: list[str] = []
    if isinstance(evidence, FrozenEvidence):
        bars = list(evidence.bars)
        metadata = {
            "source_mode": evidence.source_mode,
            "data_source": evidence.data_source,
            "price_basis": evidence.price_basis,
            "price_mapping_id": evidence.price_mapping_id,
            "quality": evidence.quality_status
            if evidence.quality_status in {"ok", "partial", "unavailable"}
            else "unknown",
            "warnings": list(evidence.warnings),
            "refs": list(evidence.source_manifest),
            "benchmark": evidence.benchmark,
        }
        if source_mode is not None and source_mode != evidence.source_mode:
            reasons.append("evidence_source_mode_mismatch")
        if data_source is not None and data_source != evidence.data_source:
            reasons.append("evidence_data_source_mismatch")
        if price_basis is not None and price_basis != evidence.price_basis:
            reasons.append("evidence_price_basis_mismatch")
        if price_mapping_id is not None and price_mapping_id != evidence.price_mapping_id:
            reasons.append("evidence_price_mapping_mismatch")
        if evidence.knowledge_at > knowledge_at:
            reasons.append("evidence_knowledge_after_view_cutoff")
    else:
        bars = list(evidence or [])
        metadata = {
            "source_mode": source_mode or "historical_reconstruction",
            "data_source": data_source or "unknown",
            "price_basis": price_basis or "adjusted",
            "price_mapping_id": price_mapping_id,
            "quality": "unknown",
            "warnings": [],
            "refs": [],
            "benchmark": None,
        }
    if metadata["source_mode"] not in {
        "observed_pit",
        "historical_reconstruction",
        "daily_execution_proxy",
    }:
        reasons.append("source_mode_unknown")
    if metadata["data_source"] not in {"real", "synthetic", "unknown"}:
        reasons.append("data_source_unknown")
    if metadata["price_basis"] not in {"raw", "adjusted"}:
        reasons.append("price_basis_unknown")
    if source_mode is not None and metadata["source_mode"] != source_mode:
        reasons.append("evidence_source_mode_mismatch")
    if data_source is not None and metadata["data_source"] != data_source:
        reasons.append("evidence_data_source_mismatch")
    if price_basis is not None and metadata["price_basis"] != price_basis:
        reasons.append("evidence_price_basis_mismatch")
    if price_mapping_id is not None and metadata["price_mapping_id"] != price_mapping_id:
        reasons.append("evidence_price_mapping_mismatch")
    visible: list[EvidenceBar] = []
    for bar in bars:
        if bar.available_at > knowledge_at:
            reasons.append("future_bar_after_knowledge_cutoff")
            continue
        # A daily bar on the current knowledge date is not known to be complete
        # unless its session close was recorded.  This is deliberately
        # conservative: no weekday arithmetic is used as a fake calendar.
        if bar.date > knowledge_at.date():
            reasons.append("future_bar_date")
            continue
        if bar.date == knowledge_at.date() and bar.session_close_at is None:
            reasons.append("same_day_close_boundary_unknown")
            continue
        if bar.session_close_at is not None and bar.session_close_at > knowledge_at:
            reasons.append("incomplete_bar_at_knowledge_cutoff")
            continue
        visible.append(bar)
    visible.sort(key=lambda item: item.date)
    if len({item.date for item in visible}) != len(visible):
        reasons.append("duplicate_visible_bar_date")
    return visible, metadata, reasons


def _references(
    bars: Sequence[EvidenceBar],
    refs: Sequence[EvidenceReference],
    *,
    symbol: str,
) -> list[EvidenceReference]:
    by_date = {item.bar_date: item for item in refs if item.symbol == symbol}
    output: list[EvidenceReference] = []
    for bar in bars:
        existing = by_date.get(bar.date)
        if existing is not None and existing.content_sha256 == bar.content_sha256:
            output.append(existing)
            continue
        output.append(
            EvidenceReference(
                symbol=symbol,
                bar_date=bar.date,
                source=bar.source,
                source_revision=bar.source_revision,
                available_at=bar.available_at,
                content_sha256=bar.content_sha256,
                session_id=bar.session_id,
            )
        )
    return output


def _session_distance(
    *,
    calendar: TradingSessionCalendar | None,
    start: date,
    end: date,
) -> int | None:
    if calendar is None:
        return None
    if start not in calendar.session_dates:
        return None
    dates = [item for item in calendar.session_dates if item >= start]
    if end not in dates:
        return None
    return dates.index(end)


def _daily_freshness(
    *,
    latest: EvidenceBar,
    viewed_at: datetime,
    calendar: TradingSessionCalendar | None,
    market_timezone: str = "America/New_York",
) -> tuple[str, list[str]]:
    """Assess a daily close without assuming weekdays are trading sessions."""

    if calendar is None:
        return "unknown", ["calendar_required_for_daily_freshness"]
    try:
        local_view_date = viewed_at.astimezone(ZoneInfo(market_timezone)).date()
    except (ZoneInfoNotFoundError, ValueError):
        return "unknown", ["market_timezone_unknown"]
    if local_view_date > calendar.session_dates[-1]:
        return "unknown", ["calendar_does_not_cover_view_time"]
    if latest.date not in calendar.session_dates:
        return "unknown", ["latest_bar_not_in_trading_calendar"]
    if latest.session_close_at is not None and viewed_at < latest.session_close_at:
        return "stale", ["latest_session_not_closed_at_view_time"]
    sessions_through_view = [
        item for item in calendar.session_dates if item <= local_view_date
    ]
    if not sessions_through_view:
        return "unknown", ["view_before_calendar_coverage"]
    current_session = sessions_through_view[-1]
    if latest.date < current_session:
        return "stale", ["daily_close_older_than_current_session"]
    if latest.session_close_at is None:
        return "unknown", ["daily_session_completion_unknown"]
    return "fresh", []


def _event_risk(
    *,
    events: Sequence[ViewEvent | Mapping[str, Any] | object] | None,
    event_coverage: str,
    symbol: str,
    asset_type: str | None,
    knowledge_at: datetime,
    reference_date: date | None,
    calendar: TradingSessionCalendar | None,
) -> ViewEventRisk:
    if event_coverage not in EVENT_COVERAGE_STATES:
        raise ValueError(f"未知 event_coverage：{event_coverage}")
    if event_coverage == "unknown":
        return ViewEventRisk(status="unknown", codes=["event_coverage_unknown"])
    if reference_date is None:
        return ViewEventRisk(status="unknown", codes=["event_reference_date_unknown"])
    known: list[ViewEvent] = []
    invalid_count = 0
    future_count = 0
    for item in events or []:
        if isinstance(item, ViewEvent):
            event = item
        else:
            try:
                required = ("event_id", "event_at", "available_at", "source", "source_revision", "content_sha256")
                if any(_value(item, field) is None for field in required):
                    invalid_count += 1
                    continue
                payload = {
                    "event_id": str(_value(item, "event_id")),
                    "symbol": _value(item, "symbol", symbol),
                    "event_type": str(_value(item, "event_type", _value(item, "type", "event"))),
                    "event_at": _value(item, "event_at", _value(item, "scheduled_at")),
                    "status": _value(item, "status", "scheduled"),
                    "published_at": _value(item, "published_at"),
                    "available_at": _value(item, "available_at"),
                    "source": str(_value(item, "source")),
                    "source_revision": str(_value(item, "source_revision")),
                    "content_sha256": str(_value(item, "content_sha256")),
                }
                event = ViewEvent(**payload)
            except (TypeError, ValueError):
                invalid_count += 1
                continue
        if event.symbol not in {None, symbol}:
            continue
        if event.available_at is None or event.available_at > knowledge_at:
            future_count += 1
            continue
        if event.published_at is not None and event.published_at > knowledge_at:
            future_count += 1
            continue
        known.append(event)
    relevant: list[ViewEvent] = []
    ignored_company_earnings = 0
    for event in known:
        company_earnings = event.event_type.lower() in {"earnings", "company_earnings"}
        if asset_type == "etf" and company_earnings:
            ignored_company_earnings += 1
            continue
        relevant.append(event)
    partial_reason: list[str] = []
    if event_coverage == "partial":
        partial_reason.append("event_coverage_partial")
    if invalid_count:
        partial_reason.append("event_evidence_invalid")
    if future_count:
        partial_reason.append("event_evidence_after_knowledge_cutoff")
    if not relevant and ignored_company_earnings and not partial_reason:
        return ViewEventRisk(
            status="not_applicable",
            codes=["etf_company_earnings_not_applicable"],
            known_events=[item.event_id for item in known],
        )
    if (relevant or future_count) and calendar is None:
        return ViewEventRisk(
            status="unknown",
            codes=partial_reason + ["calendar_required_for_event_horizon"],
            known_events=[item.event_id for item in relevant],
            horizon_sessions=20,
        )
    near: list[ViewEvent] = []
    holding: list[ViewEvent] = []
    calendar_miss: list[ViewEvent] = []
    for event in relevant:
        distance = _session_distance(
            calendar=calendar,
            start=reference_date,
            end=_event_date(event.event_at),
        )
        if distance is None:
            calendar_miss.append(event)
            continue
        if distance is not None and 0 <= distance <= 20:
            holding.append(event)
        if distance is not None and 0 <= distance <= 2:
            near.append(event)
    if near:
        return ViewEventRisk(
            status="near_term",
            codes=partial_reason + ["event_within_two_sessions"],
            known_events=[item.event_id for item in holding],
            horizon_sessions=2,
        )
    if partial_reason:
        return ViewEventRisk(
            status="unknown",
            codes=partial_reason,
            known_events=[item.event_id for item in holding],
            horizon_sessions=20,
        )
    if calendar_miss:
        return ViewEventRisk(
            status="unknown",
            codes=["event_date_not_in_trading_calendar"],
            known_events=[item.event_id for item in calendar_miss],
            horizon_sessions=20,
        )
    if holding:
        return ViewEventRisk(
            status="holding_window",
            codes=["event_within_twenty_sessions"],
            known_events=[item.event_id for item in holding],
            horizon_sessions=20,
        )
    if ignored_company_earnings and asset_type == "etf":
        return ViewEventRisk(
            status="not_applicable",
            codes=["etf_company_earnings_not_applicable"],
            known_events=[item.event_id for item in known],
        )
    return ViewEventRisk(status="clear", codes=[], known_events=[])


def _status_for_view(
    *,
    setup: SetupRecord,
    trigger: TriggerRecord | None,
    reference_price: float | None,
    reference_kind: str,
    freshness: str,
    quality: str,
    invalidated: bool,
    expired: bool,
    event_risk: ViewEventRisk,
    has_calendar: bool,
) -> tuple[str, list[str], str, str]:
    reasons: list[str] = []
    condition = f"研究价格区间 [{setup.upper_trigger:g}, {setup.upper_limit:g}]"
    invalidation = f"价格路径触及 {setup.invalidation_stop:g} 后，该结构失效且不复活"
    if invalidated:
        return "invalidated", ["invalidation_touched"], condition, invalidation
    if freshness == "stale":
        reasons.append("reference_stale")
        return "stale", reasons, condition, invalidation
    if quality == "unavailable" or reference_kind == "none" and not trigger:
        reasons.append("insufficient_visible_evidence")
        return "unavailable", reasons, condition, invalidation
    if expired and not trigger:
        return "expired", ["setup_wait_window_expired"], condition, invalidation
    if not trigger:
        if not has_calendar:
            reasons.append("calendar_unknown_for_session_age")
        if reference_price is not None and reference_price > setup.upper_trigger:
            reasons.append("reference_above_trigger_without_recorded_trigger")
            return "triggered_unverified", reasons, condition, invalidation
        return "preparing", reasons, condition, invalidation

    if trigger.extension_status == "missed_extension" or (
        trigger.current_close is not None and trigger.current_close > setup.upper_limit
    ):
        reasons.append("trigger_first_observation_extended")
        if reference_price is not None and reference_price <= setup.upper_trigger:
            reasons.append("extended_then_returned_below_trigger")
            return "waiting", reasons, condition, invalidation
        return "extended", reasons, condition, invalidation
    if reference_price is None:
        return "triggered_unverified", ["trigger_visible_without_current_reference"], condition, invalidation
    if reference_price > setup.upper_limit:
        return "extended", ["reference_above_upper_limit"], condition, invalidation
    if reference_price < setup.upper_trigger:
        return "waiting", ["triggered_price_below_trigger"], condition, invalidation
    if trigger.kind in {"late_discovery", "cross_unobserved"}:
        reasons.append("trigger_path_not_observed")
    if quality != "ok":
        reasons.append("evidence_quality_not_complete")
    if event_risk.status == "near_term":
        reasons.append("near_term_event_restricts_participation")
    if event_risk.status == "holding_window":
        reasons.append("event_within_holding_window")
    if event_risk.status == "unknown":
        reasons.append("event_risk_unknown")
    if reference_kind != "intraday_quote":
        reasons.append("daily_reference_is_not_current_quote")
    if reasons:
        return "triggered_unverified", reasons, condition, invalidation
    return "in_entry_zone", ["current_quote_in_research_zone"], condition, invalidation


def project_opportunity_view(
    setup: SetupRecord,
    trigger: TriggerRecord | None = None,
    *,
    evidence: FrozenEvidence | Sequence[EvidenceBar] | None = None,
    daily_bars: Sequence[EvidenceBar] | None = None,
    viewed_at: datetime,
    knowledge_at: datetime,
    quote: ViewQuote | Mapping[str, Any] | object | None = None,
    events: Sequence[ViewEvent | Mapping[str, Any] | object] | None = None,
    event_coverage: Literal["complete", "partial", "unknown"] = "unknown",
    calendar: TradingSessionCalendar | None = None,
    max_quote_age_seconds: int = DEFAULT_QUOTE_MAX_AGE_SECONDS,
    rules_hash: str | None = None,
    dataset_id: str | None = None,
    scan_id: str | None = None,
    source_mode: Literal[
        "observed_pit", "historical_reconstruction", "daily_execution_proxy"
    ] | None = None,
    data_source: Literal["real", "synthetic", "unknown"] | None = None,
    price_basis: Literal["raw", "adjusted"] | None = None,
    price_mapping_id: str | None = None,
    market_timezone: str = "America/New_York",
    research_support: Literal["unknown", "research_only", "supported"] = "unknown",
) -> OpportunityView:
    """Project visible evidence into a stable, read-only opportunity view.

    ``knowledge_at`` is the evidence cutoff and ``viewed_at`` is the local
    read time.  The function never turns a daily close into a current quote;
    therefore a daily-only view cannot be ``in_entry_zone``.
    """

    viewed_at = _aware(viewed_at, name="viewed_at")
    knowledge_at = _aware(knowledge_at, name="knowledge_at")
    if knowledge_at > viewed_at:
        raise ValueError("knowledge_at 不能晚于 viewed_at")
    if max_quote_age_seconds < 0:
        raise ValueError("max_quote_age_seconds 不能为负")
    if daily_bars is not None:
        if evidence is not None:
            raise ValueError("evidence 与 daily_bars 只能提供一个")
        evidence = daily_bars
    scan_identifier = scan_id or setup.origin_scan_id
    if setup.symbol != normalise_symbol(setup.symbol):
        raise ValueError("setup symbol 非法")
    if trigger is not None and trigger.setup_id != setup.setup_id:
        raise ValueError("trigger 必须属于 setup")
    requested_basis = price_basis
    if not isinstance(evidence, FrozenEvidence):
        requested_basis = price_basis or setup.price_basis
    bars, metadata, evidence_reasons = _bars_and_metadata(
        evidence,
        knowledge_at=knowledge_at,
        symbol=setup.symbol,
        source_mode=source_mode,
        data_source=data_source,
        price_basis=requested_basis,
        price_mapping_id=price_mapping_id,
    )
    source_mode = str(metadata["source_mode"])
    data_source = str(metadata["data_source"])
    basis = str(metadata["price_basis"])
    mapping_id = metadata["price_mapping_id"]
    quality = str(metadata["quality"])
    warnings = list(metadata["warnings"])
    reasons = list(dict.fromkeys(evidence_reasons))
    if isinstance(evidence, FrozenEvidence):
        # Setup identity includes the price basis/mapping used to derive S/L/U.
        # A frozen bar package with different semantics cannot be used to
        # validate that setup, even when its numeric prices happen to fit.
        if setup.price_basis != basis:
            reasons.append("setup_price_basis_mismatch")
        if setup.price_mapping_id != mapping_id:
            reasons.append("setup_price_mapping_mismatch")
    if isinstance(evidence, FrozenEvidence) and evidence.symbol != setup.symbol:
        reasons.append("evidence_symbol_mismatch")
    setup_knowledge_at = getattr(setup, "knowledge_at", None)
    if setup_knowledge_at is not None:
        if setup_knowledge_at.tzinfo is None or setup_knowledge_at.utcoffset() is None:
            reasons.append("setup_knowledge_at_naive")
        elif setup_knowledge_at > knowledge_at:
            reasons.append("setup_knowledge_after_view_cutoff")
    if setup.observed_at > knowledge_at.date():
        reasons.append("setup_observed_after_view_cutoff")
    if scan_id is not None and scan_id != setup.origin_scan_id:
        reasons.append("scan_setup_mismatch")
    if trigger is not None:
        if trigger.symbol != setup.symbol:
            reasons.append("trigger_symbol_mismatch")
        if trigger.known_at > knowledge_at:
            reasons.append("trigger_known_after_view_cutoff")
        if trigger.occurred_at is not None:
            if trigger.occurred_at > knowledge_at:
                reasons.append("trigger_occurred_after_view_cutoff")
            if trigger.known_at < trigger.occurred_at:
                reasons.append("trigger_known_before_occurrence")
    if reasons:
        # Identity, provenance, and temporal violations are hard gates.  A
        # reason alone must never leave quality=ok and accidentally produce an
        # in_entry_zone conclusion.
        hard_reason_prefixes = (
            "evidence_",
            "quote_",
            "setup_",
            "trigger_",
            "scan_",
            "future_",
            "incomplete_",
            "same_day_",
            "duplicate_",
        )
        if any(item.startswith(hard_reason_prefixes) for item in reasons):
            quality = "unavailable"

    reference_price: float | None = None
    reference_kind: str = "none"
    freshness: str = "unknown"
    market_as_of: datetime | date | None = None
    quote_value: ViewQuote | None = None
    quote_invalidated = False
    if quote is not None and not reasons:
        quote_value, quote_reasons = _normalise_quote(
            quote,
            symbol=setup.symbol,
            knowledge_at=knowledge_at,
            source_mode=source_mode,
            data_source=data_source,
            price_basis=basis,
            price_mapping_id=mapping_id,
        )
        reasons.extend(quote_reasons)
        if quote_value is not None:
            timestamp_age = (viewed_at - quote_value.timestamp).total_seconds()
            receipt_lag = (quote_value.received_at - quote_value.timestamp).total_seconds()
            if timestamp_age < 0:
                reasons.append("quote_after_view_time")
            elif timestamp_age > max_quote_age_seconds:
                reasons.append("quote_timestamp_stale")
                freshness = "stale"
            elif receipt_lag > max_quote_age_seconds:
                reasons.append("quote_receipt_lag_stale")
                freshness = "stale"
            else:
                reference_price = round((quote_value.bid + quote_value.ask) / 2, 8)
                reference_kind = "intraday_quote"
                market_as_of = quote_value.timestamp
                freshness = "fresh"
                if quote_value.quality != "ok":
                    quality = "partial"
                if quote_value.ask <= setup.invalidation_stop:
                    quote_invalidated = True
                    reasons.append("quote_invalidation_touched")
                elif quote_value.bid <= setup.invalidation_stop:
                    # The spread straddles S.  It is evidence of an
                    # unresolved boundary, not permission to choose the
                    # midpoint and claim either invalidation or entry.
                    reasons.append("quote_straddles_invalidation")
    if quote is not None and quote_value is None:
        freshness = "stale" if any(
            item in reasons for item in {"quote_timestamp_stale", "quote_receipt_lag_stale"}
        ) else "unknown"

    hard_reason_prefixes = (
        "evidence_",
        "quote_",
        "setup_",
        "trigger_",
        "scan_",
        "future_",
        "incomplete_",
        "same_day_",
        "duplicate_",
    )
    if any(item.startswith(hard_reason_prefixes) for item in reasons):
        # Staleness is a meaningful view state, whereas identity/provenance
        # violations make the reference unusable.  The status resolver keeps
        # stale ahead of this quality gate.
        if not any(item in {"quote_timestamp_stale", "quote_receipt_lag_stale"} for item in reasons):
            quality = "unavailable"

    if quote is None and bars:
        last_bar = bars[-1]
        reference_price = float(last_bar.close)
        reference_kind = "daily_close"
        market_as_of = last_bar.session_close_at or last_bar.date
        # A complete visible daily close is usable as a historical reference,
        # but freshness is relative to the supplied exchange calendar and
        # viewed_at, never merely to the original evidence cutoff.
        freshness, freshness_reasons = _daily_freshness(
            latest=last_bar,
            viewed_at=viewed_at,
            calendar=calendar,
            market_timezone=market_timezone,
        )
        reasons.extend(freshness_reasons)
    elif quote is not None and quote_value is None:
        reference_kind = "none"
        reference_price = None

    if metadata["quality"] in {"unavailable", "failed"}:
        quality = "unavailable"
    if not bars and evidence is not None:
        quality = "unavailable"
        reasons.append("no_visible_daily_bars")

    invalidated = any(
        bar.date >= setup.valid_from and float(bar.low) <= setup.invalidation_stop
        for bar in bars
    )
    if invalidated:
        reasons.append("invalidation_touched")
    invalidated = invalidated or quote_invalidated
    latest_date = bars[-1].date if bars else None
    expired = False
    if trigger is None and latest_date is not None and calendar is not None:
        session_number = calendar.session_number(
            valid_from=setup.valid_from,
            observed_date=latest_date,
        )
        expired = session_number is not None and session_number > setup.valid_sessions
    elif trigger is None and latest_date is not None and calendar is None:
        reasons.append("calendar_required_to_prove_expiry")

    event_reference_date = (
        quote_value.timestamp.date()
        if quote_value is not None and reference_kind == "intraday_quote"
        else latest_date
    )
    event_risk = _event_risk(
        events=events,
        event_coverage=event_coverage,
        symbol=setup.symbol,
        asset_type=(evidence.asset_type if isinstance(evidence, FrozenEvidence) else None),
        knowledge_at=knowledge_at,
        reference_date=event_reference_date,
        calendar=calendar,
    )
    status, status_reasons, condition, invalidation = _status_for_view(
        setup=setup,
        trigger=trigger,
        reference_price=reference_price,
        reference_kind=reference_kind,
        freshness=freshness,
        quality=quality,
        invalidated=invalidated,
        expired=expired,
        event_risk=event_risk,
        has_calendar=calendar is not None,
    )
    reasons.extend(status_reasons)
    if trigger is not None and trigger.extension_status == "missed_extension":
        reasons.append("missed_extension")
    if event_risk.codes:
        reasons.extend(event_risk.codes)
    if source_mode == "historical_reconstruction":
        reasons.append("historical_reconstruction")
    if data_source == "synthetic":
        reasons.append("synthetic_data")
    # A daily close is deliberately not an entry-zone assertion.
    if status == "in_entry_zone" and reference_kind != "intraday_quote":
        status = "triggered_unverified"
        reasons.append("daily_close_cannot_prove_current_entry_zone")
    reasons = list(dict.fromkeys(reasons))
    distance = None
    if reference_price is not None:
        distance = round((setup.upper_trigger - reference_price) / setup.atr, 8)
    effective_rules_hash = rules_hash or content_sha256(
        {
            "family_version": setup.family_version,
            "parameter_hash": setup.parameter_hash,
            "view_schema": "urus.buy_opportunity.view.v1",
        }
    )
    refs = _references(bars, metadata["refs"], symbol=setup.symbol)
    payload: dict[str, Any] = {
        "schema_version": "urus.buy_opportunity.view.v1",
        "view_id": "pending",
        "dataset_id": dataset_id,
        "scan_id": scan_identifier,
        "setup_id": setup.setup_id,
        "trigger_id": trigger.trigger_id if trigger else None,
        "symbol": setup.symbol,
        "family_version": setup.family_version,
        "rules_hash": effective_rules_hash,
        "viewed_at": viewed_at,
        "knowledge_at": knowledge_at,
        "market_as_of": market_as_of,
        "source_mode": source_mode,
        "data_source": data_source,
        "price_basis": basis,
        "price_mapping_id": mapping_id,
        "quality": quality if quality in {"ok", "partial", "unavailable"} else "unknown",
        "quality_warnings": warnings + reasons,
        "freshness": freshness,
        "status": status,
        "upper_trigger": setup.upper_trigger,
        "invalidation_stop": setup.invalidation_stop,
        "atr": setup.atr,
        "upper_limit": setup.upper_limit,
        "reference_price": reference_price,
        "reference_kind": reference_kind,
        "reference_quote": quote_value,
        "distance_to_trigger_atr": distance,
        "condition": condition,
        "invalidation": invalidation,
        "event_risk": event_risk,
        "research_support": research_support,
        "reason_codes": reasons,
        "evidence_refs": refs,
    }
    digest = view_content_hash({key: value for key, value in payload.items() if key != "view_id"})
    payload["view_id"] = f"{setup.setup_id}:view:{digest[:20]}"
    payload["content_sha256"] = view_content_hash(payload)
    return OpportunityView(**payload)


def project_daily_view(
    setup: SetupRecord,
    trigger: TriggerRecord | None = None,
    *,
    evidence: FrozenEvidence | Sequence[EvidenceBar] | None = None,
    daily_bars: Sequence[EvidenceBar] | None = None,
    viewed_at: datetime,
    knowledge_at: datetime,
    **kwargs: Any,
) -> OpportunityView:
    """Explicit daily-only entry point; it cannot create ``in_entry_zone``."""

    kwargs.pop("quote", None)
    return project_opportunity_view(
        setup,
        trigger,
        evidence=evidence,
        daily_bars=daily_bars,
        viewed_at=viewed_at,
        knowledge_at=knowledge_at,
        quote=None,
        **kwargs,
    )


# Small aliases make the boundary easy to discover without creating a second
# implementation.  They are intentionally read-only names.
project_view = project_opportunity_view
build_opportunity_view = project_opportunity_view


__all__ = [
    "DEFAULT_QUOTE_MAX_AGE_SECONDS",
    "build_opportunity_view",
    "project_daily_view",
    "project_opportunity_view",
    "project_view",
]
