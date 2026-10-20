"""Independent acceptance tests for normalized intraday equity references.

The fixtures use a temporary SQLite sidecar populated with the public four-table
reference contract.  They intentionally do not call a provider, the scheduler,
or the production database.  These tests cover the resolver-to-P3-to-P4 seam and
the historical indicator rules that are easy to accidentally regress when raw
shards are replaced by references.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping, Sequence

import exchange_calendars as xcals
import pytest

from app.decision_harness.contracts import content_sha256
from app.intraday.equities import build_intraday_projection
from app.intraday.history_features import enrich_indicator_history
from app.intraday.references import hydrate_capture_from_path, resolved_capture_data


_TRADE_DATE = date(2026, 10, 15)
_SESSION_OPEN = datetime(2026, 10, 15, 13, 30, tzinfo=UTC)
_CUTOFF = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)
_CAPTURED_AT = datetime(2026, 10, 15, 14, 0, 3, tzinfo=UTC)


def _minute_bar(
    symbol: str,
    index: int,
    *,
    start: datetime = _SESSION_OPEN,
    close_offset: float = 0.0,
    volume: float | None = None,
) -> dict[str, Any]:
    bar_start = start + timedelta(minutes=index)
    bar_end = bar_start + timedelta(minutes=1)
    close = 100.0 + close_offset + index * 0.1
    bar_volume = float(volume if volume is not None else 100.0 + index)
    return {
        "symbol": symbol,
        "bar_start_at": bar_start.isoformat(),
        "bar_end_at": bar_end.isoformat(),
        "open": close - 0.05,
        "high": close + 0.2,
        "low": close - 0.2,
        "close": close,
        "volume": bar_volume,
        "turnover": bar_volume * close,
        "provider": "moomoo_openapi",
        "quality_state": "ready",
    }


def _five_minute_bar(
    symbol: str,
    start: datetime,
    *,
    close: float,
    volume: float,
    trading_date: str | None = None,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "bar_start_at": start.isoformat(),
        "bar_end_at": (start + timedelta(minutes=5)).isoformat(),
        "open": close - 0.1,
        "high": close + 0.25,
        "low": close - 0.25,
        "close": close,
        "volume": float(volume),
        "turnover": float(volume) * close,
        "provider": "moomoo_openapi",
        "quality_state": "ready",
        **({"trading_date": trading_date} if trading_date else {}),
    }


def _ref(
    *,
    kind: str,
    timeframe: str,
    symbols: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
    from_time: datetime,
    through_time: datetime,
    watermark: int,
    capture_trading_date: str = _TRADE_DATE.isoformat(),
    task_name: str | None = None,
    scheduled_at: datetime | None = None,
) -> dict[str, Any]:
    ordered = sorted((dict(row) for row in rows), key=lambda row: (
        str(row.get("symbol") or ""),
        str(row.get("bar_end_at") or ""),
    ))
    descriptor: dict[str, Any] = {
        "schema_version": "moomoo-intraday-equities-refs-v1",
        "ref_id": "sha256:pending",
        "kind": kind,
        "provider": "moomoo_openapi",
        "symbols": sorted({str(symbol).upper() for symbol in symbols}),
        "timeframe": timeframe,
        "adjustment": "none",
        "from": from_time.isoformat(),
        "through": through_time.isoformat(),
        "capture_trading_date": capture_trading_date,
        "asof_revision_watermark": watermark,
        "row_count": len(ordered),
        "content_sha256": content_sha256(ordered),
        "status": "available" if ordered else "unavailable",
    }
    if kind == "quotes":
        descriptor.pop("adjustment")
        descriptor["task_name"] = task_name or "intraday-equities"
        descriptor["scheduled_at"] = (scheduled_at or _CUTOFF).isoformat()
    descriptor["ref_id"] = "sha256:" + content_sha256(descriptor)
    return descriptor


def _create_sidecar(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE equity_data_watermarks(
          watermark INTEGER PRIMARY KEY, recorded_at TEXT NOT NULL,
          change_kind TEXT NOT NULL, trading_date TEXT
        );
        CREATE TABLE equity_bar_revisions(
          watermark INTEGER PRIMARY KEY, provider TEXT NOT NULL, symbol TEXT NOT NULL,
          timeframe TEXT NOT NULL, adjustment TEXT NOT NULL, bar_end_at TEXT NOT NULL,
          trading_date TEXT NOT NULL, bar_json TEXT NOT NULL,
          content_sha256 TEXT NOT NULL, observed_at TEXT NOT NULL,
          source_capture_id TEXT NOT NULL
        );
        CREATE TABLE equity_quote_batches(
          task_name TEXT NOT NULL, scheduled_at TEXT NOT NULL, trading_date TEXT NOT NULL,
          watermark INTEGER NOT NULL, captured_at TEXT NOT NULL, quotes_json TEXT NOT NULL,
          content_sha256 TEXT NOT NULL, source_capture_id TEXT NOT NULL,
          PRIMARY KEY(task_name, scheduled_at)
        );
        CREATE TABLE raw_captures(
          capture_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL, kind TEXT NOT NULL,
          captured_at TEXT NOT NULL, payload_json TEXT NOT NULL
        );
        CREATE TABLE scheduled_captures(
          task_name TEXT NOT NULL, scheduled_at TEXT NOT NULL, capture_id TEXT NOT NULL,
          kind TEXT NOT NULL, completed INTEGER NOT NULL, updated_at TEXT NOT NULL,
          PRIMARY KEY(task_name, scheduled_at)
        );
        """
    )
    return connection


