from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from app.buy_opportunities.breakout import (
    IntradayObservation,
    build_s1_setup,
    evaluate_s1_trigger,
)
from app.buy_opportunities.contracts import EvidenceBar, FrozenEvidence
from app.buy_opportunities.evidence import freeze_daily_evidence
from app.buy_opportunities.lifecycle import TradingSessionCalendar
from app.buy_opportunities.view_contracts import ViewEvent, ViewQuote
from app.buy_opportunities.view_projection import project_opportunity_view


UTC_KNOWLEDGE = datetime(2026, 3, 11, 21, tzinfo=UTC)


def _rows(*, start: date = date(2026, 1, 1), count: int = 70, symbol: str = "AAA") -> list[dict]:
    rows: list[dict] = []
    for index in range(count):
        day = start + timedelta(days=index)
        close = 100.0 if index < count - 20 else 108.0
        close_at = datetime.combine(day, time(21), tzinfo=UTC)
        rows.append(
            {
                "date": day,
                "open": close,
                "high": close + 2,
                "low": close - 2,
                "close": close + 1,
                "volume": 2_000_000,
                "source": "view-fixture",
                "source_revision": f"{symbol}-{index}",
                "available_at": close_at,
                "collected_at": close_at,
                "session_open_at": close_at - timedelta(hours=6, minutes=30),
                "session_close_at": close_at,
                "adjustment": "QFQ",
            }
        )
    return rows


def _evidence(
    *,
    rows: list[dict] | None = None,
    asset_type: str = "equity",
    symbol: str = "AAA",
    knowledge_at: datetime = UTC_KNOWLEDGE,
) -> FrozenEvidence:
    return freeze_daily_evidence(
        symbol=symbol,
        asset_type=asset_type,
        bars=rows or _rows(symbol=symbol),
        knowledge_at=knowledge_at,
        source_mode="historical_reconstruction",
        data_source="real",
        price_basis="adjusted",
    )


def _trigger(setup, *, end_at: datetime = datetime(2026, 3, 12, 14, tzinfo=UTC)):
    result = evaluate_s1_trigger(
        setup=setup,
        observation=IntradayObservation(
            bar_id="view-trigger-bar",
            start_at=end_at - timedelta(minutes=15),
            end_at=end_at,
            available_at=end_at + timedelta(seconds=30),
            open=109,
            high=111,
            low=108,
            close=110.5,
        ),
        previous_close=109,
        known_at=end_at + timedelta(seconds=30),
        session_number=1,
    )
    assert result.trigger is not None
    return result.trigger


def _quote(
    *,
    timestamp: datetime = datetime(2026, 3, 12, 14, 2, tzinfo=UTC),
    symbol: str = "AAA",
    source_mode: str = "historical_reconstruction",
    data_source: str = "real",
    price_basis: str = "adjusted",
    bid: float = 110,
    ask: float = 110.5,
) -> ViewQuote:
    return ViewQuote(
        quote_id="view-quote",
        symbol=symbol,
        source_mode=source_mode,
        data_source=data_source,
        price_basis=price_basis,
        timestamp=timestamp,
        received_at=timestamp,
        bid=bid,
        ask=ask,
    )


def _calendar(*, start: date = date(2026, 3, 11), count: int = 30) -> TradingSessionCalendar:
    return TradingSessionCalendar(
        session_dates=[start + timedelta(days=index) for index in range(count)]
    )


def test_daily_only_view_never_claims_current_entry_zone() -> None:
    evidence = _evidence()
    setup = build_s1_setup(evidence=evidence, origin_scan_id="scan-1")
    view = project_opportunity_view(
        setup,
        _trigger(setup),
        evidence=evidence,
        viewed_at=datetime(2026, 3, 12, 15, tzinfo=UTC),
        knowledge_at=datetime(2026, 3, 12, 14, 1, tzinfo=UTC),
        calendar=_calendar(),
        event_coverage="complete",
    )

    assert view.reference_kind == "daily_close"
    assert view.status != "in_entry_zone"
    assert "daily_reference_is_not_current_quote" in view.reason_codes or view.status == "stale"


def test_old_dataset_is_stale_against_explicit_calendar() -> None:
    evidence = _evidence()
    setup = build_s1_setup(evidence=evidence, origin_scan_id="scan-1")
    view_date = date(2026, 9, 5)
    calendar = TradingSessionCalendar(
        session_dates=[date(2026, 3, 11) + timedelta(days=index) for index in range(180)]
    )
    view = project_opportunity_view(
        setup,
        evidence=evidence,
        viewed_at=datetime.combine(view_date, time(21), tzinfo=UTC),
        knowledge_at=UTC_KNOWLEDGE,
        calendar=calendar,
        event_coverage="complete",
    )

    assert view.status == "stale"
    assert "daily_close_older_than_current_session" in view.reason_codes


