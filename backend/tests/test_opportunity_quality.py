from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path

import pytest

from app.buy_opportunities.contracts import EvidenceBar, FrozenEvidence
from app.buy_opportunities.evidence import InMemoryDailyEvidenceReader
from app.buy_opportunities.scanner import scan_universe
from app.research.opportunity_quality import (
    OpportunityQualityError,
    QualityConfig,
    _assessment,
    _complete_snapshot_hash,
    _label_one,
    _evidence_through,
    _item_symbol,
    run_opportunity_quality,
    synchronized_session_block_bootstrap,
)


def _bar(
    day: date,
    *,
    close: float = 100.0,
    high: float | None = None,
    low: float | None = None,
    index: int = 0,
    available_at: datetime | None = None,
) -> EvidenceBar:
    close_at = datetime.combine(day, datetime.min.time(), tzinfo=UTC).replace(hour=21)
    available = available_at or close_at + timedelta(minutes=5)
    return EvidenceBar(
        date=day,
        open=close,
        high=high if high is not None else close + 1,
        low=low if low is not None else close - 1,
        close=close,
        volume=1_000_000,
        turnover=25_000_000,
        adjustment="QFQ",
        available_at=available,
        collected_at=available,
        source="synthetic_fixture",
        source_revision=f"rev-{index}",
        session_open_at=close_at - timedelta(hours=6, minutes=30),
        session_close_at=close_at,
    )


def _evidence(days: list[date], *, closes: list[float] | None = None) -> FrozenEvidence:
    closes = closes or [100.0] * len(days)
    bars = [_bar(day, close=close, index=index) for index, (day, close) in enumerate(zip(days, closes))]
    return FrozenEvidence(
        symbol="AAA",
        asset_type="equity",
        source_mode="historical_reconstruction",
        data_source="synthetic",
        price_basis="adjusted",
        knowledge_at=bars[-1].available_at,
        bars=bars,
    )


def _snapshot(*, reference_date: date, reference_at: datetime, snapshot_id: str = "snap-1") -> dict:
    payload = {
        "snapshot_id": snapshot_id,
        "cohort": "lock",
        "economic_opportunity_id": "AAA:setup:one",
        "symbol": "AAA",
        "source_mode": "historical_reconstruction",
        "data_source": "synthetic",
        "reference_date": reference_date.isoformat(),
        "market_as_of": reference_date.isoformat(),
        "reference_at": reference_at.isoformat(),
        "viewed_at": reference_at.isoformat(),
        "reference_price": 100.0,
        "upper_trigger": 105.0,
        "invalidation_stop": 90.0,
        "upper_limit": 110.0,
    }
    return payload


def test_label_uses_real_session_endpoint_and_excludes_reference_bar_extremes() -> None:
    reference = date(2026, 1, 2)
    future = [reference + timedelta(days=index) for index in range(1, 4)]
    evidence = FrozenEvidence(
        symbol="AAA",
        asset_type="equity",
        source_mode="historical_reconstruction",
        data_source="synthetic",
        price_basis="adjusted",
        knowledge_at=datetime(2026, 1, 10, tzinfo=UTC),
        bars=[
            _bar(reference, close=100, high=999, low=1, index=0),
            _bar(future[0], close=105, high=106, low=95, index=1),
            _bar(future[1], close=107, high=108, low=96, index=2),
            _bar(future[2], close=108, high=109, low=97, index=3),
        ],
    )
    label = _label_one(
        snapshot=_snapshot(reference_date=reference, reference_at=datetime(2026, 1, 2, 21, 5, tzinfo=UTC)),
        evidence=evidence,
        benchmark=None,
        calendar=[reference, *future],
        benchmark_calendar=None,
        horizon=2,
        evaluation_as_of=datetime(2026, 1, 10, tzinfo=UTC),
    )

    assert label is not None
    assert label.status == "mature"
    assert label.target_date == future[1]
    assert label.target_price == 107
    assert label.reference_price_return == pytest.approx(0.07)
    assert label.mfe == pytest.approx(0.08)
    assert label.mae == pytest.approx(-0.05)
    assert label.benchmark_return is None
    assert label.benchmark_status == "unknown"


