from datetime import UTC, date, datetime
import json
import sqlite3

import pytest

from app.intraday_options.storage import IntradayOptionStore
from urus_moomoo.storage.intraday import CanonicalIntradayStore
from urus_moomoo.services.scheduler import CollectorScheduler


@pytest.fixture
def store(tmp_path):
    path = tmp_path / 'intraday.sqlite'
    bootstrap = IntradayOptionStore(f'sqlite:///{path}', tmp_path / 'raw')
    bootstrap.close()
    store = CanonicalIntradayStore(path, raw_payload_dir=None, market_timezone='America/New_York')
    yield store
    store.close()


def seed(store):
    return store.persist(scheduled_at=datetime(2026, 9, 8, 14, tzinfo=UTC),
        capture_tier='fast', symbols=['SPY'], band_percent=3, payload={
            'captured_at': '2026-09-08T14:00:02+00:00',
            'quality_summary': {'complete': True},
            'symbols': [{'symbol': 'SPY', 'spot': 100, 'expirations': []}],
        })


def close_payload():
    return {'complete': True, 'trading_date': '2026-09-08',
            'captured_at': '2026-09-08T20:05:00+00:00',
            'requested_symbols': ['SPY'], 'official_closes': {'SPY': 102},
            'underlying_bars': [{'symbol': 'SPY', 'bar_time': '2026-09-08T20:00:00+00:00',
                'provider': 'moomoo', 'open': 101, 'high': 103, 'low': 99, 'close': 102}]}


def test_close_materializes_last_bar_and_idempotent_selected_outcome(store):
    seed(store)
    for _ in range(2):
        assert store.finalize_trading_day(date(2026, 9, 8), close_payload()) == 1
    rows = store.connection.execute('SELECT * FROM intraday_option_outcomes').fetchall()
    assert len(rows) == 1
    assert rows[0]['close_return'] == pytest.approx(.02)
    assert rows[0]['max_up_move'] == 3
    assert rows[0]['max_down_move'] == -1
    assert store.connection.execute('SELECT MAX(bar_time) FROM intraday_underlying_minute_bars').fetchone()[0].startswith('2026-09-08 20:00')
    assert store.connection.execute('SELECT count(*) FROM intraday_underlying_minute_bar_revisions').fetchone()[0] == 1


