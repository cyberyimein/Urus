from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.buy_opportunities.breakout import (
    IntradayObservation,
    build_s1_setup,
    calculate_s1_levels,
    can_rebuild_s1_setup,
    evaluate_s1_trigger,
)
from app.buy_opportunities.contracts import EvidenceBar, FrozenEvidence, SetupRecord
from app.buy_opportunities.evidence import InMemoryDailyEvidenceReader, freeze_daily_evidence
from app.buy_opportunities.scanner import scan_universe
from app.decision_harness.strategies import (
    BreakoutVolumeStrategy,
    MeanReversionStrategy,
    StrategyContext,
    deterministic_synthesis,
)
from app.research.manifest import ResearchManifest, write_jsonl_atomic
from app.research.buy_execution import (
    ExecutionPolicy,
    PaperPortfolioPolicy,
    QuoteObservation,
    build_q1_entry_intent,
    execute_d1,
    execute_m1,
    execute_q1,
    evaluate_execution_eligibility,
    simulate_long_exit,
    user_ready_at,
)


KNOWLEDGE_AT = datetime(2026, 9, 5, 15, 0, tzinfo=UTC)


def _bars(symbol: str, *, count: int = 70) -> list[dict[str, object]]:
    start = date(2026, 1, 1)
    rows: list[dict[str, object]] = []
    for index in range(count):
        base = 100.0 if index < count - 20 else 108.0
        rows.append(
            {
                "symbol": symbol,
                "date": start + timedelta(days=index),
                "open": base,
                "high": base + 2.0,
                "low": base - 2.0,
                "close": base + 1.0,
                "volume": 2_000_000.0,
                "available_at": KNOWLEDGE_AT - timedelta(minutes=5),
                "collected_at": KNOWLEDGE_AT - timedelta(minutes=5),
                "source": "fixture",
                "source_revision": f"{symbol}-{index}",
                "adjustment": "QFQ",
            }
        )
    return rows


def _evidence(symbol: str, *, count: int = 70) -> FrozenEvidence:
    return freeze_daily_evidence(
        symbol=symbol,
        asset_type="equity",
        bars=_bars(symbol, count=count),
        knowledge_at=KNOWLEDGE_AT,
        source_mode="historical_reconstruction",
        price_basis="adjusted",
        trading_date=date(2026, 3, 11),
    )


def test_s1_levels_use_61_bar_boundary_and_frozen_formula() -> None:
    evidence = _evidence("AAA")

    levels = calculate_s1_levels(evidence)

    assert levels["upper_trigger"] == 110.0
    assert levels["atr"] == 4.0
    assert levels["invalidation_stop"] == 105.0
    assert levels["upper_limit"] == 112.0


def test_in_memory_reader_preserves_source_quality_and_warnings_when_refrozen() -> None:
    source = _evidence("AAA").model_copy(
        update={
            "quality_status": "partial",
            "warnings": ["source_quality_conflicted", "source_revision_unknown"],
        }
    )
    loaded = InMemoryDailyEvidenceReader({"AAA": source}).load(
        "AAA", knowledge_at=KNOWLEDGE_AT
    )

    assert loaded.quality_status == "partial"
    assert loaded.warnings[:2] == ["source_quality_conflicted", "source_revision_unknown"]


