from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from urus_moomoo.config import CollectionTask
from urus_moomoo.services.scheduler import CollectorScheduler
from urus_moomoo.storage.sqlite import CollectorStore


_MARKET_TZ = ZoneInfo("America/New_York")
_TRADING_DATE = datetime(2026, 10, 15).date()
_CUTOFF = datetime(2026, 10, 15, 9, 35, tzinfo=_MARKET_TZ)


class _SessionCalendar:
    def is_session(self, _value):
        return True

    def session_open(self, value):
        return datetime(value.year, value.month, value.day, 9, 30, tzinfo=_MARKET_TZ)

    def session_close(self, value):
        return datetime(value.year, value.month, value.day, 16, 0, tzinfo=_MARKET_TZ)


def _windowed_task(*, end_margin_seconds: float = 300) -> CollectionTask:
    return CollectionTask(
        "equity-intraday",
        True,
        "intraday",
        ("SPY", "QQQ", "SMH", "AAA"),
        "capture_intraday_equities",
        params={
            "interval_seconds": 300,
            "start_margin_seconds": 300,
            "end_margin_seconds": end_margin_seconds,
            "equity_shards_enabled": True,
            "equity_shard_symbols": {
                "G1": ["SPY", "QQQ"],
                "G2": ["SMH"],
                "G3": ["AAA"],
            },
        },
    )


def _scheduler(task: CollectionTask) -> CollectorScheduler:
    return CollectorScheduler(
        [task],
        timezone="America/New_York",
        calendar=_SessionCalendar(),
        runner=lambda *_args: None,
        clock=lambda: _CUTOFF,
        intraday_catch_up_seconds=600,
        market_timezone="America/New_York",
    )


def test_opt_in_equity_shard_windows_share_one_bar_cutoff() -> None:
    scheduler = _scheduler(_windowed_task())
    now = _CUTOFF + timedelta(seconds=120)

    due = scheduler.due(now)

    assert len(due) == 1
    assert due[0].scheduled_at == _CUTOFF + timedelta(seconds=120)
    assert due[0].task.params["shard_id"] == "G2"
    assert due[0].task.params["slot_cutoff_at"] == _CUTOFF.isoformat()
    assert due[0].task.params["shard_symbols"] == ["SMH"]


@pytest.mark.parametrize(
    ("seconds_after_cutoff", "expected_shard"),
    [
        (59, None),
        (60, "G1"),
        (104, "G1"),
        (105, "G1"),
        (120, "G2"),
        (179, "G2"),
        (180, "G3"),
        (239, "G3"),
        (240, "finalize"),
        (300, None),
    ],
)
def test_opt_in_equity_shards_never_catch_up_across_windows(
    seconds_after_cutoff: int,
    expected_shard: str | None,
) -> None:
    scheduler = _scheduler(_windowed_task())

    due = scheduler.due(_CUTOFF + timedelta(seconds=seconds_after_cutoff))

    if expected_shard is None:
        assert due == []
    elif expected_shard == "finalize":
        assert len(due) == 1
        assert due[0].task.operation == "finalize_intraday_equities"
        assert due[0].task.params["slot_cutoff_at"] == _CUTOFF.isoformat()
    else:
        assert [item.task.params["shard_id"] for item in due] == [expected_shard]
        if seconds_after_cutoff == 105:
            assert due[0].task.params["request_budget_deadline_at"] == (
                _CUTOFF + timedelta(seconds=105)
            ).isoformat()


def test_windowed_equity_schedule_includes_last_close_bar_without_moving_eoflora() -> None:
    equity = _windowed_task(end_margin_seconds=300)
    eoflora = CollectionTask(
        "options-intraday",
        True,
        "intraday",
        ("SPY",),
        "capture_intraday",
        params={"interval_seconds": 300, "start_margin_seconds": 300, "end_margin_seconds": 300},
    )
    scheduler = CollectorScheduler(
        [equity, eoflora],
        timezone="America/New_York",
        calendar=_SessionCalendar(),
        runner=lambda *_args: None,
        market_timezone="America/New_York",
    )

    equity_times = scheduler._task_times(equity, _TRADING_DATE)
    eoflora_times = scheduler._task_times(eoflora, _TRADING_DATE)

    assert equity_times[0][0] == datetime(2026, 10, 15, 9, 36, tzinfo=_MARKET_TZ)
    assert equity_times[-1][0] == datetime(2026, 10, 15, 16, 4, tzinfo=_MARKET_TZ)
    assert eoflora_times[0][0] == datetime(2026, 10, 15, 9, 35, tzinfo=_MARKET_TZ)
    assert eoflora_times[-1][0] == datetime(2026, 10, 15, 15, 55, tzinfo=_MARKET_TZ)


