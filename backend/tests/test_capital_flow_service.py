from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.capital_flows import CapitalFlowDailyModel
from app.repositories.capital_flows import CapitalFlowRepository
from app.services.capital_flow import CapitalFlowService, latest_completed_session_date


class FakeCapitalFlowSource:
    def __init__(self) -> None:
        self.calls: list[tuple[str, date]] = []

    def capital_flow_day(self, symbol: str, trading_date: date) -> dict[str, object]:
        self.calls.append((symbol, trading_date))
        return {
            "in_flow": 1.0,
            "main_in_flow": 3.0,
            "super_in_flow": 2.0,
            "big_in_flow": 1.0,
            "mid_in_flow": -1.0,
            "sml_in_flow": -1.0,
            "source_time": trading_date.isoformat(),
            "quality_status": "ok",
            "quality_warnings": [],
            "raw_payload": {},
        }


def test_latest_completed_session_never_uses_an_open_session() -> None:
    assert latest_completed_session_date(
        datetime(2026, 8, 3, 12, 0, tzinfo=UTC), "XNYS"
    ) == date(2026, 7, 31)
    assert latest_completed_session_date(
        datetime(2026, 8, 3, 21, 0, tzinfo=UTC), "XNYS"
    ) == date(2026, 8, 3)


def test_collect_fetches_only_missing_latest_day_and_then_hits_cache() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    source = FakeCapitalFlowSource()
    with Session(engine) as session:
        service = CapitalFlowService(
            CapitalFlowRepository(session),
            source,
            symbols=["QQQ", "SOXX"],
            calendar_name="XNYS",
            cache_days=30,
        )
        cutoff = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)

        first = service.collect(cutoff)
        second = service.collect(cutoff)

        assert source.calls == [
            ("QQQ", date(2026, 7, 31)),
            ("SOXX", date(2026, 7, 31)),
        ]
        assert first["fetched_symbols"] == ["QQQ", "SOXX"]
        assert second["fetched_symbols"] == []
        assert second["cache_hit_symbols"] == ["QQQ", "SOXX"]
        assert session.scalar(select(func.count()).select_from(CapitalFlowDailyModel)) == 2
        assert all(item["cached_trading_days"] == 1 for item in second["symbols"])


def test_backfill_seeds_five_completed_sessions_and_is_idempotent() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    source = FakeCapitalFlowSource()
    with Session(engine) as session:
        service = CapitalFlowService(
            CapitalFlowRepository(session),
            source,
            symbols=["QQQ", "SOXX"],
            calendar_name="XNYS",
            cache_days=30,
        )
        cutoff = datetime(2026, 8, 3, 21, 0, tzinfo=UTC)

        first = service.backfill(cutoff, days=5)
        second = service.backfill(cutoff, days=5)

        assert first["session_dates"] == [
            "2026-07-28",
            "2026-07-29",
            "2026-07-30",
            "2026-07-31",
            "2026-08-03",
        ]
        assert len(source.calls) == 10
        assert len(first["fetched_observations"]) == 10
        assert len(second["fetched_observations"]) == 0
        assert len(second["cache_hit_observations"]) == 10
        assert session.scalar(select(func.count()).select_from(CapitalFlowDailyModel)) == 10
        assert all(item["cached_trading_days"] == 5 for item in second["symbols"])
