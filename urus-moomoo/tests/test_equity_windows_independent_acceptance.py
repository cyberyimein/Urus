"""Independent end-to-end acceptance checks for the 1+3+1 equity window.

The scheduler tests in ``test_intraday_equity_windows.py`` verify task shaping,
while these tests drive a real ``CollectorRuntime`` and ``CollectorStore`` with
an in-memory fake provider.  No OpenD connection, provider SDK, deployment task
file, or shared database is used here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from urus_moomoo.config import CollectionTask, CollectorConfig
from urus_moomoo.runtime import CollectorRuntime
from urus_moomoo.services.scheduler import CollectorScheduler
from urus_moomoo.storage.sqlite import CollectorStore


MARKET_TZ = ZoneInfo("America/New_York")
UTC_SESSION_OPEN = datetime(2026, 10, 15, 13, 30, tzinfo=UTC)
CUTOFF = datetime(2026, 10, 15, 9, 35, tzinfo=MARKET_TZ)
LATER_CUTOFF = CUTOFF + timedelta(minutes=5)
SYMBOLS = ("SPY", "QQQ", "SMH", "AAA")
SHARDS = {"G1": ["SPY", "QQQ"], "G2": ["SMH"], "G3": ["AAA"]}
GROUPS = {"A": ["SPY"], "B": ["QQQ"], "C": ["SMH"], "D": ["AAA"]}


class _SessionCalendar:
    def is_session(self, _value: object) -> bool:
        return True

    def session_open(self, value: Any) -> datetime:
        return datetime(value.year, value.month, value.day, 9, 30, tzinfo=MARKET_TZ)

    def session_close(self, value: Any) -> datetime:
        return datetime(value.year, value.month, value.day, 16, 0, tzinfo=MARKET_TZ)


class _WindowProvider:
    """Provider seam that returns P2-shaped, cutoff-bounded minute rows."""

    def __init__(self, *, fail_shards: set[str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.reset_calls = 0
        self.quota_calls = 0
        self.quote_calls = 0
        self.history_calls = 0
        self.option_calls = 0
        self.fail_shards = set(fail_shards or set())

    def reset_collection_run(self) -> None:
        self.reset_calls += 1

    def quota_snapshot(self) -> dict[str, object]:
        self.quota_calls += 1
        return {"available": True, "used": 0, "remain": 400, "total": 400}

    def get_market_snapshot(self, *_args: Any, **_kwargs: Any) -> object:
        self.quote_calls += 1
        raise AssertionError("the runtime fake must use its public capture operation")

    def request_history_kline(self, *_args: Any, **_kwargs: Any) -> object:
        self.history_calls += 1
        raise AssertionError("the runtime fake must use its public capture operation")

    def capture_intraday_equities(self, *, symbols: list[str], **params: Any) -> dict[str, object]:
        shard_id = str(params.get("shard_id") or "")
        self.calls.append(
            {
                "shard_id": shard_id,
                "scheduled_at": params.get("scheduled_at"),
                "evidence_cutoff": params.get("evidence_cutoff"),
                "capture_quotes": bool(params.get("capture_quotes")),
                "minute_symbols": list(params.get("minute_symbols") or []),
                "quote_symbols": list(params.get("quote_symbols") or []),
            }
        )
        if shard_id in self.fail_shards:
            raise TimeoutError(f"synthetic timeout for {shard_id}")

        cutoff = params["evidence_cutoff"]
        assert isinstance(cutoff, datetime)
        cutoff_utc = cutoff.astimezone(UTC)
        minute_symbols = [str(symbol).upper() for symbol in params.get("minute_symbols", [])]
        minute_count = max(
            0,
            int((cutoff_utc - UTC_SESSION_OPEN).total_seconds() // 60),
        )
        rows: list[dict[str, object]] = []
        for symbol_index, symbol in enumerate(minute_symbols):
            for minute_index in range(minute_count):
                start = UTC_SESSION_OPEN + timedelta(minutes=minute_index)
                end = start + timedelta(minutes=1)
                base = 100.0 + symbol_index + minute_index * 0.1
                rows.append(
                    {
                        "symbol": symbol,
                        "bar_time": start.astimezone(MARKET_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                        "bar_start_at": start.isoformat(),
                        "bar_end_at": end.isoformat(),
                        "open": base,
                        "high": base + 0.2,
                        "low": base - 0.1,
                        "close": base + 0.1,
                        "volume": 100.0 + minute_index,
                        "turnover": (100.0 + minute_index) * (base + 0.1),
                        "quality_state": "ready",
                    }
                )

        quote_rows = (
            [
                {
                    "symbol": symbol,
                    "quote_code": f"US.{symbol}",
                    "last_price": 100.0,
                    "provider": "moomoo_openapi",
                    "source": "moomoo_opend_snapshot",
                    "update_time": "2026-10-15 09:35:01",
                }
                for symbol in symbols
            ]
            if bool(params.get("capture_quotes"))
            else []
        )
        snapshots = (
            [
                {
                    "symbols": list(symbols),
                    "records": [{"symbol": symbol} for symbol in symbols],
                    "response_received_at": "2026-10-15T13:35:05+00:00",
                }
            ]
            if bool(params.get("capture_quotes"))
            else []
        )
        return {
            "schema_version": "moomoo-intraday-equities-v1",
            "provider": "moomoo_openapi",
            "captured_at": (cutoff_utc + timedelta(seconds=5)).isoformat(),
            "trading_date": cutoff.astimezone(MARKET_TZ).date().isoformat(),
            "scheduled_at": cutoff.isoformat(),
            "evidence_cutoff": cutoff.isoformat(),
            "minute_timestamp_role": "start",
            "minute_timestamp_policy": {
                "source_fields": ["time_key", "bar_time"],
                "role": "start",
                "interval_seconds": 60,
                "naive_timestamp_timezone": "America/New_York",
                "provider_boundary_verified": True,
                "provider_boundary_evidence": {
                    "provider": "moomoo_openapi",
                    "market": "US",
                    "interval": "K_1M",
                    "role": "start",
                    "evidence_trading_date": cutoff.astimezone(MARKET_TZ).date().isoformat(),
                    "capture_check_sha256": "a" * 64,
                },
                "enablement_note": None,
            },
            "slot_index": int(params.get("slot_index", 0)),
            "rotation": int(params.get("slot_index", 0)) % 3,
            "shard_id": shard_id,
            "shard_symbols": minute_symbols,
            "capture_quotes": bool(params.get("capture_quotes")),
            "slot_shard_status": "complete",
            "request_budget": {"deadline_at": None, "request_timeout_seconds": 3.0, "exhausted": False},
            "requested_symbols": list(symbols),
            "quote_symbols": list(symbols),
            "minute_symbols": minute_symbols,
            "group_by_symbol": dict(params.get("group_by_symbol") or {}),
            "quotes": quote_rows,
            "unavailable_quote_symbols": [],
            "minute_bars": rows,
            "invalid_minute_bars": [],
            "minute_bar_unavailable_symbols": [],
            "history_quota": {
                "available": True,
                "requested_symbols": minute_symbols,
                "admitted_symbols": minute_symbols,
                "deferred_symbols": [],
                "admissions": [],
            },
            "quality_summary": {
                "complete": True,
                "status": "ok",
                "quote_coverage": 1.0 if params.get("capture_quotes") else 0.0,
                "minute_symbols_requested": len(minute_symbols),
                "minute_symbols_admitted": len(minute_symbols),
                "minute_bars_returned": len(rows),
                "invalid_minute_bars": 0,
                "unavailable_quote_symbols": [],
                "unavailable_minute_symbols": [],
                "failed_history_symbols": [],
                "skipped_budget_symbols": [],
                "filtered_future_bars": 0,
                "minute_timestamp_role": "start",
                "minute_timestamp_policy": {
                    "provider_boundary_verified": True,
                    "role": "start",
                },
                "evidence_cutoff": cutoff.isoformat(),
                "quality_warnings": [],
            },
            "provider_raw": {
                "market_snapshot_batches": snapshots,
                "history_kline_batches": [],
            },
        }

    def close(self) -> None:
        return None


def _window_task() -> CollectionTask:
    return CollectionTask(
        "equity-intraday",
        True,
        "intraday",
        SYMBOLS,
        "capture_intraday_equities",
        params={
            "interval_seconds": 300,
            "start_margin_seconds": 300,
            "end_margin_seconds": 300,
            "equity_shards_enabled": True,
            "equity_shard_symbols": SHARDS,
            "groups": GROUPS,
        },
    )


def _runtime(tmp_path: Path, provider: _WindowProvider, clock: dict[str, datetime]) -> CollectorRuntime:
    return CollectorRuntime(
        provider,
        CollectorConfig(
            timezone="America/New_York",
            market_timezone="America/New_York",
            output_dir=tmp_path,
            tasks=(_window_task(),),
            lock_path=tmp_path / "collection.lock",
            sqlite_path=tmp_path / "collector.sqlite",
        ),
        clock=lambda: clock["now"],
    )


def _shard_task(cutoff: datetime, shard_id: str, *, slot_index: int = 0) -> CollectionTask:
    execution_offset = {"G1": 60, "G2": 120, "G3": 180}[shard_id]
    return CollectionTask(
        f"equity-intraday:{shard_id}",
        True,
        "intraday",
        SYMBOLS,
        "capture_intraday_equities",
        params={
            "windowed_equity_shard": True,
            "source_task_name": "equity-intraday",
            "shard_id": shard_id,
            "shard_symbols": SHARDS[shard_id],
            "equity_shard_symbols": SHARDS,
            "slot_cutoff_at": cutoff.isoformat(),
            "request_budget_deadline_at": (
                cutoff + timedelta(seconds=execution_offset + 45)
            ).isoformat(),
            "capture_quotes": shard_id == "G1",
            "slot_index": slot_index,
            "groups": GROUPS,
        },
    )


def _finalizer_task(cutoff: datetime, *, slot_index: int = 0) -> CollectionTask:
    return CollectionTask(
        "equity-intraday:finalize",
        True,
        "intraday",
        SYMBOLS,
        "finalize_intraday_equities",
        params={
            "equity_shard_finalizer": True,
            "source_task_name": "equity-intraday",
            "equity_shard_symbols": SHARDS,
            "slot_cutoff_at": cutoff.isoformat(),
            "slot_index": slot_index,
            "groups": GROUPS,
        },
    )


def _set_clock(clock: dict[str, datetime], value: datetime) -> None:
    clock["now"] = value


def _p3_projection(contract: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """Use the real backend projection against the finalizer's stored contract."""

    import sys

    backend_root = Path(__file__).resolve().parents[2] / "backend"
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    from app.intraday.equities import build_intraday_projection

    return build_intraday_projection(contract, now=now)


