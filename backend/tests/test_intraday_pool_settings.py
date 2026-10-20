from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.decision_harness.contracts import content_sha256
from app.main import create_app


def test_collection_plan_exposes_member_asset_types_for_stock_only_prewarm(pool_client):
    client, _app = pool_client
    response = client.get("/api/intraday/collection-plan?trading_date=2026-10-19")
    assert response.status_code == 200
    plan = response.json()
    types = plan["asset_type_by_symbol"]
    assert set(types) == set(plan["member_symbols"])
    assert types["INTC"] == "equity"
    assert types["SMH"] == "etf"
    assert all(types[symbol] != "equity" for symbol in ("SPY", "QQQ", "IWM"))
    assert content_sha256({key: value for key, value in plan.items() if key != "plan_id"}) == plan["plan_id"]


def _write_legacy_capture(
    path: Path,
    symbols: list[str],
    *,
    capture_id: str = "legacy-intraday-1",
    scheduled_at: str = "2026-10-16T19:55:00+00:00",
    trading_date: str | None = None,
    complete: bool = True,
    scheduler_completed: bool = True,
) -> None:
    trading_date = trading_date or scheduled_at[:10]
    captured_at = (
        datetime.fromisoformat(scheduled_at) + timedelta(seconds=5)
    ).isoformat()
    data = {
        "schema_version": "moomoo-intraday-equities-refs-v1",
        "provider": "moomoo_openapi",
        "trading_date": trading_date,
        "requested_symbols": list(symbols),
        "quote_symbols": list(symbols),
        "minute_symbols": list(symbols),
        "quality_summary": {
            "complete": complete,
            "status": "ok" if complete else "partial",
        },
    }
    contract = {
        "schema_version": "urus.moomoo.capture.v2",
        "capture_id": capture_id,
        "kind": "capture_intraday_equities",
        "task_name": "equity-intraday",
        "provider": "moomoo_openapi",
        "scheduled_at": scheduled_at,
        "trading_date": trading_date,
        "captured_at": captured_at,
        "requested_symbols": list(symbols),
        "content_sha256": content_sha256(data),
        "data": data,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS raw_captures (
                capture_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL,
                kind TEXT NOT NULL, captured_at TEXT NOT NULL, payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scheduled_captures (
                task_name TEXT NOT NULL, scheduled_at TEXT NOT NULL,
                capture_id TEXT NOT NULL, kind TEXT NOT NULL,
                completed INTEGER NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(task_name, scheduled_at)
            );
            """
        )
        connection.execute(
            "INSERT INTO raw_captures VALUES (?, ?, ?, ?, ?)",
            (
                contract["capture_id"],
                contract["schema_version"],
                contract["kind"],
                contract["captured_at"],
                json.dumps(contract, ensure_ascii=False),
            ),
        )
        connection.execute(
            "INSERT INTO scheduled_captures VALUES (?, ?, ?, ?, ?, ?)",
            (
                contract["task_name"],
                contract["scheduled_at"],
                contract["capture_id"],
                contract["kind"],
                int(scheduler_completed),
                contract["captured_at"],
            ),
        )
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def pool_client(tmp_path: Path):
    collector_path = tmp_path / "collector.sqlite"
    _write_legacy_capture(collector_path, ["INTC", "SMH", "SPY", "QQQ", "IWM"])
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'urus.db'}",
        cors_origins="http://testserver",
        observation_universe_source_url="",
        enabled_symbols="QQQ",
        instrument_validation_symbols="INTC,SMH",
        moomoo_market_symbols="SPY,QQQ,IWM",
        sidecar_capture_database_path=str(collector_path),
        moomoo_enabled=False,
        fred_enabled=False,
        yahoo_enabled=False,
        anomalo_workflow_enabled=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        client.get("/api/settings/universe")
        yield client, app


def test_settings_default_uses_exact_legacy_capture_and_pins_minute_benchmarks(pool_client):
    client, _app = pool_client

    response = client.get("/api/settings/intraday-pool")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "configured"
    assert payload["active"]["source"] == "legacy_capture"
    assert payload["active"]["source_capture_id"] == "legacy-intraday-1"
    assert payload["active"]["member_symbols"] == ["INTC", "SMH", "SPY", "QQQ", "IWM"]
    assert payload["benchmark_symbols"] == ["SPY", "QQQ", "IWM"]
    assert payload["active"]["display_group_by_symbol"]["SPY"] == "大盘基准"


def test_bootstrap_uses_latest_valid_partial_capture_metadata(tmp_path: Path):
    collector_path = tmp_path / "collector.sqlite"
    symbols = ["INTC", "SMH", "SPY", "QQQ", "IWM"]
    _write_legacy_capture(collector_path, symbols)
    _write_legacy_capture(
        collector_path,
        symbols,
        capture_id="legacy-intraday-partial",
        scheduled_at="2026-10-19T14:36:00+00:00",
        complete=False,
        scheduler_completed=False,
    )
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'urus.db'}",
        observation_universe_source_url="",
        enabled_symbols="QQQ",
        instrument_validation_symbols="INTC,SMH",
        moomoo_market_symbols="SPY,QQQ,IWM",
        sidecar_capture_database_path=str(collector_path),
        moomoo_enabled=False,
        fred_enabled=False,
        yahoo_enabled=False,
        anomalo_workflow_enabled=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        client.get("/api/settings/universe")
        payload = client.get("/api/settings/intraday-pool").json()

    assert payload["status"] == "configured"
    assert payload["active"]["source_capture_id"] == "legacy-intraday-partial"
    assert payload["active"]["member_symbols"] == symbols


def test_bootstrap_skips_newer_scheduler_row_without_a_valid_capture_envelope(tmp_path: Path):
    collector_path = tmp_path / "collector.sqlite"
    symbols = ["INTC", "SMH", "SPY", "QQQ", "IWM"]
    _write_legacy_capture(collector_path, symbols)
    with sqlite3.connect(collector_path) as connection:
        connection.execute(
            "INSERT INTO scheduled_captures VALUES (?, ?, ?, ?, ?, ?)",
            (
                "equity-intraday",
                "2026-10-19T14:36:00+00:00",
                "capture-envelope-missing",
                "capture_intraday_equities",
                1,
                "2026-10-19T14:36:05+00:00",
            ),
        )
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'urus.db'}",
        observation_universe_source_url="",
        enabled_symbols="QQQ",
        instrument_validation_symbols="INTC,SMH",
        moomoo_market_symbols="SPY,QQQ,IWM",
        sidecar_capture_database_path=str(collector_path),
        moomoo_enabled=False,
        fred_enabled=False,
        yahoo_enabled=False,
        anomalo_workflow_enabled=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        client.get("/api/settings/universe")
        payload = client.get("/api/settings/intraday-pool").json()

    assert payload["status"] == "configured"
    assert payload["active"]["source_capture_id"] == "legacy-intraday-1"


def test_bootstrap_matches_trading_date_in_market_timezone_after_jst_midnight(tmp_path: Path):
    collector_path = tmp_path / "collector.sqlite"
    symbols = ["INTC", "SMH", "SPY", "QQQ", "IWM"]
    _write_legacy_capture(
        collector_path,
        symbols,
        capture_id="jst-after-midnight",
        scheduled_at="2026-10-20T05:00:00+09:00",
        trading_date="2026-10-19",
        complete=False,
        scheduler_completed=False,
    )
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'urus.db'}",
        observation_universe_source_url="",
        enabled_symbols="QQQ",
        instrument_validation_symbols="INTC,SMH",
        moomoo_market_symbols="SPY,QQQ,IWM",
        sidecar_capture_database_path=str(collector_path),
        market_timezone="America/New_York",
        moomoo_enabled=False,
        fred_enabled=False,
        yahoo_enabled=False,
        anomalo_workflow_enabled=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        client.get("/api/settings/universe")
        payload = client.get("/api/settings/intraday-pool").json()

    assert payload["status"] == "configured"
    assert payload["active"]["source_capture_id"] == "jst-after-midnight"
    assert payload["active"]["effective_date"] == "2026-10-19"


def test_missing_legacy_capture_is_explicit_and_does_not_silently_create_a_pool(tmp_path: Path):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'urus.db'}",
        observation_universe_source_url="",
        enabled_symbols="QQQ",
        instrument_validation_symbols="INTC,SMH",
        sidecar_capture_database_path=str(tmp_path / "missing.sqlite"),
        moomoo_enabled=False,
        fred_enabled=False,
        yahoo_enabled=False,
        anomalo_workflow_enabled=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        client.get("/api/settings/universe")
        settings_response = client.get("/api/settings/intraday-pool")
        plan_response = client.get(
            "/api/intraday/collection-plan?trading_date=2026-10-19"
        )

    assert settings_response.status_code == 200
    assert settings_response.json()["status"] == "unconfigured"
    assert settings_response.json()["active"] is None
    assert plan_response.status_code == 409
    assert plan_response.json()["error"]["code"] == "intraday_pool_unavailable"


def test_pool_save_is_pending_until_next_xnys_session_and_does_not_change_universe(
    pool_client, monkeypatch
):
    from app.api import intraday_pool

    client, _app = pool_client
    monkeypatch.setattr(intraday_pool, "market_today", lambda _settings: date(2026, 10, 19))
    universe_before = client.get("/api/settings/universe").json()
    runtime = client.get("/api/settings").json()
    runtime["schedule"]["post_close_observation"]["enabled"] = False
    assert client.put(
        "/api/settings",
        json={"revision": runtime["revision"], "schedule": runtime["schedule"]},
    ).status_code == 200

    response = client.put(
        "/api/settings/intraday-pool",
        json={"base_revision": 0, "selected_symbols": ["INTC"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["active"]["member_symbols"] == ["INTC", "SMH", "SPY", "QQQ", "IWM"]
    assert payload["pending"]["effective_date"] == "2026-10-20"
    assert payload["pending"]["member_symbols"] == ["INTC", "SPY", "QQQ", "IWM"]
    universe_after = client.get("/api/settings/universe").json()
    assert universe_after["version_id"] == universe_before["version_id"]
    assert client.get("/api/settings").json()["schedule"]["post_close_observation"]["enabled"] is False

    today_plan = client.get(
        "/api/intraday/collection-plan?trading_date=2026-10-19"
    ).json()
    next_plan = client.get(
        "/api/intraday/collection-plan?trading_date=2026-10-20"
    ).json()
    assert today_plan["member_symbols"] == payload["active"]["member_symbols"]
    assert next_plan["member_symbols"] == payload["pending"]["member_symbols"]
    assert today_plan["display_group_by_symbol"]["SPY"] == "大盘基准"


def test_fixed_benchmarks_cannot_be_removed_from_quote_or_minute_plan(pool_client, monkeypatch):
    from app.api import intraday_pool

    client, _app = pool_client
    monkeypatch.setattr(intraday_pool, "market_today", lambda _settings: date(2026, 10, 19))

    response = client.put(
        "/api/settings/intraday-pool",
        json={"base_revision": 0, "selected_symbols": ["INTC"]},
    )
    plan = client.get(
        "/api/intraday/collection-plan?trading_date=2026-10-20"
    ).json()

    assert response.status_code == 200
    assert all(symbol in plan["quote_symbols"] for symbol in ("SPY", "QQQ", "IWM"))
    assert all(symbol in plan["minute_symbols"] for symbol in ("SPY", "QQQ", "IWM"))
    assert {plan["display_group_by_symbol"][symbol] for symbol in ("SPY", "QQQ", "IWM")} == {
        "大盘基准"
    }


def test_runtime_settings_update_preserves_intraday_pool_payload(pool_client, monkeypatch):
    from app.api import intraday_pool

    client, _app = pool_client
    monkeypatch.setattr(intraday_pool, "market_today", lambda _settings: date(2026, 10, 19))
    saved = client.put(
        "/api/settings/intraday-pool",
        json={"base_revision": 0, "selected_symbols": ["INTC"]},
    )
    assert saved.status_code == 200
    settings = client.get("/api/settings").json()
    settings["schedule"]["post_close_observation"]["enabled"] = False
    updated = client.put(
        "/api/settings",
        json={"revision": settings["revision"], "schedule": settings["schedule"]},
    )

    assert updated.status_code == 200
    after = client.get("/api/settings/intraday-pool").json()
    assert after["pending"]["member_symbols"] == ["INTC", "SPY", "QQQ", "IWM"]
    assert after["revision"] == saved.json()["revision"]


def test_second_same_day_save_replaces_pending_only(pool_client, monkeypatch):
    from app.api import intraday_pool

    client, _app = pool_client
    monkeypatch.setattr(intraday_pool, "market_today", lambda _settings: date(2026, 10, 19))
    first = client.put(
        "/api/settings/intraday-pool",
        json={"base_revision": 0, "selected_symbols": ["INTC"]},
    ).json()
    second_response = client.put(
        "/api/settings/intraday-pool",
        json={"base_revision": first["revision"], "selected_symbols": ["SMH"]},
    )

    assert second_response.status_code == 200
    second = second_response.json()
    assert second["active"]["member_symbols"] == first["active"]["member_symbols"]
    assert second["pending"]["effective_date"] == "2026-10-20"
    assert second["pending"]["member_symbols"] == ["SMH", "SPY", "QQQ", "IWM"]


def test_save_on_effective_date_promotes_pending_before_staging_new_pool(
    pool_client, monkeypatch
):
    from app.api import intraday_pool

    client, _app = pool_client
    monkeypatch.setattr(intraday_pool, "market_today", lambda _settings: date(2026, 10, 19))
    first = client.put(
        "/api/settings/intraday-pool",
        json={"base_revision": 0, "selected_symbols": ["INTC"]},
    ).json()
    previous_pending = first["pending"]
    monkeypatch.setattr(intraday_pool, "market_today", lambda _settings: date(2026, 10, 20))

    second = client.put(
        "/api/settings/intraday-pool",
        json={"base_revision": first["revision"], "selected_symbols": ["SMH"]},
    ).json()

    assert second["active"]["member_symbols"] == previous_pending["member_symbols"]
    assert second["active"]["revision"] == previous_pending["revision"]
    assert second["pending"]["effective_date"] == "2026-10-21"


def test_manual_pool_can_be_staged_without_legacy_capture_but_today_stays_unavailable(
    tmp_path: Path, monkeypatch
):
    from app.api import intraday_pool

    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'urus.db'}",
        observation_universe_source_url="",
        enabled_symbols="QQQ",
        instrument_validation_symbols="INTC,SMH",
        sidecar_capture_database_path=str(tmp_path / "missing.sqlite"),
        moomoo_enabled=False,
        fred_enabled=False,
        yahoo_enabled=False,
        anomalo_workflow_enabled=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        client.get("/api/settings/universe")
        monkeypatch.setattr(intraday_pool, "market_today", lambda _settings: date(2026, 10, 19))
        saved = client.put(
            "/api/settings/intraday-pool",
            json={"base_revision": 0, "selected_symbols": ["INTC"]},
        )
        today_plan = client.get(
            "/api/intraday/collection-plan?trading_date=2026-10-19"
        )
        next_plan = client.get(
            "/api/intraday/collection-plan?trading_date=2026-10-20"
        )

    assert saved.status_code == 200
    assert saved.json()["status"] == "pending_only"
    assert today_plan.status_code == 409
    assert next_plan.status_code == 200
    assert next_plan.json()["member_symbols"] == ["INTC", "SPY", "QQQ", "IWM"]


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 11, 25), date(2026, 11, 27)),
        (date(2026, 11, 27), date(2026, 11, 30)),
        (date(2026, 11, 28), date(2026, 11, 30)),
        (date(2026, 3, 6), date(2026, 3, 9)),
    ],
)
def test_next_session_skips_weekends_holidays_and_dst_dates(today, expected):
    from app.api.intraday_pool import next_trading_session

    assert next_trading_session(today, "XNYS") == expected


def test_plan_rejects_non_session_date(pool_client):
    client, _app = pool_client

    response = client.get("/api/intraday/collection-plan?trading_date=2026-10-18")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "intraday_collection_plan_invalid"


def test_capture_projection_uses_frozen_display_group_and_legacy_is_null():
    from app.decision_harness.contracts import content_sha256
    from app.intraday.equities import build_intraday_projection
    from test_intraday_equities_acceptance import CAPTURE_CUTOFF, _capture_contract

    contract = _capture_contract([], cutoff=CAPTURE_CUTOFF)
    contract["data"]["display_group_by_symbol"] = {
        "AAA": "半导体",
        "SPY": "大盘基准",
    }
    contract["content_sha256"] = content_sha256(contract["data"])
    projected = build_intraday_projection(contract, now=CAPTURE_CUTOFF)
    assert projected["symbols"]["AAA"]["observation_group"] == "半导体"
    assert projected["symbols"]["SPY"]["observation_group"] == "大盘基准"

    contract["data"].pop("display_group_by_symbol")
    contract["content_sha256"] = content_sha256(contract["data"])
    legacy = build_intraday_projection(contract, now=CAPTURE_CUTOFF)
    assert legacy["symbols"]["AAA"]["observation_group"] is None