def test_missing_calendar_never_matures_from_observed_bar_dates() -> None:
    reference = date(2026, 1, 2)
    evidence = _evidence([reference, reference + timedelta(days=1), reference + timedelta(days=2)])
    label = _label_one(
        snapshot=_snapshot(reference_date=reference, reference_at=datetime(2026, 1, 2, 21, 5, tzinfo=UTC)),
        evidence=evidence,
        benchmark=None,
        calendar=[],
        benchmark_calendar=None,
        horizon=1,
        evaluation_as_of=datetime(2026, 1, 10, tzinfo=UTC),
    )

    assert label is not None
    assert label.status == "censored"
    assert label.target_date is None
    assert label.reference_price_return is None
    assert "calendar_unknown" in label.reason_codes


def test_partial_path_ambiguity_does_not_turn_pending_horizon_into_mature() -> None:
    reference = date(2026, 1, 2)
    day_one = reference + timedelta(days=1)
    day_two = reference + timedelta(days=2)
    evidence = FrozenEvidence(
        symbol="AAA",
        asset_type="equity",
        source_mode="historical_reconstruction",
        data_source="synthetic",
        price_basis="adjusted",
        knowledge_at=datetime(2026, 1, 5, tzinfo=UTC),
        bars=[
            _bar(reference, index=0),
            _bar(day_one, high=111, low=89, index=1),
            _bar(day_two, close=103, available_at=datetime(2026, 1, 4, 22, tzinfo=UTC), index=2),
        ],
    )
    label = _label_one(
        snapshot=_snapshot(reference_date=reference, reference_at=datetime(2026, 1, 2, 21, 5, tzinfo=UTC)),
        evidence=evidence,
        benchmark=None,
        calendar=[reference, day_one, day_two],
        benchmark_calendar=None,
        horizon=2,
        evaluation_as_of=datetime(2026, 1, 2, 21, 5, tzinfo=UTC),
    )

    assert label is not None
    assert label.status == "pending"
    assert label.reference_price_return is None
    assert label.mfe is None and label.mae is None


def test_assessment_locks_primary_to_lock_source_mode_and_horizon_and_dedupes() -> None:
    labels = [
        {
            "snapshot_id": "dev",
            "cohort": "development",
            "economic_opportunity_id": "AAA:dev",
            "source_mode": "historical_reconstruction",
            "data_source": "synthetic",
            "horizon_sessions": 1,
            "status": "mature",
            "reference_at": "2026-01-01T21:00:00+00:00",
            "reference_date": "2026-01-01",
            "reference_price_return": 0.90,
            "benchmark_excess_return": None,
            "benchmark_status": "unknown",
            "mfe": 0.90,
            "mae": -0.01,
            "interval_first_touch_date": None,
            "invalidated_date": None,
        },
        {
            "snapshot_id": "lock-earlier",
            "cohort": "lock",
            "economic_opportunity_id": "AAA:lock",
            "source_mode": "historical_reconstruction",
            "data_source": "synthetic",
            "horizon_sessions": 1,
            "status": "mature",
            "reference_at": "2026-02-01T21:00:00+00:00",
            "reference_date": "2026-02-01",
            "reference_price_return": 0.10,
            "benchmark_excess_return": None,
            "benchmark_status": "unknown",
            "mfe": 0.12,
            "mae": -0.02,
            "interval_first_touch_date": None,
            "invalidated_date": None,
        },
        {
            "snapshot_id": "lock-repeat",
            "cohort": "lock",
            "economic_opportunity_id": "AAA:lock",
            "source_mode": "historical_reconstruction",
            "data_source": "synthetic",
            "horizon_sessions": 1,
            "status": "mature",
            "reference_at": "2026-02-02T21:00:00+00:00",
            "reference_date": "2026-02-02",
            "reference_price_return": 0.20,
            "benchmark_excess_return": None,
            "benchmark_status": "unknown",
            "mfe": 0.20,
            "mae": -0.02,
            "interval_first_touch_date": None,
            "invalidated_date": None,
        },
    ]
    for item in labels:
        item["snapshot_status"] = "triggered_unverified"
        item["setup_present"] = True
    config = QualityConfig(
        run_id="run",
        pool_version="pool",
        created_at=datetime(2026, 3, 1, tzinfo=UTC),
        knowledge_at=datetime(2026, 3, 1, tzinfo=UTC),
        development_start=date(2026, 1, 1),
        development_end=date(2026, 1, 31),
        lock_start=date(2026, 2, 1),
        lock_end=date(2026, 2, 28),
        evaluation_as_of=datetime(2026, 3, 1, tzinfo=UTC),
        primary_source_mode="historical_reconstruction",
        primary_data_source="synthetic",
        primary_horizon=1,
    )
    assessment = _assessment(config=config, scans=[], snapshots=[labels[0], labels[1], labels[2]], labels=labels)

    primary = assessment["metrics"]["primary_lock_cohort"]
    assert primary["label_count"] == 2
    assert primary["independent_label_count"] == 1
    assert primary["horizons"]["1"]["reference_price_return"]["mean"] == pytest.approx(0.15)
    assert assessment["metrics"]["descriptive_all_views"]["horizons"]["1"]["reference_price_return"]["mean"] == pytest.approx(0.4)
    assert assessment["baseline"]["status"] == "unknown"
    assert assessment["research_support"] == "research_only"


