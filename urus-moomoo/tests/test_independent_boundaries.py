from __future__ import annotations

from datetime import UTC, date, datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

from urus_moomoo.integrations.moomoo import OpenDMarketAdapter
from urus_moomoo.integrations.moomoo_intraday_options import MoomooIntradayOptionsAdapter
from urus_moomoo.services.calendar import MoomooTradingCalendar
from urus_moomoo.services.pacing import CollectionCoordinator
from urus_moomoo.services.quota import HistoryAdmission, HistoryCapacityService
from urus_moomoo.services.scheduler import CollectorScheduler
from urus_moomoo.runtime import CollectorRuntime
from urus_moomoo.config import CollectionTask
from urus_moomoo.storage.sqlite import CollectorStore, SCHEMA_VERSION
from urus_moomoo.storage.intraday import CanonicalIntradayStore


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


class _NoopRateLimiter:
    def acquire_rate_slot(self, *_args, **_kwargs):
        return None


class _AdapterSdk:
    RET_OK = 0

    class KLType:
        K_DAY = "K_DAY"

    class AuType:
        QFQ = "QFQ"


class _AdapterQuoteContext:
    def __init__(self):
        self.snapshot_calls = 0
        self.history_calls = 0

    def get_market_snapshot(self, _codes):
        self.snapshot_calls += 1
        return 0, [{"code": "US.AAA", "last_price": 100, "prev_close_price": 99}]

    def request_history_kline(self, **_kwargs):
        self.history_calls += 1
        return 0, [{
            "time_key": "2026-09-07",
            "open": 99,
            "high": 101,
            "low": 98,
            "close": 100,
            "volume": 10,
        }]


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


def test_scheduler_releases_calendar_provider_lock_before_idle(tmp_path: Path):
    coordinator = CollectionCoordinator(tmp_path / "scheduler.lock")

    class _LockingCalendar(_SessionCalendar):
        def is_session(self, value):
            coordinator.acquire_rate_slot("calendar", 0.01, now=datetime.now(UTC))
            return False

    scheduler = CollectorScheduler(
        [],
        timezone="UTC",
        calendar=_LockingCalendar(set()),
        runner=lambda *_args: None,
        on_idle=coordinator.close,
    )

    scheduler.run_once(datetime(2026, 9, 8, 12, tzinfo=UTC))

    assert coordinator.locked is False


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


def test_quota_admission_locally_reserves_new_symbols_until_task_reset():
    service = HistoryCapacityService(minimum_bar_count=2, reserve_absolute=10, reserve_ratio=0)
    admission = HistoryAdmission(
        service,
        quota_reader=lambda: {
            "available": True,
            "used": 10,
            "remain": 11,
            "total": 21,
        },
    )
    admission.prepare_symbols(["AAA", "BBB"])

    assert admission.acquire("AAA")["admitted"] is True
    second = admission.acquire("BBB")
    assert second["admitted"] is False
    assert second["reason_code"] == "history_quota_reserve"

    admission.reset_run()
    assert admission.acquire("AAA")["admitted"] is True
    assert admission.acquire("BBB")["admitted"] is False


def test_moomoo_run_cache_reset_forces_fresh_snapshot_and_history_requests():
    context = _AdapterQuoteContext()
    adapter = OpenDMarketAdapter(
        host="unused",
        port=0,
        market_symbols=["AAA"],
        history_days=20,
        sdk=_AdapterSdk(),
        quote_context=context,
        rate_limiter=_NoopRateLimiter(),
    )

    adapter._collect_market_snapshot(["US.AAA"])
    adapter._history_summary("US.AAA", previous_close=99, include_bars=True)
    adapter._collect_market_snapshot(["US.AAA"])
    adapter._history_summary("US.AAA", previous_close=99, include_bars=True)
    assert context.snapshot_calls == 1
    assert context.history_calls == 1

    adapter.reset_collection_run()
    adapter._collect_market_snapshot(["US.AAA"])
    adapter._history_summary("US.AAA", previous_close=99, include_bars=True)
    assert context.snapshot_calls == 2
    assert context.history_calls == 2


