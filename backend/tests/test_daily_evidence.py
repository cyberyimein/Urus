from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import Base
from app.decision_harness.market_evidence import DailyMarketEvidenceService
from app.models.daily_evidence import (
    DailyBarModel,
    DailyDecisionDatasetModel,
    DailyIndicatorSnapshotModel,
    DecisionChartProjectionModel,
)
from app.repositories.daily_evidence import DailyEvidenceRepository
from app.services.capital_flow import completed_session_dates


def _fixture_bars(symbol: str, session_dates: list[date], start: float) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, trading_date in enumerate(session_dates):
        close = start + index * 0.35
        rows.append(
            {
                "symbol": symbol,
                "date": trading_date.isoformat(),
                "open": close - 0.25,
                "high": close + 0.75,
                "low": close - 0.75,
                "close": close,
                "volume": 100000.0 + index * 100.0,
            }
        )
    return rows


def _service(session: Session, *, minimum_history_bars: int = 260) -> DailyMarketEvidenceService:
    return DailyMarketEvidenceService(
        session,
        Settings(
            app_env="test",
            market_calendar="XNYS",
            market_timezone="America/New_York",
            daily_min_history_bars=minimum_history_bars,
        ),
    )


class FakeDailyBarAdapter:
    def __init__(self, bars: list[dict[str, object]]) -> None:
        self.bars = bars
        self.calls: list[list[str]] = []

    def instrument_cards(self, symbols: list[str]) -> dict[str, object]:
        self.calls.append(list(symbols))
        return {
            "_persistence": {
                "symbols": [
                    {
                        "symbol": symbol,
                        "history": {
                            "bars": [
                                {key: value for key, value in bar.items() if key != "symbol"}
                                for bar in self.bars
                                if bar["symbol"] == symbol
                            ]
                        },
                    }
                    for symbol in symbols
                ]
            }
        }


def test_daily_evidence_refreshes_only_missing_symbols_then_hits_canonical_cache() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    cutoff = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    session_dates = completed_session_dates(cutoff, "XNYS", count=20)
    adapter = FakeDailyBarAdapter(_fixture_bars("INTC", session_dates, 20.0))

    with Session(engine) as session:
        service = _service(session, minimum_history_bars=20)
        first = service.freeze(
            scope_type="instrument",
            scope_id="INTC",
            symbols=["INTC"],
            trading_date=session_dates[-1],
            cutoff_time=cutoff,
            bar_source=adapter,
        )
        second = service.freeze(
            scope_type="instrument",
            scope_id="INTC",
            symbols=["INTC"],
            trading_date=session_dates[-1],
            cutoff_time=cutoff,
            bar_source=adapter,
        )

        assert first["dataset"]["quality"]["collection"]["status"] == "ok"
        assert first["dataset"]["quality"]["collection"]["fetched_symbols"] == ["INTC"]
        assert second["dataset"]["quality"]["collection"]["status"] == "cache_hit"
        assert adapter.calls == [["INTC"]]


def test_daily_evidence_freezes_indicators_and_chart_projection_idempotently() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    cutoff = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    session_dates = completed_session_dates(cutoff, "XNYS", count=260)
    target_date = session_dates[-1]

    with Session(engine) as session:
        repository = DailyEvidenceRepository(session)
        repository.upsert_bars(
            _fixture_bars("INTC", session_dates, 20.0),
            source="fixture",
            collected_at=cutoff,
        )
        repository.upsert_bars(
            _fixture_bars("QQQ", session_dates, 300.0),
            source="fixture",
            collected_at=cutoff,
        )

        service = _service(session)
        first = service.freeze(
            scope_type="instrument",
            scope_id="INTC",
            symbols=["INTC"],
            benchmark_symbols=["QQQ"],
            scope_version=1,
            trading_date=target_date,
            cutoff_time=cutoff,
        )
        second = service.freeze(
            scope_type="instrument",
            scope_id="INTC",
            symbols=["INTC"],
            benchmark_symbols=["QQQ"],
            scope_version=1,
            trading_date=target_date,
            cutoff_time=cutoff,
        )

        first_dataset = first["dataset"]
        first_chart = first["chart"]
        assert first_dataset["status"] == "ok"
        assert first_dataset["quality"]["status"] == "ok"
        assert first_dataset["bar_completion_policy"] == "official_exchange_close_only_v1"
        assert first_dataset["quality"]["available_symbol_count"] == 1
        assert first_dataset["bar_manifest"][0]["bar_count"] == 260
        assert first_chart["price"]["bars"][-1]["date"] == target_date.isoformat()
        assert {item["series_id"] for item in first_chart["series"]} >= {
            "close",
            "ma20",
            "rsi14",
            "macd_histogram",
            "relative_performance_vs_QQQ",
        }
        assert first["dataset"]["dataset_id"] == second["dataset"]["dataset_id"]
        assert first["chart"]["dataset_id"] == first["dataset"]["dataset_id"]
        assert second["chart"]["dataset_id"] == second["dataset"]["dataset_id"]
        assert session.scalar(select(func.count()).select_from(DailyBarModel)) == 520
        assert session.scalar(select(func.count()).select_from(DailyIndicatorSnapshotModel)) == 2
        assert session.scalar(select(func.count()).select_from(DailyDecisionDatasetModel)) == 1
        assert session.scalar(select(func.count()).select_from(DecisionChartProjectionModel)) == 1


