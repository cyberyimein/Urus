from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path

import pytest

from urus_moomoo.config import CollectionTask
from urus_moomoo.storage.sqlite import CollectorStore


def _plan(trading_date: str, members: list[str] | None = None) -> dict[str, object]:
    members = members or ["AAA", "SPY", "QQQ", "IWM"]
    groups = {symbol: ("大盘基准" if symbol in {"SPY", "QQQ", "IWM"} else "半导体")
              for symbol in members}
    definition: dict[str, object] = {
        "schema_version": "urus.intraday.collection_plan.v1",
        "status": "configured",
        "trading_date": trading_date,
        "pool_revision": 1,
        "selected_symbols": [symbol for symbol in members if symbol not in {"SPY", "QQQ", "IWM"}],
        "benchmark_symbols": ["SPY", "QQQ", "IWM"],
        "member_symbols": members,
        "quote_symbols": members,
        "minute_symbols": members,
        "display_group_by_symbol": groups,
    }
    canonical = json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"plan_id": hashlib.sha256(canonical.encode()).hexdigest(), **definition}


def _task() -> CollectionTask:
    return CollectionTask(
        "equity-intraday",
        True,
        "intraday",
        ("AAA", "BBB", "SPY", "QQQ", "IWM"),
        "capture_intraday_equities",
        params={
            "interval_seconds": 300,
            "equity_shards_enabled": True,
            "equity_shard_symbols": {
                "G1": ["AAA", "SPY"],
                "G2": ["BBB", "QQQ"],
                "G3": ["IWM"],
            },
            "groups": {"A": ["SPY"], "B": ["AAA"], "C": ["QQQ", "IWM"], "D": ["BBB"]},
        },
    )






def test_collector_store_freezes_one_plan_per_trading_date(tmp_path: Path) -> None:
    store = CollectorStore(tmp_path / "collector.sqlite")
    first = _plan("2026-10-19")
    changed = _plan("2026-10-19", ["NEW", "SPY", "QQQ", "IWM"])
    try:
        stored = store.freeze_intraday_collection_plan("2026-10-19", first)
        repeated = store.freeze_intraday_collection_plan("2026-10-19", changed)

        assert stored["plan_id"] == first["plan_id"]
        assert repeated["plan_id"] == first["plan_id"]
        assert store.get_intraday_collection_plan("2026-10-19") == first
        assert store.get_intraday_collection_plan("2026-10-20") is None
    finally:
        store.close()


def test_plan_loader_gets_and_persists_once_then_returns_frozen_copy(tmp_path: Path) -> None:
    from urus_moomoo.intraday_pool import IntradayPoolPlanLoader

    calls: list[str] = []
    plan = _plan("2026-10-19")

    def get(url: str, _timeout: float):
        calls.append(url)
        return 200, plan

    store = CollectorStore(tmp_path / "collector.sqlite")
    loader = IntradayPoolPlanLoader(
        api_url="http://urus:8000",
        timeout_seconds=5,
        store=store,
        get=get,
    )
    try:
        first = loader.plan_for("2026-10-19")
        changed_remote = _plan("2026-10-19", ["NEW", "SPY", "QQQ", "IWM"])
        plan.update(changed_remote)
        second = loader.plan_for("2026-10-19")

        assert first["plan_id"] != changed_remote["plan_id"]
        assert second["plan_id"] == first["plan_id"]
        assert len(calls) == 1
        assert calls[0] == (
            "http://urus:8000/api/intraday/collection-plan?trading_date=2026-10-19"
        )
    finally:
        store.close()


def test_plan_loader_rejects_missing_plan_instead_of_static_fallback(tmp_path: Path) -> None:
    from urus_moomoo.intraday_pool import IntradayPoolPlanLoader

    store = CollectorStore(tmp_path / "collector.sqlite")
    loader = IntradayPoolPlanLoader(
        api_url="http://urus:8000",
        timeout_seconds=5,
        store=store,
        get=lambda _url, _timeout: (409, {"status": "unconfigured"}),
    )
    try:
        with pytest.raises(RuntimeError, match="unavailable"):
            loader.plan_for("2026-10-19")
        assert store.get_intraday_collection_plan("2026-10-19") is None
    finally:
        store.close()


def test_task_preparation_uses_plan_symbols_and_preserves_internal_roles(tmp_path: Path) -> None:
    from urus_moomoo.intraday_pool import IntradayPoolPlanLoader

    plan = _plan("2026-10-19", ["AAA", "NEW", "SPY", "QQQ", "IWM"])
    store = CollectorStore(tmp_path / "collector.sqlite")
    loader = IntradayPoolPlanLoader(
        api_url="http://urus:8000",
        timeout_seconds=5,
        store=store,
        get=lambda _url, _timeout: (200, plan),
    )
    try:
        prepared = loader.prepare_task(_task(), date(2026, 10, 19))
    finally:
        store.close()

    assert prepared.symbols == tuple(plan["member_symbols"])
    assert prepared.params["pool_plan_id"] == plan["plan_id"]
    assert prepared.params["display_group_by_symbol"] == plan["display_group_by_symbol"]
    assert prepared.params["groups"] == {
        "A": ["SPY"],
        "B": ["AAA"],
        "C": ["QQQ", "IWM"],
        "D": ["NEW"],
    }
    shards = prepared.params["equity_shard_symbols"]
    assert {symbol for members in shards.values() for symbol in members} == set(plan["member_symbols"])
    assert all(symbol in plan["member_symbols"] for members in shards.values() for symbol in members)


def test_task_preparation_also_updates_preopen_maintenance(tmp_path: Path) -> None:
    from urus_moomoo.intraday_pool import IntradayPoolPlanLoader

    plan = _plan("2026-10-19")
    store = CollectorStore(tmp_path / "collector.sqlite")
    loader = IntradayPoolPlanLoader(
        api_url="http://urus:8000",
        timeout_seconds=5,
        store=store,
        get=lambda _url, _timeout: (200, plan),
    )
    maintenance = CollectionTask(
        "equity-maintenance", True, "pre-open", ("OLD",), "maintain_intraday_equities"
    )
    try:
        prepared = loader.prepare_task(maintenance, date(2026, 10, 19))
    finally:
        store.close()

    assert prepared.symbols == tuple(plan["member_symbols"])
    assert prepared.params["pool_plan_id"] == plan["plan_id"]
