from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import Base
from app.models.market_data_capacity import (
    HistoryCollectionStateModel,
    HistoryQuotaSnapshotModel,
)
from app.repositories.daily_evidence import DailyEvidenceRepository
from app.services.history_quota import HistoryAdmission, HistoryCapacityService
from app.services.market_data_collection import MoomooCollectionCoordinator


def _settings(**overrides: object) -> Settings:
    return Settings(
        app_env="test",
        market_calendar="XNYS",
        daily_min_history_bars=3,
        moomoo_history_quota_reserve_absolute=2,
        moomoo_history_quota_reserve_ratio=0.2,
        **overrides,
    )


def _bars(symbol: str, count: int = 3) -> list[dict[str, object]]:
    return [
        {
            "symbol": symbol,
            "date": (date(2026, 8, 1) + timedelta(days=index)).isoformat(),
            "open": 100 + index,
            "high": 101 + index,
            "low": 99 + index,
            "close": 100.5 + index,
            "volume": 1000,
        }
        for index in range(count)
    ]


def test_capacity_plan_keeps_overflow_symbols_pending_without_disabling_universe() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    items = [
        {"symbol": symbol, "enabled": True, "collection": {"daily_history": True}}
        for symbol in ("AAA", "BBB", "CCC")
    ]
    with Session(engine) as session:
        service = HistoryCapacityService(session, _settings())
        plan = service.build_plan(
            items,
            quota={"available": True, "used": 90, "remain": 10, "total": 100},
            persist=False,
            now=datetime(2026, 8, 4, tzinfo=UTC),
        )
        service.apply_plan(plan, now=datetime(2026, 8, 4, tzinfo=UTC))

        assert plan["summary"]["pending_quota_count"] == 3
        assert {item["decision"] for item in plan["symbols"]} == {"pending_quota"}
        assert all(item["enabled"] for item in items)


def test_pending_state_is_cleared_only_after_canonical_bars_are_written() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 8, 4, tzinfo=UTC)
    with Session(engine) as session:
        service = HistoryCapacityService(session, _settings())
        plan = service.build_plan(
            [{"symbol": "AAA", "enabled": True, "collection": {"daily_history": True}}],
            quota={"available": True, "used": 90, "remain": 10, "total": 100},
            persist=False,
            now=now,
        )
        service.apply_plan(plan, now=now)
        session.commit()
        state = session.scalar(
            select(HistoryCollectionStateModel).where(HistoryCollectionStateModel.symbol == "AAA")
        )
        assert state is not None and state.access_state == "pending_quota"

        DailyEvidenceRepository(session).sync_legacy_snapshot_bars(
            {"symbols": [{"symbol": "AAA", "history": {"bars": _bars("AAA")}}]},
            collected_at=now,
        )
        session.commit()
        session.refresh(state)
        assert state.access_state == "acquired"
        assert state.quality_state == "ready"
        assert state.reason_code is None


def test_reconcile_retries_when_latest_bar_misses_required_target() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 8, 4, tzinfo=UTC)
    with Session(engine) as session:
        service = HistoryCapacityService(session, _settings())
        plan = service.build_plan(
            [{"symbol": "AAA", "enabled": True, "collection": {"daily_history": True}}],
            quota={"available": True, "used": 0, "remain": 90, "total": 100},
            persist=False,
            now=now,
        )
        service.apply_plan(plan, now=now)
        state = service._state("AAA")
        assert state is not None
        state.required_through_date = date(2026, 8, 4)
        state.access_state = "collecting"
        session.commit()

        DailyEvidenceRepository(session).sync_legacy_snapshot_bars(
            {"symbols": [{"symbol": "AAA", "history": {"bars": _bars("AAA")}}]},
            collected_at=now,
        )
        session.commit()
        session.refresh(state)
        assert state.access_state == "retry_wait"
        assert state.reason_code == "history_target_not_reached"


def test_universe_reconciliation_disables_removed_history_symbols() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 8, 4, tzinfo=UTC)
    with Session(engine) as session:
        service = HistoryCapacityService(session, _settings())
        first = service.build_plan(
            [
                {"symbol": "AAA", "enabled": True, "collection": {"daily_history": True}},
                {"symbol": "BBB", "enabled": True, "collection": {"daily_history": True}},
            ],
            quota={"available": True, "used": 0, "remain": 90, "total": 100},
            persist=False,
            now=now,
        )
        service.apply_plan(first, now=now)
        second = service.build_plan(
            [{"symbol": "AAA", "enabled": True, "collection": {"daily_history": True}}],
            quota={"available": True, "used": 0, "remain": 90, "total": 100},
            persist=False,
            now=now,
        )
        service.apply_plan(second, now=now)
        state = service._state("BBB")
        assert state is not None
        assert state.desired_history is False
        assert state.access_state == "disabled"


