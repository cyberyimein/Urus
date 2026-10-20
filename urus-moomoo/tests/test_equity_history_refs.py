from datetime import UTC, datetime, timedelta
from pathlib import Path
import gc
import json

from urus_moomoo.storage.sqlite import CollectorStore


_TRADE_DATE = "2026-10-15"
_OPEN = datetime(2026, 10, 15, 13, 30, tzinfo=UTC)


def _bar(minute_index: int, *, close_delta: float = 0.0) -> dict[str, object]:
    start = _OPEN + timedelta(minutes=minute_index)
    end = start + timedelta(minutes=1)
    close = 100.0 + minute_index + close_delta
    return {
        "symbol": "AAA",
        "bar_time": start.isoformat(),
        "bar_start_at": start.isoformat(),
        "bar_end_at": end.isoformat(),
        "open": close - 0.1,
        "high": close + 0.2,
        "low": close - 0.2,
        "close": close,
        "volume": 10.0 + minute_index,
        "turnover": close * (10.0 + minute_index),
        "vwap": close,
        "provider": "moomoo_openapi",
        "quality_state": "ready",
    }


def _bar_ref(store: CollectorStore, watermark: int) -> dict[str, object]:
    return store.equity_history.make_bar_ref(
        provider="moomoo_openapi",
        symbols=["AAA"],
        timeframe="1m",
        adjustment="none",
        from_time=_OPEN,
        through_time=_OPEN + timedelta(minutes=5),
        asof_revision_watermark=watermark,
        capture_trading_date=_TRADE_DATE,
    )


def _cleanup_store(path: Path) -> CollectorStore:
    sessions = ["2026-10-14", _TRADE_DATE]
    return CollectorStore(
        path,
        session_dates_provider=lambda current, count: [
            trading_date for trading_date in sessions if trading_date <= current
        ][-count:],
    )


def _five_minute_ref(store: CollectorStore) -> dict[str, object]:
    store.equity_history.upsert_bars(
        [_bar(index) for index in range(5)],
        timeframe="1m",
        trading_date=_TRADE_DATE,
        source_capture_id="minute-capture",
    )
    aggregate = store.equity_history.aggregate_5m_from_1m(
        symbols=["AAA"],
        trading_date=_TRADE_DATE,
        source_capture_id="five-minute-capture",
        market_timezone="America/New_York",
    )
    return store.equity_history.make_bar_ref(
        provider="moomoo_openapi",
        symbols=["AAA"],
        timeframe="5m",
        adjustment="none",
        from_time=_OPEN,
        through_time=_OPEN + timedelta(minutes=5),
        asof_revision_watermark=aggregate["watermark"],
        capture_trading_date=_TRADE_DATE,
    )


def test_identical_bars_do_not_add_revisions_and_old_refs_restore_prior_values(
    tmp_path: Path,
) -> None:
    store = CollectorStore(tmp_path / "collector.sqlite")
    try:
        first = store.equity_history.upsert_bars(
            [_bar(0)],
            timeframe="1m",
            trading_date=_TRADE_DATE,
            source_capture_id="capture-first",
        )
        original_watermark = first["watermark"]
        original_ref = _bar_ref(store, original_watermark)

        unchanged = store.equity_history.upsert_bars(
            [_bar(0)],
            timeframe="1m",
            trading_date=_TRADE_DATE,
            source_capture_id="capture-repeat",
        )
        revised = store.equity_history.upsert_bars(
            [_bar(0, close_delta=0.25)],
            timeframe="1m",
            trading_date=_TRADE_DATE,
            source_capture_id="capture-correction",
        )
        current_ref = _bar_ref(store, revised["watermark"])
        reverted = store.equity_history.upsert_bars(
            [_bar(0)],
            timeframe="1m",
            trading_date=_TRADE_DATE,
            source_capture_id="capture-revert",
        )
        reverted_ref = _bar_ref(store, reverted["watermark"])

        original = store.equity_history.resolve_ref(original_ref)
        current = store.equity_history.resolve_ref(current_ref)
        restored = store.equity_history.resolve_ref(reverted_ref)
        revision_count = store.connection.execute(
            "SELECT COUNT(*) FROM equity_bar_revisions "
            "WHERE symbol = 'AAA' AND timeframe = '1m'"
        ).fetchone()[0]
    finally:
        store.close()

    assert first["inserted"] == 1
    assert unchanged["unchanged"] == 1
    assert unchanged["watermark"] == original_watermark
    assert revised["revised"] == 1
    assert reverted["revised"] == 1
    assert revision_count == 3
    assert original["available"] is True
    assert current["available"] is True
    assert restored["available"] is True
    assert original["rows"][0]["close"] == _bar(0)["close"]
    assert current["rows"][0]["close"] == _bar(0, close_delta=0.25)["close"]
    assert restored["rows"][0]["close"] == _bar(0)["close"]