def _insert_capture_database(
    path: Path,
    capture: dict[str, Any],
    bundles: Sequence[Sequence[Mapping[str, Any]]],
    *,
    # The producer writes stored_at independently of the envelope capture
    # timestamp.  It still has to precede the capture's evidence timestamp so
    # the resolver can prove the rows were known at capture time.
    stored_at: str = "2026-10-15T14:00:01+00:00",
) -> None:
    connection = _create_sidecar(path)
    try:
        watermark = 0
        for ref, rows in zip(capture["data"]["equity_data_refs"], bundles, strict=True):
            if ref["kind"] == "quotes":
                watermark += 1
                quotes_json = json.dumps(
                    list(rows), ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                connection.execute(
                    "INSERT INTO equity_data_watermarks VALUES (?, ?, ?, ?)",
                    (watermark, stored_at, "quote_batch", capture["trading_date"]),
                )
                connection.execute(
                    "INSERT INTO equity_quote_batches VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        ref["task_name"],
                        ref["scheduled_at"],
                        capture["trading_date"],
                        watermark,
                        stored_at,
                        quotes_json,
                        content_sha256(list(rows)),
                        capture["capture_id"],
                    ),
                )
                continue
            for row in rows:
                watermark += 1
                bar = dict(row)
                bar_json = json.dumps(
                    bar, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                connection.execute(
                    "INSERT INTO equity_data_watermarks VALUES (?, ?, ?, ?)",
                    (watermark, stored_at, "bar_insert", str(bar.get("trading_date") or capture["trading_date"])),
                )
                connection.execute(
                    "INSERT INTO equity_bar_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        watermark,
                        "moomoo_openapi",
                        bar["symbol"],
                        ref["timeframe"],
                        ref.get("adjustment", "none"),
                        bar["bar_end_at"],
                        str(bar.get("trading_date") or capture["trading_date"]),
                        bar_json,
                        content_sha256(bar),
                        stored_at,
                        capture["capture_id"],
                    ),
                )
        # The loader must be able to prove that the newest scheduled row is the
        # one being read.  ``stored_at`` is deliberately different from the
        # envelope's captured_at to exercise the real producer bridge.
        connection.execute(
            "INSERT INTO raw_captures VALUES (?, ?, ?, ?, ?)",
            (
                capture["capture_id"],
                capture["schema_version"],
                capture["kind"],
                stored_at,
                json.dumps(capture, ensure_ascii=False, sort_keys=True),
            ),
        )
        connection.execute(
            "INSERT INTO scheduled_captures VALUES (?, ?, ?, ?, ?, ?)",
            (
                capture["task_name"],
                capture["scheduled_at"],
                capture["capture_id"],
                capture["kind"],
                1,
                stored_at,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _compact_capture() -> tuple[dict[str, Any], list[list[dict[str, Any]]]]:
    aaa_minutes = [_minute_bar("AAA", index) for index in range(30)]
    spy_minutes = [_minute_bar("SPY", index, close_offset=400.0) for index in range(30)]
    history = [
        _five_minute_bar(
            "AAA",
            datetime(2026, 10, 14, 13, 30, tzinfo=UTC),
            close=100.0,
            volume=120.0,
            trading_date="2026-10-14",
        ),
        _five_minute_bar(
            "SPY",
            datetime(2026, 10, 14, 13, 30, tzinfo=UTC),
            close=500.0,
            volume=120.0,
            trading_date="2026-10-14",
        ),
    ]
    quotes = [
        {
            "symbol": "AAA",
            "last_price": 103.0,
            "update_time": "2026-10-15T14:00:02+00:00",
            "source": "moomoo_opend_snapshot",
        },
        {
            "symbol": "SPY",
            "last_price": 503.0,
            "update_time": "2026-10-15T14:00:02+00:00",
            "source": "moomoo_opend_snapshot",
        },
    ]
    g1 = _ref(
        kind="bars", timeframe="1m", symbols=["AAA"], rows=aaa_minutes,
        from_time=_SESSION_OPEN, through_time=_CUTOFF, watermark=30,
    )
    g2 = _ref(
        kind="bars", timeframe="1m", symbols=["SPY"], rows=spy_minutes,
        from_time=_SESSION_OPEN, through_time=_CUTOFF, watermark=60,
    )
    g3 = _ref(
        kind="bars", timeframe="5m", symbols=["AAA", "SPY"], rows=history,
        from_time=datetime(2026, 10, 14, 13, 30, tzinfo=UTC),
        through_time=datetime(2026, 10, 14, 13, 35, tzinfo=UTC), watermark=62,
        capture_trading_date=_TRADE_DATE.isoformat(),
    )
    quote_ref = _ref(
        kind="quotes", timeframe="snapshot", symbols=["AAA", "SPY"], rows=quotes,
        from_time=datetime(2026, 10, 15, 14, 0, 2, tzinfo=UTC),
        through_time=datetime(2026, 10, 15, 14, 0, 2, tzinfo=UTC), watermark=63,
        task_name="intraday-equities:G1",
        scheduled_at=_CUTOFF,
    )
    refs = [g1, g2, g3, quote_ref]
    data: dict[str, Any] = {
        "schema_version": "moomoo-intraday-equities-refs-v1",
        "provider": "moomoo_openapi",
        "trading_date": _TRADE_DATE.isoformat(),
        "scheduled_at": _CUTOFF.isoformat(),
        "evidence_cutoff": _CUTOFF.isoformat(),
        "minute_timestamp_role": "start",
        "minute_timestamp_policy": {
            "source_fields": ["bar_start_at", "bar_end_at"],
            "role": "start",
            "interval_seconds": 60,
            "provider_boundary_verified": True,
        },
        "quote_symbols": ["AAA", "SPY"],
        "minute_symbols": ["AAA", "SPY"],
        "requested_symbols": ["AAA", "SPY"],
        "group_by_symbol": {"AAA": "C", "SPY": "A"},
        "quality_summary": {
            "complete": True,
            "status": "ok",
            "quote_coverage": 1.0,
            "minute_symbols_requested": 2,
            "minute_symbols_admitted": 2,
            "minute_bars_returned": 60,
            "unavailable_minute_symbols": [],
            "quality_warnings": [],
        },
        "equity_data_refs": refs,
    }
    capture = {
        "schema_version": "urus.moomoo.capture.v2",
        "capture_id": "normalized-capture-1",
        "kind": "capture_intraday_equities",
        "task_name": "intraday-equities",
        "provider": "moomoo_openapi",
        "scheduled_at": _CUTOFF.isoformat(),
        "trading_date": _TRADE_DATE.isoformat(),
        "captured_at": _CAPTURED_AT.isoformat(),
        "requested_symbols": ["AAA", "SPY"],
        "content_sha256": content_sha256(data),
        "data": data,
    }
    return capture, [aaa_minutes, spy_minutes, history, quotes]


def _session_bars_for_history(
    symbol: str = "AAA",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    calendar = xcals.get_calendar("XNYS")
    sessions = list(calendar.sessions_in_range(date(2026, 9, 15), _TRADE_DATE))[-21:]
    historical: list[dict[str, Any]] = []
    for day_index, session in enumerate(sessions[:-1]):
        opened = calendar.session_open(session).to_pydatetime().astimezone(UTC)
        for index in range(78):
            historical.append(
                _five_minute_bar(
                    symbol,
                    opened + timedelta(minutes=5 * index),
                    close=100.0 + day_index + index * 0.01,
                    volume=100.0 + day_index,
                    trading_date=session.date().isoformat(),
                )
            )
    current_open = calendar.session_open(sessions[-1]).to_pydatetime().astimezone(UTC)
    current = [
        _five_minute_bar(
            symbol,
            current_open + timedelta(minutes=5 * index),
            close=130.0 + index * 0.1,
            volume=200.0 + index,
            trading_date=_TRADE_DATE.isoformat(),
        )
        for index in range(6)
    ]
    # These are intentionally passed in the historical input with today's
    # date.  A current-day cache row must not contaminate prior-day RVOL.
    historical.append(
        _five_minute_bar(
            symbol,
            current_open,
            close=999.0,
            volume=1_000_000.0,
            trading_date=_TRADE_DATE.isoformat(),
        )
    )
    return current, historical


def test_three_group_refs_hydrate_into_p3_and_p4_without_inline_or_provider_raw_duplication(
    tmp_path: Path,
) -> None:
    capture, bundles = _compact_capture()
    path = tmp_path / "collector.sqlite"
    _insert_capture_database(path, capture, bundles)

    hydrated = hydrate_capture_from_path(path, capture)
    resolved = resolved_capture_data(hydrated)
    projection = build_intraday_projection(
        hydrated,
        now=datetime(2026, 10, 15, 14, 1, tzinfo=UTC),
    )
    from app.intraday.events import evaluate

    events_result = evaluate(
        projection,
        None,
        None,
        evaluated_at=datetime(2026, 10, 15, 14, 1, tzinfo=UTC),
    )

    assert len(capture["data"]["equity_data_refs"]) == 4
    assert len(resolved["minute_bars"]) == 60
    assert len(resolved["historical_5m_bars"]) == 2
    assert len(resolved["quotes"]) == 2
    assert resolved["provider_raw"] == {}
    assert projection["source"]["content_sha256"] == capture["content_sha256"]
    assert projection["status"] in {"ok", "partial", "stale"}
    assert set(projection["symbols"]) == {"AAA", "SPY"}
    assert events_result["capabilities"]
    assert any(key.startswith("intraday-equities:AAA:") for key in events_result["capabilities"])


def test_read_only_loader_does_not_write_sidecar_or_embed_resolved_rows(tmp_path: Path) -> None:
    capture, bundles = _compact_capture()
    path = tmp_path / "collector.sqlite"
    _insert_capture_database(path, capture, bundles)
    before_counts: dict[str, int]
    with sqlite3.connect(path) as connection:
        before_counts = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "equity_data_watermarks",
                "equity_bar_revisions",
                "equity_quote_batches",
                "raw_captures",
                "scheduled_captures",
            )
        }
        raw_before = connection.execute(
            "SELECT payload_json FROM raw_captures WHERE capture_id = ?",
            (capture["capture_id"],),
        ).fetchone()[0]
    size_before = path.stat().st_size

    from app.api.intraday import _load_latest_capture

    loaded, error = _load_latest_capture(str(path))

    with sqlite3.connect(path) as connection:
        after_counts = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in before_counts
        }
        raw_after = connection.execute(
            "SELECT payload_json FROM raw_captures WHERE capture_id = ?",
            (capture["capture_id"],),
        ).fetchone()[0]

    assert error is None
    assert loaded is not None
    assert loaded["content_sha256"] == capture["content_sha256"]
    assert "_resolved_equity_refs" in loaded
    assert before_counts == after_counts
    assert raw_before == raw_after
    assert path.stat().st_size == size_before
    assert "_resolved_equity_refs" not in json.loads(raw_after)