def test_windowed_schedule_builds_one_quote_pass_three_minute_shards_and_finalizer_per_slot() -> None:
    scheduled = _scheduler(_windowed_task())._task_times(_windowed_task(), _TRADING_DATE)

    # G1 combines the slot's single quote pass with its first minute shard.
    assert len(scheduled) == 78 * 4
    first_slot = scheduled[:4]
    assert [task.name.rsplit(":", 1)[-1] for _, task in first_slot] == [
        "G1",
        "G2",
        "G3",
        "finalize",
    ]
    assert [task.operation for _, task in first_slot] == [
        "capture_intraday_equities",
        "capture_intraday_equities",
        "capture_intraday_equities",
        "finalize_intraday_equities",
    ]
    assert first_slot[0][1].params["capture_quotes"] is True
    assert first_slot[1][1].params["capture_quotes"] is False
    assert first_slot[2][1].params["capture_quotes"] is False
    assert [task.params["shard_symbols"] for _, task in first_slot[:3]] == [
        ["SPY", "QQQ"],
        ["SMH"],
        ["AAA"],
    ]
    assert [scheduled_at for scheduled_at, _ in first_slot] == [
        _CUTOFF + timedelta(seconds=60),
        _CUTOFF + timedelta(seconds=120),
        _CUTOFF + timedelta(seconds=180),
        _CUTOFF + timedelta(seconds=240),
    ]


@pytest.mark.parametrize(
    ("session_date", "expected"),
    [
        (date(2026, 3, 6), datetime(2026, 3, 6, 8, 0, tzinfo=ZoneInfo("America/New_York"))),
        (date(2026, 3, 9), datetime(2026, 3, 9, 8, 0, tzinfo=ZoneInfo("America/New_York"))),
    ],
)
def test_pre_open_schedule_tracks_local_exchange_open_across_dst(session_date, expected) -> None:
    task = CollectionTask(
        "equity-maintenance",
        True,
        "pre-open",
        ("SPY",),
        "maintain_intraday_equities",
        delay_seconds=90 * 60,
    )
    scheduler = CollectorScheduler(
        [task],
        timezone="America/New_York",
        calendar=_SessionCalendar(),
        runner=lambda *_args: None,
        market_timezone="America/New_York",
    )

    scheduled_at = scheduler._task_times(task, session_date)[0][0]

    assert scheduled_at == expected
    assert scheduled_at.utcoffset() == expected.utcoffset()


def test_pre_open_schedule_uses_exchange_open_on_a_half_day() -> None:
    class _HalfDayCalendar(_SessionCalendar):
        def session_close(self, value):
            return datetime(value.year, value.month, value.day, 13, 0, tzinfo=_MARKET_TZ)

    task = CollectionTask(
        "equity-maintenance",
        True,
        "pre-open",
        ("SPY",),
        "maintain_intraday_equities",
        delay_seconds=90 * 60,
    )
    scheduler = CollectorScheduler(
        [task],
        timezone="America/New_York",
        calendar=_HalfDayCalendar(),
        runner=lambda *_args: None,
        market_timezone="America/New_York",
    )

    scheduled_at = scheduler._task_times(task, date(2026, 11, 27))[0][0]

    assert scheduled_at == datetime(2026, 11, 27, 8, 0, tzinfo=_MARKET_TZ)


def test_pre_open_maintenance_is_not_due_at_the_exact_thirty_minute_cutoff() -> None:
    task = CollectionTask(
        "equity-maintenance",
        True,
        "pre-open",
        ("SPY",),
        "maintain_intraday_equities",
        delay_seconds=90 * 60,
    )
    scheduler = CollectorScheduler(
        [task],
        timezone="America/New_York",
        calendar=_SessionCalendar(),
        runner=lambda *_args: None,
        market_timezone="America/New_York",
        catch_up_seconds=2 * 60 * 60,
    )

    exact_cutoff = datetime(2026, 10, 15, 9, 0, tzinfo=_MARKET_TZ)
    assert scheduler.due(exact_cutoff) == []


def test_windowed_equity_requires_an_exact_three_shard_partition() -> None:
    invalid = _windowed_task()
    invalid.params["equity_shard_symbols"] = {"G1": ["SPY"], "G2": ["SMH"]}
    scheduler = _scheduler(invalid)

    with pytest.raises(ValueError, match="G1, G2, and G3"):
        scheduler._task_times(invalid, _TRADING_DATE)


