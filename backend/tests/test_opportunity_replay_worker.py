import multiprocessing
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.decision_harness.market_evidence import DailyMarketEvidenceService
from app.repositories.daily_evidence import DailyEvidenceRepository
from app.services.capital_flow import completed_session_dates
from app.services.opportunity_view_replay import (
    OpportunityReplayBusy, OpportunityReplayCancelled, OpportunityReplayCoordinator,
    OpportunityReplayTimeout, _readonly_database,
)


def _args(database_url, dataset_id="dataset"):
    return dict(
        database_url=database_url, dataset_id=dataset_id, symbol="AAA",
        viewed_at=datetime(2026, 10, 4, tzinfo=UTC), market_timezone="America/New_York",
        market_calendar="XNYS", daily_min_history_bars=20,
    )


def test_real_worker_reads_minimal_chart_and_exits_with_readonly_database(client, app):
    cutoff = datetime(2026, 8, 24, 23, tzinfo=UTC)
    days = completed_session_dates(cutoff, "XNYS", count=20)
    with app.state.session_factory() as session:
        DailyEvidenceRepository(session).upsert_bars([
            dict(symbol="AAA", date=day.isoformat(), open=100, high=102, low=99, close=101, volume=1_000_000)
            for day in days
        ], source="fixture", collected_at=cutoff)
        evidence = DailyMarketEvidenceService(session, app.state.settings).freeze(
            scope_type="instrument", scope_id="AAA", symbols=["AAA"], cutoff_time=cutoff,
        )
    database_url = app.state.settings.database_url
    coordinator = OpportunityReplayCoordinator(timeout_seconds=10)
    payload = coordinator._run_process(**_args(database_url, evidence["dataset"]["dataset_id"]))
    assert payload["dataset_id"] == evidence["dataset"]["dataset_id"]
    assert [row["symbol"] for row in payload["views"]] == ["AAA"]
    assert not [p for p in multiprocessing.active_children() if p.name == "urus-opportunity-view-worker"]
    engine, _ = _readonly_database(database_url)
    try:
        with engine.connect() as connection:
            with pytest.raises(OperationalError, match="readonly"):
                connection.execute(text("UPDATE daily_bars SET close = 0"))
    finally:
        engine.dispose()


def test_timed_out_worker_is_reaped(client, app):
    coordinator = OpportunityReplayCoordinator(timeout_seconds=0.001)
    with pytest.raises(OpportunityReplayTimeout):
        coordinator._run_process(**_args(app.state.settings.database_url))
    assert not [p for p in multiprocessing.active_children() if p.name == "urus-opportunity-view-worker"]


def test_cancelled_worker_is_reaped(client, app):
    coordinator = OpportunityReplayCoordinator(timeout_seconds=10)
    with pytest.raises(OpportunityReplayCancelled):
        coordinator._run_process(**_args(app.state.settings.database_url), should_cancel=lambda: True)
    assert not [p for p in multiprocessing.active_children() if p.name == "urus-opportunity-view-worker"]


def test_last_waiter_cancellation_stops_work_and_releases_capacity(monkeypatch):
    coordinator = OpportunityReplayCoordinator(max_workers=1)
    stopped = threading.Event()

    def run_process(*, should_cancel, dataset_id, **kwargs):
        if dataset_id == "next":
            return {"result": "next"}
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if should_cancel():
                stopped.set()
                raise OpportunityReplayCancelled("cancelled")
            time.sleep(0.01)
        raise AssertionError("last caller's cancellation did not reach the worker")

    monkeypatch.setattr(coordinator, "_run_process", run_process)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(OpportunityReplayCancelled):
        coordinator.run(key=("cancel",), cancel_event=cancel, **_args("unused"))
    assert stopped.wait(2)
    deadline = time.monotonic() + 2
    while coordinator.inflight_count and time.monotonic() < deadline:
        time.sleep(0.01)
    assert coordinator.inflight_count == 0
    assert coordinator.run(key=("next",), **_args("unused", "next")) == {"result": "next"}


def test_single_flight_and_capacity_release_after_callers_cancel(monkeypatch):
    coordinator = OpportunityReplayCoordinator(max_workers=1)
    started = threading.Event()
    finish = threading.Event()
    calls = []

    def run_process(*, should_cancel, **kwargs):
        calls.append(kwargs)
        started.set()
        while not finish.wait(0.01):
            if should_cancel():
                raise OpportunityReplayCancelled("cancelled")
        return {"result": "shared"}

    monkeypatch.setattr(coordinator, "_run_process", run_process)
    cancel = threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(coordinator.run, key=("same",), cancel_event=cancel, **_args("unused"))
        assert started.wait(2)
        second = pool.submit(coordinator.run, key=("same",), **_args("unused"))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with coordinator._lock:
                if coordinator._inflight[("same",)].waiters == 2:
                    break
            time.sleep(0.01)
        else:
            pytest.fail("second caller did not join single-flight")
        with pytest.raises(OpportunityReplayBusy):
            coordinator.run(key=("other",), **_args("unused"))
        cancel.set()
        with pytest.raises(OpportunityReplayCancelled):
            first.result(timeout=2)
        # The remaining caller keeps the useful work alive.
        finish.set()
        assert second.result(timeout=2) == {"result": "shared"}
    assert len(calls) == 1
    deadline = time.monotonic() + 2
    while coordinator.inflight_count and time.monotonic() < deadline:
        time.sleep(0.01)
    assert coordinator.inflight_count == 0
    assert coordinator.run(key=("next",), **_args("unused")) == {"result": "shared"}
