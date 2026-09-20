from datetime import UTC, date, datetime, timedelta
import json
import sqlite3

import pytest

from app.decision_harness.contracts import content_sha256
from app.integrations.sidecar_captures import (
    SidecarCaptureStore,
    SidecarMarketCaptureAdapter,
)
from app.schemas.observation import ObservationSourceCaptures
from app.services.capital_flow import completed_session_dates


def _contract(
    capture_id: str,
    *,
    kind: str,
    task_name: str,
    symbols: list[str],
    data: dict,
) -> dict:
    return {
        "schema_version": "urus.moomoo.capture.v2",
        "capture_id": capture_id,
        "kind": kind,
        "task_name": task_name,
        "scheduled_at": "2026-09-08T16:30:00-04:00",
        "trading_date": "2026-09-08",
        "content_sha256": content_sha256(data),
        "provider": "moomoo_openapi",
        "captured_at": datetime(2026, 9, 8, 20, 35, tzinfo=UTC).isoformat(),
        "requested_symbols": symbols,
        "data": data,
    }


def _database(tmp_path, market: dict, options: dict):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "collector.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE raw_captures (capture_id TEXT PRIMARY KEY, "
        "schema_version TEXT, kind TEXT, captured_at TEXT, payload_json TEXT)"
    )
    for contract in (market, options):
        connection.execute(
            "INSERT INTO raw_captures VALUES (?, ?, ?, ?, ?)",
            (
                contract["capture_id"],
                contract["schema_version"],
                contract["kind"],
                contract["captured_at"],
                json.dumps(contract),
            ),
        )
    connection.commit()
    connection.close()
    return path


def test_capture_store_loads_exact_verified_market_and_options_pair(tmp_path) -> None:
    market_data = {
        "available": True,
        "quality_status": "ok",
        "instruments": [
            {"symbol": "QQQ", "quality_status": "ok"},
            {"symbol": "INTC", "quality_status": "ok"},
        ],
        "history_snapshots": [],
    }
    options_data = {
        "is_mock": False,
        "available": True,
        "status": "available",
        "symbols": [{"symbol": "QQQ"}, {"symbol": "INTC"}],
    }
    market = _contract(
        "market-1",
        kind="instrument_cards",
        task_name="market-post-close",
        symbols=["QQQ", "INTC"],
        data=market_data,
    )
    options = _contract(
        "options-1",
        kind="options_snapshot",
        task_name="options-snapshot-post-close",
        symbols=["QQQ", "INTC"],
        data=options_data,
    )
    path = _database(tmp_path, market, options)

    loaded_market, loaded_options = SidecarCaptureStore(
        path, market_timezone="America/New_York"
    ).load_pair(
        ObservationSourceCaptures(
            market_capture_id="market-1",
            market_content_sha256=content_sha256(market_data),
            options_capture_id="options-1",
            options_content_sha256=content_sha256(options_data),
        ),
        trading_date=date(2026, 9, 8),
    )

    assert loaded_market.data == market_data
    assert loaded_options.data == options_data
    assert loaded_options.provenance()["task_name"] == "options-snapshot-post-close"


def test_capture_store_rejects_checksum_task_and_date_mismatches(tmp_path) -> None:
    market = _contract(
        "market-1",
        kind="instrument_cards",
        task_name="wrong-task",
        symbols=["QQQ"],
        data={"history_snapshots": []},
    )
    options = _contract(
        "options-1",
        kind="options_snapshot",
        task_name="options-snapshot-post-close",
        symbols=["QQQ"],
        data={"symbols": []},
    )
    path = _database(tmp_path, market, options)
    store = SidecarCaptureStore(path, market_timezone="America/New_York")

    with pytest.raises(ValueError, match="task_name mismatch"):
        store.load_pair(
            ObservationSourceCaptures(
                market_capture_id="market-1",
                market_content_sha256=content_sha256(market["data"]),
                options_capture_id="options-1",
                options_content_sha256=content_sha256(options["data"]),
            ),
            trading_date=date(2026, 9, 8),
        )

    market["task_name"] = "market-post-close"
    market["content_sha256"] = "0" * 64
    path = _database(tmp_path / "tampered", market, options)
    with pytest.raises(ValueError, match="content_sha256 mismatch"):
        SidecarCaptureStore(path, market_timezone="America/New_York").load_pair(
            ObservationSourceCaptures(
                market_capture_id="market-1",
                market_content_sha256=content_sha256(market["data"]),
                options_capture_id="options-1",
                options_content_sha256=content_sha256(options["data"]),
            ),
            trading_date=date(2026, 9, 8),
        )


