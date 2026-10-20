from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from urus_moomoo.config import CollectionTask, CollectorConfig
from urus_moomoo.runtime import CollectorRuntime


_CUTOFF = datetime.fromisoformat("2026-10-15T09:35:00-04:00")
_SHARDS = {"G1": ["SPY", "QQQ"], "G2": ["SMH"], "G3": ["AAA"]}
_DISPLAY_GROUPS = {
    "SPY": "大盘基准",
    "QQQ": "大盘基准",
    "SMH": "半导体",
    "AAA": "航天",
}


class _EquityProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def reset_collection_run(self) -> None:
        return None

    def capture_intraday_equities(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        symbols = list(kwargs.get("minute_symbols", []))
        start = datetime.fromisoformat("2026-10-15T13:34:00+00:00")
        return {
            "schema_version": "moomoo-intraday-equities-v1",
            "provider": "moomoo_openapi",
            "captured_at": datetime.now().astimezone().isoformat(),
            "trading_date": _CUTOFF.date().isoformat(),
            "scheduled_at": _CUTOFF.isoformat(),
            "evidence_cutoff": _CUTOFF.isoformat(),
            "requested_symbols": ["SPY", "QQQ", "SMH", "AAA"],
            "quote_symbols": ["SPY", "QQQ", "SMH", "AAA"],
            "minute_symbols": symbols,
            "quotes": (
                [
                    {"symbol": symbol, "quote_code": f"US.{symbol}", "last_price": 100.0}
                    for symbol in _base_task().symbols
                ]
                if kwargs.get("capture_quotes")
                else []
            ),
            "minute_bars": [
                {
                    "symbol": symbol,
                    "bar_time": "2026-10-15 09:34:00",
                    "bar_start_at": start.isoformat(),
                    "bar_end_at": (start + timedelta(minutes=1)).isoformat(),
                    "open": 99.9,
                    "high": 100.2,
                    "low": 99.8,
                    "close": 100.0,
                    "volume": 10.0,
                    "turnover": 1000.0,
                    "vwap": 100.0,
                    "provider": "moomoo_openapi",
                    "quality_state": "ready",
                }
                for symbol in symbols
            ],
            "quality_summary": {
                "complete": True,
                "status": "ok",
                "minute_symbols_requested": len(symbols),
                "minute_bars_returned": len(symbols),
                "unavailable_minute_symbols": [],
            },
            "provider_raw": {"history_kline_batches": [], "market_snapshot_batches": []},
        }


def _base_task() -> CollectionTask:
    return CollectionTask(
        "equity-intraday",
        True,
        "intraday",
        ("SPY", "QQQ", "SMH", "AAA"),
        "capture_intraday_equities",
        params={
            "equity_shards_enabled": True,
            "pool_plan_id": "frozen-plan",
            "pool_revision": 7,
            "display_group_by_symbol": _DISPLAY_GROUPS,
        },
    )


def _shard_task(shard_id: str = "G2") -> CollectionTask:
    return CollectionTask(
        f"equity-intraday:{shard_id}",
        True,
        "intraday",
        _base_task().symbols,
        "capture_intraday_equities",
        params={
            "windowed_equity_shard": True,
            "source_task_name": "equity-intraday",
            "shard_id": shard_id,
            "shard_symbols": _SHARDS[shard_id],
            "equity_shard_symbols": _SHARDS,
            "slot_cutoff_at": _CUTOFF.isoformat(),
            "request_budget_deadline_at": (
                _CUTOFF + timedelta(seconds=120 + 45)
            ).isoformat(),
            "capture_quotes": shard_id == "G1",
            "slot_index": 0,
            "pool_plan_id": "frozen-plan",
            "pool_revision": 7,
            "display_group_by_symbol": _DISPLAY_GROUPS,
        },
    )


def _runtime(tmp_path: Path, provider: _EquityProvider) -> CollectorRuntime:
    base = _base_task()
    config = CollectorConfig(
        timezone="America/New_York",
        market_timezone="America/New_York",
        output_dir=tmp_path,
        tasks=(base,),
        lock_path=tmp_path / "collection.lock",
        sqlite_path=tmp_path / "collector.sqlite",
    )
    return CollectorRuntime(
        provider,
        config,
        clock=lambda: _CUTOFF + timedelta(seconds=61),
    )


def test_shard_capture_uses_the_shared_cutoff_and_only_its_minute_symbols(tmp_path: Path) -> None:
    provider = _EquityProvider()
    runtime = _runtime(tmp_path, provider)
    scheduled_at = _CUTOFF + timedelta(seconds=120)
    try:
        result = runtime.collect_task(_shard_task("G2"), scheduled_at)
    finally:
        runtime.close()

    assert result["completed"] is True
    assert result["terminal"] is True
    assert provider.calls[0]["scheduled_at"] == _CUTOFF
    assert provider.calls[0]["evidence_cutoff"] == _CUTOFF
    assert provider.calls[0]["minute_symbols"] == ["SMH"]
    assert provider.calls[0]["quote_symbols"] == ["SPY", "QQQ", "SMH", "AAA"]
    assert provider.calls[0]["capture_quotes"] is False
    assert result["contract"]["data"]["pool_plan_id"] == "frozen-plan"
    assert result["contract"]["data"]["display_group_by_symbol"] == _DISPLAY_GROUPS


def test_nonwindowed_capture_persists_frozen_pool_metadata(tmp_path: Path) -> None:
    provider = _EquityProvider()
    runtime = _runtime(tmp_path, provider)
    try:
        result = runtime.collect_task(_base_task(), _CUTOFF)
        stored = runtime.store.read_scheduled_raw("equity-intraday", _CUTOFF)
    finally:
        runtime.close()

    assert result["completed"] is True
    assert stored is not None
    data = stored["contract"]["data"]
    assert data["pool_plan_id"] == "frozen-plan"
    assert data["pool_revision"] == 7
    assert data["display_group_by_symbol"] == _DISPLAY_GROUPS


def test_same_slot_shard_is_idempotent_and_does_not_repeat_provider_call(tmp_path: Path) -> None:
    provider = _EquityProvider()
    runtime = _runtime(tmp_path, provider)
    task = _shard_task("G2")
    scheduled_at = _CUTOFF + timedelta(seconds=120)
    try:
        first = runtime.collect_task(task, scheduled_at)
        second = runtime.collect_task(task, scheduled_at + timedelta(seconds=1))
    finally:
        runtime.close()

    assert first["contract"]["capture_id"] == second["contract"]["capture_id"]
    assert provider.calls and len(provider.calls) == 1


def test_finalizer_merges_shards_under_one_immutable_slot_contract(tmp_path: Path) -> None:
    provider = _EquityProvider()
    runtime = _runtime(tmp_path, provider)
    try:
        for shard_id, offset in (("G1", 60), ("G2", 120), ("G3", 180)):
            runtime.collect_task(
                _shard_task(shard_id),
                _CUTOFF + timedelta(seconds=offset),
            )
        finalizer = CollectionTask(
            "equity-intraday:finalize",
            True,
            "intraday",
            _base_task().symbols,
            "finalize_intraday_equities",
            params={
                "equity_shard_finalizer": True,
                "source_task_name": "equity-intraday",
                "equity_shard_symbols": _SHARDS,
                "slot_cutoff_at": _CUTOFF.isoformat(),
                "slot_index": 0,
                "pool_plan_id": "frozen-plan",
                "pool_revision": 7,
                "display_group_by_symbol": _DISPLAY_GROUPS,
            },
        )

        result = runtime.collect_task(finalizer, _CUTOFF + timedelta(seconds=240))
        stored = runtime.store.read_scheduled_raw("equity-intraday", _CUTOFF)
        scheduled_rows = runtime.store.connection.execute(
            "SELECT task_name, scheduled_at FROM scheduled_captures "
            "WHERE kind = 'capture_intraday_equities'"
        ).fetchall()
    finally:
        runtime.close()

    assert result["completed"] is True
    assert result["terminal"] is True
    assert result["contract"]["task_name"] == "equity-intraday"
    assert result["contract"]["scheduled_at"] == _CUTOFF.isoformat()
    assert stored is not None
    assert stored["contract"]["data"]["slot_coverage"]["complete"] is True
    assert stored["contract"]["data"]["minute_symbols"] == ["SPY", "QQQ", "SMH", "AAA"]
    assert stored["contract"]["data"]["pool_plan_id"] == "frozen-plan"
    assert stored["contract"]["data"]["pool_revision"] == 7
    assert stored["contract"]["data"]["display_group_by_symbol"] == _DISPLAY_GROUPS
    assert len(provider.calls) == 3
    assert [(row["task_name"], row["scheduled_at"]) for row in scheduled_rows] == [
        ("equity-intraday", _CUTOFF.isoformat())
    ]


def test_finalizer_marks_missing_shard_partial_without_reusing_previous_slot(tmp_path: Path) -> None:
    provider = _EquityProvider()
    runtime = _runtime(tmp_path, provider)
    try:
        runtime.collect_task(_shard_task("G1"), _CUTOFF + timedelta(seconds=60))
        runtime.collect_task(_shard_task("G3"), _CUTOFF + timedelta(seconds=180))
        finalizer = CollectionTask(
            "equity-intraday:finalize",
            True,
            "intraday",
            _base_task().symbols,
            "finalize_intraday_equities",
            params={
                "equity_shard_finalizer": True,
                "source_task_name": "equity-intraday",
                "equity_shard_symbols": _SHARDS,
                "slot_cutoff_at": _CUTOFF.isoformat(),
                "slot_index": 0,
            },
        )

        result = runtime.collect_task(finalizer, _CUTOFF + timedelta(seconds=240))
    finally:
        runtime.close()

    data = result["contract"]["data"]
    assert result["completed"] is False
    assert result["terminal"] is True
    assert data["slot_coverage"]["missing_shards"] == ["G2"]
    assert data["slot_coverage"]["shards"]["G2"]["status"] == "skipped_window_expired"
    assert data["minute_bar_unavailable_symbols"] == ["SMH"]
    assert len(provider.calls) == 2


def test_runtime_restart_reuses_shards_and_t240_finalizer_never_calls_provider(
    tmp_path: Path,
) -> None:
    first_provider = _EquityProvider()
    first_runtime = _runtime(tmp_path, first_provider)
    try:
        for shard_id, offset in (("G1", 60), ("G2", 120), ("G3", 180)):
            first_runtime.collect_task(
                _shard_task(shard_id),
                _CUTOFF + timedelta(seconds=offset),
            )
    finally:
        first_runtime.close()

    restarted_provider = _EquityProvider()
    restarted_runtime = _runtime(tmp_path, restarted_provider)
    finalizer = CollectionTask(
        "equity-intraday:finalize",
        True,
        "intraday",
        _base_task().symbols,
        "finalize_intraday_equities",
        params={
            "equity_shard_finalizer": True,
            "source_task_name": "equity-intraday",
            "equity_shard_symbols": _SHARDS,
            "slot_cutoff_at": _CUTOFF.isoformat(),
            "slot_index": 0,
        },
    )
    try:
        replayed_shard = restarted_runtime.collect_task(
            _shard_task("G2"),
            _CUTOFF + timedelta(seconds=121),
        )
        result = restarted_runtime.collect_task(
            finalizer,
            _CUTOFF + timedelta(seconds=240),
        )
        scheduled_rows = restarted_runtime.store.connection.execute(
            "SELECT task_name, scheduled_at FROM scheduled_captures "
            "WHERE kind = 'capture_intraday_equities'"
        ).fetchall()
    finally:
        restarted_runtime.close()

    assert replayed_shard["contract"]["capture_id"]
    assert result["completed"] is True
    assert result["contract"]["data"]["slot_coverage"]["complete"] is True
    assert restarted_provider.calls == []
    assert [(row["task_name"], row["scheduled_at"]) for row in scheduled_rows] == [
        ("equity-intraday", _CUTOFF.isoformat())
    ]


def test_runtime_persists_budget_skip_as_terminal_without_calling_provider(tmp_path: Path) -> None:
    provider = _EquityProvider()
    runtime = _runtime(tmp_path, provider)
    task = _shard_task("G1")
    task.params["skip_reason"] = "skipped_budget"
    try:
        result = runtime.collect_task(task, _CUTOFF + timedelta(seconds=105))
    finally:
        runtime.close()

    assert result["completed"] is True
    assert result["terminal"] is True
    assert result["contract"]["data"]["slot_shard_status"] == "skipped_budget"
    assert provider.calls == []