def test_five_minute_materialization_requires_five_complete_one_minute_bars(
    tmp_path: Path,
) -> None:
    store = CollectorStore(tmp_path / "collector.sqlite")
    try:
        store.equity_history.upsert_bars(
            [_bar(index) for index in range(4)],
            timeframe="1m",
            trading_date=_TRADE_DATE,
            source_capture_id="four-minute-capture",
        )
        partial = store.equity_history.aggregate_5m_from_1m(
            symbols=["AAA"],
            trading_date=_TRADE_DATE,
            source_capture_id="aggregate-partial",
            market_timezone="America/New_York",
        )
        store.equity_history.upsert_bars(
            [_bar(4)],
            timeframe="1m",
            trading_date=_TRADE_DATE,
            source_capture_id="fifth-minute-capture",
        )
        complete = store.equity_history.aggregate_5m_from_1m(
            symbols=["AAA"],
            trading_date=_TRADE_DATE,
            source_capture_id="aggregate-complete",
            market_timezone="America/New_York",
        )
        rows = store.connection.execute(
            "SELECT bar_json FROM equity_bar_revisions WHERE timeframe = '5m' "
            "ORDER BY watermark DESC LIMIT 1"
        ).fetchone()
    finally:
        store.close()

    assert partial["inserted"] == 0
    assert complete["inserted"] == 1
    bar = json.loads(rows["bar_json"])
    assert bar["bar_end_at"] == "2026-10-15T13:35:00.000000Z"
    assert bar["open"] == _bar(0)["open"]
    assert bar["high"] == max(_bar(index)["high"] for index in range(5))
    assert bar["low"] == min(_bar(index)["low"] for index in range(5))
    assert bar["close"] == _bar(4)["close"]
    assert bar["volume"] == sum(_bar(index)["volume"] for index in range(5))


def test_quote_batch_is_immutable_idempotent_and_resolvable_by_ref(tmp_path: Path) -> None:
    store = CollectorStore(tmp_path / "collector.sqlite")
    quotes = [
        {
            "symbol": "AAA",
            "quote_code": "US.AAA",
            "last_price": 101.5,
            "received_at": "2026-10-15T09:35:03-04:00",
        },
        {"symbol": "BBB", "quote_code": "US.BBB", "last_price": 88.0},
    ]
    args = {
        "task_name": "equity-intraday",
        "scheduled_at": datetime.fromisoformat("2026-10-15T09:35:00-04:00"),
        "trading_date": _TRADE_DATE,
        "capture_id": "g1-capture",
        "captured_at": datetime.fromisoformat("2026-10-15T09:35:02-04:00"),
        "quotes": quotes,
    }
    try:
        first_ref = store.equity_history.write_quote_batch(**args)
        retry_args = {
            **args,
            "captured_at": args["captured_at"] + timedelta(seconds=1),
        }
        second_ref = store.equity_history.write_quote_batch(**retry_args)
        result = store.equity_history.resolve_ref(first_ref)
        batch_count = store.connection.execute(
            "SELECT COUNT(*) FROM equity_quote_batches"
        ).fetchone()[0]
    finally:
        store.close()

    assert first_ref == second_ref
    assert first_ref["kind"] == "quotes"
    assert first_ref["timeframe"] == "snapshot"
    assert result["available"] is True
    assert [row["symbol"] for row in result["rows"]] == ["AAA", "BBB"]
    assert result["rows"][0]["received_at"] == "2026-10-15T13:35:03.000000Z"
    assert result["rows"][1]["received_at"] == "2026-10-15T13:35:02.000000Z"
    assert batch_count == 1


