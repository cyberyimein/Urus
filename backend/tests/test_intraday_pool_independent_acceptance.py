"""Independent acceptance checks for the intraday pool bootstrap contract.

These cases intentionally use a temporary sidecar and the public HTTP routes.
They do not read a deployment-owned database or contact a market-data provider.
The file is kept separate from the implementation team's pool test module so a
bootstrap regression remains visible during the final review.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
import json
import sqlite3

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.decision_harness.contracts import content_sha256


BENCHMARKS = ["SPY", "QQQ", "IWM"]


def _write_sidecar_capture(
    path: Path,
    symbols: list[str],
    *,
    capture_id: str,
    scheduled_at: str,
    complete: bool,
) -> None:
    data = {
        "schema_version": "moomoo-intraday-equities-refs-v1",
        "provider": "moomoo_openapi",
        "trading_date": scheduled_at[:10],
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
        "trading_date": scheduled_at[:10],
        "captured_at": scheduled_at,
        "requested_symbols": list(symbols),
        "content_sha256": content_sha256(data),
        "data": data,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
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
                capture_id,
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
                capture_id,
                contract["kind"],
                int(complete),
                contract["captured_at"],
            ),
        )


def _settings(
    tmp_path: Path,
    sidecar: Path,
    symbols: list[str],
    *,
    catalog_symbols: list[str] | None = None,
    disable_option_defaults: bool = False,
) -> Settings:
    # Keep the configured validation list deliberately small.  A successful
    # bootstrap must come from the sidecar requested_symbols, not this default.
    catalog = list(catalog_symbols or symbols)
    validation = [symbol for symbol in symbols[:18] if symbol in catalog]
    return Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'urus.db'}",
        observation_universe_source_url="",
        enabled_symbols=",".join(catalog),
        instrument_validation_symbols=",".join(validation),
        moomoo_market_symbols=",".join(symbol for symbol in BENCHMARKS if symbol in catalog),
        options_target_symbols="" if disable_option_defaults else "SPY,QQQ,SMH,IGV",
        options_watchlist_symbols="" if disable_option_defaults else (
            "LITE,COHR,MRVL,NOK,AMD,INTC,NVDA,NBIS,ORCL,MSFT,NOW,RKLB,AMZN,AAPL,GOOG"
        ),
        sidecar_capture_database_path=str(sidecar),
        moomoo_enabled=False,
        fred_enabled=False,
        yahoo_enabled=False,
        anomalo_workflow_enabled=False,
    )


def test_bootstrap_uses_latest_partial_46_symbol_capture_and_pins_benchmarks(tmp_path: Path) -> None:
    symbols = [f"EQ{index:02d}" for index in range(43)] + BENCHMARKS
    sidecar = tmp_path / "collector.sqlite"
    _write_sidecar_capture(
        sidecar,
        symbols,
        capture_id="complete-old",
        scheduled_at="2026-10-16T19:55:00+00:00",
        complete=True,
    )
    _write_sidecar_capture(
        sidecar,
        symbols,
        capture_id="partial-latest",
        scheduled_at="2026-10-19T14:36:00+00:00",
        complete=False,
    )

    with TestClient(create_app(_settings(tmp_path, sidecar, symbols))) as client:
        # Ensure the local default Universe is materialized from the fixture
        # settings before the pool route resolves its catalog.
        assert client.get("/api/settings/universe").status_code == 200
        settings = client.get("/api/settings/intraday-pool")
        plan = client.get("/api/intraday/collection-plan?trading_date=2026-10-19")

    assert settings.status_code == 200
    active = settings.json()["active"]
    assert settings.json()["status"] == "configured"
    assert active["source_capture_id"] == "partial-latest"
    assert active["member_symbols"] == symbols

    assert plan.status_code == 200
    plan_payload = plan.json()
    assert plan_payload["member_symbols"] == symbols
    assert plan_payload["quote_symbols"] == symbols
    assert plan_payload["minute_symbols"] == symbols
    assert plan_payload["benchmark_symbols"] == BENCHMARKS
    for symbol in BENCHMARKS:
        assert symbol in plan_payload["quote_symbols"]
        assert symbol in plan_payload["minute_symbols"]


def test_missing_fixed_benchmark_in_universe_is_structured_unavailable(tmp_path: Path) -> None:
    symbols = ["AAPL", *BENCHMARKS]
    sidecar = tmp_path / "collector.sqlite"
    _write_sidecar_capture(
        sidecar,
        symbols,
        capture_id="benchmark-directory-mismatch",
        scheduled_at="2026-10-19T14:36:00+00:00",
        complete=False,
    )
    # The sidecar mentions all fixed benchmarks, while the local Universe
    # deliberately omits QQQ.  The API must return a domain error, not a raw
    # KeyError from catalog lookup.
    catalog = ["AAPL", "SPY", "IWM"]
    with TestClient(create_app(_settings(
        tmp_path,
        sidecar,
        symbols,
        catalog_symbols=catalog,
        disable_option_defaults=True,
    ))) as client:
        assert client.get("/api/settings/universe").status_code == 200
        response = client.get("/api/settings/intraday-pool")

    assert response.status_code == 409
    payload = response.json()
    assert payload["error"]["code"] == "intraday_pool_baseline_unavailable"


def test_after_pending_promotion_an_earlier_session_is_not_reinterpreted_as_new_pool(
    tmp_path: Path,
    monkeypatch,
) -> None:
    symbols = ["AAPL", *BENCHMARKS]
    sidecar = tmp_path / "collector.sqlite"
    _write_sidecar_capture(
        sidecar,
        symbols,
        capture_id="promotion-history",
        scheduled_at="2026-10-19T14:36:00+00:00",
        complete=False,
    )
    settings = _settings(tmp_path, sidecar, symbols)
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/settings/universe").status_code == 200
        from app.api import intraday_pool

        monkeypatch.setattr(intraday_pool, "market_today", lambda _settings: date(2026, 10, 19))
        first = client.put(
            "/api/settings/intraday-pool",
            json={"base_revision": 0, "selected_symbols": ["AAPL"]},
        )
        assert first.status_code == 200

        # On the next XNYS session the existing pending pool is promoted before
        # a new selection is scheduled.  Asking for the already-closed prior
        # session must never reinterpret it using the newly active pool.
        monkeypatch.setattr(intraday_pool, "market_today", lambda _settings: date(2026, 10, 20))
        second = client.put(
            "/api/settings/intraday-pool",
            json={
                "base_revision": first.json()["revision"],
                "selected_symbols": ["AAPL"],
            },
        )
        assert second.status_code == 200
        earlier = client.get("/api/intraday/collection-plan?trading_date=2026-10-19")

    assert earlier.status_code == 409
    assert earlier.json()["error"]["code"] == "intraday_pool_unavailable"
