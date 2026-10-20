"""P6 read-only assembly/API acceptance tests.

The fixtures persist daily bars, datasets and chart projections through the
existing repository and calculate their identities with the existing contract
helpers.  They do not construct an in-memory idealized baseline object and do
not call a provider.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime, time, timedelta
import math
import sqlite3
from pathlib import Path
from typing import Any, Mapping

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.core.config import Settings
from app.core.database import Base
from app.decision_harness.contracts import (
    BAR_COMPLETION_POLICY,
    CHART_PROJECTION_SCHEMA,
    DAILY_DATASET_SCHEMA,
    compact_bar,
    content_sha256,
    hash_bars,
)
from app.models.daily_evidence import DailyDecisionDatasetModel, DecisionChartProjectionModel
from app.repositories.daily_evidence import DailyEvidenceRepository
from app.services.capital_flow import completed_session_dates


NOW = datetime(2026, 8, 24, 15, 0, tzinfo=UTC)
QUOTE_AT = NOW - timedelta(seconds=60)
MARKET_TZ = "America/New_York"


def _session_dates(count: int = 260) -> list[date]:
    return completed_session_dates(NOW, "XNYS", count=count)


def _daily_rows(
    symbol: str,
    dates: list[date],
    *,
    adjustment: str,
    missing: set[date] | None = None,
) -> list[dict[str, Any]]:
    missing = missing or set()
    offset = {"SPY": 500.0, "QQQ": 400.0, "IWM": 200.0}.get(symbol, 100.0)
    rows: list[dict[str, Any]] = []
    for index, trading_date in enumerate(dates):
        if trading_date in missing:
            continue
        close = offset + index * 0.17 + (index % 5) * 0.03
        rows.append(
            {
                "symbol": symbol,
                "date": trading_date.isoformat(),
                "open": close - 0.10,
                "high": close + 0.25,
                "low": close - 0.25,
                "close": close,
                "volume": 1_000_000.0 + index * 1_000.0,
                "adjustment": adjustment,
            }
        )
    return rows


def _projection(
    symbols: Mapping[str, Mapping[str, Any]],
    *,
    quote_at: datetime = QUOTE_AT,
) -> dict[str, Any]:
    quote_at = quote_at.astimezone(UTC)
    symbol_payload: dict[str, Any] = {}
    for symbol, overrides in symbols.items():
        quote = {
            "last_price": float(overrides.get("last_price", 550.0)),
            "quote_time": quote_at.isoformat(),
            "quote_time_status": "known",
            "price_basis": "raw",
            "quality_status": "ready",
        }
        quote.update(dict(overrides.get("quote") or {}))
        item = {
            "symbol": symbol,
            "role": str(overrides.get("role") or "A"),
            "status": str(overrides.get("status") or "ok"),
            "as_of": quote.get("quote_time"),
            "quote": quote,
            "bars": [],
            "features": {},
            "reasons": {},
            "coverage": {"status": str(overrides.get("status") or "ok")},
        }
        item.update({key: value for key, value in overrides.items() if key not in {"quote", "last_price"}})
        item["quote"] = quote
        symbol_payload[symbol] = item
    return {
        "schema_version": "urus.intraday.projection.v1",
        "formula_version": "urus.intraday.equities.v1",
        "status": "fresh",
        "source": {
            "capture_id": "p6-projection-capture",
            "kind": "capture_intraday_equities",
            "trading_date": NOW.date().isoformat(),
            "scheduled_at": quote_at.isoformat(),
            "cutoff": quote_at.isoformat(),
            "captured_at": quote_at.isoformat(),
            "content_sha256": "p" * 64,
        },
        "freshness": {
            "status": "fresh",
            "as_of": quote_at.isoformat(),
            "age_seconds": 60,
        },
        "quality": {"status": "ok"},
        "coverage": {"status": "ok"},
        "symbols": symbol_payload,
        "breadth": {"status": "ok"},
        "warnings": [],
    }


def _persist_package(
    session: Session,
    symbol: str,
    *,
    dates: list[date] | None = None,
    adjustment: str = "NONE",
    dataset_created_at: datetime = QUOTE_AT - timedelta(minutes=10),
    chart_created_at: datetime | None = None,
    missing: set[date] | None = None,
    suffix: str = "base",
    quality_status: str = "ok",
) -> dict[str, Any]:
    dates = list(dates or _session_dates())
    chart_created_at = chart_created_at or dataset_created_at
    rows = _daily_rows(symbol, dates, adjustment=adjustment, missing=missing)
    repository = DailyEvidenceRepository(session)
    repository.upsert_bars(
        rows,
        source=f"p6-fixture-{suffix}",
        adjustment=adjustment,
        collected_at=dataset_created_at - timedelta(minutes=1),
    )
    stored = repository.bars([symbol], through_date=max(dates), adjustment=adjustment)[symbol]
    compact = [compact_bar(item) for item in stored]
    assert compact
    input_hash = hash_bars(compact)
    last_date = date.fromisoformat(str(compact[-1]["date"]))
    dataset_id = f"p6-{symbol.lower()}-{suffix}-{last_date.isoformat()}"
    cutoff_time = datetime.combine(last_date, time(20, 0), tzinfo=UTC)
    scope = {
        "scope_type": "instrument",
        "scope_id": symbol,
        "scope_version": 1,
        "symbols": [symbol],
        "benchmark_symbols": [],
        "auxiliary_symbols": [],
        "trading_date": last_date.isoformat(),
    }
    manifest = {
        "symbol": symbol,
        "bar_count": len(compact),
        "start_date": compact[0]["date"],
        "end_date": compact[-1]["date"],
        "input_bar_hash": input_hash,
        "source": f"p6-fixture-{suffix}",
        "adjustment": adjustment,
        "exchange": "XNYS",
        "source_revisions": [str(item.content_sha256) for item in stored],
    }
    dataset_without_id = {
        "schema_version": DAILY_DATASET_SCHEMA,
        "feature_version": "technical_v5",
        "macd_momentum_version": "macd_histogram_momentum_v2",
        "trading_date": last_date.isoformat(),
        "cutoff_time": cutoff_time.isoformat(),
        "market_timezone": MARKET_TZ,
        "bar_completion_policy": BAR_COMPLETION_POLICY,
        "scope": scope,
        "bar_manifest": [manifest],
        "indicator_snapshot_ids": [],
        "group_snapshot_ids": [],
        "quality": {
            "status": quality_status,
            "symbols": {symbol: {"status": quality_status, "adjustment": adjustment}},
        },
        "status": "ok",
    }
    dataset_digest = content_sha256(dataset_without_id)
    dataset_payload = {
        "dataset_id": dataset_id,
        **dataset_without_id,
        "content_sha256": dataset_digest,
    }
    dataset = repository.save_dataset(
        dataset_payload,
        digest=dataset_digest,
        created_at=dataset_created_at,
    )
    chart_payload = {
        "schema_version": CHART_PROJECTION_SCHEMA,
        "dataset_id": dataset.id,
        "macd_momentum_version": "macd_histogram_momentum_v2",
        "scope": scope,
        "timezone": MARKET_TZ,
        "instruments": {
            symbol: {
                "symbol": symbol,
                "price": {
                    "symbol": symbol,
                    "price_format": {"precision": 2, "currency": "USD"},
                    "bars": compact,
                },
                "series": [],
                "macd_momentum": None,
                "indicator_snapshot_id": None,
                "quality": {"status": quality_status},
            }
        },
        "overlays": [],
        "state_segments": [],
        "events": [],
        "quality": {"status": quality_status},
        "price": {
            "symbol": symbol,
            "price_format": {"precision": 2, "currency": "USD"},
            "bars": compact,
        },
        "series": [],
    }
    chart_digest = content_sha256(chart_payload)
    chart_payload["content_sha256"] = chart_digest
    chart = repository.save_chart(
        dataset_id=dataset.id,
        scope_type="instrument",
        scope_id=symbol,
        payload=chart_payload,
        digest=chart_digest,
        created_at=chart_created_at,
    )
    session.commit()
    return {
        "dataset_id": dataset.id,
        "dataset_hash": dataset_digest,
        "chart_id": chart.id,
        "chart_hash": chart_digest,
        "input_hash": input_hash,
        "last_bar_date": last_date,
        "adjustment": adjustment,
    }


def _rewrite_package_with_invalid_chart_bar(
    session: Session,
    package: Mapping[str, Any],
    symbol: str,
    mutate: Any,
) -> None:
    """Keep evidence identities valid while making the canonical bar invalid.

    The normal repository correctly rejects malformed OHLCV rows.  This
    helper first persists a real valid package, then models a corrupted frozen
    chart/dataset received from storage by updating both payload hashes from
    the modified canonical bars.  The service must still validate OHLCV
    semantics after hash verification succeeds.
    """

    dataset = session.get(DailyDecisionDatasetModel, str(package["dataset_id"]))
    chart = session.get(DecisionChartProjectionModel, str(package["chart_id"]))
    assert dataset is not None and chart is not None

    dataset_payload = deepcopy(dataset.payload_json)
    chart_payload = deepcopy(chart.payload_json)
    instrument = chart_payload["instruments"][symbol]
    bars = instrument["price"]["bars"]
    assert isinstance(bars, list) and bars
    mutate(bars[-1])

    input_hash = hash_bars(bars)
    manifest = dataset_payload["bar_manifest"][0]
    manifest["input_bar_hash"] = input_hash
    dataset_without_hash = {
        key: value
        for key, value in dataset_payload.items()
        if key not in {"dataset_id", "content_sha256"}
    }
    dataset_digest = content_sha256(dataset_without_hash)
    dataset_payload["content_sha256"] = dataset_digest
    dataset.payload_json = dataset_payload
    dataset.content_sha256 = dataset_digest

    chart_without_hash = {
        key: value for key, value in chart_payload.items() if key != "content_sha256"
    }
    chart_digest = content_sha256(chart_without_hash)
    chart_payload["content_sha256"] = chart_digest
    chart.payload_json = chart_payload
    chart.content_sha256 = chart_digest
    session.commit()


def _session(tmp_path: Path) -> tuple[Any, Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'p6-risk.db'}")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def _build(session: Session, projection: Mapping[str, Any], *, now: datetime = NOW) -> dict[str, Any]:
    from app.systematic_risk.service import build_overview

    return build_overview(session, dict(projection), now=now, market_calendar="XNYS")


def _symbol_payload(result: Mapping[str, Any], symbol: str = "SPY") -> Mapping[str, Any]:
    symbols = result.get("symbols")
    assert isinstance(symbols, Mapping), result
    value = symbols.get(symbol)
    assert isinstance(value, Mapping), result
    return value


def test_build_overview_uses_exact_previous_session_and_real_repo_identities(tmp_path: Path) -> None:
    engine, session = _session(tmp_path)
    try:
        dates = _session_dates()
        package = _persist_package(session, "SPY", dates=dates, adjustment="NONE")
        result = _build(session, _projection({"SPY": {"last_price": 552.0}}))

        item = _symbol_payload(result)
        assert item["status"] == "ok"
        assert item["quote_as_of"] == QUOTE_AT.isoformat()
        assert item["provisional"] is True
        baseline = item["baseline"]
        assert baseline["dataset_id"] == package["dataset_id"]
        assert baseline["chart_hash"] == package["chart_hash"]
        assert baseline["input_hash"] == package["input_hash"]
        assert baseline["last_bar_date"] == dates[-1].isoformat()
        assert baseline["adjustment"] == "NONE"
        assert baseline["baseline_id"]
        assert item["scenario_id"]
        assert len(item["models"]) == 3
        for model in item["models"]:
            assert isinstance(model, Mapping)
            assert set(model) >= {"baseline", "scenario", "exposure_change"}
    finally:
        session.close()
        engine.dispose()


def test_partial_short_history_keeps_short_models_and_marks_symbol_and_overview_partial(
    tmp_path: Path,
) -> None:
    engine, session = _session(tmp_path)
    try:
        dates = _session_dates(60)
        _persist_package(
            session,
            "SPY",
            dates=dates,
            adjustment="NONE",
            quality_status="partial",
            suffix="partial-60",
        )
        result = _build(session, _projection({"SPY": {"last_price": 552.0}}))

        assert result["status"] == "partial"
        item = _symbol_payload(result)
        assert item["status"] == "partial"
        models = {tuple(model["fast_slow"]): model for model in item["models"]}
        assert models[(10, 20)]["available"] is True
        assert models[(20, 60)]["available"] is True
        assert models[(50, 200)]["available"] is False
        assert models[(50, 200)]["reason_code"] == "insufficient_daily_closes"
        assert item["baseline"]["adjustment"] == "NONE"
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize("quality_status", ["conflicted", "stale", "missing"])
def test_non_partial_frozen_quality_status_is_rejected(
    tmp_path: Path,
    quality_status: str,
) -> None:
    engine, session = _session(tmp_path)
    try:
        _persist_package(
            session,
            "SPY",
            dates=_session_dates(60),
            adjustment="NONE",
            quality_status=quality_status,
            suffix=f"quality-{quality_status}",
        )
        item = _symbol_payload(_build(session, _projection({"SPY": {"last_price": 552.0}})))
        assert item["status"] == "unavailable"
        assert item["reasons"]
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize("bad_field", ["ohlc", "nonfinite", "negative_volume"])
def test_correctly_hashed_partial_evidence_still_rejects_invalid_ohlcv(
    tmp_path: Path,
    bad_field: str,
) -> None:
    engine, session = _session(tmp_path)
    try:
        package = _persist_package(
            session,
            "SPY",
            dates=_session_dates(60),
            adjustment="NONE",
            quality_status="partial",
            suffix=f"invalid-{bad_field}",
        )

        def mutate(bar: dict[str, Any]) -> None:
            if bad_field == "ohlc":
                bar["high"] = min(float(bar["open"]), float(bar["close"])) - 1.0
            elif bad_field == "nonfinite":
                bar["close"] = math.nan
            else:
                bar["volume"] = -1.0

        _rewrite_package_with_invalid_chart_bar(session, package, "SPY", mutate)
        item = _symbol_payload(_build(session, _projection({"SPY": {"last_price": 552.0}})))
        assert item["status"] == "unavailable"
        assert item["reasons"]
        assert item["reasons"][0]["code"] in {
            "daily_ohlc_invalid",
            "daily_ohlcv_invalid",
            "daily_close_invalid",
            "daily_volume_invalid",
        }
    finally:
        session.close()
        engine.dispose()


def test_missing_exact_previous_session_or_daily_gap_is_unavailable(tmp_path: Path) -> None:
    engine, session = _session(tmp_path)
    try:
        dates = _session_dates()
        package = _persist_package(session, "SPY", dates=dates[:-1], adjustment="NONE", suffix="old")
        result = _build(session, _projection({"SPY": {"last_price": 552.0}}))
        item = _symbol_payload(result)
        assert item["status"] != "ok"
        assert item.get("reasons")
        assert package["last_bar_date"].isoformat() != dates[-1].isoformat()

        gap_dates = list(dates)
        gap_dates.pop(-30)
        _persist_package(session, "QQQ", dates=gap_dates, adjustment="NONE", suffix="gap")
        result = _build(session, _projection({"QQQ": {"last_price": 452.0}}))
        qqq = _symbol_payload(result, "QQQ")
        assert qqq["status"] != "ok"
        assert qqq.get("reasons")
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize("future_field", ["dataset", "chart"])
def test_dataset_or_chart_created_after_quote_is_unavailable(
    tmp_path: Path,
    future_field: str,
) -> None:
    engine, session = _session(tmp_path)
    try:
        future = QUOTE_AT + timedelta(seconds=1)
        package = _persist_package(
            session,
            "SPY",
            adjustment="NONE",
            dataset_created_at=future if future_field == "dataset" else QUOTE_AT - timedelta(minutes=10),
            chart_created_at=future if future_field == "chart" else QUOTE_AT - timedelta(minutes=10),
        )
        result = _build(session, _projection({"SPY": {"last_price": 552.0}}))
        item = _symbol_payload(result)
        assert item["status"] != "ok"
        assert item.get("reasons")
        assert package["dataset_id"]
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize("corrupt", ["dataset", "chart"])
def test_dataset_or_chart_content_hash_corruption_is_unavailable(
    tmp_path: Path,
    corrupt: str,
) -> None:
    engine, session = _session(tmp_path)
    try:
        package = _persist_package(session, "SPY", adjustment="NONE")
        if corrupt == "dataset":
            dataset = session.get(DailyDecisionDatasetModel, package["dataset_id"])
            assert dataset is not None
            dataset.content_sha256 = "0" * 64
        else:
            chart = session.get(DecisionChartProjectionModel, package["chart_id"])
            assert chart is not None
            chart.content_sha256 = "0" * 64
        session.commit()

        item = _symbol_payload(_build(session, _projection({"SPY": {"last_price": 552.0}})))
        assert item["status"] != "ok"
        assert item.get("reasons")
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize("variant", ["stale_quote", "unknown_quote", "qfq_mismatch"])
def test_quote_freshness_and_price_basis_must_match_daily_baseline(
    tmp_path: Path,
    variant: str,
) -> None:
    engine, session = _session(tmp_path)
    try:
        adjustment = "QFQ" if variant == "qfq_mismatch" else "NONE"
        _persist_package(session, "SPY", adjustment=adjustment, suffix=variant)
        if variant == "stale_quote":
            projection = _projection(
                {"SPY": {"last_price": 552.0, "quote": {"quote_time": (NOW - timedelta(seconds=601)).isoformat()}}}
            )
        elif variant == "unknown_quote":
            projection = _projection(
                {"SPY": {"last_price": 552.0, "quote": {"quote_time": None, "quote_time_status": "unknown"}}}
            )
        else:
            projection = _projection({"SPY": {"last_price": 552.0}})
        item = _symbol_payload(_build(session, projection))
        assert item["status"] != "ok"
        assert item.get("reasons")
    finally:
        session.close()
        engine.dispose()


def test_stale_other_etf_does_not_hide_healthy_spy(tmp_path: Path) -> None:
    engine, session = _session(tmp_path)
    try:
        _persist_package(session, "SPY", adjustment="NONE", suffix="spy")
        _persist_package(session, "QQQ", adjustment="NONE", suffix="qqq")
        projection = _projection(
            {
                "SPY": {"last_price": 552.0, "role": "A"},
                "QQQ": {
                    "last_price": 452.0,
                    "role": "D",
                    "status": "stale",
                    "quote": {"quote_time": (NOW - timedelta(seconds=601)).isoformat()},
                },
            }
        )
        result = _build(session, projection)
        assert _symbol_payload(result, "SPY")["status"] == "ok"
        assert _symbol_payload(result, "QQQ")["status"] != "ok"
    finally:
        session.close()
        engine.dispose()


def test_build_overview_is_read_only_and_never_calls_freeze(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.decision_harness.market_evidence import DailyMarketEvidenceService

    engine, session = _session(tmp_path)
    try:
        package = _persist_package(session, "SPY", adjustment="NONE")
        writes: list[str] = []

        def track_write(_conn: Any, _cursor: Any, statement: str, _parameters: Any, _context: Any, _executemany: bool) -> None:
            verb = statement.lstrip().split(maxsplit=1)[0].upper() if statement.strip() else ""
            if verb in {"INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER"}:
                writes.append(verb)

        event.listen(engine, "before_cursor_execute", track_write)

        def fail_freeze(*_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("P6 GET/assembly must not call daily freeze")

        monkeypatch.setattr(DailyMarketEvidenceService, "freeze", fail_freeze)
        result = _build(session, _projection({"SPY": {"last_price": 552.0}}))
        assert _symbol_payload(result)["status"] == "ok"
        assert writes == []
        assert session.scalar(select(func.count()).select_from(DailyDecisionDatasetModel)) == 1
        assert session.scalar(select(func.count()).select_from(DecisionChartProjectionModel)) == 1
        assert package["dataset_id"]
    finally:
        session.close()
        engine.dispose()


def test_multiple_quotes_reuse_baseline_but_do_not_accumulate_scenarios(tmp_path: Path) -> None:
    engine, session = _session(tmp_path)
    try:
        package = _persist_package(session, "SPY", adjustment="NONE")
        first_projection = _projection(
            {"SPY": {"last_price": 552.0}},
            quote_at=QUOTE_AT,
        )
        second_time = QUOTE_AT + timedelta(minutes=5)
        second_projection = _projection(
            {"SPY": {"last_price": 548.0}},
            quote_at=second_time,
        )
        first = _symbol_payload(_build(session, first_projection))
        second = _symbol_payload(
            _build(session, second_projection, now=second_time + timedelta(seconds=1))
        )
        assert first["baseline"]["dataset_id"] == package["dataset_id"]
        assert second["baseline"]["dataset_id"] == package["dataset_id"]
        assert first["baseline"] == second["baseline"]
        assert first["scenario_id"] != second["scenario_id"]
        assert first["quote_as_of"] != second["quote_as_of"]
        assert all(model["scenario"]["provisional"] is True for model in first["models"])
        assert all(model["scenario"]["provisional"] is True for model in second["models"])
    finally:
        session.close()
        engine.dispose()


def test_systematic_risk_api_is_disabled_read_only_and_does_not_create_risk_tables(
    tmp_path: Path,
) -> None:
    from app.main import create_app

    database_path = tmp_path / "p6-api.db"
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{database_path}",
        systematic_risk_enabled=False,
        moomoo_enabled=False,
        fred_enabled=False,
        yahoo_enabled=False,
        anomalo_workflow_enabled=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/api/systematic-risk/overview")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["status"] == "disabled"
        assert payload.get("symbols") in ({}, None)

    connection = sqlite3.connect(database_path)
    try:
        table_names = {
            str(row[0])
            for row in connection.execute("select name from sqlite_master where type='table'")
        }
        assert not any(name.startswith("systematic_risk") for name in table_names)
    finally:
        connection.close()


def test_enabled_systematic_risk_api_assembles_local_fixture_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import systematic_risk as systematic_risk_api
    from app.main import create_app

    database_path = tmp_path / "p6-enabled-api.db"
    sidecar_path = tmp_path / "missing-capture.sqlite"
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        package = _persist_package(session, "SPY", adjustment="NONE", suffix="enabled-api")
        assert package["dataset_id"]
    finally:
        session.close()
        engine.dispose()

    calls: dict[str, int] = {"capture": 0, "projection": 0}

    def fake_load_latest_capture(path: str) -> tuple[object, None]:
        calls["capture"] += 1
        assert path == str(sidecar_path)
        return object(), None

    def fake_build_projection(
        contract: object,
        *,
        now: datetime,
        market_timezone: str,
        market_calendar: str,
    ) -> dict[str, Any]:
        calls["projection"] += 1
        assert contract is not None
        assert now == NOW
        assert market_timezone == MARKET_TZ
        assert market_calendar == "XNYS"
        return _projection({"SPY": {"last_price": 552.0}})

    class FrozenDateTime:
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return NOW

    monkeypatch.setattr(systematic_risk_api, "datetime", FrozenDateTime)
    monkeypatch.setattr(systematic_risk_api, "_load_latest_capture", fake_load_latest_capture)
    monkeypatch.setattr(systematic_risk_api, "build_intraday_projection", fake_build_projection)

    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{database_path}",
        sidecar_capture_database_path=str(sidecar_path),
        systematic_risk_enabled=True,
        moomoo_enabled=False,
        fred_enabled=False,
        yahoo_enabled=False,
        anomalo_workflow_enabled=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/api/systematic-risk/overview")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["symbols"]["SPY"]["status"] == "ok", payload["symbols"]["SPY"]["reasons"]
        assert payload["status"] == "partial", payload
        assert payload["symbols"]["SPY"]["provisional"] is True

    assert calls == {"capture": 1, "projection": 1}
    assert not sidecar_path.exists()

    connection = sqlite3.connect(database_path)
    try:
        table_names = {
            str(row[0])
            for row in connection.execute("select name from sqlite_master where type='table'")
        }
        assert not any(name.startswith("systematic_risk") for name in table_names)
        dataset_count = connection.execute(
            "select count(*) from daily_decision_datasets"
        ).fetchone()[0]
        chart_count = connection.execute(
            "select count(*) from decision_chart_projections"
        ).fetchone()[0]
        assert dataset_count == 1
        assert chart_count == 1
    finally:
        connection.close()


def test_enabled_systematic_risk_api_bridges_real_p2_sidecar_to_raw_quote_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the unmocked P2 sidecar -> P3 projection -> P6 API boundary.

    The sidecar contract is written through the real CollectorStore.  The
    daily baseline remains a real repository package in the primary temporary
    SQLite database.  This intentionally checks the quote's provider and raw
    price basis before asking the API to assemble the risk overview, so the
    wiring test above cannot pass on an idealized projection alone.
    """

    import sys

    from app.api import systematic_risk as systematic_risk_api
    from app.api.intraday import _load_latest_capture
    from app.intraday.equities import build_intraday_projection
    from app.main import create_app

    source_root = Path(__file__).resolve().parents[2] / "urus-moomoo" / "src"
    sys.path.insert(0, str(source_root))
    from urus_moomoo.contracts import capture_contract
    from urus_moomoo.storage.sqlite import CollectorStore

    bridge_now = datetime(2026, 10, 15, 14, 0, 30, tzinfo=UTC)
    scheduled_at = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)
    sidecar_path = tmp_path / "real-bridge" / "collector.sqlite"
    primary_path = tmp_path / "real-bridge" / "primary.sqlite"

    from test_intraday_equities_acceptance import _capture_contract, _minute_rows

    data = dict(
        _capture_contract(
            _minute_rows("AAA", count=30),
            benchmark_bars=_minute_rows("SPY", count=30),
            cutoff=scheduled_at,
            captured_at=scheduled_at + timedelta(seconds=10),
        )["data"]
    )
    data["minute_timestamp_policy"] = {
        **dict(data["minute_timestamp_policy"]),
        "provider_boundary_verified": True,
    }
    data["quotes"] = [
        {
            "symbol": "AAA",
            "last_price": 102.0,
            "provider": "moomoo_openapi",
            "update_time": "2026-10-15 10:00:03",
            "quality_status": "ready",
        },
        {
            "symbol": "SPY",
            "last_price": 500.0,
            "provider": "moomoo_openapi",
            "update_time": "2026-10-15 10:00:03",
            "quality_status": "ready",
        },
    ]
    # capture_contract hashes the final data shape; its captured_at is then
    # pinned before the bridge's known evaluation instant.
    contract = capture_contract(
        kind="capture_intraday_equities",
        payload=data,
        requested_symbols=["AAA", "SPY"],
        task_name="equities-capture-real-bridge",
        scheduled_at=scheduled_at,
        trading_date="2026-10-15",
    )
    contract["captured_at"] = (scheduled_at + timedelta(seconds=10)).isoformat()
    with CollectorStore(sidecar_path) as store:
        assert store.write_scheduled_raw(
            "equities-capture-real-bridge",
            scheduled_at,
            contract["capture_id"],
            contract["kind"],
            contract,
            schema_version=contract["schema_version"],
            completed=True,
        ) is True

    daily_dates = completed_session_dates(bridge_now, "XNYS", count=260)
    engine = create_engine(f"sqlite:///{primary_path}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        package = _persist_package(
            session,
            "SPY",
            dates=daily_dates,
            adjustment="NONE",
            dataset_created_at=bridge_now - timedelta(minutes=15),
            suffix="real-bridge",
        )
    finally:
        session.close()
        engine.dispose()

    loaded, source_error = _load_latest_capture(str(sidecar_path))
    assert source_error is None
    assert loaded is not None
    projection = build_intraday_projection(
        loaded,
        now=bridge_now,
        market_timezone=MARKET_TZ,
        market_calendar="XNYS",
    )
    spy_quote = projection["symbols"]["SPY"]["quote"]
    assert spy_quote["provider"] == "moomoo_openapi"
    assert spy_quote["price_basis"] == "raw"
    assert spy_quote["quote_time"] == "2026-10-15T14:00:03+00:00"

    class FrozenDateTime:
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return bridge_now

    monkeypatch.setattr(systematic_risk_api, "datetime", FrozenDateTime)
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{primary_path}",
        sidecar_capture_database_path=str(sidecar_path),
        intraday_equities_enabled=True,
        systematic_risk_enabled=True,
        enabled_symbols="AAA,SPY",
        instrument_validation_symbols="AAA,SPY",
        moomoo_enabled=False,
        fred_enabled=False,
        yahoo_enabled=False,
        anomalo_workflow_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/systematic-risk/overview")
        assert response.status_code == 200, response.text
        payload = response.json()

    assert payload["status"] == "partial"
    assert payload["symbols"]["SPY"]["status"] == "ok", payload
    assert payload["symbols"]["SPY"]["quote_as_of"] == "2026-10-15T14:00:03+00:00"
    assert payload["symbols"]["SPY"]["baseline"]["dataset_id"] == package["dataset_id"]