def test_ref_digest_mismatch_is_reported_as_unavailable(tmp_path: Path) -> None:
    store = CollectorStore(tmp_path / "collector.sqlite")
    try:
        status = store.equity_history.upsert_bars(
            [_bar(0)],
            timeframe="1m",
            trading_date=_TRADE_DATE,
            source_capture_id="capture-first",
        )
        ref = _bar_ref(store, status["watermark"])
        ref["content_sha256"] = "0" * 64

        result = store.equity_history.resolve_ref(ref)
    finally:
        store.close()

    assert result["available"] is False
    assert result["reason"] == "ref_digest_mismatch"
    assert result["rows"] == []


def test_capacity_measure_reports_equity_logical_bytes_and_database_wal_sizes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "collector.sqlite"
    store = CollectorStore(path)
    try:
        store.equity_history.upsert_bars(
            [_bar(0)],
            timeframe="1m",
            trading_date=_TRADE_DATE,
            source_capture_id="capture-first",
        )
        capacity = store.equity_history.measure_capacity()
        policy = store.equity_history.capacity_policy(
            capacity,
            metrics_budget_bytes=100,
            raw_budget_bytes=10_000,
            total_budget_bytes=500,
            warning_ratio=0.5,
        )
    finally:
        store.close()

    assert capacity["equity_1m_bytes"] > 0
    assert capacity["equity_5m_bytes"] == 0
    assert capacity["equity_logical_bytes"] >= capacity["equity_1m_bytes"]
    assert capacity["main_database_bytes"] == path.stat().st_size
    assert capacity["wal_bytes"] >= 0
    assert policy["defer_warmup"] is True
    assert policy["drop_d_symbols"] is True
    assert policy["skip_all_equities"] is False