def test_completed_shard_is_not_replayed_on_next_tick_or_scheduler_restart(tmp_path) -> None:
    store = CollectorStore(tmp_path / "collector.sqlite")
    calls: list[str] = []
    now = _CUTOFF + timedelta(seconds=61)

    def runner(task, _scheduled_at):
        calls.append(task.name)
        return {"completed": True}

    try:
        first = CollectorScheduler(
            [_windowed_task()],
            timezone="America/New_York",
            calendar=_SessionCalendar(),
            runner=runner,
            store=store,
            clock=lambda: now,
            market_timezone="America/New_York",
        )
        assert first.run_once(now) == ["equity-intraday:G1"]
        assert first.run_once(now + timedelta(seconds=1)) == []

        restarted = CollectorScheduler(
            [_windowed_task()],
            timezone="America/New_York",
            calendar=_SessionCalendar(),
            runner=runner,
            store=store,
            clock=lambda: now + timedelta(seconds=2),
            market_timezone="America/New_York",
        )
        assert restarted.run_once(now + timedelta(seconds=2)) == []
        assert calls == ["equity-intraday:G1"]
    finally:
        store.close()


def test_eoflora_retry_runs_before_equity_and_marks_priority_skip() -> None:
    equity = _windowed_task()
    eoflora = CollectionTask(
        "options-intraday",
        True,
        "intraday",
        ("SPY",),
        "capture_intraday",
        params={"interval_seconds": 300, "start_margin_seconds": 300, "end_margin_seconds": 300},
    )
    calls: list[tuple[str, str | None]] = []

    def runner(task, _scheduled_at):
        if task.operation == "capture_intraday":
            calls.append((task.name, None))
            return {"completed": False}
        reason = dict(task.params or {}).get("skip_reason")
        calls.append((task.name, reason))
        return {"completed": True, "terminal": True}

    scheduler = CollectorScheduler(
        [equity, eoflora],
        timezone="America/New_York",
        calendar=_SessionCalendar(),
        runner=runner,
        clock=lambda: _CUTOFF + timedelta(seconds=60),
        market_timezone="America/New_York",
    )

    ran = scheduler.run_once(_CUTOFF + timedelta(seconds=60))

    assert calls == [
        ("options-intraday", None),
        ("equity-intraday:G1", "skipped_priority"),
    ]
    assert ran == ["equity-intraday:G1"]


def test_any_incomplete_due_eoflora_slot_keeps_equity_waiting() -> None:
    equity = _windowed_task()
    eoflora = CollectionTask(
        "options-intraday",
        True,
        "intraday",
        ("SPY",),
        "capture_intraday",
        params={"interval_seconds": 300, "start_margin_seconds": 300, "end_margin_seconds": 300},
    )
    current = _CUTOFF + timedelta(minutes=6)
    calls: list[tuple[str, datetime, str | None]] = []

    def runner(task, scheduled_at):
        params = dict(task.params or {})
        skip_reason = params.get("skip_reason")
        calls.append((task.operation, scheduled_at, skip_reason))
        if task.operation == "capture_intraday":
            # The 9:35 slot is still retryable and fails; the newer 9:40
            # slot succeeds. Equity T=9:40 must still yield to the retry.
            return {"completed": scheduled_at != _CUTOFF}
        return {"completed": True, "terminal": True}

    scheduler = CollectorScheduler(
        [equity, eoflora],
        timezone="America/New_York",
        calendar=_SessionCalendar(),
        runner=runner,
        clock=lambda: current,
        market_timezone="America/New_York",
    )

    ran = scheduler.run_once(current)

    assert calls == [
        ("capture_intraday", _CUTOFF, None),
        ("capture_intraday", _CUTOFF + timedelta(minutes=5), None),
        ("capture_intraday_equities", _CUTOFF + timedelta(minutes=6), "skipped_priority"),
    ]
    assert ran == ["options-intraday", "equity-intraday:G1"]


def test_equity_window_records_budget_skip_without_starting_provider_work() -> None:
    task = _windowed_task()
    calls: list[str | None] = []

    def runner(due_task, _scheduled_at):
        calls.append(dict(due_task.params or {}).get("skip_reason"))
        return {"completed": True, "terminal": True}

    scheduler = CollectorScheduler(
        [task],
        timezone="America/New_York",
        calendar=_SessionCalendar(),
        runner=runner,
        clock=lambda: _CUTOFF + timedelta(seconds=105),
        market_timezone="America/New_York",
    )

    scheduler.run_once(_CUTOFF + timedelta(seconds=105))

    assert calls == ["skipped_budget"]