def test_eoflora_eod_selects_every_actual_expiration_from_1_through_45_dte():
    import pandas as pd

    frame = pd.DataFrame(
        [
            {"strike_time": "2026-09-08", "option_expiry_date_distance": 0},
            {"strike_time": "2026-09-09", "option_expiry_date_distance": 1},
            {"strike_time": "2026-09-18", "option_expiry_date_distance": 10},
            {"strike_time": "2026-10-23", "option_expiry_date_distance": 45},
            {"strike_time": "2026-10-24", "option_expiry_date_distance": 46},
        ]
    )

    selected = MoomooIntradayOptionsAdapter._select_intraday_expirations(
        frame, "eod", max_dte=45
    )

    assert selected == [
        ("2026-09-09", 1),
        ("2026-09-18", 10),
        ("2026-10-23", 45),
    ]


def test_eoflora_eod_contract_is_ready_only_with_complete_model_fields(monkeypatch):
    adapter = object.__new__(MoomooIntradayOptionsAdapter)
    captured: dict[str, object] = {}

    def capture_intraday(**kwargs):
        captured.update(kwargs)
        def contracts(symbol):
            return [
                {
                    "contract_code": f"US.{symbol}260909C00100000",
                    "option_type": "CALL",
                    "expiration": "2026-09-09",
                    "strike": 100.0,
                    "bid": 1.0,
                    "ask": 1.1,
                    "open_interest": 20,
                    "implied_volatility": 0.0,
                    "delta": 0.0,
                    "gamma": 0.0,
                    "volume": 2,
                    "quote_time": "2026-09-08 16:59:59",
                    "request_started_at": "2026-09-08T21:00:00+00:00",
                    "received_at": "2026-09-08T21:00:01+00:00",
                },
                {
                    "contract_code": f"US.{symbol}260909P00100000",
                    "option_type": "PUT",
                    "expiration": "2026-09-09",
                    "strike": 100.0,
                    "bid": 0.9,
                    "ask": 1.0,
                    "open_interest": 25,
                    "implied_volatility": 21.0,
                    "delta": -0.5,
                    "gamma": 0.03,
                    "volume": 3,
                    "quote_time": "2026-09-08 16:59:59",
                    "request_started_at": "2026-09-08T21:00:00+00:00",
                    "received_at": "2026-09-08T21:00:01+00:00",
                },
            ]
        requested = kwargs["symbols"]
        return {
            "schema_version": "moomoo-intraday-options-v2",
            "provider": "moomoo_openapi",
            "captured_at": "2026-09-08T21:00:01+00:00",
            "trading_date": "2026-09-08",
            "symbols": [
                {
                    "symbol": symbol,
                    "spot": 100.0,
                    "expirations": [
                        {"expiration": "2026-09-09", "contracts": contracts(symbol)}
                    ],
                }
                for symbol in requested
            ],
            "underlying_bars": [
                {"symbol": symbol, "bar_time": "2026-09-08 15:59:00", "close": 100.0}
                for symbol in requested
            ],
            "quality_summary": {"complete": True},
        }

    monkeypatch.setattr(adapter, "capture_intraday", capture_intraday)

    result = adapter.capture_eod_options(
        symbols=["SPY", "QQQ", "IWM"], trading_date=date(2026, 9, 8)
    )

    assert captured["capture_tier"] == "eod"
    assert captured["band_percent"] == 10.0
    assert captured["max_dte"] == 45
    assert result["schema_version"] == "urus.moomoo.eoflora-eod.v1"
    assert result["dte_min"] == 1
    assert result["dte_max"] == 45
    assert "symbols" not in result
    assert result["eoflora_option_rows"][0]["type"] == "call"
    assert result["eoflora_option_rows"][0]["implied_volatility"] > 0
    assert result["eoflora_option_rows"][0]["iv_source"] == "calculated_mid"
    assert result["eoflora_option_rows"][0]["delta"] > 0
    assert result["eoflora_option_rows"][0]["gamma"] > 0
    assert result["eoflora_option_rows"][1]["implied_volatility"] == 0.21
    assert result["eoflora_option_rows"][1]["iv_source"] == "moomoo"
    assert result["quality_summary"]["iv_backfill_attempted"] == 3
    assert result["quality_summary"]["iv_backfill_succeeded"] == 3
    expected_content = {
        "trading_date": "2026-09-08",
        "requested_symbols": ["IWM", "QQQ", "SPY"],
        "dte_range": [1, 45],
        "strike_band_percent": 10.0,
        "underlying_closes": result["underlying_closes"],
        "option_rows": result["eoflora_option_rows"],
    }
    expected_digest = hashlib.sha256(
        json.dumps(
            expected_content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert result["content_sha256"] == expected_digest
    assert result["underlying_closes"] == {"SPY": 100.0, "QQQ": 100.0, "IWM": 100.0}
    assert len(result["content_sha256"]) == 64
    assert result["ready"] is True
    assert result["complete"] is True
    invalid = dict(result["eoflora_option_rows"][0], gamma=None)
    assert adapter._valid_eoflora_row(invalid) is False


def test_eoflora_eod_iv_backfill_rejects_midpoint_below_no_arbitrage_bound():
    adapter = object.__new__(MoomooIntradayOptionsAdapter)

    calculated = adapter._calculate_eod_iv_and_greeks(
        row={"type": "call", "strike": 80.0, "bid": 19.0, "ask": 19.5},
        spot=100.0,
        trading_date=date(2026, 9, 8),
        expiration="2026-09-09",
    )

    assert calculated is None


def test_sqlite_storage_and_contract_boundary(tmp_path: Path):
    with CollectorStore(tmp_path / "collector.sqlite") as store:
        store.write_raw("capture-1", "daily", {"value": 1}, schema_version="urus.moomoo.daily.v1")
        store.write_daily_bars([{"symbol": "AAA", "date": "2026-01-02", "close": 1}])
        quota = HistoryCapacityService(store=store)
        quota.capture(lambda: {"available": True, "used": 1, "remain": 2, "total": 3})
        assert store.latest_quota_snapshot() is not None
        assert store.read_raw("capture-1")["kind"] == "daily"
    assert SCHEMA_VERSION == 3


def test_canonical_intraday_store_materializes_consumer_ready_snapshot(tmp_path: Path):
    from app.intraday_options.storage import IntradayOptionStore

    database = tmp_path / "intraday.sqlite"
    bootstrap = IntradayOptionStore(f"sqlite:///{database}", tmp_path / "bootstrap-raw")
    bootstrap.close()
    payload = {
        "captured_at": "2026-09-08T14:00:02+00:00",
        "quality_summary": {"complete": True},
        "warnings": [],
        "underlying_bars": [],
        "symbols": [
            {
                "symbol": symbol,
                "spot": 100.0,
                "spot_time": "2026-09-08 10:00:00",
                "spot_request_started_at": "2026-09-08T14:00:00+00:00",
                "spot_received_at": "2026-09-08T14:00:01+00:00",
                "expirations": [
                    {
                        "expiration": "2026-09-09",
                        "contracts": [
                            {
                                "contract_code": f"US.{symbol}260909C00100000",
                                "option_type": "CALL",
                                "expiration": "2026-09-09",
                                "strike": 100,
                                "bid": 1.0,
                                "ask": 1.1,
                                "quote_time": "2026-09-08 10:00:00",
                                "request_started_at": "2026-09-08T14:00:00+00:00",
                                "received_at": "2026-09-08T14:00:01+00:00",
                            }
                        ],
                    }
                ],
            }
            for symbol in ("SPY", "QQQ", "IWM")
        ],
    }
    store = CanonicalIntradayStore(
        database,
        raw_payload_dir=tmp_path / "raw",
        market_timezone="America/New_York",
    )
    try:
        result = store.persist(
            scheduled_at=datetime(2026, 9, 8, 14, tzinfo=UTC),
            capture_tier="structure",
            symbols=["SPY", "QQQ", "IWM"],
            band_percent=10,
            payload=payload,
        )
        repeated = store.persist(
            scheduled_at=datetime(2026, 9, 8, 14, tzinfo=UTC),
            capture_tier="structure",
            symbols=["SPY", "QQQ", "IWM"],
            band_percent=10,
            payload=payload,
        )
        connection = store.connection
        run = connection.execute(
            "SELECT status, symbols_completed, selected_attempt_id FROM intraday_option_capture_runs"
        ).fetchone()
        underlying_count = connection.execute(
            "SELECT COUNT(*) FROM intraday_underlying_snapshots"
        ).fetchone()[0]
        option_count = connection.execute(
            "SELECT COUNT(*) FROM intraday_option_contract_snapshots"
        ).fetchone()[0]
    finally:
        store.close()

    assert repeated == result
    assert run["status"] == "succeeded"
    assert json.loads(run["symbols_completed"]) == ["IWM", "QQQ", "SPY"]
    assert run["selected_attempt_id"] == result["attempt_id"]
    assert underlying_count == 3
    assert option_count == 3


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


def test_scheduler_retries_exceptions_and_incomplete_results(tmp_path: Path, caplog):
    session = date(2026, 7, 6)
    source = _ProviderCalendar([{"time": session}])
    calendar = MoomooTradingCalendar(source, _SessionCalendar({session}))
    tasks = [
        CollectionTask("provider-error", True, "14:00", ("AAA",)),
        CollectionTask("incomplete", True, "14:00", ("BBB",)),
    ]
    attempts = {task.name: 0 for task in tasks}

    def runner(task, _scheduled_at):
        attempts[task.name] += 1
        if attempts[task.name] == 1 and task.name == "provider-error":
            raise RuntimeError("temporary provider failure")
        if attempts[task.name] == 1:
            return {"completed": False}
        return None

    store = CollectorStore(tmp_path / "retry.sqlite")
    scheduler = CollectorScheduler(
        tasks,
        timezone="UTC",
        calendar=calendar,
        runner=runner,
        store=store,
        catch_up_seconds=600,
    )
    scheduled_at = datetime(2026, 7, 6, 14, 0, tzinfo=UTC)
    try:
        assert scheduler.run_once(datetime(2026, 7, 6, 14, 1, tzinfo=UTC)) == []
        assert attempts == {"provider-error": 1, "incomplete": 1}
        assert not store.scheduler_completed("provider-error", scheduled_at)
        assert not store.scheduler_completed("incomplete", scheduled_at)
        assert "scheduled task failed" in caplog.text

        assert scheduler.run_once(datetime(2026, 7, 6, 14, 2, tzinfo=UTC)) == [
            "provider-error",
            "incomplete",
        ]
        assert store.scheduler_completed("provider-error", scheduled_at)
        assert store.scheduler_completed("incomplete", scheduled_at)
    finally:
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
    assert set(due[0].task.params) == {"capture_tier", "band_percent"}


def test_intraday_anchor_is_compared_in_market_timezone():
    session = date(2026, 1, 6)
    source = _ProviderCalendar([{"time": session}])
    calendar = MoomooTradingCalendar(source, _SessionCalendar({session}))
    task = CollectionTask(
        name="tokyo-intraday",
        enabled=True,
        schedule="intraday",
        symbols=("TEST_ASSET",),
        operation="capture_intraday",
        params={
            "interval_seconds": 300,
            "start_margin_seconds": 300,
            "end_margin_seconds": 300,
            "anchor_times": ["09:35"],
        },
    )
    scheduler = CollectorScheduler(
        [task],
        timezone="Asia/Tokyo",
        market_timezone="America/New_York",
        calendar=calendar,
        runner=lambda *_: None,
    )

    due = scheduler.due(datetime(2026, 1, 6, 23, 36, tzinfo=ZoneInfo("Asia/Tokyo")))
    assert len(due) == 1
    assert due[0].scheduled_at.tzinfo is not None
    assert due[0].scheduled_at.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M") == "09:35"
    assert due[0].task.params["capture_tier"] == "structure"


class _RuntimeProvider:
    def __init__(self):
        self.calls = []
        self.reset_calls = 0

    def reset_collection_run(self):
        self.reset_calls += 1

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
    assert provider.reset_calls == 2


def test_runtime_maps_capture_quality_to_scheduler_completion(tmp_path: Path):
    class _QualityProvider:
        def capture_intraday(self, *, symbols, **_params):
            return {"symbols": symbols, "quality_summary": {"complete": False}}

        def capture_session_close(self, *, symbols, trading_date, **_params):
            return {
                "requested_symbols": symbols,
                "trading_date": trading_date.isoformat(),
                "complete": True,
            }

    tasks = (
        CollectionTask(
            "intraday-quality",
            True,
            "12:34",
            ("AAA",),
            "capture_intraday",
            params={"capture_tier": "fast", "band_percent": 3},
        ),
        CollectionTask("session-close", True, "12:35", ("AAA",), "capture_session_close"),
    )
    from urus_moomoo.config import CollectorConfig

    runtime = CollectorRuntime(
        _QualityProvider(),
        CollectorConfig(
            timezone="UTC",
            market_timezone="America/New_York",
            output_dir=tmp_path,
            tasks=tasks,
        ),
    )
    try:
        incomplete = runtime.collect_task(tasks[0], datetime(2026, 7, 6, 12, 34, tzinfo=UTC))
        complete = runtime.collect_task(tasks[1], datetime(2026, 7, 6, 12, 35, tzinfo=UTC))
    finally:
        runtime.close()

    assert incomplete["completed"] is False
    assert complete["completed"] is True
