from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from scripts.schedule_market_data_collection import collect, due_slots, slot_key


TOKYO = ZoneInfo("Asia/Tokyo")
NEW_YORK = ZoneInfo("America/New_York")


def test_due_slots_use_tokyo_clock_for_us_friday_close() -> None:
    now = datetime(2026, 8, 8, 5, 31, tzinfo=TOKYO)

    due = due_slots(now, {}, timedelta(minutes=180), NEW_YORK, False)

    assert [(item[0].strftime("%Y-%m-%d %H:%M"), item[1]) for item in due] == [
        ("2026-08-08 05:30", "post_close_observation"),
    ]


def test_due_slots_skip_us_weekend_and_completed_slot() -> None:
    now = datetime(2026, 8, 9, 5, 31, tzinfo=TOKYO)
    completed = {
        slot_key(datetime(2026, 8, 8, 5, 30, tzinfo=TOKYO), "post_close_observation"): {
            "status": "succeeded"
        }
    }

    assert due_slots(now, completed, timedelta(minutes=180), NEW_YORK, False) == []


def test_due_slots_skip_nyse_holiday_even_when_jst_slot_is_a_weekday() -> None:
    now = datetime(2026, 7, 4, 5, 31, tzinfo=TOKYO)

    assert due_slots(now, {}, timedelta(minutes=180), NEW_YORK, False) == []
    assert due_slots(now, {}, timedelta(minutes=180), NEW_YORK, True) == []


def test_due_slots_keep_post_close_observation_after_early_close() -> None:
    now = datetime(2026, 11, 28, 5, 31, tzinfo=TOKYO)

    due = due_slots(now, {}, timedelta(hours=4), NEW_YORK, False)

    assert [(item[0].strftime("%Y-%m-%d %H:%M"), item[1]) for item in due] == [
        ("2026-11-28 05:30", "post_close_observation"),
    ]


def test_due_slots_skip_candidates_outside_catch_up_window() -> None:
    now = datetime(2026, 8, 4, 23, 0, tzinfo=TOKYO)

    assert due_slots(now, {}, timedelta(minutes=60), NEW_YORK, False) == []


def test_due_slots_apply_runtime_collection_switch() -> None:
    now = datetime(2026, 8, 8, 5, 31, tzinfo=TOKYO)
    schedule = {"post_close_observation": {"enabled": False}}

    assert due_slots(now, {}, timedelta(minutes=180), NEW_YORK, False, schedule) == []


def test_post_close_slot_syncs_universe_and_creates_observation(monkeypatch) -> None:
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

    result = collect("http://urus.test/api", timeout=30)

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


def test_collect_can_pin_the_provider_completed_trading_date(monkeypatch) -> None:
    payloads: list[dict | None] = []

    def fake_request(url: str, *, method: str = "GET", payload=None, timeout: float = 10.0):
        del method, timeout
        if url.endswith("/observation/groups/sync"):
            return {"universe_revision_id": "revision-1"}
        payloads.append(payload)
        return {"run_id": "observation-1", "status": "succeeded"}

    monkeypatch.setattr("scripts.schedule_market_data_collection.request_json", fake_request)

    collect("http://urus.test/api", timeout=30, trading_date=date(2026, 8, 7))

    assert payloads == [
        {
            "trigger_mode": "scheduled",
            "trading_date": "2026-08-07",
            "universe_revision_id": "revision-1",
        }
    ]