def test_close_failure_rolls_back_bars_and_outcomes(store):
    seed(store)
    store.connection.execute("CREATE TRIGGER fail_outcome BEFORE INSERT ON intraday_option_outcomes BEGIN SELECT RAISE(ABORT, 'failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.finalize_trading_day(date(2026, 9, 8), close_payload())
    assert store.connection.execute('SELECT count(*) FROM intraday_underlying_minute_bars').fetchone()[0] == 0


def test_close_rejects_other_day_before_writing(store):
    payload = close_payload()
    payload['underlying_bars'][0]['bar_time'] = '2026-09-09T20:00:00+00:00'
    with pytest.raises(ValueError, match='trading date'):
        store.finalize_trading_day(date(2026, 9, 8), payload)


def test_calendar_timeout_recovers_without_busy_loop(monkeypatch):
    from urus_moomoo.services import scheduler as module
    calls, sleeps = [], []
    class Calendar:
        def is_session(self, _day):
            calls.append(1)
            if len(calls) == 1:
                raise TimeoutError('temporary')
            return False
    scheduler = CollectorScheduler([], timezone='UTC', calendar=Calendar(), runner=lambda *_: None,
        clock=lambda: datetime(2026, 9, 8, 14, tzinfo=UTC))
    monkeypatch.setattr(module.time, 'sleep', sleeps.append)
    scheduler.run_forever(poll_seconds=10, stop=lambda: len(calls) >= 2)
    assert sleeps == [20, 10]


def test_capture_completeness_does_not_imply_fresh_quotes(store):
    seed(store)
    quality = json.loads(store.connection.execute('SELECT quality_summary FROM intraday_option_capture_runs').fetchone()[0])
    assert quality['complete'] is True
    assert quality['fresh_quote_coverage'] == 0


def test_idle_publication_yields_to_upcoming_capture():
    from urus_moomoo.config import CollectionTask
    class Calendar:
        def is_session(self, _): return True
    calls = []
    scheduler = CollectorScheduler(
        [CollectionTask('capture', True, '14:00', ('SPY',), 'instrument_cards')],
        timezone='UTC', calendar=Calendar(), runner=lambda *_: calls.append('capture'),
        on_idle=lambda: calls.append('release'), on_quiet=lambda: calls.append('publish'),
    )
    scheduler.run_once(datetime(2026, 9, 8, 13, 59, 30, tzinfo=UTC))
    assert calls == ['release']
    scheduler.run_once(datetime(2026, 9, 8, 14, tzinfo=UTC))
    assert calls == ['release', 'release', 'capture']


def test_stored_close_preview_and_replay_without_provider(store, tmp_path):
    from urus_moomoo.config import CollectorConfig, CollectionTask
    from urus_moomoo.runtime import CollectorRuntime
    from urus_moomoo.recovery import recover_close
    seed(store)
    class Provider:
        def capture_session_close(self, **_): return close_payload()
    config = CollectorConfig(timezone='UTC', tasks=(), output_dir=tmp_path / 'collector', intraday_sqlite_path=store.path)
    runtime = CollectorRuntime(Provider(), config)
    task = CollectionTask('close', True, 'post-close', ('SPY',), 'capture_session_close')
    scheduled = datetime(2026, 9, 8, 20, 5, tzinfo=UTC)
    try:
        result = runtime.collect_task(task, scheduled)
        assert store.connection.execute('SELECT count(*) FROM intraday_option_outcomes').fetchone()[0] == 1
        runtime.store.mark_scheduler_completed(task.name, scheduled, capture_id=result['contract']['capture_id'])
    finally:
        runtime.close()
    before = store.connection.total_changes
    preview = recover_close(config, date(2026, 9, 8))
    assert preview['apply'] is False and preview['bars'] == 1
    assert store.connection.total_changes == before
    assert recover_close(config, date(2026, 9, 8), apply=True)['outcomes'] == 1
    assert store.connection.execute('SELECT count(*) FROM intraday_option_outcomes').fetchone()[0] == 1


def test_audit_detects_whole_missing_session_but_not_weekend(tmp_path):
    from urus_moomoo.config import CollectorConfig, CollectionTask
    from urus_moomoo.storage.sqlite import CollectorStore
    from urus_moomoo.recovery import audit_dates
    config = CollectorConfig(timezone='UTC', output_dir=tmp_path, tasks=(
        CollectionTask('market', True, 'post-close', ('SPY',), 'instrument_cards'),))
    CollectorStore(tmp_path / 'collector.sqlite').close()
    result = audit_dates(config, date(2026, 10, 2), date(2026, 10, 4))
    assert result['expected_sessions'] == 1
    assert result['missing_completed_capture_dates'] == {'market': ['2026-10-02']}


def test_quote_quality_counts_stale_and_zero_without_reclassifying_completeness(store):
    contracts = [
        {'contract_code': f'US.SPY260909C00{i}00000', 'option_type': 'CALL', 'strike': 100+i,
         'bid': bid, 'ask': ask, 'quote_time': stamp}
        for i, (bid, ask, stamp) in enumerate([
            (1, 2, '2026-09-08T14:00:00+00:00'),
            (1, 2, '2026-09-08T13:58:00+00:00'),
            (0, 0, '2026-09-08T14:00:00+00:00'),
        ])
    ]
    store.persist(scheduled_at=datetime(2026, 9, 8, 14, tzinfo=UTC), capture_tier='fast',
        symbols=['SPY'], band_percent=3, payload={
            'captured_at': '2026-09-08T14:00:02+00:00', 'quality_summary': {'complete': True},
            'symbols': [{'symbol': 'SPY', 'spot': 100,
                         'expirations': [{'expiration': '2026-09-09', 'contracts': contracts}]}],
        })
    quality = json.loads(store.connection.execute('SELECT quality_summary FROM intraday_option_capture_runs').fetchone()[0])
    assert quality['complete'] is True
    assert quality['quote_states'] == {'ready': 1, 'stale': 1, 'zero_quote': 1}
    assert quality['fresh_quote_coverage'] == pytest.approx(1/3)