def test_scan_keeps_every_universe_symbol_and_explicit_rejection_reason() -> None:
    evidence = _evidence("AAA")
    reader = InMemoryDailyEvidenceReader({"AAA": evidence, "SHORT": _evidence("SHORT", count=30)})
    universe = [
        {
            "symbol": "AAA",
            "asset_type": "equity",
            "enabled": True,
            "collection": {"daily_history": True},
        },
        {
            "symbol": "SHORT",
            "asset_type": "equity",
            "enabled": True,
            "collection": {"daily_history": True},
        },
        {
            "symbol": "MISSING",
            "asset_type": "equity",
            "enabled": True,
            "collection": {"daily_history": True},
        },
        {
            "symbol": "DISABLED",
            "asset_type": "equity",
            "enabled": False,
            "collection": {"daily_history": True},
        },
        {
            "symbol": "SPY",
            "asset_type": "market",
            "enabled": True,
            "collection": {"daily_history": True},
        },
    ]

    scans, setups = scan_universe(
        universe_items=universe,
        evidence_reader=reader,
        pool_version="universe-v1",
        knowledge_at=KNOWLEDGE_AT,
    )

    by_symbol = {scan.symbol: scan for scan in scans}
    assert set(by_symbol) == {"AAA", "SHORT", "MISSING", "DISABLED", "SPY"}
    assert by_symbol["AAA"].status == "eligible"
    assert by_symbol["AAA"].setup_id == setups[0].setup_id
    assert by_symbol["SHORT"].status == "rejected"
    assert "insufficient_history" in by_symbol["SHORT"].rejection_codes
    assert by_symbol["MISSING"].status == "coverage_miss"
    assert by_symbol["MISSING"].rejection_codes == ["coverage_miss"]
    assert by_symbol["DISABLED"].rejection_codes == ["not_enabled"]
    assert by_symbol["SPY"].rejection_codes == ["asset_type_ineligible"]


def test_s1_trigger_distinguishes_cross_kind_extension_and_invalidation() -> None:
    setup = build_s1_setup(evidence=_evidence("AAA"), origin_scan_id="scan")
    end_at = datetime(2026, 3, 12, 14, 0, tzinfo=UTC)

    continuous = evaluate_s1_trigger(
        setup=setup,
        observation=IntradayObservation(
            bar_id="bar-1",
            start_at=end_at - timedelta(minutes=15),
            end_at=end_at,
            available_at=end_at + timedelta(seconds=30),
            open=109.0,
            high=111.0,
            low=108.0,
            close=110.5,
        ),
        previous_close=109.0,
        known_at=end_at + timedelta(seconds=30),
        session_number=1,
    )
    assert continuous.status == "triggered"
    assert continuous.trigger is not None
    assert continuous.trigger.kind == "continuous_cross"
    assert continuous.trigger.extension_status == "within_limit"

    missed = evaluate_s1_trigger(
        setup=setup,
        observation=IntradayObservation(
            bar_id="bar-2",
            start_at=end_at - timedelta(minutes=15),
            end_at=end_at,
            available_at=end_at + timedelta(seconds=30),
            open=111.0,
            high=114.0,
            low=110.0,
            close=113.0,
        ),
        previous_close=109.0,
        known_at=end_at + timedelta(seconds=30),
        session_number=1,
    )
    assert missed.trigger is not None
    assert missed.trigger.kind == "open_gap_cross"
    assert missed.trigger.extension_status == "missed_extension"

    invalidated = evaluate_s1_trigger(
        setup=setup,
        observation=IntradayObservation(
            bar_id="bar-3",
            start_at=end_at - timedelta(minutes=15),
            end_at=end_at,
            available_at=end_at + timedelta(seconds=30),
            open=109.0,
            high=111.0,
            low=setup.invalidation_stop,
            close=110.5,
        ),
        previous_close=109.0,
        known_at=end_at + timedelta(seconds=30),
        session_number=1,
    )
    assert invalidated.status == "invalidated"
    assert invalidated.trigger is None


def test_s1_trigger_handles_late_discovery_cross_unobserved_expiry_and_idempotency() -> None:
    setup = build_s1_setup(evidence=_evidence("AAA"), origin_scan_id="scan")
    end_at = datetime(2026, 3, 12, 14, 0, tzinfo=UTC)
    observation = IntradayObservation(
        bar_id="bar-late",
        start_at=end_at - timedelta(minutes=15),
        end_at=end_at,
        available_at=end_at + timedelta(seconds=30),
        open=110.5,
        high=111.0,
        low=110.0,
        close=110.5,
    )

    late = evaluate_s1_trigger(
        setup=setup,
        observation=observation,
        previous_close=None,
        known_at=observation.available_at,
        session_number=1,
    )
    unobserved = evaluate_s1_trigger(
        setup=setup,
        observation=observation,
        previous_close=109.0,
        known_at=observation.available_at,
        session_number=1,
        crossed_while_unobserved=True,
    )
    expired = evaluate_s1_trigger(
        setup=setup,
        observation=observation,
        previous_close=109.0,
        known_at=observation.available_at,
        session_number=setup.valid_sessions + 1,
    )
    repeated = evaluate_s1_trigger(
        setup=setup,
        observation=observation,
        previous_close=None,
        known_at=observation.available_at,
        session_number=1,
    )

    assert late.trigger is not None and late.trigger.kind == "late_discovery"
    assert unobserved.trigger is not None and unobserved.trigger.kind == "cross_unobserved"
    assert expired.status == "expired"
    assert late.model_dump(mode="json") == repeated.model_dump(mode="json")


