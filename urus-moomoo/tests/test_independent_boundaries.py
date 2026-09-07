from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
import subprocess
import sys

from urus_moomoo.services.calendar import MoomooTradingCalendar
from urus_moomoo.services.pacing import CollectionCoordinator
from urus_moomoo.services.quota import HistoryAdmission, HistoryCapacityService
from urus_moomoo.services.scheduler import CollectorScheduler
from urus_moomoo.runtime import CollectorRuntime
from urus_moomoo.config import CollectionTask
from urus_moomoo.storage.sqlite import CollectorStore, SCHEMA_VERSION


class _ProviderCalendar:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def trading_days(self, start, end):
        self.calls += 1
        return [row for row in self.rows if start <= row["time"] <= end]


class _SessionCalendar:
    def __init__(self, sessions):
        self.sessions = sessions

    def is_session(self, value):
        return value in self.sessions

    def session_open(self, value):
        return datetime(value.year, value.month, value.day, 14, 30, tzinfo=UTC)

    def session_close(self, value):
        return datetime(value.year, value.month, value.day, 21, tzinfo=UTC)


def test_moomoo_calendar_is_cached_and_conflict_fails_closed():
    session = date(2026, 7, 6)
    source = _ProviderCalendar([{"time": session, "trade_date_type": "WHOLE"}])
    calendar = MoomooTradingCalendar(source, _SessionCalendar(set()), chunk_days=30)

    assert calendar.is_session(session)
    assert calendar.is_session(session)
    assert source.calls == 1
    assert calendar.session_close(session) is None


def test_coordinator_persists_rate_classes(tmp_path: Path):
    path = tmp_path / "pacing.lock"
    sleeps: list[float] = []
    first = CollectionCoordinator(path, sleeper=sleeps.append)
    first.acquire_rate_slot("history", 2, now=datetime(2026, 1, 1, tzinfo=UTC))
    first.close()
    second = CollectionCoordinator(path, sleeper=sleeps.append)
    waited = second.acquire_rate_slot("history", 2, now=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC))
    assert waited == 1
    assert second.acquire_rate_slot("snapshot", 2, now=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)) == 0
    second.close()


def test_quota_reserve_and_request_time_admission_fail_closed():
    service = HistoryCapacityService(minimum_bar_count=2, reserve_absolute=2, reserve_ratio=0)
    plan = service.build_plan(
        [{"symbol": "AAA"}, {"symbol": "BBB"}],
        quota={"available": True, "used": 8, "remain": 2, "total": 10},
    )
    assert all(item["decision"] == "pending_quota" for item in plan["symbols"])

    admission = HistoryAdmission(service, quota_reader=lambda: {"available": True, "used": 10, "remain": 0, "total": 10})
    admission.prepare_symbols(["AAA"])
    decision = admission.acquire("AAA")
    assert decision["admitted"] is False
    assert decision["reason_code"] == "history_quota_reserve"


def test_sqlite_storage_and_contract_boundary(tmp_path: Path):
    with CollectorStore(tmp_path / "collector.sqlite") as store:
        store.write_raw("capture-1", "daily", {"value": 1}, schema_version="urus.moomoo.daily.v1")
        store.write_daily_bars([{"symbol": "AAA", "date": "2026-01-02", "close": 1}])
        quota = HistoryCapacityService(store=store)
        quota.capture(lambda: {"available": True, "used": 1, "remain": 2, "total": 3})
        assert store.latest_quota_snapshot() is not None
        assert store.read_raw("capture-1")["kind"] == "daily"
    assert SCHEMA_VERSION == 2


def test_source_boundary_has_no_urus_application_imports():
    root = Path(__file__).parents[1] / "src" / "urus_moomoo"
    forbidden = []
    for path in root.rglob("*.py"):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith(("import app", "from app", "import backend", "from backend")):
                forbidden.append(f"{path}:{line_number}")
    assert forbidden == []


