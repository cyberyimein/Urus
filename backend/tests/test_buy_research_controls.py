from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from app.buy_opportunities.breakout import IntradayObservation, build_s1_setup
from app.buy_opportunities.evidence import freeze_daily_evidence
from app.buy_opportunities.lifecycle import (
    TradingSessionCalendar,
    advance_s1_lifecycle,
    start_s1_lifecycle,
)
from app.research.buy_evaluation import (
    EvaluationRow,
    independent_forward_labels,
    synchronized_block_bootstrap,
)
from app.research.buy_execution import (
    PaperPortfolioPolicy,
    PriceMapping,
    QuoteObservation,
    convert_price_quantity,
    convert_quote_observation,
)
from app.research.portfolio_ledger import LedgerEvent, replay_portfolio_ledger


UTC_NOW = datetime(2026, 1, 10, 15, tzinfo=UTC)


def _raw_bar(day: date, close: float, revision: str, available_at: datetime) -> dict:
    return {
        "date": day,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": 1_000_000,
        "source": "fixture",
        "source_revision": revision,
        "available_at": available_at,
        "collected_at": available_at,
    }


def test_freeze_selects_latest_visible_revision_and_keeps_source_mode() -> None:
    day = date(2026, 1, 2)
    evidence = freeze_daily_evidence(
        symbol="AAA",
        asset_type="equity",
        bars=[
            _raw_bar(day, 100, "r1", UTC_NOW - timedelta(hours=2)),
            _raw_bar(day, 101, "r2", UTC_NOW - timedelta(hours=1)),
        ],
        knowledge_at=UTC_NOW,
        source_mode="historical_reconstruction",
        price_basis="adjusted",
        data_source="synthetic",
    )

    assert [bar.source_revision for bar in evidence.bars] == ["r2"]
    assert evidence.data_source == "synthetic"


def test_frozen_price_mapping_converts_quote_and_share_units() -> None:
    mapping = PriceMapping(
        mapping_id="split-2-for-1",
        symbol="AAA",
        from_basis="raw",
        to_basis="adjusted",
        price_factor=0.5,
        quantity_factor=2.0,
        cash_dividend_per_share=0.25,
        source_revision="corp-action-r1",
    )
    quote = QuoteObservation(
        quote_id="raw-q1",
        symbol="AAA",
        price_basis="raw",
        price_mapping_id=mapping.mapping_id,
        timestamp=UTC_NOW,
        received_at=UTC_NOW,
        bid=100,
        ask=101,
    )
    converted = convert_quote_observation(
        quote,
        target_basis="adjusted",
        target_mapping_id=mapping.mapping_id,
        mapping=mapping,
    )
    price, quantity, dividend = convert_price_quantity(
        price=100,
        quantity=10,
        from_basis="raw",
        to_basis="adjusted",
        mapping=mapping,
    )
    assert (converted.bid, converted.ask, converted.price_basis) == (50, 50.5, "adjusted")
    assert (price, quantity, dividend) == (50, 20, 2.5)


def test_lifecycle_resolves_setup_and_replays_one_economic_trigger() -> None:
    bars = [
        _raw_bar(
            date(2026, 1, 1) + timedelta(days=index),
            100 if index < 50 else 108,
            str(index),
            datetime.combine(
                date(2026, 1, 1) + timedelta(days=index),
                time(21),
                tzinfo=UTC,
            ),
        )
        for index in range(70)
    ]
    evidence = freeze_daily_evidence(
        symbol="AAA",
        asset_type="equity",
        bars=bars,
        knowledge_at=datetime(2026, 3, 11, 21, tzinfo=UTC),
        source_mode="historical_reconstruction",
        price_basis="adjusted",
    )
    setup = build_s1_setup(evidence=evidence, origin_scan_id="scan")
    end_at = datetime(2026, 3, 12, 14, tzinfo=UTC)
    observation = IntradayObservation(
        bar_id="trigger-bar",
        start_at=end_at - timedelta(minutes=15),
        end_at=end_at,
        available_at=end_at + timedelta(seconds=30),
        open=109,
        high=111,
        low=108,
        close=110.5,
    )
    state, first = advance_s1_lifecycle(
        state=start_s1_lifecycle(setup),
        observation=observation,
        previous_close=109,
        known_at=observation.available_at,
        calendar=TradingSessionCalendar(session_dates=[end_at.date()]),
    )
    replayed, second = advance_s1_lifecycle(
        state=state,
        observation=observation,
        previous_close=109,
        known_at=observation.available_at + timedelta(seconds=1),
        calendar=TradingSessionCalendar(session_dates=[end_at.date()]),
    )

    assert first.status == "triggered"
    assert state.setup.status == "resolved"
    assert second.status == "no_trigger"
    assert replayed.setup.setup_id == state.setup.setup_id


