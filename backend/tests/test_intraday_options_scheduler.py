from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from scripts.schedule_intraday_options_collection import (
    capture_slots,
    due_capture_slots,
    exchange_calendar,
)


NEW_YORK = ZoneInfo("America/New_York")


def test_full_session_has_77_slots_and_priority_structure_anchors() -> None:
    slots = capture_slots(date(2026, 9, 4), exchange_calendar("XNYS"), NEW_YORK)

    assert len(slots) == 77
    structure_times = [item[0].strftime("%H:%M") for item in slots if item[1] == "structure"]
    assert structure_times == ["09:35", "10:00", "12:30", "15:00", "15:30", "15:50"]
    assert slots[-1][0].strftime("%H:%M") == "15:55"


def test_early_close_stops_five_minutes_before_actual_close() -> None:
    slots = capture_slots(date(2026, 11, 27), exchange_calendar("XNYS"), NEW_YORK)

    assert slots[-1][0].strftime("%H:%M") == "12:55"
    assert all(item[0].strftime("%H:%M") not in {"15:00", "15:30", "15:50"} for item in slots)


def test_due_slots_never_backfill_an_old_option_chain() -> None:
    now = datetime(2026, 9, 4, 10, 7, tzinfo=NEW_YORK)
    due = due_capture_slots(
        now,
        exchange_calendar("XNYS"),
        NEW_YORK,
        catch_up=timedelta(minutes=4),
    )

    assert [(item[0].strftime("%H:%M"), item[1]) for item in due] == [("10:05", "fast")]