def test_runtime_scheduler_merges_three_shards_into_one_p3_parseable_slot(tmp_path: Path) -> None:
    provider = _WindowProvider()
    clock = {"now": CUTOFF + timedelta(seconds=60)}
    runtime = _runtime(tmp_path, provider, clock)
    scheduler = CollectorScheduler(
        [_window_task()],
        timezone="America/New_York",
        market_timezone="America/New_York",
        calendar=_SessionCalendar(),
        runner=runtime.collect_task,
        store=runtime.store,
        clock=lambda: clock["now"],
    )
    try:
        expected = {
            60: ["equity-intraday:G1"],
            120: ["equity-intraday:G2"],
            180: ["equity-intraday:G3"],
            240: ["equity-intraday:finalize"],
        }
        for offset, task_names in expected.items():
            _set_clock(clock, CUTOFF + timedelta(seconds=offset))
            assert scheduler.run_once(clock["now"]) == task_names

        scheduled_rows = runtime.store.connection.execute(
            "SELECT task_name, scheduled_at, kind, completed FROM scheduled_captures"
        ).fetchall()
        assert len(scheduled_rows) == 1
        assert tuple(scheduled_rows[0]) == (
            "equity-intraday",
            CUTOFF.isoformat(),
            "capture_intraday_equities",
            1,
        )
        stored = runtime.store.read_scheduled_raw("equity-intraday", CUTOFF)
        assert stored is not None
        contract = stored["contract"]
        assert contract["task_name"] == "equity-intraday"
        assert contract["scheduled_at"] == CUTOFF.isoformat()
        data = contract["data"]
        assert data["slot_coverage"]["complete"] is True
        assert data["slot_coverage"]["completed_shards"] == ["G1", "G2", "G3"]
        assert data["minute_symbols"] == ["SPY", "QQQ", "SMH", "AAA"]
        assert data["group_by_symbol"] == {
            "SPY": "A",
            "QQQ": "B",
            "SMH": "C",
            "AAA": "D",
        }
        assert [call["shard_id"] for call in provider.calls] == ["G1", "G2", "G3"]
        assert all(call["scheduled_at"] == CUTOFF for call in provider.calls)
        assert all(call["evidence_cutoff"] == CUTOFF for call in provider.calls)
        assert provider.calls[0]["capture_quotes"] is True
        assert [call["capture_quotes"] for call in provider.calls[1:]] == [False, False]
        # The T+240 finalizer is a local merge.  It must not make a quote,
        # minute-history, quota, or option-provider request of its own.
        assert provider.quota_calls == 0
        assert provider.quote_calls == 0
        assert provider.history_calls == 0
        assert provider.option_calls == 0

        # refs-v1 final captures are compact.  Hydrate the immutable references
        # through the backend resolver before handing the contract to P3.
        import sys

        backend_root = Path(__file__).resolve().parents[2] / "backend"
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))
        from app.intraday.references import hydrate_capture_from_path

        hydrated = hydrate_capture_from_path(runtime.store.path, contract)
        projection = _p3_projection(hydrated, now=CUTOFF + timedelta(seconds=30))
        assert projection["status"] == "ok"
        assert projection["quality"]["status"] == "ok"
        assert projection["freshness"]["status"] == "fresh"
        assert projection["coverage"]["missing_bars_5m"] == 0
        assert {
            symbol: len(item["bars"])
            for symbol, item in projection["symbols"].items()
        } == {symbol: 1 for symbol in SYMBOLS}
        assert projection["symbols"]["SPY"]["role"] == "A"
        assert projection["symbols"]["AAA"]["role"] == "D"
    finally:
        runtime.close()


