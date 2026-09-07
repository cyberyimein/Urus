from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.buy_opportunities.daily_replay import (
    _digest,
    _setup_with_status,
    replay_daily_opportunities,
    replay_daily_views,
)
from app.buy_opportunities.evidence import freeze_daily_evidence
from app.buy_opportunities.lifecycle import TradingSessionCalendar


START = date(2026, 1, 1)


def _bar(
    day: date,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    ordinal: int,
) -> dict[str, object]:
    available_at = datetime.combine(day, datetime.min.time().replace(hour=17), tzinfo=UTC)
    return {
        "date": day,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 2_000_000.0,
        "turnover": 25_000_000.0,
        "available_at": available_at,
        "collected_at": available_at,
        "source": "fixture",
        "source_revision": f"fixture-{ordinal}",
        "adjustment": "QFQ",
    }


def _history(
    *,
    extra: list[dict[str, object]] | None = None,
    count: int = 70,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(count):
        base = 100.0 if index < count - 20 else 108.0
        rows.append(
            _bar(
                START + timedelta(days=index),
                open_=base,
                high=base + 2.0,
                low=base - 2.0,
                close=base + 1.0,
                ordinal=index,
            )
        )
    rows.extend(extra or [])
    return rows


def _evidence(rows: list[dict[str, object]]):
    last_day = max(row["date"] for row in rows)
    knowledge_at = datetime.combine(
        last_day,
        datetime.min.time().replace(hour=18),
        tzinfo=UTC,
    )
    return freeze_daily_evidence(
        symbol="AAA",
        asset_type="equity",
        bars=rows,
        knowledge_at=knowledge_at,
        source_mode="historical_reconstruction",
        data_source="synthetic",
        price_basis="adjusted",
        trading_date=last_day,
    )


def _calendar(rows: list[dict[str, object]]) -> TradingSessionCalendar:
    return TradingSessionCalendar(session_dates=sorted(row["date"] for row in rows))


def test_daily_replay_builds_setup_before_current_bar_and_keeps_triggered_setup() -> None:
    trigger_day = START + timedelta(days=70)
    rows = _history(
        extra=[
            _bar(
                trigger_day,
                open_=109.0,
                high=111.0,
                low=108.0,
                close=110.5,
                ordinal=70,
            )
        ]
    )
    evidence = _evidence(rows)
    replay = replay_daily_opportunities(
        evidence,
        _calendar(rows),
        pool_version="fixture-pool",
    )

    step = replay.step_for_date(trigger_day)
    assert step is not None
    assert step.setup is not None
    assert step.setup.trigger_timeframe == "daily"
    assert step.setup.upper_trigger == 110.0
    assert step.trigger_evaluation is not None
    assert step.trigger_evaluation.status == "triggered"
    assert step.trigger is not None
    assert step.trigger.kind == "continuous_cross"
    assert step.setup.status == "resolved"
    assert replay.latest_setup is not None
    assert replay.latest_setup.setup_id == step.setup.setup_id
    assert replay.latest_setup.status == "resolved"
    assert replay.latest_trigger is not None
    assert replay.latest_trigger.trigger_id == step.trigger.trigger_id

    # A setup built with the current high would have L=111.  L=110 proves the
    # trigger bar was kept out of the structure window.
    assert all(setup.origin_scan_id in {scan.scan_id for scan in replay.scans} for setup in replay.setups)


def test_daily_replay_expires_after_fifth_complete_session_and_counts_calendar_gap() -> None:
    # Use the minimum warmup so first_day is the actual first setup's
    # valid_from.  With a longer warmup the replay quite correctly starts a
    # setup earlier, and first_day+4 would be after that setup had expired.
    history_count = 61
    first_day = START + timedelta(days=history_count)
    five_days = [
        _bar(
            first_day + timedelta(days=index),
            open_=109.0,
            high=110.0,
            low=108.0,
            close=109.0,
            ordinal=70 + index,
        )
        for index in range(5)
    ]
    rows = _history(count=history_count, extra=five_days)
    replay = replay_daily_opportunities(_evidence(rows), _calendar(rows))
    final = replay.step_for_date(first_day + timedelta(days=4))
    assert final is not None
    assert final.trigger_evaluation is not None
    assert final.trigger_evaluation.status == "expired"
    assert final.lifecycle_state.status == "expired"
    assert final.setup is not None
    assert final.setup.status == "expired"
    assert final.trigger_evaluation.reason_code == "setup_expired_after_final_session"

    # The calendar contains a session without a bar.  Session numbering still
    # advances by the authoritative calendar, while lifecycle does not advance
    # on the missing observation itself.
    gap_day = first_day + timedelta(days=2)
    gap_rows = _history(
        count=history_count,
        extra=[
            five_days[0],
            five_days[1],
            five_days[3],
            five_days[4],
        ]
    )
    gap_calendar = TradingSessionCalendar(
        session_dates=sorted(
            {
                first_day + timedelta(days=index)
                for index in range(5)
            }
            | {
                START + timedelta(days=index)
                for index in range(70)
            }
        )
    )
    gap_replay = replay_daily_opportunities(_evidence(gap_rows), gap_calendar)
    missing = gap_replay.step_for_date(gap_day)
    fourth = gap_replay.step_for_date(first_day + timedelta(days=3))
    assert missing is not None
    assert "missing_complete_session_bar" in missing.reason_codes
    assert missing.trigger_evaluation is None
    assert fourth is not None
    assert fourth.lifecycle_state.session_number == 4
    assert gap_replay.latest_setup is not None
    assert gap_replay.latest_setup.status == "expired"

    # When the next observed close crosses L after the missing session, the
    # replay must retain the calendar gap as cross_unobserved instead of
    # manufacturing a continuous cross from the last available close.
    cross_gap_rows = _history(
        count=history_count,
        extra=[
            five_days[0],
            _bar(
                first_day + timedelta(days=2),
                open_=109.0,
                high=111.0,
                low=108.0,
                close=110.5,
                ordinal=72,
            ),
        ],
    )
    cross_gap_replay = replay_daily_opportunities(
        _evidence(cross_gap_rows), gap_calendar
    )
    cross_step = cross_gap_replay.step_for_date(first_day + timedelta(days=2))
    assert cross_step is not None
    assert cross_step.trigger is not None
    assert cross_step.trigger.kind == "cross_unobserved"
    assert cross_step.trigger.previous_close is None


def test_daily_replay_processes_cross_on_final_valid_session_before_expiring() -> None:
    history_count = 61
    first_day = START + timedelta(days=history_count)
    rows = _history(
        count=history_count,
        extra=[
            *[
                _bar(
                    first_day + timedelta(days=index),
                    open_=109.0,
                    high=110.0,
                    low=108.0,
                    close=109.0,
                    ordinal=history_count + index,
                )
                for index in range(4)
            ],
            _bar(
                first_day + timedelta(days=4),
                open_=109.0,
                high=111.0,
                low=108.0,
                close=110.5,
                ordinal=history_count + 4,
            ),
        ],
    )
    replay = replay_daily_opportunities(_evidence(rows), _calendar(rows))

    final = replay.step_for_date(first_day + timedelta(days=4))
    assert final is not None
    assert final.lifecycle_state.session_number == 5
    assert final.trigger_evaluation is not None
    assert final.trigger_evaluation.status == "triggered"
    assert final.trigger is not None
    assert final.setup is not None
    assert final.setup.status == "resolved"


def test_daily_replay_rebuilds_only_after_three_post_close_sessions_and_preserves_origin() -> None:
    first_day = START + timedelta(days=70)
    rows = _history(
        extra=[
            _bar(first_day, open_=109.0, high=111.0, low=108.0, close=110.5, ordinal=70),
            _bar(first_day + timedelta(days=1), open_=109.0, high=110.0, low=108.0, close=109.0, ordinal=71),
            _bar(first_day + timedelta(days=2), open_=109.0, high=110.0, low=108.0, close=109.0, ordinal=72),
            _bar(first_day + timedelta(days=3), open_=109.0, high=115.0, low=108.0, close=109.0, ordinal=73),
            _bar(first_day + timedelta(days=4), open_=109.0, high=115.0, low=108.0, close=109.0, ordinal=74),
        ]
    )
    replay = replay_daily_opportunities(_evidence(rows), _calendar(rows))
    trigger_step = replay.step_for_date(first_day)
    second = replay.step_for_date(first_day + timedelta(days=1))
    third = replay.step_for_date(first_day + timedelta(days=2))
    fourth = replay.step_for_date(first_day + timedelta(days=3))
    assert trigger_step is not None and trigger_step.setup is not None
    assert second is not None and second.next_setup is None
    assert third is not None and third.next_setup is None
    assert fourth is not None and fourth.next_setup is not None
    assert fourth.next_setup.setup_id != trigger_step.setup.setup_id
    assert fourth.next_setup.anchor_pivot_bar_id > first_day.isoformat()
    assert fourth.next_setup.origin_scan_id in {scan.scan_id for scan in replay.scans}

    # The initial setup's origin stays fixed after later scans produce rolling
    # candidates; no current scan is silently substituted into its identity.
    initial = next(setup for setup in replay.setups if setup.setup_id == trigger_step.setup.setup_id)
    assert initial.origin_scan_id == trigger_step.setup.origin_scan_id
    assert initial.origin_scan_id != fourth.scan.scan_id

    setup_ids = {setup.setup_id for setup in replay.setups}
    scan_ids = {scan.scan_id for scan in replay.scans}
    trigger_ids = {trigger.trigger_id for trigger in replay.triggers}
    assert all(
        scan.setup_id is None or scan.setup_id in setup_ids for scan in replay.scans
    )
    assert all(setup.origin_scan_id in scan_ids for setup in replay.setups)
    assert all(trigger.setup_id in setup_ids for trigger in replay.triggers)
    assert all(
        step.scan.scan_id in scan_ids
        and all(
            setup is None or setup.setup_id in setup_ids
            for setup in (step.setup, step.next_setup, step.candidate_setup)
        )
        for step in replay.steps
    )

    # Status transitions are content-addressed from the new payload only;
    # changing a previous digest cannot alter the transitioned setup hash.
    resolved = _setup_with_status(initial, "resolved")
    resolved_payload = resolved.model_dump(mode="python")
    resolved_hash = resolved_payload.pop("content_sha256")
    assert resolved_hash == _digest(resolved_payload)
    tampered_initial = initial.model_copy(update={"content_sha256": "f" * 64})
    assert _setup_with_status(tampered_initial, "resolved").content_sha256 == resolved_hash


def test_replay_daily_views_returns_strict_view_rows_for_quality_and_core() -> None:
    first_day = START + timedelta(days=70)
    rows = _history(
        extra=[
            _bar(first_day, open_=109.0, high=111.0, low=108.0, close=110.5, ordinal=70),
            _bar(first_day + timedelta(days=1), open_=109.0, high=111.0, low=108.0, close=109.0, ordinal=71),
        ]
    )
    evidence = _evidence(rows)
    view_days = [first_day, first_day + timedelta(days=1)]
    view_times = {
        day: datetime.combine(day, datetime.min.time().replace(hour=18), tzinfo=UTC)
        for day in view_days
    }
    result = replay_daily_views(
        universe_items=[{"symbol": "AAA", "asset_type": "equity", "enabled": True}],
        evidence_by_symbol={"AAA": evidence},
        calendars={"AAA": [row["date"] for row in rows]},
        view_days=view_days,
        view_times=view_times,
        pool_version="fixture-pool",
        knowledge_at=evidence.knowledge_at,
    )

    assert len(result["views"]) == 2
    first, second = result["views"]
    assert {key for key in ("symbol", "viewed_at", "knowledge_at", "market_as_of", "scan", "setup", "trigger", "evidence")} <= set(first)
    assert first["symbol"] == "AAA"
    assert first["knowledge_at"] == view_times[first_day]
    assert first["market_as_of"] == first_day
    assert first["setup"] is not None
    assert first["trigger"] is not None
    assert first["setup"].trigger_timeframe == "daily"
    assert max(bar.date for bar in first["evidence"].bars) == first_day
    assert first["evidence"].knowledge_at == first["knowledge_at"]
    assert second["market_as_of"] == first_day + timedelta(days=1)
    assert max(bar.date for bar in second["evidence"].bars) == first_day + timedelta(days=1)
    scan_ids = {scan.scan_id for scan in result["scans"]}
    assert all(setup.origin_scan_id in scan_ids for setup in result["setups"])

    # The wrapper is deterministic across repeated calls and never lets an
    # evidence bar become visible merely because a later view was requested.
    repeated = replay_daily_views(
        universe_items=[{"symbol": "AAA", "asset_type": "equity", "enabled": True}],
        evidence_by_symbol={"AAA": evidence},
        calendars={"AAA": [row["date"] for row in rows]},
        view_days=view_days,
        view_times=view_times,
        pool_version="fixture-pool",
        knowledge_at=evidence.knowledge_at,
    )
    assert [row["scan"].scan_id for row in repeated["views"]] == [
        row["scan"].scan_id for row in result["views"]
    ]
    assert [row["setup"].setup_id for row in repeated["views"]] == [
        row["setup"].setup_id for row in result["views"]
    ]
    assert [row["trigger"].trigger_id for row in repeated["views"]] == [
        row["trigger"].trigger_id for row in result["views"]
    ]
