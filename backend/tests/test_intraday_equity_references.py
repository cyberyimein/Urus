import json
import sqlite3
import pytest

from app.decision_harness.contracts import content_sha256
from app.intraday.references import hydrate_capture, resolved_capture_data


def _fixture(path=":memory:"):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE equity_data_watermarks(watermark INTEGER PRIMARY KEY, recorded_at TEXT, change_kind TEXT);
        CREATE TABLE equity_bar_revisions(watermark INTEGER PRIMARY KEY, provider TEXT, symbol TEXT,
          timeframe TEXT, adjustment TEXT, bar_end_at TEXT, bar_json TEXT, content_sha256 TEXT,
          observed_at TEXT, source_capture_id TEXT);
        CREATE TABLE equity_quote_batches(task_name TEXT, scheduled_at TEXT, watermark INTEGER,
          captured_at TEXT, quotes_json TEXT, content_sha256 TEXT, PRIMARY KEY(task_name,scheduled_at));
    """)
    bar = {
        "provider": "moomoo_openapi", "symbol": "SPY", "adjustment": "none",
        "bar_start_at": "2026-10-15T13:30:00+00:00", "bar_end_at": "2026-10-15T13:31:00+00:00",
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
        "volume": 100.0, "turnover": 10050.0, "quality_state": "ready",
    }
    connection.execute("INSERT INTO equity_data_watermarks VALUES (1,?, 'bar')", ("2026-10-15T13:36:00+00:00",))
    connection.execute("INSERT INTO equity_bar_revisions VALUES (?,?,?,?,?,?,?,?,?,?)", (
        1, "moomoo_openapi", "SPY", "1m", "none", bar["bar_end_at"], json.dumps(bar),
        content_sha256(bar), "2026-10-15T13:36:00+00:00", "capture-g1",
    ))
    ref = {
        "schema_version": "moomoo-intraday-equities-refs-v1", "kind": "bars",
        "provider": "moomoo_openapi", "symbols": ["SPY"], "timeframe": "1m",
        "adjustment": "none", "from": "2026-10-15T13:30:00+00:00",
        "through": "2026-10-15T13:35:00+00:00", "asof_revision_watermark": 1,
        "row_count": 1, "content_sha256": content_sha256([bar]),
    }
    data = {"schema_version": "moomoo-intraday-equities-refs-v1", "equity_data_refs": [ref]}
    capture = {
        "capture_id": "capture-final", "captured_at": "2026-10-15T13:39:00+00:00",
        "requested_symbols": ["SPY"], "data": data, "content_sha256": content_sha256(data),
    }
    connection.commit()
    return connection, capture, bar


def test_hydration_does_not_rewrite_compact_capture_hash_and_old_revision_stays_frozen():
    connection, capture, bar = _fixture()
    revised = {**bar, "close": 100.8}
    connection.execute("INSERT INTO equity_data_watermarks VALUES (2,?, 'bar')", ("2026-10-15T13:40:00+00:00",))
    connection.execute("INSERT INTO equity_bar_revisions SELECT 2,provider,symbol,timeframe,adjustment,bar_end_at,?,?,?,source_capture_id FROM equity_bar_revisions WHERE watermark=1", (
        json.dumps(revised), content_sha256(revised), "2026-10-15T13:40:00+00:00",
    ))
    hydrated = hydrate_capture(connection, capture)
    assert hydrated["data"] == capture["data"]
    assert hydrated["content_sha256"] == capture["content_sha256"]
    assert resolved_capture_data(hydrated)["minute_bars"] == [bar]
    assert "_resolved_equity_refs" not in capture


def test_expired_or_missing_reference_is_not_replaced_with_latest_rows():
    connection, capture, _ = _fixture()
    connection.execute("DELETE FROM equity_bar_revisions")
    with pytest.raises(ValueError, match="reference|引用"):
        hydrate_capture(connection, capture)


def test_modified_storage_or_modified_offline_bundle_fails_content_hash():
    connection, capture, bar = _fixture()
    hydrated = hydrate_capture(connection, capture)
    hydrated["_resolved_equity_refs"][0][0]["close"] = 999
    with pytest.raises(ValueError, match="hash|哈希"):
        resolved_capture_data(hydrated)
    connection.execute("UPDATE equity_bar_revisions SET bar_json=?", (json.dumps({**bar, "close": 999}),))
    with pytest.raises(ValueError, match="hash|哈希"):
        hydrate_capture(connection, capture)


def test_reference_watermark_cannot_claim_data_recorded_after_capture():
    connection, capture, _ = _fixture()
    connection.execute("UPDATE equity_bar_revisions SET observed_at='2026-10-15T13:45:00+00:00'")
    with pytest.raises(ValueError, match="later|future|晚于|之后"):
        hydrate_capture(connection, capture)


def test_reference_scope_cannot_expand_beyond_capture_symbols():
    connection, capture, _ = _fixture()
    capture["data"]["equity_data_refs"][0]["symbols"] = ["QQQ"]
    capture["content_sha256"] = content_sha256(capture["data"])
    with pytest.raises(ValueError, match="scope|范围"):
        hydrate_capture(connection, capture)


def test_quote_batch_is_read_once_without_provider_raw_duplication():
    connection, capture, _ = _fixture()
    quotes = [{"symbol": "SPY", "last_price": 100.5, "received_at": "2026-10-15T13:36:00+00:00"}]
    connection.execute("INSERT INTO equity_quote_batches VALUES (?,?,?,?,?,?)", (
        "stocks:G1", "2026-10-15T13:35:00+00:00", 1,
        "2026-10-15T13:36:00+00:00", json.dumps(quotes), content_sha256(quotes),
    ))
    ref = {**capture["data"]["equity_data_refs"][0], "kind": "quotes", "timeframe": "snapshot",
           "task_name": "stocks:G1", "scheduled_at": "2026-10-15T13:35:00+00:00",
           "content_sha256": content_sha256(quotes)}
    capture["data"]["equity_data_refs"].append(ref)
    capture["content_sha256"] = content_sha256(capture["data"])
    result = resolved_capture_data(hydrate_capture(connection, capture))
    assert result["quotes"] == quotes
    assert result["provider_raw"] == {}


def _compact_projection_capture():
    from test_intraday_equities_acceptance import _capture_contract, _minute_rows

    capture = _capture_contract(_minute_rows("AAA"), benchmark_bars=_minute_rows("SPY"))
    data = capture["data"]
    minutes = sorted(data.pop("minute_bars"), key=lambda row: (row["symbol"], row["bar_end_at"]))
    quotes = sorted(data.pop("quotes"), key=lambda row: row["symbol"])
    data.pop("provider_raw")
    data["schema_version"] = "moomoo-intraday-equities-refs-v1"
    base = {
        "schema_version": data["schema_version"], "provider": "moomoo_openapi",
        "symbols": ["AAA", "SPY"], "adjustment": "none",
        "from": "2026-10-15T13:30:00+00:00", "through": data["evidence_cutoff"],
        "asof_revision_watermark": 100,
    }
    data["equity_data_refs"] = [
        {**base, "kind": "bars", "timeframe": "1m", "row_count": len(minutes), "content_sha256": content_sha256(minutes)},
        {**base, "kind": "quotes", "timeframe": "snapshot", "row_count": len(quotes), "content_sha256": content_sha256(quotes),
         "task_name": capture["task_name"] + ":G1", "scheduled_at": capture["scheduled_at"]},
    ]
    capture["content_sha256"] = content_sha256(data)
    capture["_resolved_equity_refs"] = [minutes, quotes]
    return capture


def test_p3_projects_compact_references_without_changing_source_identity():
    from datetime import datetime
    from app.intraday.equities import build_intraday_projection

    capture = _compact_projection_capture()
    result = build_intraday_projection(capture, now=datetime.fromisoformat(capture["captured_at"]))
    assert result["source"]["content_sha256"] == capture["content_sha256"]
    assert result["coverage"]["bars_5m_available"] == 12
    assert result["symbols"]["AAA"]["quote"]["last_price"] == 102.0


def test_p3_compact_capture_requires_verified_resolved_rows():
    from datetime import datetime
    from app.intraday.equities import build_intraday_projection

    capture = _compact_projection_capture()
    capture.pop("_resolved_equity_refs")
    with pytest.raises(ValueError, match="reference|引用"):
        build_intraday_projection(capture, now=datetime.fromisoformat(capture["captured_at"]))


def _write_compact_sidecar(path):
    connection, _, _ = _fixture(path)
    capture = _compact_projection_capture()
    minutes, quotes = capture.pop("_resolved_equity_refs")
    connection.execute("DELETE FROM equity_bar_revisions")
    for index, bar in enumerate(minutes, start=2):
        connection.execute("INSERT INTO equity_bar_revisions VALUES (?,?,?,?,?,?,?,?,?,?)", (
            index, "moomoo_openapi", bar["symbol"], "1m", "none", bar["bar_end_at"],
            json.dumps(bar), content_sha256(bar), capture["captured_at"], "capture-g1",
        ))
    quote_ref = capture["data"]["equity_data_refs"][1]
    connection.execute("INSERT INTO equity_quote_batches VALUES (?,?,?,?,?,?)", (
        quote_ref["task_name"], quote_ref["scheduled_at"], 99, capture["captured_at"],
        json.dumps(quotes), content_sha256(quotes),
    ))
    connection.executescript("""
        CREATE TABLE raw_captures(capture_id TEXT PRIMARY KEY,schema_version TEXT,kind TEXT,captured_at TEXT,payload_json TEXT);
        CREATE TABLE scheduled_captures(task_name TEXT,scheduled_at TEXT,capture_id TEXT,kind TEXT,completed INTEGER,updated_at TEXT);
    """)
    connection.execute("INSERT INTO raw_captures VALUES (?,?,?,?,?)", (
        capture["capture_id"], capture["schema_version"], capture["kind"], capture["captured_at"], json.dumps(capture),
    ))
    connection.execute("INSERT INTO scheduled_captures VALUES (?,?,?,?,?,?)", (
        capture["task_name"], capture["scheduled_at"], capture["capture_id"], capture["kind"], 1, capture["captured_at"],
    ))
    connection.commit()
    connection.close()
    return capture


def test_read_only_api_loader_resolves_compact_capture_from_same_database(tmp_path):
    from app.api.intraday import _load_latest_capture

    path = tmp_path / "collector.sqlite"
    original = _write_compact_sidecar(path)
    loaded, error = _load_latest_capture(str(path))
    assert error is None
    assert loaded["content_sha256"] == original["content_sha256"]
    assert len(loaded["_resolved_equity_refs"][0]) == 60
    with sqlite3.connect(path) as connection:
        persisted = json.loads(connection.execute("SELECT payload_json FROM raw_captures").fetchone()[0])
    assert "_resolved_equity_refs" not in persisted


def test_event_source_reader_resolves_same_reference_contract(tmp_path):
    from app.intraday.event_service import _read_source_rows

    path = tmp_path / "collector.sqlite"
    _write_compact_sidecar(path)
    rows, backlog, error = _read_source_rows(str(path), cursor_data={}, max_slots=12)
    assert error is None and backlog is False
    assert len(rows) == 1
    assert rows[0]["data_hash_valid"] is True
    assert len(rows[0]["contract"]["_resolved_equity_refs"][0]) == 60


def test_normalized_event_consumer_applies_two_session_retention_to_its_own_task(tmp_path):
    from datetime import datetime
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session, sessionmaker
    from app.core.database import Base
    from app.intraday.event_service import PROCESSOR_ID, run_once
    from app.models.intraday_events import IntradayEventModel
    from test_intraday_event_retention import _event

    source = tmp_path / "collector.sqlite"
    capture = _write_compact_sidecar(source)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    event = _event("old-normalized", "2026-10-13", processor=PROCESSOR_ID)
    event.task_name = capture["task_name"]
    with Session(engine) as session:
        session.add(event)
        session.commit()
    result = run_once(
        source_db_path=source,
        session_factory=sessionmaker(bind=engine), now=datetime.fromisoformat(capture["captured_at"]),
    )
    assert result["status"] in {"ok", "partial"}
    assert result["event_retention"]["deleted_events"] == 1
    with Session(engine) as session:
        assert session.scalar(select(IntradayEventModel).where(IntradayEventModel.setup_id == "old-normalized")) is None
    engine.dispose()


def test_reference_hash_validation_does_not_hold_back_shared_eof_writes(tmp_path, monkeypatch):
    from app.intraday import references

    source = tmp_path / "collector.sqlite"
    connection, capture, _ = _fixture(source)
    connection.execute(
        "CREATE TABLE raw_captures(capture_id TEXT PRIMARY KEY, kind TEXT, payload_json TEXT)"
    )
    connection.commit()
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    connection.close()
    validate = references._validate_rows

    def validate_with_eof_write(ref, rows):
        # The rows are already fetched. Slow hashing must not retain a read
        # transaction that prevents the existing collector committing Eof data.
        writer = sqlite3.connect(source, timeout=0.02)
        try:
            with writer:
                writer.execute(
                    "INSERT INTO raw_captures VALUES (?, ?, ?)",
                    ("eof-during-validation", "capture_intraday", "{}"),
                )
        finally:
            writer.close()
        return validate(ref, rows)

    monkeypatch.setattr(references, "_validate_rows", validate_with_eof_write)
    hydrated = references.hydrate_capture_from_path(source, capture)
    assert len(hydrated["_resolved_equity_refs"][0]) == 1
    with sqlite3.connect(source) as verification:
        assert verification.execute(
            "SELECT kind FROM raw_captures WHERE capture_id=?", ("eof-during-validation",)
        ).fetchone()[0] == "capture_intraday"
