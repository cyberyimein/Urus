from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from scripts.schedule_market_data_collection import due_slots, slot_key, slot_policy


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
