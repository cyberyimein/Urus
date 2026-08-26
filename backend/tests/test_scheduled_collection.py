from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from scripts.schedule_market_data_collection import collect, due_slots, slot_key, slot_policy


TOKYO = ZoneInfo("Asia/Tokyo")
NEW_YORK = ZoneInfo("America/New_York")


def test_due_slots_use_tokyo_clock_and_keep_saturday_morning_for_us_friday() -> None:
    now = datetime(2026, 8, 8, 5, 31, tzinfo=TOKYO)  # Saturday JST, Friday ET

    due = due_slots(now, {}, timedelta(minutes=180), NEW_YORK, False)

    assert [(item[0].strftime("%Y-%m-%d %H:%M"), item[1]) for item in due] == [
        ("2026-08-08 04:00", "pre_close"),
        ("2026-08-08 05:30", "post_close_review"),
    ]


def test_due_slots_skip_us_weekend_and_completed_slot() -> None:
    now = datetime(2026, 8, 9, 5, 31, tzinfo=TOKYO)  # Sunday JST, Saturday ET
    completed = {
        slot_key(datetime(2026, 8, 9, 4, 0, tzinfo=TOKYO), "pre_close"): {"status": "mixed"}
    }

    assert due_slots(now, completed, timedelta(minutes=180), NEW_YORK, False) == []


def test_due_slots_skip_nyse_holiday_even_when_jst_slot_is_a_weekday() -> None:
    # 2026-07-03 is the observed Independence Day holiday. Its 04:00/05:30
    # JST candidates map to Friday afternoon ET, so a weekend-only filter
    # would incorrectly run them.
    now = datetime(2026, 7, 4, 5, 31, tzinfo=TOKYO)

    assert due_slots(now, {}, timedelta(minutes=180), NEW_YORK, False) == []
    assert due_slots(now, {}, timedelta(minutes=180), NEW_YORK, True) == []


def test_due_slots_adjust_nyse_early_close_slots() -> None:
    # The Friday after Thanksgiving closes at 13:00 ET. The nominal 04:00
    # JST tail slot is therefore moved to one hour before close, while the
    # post-close review remains after the actual close.
    now = datetime(2026, 11, 28, 5, 31, tzinfo=TOKYO)

    due = due_slots(now, {}, timedelta(hours=4), NEW_YORK, False)

    assert [(item[0].strftime("%Y-%m-%d %H:%M"), item[1]) for item in due] == [
        ("2026-11-28 02:00", "pre_close"),
        ("2026-11-28 05:30", "post_close_review"),
    ]


def test_due_slots_only_catch_up_inside_configured_window() -> None:
    now = datetime(2026, 8, 4, 23, 0, tzinfo=TOKYO)

    due = due_slots(now, {}, timedelta(minutes=60), NEW_YORK, False)

    assert due == []


def test_due_slots_apply_runtime_schedule_switches() -> None:
    now = datetime(2026, 8, 8, 5, 31, tzinfo=TOKYO)
    schedule = {
        "pre_close": {"enabled": True, "skip_ai_decision": False},
        "post_close_review": {"enabled": False, "skip_ai_decision": False},
        "pre_market": {"enabled": True, "skip_ai_decision": True},
    }

    due = due_slots(now, {}, timedelta(minutes=180), NEW_YORK, False, schedule)

    assert [(item[1], item[2]) for item in due] == [("pre_close", "尾盘前")]
    assert slot_policy(schedule, "pre_close") == (True, True)
    assert slot_policy(schedule, "pre_market") == (True, True)
    assert slot_policy(schedule, "pre_market", ai_decision_enabled=False) == (True, True)


def test_post_close_slot_syncs_universe_and_creates_deterministic_observation(monkeypatch) -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def fake_request(url: str, *, method: str = "GET", payload=None, timeout: float = 10.0):
        calls.append((url, method, payload))
        if url.endswith("/observation/groups/sync"):
            return {
                "source": "stale",
                "group_count": 4,
                "symbol_count": 27,
                "universe_revision_id": "revision-1",
                "universe_freshness": "stale",
                "source_url": "https://deployed.example/api",
            }
        if url.endswith("/observation/runs"):
            return {"run_id": "observation-1", "status": "succeeded"}
        raise AssertionError(f"unexpected scheduler request: {url}")

    monkeypatch.setattr("scripts.schedule_market_data_collection.request_json", fake_request)

    result = collect("http://urus.test/api", "post_close_review", timeout=30)

    assert result["run_id"] == "observation-1"
    assert result["universe_sync"]["symbol_count"] == 27
    assert calls == [
        ("http://urus.test/api/observation/groups/sync", "POST", {}),
        (
            "http://urus.test/api/observation/runs",
            "POST",
            {
                "trigger_mode": "scheduled",
                "universe_revision_id": "revision-1",
                "universe_freshness": "stale",
                "universe_source_url": "https://deployed.example/api",
            },
        ),
    ]