def test_shared_ledger_rejects_competing_capital_and_releases_after_exit() -> None:
    start = datetime(2026, 1, 2, 14, tzinfo=UTC)
    policy = PaperPortfolioPolicy(
        max_symbol_notional_fraction=1.0,
        max_total_notional_fraction=1.0,
        max_total_risk_fraction=1.0,
        max_concurrent_symbols=1,
    )
    ledger = replay_portfolio_ledger(
        initial_cash=2_000,
        policy=policy,
        events=[
            LedgerEvent(
                event_id="a-entry",
                opportunity_id="a",
                symbol="AAA",
                event_at=start,
                side="entry",
                status="filled",
                quantity=10,
                price=100,
                planned_risk=20,
            ),
            LedgerEvent(
                event_id="b-entry",
                opportunity_id="b",
                symbol="BBB",
                event_at=start,
                side="entry",
                status="filled",
                quantity=10,
                price=100,
                planned_risk=20,
                display_order=1,
            ),
            LedgerEvent(
                event_id="a-exit",
                opportunity_id="a",
                symbol="AAA",
                event_at=start + timedelta(hours=2),
                side="exit",
                status="filled",
                quantity=10,
                price=110,
            ),
        ],
    )

    assert ledger.rejected_event_ids == ["b-entry"]
    assert ledger.realized_pnl == 100
    assert not ledger.positions


def test_forward_labels_and_bootstrap_keep_session_gaps_and_seed_stable() -> None:
    bars = [
        # The missing 2026-01-03 session must not be replaced by a bar-count shortcut.
        _raw_bar(date(2026, 1, 2), 100, "r1", UTC_NOW),
        _raw_bar(date(2026, 1, 5), 110, "r2", UTC_NOW),
    ]
    evidence = freeze_daily_evidence(
        symbol="AAA",
        asset_type="equity",
        bars=bars,
        knowledge_at=UTC_NOW,
        source_mode="historical_reconstruction",
        price_basis="adjusted",
    )
    labels = independent_forward_labels(
        opportunity_id="opp",
        symbol="AAA",
        entry_date=date(2026, 1, 2),
        entry_price=100,
        bars=evidence.bars,
        horizons=[1],
        session_dates=[date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 5)],
    )
    rows = [
        EvaluationRow(
            opportunity_id=f"opp-{index}",
            symbol="AAA",
            data_source="real",
            entry_date=date(2026, 1, 1) + timedelta(days=index),
            exit_date=date(2026, 1, 1) + timedelta(days=index),
            entry_price=100,
            exit_price=101,
            quantity=1,
            net_pnl=1,
            gross_pnl=1,
            status="stop",
        )
        for index in range(40)
    ]
    first = synchronized_block_bootstrap(
        rows,
        block_days=20,
        samples=100,
        seed=7,
        trading_dates=[date(2026, 1, 1) + timedelta(days=index) for index in range(40)],
    )
    second = synchronized_block_bootstrap(
        rows,
        block_days=20,
        samples=100,
        seed=7,
        trading_dates=[date(2026, 1, 1) + timedelta(days=index) for index in range(40)],
    )

    assert labels[0].status == "censored"
    assert labels[0].mfe_return is None
    assert first == second