def test_daily_freshness_uses_market_local_date_at_utc_midnight() -> None:
    evidence = _evidence()
    setup = build_s1_setup(evidence=evidence, origin_scan_id="scan-1")
    # 00:30 UTC is still the prior NYSE session date (20:30 ET) in March.
    # A UTC-date comparison would incorrectly call the Mar-11 close stale.
    view = project_opportunity_view(
        setup,
        evidence=evidence,
        viewed_at=datetime(2026, 3, 12, 0, 30, tzinfo=UTC),
        knowledge_at=datetime(2026, 3, 11, 21, 1, tzinfo=UTC),
        calendar=_calendar(start=date(2026, 3, 11), count=1),
        event_coverage="complete",
    )

    assert view.freshness == "fresh"


def test_setup_price_semantics_mismatch_is_hard_unavailable_gate() -> None:
    evidence = _evidence().model_copy(
        update={"price_basis": "raw", "price_mapping_id": "raw-map"}
    )
    setup = build_s1_setup(evidence=_evidence(), origin_scan_id="scan-1")
    view = project_opportunity_view(
        setup,
        evidence=evidence,
        viewed_at=datetime(2026, 3, 11, 22, tzinfo=UTC),
        knowledge_at=datetime(2026, 3, 11, 21, 1, tzinfo=UTC),
        calendar=_calendar(start=date(2026, 3, 11), count=1),
        event_coverage="complete",
    )

    assert view.quality == "unavailable"
    assert view.status == "unavailable"
    assert "setup_price_basis_mismatch" in view.reason_codes
    assert "setup_price_mapping_mismatch" in view.reason_codes


def test_quote_requires_both_timestamp_freshness_and_receipt_lag() -> None:
    evidence = _evidence()
    setup = build_s1_setup(evidence=evidence, origin_scan_id="scan-1")
    trigger = _trigger(setup)
    old_timestamp = datetime(2026, 3, 12, 13, 0, tzinfo=UTC)
    quote = _quote(timestamp=old_timestamp).model_copy(
        update={"received_at": datetime(2026, 3, 12, 14, 2, tzinfo=UTC)}
    )
    view = project_opportunity_view(
        setup,
        trigger,
        evidence=evidence,
        viewed_at=datetime(2026, 3, 12, 14, 2, 1, tzinfo=UTC),
        knowledge_at=datetime(2026, 3, 12, 14, 2, tzinfo=UTC),
        quote=quote,
        calendar=_calendar(),
        event_coverage="complete",
    )

    assert view.status == "stale"
    assert view.reference_quote is not None
    assert "quote_timestamp_stale" in view.reason_codes


def test_quote_identity_mismatch_is_unavailable_and_cannot_be_in_entry_zone() -> None:
    evidence = _evidence()
    setup = build_s1_setup(evidence=evidence, origin_scan_id="scan-1")
    view = project_opportunity_view(
        setup,
        _trigger(setup),
        evidence=evidence,
        viewed_at=datetime(2026, 3, 12, 14, 3, tzinfo=UTC),
        knowledge_at=datetime(2026, 3, 12, 14, 2, tzinfo=UTC),
        quote=_quote(data_source="synthetic"),
        calendar=_calendar(),
        event_coverage="complete",
    )

    assert view.status == "unavailable"
    assert "quote_data_source_mismatch" in view.reason_codes


def test_quote_at_or_below_stop_is_invalidated_and_spread_crossing_is_unknown() -> None:
    evidence = _evidence()
    setup = build_s1_setup(evidence=evidence, origin_scan_id="scan-1")
    kwargs = {
        "evidence": evidence,
        "viewed_at": datetime(2026, 3, 12, 14, 3, tzinfo=UTC),
        "knowledge_at": datetime(2026, 3, 12, 14, 2, tzinfo=UTC),
        "calendar": _calendar(),
        "event_coverage": "complete",
    }
    below = project_opportunity_view(
        setup,
        _trigger(setup),
        quote=_quote(bid=104, ask=104.5),
        **kwargs,
    )
    straddling = project_opportunity_view(
        setup,
        _trigger(setup),
        quote=_quote(bid=104.5, ask=105.5),
        **kwargs,
    )

    assert below.status == "invalidated"
    assert "quote_invalidation_touched" in below.reason_codes
    assert straddling.status == "unavailable"
    assert "quote_straddles_invalidation" in straddling.reason_codes