def test_complete_snapshot_hash_changes_when_cohort_or_economic_identity_changes() -> None:
    original = _complete_snapshot_hash({"view_id": "v", "cohort": "lock", "economic_opportunity_id": "o"})
    changed = _complete_snapshot_hash({"view_id": "v", "cohort": "development", "economic_opportunity_id": "o"})
    changed_id = _complete_snapshot_hash({"view_id": "v", "cohort": "lock", "economic_opportunity_id": "other"})

    assert original["snapshot_id"] == "v"
    assert original["content_sha256"] != changed["content_sha256"]
    assert original["content_sha256"] != changed_id["content_sha256"]


def test_quality_config_rejects_overlapping_cohorts_and_transaction_inputs_are_not_accepted() -> None:
    with pytest.raises(OpportunityQualityError):
        QualityConfig(
            run_id="run",
            pool_version="pool",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            knowledge_at=datetime(2026, 1, 1, tzinfo=UTC),
            development_start=date(2026, 1, 1),
            development_end=date(2026, 2, 1),
            lock_start=date(2026, 2, 1),
            lock_end=date(2026, 3, 1),
        )


def test_raw_fixture_pipeline_preserves_full_pool_and_separates_view_labels() -> None:
    fixture_path = Path(__file__).parents[2] / "docs/examples/opportunity-quality-v3-synthetic.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    evidence_by_symbol = {
        symbol: FrozenEvidence(**value) for symbol, value in payload["evidence"].items()
    }
    view_times = {
        date.fromisoformat(value[:10]): datetime.fromisoformat(value)
        for value in payload["viewed_at"]
    }

    def replay_daily_views(**kwargs):
        rows = []
        scans = []
        setups = []
        for item in kwargs["universe_items"]:
            symbol = _item_symbol(item)
            evidence = evidence_by_symbol.get(symbol)
            for view_day in kwargs["view_days"]:
                viewed_at = kwargs["view_times"][view_day]
                if evidence is None:
                    reader = InMemoryDailyEvidenceReader({})
                    scan, setup = scan_universe(
                        universe_items=[item],
                        evidence_reader=reader,
                        pool_version=kwargs["pool_version"],
                        knowledge_at=viewed_at,
                    )
                    scan_value, setup_value = scan[0], None
                    view_evidence = None
                else:
                    prior = [bar for bar in evidence.bars if bar.date < view_day]
                    prior_evidence = _evidence_through(
                        evidence,
                        market_as_of=prior[-1].date if prior else view_day,
                        knowledge_at=viewed_at,
                    )
                    scan, setup = scan_universe(
                        universe_items=[item],
                        evidence_reader=InMemoryDailyEvidenceReader({symbol: prior_evidence}),
                        pool_version=kwargs["pool_version"],
                        knowledge_at=viewed_at,
                    )
                    scan_value, setup_value = scan[0], setup[0] if setup else None
                    view_evidence = _evidence_through(
                        evidence,
                        market_as_of=view_day,
                        knowledge_at=viewed_at,
                    )
                scans.append(scan_value)
                if setup_value is not None:
                    setups.append(setup_value)
                rows.append(
                    {
                        "symbol": symbol,
                        "viewed_at": viewed_at,
                        "knowledge_at": viewed_at,
                        "market_as_of": view_day,
                        "scan": scan_value,
                        "setup": setup_value,
                        "trigger": None,
                        "evidence": view_evidence,
                        "full_evidence": evidence,
                    }
                )
        return {"views": rows, "scans": scans, "setups": setups, "triggers": []}

    result = run_opportunity_quality(
        payload=payload,
        config=QualityConfig(
            run_id="fixture-run",
            pool_version="fixture-pool",
            created_at=datetime(2026, 5, 23, 22, 0, tzinfo=UTC),
            knowledge_at=datetime(2026, 5, 23, 22, 0, tzinfo=UTC),
            evaluation_as_of=datetime(2026, 5, 23, 22, 0, tzinfo=UTC),
            development_start=date(2026, 3, 30),
            development_end=date(2026, 4, 3),
            lock_start=date(2026, 4, 6),
            lock_end=date(2026, 4, 10),
            primary_source_mode="historical_reconstruction",
            primary_data_source="synthetic",
            primary_horizon=1,
            bootstrap_replicates=10,
        ),
        daily_replay=replay_daily_views,
    )

    assert len(result.snapshots) == 12
    assert {item["symbol"] for item in result.snapshots} == {"AAA", "SHORT", "MISSING", "DISABLED"}
    assert any(item["symbol"] == "MISSING" and item["status"] == "unavailable" for item in result.snapshots)
    assert any(item["symbol"] == "DISABLED" and item["status"] == "unavailable" for item in result.snapshots)
    assert len([item for item in result.labels if item["symbol"] == "AAA"]) == 15
    assert all("reference_price_return" in item for item in result.labels)
    assert "response_minutes" not in json.dumps(result.records(), ensure_ascii=False)
    assert result.assessment["research_support"] == "research_only"
    assert result.assessment["primary_cohort"]["status"] == "unknown"
    assert result.assessment["metrics"]["primary_lock_cohort"]["label_count"] == 0
    assert result.assessment["metrics"]["requested_view_scan_count"] == 12
    assert result.assessment["metrics"]["scan_count_all_history"] == 12


