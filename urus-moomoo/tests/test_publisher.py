from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from urus_moomoo.config import CollectionTask
from urus_moomoo import publisher as publisher_module
from urus_moomoo.publisher import ObservationPublisher


def _contract(kind: str, capture_id: str, symbols=("SPY", "QQQ")):
    rows_key = "instruments" if kind == "instrument_cards" else "symbols"
    data = {
        "available": True,
        "unavailable_symbols": [],
        rows_key: [{"symbol": symbol} for symbol in symbols],
    }
    if kind == "instrument_cards":
        data["quality_status"] = "ok"
    canonical = json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return {
        "schema_version": "urus.moomoo.capture.v2",
        "capture_id": capture_id,
        "kind": kind,
        "task_name": (
            "market-post-close" if kind == "instrument_cards" else "options-snapshot-post-close"
        ),
        "scheduled_at": "2026-09-08T21:30:00+00:00",
        "trading_date": "2026-09-08",
        "content_sha256": hashlib.sha256(canonical).hexdigest(),
        "provider": "moomoo_openapi",
        "captured_at": "2026-09-08T21:00:00+00:00",
        "requested_symbols": list(symbols),
        "data": data,
    }


def _row(contract):
    return {"payload_json": json.dumps(contract, ensure_ascii=False, sort_keys=True)}


def _task(kind: str):
    name = "market-post-close" if kind == "instrument_cards" else "options-snapshot-post-close"
    return CollectionTask(name, True, "post-close", ("SPY", "QQQ"), kind)


def _plan_loader(trading_date: str):
    return {
        "schema_version": "urus.observation.collection_plan.v1",
        "plan_id": "a" * 64,
        "trading_date": trading_date,
        "universe_revision_id": None,
        "universe_freshness": "local",
        "group_ids": [],
        "group_version_ids": [],
        "market_symbols": ["SPY", "QQQ"],
        "options_symbols": ["SPY", "QQQ"],
    }