def test_missing_or_hash_modified_ref_fails_closed_without_using_newer_revision(
    tmp_path: Path,
) -> None:
    capture, bundles = _compact_capture()
    path = tmp_path / "collector.sqlite"
    _insert_capture_database(path, capture, bundles)

    # A newer row exists, but the frozen ref's watermark must not silently fall
    # forward to it when the captured row is missing.
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO equity_data_watermarks VALUES (?, ?, ?, ?)",
            (10_000, "2026-10-15T14:00:02+00:00", "bar_revision", _TRADE_DATE.isoformat()),
    )
    replacement = _minute_bar("AAA", 0, close_offset=9.0)
    connection.execute(
        "INSERT INTO equity_bar_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            10_000,
            "moomoo_openapi",
            "AAA",
            "1m",
            "none",
            replacement["bar_end_at"],
            _TRADE_DATE.isoformat(),
            json.dumps(replacement, sort_keys=True, separators=(",", ":")),
            content_sha256(replacement),
            "2026-10-15T14:00:02+00:00",
            "newer-capture",
        ),
    )
    connection.execute(
        "DELETE FROM equity_bar_revisions WHERE watermark = 1"
    )
    connection.commit()
    connection.close()

    with pytest.raises(ValueError, match="missing|expired|row count|引用"):
        hydrate_capture_from_path(path, capture)

    # Rebuild a clean sidecar and corrupt the stored canonical hash instead.
    path.unlink()
    _insert_capture_database(path, capture, bundles)
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE equity_bar_revisions SET content_sha256 = ? WHERE watermark = 1",
        ("0" * 64,),
    )
    connection.commit()
    connection.close()
    with pytest.raises(ValueError, match="hash|哈希"):
        hydrate_capture_from_path(path, capture)