@pytest.mark.parametrize("failure_mode", ["missing", "timeout"])
def test_missing_or_timed_out_g2_is_partial_and_never_reuses_previous_slot(
    tmp_path: Path, failure_mode: str
) -> None:
    provider = _WindowProvider()
    clock = {"now": LATER_CUTOFF + timedelta(seconds=60)}
    runtime = _runtime(tmp_path, provider, clock)
    try:
        # Seed an earlier G2 raw package.  The current finalizer may only read
        # the current cutoff's shard identities.
        previous_task = _shard_task(CUTOFF, "G2")
        _set_clock(clock, CUTOFF + timedelta(seconds=120))
        runtime.collect_task(previous_task, clock["now"])
        previous_capture = runtime.store.read_raw(
            next(
                row["capture_id"]
                for row in runtime.store.connection.execute(
                    "SELECT capture_id FROM raw_captures WHERE kind = 'capture_intraday_equities'"
                ).fetchall()
            )
        )
        assert previous_capture is not None

        provider.fail_shards = {"G2"} if failure_mode == "timeout" else set()
        for shard_id in ("G1", "G3"):
            _set_clock(clock, LATER_CUTOFF + timedelta(seconds={"G1": 60, "G3": 180}[shard_id]))
            runtime.collect_task(_shard_task(LATER_CUTOFF, shard_id, slot_index=1), clock["now"])
        if failure_mode == "timeout":
            _set_clock(clock, LATER_CUTOFF + timedelta(seconds=120))
            runtime.collect_task(_shard_task(LATER_CUTOFF, "G2", slot_index=1), clock["now"])

        _set_clock(clock, LATER_CUTOFF + timedelta(seconds=240))
        result = runtime.collect_task(
            _finalizer_task(LATER_CUTOFF, slot_index=1), clock["now"]
        )
        assert result["completed"] is False
        data = result["contract"]["data"]
        assert data["quality_summary"]["status"] == "partial"
        assert data["minute_bar_unavailable_symbols"] == ["SMH"]
        import sys

        backend_root = Path(__file__).resolve().parents[2] / "backend"
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))
        from app.intraday.references import hydrate_capture_from_path, resolved_capture_data

        hydrated = hydrate_capture_from_path(runtime.store.path, result["contract"])
        resolved_data = resolved_capture_data(hydrated)
        assert all(row["symbol"] != "SMH" for row in resolved_data["minute_bars"])
        assert all(
            row["bar_start_at"] >= UTC_SESSION_OPEN.isoformat()
            for row in resolved_data["minute_bars"]
        )
        g2_coverage = data["slot_coverage"]["shards"]["G2"]
        if failure_mode == "missing":
            assert data["slot_coverage"]["missing_shards"] == ["G2"]
            assert g2_coverage["status"] == "skipped_window_expired"
        else:
            assert data["slot_coverage"]["partial_shards"] == ["G2"]
            assert g2_coverage["status"] == "provider_error"
    finally:
        runtime.close()