def test_runtime_admission_rechecks_live_quota_before_history_request() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 8, 4, tzinfo=UTC)
    with Session(engine) as session:
        settings = _settings()
        service = HistoryCapacityService(session, settings)
        plan = service.build_plan(
            [{"symbol": "AAA", "enabled": True, "collection": {"daily_history": True}}],
            quota={"available": True, "used": 0, "remain": 90, "total": 100},
            persist=False,
            now=now,
        )
        service.apply_plan(plan, now=now)
        session.commit()

        admission = HistoryAdmission(
            session,
            settings,
            quota_reader=lambda: {"available": True, "used": 100, "remain": 0, "total": 100},
        )
        decision = admission.acquire("AAA", now=now)
        assert decision["admitted"] is False
        assert decision["reason_code"] == "history_quota_reserve"
        assert service._state("AAA").access_state == "pending_quota"  # type: ignore[union-attr]


def test_runtime_admission_counts_new_symbols_when_provider_detail_lags() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 8, 4, tzinfo=UTC)
    with Session(engine) as session:
        settings = _settings()
        service = HistoryCapacityService(session, settings)
        items = [
            {"symbol": symbol, "enabled": True, "collection": {"daily_history": True}}
            for symbol in ("AAA", "BBB")
        ]
        plan = service.build_plan(
            items,
            quota={"available": True, "used": 0, "remain": 10, "total": 10},
            persist=False,
            now=now,
        )
        service.apply_plan(plan, now=now)
        session.commit()
        admission = HistoryAdmission(
            session,
            settings,
            quota_reader=lambda: {"available": True, "used": 7, "remain": 3, "total": 10},
        )

        first = admission.acquire("AAA", now=now)
        second = admission.acquire("BBB", now=now)

        assert first["admitted"] is True
        assert second["admitted"] is False
        assert second["reason_code"] == "history_quota_reserve"


def test_coordinator_persists_rate_window_without_database_commits(tmp_path) -> None:
    now = datetime(2026, 8, 4, tzinfo=UTC)
    sleeps: list[float] = []
    path = tmp_path / "moomoo.lock"
    first = MoomooCollectionCoordinator(_settings(), lock_path=path, sleeper=sleeps.append)
    first.acquire_rate_slot("moomoo_option_chain", 3.05, now=now)
    first.close()

    second = MoomooCollectionCoordinator(_settings(), lock_path=path, sleeper=sleeps.append)
    waited = second.acquire_rate_slot("moomoo_option_chain", 3.05, now=now)
    second.close()

    assert waited == 3.05
    assert sleeps == [3.05]


def test_quota_capture_overwrites_single_latest_snapshot() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        service = HistoryCapacityService(session, _settings())
        service.capture(lambda: {"used": 1, "remain": 99, "total": 100})
        service.capture(lambda: {"used": 2, "remain": 98, "total": 100})
        session.commit()

        rows = list(session.scalars(select(HistoryQuotaSnapshotModel)))
        assert len(rows) == 1
        assert rows[0].used_quota == 2


def test_history_admission_reuses_ready_canonical_bars_without_provider_request() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 8, 4, tzinfo=UTC)
    with Session(engine) as session:
        settings = _settings()
        DailyEvidenceRepository(session).upsert_bars(
            _bars("AAA"), source="moomoo_opend_history", collected_at=now
        )
        service = HistoryCapacityService(session, settings)
        plan = service.build_plan(
            [{"symbol": "AAA", "enabled": True, "collection": {"daily_history": True}}],
            quota={"available": True, "used": 90, "remain": 10, "total": 100},
            persist=False,
            now=now,
        )
        service.apply_plan(plan, now=now)
        session.commit()

        decision = HistoryAdmission(session, settings, quota_reader=lambda: {}).acquire(
            "AAA", now=now
        )
        assert decision["admitted"] is True
        assert decision["cached"] is True


def test_quota_unavailable_fails_closed_for_new_symbols() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        admission = HistoryAdmission(session, _settings(), quota_reader=lambda: {})
        decision = admission.acquire("AAA", now=datetime(2026, 8, 4, tzinfo=UTC))
        assert decision["admitted"] is False
        assert decision["access_state"] == "pending_quota"
