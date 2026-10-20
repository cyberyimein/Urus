"""Offline inspection and explicit replay of stored close captures; no provider calls."""
from __future__ import annotations

from datetime import date
import json
import hashlib
from pathlib import Path
import sqlite3

from .storage.intraday import CanonicalIntradayStore


def recover_close(config, trading_date: date, *, apply: bool = False) -> dict:
    path = Path(config.sqlite_path or Path(config.output_dir) / 'collector.sqlite')
    connection = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        row = connection.execute(
            """SELECT r.payload_json FROM scheduler_runs s
               JOIN raw_captures r ON r.capture_id = s.capture_id
               WHERE r.kind = 'capture_session_close'
                 AND json_extract(r.payload_json, '$.trading_date') = ?
               ORDER BY s.completed_at DESC LIMIT 1""", (trading_date.isoformat(),),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError('no completed stored close capture for requested date')
    contract = json.loads(row[0])
    if (contract.get('kind') != 'capture_session_close'
            or contract.get('trading_date') != trading_date.isoformat()):
        raise ValueError('stored close identity mismatch')
    payload = contract['data']
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':'), default=str).encode()).hexdigest()
    if contract.get('content_sha256') != digest:
        raise ValueError('stored close content hash mismatch')
    if not payload.get('complete'):
        raise ValueError('stored close capture is incomplete')
    result = {'trading_date': trading_date.isoformat(), 'apply': apply,
              'bars': len(payload.get('underlying_bars', [])),
              'symbols': len(payload.get('official_closes', {}))}
    if apply:
        if config.intraday_sqlite_path is None:
            raise ValueError('intraday_sqlite_path required')
        store = CanonicalIntradayStore(config.intraday_sqlite_path, raw_payload_dir=None,
                                      market_timezone=config.market_timezone)
        try:
            result['outcomes'] = store.finalize_trading_day(trading_date, payload)
        finally:
            store.close()
    return result


def audit_dates(config, start: date, end: date) -> dict:
    """Explicit historical range avoids inventing an installation/enablement date."""
    import exchange_calendars

    if start > end:
        raise ValueError('start must not be after end')
    calendar = exchange_calendars.get_calendar(config.exchange_calendar)
    expected = {d.date().isoformat() for d in calendar.sessions_in_range(start, end)}
    path = Path(config.sqlite_path or Path(config.output_dir) / 'collector.sqlite')
    connection = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        missing = {}
        for task in config.tasks:
            if not task.enabled or task.operation not in {'instrument_cards', 'options_snapshot', 'capture_session_close'}:
                continue
            dates = {row[0] for row in connection.execute(
                """SELECT DISTINCT json_extract(r.payload_json, '$.trading_date')
                   FROM scheduler_runs s JOIN raw_captures r ON r.capture_id=s.capture_id
                   WHERE s.task_name = ?""", (task.name,),
            )}
            missing[task.name] = sorted(expected - dates)
        return {'start': start.isoformat(), 'end': end.isoformat(),
                'expected_sessions': len(expected), 'missing_completed_capture_dates': missing}
    finally:
        connection.close()