def test_missing_g1_does_not_reuse_previous_slot_quotes_and_finalizer_stays_terminal(
    tmp_path: Path,
) -> None:
    provider = _WindowProvider()
    clock = {"now": LATER_CUTOFF + timedelta(seconds=60)}
    runtime = _runtime(tmp_path, provider, clock)
    try:
        _set_clock(clock, CUTOFF + timedelta(seconds=60))
        runtime.collect_task(_shard_task(CUTOFF, "G1"), clock["now"])
        provider.calls.clear()

        for shard_id in ("G2", "G3"):
            _set_clock(clock, LATER_CUTOFF + timedelta(seconds={"G2": 120, "G3": 180}[shard_id]))
            runtime.collect_task(_shard_task(LATER_CUTOFF, shard_id, slot_index=1), clock["now"])
        _set_clock(clock, LATER_CUTOFF + timedelta(seconds=240))
        result = runtime.collect_task(
            _finalizer_task(LATER_CUTOFF, slot_index=1), clock["now"]
        )
        data = result["contract"]["data"]
        assert result["completed"] is False
        import sys

        backend_root = Path(__file__).resolve().parents[2] / "backend"
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))
        from app.intraday.references import hydrate_capture_from_path, resolved_capture_data

        hydrated = hydrate_capture_from_path(runtime.store.path, result["contract"])
        resolved_data = resolved_capture_data(hydrated)
        assert resolved_data["quotes"] == []
        assert data["unavailable_quote_symbols"] == list(SYMBOLS)
        assert data["slot_coverage"]["missing_shards"] == ["G1"]

        # A finalizer is terminal even when it publishes a partial slot.  A
        # scheduler retry must not ask the provider or create another slot row.
        first_capture_id = result["contract"]["capture_id"]
        second = runtime.collect_task(
            _finalizer_task(LATER_CUTOFF, slot_index=1), clock["now"] + timedelta(seconds=1)
        )
        assert second["contract"]["capture_id"] == first_capture_id
        assert len(provider.calls) == 2
    finally:
        runtime.close()