def test_enabled_api_missing_primary_is_structured_and_does_not_create_database_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read-only GET must not create a missing primary SQLite database.

    The TestClient is intentionally used without its lifespan context: this
    isolates dependency/session construction from startup schema creation and
    catches an implicit SQLite file created before the route's file guard.
    """

    from app.api import systematic_risk as systematic_risk_api
    from app.main import create_app

    primary_path = tmp_path / "missing-primary" / "urus.sqlite"
    sidecar_path = tmp_path / "valid-source" / "collector.sqlite"
    calls = {"capture": 0, "projection": 0}

    def fake_load_latest_capture(path: str) -> tuple[object, None]:
        calls["capture"] += 1
        assert path == str(sidecar_path)
        return object(), None

    def fake_build_projection(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls["projection"] += 1
        return _projection({"SPY": {"last_price": 552.0}})

    monkeypatch.setattr(systematic_risk_api, "_load_latest_capture", fake_load_latest_capture)
    monkeypatch.setattr(systematic_risk_api, "build_intraday_projection", fake_build_projection)
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{primary_path}",
        sidecar_capture_database_path=str(sidecar_path),
        systematic_risk_enabled=True,
        moomoo_enabled=False,
        fred_enabled=False,
        yahoo_enabled=False,
        anomalo_workflow_enabled=False,
    )
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        response = client.get("/api/systematic-risk/overview")
    finally:
        client.close()

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] in {"unavailable", "error"}
    assert payload.get("reasons")
    assert not primary_path.exists()
    assert not primary_path.parent.exists()
    assert calls == {"capture": 0, "projection": 0}


def test_enabled_api_invalid_primary_returns_structured_error_not_http_500(
    tmp_path: Path,
) -> None:
    from app.main import create_app

    primary_path = tmp_path / "invalid-primary.sqlite"
    primary_path.write_bytes(b"not a sqlite database")
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{primary_path}",
        sidecar_capture_database_path=str(tmp_path / "missing-sidecar.sqlite"),
        systematic_risk_enabled=True,
        moomoo_enabled=False,
        fred_enabled=False,
        yahoo_enabled=False,
        anomalo_workflow_enabled=False,
    )
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        response = client.get("/api/systematic-risk/overview")
    finally:
        client.close()

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] in {"unavailable", "error"}
    assert payload.get("reasons")


def test_disabled_api_does_not_touch_missing_primary_or_sidecar(
    tmp_path: Path,
) -> None:
    from app.main import create_app

    primary_path = tmp_path / "disabled-primary" / "urus.sqlite"
    sidecar_path = tmp_path / "disabled-sidecar" / "collector.sqlite"
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{primary_path}",
        sidecar_capture_database_path=str(sidecar_path),
        systematic_risk_enabled=False,
        moomoo_enabled=False,
        fred_enabled=False,
        yahoo_enabled=False,
        anomalo_workflow_enabled=False,
    )
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        response = client.get("/api/systematic-risk/overview")
    finally:
        client.close()

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "disabled"
    assert not primary_path.exists()
    assert not primary_path.parent.exists()
    assert not sidecar_path.exists()
    assert not sidecar_path.parent.exists()


def test_systematic_risk_setting_defaults_off() -> None:
    settings = Settings(app_env="test")
    assert settings.systematic_risk_enabled is False
