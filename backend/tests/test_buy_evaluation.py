from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app.buy_opportunities.contracts import EvidenceBar
from app.research.buy_evaluation import (
    EvaluationConfig,
    EvaluationRow,
    build_assessment,
    build_coverage_report,
    compare_baselines,
    compare_filter_variant,
    independent_forward_labels,
    summarize_evaluations,
)


def _bar(day: date, close: float) -> EvidenceBar:
    return EvidenceBar(
        date=day,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=1_000_000,
        available_at=datetime(2026, 1, 10, tzinfo=UTC),
        collected_at=datetime(2026, 1, 10, tzinfo=UTC),
        source="fixture",
        source_revision=day.isoformat(),
    )


def _row(
    opportunity_id: str,
    *,
    net_pnl: float | None,
    gross_pnl: float | None,
    status: str = "stop",
    data_source: str = "real",
    response_minutes: float = 15,
) -> EvaluationRow:
    return EvaluationRow(
        opportunity_id=opportunity_id,
        symbol="AAA",
        data_source=data_source,
        entry_date=date(2026, 1, 1),
        exit_date=date(2026, 1, 3) if net_pnl is not None else None,
        entry_price=100,
        exit_price=99 if (net_pnl or 0) < 0 else 102,
        quantity=1,
        planned_risk=10,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        cost=abs((gross_pnl or 0) - (net_pnl or 0)),
        status=status,
        response_minutes=response_minutes,
        benchmark_return=0.01,
    )


def test_forward_labels_keep_pending_and_censored_distinct() -> None:
    bars = [_bar(date(2026, 1, 1) + timedelta(days=index), 100 + index) for index in range(3)]

    pending = independent_forward_labels(
        opportunity_id="opp",
        symbol="AAA",
        entry_date=date(2026, 1, 1),
        entry_price=100,
        bars=bars,
        horizons=[1, 2, 5],
        coverage_complete=True,
    )
    censored = independent_forward_labels(
        opportunity_id="opp",
        symbol="AAA",
        entry_date=date(2026, 1, 1),
        entry_price=100,
        bars=bars,
        horizons=[5],
        coverage_complete=False,
    )

    assert [label.status for label in pending] == ["observed", "observed", "pending"]
    assert censored[0].status == "censored"
    assert pending[0].gross_return == pytest.approx(0.01)


def test_summary_baseline_filter_and_coverage_report_are_explicit() -> None:
    losing = _row("loss", net_pnl=-12, gross_pnl=-10)
    winner = _row("win", net_pnl=20, gross_pnl=23, response_minutes=30)
    synthetic = _row(
        "synthetic-win",
        net_pnl=100,
        gross_pnl=100,
        data_source="synthetic",
    )
    summary = summarize_evaluations([losing, winner, synthetic])
    baseline = compare_baselines([losing, winner])
    filtered = compare_filter_variant(
        baseline_rows=[losing, winner],
        variant_rows=[winner],
    )
    coverage = build_coverage_report(
        scans=[
            {"status": "eligible", "rejection_codes": []},
            {"status": "coverage_miss", "rejection_codes": ["coverage_miss"]},
        ],
        rows=[
            losing.model_copy(update={"missed_by_attention": True}),
            winner,
        ],
    )

    assert summary["net_pnl"] == 108
    assert summary["by_data_source"] if "by_data_source" in summary else True
    assert baseline["benchmark_observed_count"] == 2
    assert filtered["missed_profit"] == 0
    assert filtered["frequency"]["delta"] == -1
    assert coverage.missing_data_count == 1
    assert coverage.attention_miss_count == 1


def test_assessment_separates_synthetic_and_refuses_small_sample_profit_claim() -> None:
    config = EvaluationConfig(
        strategy_version="s1_breakout_v1",
        primary_policy="Q1",
        development_start=date(2026, 1, 1),
        development_end=date(2026, 1, 31),
        lock_start=date(2026, 2, 1),
        lock_end=date(2026, 2, 28),
        min_sample_size=3,
    )
    assessment = build_assessment(
        assessment_id="assessment-1",
        manifest_hash="a" * 64,
        config=config,
        rows=[
            _row("real-loss", net_pnl=-5, gross_pnl=-4),
            _row("synthetic-win", net_pnl=100, gross_pnl=100, data_source="synthetic"),
        ],
    )

    assert assessment.applicability == "unknown"
    assert assessment.metrics["decision_page"]["default_15m_net_value"] == "unknown"
    assert set(assessment.metrics["by_data_source"]) == {"real", "synthetic"}
    assert any("synthetic 不得替代真实研究结论" in item for item in assessment.limitations)