def test_s1_setup_rebuild_waits_three_bars_and_requires_new_structure() -> None:
    original = _evidence("AAA")
    previous_setup = build_s1_setup(evidence=original, origin_scan_id="scan")
    rows = _bars("AAA") + [
        {
            "symbol": "AAA",
            "date": date(2026, 3, 12 + index),
            "open": 109.0,
            "high": 111.0,
            "low": 107.0,
            "close": 109.0,
            "volume": 2_000_000.0,
            "available_at": KNOWLEDGE_AT - timedelta(minutes=5),
            "collected_at": KNOWLEDGE_AT - timedelta(minutes=5),
            "source": "fixture",
            "source_revision": f"AAA-new-{index}",
            "adjustment": "QFQ",
        }
        for index in range(3)
    ]
    rebuilt_evidence = freeze_daily_evidence(
        symbol="AAA",
        asset_type="equity",
        bars=rows,
        knowledge_at=KNOWLEDGE_AT,
        source_mode="historical_reconstruction",
        price_basis="adjusted",
    )

    assert not can_rebuild_s1_setup(
        previous_setup=previous_setup,
        evidence=rebuilt_evidence,
        completed_bars_since_previous_setup=2,
    )
    assert can_rebuild_s1_setup(
        previous_setup=previous_setup,
        evidence=rebuilt_evidence,
        completed_bars_since_previous_setup=3,
    )


def test_q1_intent_freezes_q0_and_execution_never_chases_above_limit() -> None:
    setup = build_s1_setup(evidence=_evidence("AAA"), origin_scan_id="scan")
    end_at = datetime(2026, 3, 12, 14, 0, tzinfo=UTC)
    trigger = evaluate_s1_trigger(
        setup=setup,
        observation=IntradayObservation(
            bar_id="bar-q1",
            start_at=end_at - timedelta(minutes=15),
            end_at=end_at,
            available_at=end_at + timedelta(seconds=30),
            open=109.0,
            high=111.0,
            low=108.0,
            close=110.5,
        ),
        previous_close=109.0,
        known_at=end_at + timedelta(seconds=30),
        session_number=1,
    ).trigger
    assert trigger is not None
    policy = ExecutionPolicy(name="Q1", slippage_bps=5)
    delivered_at = end_at + timedelta(minutes=1)
    ready_at = delivered_at + timedelta(minutes=10)
    q0 = QuoteObservation(
        quote_id="q0",
        timestamp=ready_at,
        received_at=ready_at,
        bid=110.2,
        ask=110.5,
    )
    intent, quantity_plan = build_q1_entry_intent(
        trigger=trigger,
        setup=setup,
        created_at=end_at + timedelta(seconds=30),
        delivered_at=delivered_at,
        user_ready_at=ready_at,
        quotes=[q0],
        nav=100_000,
        cash=100_000,
        policy=policy,
        portfolio=PaperPortfolioPolicy(),
    )

    assert intent.status == "ready"
    assert intent.q0_ask == 110.5
    assert intent.planned_quantity == quantity_plan.quantity
    filled = execute_q1(
        intent=intent,
        setup=setup,
        q1_quotes=[
            QuoteObservation(
                quote_id="q1",
                timestamp=ready_at + timedelta(seconds=2),
                received_at=ready_at + timedelta(seconds=2),
                bid=110.3,
                ask=110.7,
            )
        ],
        sent_at=ready_at,
        policy=policy,
    )
    assert filled.status == "filled"
    assert filled.fill_price is not None
    assert filled.fill_price <= setup.upper_limit

    not_filled = execute_q1(
        intent=intent,
        setup=setup,
        q1_quotes=[
            QuoteObservation(
                quote_id="q1-high",
                timestamp=ready_at + timedelta(seconds=2),
                received_at=ready_at + timedelta(seconds=2),
                bid=112.0,
                ask=112.1,
            )
        ],
        sent_at=ready_at,
        policy=ExecutionPolicy(name="Q1-no-chase"),
    )
    assert not_filled.status == "not_filled"
    assert not_filled.reason_code == "q1_above_upper_limit"

    adverse = execute_q1(
        intent=intent,
        setup=setup,
        q1_quotes=[
            QuoteObservation(
                quote_id="q1-adverse",
                timestamp=ready_at + timedelta(seconds=2),
                received_at=ready_at + timedelta(seconds=2),
                bid=103.5,
                ask=104.0,
            )
        ],
        sent_at=ready_at,
        policy=ExecutionPolicy(name="Q1-adverse"),
    )
    assert adverse.status == "filled"
    assert adverse.reason_code == "breach_during_latency"