def test_task_config_missing_is_fail_closed(tmp_path: Path):
    import os

    env = dict(os.environ, URUS_MOOMOO_TASK_CONFIG=str(tmp_path / "missing.toml"), PYTHONPATH=str(Path(__file__).parents[1] / "src"))
    result = subprocess.run([sys.executable, "-c", "from urus_moomoo.config import load_config; load_config()"], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "Missing local urus-moomoo task configuration" in result.stderr


def test_scheduler_runs_without_backend_health_or_route(tmp_path: Path):
    session = date(2026, 7, 6)
    source = _ProviderCalendar([{"time": session}])
    calendar = MoomooTradingCalendar(source, _SessionCalendar({session}))
    tasks = [
        CollectionTask(
            name="synthetic-daily",
            enabled=True,
            schedule="14:00",
            symbols=("TEST_ASSET",),
        )
    ]
    calls: list[str] = []
    store = CollectorStore(tmp_path / "runs.sqlite")
    scheduler = CollectorScheduler(
        tasks,
        timezone="UTC",
        calendar=calendar,
        runner=lambda task, _: calls.append(task.name),
        store=store,
    )

    assert scheduler.run_once(datetime(2026, 7, 6, 14, 1, tzinfo=UTC)) == ["synthetic-daily"]
    assert scheduler.run_once(datetime(2026, 7, 6, 14, 2, tzinfo=UTC)) == []
    restarted = CollectorScheduler(
        tasks,
        timezone="UTC",
        calendar=calendar,
        runner=lambda task, _: calls.append(task.name),
        store=store,
    )
    assert restarted.run_once(datetime(2026, 7, 6, 14, 3, tzinfo=UTC)) == []
    assert calls == ["synthetic-daily"]
    store.close()


def test_post_close_schedule_uses_calendar_close_and_local_delay(tmp_path: Path):
    session = date(2026, 7, 6)
    source = _ProviderCalendar([{"time": session}])
    calendar = MoomooTradingCalendar(source, _SessionCalendar({session}))
    task = CollectionTask(
        name="synthetic-close",
        enabled=True,
        schedule="post-close",
        symbols=("TEST_ASSET",),
        delay_seconds=120,
    )
    scheduler = CollectorScheduler(
        [task], timezone="UTC", calendar=calendar, runner=lambda *_: None,
        store=CollectorStore(tmp_path / "close.sqlite"), catch_up_seconds=600,
    )
    assert scheduler.due(datetime(2026, 7, 6, 21, 1, tzinfo=UTC)) == []
    assert [
        item.task.name
        for item in scheduler.due(datetime(2026, 7, 6, 21, 2, tzinfo=UTC))
    ] == ["synthetic-close"]


def test_intraday_schedule_uses_session_bounds_and_private_parameters(tmp_path: Path):
    session = date(2026, 7, 6)
    source = _ProviderCalendar([{"time": session}])
    calendar = MoomooTradingCalendar(source, _SessionCalendar({session}))
    task = CollectionTask(
        name="synthetic-intraday",
        enabled=True,
        schedule="intraday",
        symbols=("TEST_ASSET",),
        operation="capture_intraday",
        params={
            "interval_seconds": 300,
            "start_margin_seconds": 300,
            "end_margin_seconds": 300,
            "anchor_times": ["14:35"],
            "fast_band_percent": 3,
            "structure_band_percent": 10,
        },
    )
    scheduler = CollectorScheduler(
        [task],
        timezone="UTC",
        market_timezone="UTC",
        calendar=calendar,
        runner=lambda *_: None,
        store=CollectorStore(tmp_path / "intraday.sqlite"),
        catch_up_seconds=600,
    )
    due = scheduler.due(datetime(2026, 7, 6, 14, 36, tzinfo=UTC))
    assert len(due) == 1
    assert due[0].task.params["capture_tier"] == "structure"
    assert due[0].task.params["band_percent"] == 10


class _RuntimeProvider:
    def __init__(self):
        self.calls = []

    def quota_snapshot(self):
        return {"available": True, "history": {"used": 0, "remain": 10}}

    def instrument_cards(self, symbols):
        self.calls.append(("instrument_cards", symbols))
        return {"instruments": []}

    def capital_flow_day(self, symbol, trading_date):
        self.calls.append(("capital_flow_day", symbol, trading_date))
        return {"symbol": symbol}


def test_runtime_dispatches_explicit_operations(tmp_path: Path):
    from urus_moomoo.config import CollectorConfig

    tasks = (
        CollectionTask("synthetic-market", True, "12:34", ("TEST_ASSET",)),
        CollectionTask("synthetic-flow", True, "12:35", ("TEST_ASSET",), "capital_flow_day"),
    )
    config = CollectorConfig(timezone="UTC", output_dir=tmp_path, tasks=tasks)
    provider = _RuntimeProvider()
    runtime = CollectorRuntime(provider, config)
    try:
        runtime.collect_task(tasks[0], datetime(2026, 7, 6, 12, 34, tzinfo=UTC))
        runtime.collect_task(tasks[1], datetime(2026, 7, 6, 12, 35, tzinfo=UTC))
    finally:
        runtime.close()
    assert [call[0] for call in provider.calls] == ["instrument_cards", "capital_flow_day"]