def test_real_shared_replay_can_make_the_triggered_april_view_primary() -> None:
    fixture_path = Path(__file__).parents[2] / "docs/examples/opportunity-quality-v3-synthetic.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    result = run_opportunity_quality(
        payload=payload,
        config=QualityConfig(
            run_id="fixture-lock-run",
            pool_version="fixture-pool",
            created_at=datetime(2026, 5, 23, 22, 0, tzinfo=UTC),
            knowledge_at=datetime(2026, 5, 23, 22, 0, tzinfo=UTC),
            evaluation_as_of=datetime(2026, 5, 23, 22, 0, tzinfo=UTC),
            development_start=date(2026, 3, 30),
            development_end=date(2026, 3, 31),
            lock_start=date(2026, 4, 1),
            lock_end=date(2026, 4, 1),
            primary_source_mode="historical_reconstruction",
            primary_data_source="synthetic",
            primary_horizon=1,
            bootstrap_replicates=10,
        ),
    )

    primary = result.assessment["metrics"]["primary_lock_cohort"]
    assert result.assessment["primary_cohort"]["status"] == "locked"
    assert primary["label_count"] == 1
    assert primary["horizons"]["1"]["label_count"] == 1
    assert result.assessment["uncertainty"]["bootstrap_40_session"]["reason"] == "insufficient_blocks"
    assert all(
        item["evaluation_scan_id"]
        for item in result.snapshots
    )


def test_cohort_uses_market_timezone_at_utc_day_boundary() -> None:
    config = QualityConfig(
        run_id="tz-run",
        pool_version="pool",
        created_at=datetime(2026, 4, 2, tzinfo=UTC),
        knowledge_at=datetime(2026, 4, 2, tzinfo=UTC),
        development_start=date(2026, 4, 1),
        development_end=date(2026, 4, 1),
        lock_start=date(2026, 4, 2),
        lock_end=date(2026, 4, 2),
    )

    assert config.cohort_for(datetime(2026, 4, 2, 0, 30, tzinfo=UTC)) == "development"
    assert config.cohort_for(datetime(2026, 4, 2, 4, 30, tzinfo=UTC)) == "lock"