def test_q1_delivery_expiry_and_paper_capital_constraints_are_explicit() -> None:
    setup = build_s1_setup(evidence=_evidence("AAA"), origin_scan_id="scan")
    end_at = datetime(2026, 3, 12, 14, 0, tzinfo=UTC)
    trigger = evaluate_s1_trigger(
        setup=setup,
        observation=IntradayObservation(
            bar_id="bar-expiry",
            start_at=end_at - timedelta(minutes=15),
            end_at=end_at,
            available_at=end_at + timedelta(seconds=30),
            open=109.0,
            high=111.0,
            low=108.0,
            close=110.5,
        ),
        previous_close=109.0,
        known_at=end_at + timedelta(seconds=30),
        session_number=1,
    ).trigger
    assert trigger is not None
    delivered_at = end_at + timedelta(minutes=1)
    expired, plan = build_q1_entry_intent(
        trigger=trigger,
        setup=setup,
        created_at=end_at + timedelta(seconds=30),
        delivered_at=delivered_at,
        user_ready_at=delivered_at + timedelta(minutes=46),
        quotes=[],
        nav=100_000,
        cash=100_000,
    )
    assert expired.status == "expired"
    assert expired.reason_code == "response_after_expiry"
    assert plan.quantity == 0
    assert user_ready_at(delivered_at=delivered_at, response_minutes=30) == delivered_at + timedelta(
        minutes=30
    )
    assert user_ready_at(delivered_at=None, response_minutes=30) is None


def test_execution_eligibility_keeps_event_asset_and_reentry_reasons_separate() -> None:
    eligibility = evaluate_execution_eligibility(
        asset_type="equity",
        source_mode="historical_reconstruction",
        event_codes=["E0", "holding_period_event"],
        coverage_status="partial",
        has_existing_position=True,
        days_since_exit=1,
    )

    assert eligibility.status == "coverage_unknown"
    assert "event_E0_block" in eligibility.reasons
    assert "holding_period_event" in eligibility.reasons
    assert not eligibility.two_day_entry_block
    assert "two_day_entry_block" not in eligibility.reasons
    assert "existing_position" in eligibility.reasons