def test_same_shard_retry_and_runtime_restart_do_not_repeat_provider_work(
    tmp_path: Path,
) -> None:
    provider = _WindowProvider()
    clock = {"now": CUTOFF + timedelta(seconds=120)}
    task = _shard_task(CUTOFF, "G2")

    first_runtime = _runtime(tmp_path, provider, clock)
    try:
        first = first_runtime.collect_task(task, clock["now"])
        second = first_runtime.collect_task(task, clock["now"] + timedelta(seconds=1))
    finally:
        first_runtime.close()

    restarted_runtime = _runtime(tmp_path, provider, clock)
    try:
        restarted = restarted_runtime.collect_task(task, clock["now"] + timedelta(seconds=2))
    finally:
        restarted_runtime.close()

    assert first["contract"]["capture_id"] == second["contract"]["capture_id"]
    assert second["contract"]["capture_id"] == restarted["contract"]["capture_id"]
    assert len(provider.calls) == 1


@pytest.mark.parametrize("eof_failure", ["timeout", "incomplete"])
def test_eoflora_failure_from_previous_slot_still_yields_new_equity_window(
    tmp_path: Path, eof_failure: str
) -> None:
    """An outstanding Eoflora retry must keep priority over the next slot."""

    eof_task = CollectionTask(
        "eoflora-intraday",
        True,
        "intraday",
        ("SPY",),
        "capture_intraday",
        params={"interval_seconds": 300, "start_margin_seconds": 300, "end_margin_seconds": 300},
    )
    equity_task = _window_task()
    clock = {"now": CUTOFF + timedelta(minutes=6)}
    calls: list[tuple[str, datetime, str | None]] = []
    with CollectorStore(tmp_path / "scheduler.sqlite") as store:
        def runner(task: CollectionTask, scheduled_at: datetime) -> dict[str, object]:
            if task.operation == "capture_intraday":
                calls.append(("eof", scheduled_at, None))
                if scheduled_at == CUTOFF:
                    if eof_failure == "timeout":
                        raise TimeoutError("synthetic Eoflora timeout")
                    return {"completed": False}
                return {"completed": True}
            skip_reason = str(dict(task.params or {}).get("skip_reason") or "") or None
            calls.append(("equity", scheduled_at, skip_reason))
            return {"completed": True, "terminal": True}

        scheduler = CollectorScheduler(
            [eof_task, equity_task],
            timezone="America/New_York",
            market_timezone="America/New_York",
            calendar=_SessionCalendar(),
            runner=runner,
            store=store,
            clock=lambda: clock["now"],
            intraday_catch_up_seconds=600,
        )
        ran = scheduler.run_once(clock["now"])

        eof_calls = [item for item in calls if item[0] == "eof"]
        equity_calls = [item for item in calls if item[0] == "equity"]
        assert [item[1] for item in eof_calls] == [CUTOFF, LATER_CUTOFF]
        assert len(equity_calls) == 1
        assert equity_calls[0][1] == LATER_CUTOFF + timedelta(seconds=60)
        assert equity_calls[0][2] == "skipped_priority"
        assert ran == ["eoflora-intraday", "equity-intraday:G1"]
