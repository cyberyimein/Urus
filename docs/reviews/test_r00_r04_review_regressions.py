"""Independent acceptance regressions. Run from backend with its virtualenv.

.venv/bin/python -m pytest ../docs/reviews/test_r00_r04_review_regressions.py -q
These assert the required behavior, not the reviewed implementation's behavior.
"""
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
import runpy
import sys

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))
fixtures = runpy.run_path(str(BACKEND / "tests/test_buy_opportunities.py"))
evaluation_fixtures = runpy.run_path(str(BACKEND / "tests/test_buy_evaluation.py"))

from app.buy_opportunities.breakout import build_s1_setup, IntradayObservation, evaluate_s1_trigger
from app.buy_opportunities.contracts import EvidenceBar
from app.research.buy_evaluation import EvaluationConfig, build_assessment
from app.research.buy_execution import (
    QuoteObservation, ExecutionPolicy, build_q1_entry_intent, execute_q1,
    evaluate_execution_eligibility, simulate_long_exit,
)


def scenario():
    t = datetime(2026, 3, 12, 14, tzinfo=UTC)
    rows = fixtures['_bars']('AAA')
    for row in rows:
        row['available_at'] = row['collected_at'] = t - timedelta(hours=12)
    evidence = fixtures['freeze_daily_evidence'](symbol='AAA', asset_type='equity',
        bars=rows, knowledge_at=t-timedelta(hours=12),
        source_mode='historical_reconstruction', price_basis='adjusted',
        trading_date=date(2026,3,11))
    setup = build_s1_setup(evidence=evidence, origin_scan_id='scan')
    obs = IntradayObservation(bar_id='bar', start_at=t-timedelta(minutes=15),
        end_at=t, available_at=t, open=109, high=111, low=108, close=110.5)
    trigger = evaluate_s1_trigger(setup=setup, observation=obs, previous_close=109,
        known_at=t, session_number=1).trigger
    ready = t + timedelta(minutes=15)
    q0 = QuoteObservation(quote_id='q0', timestamp=ready, received_at=ready, bid=110, ask=110.5)
    intent, _ = build_q1_entry_intent(trigger=trigger, setup=setup, created_at=t,
        delivered_at=t, user_ready_at=ready, quotes=[q0], nav=100000, cash=100000)
    return setup, obs, trigger, intent, t, ready


def test_development_one_minute_rows_cannot_prove_locked_fifteen_minute_value():
    config = EvaluationConfig(strategy_version='s1', primary_policy='Q1',
        development_start=date(2026,1,1), development_end=date(2026,1,31),
        lock_start=date(2026,2,1), lock_end=date(2026,2,28))
    rows = [evaluation_fixtures['_row'](str(i), net_pnl=20, gross_pnl=22,
        response_minutes=1).model_copy(update={'benchmark_return': None}) for i in range(30)]
    result = build_assessment(assessment_id='review', manifest_hash='a'*64, config=config, rows=rows)
    assert result.metrics['decision_page']['default_15m_net_value'] == 'unknown'


def test_reprocessing_same_bar_preserves_trigger_identity():
    setup, obs, trigger, _, t, _ = scenario()
    again = evaluate_s1_trigger(setup=setup, observation=obs, previous_close=109,
        known_at=t+timedelta(seconds=1), session_number=1).trigger
    assert again.trigger_id == trigger.trigger_id


def test_q1_cannot_fill_before_user_ready_and_q0():
    setup, _, _, intent, t, ready = scenario()
    quote = QuoteObservation(quote_id='early', timestamp=t+timedelta(seconds=2),
        received_at=t+timedelta(seconds=2), bid=110, ask=110.5)
    try:
        result = execute_q1(intent=intent, setup=setup, q1_quotes=[quote],
            sent_at=t, policy=ExecutionPolicy(name='Q1'))
    except ValueError:
        return
    assert result.status != 'filled' or result.fill_at >= ready


@pytest.mark.parametrize('kwargs', [{'has_existing_position': True}, {'coverage_status': 'failed'}])
def test_entry_gate_blocks_unavailable_or_already_held(kwargs):
    assert evaluate_execution_eligibility(asset_type='equity', source_mode='observed_pit',
        **kwargs).status != 'eligible'


def test_close_failure_cannot_exit_before_close_is_available():
    setup, _, _, intent, t, ready = scenario()
    q1 = QuoteObservation(quote_id='fill', timestamp=ready+timedelta(seconds=2),
        received_at=ready+timedelta(seconds=2), bid=110, ask=110.5)
    fill = execute_q1(intent=intent, setup=setup, q1_quotes=[q1], sent_at=ready,
        policy=ExecutionPolicy(name='Q1'))
    close_known = t+timedelta(days=1, hours=6)
    bar = EvidenceBar(date=date(2026,3,13), open=110, high=112, low=106, close=109,
        volume=1000000, available_at=close_known, collected_at=close_known,
        source='fixture', source_revision='close')
    early = QuoteObservation(quote_id='early_sell', timestamp=t+timedelta(days=1),
        received_at=t+timedelta(days=1), bid=111, ask=111.5)
    result = simulate_long_exit(execution=fill, setup=setup, daily_bars=[bar],
        sell_quotes={bar.date: early})
    assert result.exit_at is None or result.exit_at >= close_known


def test_setup_origin_resolves_to_emitted_scan():
    scans, setups = fixtures['scan_universe'](universe_items=[{'symbol':'AAA'}],
        evidence_reader=fixtures['InMemoryDailyEvidenceReader']({'AAA':fixtures['_evidence']('AAA')}),
        pool_version='review', knowledge_at=fixtures['KNOWLEDGE_AT'])
    assert setups[0].origin_scan_id == scans[0].scan_id
