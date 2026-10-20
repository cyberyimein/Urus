from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from urus_moomoo.config import CollectionTask, CollectorConfig
from urus_moomoo.runtime import CollectorRuntime
from urus_moomoo.storage.equity_history import DEFAULT_RAW_BUDGET_BYTES


_CUTOFF = datetime.fromisoformat("2026-10-15T09:35:00-04:00")
_SYMBOLS = ("SPY", "SMH", "AAA")
_SHARDS = {"G1": ["SPY"], "G2": ["SMH"], "G3": ["AAA"]}


class _Provider:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def reset_collection_run(self) -> None:
        return None

    def capture_intraday_equities(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        symbols = list(kwargs.get("minute_symbols", []))
        quotes = [
            {
                "symbol": symbol,
                "quote_code": f"US.{symbol}",
                "last_price": 100.0,
            }
            for symbol in _SYMBOLS
        ] if kwargs.get("capture_quotes") else []
        bars = []
        for symbol in symbols:
            for minute in range(5):
                start = datetime(2026, 10, 15, 13, 30, tzinfo=UTC) + timedelta(minutes=minute)
                close = 100.0 + minute
                bars.append(
                    {
                        "symbol": symbol,
                        "bar_start_at": start.isoformat(),
                        "bar_end_at": (start + timedelta(minutes=1)).isoformat(),
                        "open": close - 0.1,
                        "high": close + 0.2,
                        "low": close - 0.2,
                        "close": close,
                        "volume": 10.0,
                        "turnover": close * 10.0,
                        "vwap": close,
                        "provider": "moomoo_openapi",
                        "quality_state": "ready",
                    }
                )
        return {
            "schema_version": "moomoo-intraday-equities-v1",
            "provider": "moomoo_openapi",
            "captured_at": "2026-10-15T13:36:00+00:00",
            "trading_date": "2026-10-15",
            "scheduled_at": _CUTOFF.isoformat(),
            "evidence_cutoff": _CUTOFF.isoformat(),
            "slot_index": 0,
            "shard_id": kwargs.get("shard_id"),
            "requested_symbols": list(_SYMBOLS),
            "quote_symbols": list(_SYMBOLS),
            "minute_symbols": symbols,
            "quotes": quotes,
            "unavailable_quote_symbols": [],
            "minute_bars": bars,
            "invalid_minute_bars": [],
            "minute_bar_unavailable_symbols": [],
            "quality_summary": {
                "complete": True,
                "status": "ok",
                "quote_coverage": 1.0 if kwargs.get("capture_quotes") else 0.0,
                "minute_symbols_requested": len(symbols),
                "minute_symbols_admitted": len(symbols),
                "minute_bars_returned": len(bars),
                "invalid_minute_bars": 0,
                "unavailable_quote_symbols": [],
                "unavailable_minute_symbols": [],
                "quality_warnings": [],
            },
            "provider_raw": {
                "market_snapshot_batches": [
                    {
                        "requested_codes": [f"US.{symbol}" for symbol in _SYMBOLS],
                        "response_received_at": "2026-10-15T13:35:10+00:00",
                        "records": [{"code": f"US.{symbol}"} for symbol in _SYMBOLS],
                    }
                ] if kwargs.get("capture_quotes") else [],
                "history_kline_batches": [
                    {"symbol": symbol, "response_received_at": "2026-10-15T13:35:11+00:00"}
                    for symbol in symbols
                ],
            },
        }


def _runtime(tmp_path: Path, provider: _Provider) -> CollectorRuntime:
    task = CollectionTask(
        "equity-intraday",
        True,
        "intraday",
        _SYMBOLS,
        "capture_intraday_equities",
        params={"equity_shards_enabled": True},
    )
    config = CollectorConfig(
        timezone="America/New_York",
        market_timezone="America/New_York",
        output_dir=tmp_path,
        tasks=(task,),
        lock_path=tmp_path / "collection.lock",
        sqlite_path=tmp_path / "collector.sqlite",
    )
    return CollectorRuntime(
        provider,
        config,
        clock=lambda: datetime(2026, 10, 15, 13, 37, tzinfo=UTC),
    )


def _shard_task(shard_id: str) -> CollectionTask:
    return CollectionTask(
        f"equity-intraday:{shard_id}",
        True,
        "intraday",
        _SYMBOLS,
        "capture_intraday_equities",
        params={
            "windowed_equity_shard": True,
            "source_task_name": "equity-intraday",
            "shard_id": shard_id,
            "shard_symbols": _SHARDS[shard_id],
            "equity_shard_symbols": _SHARDS,
            "slot_cutoff_at": _CUTOFF.isoformat(),
            "request_budget_deadline_at": (_CUTOFF + timedelta(minutes=5)).isoformat(),
            "capture_quotes": shard_id == "G1",
            "slot_index": 0,
        },
    )


def _finalizer_task() -> CollectionTask:
    return CollectionTask(
        "equity-intraday:finalize",
        True,
        "intraday",
        _SYMBOLS,
        "finalize_intraday_equities",
        params={
            "source_task_name": "equity-intraday",
            "equity_shard_symbols": _SHARDS,
            "slot_cutoff_at": _CUTOFF.isoformat(),
            "slot_index": 0,
        },
    )


def test_windowed_raw_contracts_store_refs_without_inline_provider_rows(tmp_path: Path) -> None:
    provider = _Provider()
    runtime = _runtime(tmp_path, provider)
    try:
        for shard_id, offset in (("G1", 60), ("G2", 120), ("G3", 180)):
            runtime.collect_task(_shard_task(shard_id), _CUTOFF + timedelta(seconds=offset))
        result = runtime.collect_task(_finalizer_task(), _CUTOFF + timedelta(seconds=240))
        data = result["contract"]["data"]
        refs = data["equity_data_refs"]
        resolved = [runtime.store.equity_history.resolve_ref(ref) for ref in refs]
        saved = runtime.store.read_scheduled_raw("equity-intraday", _CUTOFF)
        ref_rows = runtime.store.connection.execute(
            "SELECT COUNT(*) FROM equity_capture_refs"
        ).fetchone()[0]
        stored_rows = runtime.store.connection.execute(
            "SELECT COUNT(*) FROM equity_bar_revisions"
        ).fetchone()[0]
    finally:
        runtime.close()

    assert result["completed"] is True
    assert data["schema_version"] == "moomoo-intraday-equities-refs-v1"
    assert "minute_bars" not in data
    assert "quotes" not in data
    assert "provider_raw" not in data
    assert len(refs) == 7
    assert all(item["available"] for item in resolved)
    assert any(item["rows"][0].get("received_at") == "2026-10-15T13:35:10.000000Z" for item in resolved)
    assert saved is not None
    assert ref_rows > len(refs)
    assert stored_rows == 18


def test_hard_equity_capacity_gate_skips_provider_and_equity_writes(tmp_path: Path) -> None:
    provider = _Provider()
    runtime = _runtime(tmp_path, provider)
    try:
        runtime.store.equity_history.measure_capacity = lambda: {
            "equity_1m_bytes": 0,
            "equity_5m_bytes": 0,
            "equity_quote_bytes": 0,
            "equity_ref_bytes": 0,
            "equity_refs_v1_raw_bytes": 0,
            "equity_logical_bytes": 1024 * 1024 * 1024,
            "main_database_bytes": 10_000,
            "wal_bytes": 0,
        }
        result = runtime.collect_task(
            _shard_task("G2"),
            _CUTOFF + timedelta(seconds=120),
        )
        capture_count = runtime.store.connection.execute(
            "SELECT COUNT(*) FROM raw_captures WHERE kind = 'capture_intraday_equities'"
        ).fetchone()[0]
        bar_count = runtime.store.connection.execute(
            "SELECT COUNT(*) FROM equity_bar_revisions"
        ).fetchone()[0]
    finally:
        runtime.close()

    assert provider.calls == []
    assert result["completed"] is True
    assert result["skipped_capacity"] is True
    assert result["maintenance_pending"] is True
    assert result.get("contract") is None
    assert capture_count == 0
    assert bar_count == 0


def test_near_capacity_gate_defers_d_minutes_and_keeps_a_core_workflow(tmp_path: Path) -> None:
    provider = _Provider()
    runtime = _runtime(tmp_path, provider)
    task = _shard_task("G2")
    task.params["groups"] = {"A": ["SPY"], "D": ["SMH", "AAA"]}
    try:
        runtime.store.equity_history.measure_capacity = lambda: {
            "equity_1m_bytes": DEFAULT_RAW_BUDGET_BYTES * 0.95,
            "equity_5m_bytes": 0,
            "equity_quote_bytes": 0,
            "equity_ref_bytes": 0,
            "equity_refs_v1_raw_bytes": 0,
            "equity_logical_bytes": DEFAULT_RAW_BUDGET_BYTES * 0.95,
            "main_database_bytes": 10_000,
            "wal_bytes": 0,
        }
        result = runtime.collect_task(task, _CUTOFF + timedelta(seconds=120))
        data = result["contract"]["data"]
    finally:
        runtime.close()

    assert provider.calls == []
    assert result["completed"] is True
    assert data["slot_shard_status"] == "deferred_capacity"
    assert data["minute_bar_unavailable_symbols"] == ["SMH"]
    assert data["storage_capacity_policy"]["drop_d_symbols"] is True
    assert data["maintenance_pending"] is True
    assert "minute_bars" not in data


def test_equity_retention_is_opt_in_and_runs_once_per_trading_session(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, _Provider())
    dry_runs: list[str] = []
    applied_plans: list[dict[str, Any]] = []
    try:
        runtime.store.equity_history.cleanup_expired = lambda current_date, dry_run=True: (
            dry_runs.append(current_date)
            or {
                "dry_run": True,
                "blocked": False,
                "current_trading_date": current_date,
                "plan_sha256": "test-plan",
            }
        )
        runtime.store.equity_history.apply_cleanup = lambda plan: (
            applied_plans.append(dict(plan))
            or {"dry_run": False, "deleted": {}}
        )

        disabled = runtime._apply_equity_retention_once(_CUTOFF.date().isoformat(), enabled=False)
        first = runtime._apply_equity_retention_once(_CUTOFF.date().isoformat(), enabled=True)
        second = runtime._apply_equity_retention_once(_CUTOFF.date().isoformat(), enabled=True)
        persisted_date = runtime.store.get_meta("equity_retention_last_applied_date")
    finally:
        runtime.close()

    assert disabled is None
    assert first["applied"] is True
    assert second["applied"] is False
    assert dry_runs == [_CUTOFF.date().isoformat()]
    assert len(applied_plans) == 1
    assert persisted_date == _CUTOFF.date().isoformat()


def test_equity_capture_does_not_run_maintenance_synchronously(
    tmp_path: Path,
) -> None:
    provider = _Provider()
    runtime = _runtime(tmp_path, provider)
    task = _shard_task("G2")
    task.params["equity_retention_apply"] = True
    maintenance_calls: list[str] = []

    def fail_if_called(*_args, **_kwargs):
        maintenance_calls.append("unexpected")
        return _raise_retention_error()

    try:
        runtime._apply_equity_retention_once = fail_if_called
        result = runtime.collect_task(task, _CUTOFF + timedelta(seconds=120))
        data = result["contract"]["data"]
    finally:
        runtime.close()

    assert len(provider.calls) == 1
    assert maintenance_calls == []
    assert result["completed"] is True
    assert result["maintenance_pending"] is True
    assert data["maintenance_pending"] is True
    assert "storage_retention" not in data


def _raise_retention_error() -> None:
    raise RuntimeError("dry-run could not verify equity cleanup scope")
