"""Independent offline acceptance checks for the collector pool plan boundary."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from urus_moomoo.config import CollectionTask
from urus_moomoo.services.scheduler import CollectorScheduler
from urus_moomoo.services.quota import HistoryAdmission, HistoryCapacityService
from urus_moomoo.storage.sqlite import CollectorStore


MARKET_TZ = ZoneInfo("America/New_York")
BENCHMARKS = ["SPY", "QQQ", "IWM"]


class _SessionCalendar:
    def is_session(self, _value: object) -> bool:
        return True

    def session_open(self, value: Any) -> datetime:
        return datetime(value.year, value.month, value.day, 9, 30, tzinfo=MARKET_TZ)

    def session_close(self, value: Any) -> datetime:
        return datetime(value.year, value.month, value.day, 16, 0, tzinfo=MARKET_TZ)


def _plan(trading_date: str, *, extra: str = "") -> dict[str, object]:
    selected = ["AAPL", "MSFT"]
    if extra:
        selected.append(extra)
    members = selected + BENCHMARKS
    groups = {
        symbol: ("大盘基准" if symbol in BENCHMARKS else "重点观察")
        for symbol in members
    }
    definition: dict[str, object] = {
        "schema_version": "urus.intraday.collection_plan.v1",
        "status": "configured",
        "trading_date": trading_date,
        "pool_revision": 2,
        "selected_symbols": selected,
        "benchmark_symbols": BENCHMARKS,
        "member_symbols": members,
        "quote_symbols": members,
        "minute_symbols": members,
        "display_group_by_symbol": groups,
    }
    canonical = json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"plan_id": hashlib.sha256(canonical.encode()).hexdigest(), **definition}


def _task() -> CollectionTask:
    return CollectionTask(
        "equity-intraday",
        True,
        "intraday",
        tuple(["AAPL", "MSFT", *BENCHMARKS]),
        "capture_intraday_equities",
        params={
            "interval_seconds": 300,
            "equity_shards_enabled": True,
            "groups": {
                "A": ["SPY"],
                "B": ["AAPL"],
                "C": ["QQQ", "IWM"],
                "D": ["MSFT"],
            },
            "equity_shard_symbols": {
                "G1": ["SPY"],
                "G2": ["QQQ"],
                "G3": ["IWM"],
            },
        },
    )


def test_intraday_plan_is_frozen_across_collector_store_restart(tmp_path: Path) -> None:
    from urus_moomoo.intraday_pool import IntradayPoolPlanLoader

    database = tmp_path / "collector.sqlite"
    first = _plan("2026-10-19")
    calls: list[str] = []

    def fetch(url: str, _timeout: float):
        calls.append(url)
        return 200, first

    store = CollectorStore(database)
    loader = IntradayPoolPlanLoader(
        api_url="http://loopback.invalid",
        timeout_seconds=1,
        store=store,
        get=fetch,
    )
    try:
        assert loader.plan_for("2026-10-19") == first
    finally:
        store.close()

    changed = _plan("2026-10-19", extra="NVDA")
    restarted_store = CollectorStore(database)
    restarted_loader = IntradayPoolPlanLoader(
        api_url="http://loopback.invalid",
        timeout_seconds=1,
        store=restarted_store,
        get=lambda *_args: (200, changed),
    )
    try:
        assert restarted_loader.plan_for("2026-10-19") == first
        assert restarted_store.get_intraday_collection_plan("2026-10-19") == first
    finally:
        restarted_store.close()

    assert len(calls) == 1


def test_plan_preparation_keeps_fixed_benchmarks_in_both_request_ranges(tmp_path: Path) -> None:
    from urus_moomoo.intraday_pool import IntradayPoolPlanLoader

    plan = _plan("2026-10-19")
    store = CollectorStore(tmp_path / "collector.sqlite")
    loader = IntradayPoolPlanLoader(
        api_url="http://loopback.invalid",
        timeout_seconds=1,
        store=store,
        get=lambda *_args: (200, plan),
    )
    try:
        prepared = loader.prepare_task(_task(), datetime(2026, 10, 19, tzinfo=MARKET_TZ).date())
    finally:
        store.close()

    assert tuple(prepared.symbols) == tuple(plan["member_symbols"])
    params = dict(prepared.params or {})
    assert params["pool_plan_id"] == plan["plan_id"]
    assert set(params["display_group_by_symbol"]) == set(plan["member_symbols"])
    assert all(symbol in params["groups"][role] for role in ("A", "B", "C", "D") for symbol in params["groups"][role])
    assert set(symbol for members in params["equity_shard_symbols"].values() for symbol in members) == set(plan["member_symbols"])


def test_dynamic_pool_member_is_admitted_after_plan_scope_preparation(tmp_path: Path) -> None:
    from urus_moomoo.intraday_pool import IntradayPoolPlanLoader

    plan = _plan("2026-10-19", extra="NVDA")
    store = CollectorStore(tmp_path / "collector.sqlite")
    loader = IntradayPoolPlanLoader(
        api_url="http://loopback.invalid",
        timeout_seconds=1,
        store=store,
        get=lambda *_args: (200, plan),
    )
    try:
        prepared = loader.prepare_task(_task(), datetime(2026, 10, 19, tzinfo=MARKET_TZ).date())
    finally:
        store.close()

    admission = HistoryAdmission(
        HistoryCapacityService(reserve_absolute=0, reserve_ratio=0),
        quota_reader=lambda: {
            "available": True,
            "remain": 100,
            "total": 100,
            "window_symbols": [],
        },
    )
    admission.prepare_symbols(_task().symbols)
    assert admission.acquire("NVDA")["reason_code"] == "outside_scope"

    # Plan-selected members outside the old static task scope must be passed
    # through the existing request-time admission boundary before collection.
    admission.reset_run()
    admission.prepare_symbols(prepared.symbols)
    decision = admission.acquire("NVDA")
    assert decision["admitted"] is True
    assert decision["reason_code"] is None


def test_terminal_plan_unavailable_skip_is_not_retried_in_same_scheduler_slot() -> None:
    eof = CollectionTask(
        "options-intraday",
        True,
        "intraday",
        ("SPY",),
        "capture_intraday",
        params={"interval_seconds": 300, "start_margin_seconds": 300, "end_margin_seconds": 300},
    )
    equity = CollectionTask(
        "equity-intraday",
        True,
        "intraday",
        ("SPY", "QQQ", "IWM"),
        "capture_intraday_equities",
        params={"interval_seconds": 300, "start_margin_seconds": 300, "end_margin_seconds": 300},
    )
    now = datetime(2026, 10, 19, 9, 35, tzinfo=MARKET_TZ)
    attempts: list[str] = []

    def runner(task: CollectionTask, _scheduled_at: datetime) -> dict[str, object]:
        if task.operation == "capture_intraday":
            attempts.append("eof")
            return {"completed": True}
        attempts.append("plan_http")
        # The runtime turns one bounded plan failure into a terminal slot skip;
        # the next market slot remains eligible for a fresh plan attempt.
        return {
            "completed": True,
            "terminal": True,
            "skip_reason": "intraday_plan_unavailable",
        }

    scheduler = CollectorScheduler(
        [equity, eof],
        timezone="America/New_York",
        market_timezone="America/New_York",
        calendar=_SessionCalendar(),
        runner=runner,
        intraday_catch_up_seconds=0,
    )
    assert scheduler.run_once(now) == ["options-intraday", "equity-intraday"]
    assert scheduler.run_once(now) == []
    assert attempts == ["eof", "plan_http"]


def test_collection_runner_turns_plan_failure_into_one_terminal_slot_skip() -> None:
    from urus_moomoo.collection_runner import build_collection_runner

    class _Runtime:
        def collect_task(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            raise AssertionError("runtime must not collect without a frozen pool plan")

    class _UnavailablePlan:
        def prepare_task(self, *_args: object, **_kwargs: object) -> CollectionTask:
            raise RuntimeError("intraday collection plan unavailable: API timeout")

    runner = build_collection_runner(
        _Runtime(),
        publisher=None,
        pool_plan_loader=_UnavailablePlan(),
        market_timezone="America/New_York",
    )
    result = runner(
        _task(),
        datetime(2026, 10, 19, 9, 35, tzinfo=MARKET_TZ),
    )

    assert result["completed"] is False
    assert result["terminal"] is True
    assert result["intraday_pool_unavailable"] is True
    assert "API timeout" in str(result["error"])


def test_scheduler_does_not_fetch_a_cold_plan_during_due_and_runs_eof_first() -> None:
    eof = CollectionTask(
        "options-intraday",
        True,
        "intraday",
        ("SPY",),
        "capture_intraday",
        params={"interval_seconds": 300, "start_margin_seconds": 300, "end_margin_seconds": 300},
    )
    equity = CollectionTask(
        "equity-intraday",
        True,
        "intraday",
        ("SPY", "QQQ", "IWM"),
        "capture_intraday_equities",
        params={"interval_seconds": 300, "start_margin_seconds": 300, "end_margin_seconds": 300},
    )
    now = datetime(2026, 10, 19, 9, 35, tzinfo=MARKET_TZ)
    events: list[str] = []

    def runner(task: CollectionTask, _scheduled_at: datetime) -> dict[str, object]:
        if task.operation == "capture_intraday":
            events.append("eof")
        else:
            # This represents a cold plan load performed by the runner, after
            # scheduler.due has enumerated work.  It must never precede EOF.
            events.append("plan_http")
        return {"completed": True}

    scheduler = CollectorScheduler(
        [equity, eof],
        timezone="America/New_York",
        market_timezone="America/New_York",
        calendar=_SessionCalendar(),
        runner=runner,
        intraday_catch_up_seconds=0,
    )
    assert scheduler.due(now)  # due enumeration itself has no plan-loader side effect
    assert events == []

    scheduler.run_once(now)
    assert events[:2] == ["eof", "plan_http"]