def test_cleanup_preview_protects_active_refs_and_never_selects_legacy_raw_or_eof_data(
    tmp_path: Path,
) -> None:
    trade_dates = [
        f"2026-09-{day:02d}" for day in range(1, 31)
    ] + ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07"]
    sessions = set(trade_dates)
    store = CollectorStore(
        tmp_path / "collector.sqlite",
        session_dates_provider=lambda current, count: sorted(
            session_date for session_date in sessions if session_date <= current
        )[-count:],
    )
    try:
        for index, trading_date in enumerate(trade_dates):
            store.equity_history.record_trading_session(trading_date)
            bar = _bar(0, close_delta=float(index))
            local_start = datetime.fromisoformat(f"{trading_date}T13:30:00+00:00")
            bar.update(
                {
                    "bar_time": local_start.isoformat(),
                    "bar_start_at": local_start.isoformat(),
                    "bar_end_at": (local_start + timedelta(minutes=1)).isoformat(),
                }
            )
            store.equity_history.upsert_bars(
                [bar],
                timeframe="1m",
                trading_date=trading_date,
                source_capture_id=f"capture-{trading_date}",
            )
            five_minute_bar = {
                **bar,
                "bar_end_at": (local_start + timedelta(minutes=5)).isoformat(),
            }
            store.equity_history.upsert_bars(
                [five_minute_bar],
                timeframe="5m",
                trading_date=trading_date,
                source_capture_id=f"capture-{trading_date}",
            )

        oldest_date = trade_dates[0]
        active_bar_end = datetime.fromisoformat(f"{oldest_date}T13:31:00+00:00")
        active_watermark = int(
            store.connection.execute(
                "SELECT watermark FROM equity_bar_revisions "
                "WHERE timeframe = '1m' AND trading_date = ?",
                (oldest_date,),
            ).fetchone()[0]
        )
        active_ref = store.equity_history.make_bar_ref(
            provider="moomoo_openapi",
            symbols=["AAA"],
            timeframe="1m",
            adjustment="none",
            from_time=active_bar_end - timedelta(minutes=1),
            through_time=active_bar_end,
            asof_revision_watermark=active_watermark,
            capture_trading_date="2026-10-07",
        )
        store.equity_history.register_capture_refs(
            "active-current-slot",
            "2026-10-07",
            [active_ref],
        )
        store.write_raw(
            "expired-refs-v1-capture",
            "capture_intraday_equities",
            {
                "data": {
                    "schema_version": "moomoo-intraday-equities-refs-v1",
                    "trading_date": oldest_date,
                    "equity_data_refs": [],
                }
            },
            schema_version="urus.moomoo.capture.v2",
        )
        store.write_scheduled_raw(
            "equity-intraday",
            datetime.fromisoformat(f"{oldest_date}T13:35:00+00:00"),
            "expired-refs-v1-capture",
            "capture_intraday_equities",
            {
                "data": {
                    "schema_version": "moomoo-intraday-equities-refs-v1",
                    "trading_date": oldest_date,
                    "equity_data_refs": [],
                }
            },
            schema_version="urus.moomoo.capture.v2",
            completed=True,
        )
        store.write_raw(
            "legacy-equity-capture",
            "capture_intraday_equities",
            {"data": {"schema_version": "moomoo-intraday-equities-v1"}},
            schema_version="urus.moomoo.capture.v2",
        )
        store.write_raw(
            "legacy-options-capture",
            "capture_intraday",
            {"data": {"schema_version": "intraday-option-capture-v2"}},
            schema_version="urus.moomoo.capture.v2",
        )

        preview = store.equity_history.cleanup_expired(
            "2026-10-07",
            dry_run=True,
        )
        old_row_watermarks = [
            int(row[0])
            for row in store.connection.execute(
                "SELECT watermark FROM equity_bar_revisions WHERE trading_date = ?",
                (oldest_date,),
            ).fetchall()
        ]
        old_raw = store.read_raw("legacy-equity-capture")
        options_raw = store.read_raw("legacy-options-capture")
        expired_feature_raw = store.read_raw("expired-refs-v1-capture")
        active_ref_result = store.equity_history.resolve_ref(active_ref)
        cleanup_result = store.equity_history.apply_cleanup(preview)
        expired_feature_raw_after = store.read_raw("expired-refs-v1-capture")
        expired_scheduled_raw = store.read_scheduled_raw(
            "equity-intraday", datetime.fromisoformat(f"{oldest_date}T13:35:00+00:00")
        )
        protected_bar_after = store.equity_history.resolve_ref(active_ref)
    finally:
        store.close()

    assert preview["dry_run"] is True
    assert "legacy-equity-capture" not in preview["expired_raw_capture_ids"]
    assert "legacy-options-capture" not in preview["expired_raw_capture_ids"]
    assert preview["dry_run"] is True
    assert "expired-refs-v1-capture" in preview["expired_raw_capture_ids"]
    assert old_row_watermarks
    assert old_raw is not None
    assert options_raw is not None
    assert expired_feature_raw is not None
    assert active_ref_result["available"] is True
    assert protected_bar_after["available"] is True
    assert expired_feature_raw_after is None
    assert expired_scheduled_raw is None
    assert cleanup_result["dry_run"] is False
    assert cleanup_result["deleted"]["raw_captures"] == 1


def test_cleanup_plan_reads_a_duplicate_active_bar_ref_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = _cleanup_store(tmp_path / "collector.sqlite")
    try:
        reference = _five_minute_ref(store)
        store.equity_history.register_capture_refs(
            "g2-shard-capture",
            _TRADE_DATE,
            [reference],
        )
        store.equity_history.register_capture_refs(
            "slot-finalizer-capture",
            _TRADE_DATE,
            [reference],
        )

        read_bar_rows = store.equity_history._read_bar_rows
        calls = 0

        def count_bar_reads(**kwargs):
            nonlocal calls
            calls += 1
            return read_bar_rows(**kwargs)

        monkeypatch.setattr(store.equity_history, "_read_bar_rows", count_bar_reads)
        plan = store.equity_history.cleanup_expired(_TRADE_DATE, dry_run=True)
        protected_watermark = int(
            store.connection.execute(
                "SELECT watermark FROM equity_bar_revisions WHERE timeframe = '5m'"
            ).fetchone()["watermark"]
        )
        cleanup_result = store.equity_history.apply_cleanup(plan)
    finally:
        store.close()

    assert plan["active_ref_count"] == 2
    assert plan["blocked"] is False
    assert protected_watermark in plan["protected_bar_watermarks"]
    assert cleanup_result["dry_run"] is False
    assert calls == 2