def test_daily_evidence_does_not_use_bar_revisions_collected_after_cutoff() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    cutoff = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    session_dates = completed_session_dates(cutoff, "XNYS", count=20)
    original = _fixture_bars("INTC", session_dates, 20.0)
    revised = [dict(item) for item in original]
    revised[-1].update(
        {
            "close": float(revised[-1]["close"]) + 10.0,
            "high": float(revised[-1]["high"]) + 10.0,
            "low": float(revised[-1]["low"]) + 10.0,
            "open": float(revised[-1]["open"]) + 10.0,
        }
    )

    with Session(engine) as session:
        repository = DailyEvidenceRepository(session)
        repository.upsert_bars(
            original,
            source="fixture",
            collected_at=cutoff - timedelta(hours=1),
        )
        repository.upsert_bars(
            revised,
            source="fixture",
            collected_at=cutoff + timedelta(hours=1),
        )

        result = _service(session, minimum_history_bars=20).freeze(
            scope_type="instrument",
            scope_id="INTC",
            symbols=["INTC"],
            trading_date=session_dates[-1],
            cutoff_time=cutoff,
        )

        assert result["chart"]["price"]["bars"][-1]["close"] == original[-1]["close"]


def test_daily_evidence_uses_the_next_available_benchmark() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    cutoff = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    session_dates = completed_session_dates(cutoff, "XNYS", count=20)

    with Session(engine) as session:
        repository = DailyEvidenceRepository(session)
        repository.upsert_bars(
            _fixture_bars("INTC", session_dates, 20.0),
            source="fixture",
            collected_at=cutoff,
        )
        repository.upsert_bars(
            _fixture_bars("SPY", session_dates, 300.0),
            source="fixture",
            collected_at=cutoff,
        )

        result = _service(session, minimum_history_bars=20).freeze(
            scope_type="instrument",
            scope_id="INTC",
            symbols=["INTC"],
            benchmark_symbols=["QQQ", "SPY"],
            trading_date=session_dates[-1],
            cutoff_time=cutoff,
        )

        series_ids = {item["series_id"] for item in result["chart"]["series"]}
        assert "relative_performance_vs_SPY" in series_ids
        assert "relative_performance_vs_QQQ" not in series_ids


def test_daily_evidence_marks_short_history_partial_and_rejects_non_session_date() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    cutoff = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    session_dates = completed_session_dates(cutoff, "XNYS", count=10)

    with Session(engine) as session:
        DailyEvidenceRepository(session).upsert_bars(
            _fixture_bars("INTC", session_dates, 20.0),
            source="fixture",
            collected_at=cutoff,
        )
        service = _service(session, minimum_history_bars=20)
        result = service.freeze(
            scope_type="instrument",
            scope_id="INTC",
            symbols=["INTC"],
            trading_date=session_dates[-1],
            cutoff_time=cutoff,
        )

        assert result["dataset"]["status"] == "partial"
        assert result["dataset"]["quality"]["symbols"]["INTC"]["status"] == "partial"

        with pytest.raises(ValueError, match="不是"):
            service.freeze(
                scope_type="instrument",
                scope_id="INTC",
                symbols=["INTC"],
                trading_date=date(2026, 8, 15),
                cutoff_time=cutoff,
            )