def test_publishes_only_after_exact_complete_pair(tmp_path: Path) -> None:
    contracts = {
        "market-1": _contract("instrument_cards", "market-1"),
        "options-1": _contract("options_snapshot", "options-1"),
    }
    calls = []

    def post(url, body, timeout):
        payload = json.loads(body)
        calls.append((url, payload, timeout))
        return 201, {
            "run_id": "run-1",
            "status": "succeeded",
            "source_captures": {
                "market": {"capture_id": payload["source_captures"]["market_capture_id"]},
                "options": {"capture_id": payload["source_captures"]["options_capture_id"]},
            },
        }

    publisher = ObservationPublisher(
        api_url="http://urus:8000",
        timeout_seconds=7,
        state_path=tmp_path / "publisher.sqlite",
        market_timezone="America/New_York",
        capture_reader=lambda capture_id: _row(contracts[capture_id]),
        post=post,
        plan_loader=_plan_loader,
    )
    scheduled_at = datetime(2026, 9, 9, 6, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    try:
        assert publisher.observe(
            _task("instrument_cards"), scheduled_at, {"contract": contracts["market-1"]}
        )
        assert calls == []
        assert publisher.observe(
            _task("options_snapshot"), scheduled_at, {"contract": contracts["options-1"]}
        )
        assert len(calls) == 1
        url, payload, timeout = calls[0]
        assert url == "http://urus:8000/api/observation/runs"
        assert timeout == 7
        assert payload["trading_date"] == "2026-09-08"
        assert payload["trigger_mode"] == "scheduled"
        locators = payload["source_captures"]
        assert locators["market_capture_id"] == "market-1"
        assert locators["options_capture_id"] == "options-1"
        assert locators["market_content_sha256"] == contracts["market-1"]["content_sha256"]
        assert publisher.publish_if_ready("2026-09-08") == "run-1"
        assert len(calls) == 1
    finally:
        publisher.close()


def test_incomplete_capture_is_not_recorded(tmp_path: Path) -> None:
    contract = _contract("options_snapshot", "options-1")
    contract["data"]["unavailable_symbols"] = ["QQQ"]
    publisher = ObservationPublisher(
        api_url="http://urus:8000",
        timeout_seconds=1,
        state_path=tmp_path / "publisher.sqlite",
        market_timezone="America/New_York",
        capture_reader=lambda _capture_id: _row(contract),
        post=lambda *_args: pytest.fail("must not publish"),
        plan_loader=_plan_loader,
    )
    try:
        assert not publisher.observe(
            _task("options_snapshot"),
            datetime(2026, 9, 8, 18, tzinfo=ZoneInfo("America/New_York")),
            {"contract": contract},
        )
    finally:
        publisher.close()


def test_http_failure_remains_retryable(tmp_path: Path) -> None:
    contracts = {
        "market-1": _contract("instrument_cards", "market-1"),
        "options-1": _contract("options_snapshot", "options-1"),
    }
    attempts = 0

    def post(_url, body, _timeout):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary failure")
        locators = json.loads(body)["source_captures"]
        return 201, {
            "run_id": "run-2",
            "status": "succeeded",
            "source_captures": {
                "market": {"capture_id": locators["market_capture_id"]},
                "options": {"capture_id": locators["options_capture_id"]},
            },
        }

    publisher = ObservationPublisher(
        api_url="http://urus:8000",
        timeout_seconds=1,
        state_path=tmp_path / "publisher.sqlite",
        market_timezone="America/New_York",
        capture_reader=lambda capture_id: _row(contracts[capture_id]),
        post=post,
        plan_loader=_plan_loader,
    )
    scheduled_at = datetime(2026, 9, 8, 18, tzinfo=ZoneInfo("America/New_York"))
    try:
        publisher.observe(
            _task("instrument_cards"), scheduled_at, {"contract": contracts["market-1"]}
        )
        with pytest.raises(RuntimeError, match="temporary failure"):
            publisher.observe(
                _task("options_snapshot"), scheduled_at, {"contract": contracts["options-1"]}
            )
        assert publisher.resume_if_ready(_task("options_snapshot"), scheduled_at) == "options-1"
        assert attempts == 2
    finally:
        publisher.close()


def test_rejects_success_response_for_different_locators(tmp_path: Path) -> None:
    contracts = {
        "market-1": _contract("instrument_cards", "market-1"),
        "options-1": _contract("options_snapshot", "options-1"),
    }
    publisher = ObservationPublisher(
        api_url="http://urus:8000",
        timeout_seconds=1,
        state_path=tmp_path / "publisher.sqlite",
        market_timezone="America/New_York",
        capture_reader=lambda capture_id: _row(contracts[capture_id]),
        post=lambda *_args: (
            201,
            {
                "run_id": "wrong-run",
                "status": "succeeded",
                "source_captures": {
                    "market": {"capture_id": "other"},
                    "options": {"capture_id": "options-1"},
                },
            },
        ),
        plan_loader=_plan_loader,
    )
    scheduled_at = datetime(2026, 9, 8, 18, tzinfo=ZoneInfo("America/New_York"))
    try:
        publisher.observe(
            _task("instrument_cards"), scheduled_at, {"contract": contracts["market-1"]}
        )
        with pytest.raises(RuntimeError, match="rejected"):
            publisher.observe(
                _task("options_snapshot"), scheduled_at, {"contract": contracts["options-1"]}
            )
    finally:
        publisher.close()


def test_rejects_tampered_content_checksum(tmp_path: Path) -> None:
    contract = _contract("instrument_cards", "market-1")
    contract["content_sha256"] = "0" * 64
    publisher = ObservationPublisher(
        api_url="http://urus:8000",
        timeout_seconds=1,
        state_path=tmp_path / "publisher.sqlite",
        market_timezone="America/New_York",
        capture_reader=lambda _capture_id: _row(contract),
        post=lambda *_args: pytest.fail("must not publish"),
        plan_loader=_plan_loader,
    )
    try:
        with pytest.raises(RuntimeError, match="content_sha256 mismatch"):
            publisher.observe(
                _task("instrument_cards"),
                datetime(2026, 9, 8, 18, tzinfo=ZoneInfo("America/New_York")),
                {"contract": contract},
            )
    finally:
        publisher.close()


def test_default_plan_loader_fetches_collection_plan(monkeypatch, tmp_path: Path) -> None:
    requests = []

    def post(url, body, timeout):
        requests.append(("POST", url, body, timeout))
        return 200, {"universe_revision_id": "revision-1"}

    def get(url, timeout):
        requests.append(("GET", url, None, timeout))
        return 200, {
            "schema_version": "urus.observation.collection_plan.v1",
            "plan_id": "a" * 64,
            "trading_date": "2026-09-08",
            "universe_revision_id": "revision-1",
            "universe_freshness": "current",
            "group_ids": [],
            "group_version_ids": [],
            "market_symbols": ["SPY", "QQQ"],
            "options_symbols": ["SPY", "QQQ"],
        }

    monkeypatch.setattr(publisher_module, "_get_json", get)
    publisher = ObservationPublisher(
        api_url="http://192.168.67.1:7777/",
        timeout_seconds=7,
        state_path=tmp_path / "publisher.sqlite",
        market_timezone="America/New_York",
        capture_reader=lambda _capture_id: pytest.fail("must not read a capture"),
        post=post,
    )
    try:
        plan = publisher._plan("2026-09-08")
    finally:
        publisher.close()

    assert plan["universe_revision_id"] == "revision-1"
    assert requests == [
        ("POST", "http://192.168.67.1:7777/api/observation/groups/sync", b"{}", 7),
        (
            "GET",
            "http://192.168.67.1:7777/api/observation/collection-plan?"
            "trading_date=2026-09-08&universe_revision_id=revision-1",
            None,
            7,
        ),
    ]


def test_recovery_only_verifies_new_captures_and_rechecks_on_restart(tmp_path, monkeypatch):
    from datetime import UTC
    from urus_moomoo.storage.sqlite import CollectorStore
    store = CollectorStore(tmp_path / 'captures.sqlite')
    contract = _contract('instrument_cards', 'market-1')
    store.write_raw('market-1', 'instrument_cards', contract, schema_version=contract['schema_version'])
    store.mark_scheduler_completed('market-post-close', datetime(2026, 9, 8, 21, 30, tzinfo=UTC), capture_id='market-1')
    calls = []
    verify = publisher_module._verified_content_sha256
    def tracked(value):
        calls.append(value['capture_id'])
        return verify(value)
    monkeypatch.setattr(publisher_module, '_verified_content_sha256', tracked)
    def build():
        return ObservationPublisher(api_url='http://unused', timeout_seconds=1,
            state_path=tmp_path / 'publisher.sqlite', market_timezone='America/New_York',
            capture_reader=store.read_raw, capture_database_path=store.path)
    # CollectorStore's connection is committed before recovery reads it.
    publisher = build()
    try:
        publisher.discover_completed_captures()
        publisher.discover_completed_captures()
        assert calls == ['market-1']
        second = _contract('options_snapshot', 'options-1')
        store.write_raw('options-1', 'options_snapshot', second, schema_version=second['schema_version'])
        store.mark_scheduler_completed('options-snapshot-post-close', datetime(2026, 9, 8, 21, 35, tzinfo=UTC), capture_id='options-1')
        publisher.discover_completed_captures()
        assert calls == ['market-1', 'options-1']
    finally:
        publisher.close()
    publisher = build()
    publisher.close()
    store.close()
    assert len(calls) == 4


def test_retry_failure_does_not_starve_later_date(tmp_path, monkeypatch):
    publisher = ObservationPublisher(api_url='http://unused', timeout_seconds=1,
        state_path=tmp_path / 'publisher.sqlite', market_timezone='UTC', capture_reader=lambda _: None)
    for day in ['2026-09-08', '2026-09-09']:
        publisher.connection.execute('INSERT INTO observation_publish_captures VALUES (?, ?, ?, ?, ?)',
                                     (day, 'instrument_cards', day, 'hash', 'now'))
    publisher.connection.commit()
    calls = []
    def publish(day):
        calls.append(day)
        if day == '2026-09-08':
            raise TimeoutError('offline')
        return 'run-2'
    monkeypatch.setattr(publisher, 'publish_if_ready', publish)
    try:
        assert publisher.retry_pending() == []
        assert publisher.retry_pending() == ['run-2']
        assert publisher.retry_pending() == []
        assert calls == ['2026-09-08', '2026-09-09']
    finally:
        publisher.close()