def test_cleanup_plan_releases_bar_watermark_sequences_after_protection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = _cleanup_store(tmp_path / "collector.sqlite")
    released: list[int] = []
    watermark_count = 64

    class LargeWatermark:
        def __init__(self, value: int) -> None:
            self.value = value
            self.payload = bytearray(1024)

        def __int__(self) -> int:
            return self.value

        def __del__(self) -> None:
            released.append(self.value)

    try:
        reference = _five_minute_ref(store)
        store.equity_history.register_capture_refs(
            "g2-shard-capture",
            _TRADE_DATE,
            [reference],
        )
        store.equity_history.register_capture_refs(
            "slot-finalizer-capture",
            _TRADE_DATE,
            [reference],
        )

        def resolve_with_large_watermarks(_reference):
            return {
                "available": True,
                "reason": None,
                "rows": [],
                "version_watermarks": [
                    LargeWatermark(value) for value in range(watermark_count)
                ],
            }

        monkeypatch.setattr(
            store.equity_history,
            "_resolve_bar_ref_details",
            resolve_with_large_watermarks,
        )
        original_measure_capacity = store.equity_history.measure_capacity

        def measure_after_active_ref_validation():
            gc.collect()
            assert len(released) == watermark_count
            return original_measure_capacity()

        monkeypatch.setattr(
            store.equity_history,
            "measure_capacity",
            measure_after_active_ref_validation,
        )
        plan = store.equity_history.cleanup_expired(_TRADE_DATE, dry_run=True)
    finally:
        store.close()

    assert plan["blocked"] is False
    assert plan["protected_bar_watermarks"] == list(range(watermark_count))


def test_cleanup_plan_blocks_a_corrupted_duplicate_active_descriptor(
    tmp_path: Path,
) -> None:
    store = _cleanup_store(tmp_path / "collector.sqlite")
    try:
        reference = _five_minute_ref(store)
        store.equity_history.register_capture_refs(
            "g2-shard-capture",
            _TRADE_DATE,
            [reference],
        )
        store.equity_history.register_capture_refs(
            "slot-finalizer-capture",
            _TRADE_DATE,
            [reference],
        )
        corrupted = dict(reference)
        corrupted["through"] = (_OPEN + timedelta(minutes=10)).isoformat()
        with store.connection:
            store.connection.execute(
                "UPDATE equity_capture_refs SET ref_json = ? "
                "WHERE capture_id = ? AND ref_id = ?",
                (
                    json.dumps(corrupted, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    "slot-finalizer-capture",
                    reference["ref_id"],
                ),
            )

        plan = store.equity_history.cleanup_expired(_TRADE_DATE, dry_run=True)
    finally:
        store.close()

    assert plan["blocked"] is True
    assert len(plan["invalid_active_refs"]) == 1
    invalid_reference = plan["invalid_active_refs"][0]
    assert invalid_reference["capture_id"] == "slot-finalizer-capture"
    assert invalid_reference["ref_id"] == reference["ref_id"]
    assert invalid_reference["reason"] in {
        "descriptor_identity_mismatch",
        "ref_id_mismatch",
    }
    assert all(not values for values in plan["candidates"].values())


def test_cleanup_plan_blocks_an_active_ref_when_stored_bar_is_corrupted(
    tmp_path: Path,
) -> None:
    store = _cleanup_store(tmp_path / "collector.sqlite")
    try:
        write = store.equity_history.upsert_bars(
            [_bar(0)],
            timeframe="1m",
            trading_date=_TRADE_DATE,
            source_capture_id="capture-first",
        )
        reference = _bar_ref(store, write["watermark"])
        store.equity_history.register_capture_refs(
            "active-current-slot",
            _TRADE_DATE,
            [reference],
        )
        row = store.connection.execute(
            "SELECT bar_json FROM equity_bar_revisions WHERE watermark = ?",
            (write["watermark"],),
        ).fetchone()
        corrupted_bar = json.loads(str(row["bar_json"]))
        corrupted_bar["close"] += 1.0
        with store.connection:
            store.connection.execute(
                "UPDATE equity_bar_revisions SET bar_json = ? WHERE watermark = ?",
                (
                    json.dumps(corrupted_bar, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    write["watermark"],
                ),
            )

        plan = store.equity_history.cleanup_expired(_TRADE_DATE, dry_run=True)
    finally:
        store.close()

    assert plan["blocked"] is True
    assert plan["invalid_active_refs"][0]["reason"] == "stored_bar_digest_mismatch"
    assert all(not values for values in plan["candidates"].values())