def test_invalidation_has_priority_and_does_not_revive_after_a_bounce() -> None:
    baseline = _rows()
    setup_evidence = _evidence(rows=baseline)
    setup = build_s1_setup(evidence=setup_evidence, origin_scan_id="scan-1")
    invalidation_day = date(2026, 3, 12)
    close_at = datetime.combine(invalidation_day, time(21), tzinfo=UTC)
    path = baseline + [
        {
            "date": invalidation_day,
            "open": 108,
            "high": 111,
            "low": 104,
            "close": 110,
            "volume": 2_000_000,
            "source": "view-fixture",
            "source_revision": "invalidation",
            "available_at": close_at,
            "collected_at": close_at,
            "session_close_at": close_at,
            "adjustment": "QFQ",
        }
    ]
    evidence = _evidence(rows=path, knowledge_at=datetime(2026, 3, 12, 21, tzinfo=UTC))
    view = project_opportunity_view(
        setup,
        _trigger(setup),
        evidence=evidence,
        viewed_at=datetime(2026, 3, 12, 21, 1, tzinfo=UTC),
        knowledge_at=datetime(2026, 3, 12, 21, tzinfo=UTC),
        quote=_quote(timestamp=datetime(2026, 3, 12, 21, tzinfo=UTC)),
        calendar=_calendar(),
        event_coverage="complete",
    )

    assert view.status == "invalidated"


def test_extended_trigger_returning_to_zone_is_waiting() -> None:
    evidence = _evidence()
    setup = build_s1_setup(evidence=evidence, origin_scan_id="scan-1")
    trigger = _trigger(setup).model_copy(update={"extension_status": "missed_extension"})
    view = project_opportunity_view(
        setup,
        trigger,
        evidence=evidence,
        viewed_at=datetime(2026, 3, 12, 14, 3, tzinfo=UTC),
        knowledge_at=datetime(2026, 3, 12, 14, 2, tzinfo=UTC),
        quote=_quote(bid=109, ask=109.5),
        calendar=_calendar(),
        event_coverage="complete",
    )

    assert view.status == "waiting"
    assert "trigger_first_observation_extended" in view.reason_codes


def test_event_horizons_are_known_and_calendar_is_required() -> None:
    evidence = _evidence()
    setup = build_s1_setup(evidence=evidence, origin_scan_id="scan-1")
    trigger = _trigger(setup)
    event = ViewEvent(
        event_id="earnings-1",
        symbol="AAA",
        event_type="earnings",
        event_at=date(2026, 3, 20),
        status="scheduled",
        available_at=datetime(2026, 3, 12, 13, tzinfo=UTC),
        source="calendar",
        source_revision="v1",
        content_sha256="e" * 64,
    )
    holding = project_opportunity_view(
        setup,
        trigger,
        evidence=evidence,
        viewed_at=datetime(2026, 3, 12, 14, 3, tzinfo=UTC),
        knowledge_at=datetime(2026, 3, 12, 14, 2, tzinfo=UTC),
        quote=_quote(),
        events=[event],
        event_coverage="complete",
        calendar=_calendar(),
    )
    unknown_calendar = project_opportunity_view(
        setup,
        trigger,
        evidence=evidence,
        viewed_at=datetime(2026, 3, 12, 14, 3, tzinfo=UTC),
        knowledge_at=datetime(2026, 3, 12, 14, 2, tzinfo=UTC),
        quote=_quote(),
        events=[event],
        event_coverage="complete",
    )

    assert holding.event_risk.status == "holding_window"
    assert holding.event_risk.known_events == ["earnings-1"]
    assert unknown_calendar.event_risk.status == "unknown"
    assert "calendar_required_for_event_horizon" in unknown_calendar.event_risk.codes


def test_etf_company_earnings_are_not_applicable_but_quote_can_be_in_zone() -> None:
    evidence = _evidence(asset_type="etf", symbol="ETF")
    setup = build_s1_setup(evidence=evidence, origin_scan_id="scan-etf")
    trigger = _trigger(setup)
    event = ViewEvent(
        event_id="company-earnings",
        symbol="ETF",
        event_type="earnings",
        event_at=date(2026, 3, 20),
        available_at=datetime(2026, 3, 12, 13, tzinfo=UTC),
        source="calendar",
        source_revision="v1",
        content_sha256="f" * 64,
    )
    view = project_opportunity_view(
        setup,
        trigger,
        evidence=evidence,
        viewed_at=datetime(2026, 3, 12, 14, 3, tzinfo=UTC),
        knowledge_at=datetime(2026, 3, 12, 14, 2, tzinfo=UTC),
        quote=_quote(symbol="ETF"),
        events=[event],
        event_coverage="complete",
        calendar=_calendar(),
    )

    assert view.event_risk.status == "not_applicable"
    assert view.status == "in_entry_zone"