def test_synchronized_bootstrap_is_deterministic_and_withholds_ci_for_short_40_blocks() -> None:
    sessions = [date(2026, 1, 1) + timedelta(days=index) for index in range(400)]
    rows = [
        {
            "snapshot_id": f"snap-{index}",
            "economic_opportunity_id": f"opp-{index}",
            "reference_at": f"2026-01-01T21:00:00+00:00",
            "reference_date": session.isoformat(),
            "horizon_sessions": 1,
            "status": "mature",
            "reference_price_return": (index % 7 - 3) / 100,
        }
        for index, session in enumerate(sessions)
    ]
    first = synchronized_session_block_bootstrap(
        rows,
        block_sessions=40,
        replicates=5000,
        seed=5000,
        trading_sessions=sessions,
    )
    second = synchronized_session_block_bootstrap(
        rows,
        block_sessions=40,
        replicates=5000,
        seed=5000,
        trading_sessions=sessions,
    )
    short = synchronized_session_block_bootstrap(
        rows[:200],
        block_sessions=40,
        replicates=5000,
        seed=5000,
        trading_sessions=sessions[:200],
    )

    assert first == second
    assert first["status"] == "observed"
    assert first["effective_block_count"] == 10
    assert first["sample_count"] == 5000
    assert "interval_low" in first and "interval_high" in first
    assert short["status"] == "unknown"
    assert short["reason"] == "insufficient_blocks"
    assert short["sample_count"] == 0
    assert "interval_low" not in short

    weighted_rows = [
        {
            "snapshot_id": f"weighted-{block}-{index}",
            "economic_opportunity_id": f"weighted-opp-{block}-{index}",
            "reference_date": sessions[block * 40 + (index % 40)].isoformat(),
            "horizon_sessions": 1,
            "status": "mature",
            "reference_price_return": (
                0.01 if block == 0 else 0.10 if block == 1 else 0.0
            ),
        }
        for block in range(10)
        for index in range(100 if block == 0 else 1)
    ]
    weighted = synchronized_session_block_bootstrap(
        weighted_rows,
        block_sessions=40,
        replicates=20,
        seed=5000,
        trading_sessions=sessions,
    )
    assert weighted["block_summaries"][0]["count"] == 100
    assert weighted["block_summaries"][1]["count"] == 1
    assert weighted["point_estimate"] == pytest.approx((100 * 0.01 + 0.10) / 109)


def test_benchmark_mapping_ids_may_differ_but_source_and_basis_must_match() -> None:
    reference = date(2026, 1, 2)
    target = date(2026, 1, 5)
    evidence = FrozenEvidence(
        symbol="AAA",
        asset_type="equity",
        source_mode="historical_reconstruction",
        data_source="synthetic",
        price_basis="adjusted",
        price_mapping_id="AAA-map",
        knowledge_at=datetime(2026, 1, 10, tzinfo=UTC),
        bars=[_bar(reference, index=0), _bar(target, close=105, index=1)],
    )
    benchmark = FrozenEvidence(
        symbol="SPY",
        asset_type="etf",
        source_mode="historical_reconstruction",
        data_source="synthetic",
        price_basis="adjusted",
        price_mapping_id="SPY-map",
        knowledge_at=datetime(2026, 1, 10, tzinfo=UTC),
        bars=[_bar(reference, index=0), _bar(target, close=102, index=1)],
    )
    label = _label_one(
        snapshot=_snapshot(reference_date=reference, reference_at=datetime(2026, 1, 2, 21, 5, tzinfo=UTC)),
        evidence=evidence,
        benchmark=benchmark,
        calendar=[reference, target],
        benchmark_calendar=[reference, target],
        horizon=1,
        evaluation_as_of=datetime(2026, 1, 10, tzinfo=UTC),
    )

    assert label is not None
    assert label.benchmark_status == "known"
    assert label.benchmark_excess_return == pytest.approx(0.05 - 0.02)


def test_missing_target_bar_after_known_session_close_is_censored() -> None:
    reference = date(2026, 1, 2)
    target = date(2026, 1, 5)
    target_close = datetime(2026, 1, 5, 21, tzinfo=UTC)
    evidence = FrozenEvidence(
        symbol="AAA",
        asset_type="equity",
        source_mode="historical_reconstruction",
        data_source="synthetic",
        price_basis="adjusted",
        knowledge_at=datetime(2026, 1, 10, tzinfo=UTC),
        bars=[
            _bar(reference, index=0),
            _bar(
                target,
                index=1,
                available_at=target_close + timedelta(days=1),
            ),
        ],
    )
    snapshot = _snapshot(
        reference_date=reference,
        reference_at=datetime(2026, 1, 2, 21, 5, tzinfo=UTC),
    )
    after_close = _label_one(
        snapshot=snapshot,
        evidence=evidence,
        benchmark=None,
        calendar=[reference, target],
        benchmark_calendar=None,
        horizon=1,
        evaluation_as_of=target_close + timedelta(hours=1),
    )
    before_close = _label_one(
        snapshot=snapshot,
        evidence=evidence,
        benchmark=None,
        calendar=[reference, target],
        benchmark_calendar=None,
        horizon=1,
        evaluation_as_of=target_close - timedelta(hours=1),
    )

    assert after_close is not None and after_close.status == "censored"
    assert "missing_future_session_bar" in after_close.reason_codes
    assert before_close is not None and before_close.status == "pending"
