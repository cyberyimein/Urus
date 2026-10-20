from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from urus_moomoo.collection_runner import build_collection_runner
from urus_moomoo.config import CollectionTask
from urus_moomoo.intraday_pool import IntradayPoolPlanLoader
from urus_moomoo.services.scheduler import CollectorScheduler
from urus_moomoo.storage.sqlite import CollectorStore


class _Calendar:
    def is_session(self, day: date) -> bool:
        return day == date(2026, 10, 19)

    def session_open(self, day: date):
        return datetime(2026, 10, 19, 9, 30, tzinfo=ZoneInfo("America/New_York"))

    def session_close(self, day: date):
        return datetime(2026, 10, 19, 9, 35, tzinfo=ZoneInfo("America/New_York"))


class _Runtime:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.history_admission = self
        self.prepared_symbols: list[str] = []
        self.collected: list[CollectionTask] = []

    def prepare_symbols(self, symbols) -> None:
        self.prepared_symbols = list(symbols)

    def collect_task(self, task: CollectionTask, _scheduled_at: datetime):
        self.collected.append(task)
        self.events.append(task.operation)
        return {"completed": True}


def _plan() -> dict[str, object]:
    definition: dict[str, object] = {
        "schema_version": "urus.intraday.collection_plan.v1",
        "status": "configured",
        "trading_date": "2026-10-19",
        "pool_revision": 4,
        "selected_symbols": ["AAA"],
        "benchmark_symbols": ["SPY", "QQQ", "IWM"],
        "member_symbols": ["AAA", "SPY", "QQQ", "IWM"],
        "quote_symbols": ["AAA", "SPY", "QQQ", "IWM"],
        "minute_symbols": ["AAA", "SPY", "QQQ", "IWM"],
        "display_group_by_symbol": {
            "AAA": "半导体",
            "SPY": "大盘基准",
            "QQQ": "大盘基准",
            "IWM": "大盘基准",
        },
    }
    canonical = json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"plan_id": hashlib.sha256(canonical.encode()).hexdigest(), **definition}


def test_scheduler_runs_due_eoflora_before_cold_plan_fetch_and_dynamic_equity_task(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    store = CollectorStore(tmp_path / "collector.sqlite")
    runtime = _Runtime(events)
    loader = IntradayPoolPlanLoader(
        api_url="http://urus:8000",
        timeout_seconds=3,
        store=store,
        get=lambda _url, _timeout: (events.append("plan_get") or 200, _plan()),
    )
    runner = build_collection_runner(
        runtime,
        publisher=None,
        pool_plan_loader=loader,
        market_timezone="America/New_York",
    )
    tasks = (
        CollectionTask(
            "eoflora", True, "intraday", ("SPY",), "capture_intraday",
            params={"interval_seconds": 300, "start_margin_seconds": 0,
                    "end_margin_seconds": 300},
        ),
        CollectionTask(
            "equities", True, "intraday", ("OLD1", "OLD2", "OLD3"), "capture_intraday_equities",
            params={"interval_seconds": 300, "start_margin_seconds": 0,
                    "end_margin_seconds": 0, "equity_shards_enabled": True,
                    "equity_shard_symbols": {"G1": ["OLD1"], "G2": ["OLD2"], "G3": ["OLD3"]},
                    "groups": {"A": ["OLD1"], "B": ["OLD2"], "C": ["OLD3"], "D": []}},
        ),
    )
    scheduler = CollectorScheduler(
        tasks,
        timezone="America/New_York",
        market_timezone="America/New_York",
        calendar=_Calendar(),
        runner=runner,
        clock=lambda: datetime(2026, 10, 19, 9, 31, tzinfo=ZoneInfo("America/New_York")),
        store=store,
        catch_up_seconds=0,
        intraday_catch_up_seconds=60,
    )
    try:
        scheduler.run_once()
    finally:
        store.close()

    assert events[:3] == ["capture_intraday", "plan_get", "capture_intraday_equities"]
    equity_task = next(task for task in runtime.collected if task.operation == "capture_intraday_equities")
    assert equity_task.symbols == ("AAA", "SPY", "QQQ", "IWM")
    assert equity_task.params["pool_plan_id"] == _plan()["plan_id"]
    assert equity_task.params["windowed_equity_shard"] is True
    assert runtime.prepared_symbols == ["AAA", "SPY", "QQQ", "IWM"]


def test_missing_plan_does_not_call_collection_runtime(tmp_path: Path) -> None:
    events: list[str] = []
    store = CollectorStore(tmp_path / "collector.sqlite")
    runtime = _Runtime(events)
    loader = IntradayPoolPlanLoader(
        api_url="http://urus:8000",
        timeout_seconds=3,
        store=store,
        get=lambda _url, _timeout: (409, {"status": "unconfigured"}),
    )
    runner = build_collection_runner(
        runtime,
        publisher=None,
        pool_plan_loader=loader,
        market_timezone="America/New_York",
    )
    task = CollectionTask(
        "equities", True, "intraday", ("OLD",), "maintain_intraday_equities"
    )
    try:
        result = runner(task, datetime(2026, 10, 19, 8, 0, tzinfo=ZoneInfo("America/New_York")))
    finally:
        store.close()

    assert result["completed"] is False
    assert result["terminal"] is False
    assert result["intraday_pool_unavailable"] is True
    assert runtime.collected == []


def test_maintenance_plan_failure_remains_retryable_until_its_cutoff(tmp_path: Path) -> None:
    events: list[str] = []
    store = CollectorStore(tmp_path / "collector.sqlite")
    runtime = _Runtime(events)
    loader = IntradayPoolPlanLoader(
        api_url="http://urus:8000",
        timeout_seconds=3,
        store=store,
        get=lambda _url, _timeout: (409, {"status": "unconfigured"}),
    )
    runner = build_collection_runner(
        runtime,
        publisher=None,
        pool_plan_loader=loader,
        market_timezone="America/New_York",
    )
    task = CollectionTask(
        "equities", True, "pre-open", ("OLD",), "maintain_intraday_equities"
    )
    try:
        result = runner(
            task,
            datetime(2026, 10, 19, 8, 0, tzinfo=ZoneInfo("America/New_York")),
        )
    finally:
        store.close()

    assert result["completed"] is False
    assert result["terminal"] is False
    assert runtime.collected == []