def test_m1_d1_execution_and_gap_stop_exit_are_deterministic() -> None:
    setup = build_s1_setup(evidence=_evidence("AAA"), origin_scan_id="scan")
    end_at = datetime(2026, 3, 12, 14, 0, tzinfo=UTC)
    trigger = evaluate_s1_trigger(
        setup=setup,
        observation=IntradayObservation(
            bar_id="bar-proxy",
            start_at=end_at - timedelta(minutes=15),
            end_at=end_at,
            available_at=end_at + timedelta(seconds=30),
            open=109.0,
            high=111.0,
            low=108.0,
            close=110.5,
        ),
        previous_close=109.0,
        known_at=end_at + timedelta(seconds=30),
        session_number=1,
    ).trigger
    assert trigger is not None
    ready_at = end_at + timedelta(minutes=1)
    intent, _ = build_q1_entry_intent(
        trigger=trigger,
        setup=setup,
        created_at=end_at + timedelta(seconds=30),
        delivered_at=end_at + timedelta(seconds=30),
        user_ready_at=ready_at,
        quotes=[
            QuoteObservation(
                quote_id="q0-proxy",
                timestamp=ready_at,
                received_at=ready_at,
                bid=110.1,
                ask=110.5,
            )
        ],
        nav=100_000,
        cash=100_000,
    )
    minute_execution = execute_m1(
        intent=intent,
        setup=setup,
        minute_bars=[
            IntradayObservation(
                bar_id="m1-first",
                start_at=ready_at,
                end_at=ready_at + timedelta(minutes=1),
                available_at=ready_at + timedelta(minutes=1),
                open=110.5,
                high=111.0,
                low=110.0,
                close=110.8,
            ),
            IntradayObservation(
                bar_id="m1-second",
                start_at=ready_at + timedelta(minutes=1),
                end_at=ready_at + timedelta(minutes=2),
                available_at=ready_at + timedelta(minutes=2),
                open=110.7,
                high=111.0,
                low=110.4,
                close=110.9,
            ),
        ],
        policy=ExecutionPolicy(name="M1"),
    )
    assert minute_execution.status == "filled"
    day_execution = execute_d1(
        intent=intent,
        setup=setup,
        next_session_open=110.8,
        fill_at=end_at + timedelta(days=1),
        policy=ExecutionPolicy(name="D1"),
    )
    assert day_execution.status == "filled"
    exit_record = simulate_long_exit(
        execution=day_execution,
        setup=setup,
        daily_bars=[
            EvidenceBar(
                date=date(2026, 3, 14),
                open=104.0,
                high=105.0,
                low=103.0,
                close=104.0,
                volume=1_000_000,
                available_at=KNOWLEDGE_AT,
                collected_at=KNOWLEDGE_AT,
                source="fixture",
                source_revision="exit-1",
            )
        ],
    )
    assert exit_record.status == "gap_stop"
    assert exit_record.exit_price == 104.0


def test_knowledge_cutoff_excludes_future_revision() -> None:
    rows = _bars("AAA", count=3)
    rows[-1]["available_at"] = KNOWLEDGE_AT + timedelta(minutes=1)
    rows[-1]["collected_at"] = KNOWLEDGE_AT + timedelta(minutes=1)

    evidence = freeze_daily_evidence(
        symbol="AAA",
        asset_type="equity",
        bars=rows,
        knowledge_at=KNOWLEDGE_AT,
        source_mode="observed_pit",
        price_basis="adjusted",
    )

    assert len(evidence.bars) == 2
    assert evidence.warnings == ["1 根日线在 knowledge_at 之后才可见，已排除。"]


def test_contract_rejects_naive_timestamp_and_invalid_setup_prices() -> None:
    with pytest.raises(ValidationError, match="时间戳必须包含时区"):
        EvidenceBar(
            date=date(2026, 1, 1),
            open=1,
            high=2,
            low=0.5,
            close=1.5,
            volume=1,
            available_at=datetime(2026, 1, 1),
            collected_at=datetime(2026, 1, 1),
            source="fixture",
            source_revision="r1",
        )

    with pytest.raises(ValidationError, match="invalidation_stop < upper_trigger"):
        SetupRecord(
            setup_id="setup",
            fingerprint="a" * 64,
            stream="s1_daily",
            symbol="AAA",
            family_version="s1_breakout_v1",
            parameter_hash="b" * 64,
            anchor_pivot_bar_id="2026-01-01",
            base_start_bar_id="2025-12-28",
            observed_at=date(2026, 1, 1),
            valid_from=date(2026, 1, 2),
            valid_sessions=5,
            upper_trigger=100,
            invalidation_stop=101,
            atr=2,
            upper_limit=101,
            origin_scan_id="scan",
            content_sha256="c" * 64,
        )


def test_research_manifest_and_jsonl_write_are_reproducible(tmp_path) -> None:
    manifest = ResearchManifest.build(
        run_id="run-1",
        created_at=KNOWLEDGE_AT,
        pool_version="universe-v1",
        knowledge_at=KNOWLEDGE_AT,
        source_mode="historical_reconstruction",
        parameters={"family": "s1_breakout_v1"},
        universe_symbols=["BBB", "AAA"],
        input_hashes={"AAA": "a" * 64},
    )
    target = tmp_path / "research" / "scan.jsonl"

    write_jsonl_atomic(target, manifest=manifest, records=[{"symbol": "AAA", "status": "eligible"}])

    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert '"type": "manifest"' in lines[0]
    assert '"symbol": "AAA"' in lines[1]