def test_capture_adapter_fails_closed_on_symbol_coverage() -> None:
    market = _contract(
        "market-1",
        kind="instrument_cards",
        task_name="market-post-close",
        symbols=["QQQ"],
        data={"history_snapshots": []},
    )
    # Construct through the validated immutable representation used by the adapter.
    from app.integrations.sidecar_captures import LoadedCapture

    loaded = LoadedCapture(
        capture_id="market-1",
        kind="instrument_cards",
        captured_at=datetime.fromisoformat(market["captured_at"]),
        content_sha256=market["content_sha256"],
        requested_symbols=("QQQ",),
        data=market["data"],
        task_name="market-post-close",
        trading_date=date(2026, 9, 8),
    )

    with pytest.raises(ValueError, match="does not cover symbols: INTC"):
        SidecarMarketCaptureAdapter(loaded).assert_covers(["QQQ", "INTC"])


def test_observation_run_and_options_api_consume_verified_sidecar_captures(
    client, tmp_path
) -> None:
    now = datetime.now(UTC)
    session_dates = completed_session_dates(now, "XNYS", count=260)
    trading_date = session_dates[-1]
    bars = [
        {
            "date": item.isoformat(),
            "open": 100.0 + index,
            "high": 101.0 + index,
            "low": 99.0 + index,
            "close": 100.5 + index,
            "volume": 1_000_000 + index,
        }
        for index, item in enumerate(session_dates)
    ]
    market_data = {
        "available": True,
        "quality_status": "ok",
        "instruments": [{"symbol": "QQQ"}],
        "is_mock": False,
        "requested_symbols": ["QQQ"],
        "history_snapshots": [
            {"symbol": "QQQ", "asset_type": "etf", "history": {"bars": bars}}
        ],
    }
    close = bars[-1]["close"]
    options_data = {
        "is_mock": False,
        "status": "available",
        "available": True,
        "provider": "moomoo_openapi",
        "source_mode": "snapshot",
        "captured_at": now.isoformat(),
        "requested_symbols": ["QQQ"],
        "unavailable_symbols": [],
        "symbols": [
            {
                "symbol": "QQQ",
                "spot": close,
                "spot_time": now.isoformat(),
                "overview": {},
                "expirations": [
                    {
                        "expiration": (trading_date + timedelta(days=7)).isoformat(),
                        "days_to_expiry": 7,
                        "contract_count": 1,
                        "max_pain": close,
                        "expected_move": {},
                        "exposure": {},
                    }
                ],
            }
        ],
    }
    market = _contract(
        "market-api-1",
        kind="instrument_cards",
        task_name="market-post-close",
        symbols=["QQQ"],
        data=market_data,
    )
    options = _contract(
        "options-api-1",
        kind="options_snapshot",
        task_name="options-snapshot-post-close",
        symbols=["QQQ"],
        data=options_data,
    )
    for contract in (market, options):
        contract["trading_date"] = trading_date.isoformat()
        contract["captured_at"] = now.isoformat()
    database = _database(tmp_path, market, options)
    settings = client.app.state.settings
    settings.sidecar_capture_database_path = str(database)
    settings.options_target_symbols = "QQQ"
    settings.options_watchlist_symbols = ""
    settings.enabled_symbols = ""
    created = client.post(
        "/api/observation/groups",
        json={
            "group_id": "sidecar-observation",
            "display_name": "Sidecar observation",
            "symbols": ["QQQ"],
        },
    )
    assert created.status_code == 201, created.text
    plan_response = client.get(
        "/api/observation/collection-plan",
        params={"trading_date": trading_date.isoformat()},
    )
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()

    response = client.post(
        "/api/observation/runs",
        json={
            "trigger_mode": "scheduled",
                "trading_date": trading_date.isoformat(),
                "group_ids": ["sidecar-observation"],
                "group_version_ids": [created.json()["version_id"]],
                "collection_plan_id": plan["plan_id"],
                "source_captures": {
                "market_capture_id": "market-api-1",
                "market_content_sha256": content_sha256(market_data),
                "options_capture_id": "options-api-1",
                "options_content_sha256": content_sha256(options_data),
            },
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["source_captures"]["market"]["capture_id"] == "market-api-1"
    selected = client.get(
        "/api/observation/options",
        params={"trading_date": trading_date.isoformat(), "symbol": "QQQ"},
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["run_id"] == body["run_id"]
    assert selected.json()["available"] is True