def test_history_uses_prior_sessions_for_macd_and_rvol_but_excludes_current_day_cache_rows() -> None:
    current, historical = _session_bars_for_history()
    enriched = enrich_indicator_history(
        current,
        historical,
        symbol="AAA",
        trading_date=_TRADE_DATE,
        cutoff=_CUTOFF,
    )
    features = enriched["features"]
    context = features["rvol_context"]
    current_times = {row["bar_end_at"] for row in current}
    macd_points = [
        point
        for series in features["macd"]["series"]
        if str(series.get("series_id")) == "macd_histogram"
        for point in series["points"]
    ]
    expected_prior_volumes = [100.0 + index for index in range(20)]
    expected_baseline = sum(sorted(expected_prior_volumes)[9:11]) / 2

    assert context["valid_days"] == 20
    assert context["as_of_session_date"] is not None
    assert context["as_of_session_date"] < _TRADE_DATE.isoformat()
    assert context["baseline_median_volume"] == pytest.approx(expected_baseline)
    assert features["rvol"] == pytest.approx(current[-1]["volume"] / expected_baseline)
    assert features["macd_warmup_bars"] == 20 * 78 + len(current)
    assert features["macd_signal_eligible"] is True
    assert macd_points
    assert {point["time"] for point in macd_points}.issubset(current_times)


def test_compact_capture_rejects_inline_duplicate_data_instead_of_merging_unknown_rows() -> None:
    capture, bundles = _compact_capture()
    capture["data"]["minute_bars"] = [_minute_bar("AAA", 0)]
    capture["content_sha256"] = content_sha256(capture["data"])
    with pytest.raises(ValueError, match="duplicate|重复|inline"):
        resolved_capture_data({**capture, "_resolved_equity_refs": bundles})