def _strategy_context(
    bars: list[dict[str, object]],
    indicators: dict[str, object],
) -> StrategyContext:
    return StrategyContext(
        dataset={
            "dataset_id": "fixture-dataset",
            "scope": {
                "scope_type": "instrument",
                "scope_id": "AAA",
                "symbols": ["AAA"],
                "benchmark_symbols": [],
            },
        },
        chart={},
        symbol="AAA",
        bars=bars,
        quality={"status": "ok"},
        indicators=indicators,
        benchmark=None,
        benchmark_bars=[],
        relative={},
    )


def test_breakout_volume_does_not_create_direction_from_volume_without_price_break() -> None:
    bars = [
        {
            "date": f"2026-01-{index + 1:02d}",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1_000_000.0,
        }
        for index in range(60)
    ]
    bars[-1]["volume"] = 3_000_000.0

    decision = BreakoutVolumeStrategy().evaluate(
        _strategy_context(
            bars,
            {
                "volume_effort_result": {
                    "volume_ratio_20d": 2.0,
                    "close_location_ratio": 0.5,
                    "range_atr_ratio": 1.0,
                },
                "atr14": {"value": 2.0},
            },
        )
    )

    assert decision["stance"] == "neutral"
    assert decision["action"] == "wait"
    assert "volume_expansion" not in {item["code"] for item in decision["reasons"]}


def test_breakout_failure_uses_threshold_before_previous_breakout_bar() -> None:
    bars = [
        {
            "date": f"2026-01-{index + 1:02d}",
            "open": 100.0,
            "high": 103.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1_000_000.0,
        }
        for index in range(60)
    ]
    bars[-2].update({"high": 105.0, "close": 104.0})
    bars[-1].update({"close": 102.5})

    decision = BreakoutVolumeStrategy().evaluate(
        _strategy_context(
            bars,
            {"volume_effort_result": {}, "atr14": {"value": 4.0}},
        )
    )

    assert "failed_bullish_breakout" in {item["code"] for item in decision["reasons"]}
    assert decision["setup_progress"]["stage"] == "invalidated"


def test_mean_reversion_high_score_without_band_reentry_stays_watch() -> None:
    bars = [
        {
            "date": f"2026-01-{index + 1:02d}",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1_000_000.0,
        }
        for index in range(21)
    ]
    decision = MeanReversionStrategy().evaluate(
        _strategy_context(
            bars,
            {
                "bollinger_20_2": {
                    "position_ratio": 0.0,
                    "lower": 99.0,
                    "middle": 100.0,
                    "upper": 101.0,
                },
                "rsi14": {"value": 25.0, "previous_value": 20.0},
                "moving_average": {"50d": 100.0},
                "volume_effort_result": {"signal": "volume_up_demand"},
                "atr14": {"value": 2.0},
            },
        )
    )

    assert decision["action"] == "watch"
    assert decision["setup_progress"]["stage"] == "armed"
    assert decision["setup_progress"]["stage_since"] is None
    assert decision["setup_plan"]["invalidation_price"] == 98.0


def test_two_watch_decisions_do_not_become_prioritize_consensus() -> None:
    decisions = [
        {
            "decision_id": "one",
            "status": "ok",
            "stance": "bullish",
            "action": "watch",
            "score": 30,
            "quality": {"status": "ok"},
            "strategy": {"name": "one", "version": "1", "implementation_sha256": "one"},
        },
        {
            "decision_id": "two",
            "status": "ok",
            "stance": "bullish",
            "action": "watch",
            "score": 31,
            "quality": {"status": "ok"},
            "strategy": {"name": "two", "version": "1", "implementation_sha256": "two"},
        },
    ]

    synthesis = deterministic_synthesis(
        {"dataset_id": "dataset", "scope": {"symbols": ["AAA"]}},
        decisions,
        "strategy-set",
    )

    assert synthesis["suggested_action"] == "watch"
