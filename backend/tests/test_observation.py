from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from app.decision_harness.group_observation import build_group_snapshot
from app.decision_harness.market_evidence import DailyMarketEvidenceService
from app.models.observation import GroupDailySnapshotModel
from app.repositories.daily_evidence import DailyEvidenceRepository
from app.repositories.observation import ObservationUniverseRevisionRepository
from app.repositories.universe import InstrumentUniverseRepository
from app.schemas.universe import InstrumentConfig
from app.services.capital_flow import completed_session_dates


def _bars(symbol: str, dates, start: float, slope: float = 0.25):
    return [
        {
            "symbol": symbol,
            "date": trading_date.isoformat() if hasattr(trading_date, "isoformat") else str(trading_date),
            "open": start + index * slope - 0.2,
            "high": start + index * slope + 0.6,
            "low": start + index * slope - 0.6,
            "close": start + index * slope,
            "volume": 2_000_000.0 + index * 1000,
        }
        for index, trading_date in enumerate(dates)
    ]


def test_group_snapshot_exposes_breadth_rotation_and_heatmap() -> None:
    dates = [f"2026-01-{index + 1:02d}" for index in range(80)]
    bars_a = _bars("INTC", dates, 20.0)
    bars_b = _bars("AMD", dates, 40.0, slope=0.05)
    benchmark = _bars("QQQ", dates, 300.0, slope=0.2)
    evidence = {
        "dataset": {
            "dataset_id": "dataset-group-1",
            "feature_version": "technical_v5",
            "trading_date": dates[-1],
            "indicator_snapshot_ids": ["indicator-intc", "indicator-amd", "indicator-qqq"],
        },
        "chart": {
            "instruments": {
                symbol: {
                    "price": {"bars": bars},
                    "quality": {"status": "ok", "warnings": []},
                }
                for symbol, bars in (("INTC", bars_a), ("AMD", bars_b), ("QQQ", benchmark))
            }
        },
        "strategy_decisions": [],
        "deterministic_synthesis": {},
    }
    snapshot = build_group_snapshot(
        {
            "group_id": "semiconductors",
            "version_id": "group-version-1",
            "version": 1,
            "display_name": "半导体",
            "symbols": ["INTC", "AMD"],
            "benchmark_symbols": ["QQQ"],
        },
        evidence,
    )

    assert snapshot["schema_version"] == "urus.group_daily_snapshot.v3"
    assert snapshot["feature_version"] == "technical_v5"
    assert snapshot["features"]["valid_symbol_count"] == 2
    assert snapshot["features"]["breadth"]["above_ma20"] == 1.0
    assert snapshot["group_strategy_decisions"]
    assert snapshot["changes"]["previous_trading_date"] is None
    assert snapshot["charts"]["relative_strength"]["series"]
    assert snapshot["charts"]["breadth"]["series"]["above_ma20"]
    assert len(snapshot["charts"]["rotation"]) == 2
    assert len(snapshot["charts"]["heatmap"]) == 2
    assert len(snapshot["charts"]["small_multiples"]) == 2
    assert snapshot["content_sha256"]


def test_group_snapshot_excludes_partial_and_stale_symbols_from_decision() -> None:
    dates = [f"2026-01-{index + 1:02d}" for index in range(80)]
    bars_a = _bars("INTC", dates, 20.0)
    bars_b = _bars("AMD", dates, 40.0, slope=0.05)
    evidence = {
        "dataset": {
            "dataset_id": "dataset-group-quality",
            "feature_version": "technical_v5",
            "trading_date": dates[-1],
            "indicator_snapshot_ids": [],
        },
        "chart": {
            "instruments": {
                "INTC": {
                    "price": {"bars": bars_a},
                    "quality": {"status": "ok", "warnings": []},
                },
                "AMD": {
                    "price": {"bars": bars_b},
                    "quality": {"status": "partial", "warnings": ["history incomplete"]},
                },
            }
        },
        "strategy_decisions": [],
        "deterministic_synthesis": {},
    }

    snapshot = build_group_snapshot(
        {
            "group_id": "semiconductors",
            "version_id": "group-version-quality",
            "version": 1,
            "display_name": "半导体",
            "symbols": ["INTC", "AMD"],
            "benchmark_symbols": [],
        },
        evidence,
    )

    assert snapshot["quality"]["valid_symbol_count"] == 1
    assert snapshot["quality"]["status"] == "partial"
    assert snapshot["group_decision"]["state"] == "insufficient_data"
    assert snapshot["features"]["valid_symbol_count"] == 1


def test_observation_group_version_and_run_are_idempotent(client) -> None:
    cutoff = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    session_dates = completed_session_dates(cutoff, "XNYS", count=260)
    with client.app.state.session_factory() as session:
        repository = DailyEvidenceRepository(session)
        for symbol, start in (("INTC", 20.0), ("AMD", 80.0), ("QQQ", 300.0)):
            repository.upsert_bars(
                _bars(symbol, session_dates, start),
                source="fixture",
                collected_at=cutoff,
            )
        session.commit()

    group = client.post(
        "/api/observation/groups",
        json={
            "group_id": "semiconductors",
            "display_name": "半导体",
            "description": "核心芯片观察组",
            "symbols": ["INTC", "AMD"],
            "benchmark_symbols": ["QQQ"],
            "tags": ["sector"],
        },
    )
    assert group.status_code == 201
    group_body = group.json()
    assert group_body["version"] == 1

    payload = {
        "group_ids": ["semiconductors"],
        "trading_date": session_dates[-1].isoformat(),
        "cutoff_time": cutoff.isoformat(),
        "request_intent_id": "user-intent-1",
    }
    first = client.post("/api/observation/runs", json=payload)
    assert first.status_code == 201, first.text
    first_body = first.json()
    assert first_body["status"] == "succeeded"
    assert first_body["group_count"] == 1
    assert first_body["group_snapshots"][0]["group_id"] == "semiconductors"
    report = first_body["report"]
    assert report["schema_version"] == "urus.observation_report.v1"
    assert report["mode"] == "deterministic-only"
    assert report["group_rankings"][0]["group_id"] == "semiconductors"
    assert {item["symbol"] for item in report["anomalies"]["leaders"]} <= {"INTC", "AMD"}
    assert {item["symbol"] for item in report["anomalies"]["laggards"]} <= {"INTC", "AMD"}
    assert set(report["opportunity_lanes"]) == {"confirmed", "near_confirmation", "forming"}
    assert set(report["risk_lanes"]) == {"invalidated", "bearish"}
    assert report["content_sha256"]

    indicator_catalog = client.get("/api/observation/indicator-catalog")
    assert indicator_catalog.status_code == 200
    assert {item["id"] for item in indicator_catalog.json()} >= {
        "rsi14",
        "macd_histogram",
        "above_ma20",
    }
    indicator_projection = client.get(
        f"/api/observation/runs/{first_body['run_id']}/indicators/rsi14"
    )
    assert indicator_projection.status_code == 200, indicator_projection.text
    indicator_body = indicator_projection.json()
    assert indicator_body["lens"]["id"] == "rsi14"
    assert {row["symbol"] for row in indicator_body["rows"]} == {"INTC", "AMD"}
    assert indicator_body["groups"][0]["group_name"] == "半导体"
    assert indicator_body["quality"]["snapshot_ids"]
    assert indicator_body["ai"]["status"] == "disabled"
    assert indicator_body["content_sha256"]

    strategy_catalog = client.get("/api/observation/strategy-catalog")
    assert strategy_catalog.status_code == 200
    assert any(item["id"] == "trend_momentum_v1" for item in strategy_catalog.json())
    strategy_projection = client.get(
        f"/api/observation/runs/{first_body['run_id']}/strategies/trend_momentum_v1"
    )
    assert strategy_projection.status_code == 200, strategy_projection.text
    strategy_body = strategy_projection.json()
    assert strategy_body["lens"]["id"] == "trend_momentum_v1"
    assert len(strategy_body["rows"]) == 2
    assert {row["symbol"] for row in strategy_body["rows"]} == {"INTC", "AMD"}
    assert strategy_body["groups"][0]["group_name"] == "半导体"
    assert strategy_body["ai"]["available"] is False

    second = client.post("/api/observation/runs", json=payload)
    assert second.status_code == 201
    assert second.json()["run_id"] == first_body["run_id"]

    replay = client.post(
        "/api/observation/runs",
        json={**payload, "request_intent_id": "user-intent-replay"},
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["run_id"] != first_body["run_id"]
    assert replay.json()["report"]["content_sha256"] == report["content_sha256"]

    scheduled_payload = {
        "group_ids": ["semiconductors"],
        "trading_date": session_dates[-1].isoformat(),
        "cutoff_time": cutoff.isoformat(),
        "trigger_mode": "scheduled",
    }
    scheduled_first = client.post("/api/observation/runs", json=scheduled_payload)
    assert scheduled_first.status_code == 201, scheduled_first.text
    scheduled_second = client.post("/api/observation/runs", json=scheduled_payload)
    assert scheduled_second.status_code == 201
    assert scheduled_second.json()["run_id"] == scheduled_first.json()["run_id"]


def test_observation_group_updates_require_the_current_version(client) -> None:
    created = client.post(
        "/api/observation/groups",
        json={
            "group_id": "case-sensitive-group",
            "display_name": "初始组",
            "symbols": ["INTC"],
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["group_id"] == "case-sensitive-group"

    updated = client.put(
        "/api/observation/groups/case-sensitive-group",
        json={
            "group_id": "case-sensitive-group",
            "display_name": "新版本",
            "symbols": ["INTC"],
            "base_version_id": body["version_id"],
        },
    )
    assert updated.status_code == 200
    stale = client.put(
        "/api/observation/groups/case-sensitive-group",
        json={
            "group_id": "case-sensitive-group",
            "display_name": "过期版本",
            "symbols": ["AMD"],
            "base_version_id": body["version_id"],
        },
    )
    assert stale.status_code == 409


def test_cross_section_exposes_previous_trading_session_comparison(client) -> None:
    cutoff = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    session_dates = completed_session_dates(cutoff, "XNYS", count=261)
    with client.app.state.session_factory() as session:
        repository = DailyEvidenceRepository(session)
        for symbol, start in (("INTC", 20.0), ("AMD", 80.0), ("QQQ", 300.0)):
            repository.upsert_bars(
                _bars(symbol, session_dates, start),
                source="fixture",
                collected_at=cutoff,
            )
        session.commit()

    created = client.post(
        "/api/observation/groups",
        json={
            "group_id": "comparison-group",
            "display_name": "对比测试组",
            "symbols": ["INTC", "AMD"],
            "benchmark_symbols": ["QQQ"],
        },
    )
    assert created.status_code == 201, created.text

    previous_run = client.post(
        "/api/observation/runs",
        json={
            "group_ids": ["comparison-group"],
            "trading_date": session_dates[-2].isoformat(),
            "cutoff_time": cutoff.isoformat(),
            "request_intent_id": "comparison-previous",
        },
    )
    assert previous_run.status_code == 201, previous_run.text

    current_run = client.post(
        "/api/observation/runs",
        json={
            "group_ids": ["comparison-group"],
            "trading_date": session_dates[-1].isoformat(),
            "cutoff_time": cutoff.isoformat(),
            "request_intent_id": "comparison-current",
        },
    )
    assert current_run.status_code == 201, current_run.text
    run_id = current_run.json()["run_id"]

    indicator = client.get(f"/api/observation/runs/{run_id}/indicators/rsi14")
    assert indicator.status_code == 200, indicator.text
    indicator_body = indicator.json()
    assert indicator_body["comparison"]["status"] == "ok"
    assert indicator_body["comparison"]["current_trading_date"] == session_dates[-1].isoformat()
    assert indicator_body["comparison"]["previous_trading_date"] == session_dates[-2].isoformat()
    indicator_row = next(row for row in indicator_body["rows"] if row["symbol"] == "INTC")
    assert indicator_row["previous_trading_date"] == session_dates[-2].isoformat()
    assert indicator_row["previous_snapshot_id"]
    assert indicator_row["previous_value"] is not None
    assert indicator_row["previous_state"] != "missing"
    assert indicator_body["groups"][0]["previous_distribution"]["count"] == 2

    strategy = client.get(f"/api/observation/runs/{run_id}/strategies/trend_momentum_v1")
    assert strategy.status_code == 200, strategy.text
    strategy_row = next(row for row in strategy.json()["rows"] if row["symbol"] == "INTC")
    assert strategy_row["previous_trading_date"] == session_dates[-2].isoformat()
    assert strategy_row["previous_value"] is not None
    assert "previous_state" in strategy_row
    assert "previous_action" in strategy_row


def test_cross_section_rejects_previous_strategy_implementation_mismatch(client) -> None:
    cutoff = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    session_dates = completed_session_dates(cutoff, "XNYS", count=261)
    with client.app.state.session_factory() as session:
        repository = DailyEvidenceRepository(session)
        for symbol, start in (("INTC", 20.0), ("AMD", 80.0), ("QQQ", 300.0)):
            repository.upsert_bars(
                _bars(symbol, session_dates, start),
                source="fixture",
                collected_at=cutoff,
            )
        session.commit()

    created = client.post(
        "/api/observation/groups",
        json={
            "group_id": "strategy-version-group",
            "display_name": "策略版本测试组",
            "symbols": ["INTC", "AMD"],
            "benchmark_symbols": ["QQQ"],
        },
    )
    assert created.status_code == 201, created.text

    previous_run = client.post(
        "/api/observation/runs",
        json={
            "group_ids": ["strategy-version-group"],
            "trading_date": session_dates[-2].isoformat(),
            "cutoff_time": cutoff.isoformat(),
            "request_intent_id": "strategy-version-previous",
        },
    )
    assert previous_run.status_code == 201, previous_run.text

    current_run = client.post(
        "/api/observation/runs",
        json={
            "group_ids": ["strategy-version-group"],
            "trading_date": session_dates[-1].isoformat(),
            "cutoff_time": cutoff.isoformat(),
            "request_intent_id": "strategy-version-current",
        },
    )
    assert current_run.status_code == 201, current_run.text

    previous_snapshot_id = previous_run.json()["group_snapshots"][0]["snapshot_id"]
    with client.app.state.session_factory() as session:
        snapshot = session.get(GroupDailySnapshotModel, previous_snapshot_id)
        assert snapshot is not None
        payload = dict(snapshot.payload_json)
        decisions = []
        for item in payload.get("strategy_decisions", []):
            updated = dict(item)
            strategy = dict(updated.get("strategy") or {})
            if strategy.get("name") == "trend_momentum_v1":
                strategy["version"] = "legacy"
                strategy["implementation_sha256"] = "legacy-hash"
                updated["strategy"] = strategy
            decisions.append(updated)
        assert decisions
        payload["strategy_decisions"] = decisions
        snapshot.payload_json = payload
        session.commit()

    projection = client.get(
        f"/api/observation/runs/{current_run.json()['run_id']}/strategies/trend_momentum_v1"
    )
    assert projection.status_code == 409, projection.text
    assert projection.json()["error"]["code"] == "cross_section_strategy_version_conflict"


def test_refresh_missing_bars_does_not_backdate_collection_time(client) -> None:
    cutoff = datetime.now(UTC) - timedelta(minutes=1)
    session_dates = completed_session_dates(cutoff, "XNYS", count=260)

    class HistoricalAdapter:
        def instrument_cards(self, symbols: list[str]) -> dict[str, object]:
            return {
                "_persistence": {
                    "symbols": [
                        {
                            "symbol": symbol,
                            "history": {"bars": _bars(symbol, session_dates, 20.0)},
                        }
                        for symbol in symbols
                    ]
                }
            }

    with client.app.state.session_factory() as session:
        service = DailyMarketEvidenceService(session, client.app.state.settings)
        result = service.refresh_missing_bars(
            ["INTC"],
            through_date=session_dates[-1],
            cutoff_time=cutoff,
            source_adapter=HistoricalAdapter(),
        )
        visible = DailyEvidenceRepository(session).bars(
            ["INTC"], through_date=session_dates[-1], cutoff_time=cutoff
        )

    assert result["status"] == "partial"
    assert result["fetched_symbols"] == []
    assert visible["INTC"] == []


def test_observation_run_rejects_groups_from_another_universe_revision(client) -> None:
    synced = client.post("/api/observation/groups/sync")
    assert synced.status_code == 200, synced.text

    with client.app.state.session_factory() as session:
        universe = InstrumentUniverseRepository(session).active()
        assert universe is not None
        revision = ObservationUniverseRevisionRepository(session).save(
            source_url="https://mismatch.example/api",
            upstream_version_id="mismatch-version",
            upstream_revision=99,
            local_universe_version_id=universe.id,
            content_sha256="f" * 64,
        )

    response = client.post(
        "/api/observation/runs",
        json={
            "trigger_mode": "scheduled",
            "universe_revision_id": revision.id,
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "observation_run_invalid"


def test_observation_groups_sync_from_active_universe(client) -> None:
    response = client.post("/api/observation/groups/sync")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "local"
    assert body["symbol_count"] == 2
    assert body["group_count"] >= 2

    groups = client.get("/api/observation/groups").json()
    core = next(item for item in groups if item["group_id"] == "core-watchlist")
    assert core["display_name"] == "指标推荐"
    assert "indicator-recommendation" in core["tags"]
    assert core["symbols"] == ["SMH", "INTC"]
    assert core["benchmark_symbols"] == ["QQQ"]
    assert any(item["display_name"] == "半导体" and item["symbols"] == ["SMH"] for item in groups)
    assert any(item["display_name"] == "个股观察" and item["symbols"] == ["INTC"] for item in groups)


def test_legacy_self_selected_group_is_hidden_from_active_catalog(client) -> None:
    created = client.post(
        "/api/observation/groups",
        json={
            "group_id": "legacy-self-selected-group",
            "display_name": "核心观察组",
            "description": "旧版手工自选组",
            "symbols": ["INTC"],
            "benchmark_symbols": ["QQQ"],
            "tags": ["watchlist", "user-qualified"],
        },
    )
    assert created.status_code == 201, created.text

    visible = client.get("/api/observation/groups")
    assert visible.status_code == 200, visible.text
    assert all(item["group_id"] != "legacy-self-selected-group" for item in visible.json())

    historical = client.get("/api/observation/groups/legacy-self-selected-group")
    assert historical.status_code == 200, historical.text
    assert historical.json()["group"]["display_name"] == "自选组"


def test_observation_groups_sync_from_deployed_universe(client, app, monkeypatch) -> None:
    deployed = client.get("/api/settings/universe").json()
    wolf = next(item.copy() for item in deployed["items"] if item["symbol"] == "INTC")
    wolf.update({"symbol": "WOLF", "display_name": "WOLF", "themes": ["半导体"], "theme": "半导体"})
    deployed["items"].append(wolf)
    spy = next(item for item in deployed["items"] if item["symbol"] == "SPY")
    spy["roles"]["equity_watchlist"] = True
    deployed["content_sha256"] = InstrumentUniverseRepository.content_digest(
        [InstrumentConfig.model_validate(item) for item in deployed["items"]]
    )
    deployed.update({"version_id": "deployed-v5", "revision": 5, "source": "runtime"})

    class DeployedResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return deployed

    app.state.settings.observation_universe_source_url = "https://deployed.example"
    monkeypatch.setattr(
        "app.services.observation.httpx.get",
        lambda url, timeout: DeployedResponse(),
    )

    response = client.post("/api/observation/groups/sync")

    assert response.status_code == 200, response.text
    assert response.json()["source"] == "upstream"
    assert response.json()["symbol_count"] == 4
    first_sync = response.json()
    assert first_sync["universe_freshness"] == "fresh"
    assert first_sync["source_url"] == "https://deployed.example/api"
    assert first_sync["universe_revision_id"]
    assert "WOLF" in client.get("/api/settings/universe").json()["derived"]["instrument_symbols"]
    core = next(item for item in response.json()["groups"] if item["group_id"] == "core-watchlist")
    assert core["display_name"] == "指标推荐"
    assert "SPY" in core["symbols"]
    semiconductor = next(
        item for item in client.get("/api/observation/groups").json()
        if item["display_name"] == "半导体"
    )
    assert semiconductor["symbols"] == ["SMH", "WOLF"]
    assert semiconductor["source"] == "universe"
    assert semiconductor["universe_revision_id"] == first_sync["universe_revision_id"]

    repeated = client.post("/api/observation/groups/sync")
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["universe_revision_id"] == first_sync["universe_revision_id"]
    repeated_semiconductor = next(
        item for item in repeated.json()["groups"] if item["display_name"] == "半导体"
    )
    assert repeated_semiconductor["version_id"] == semiconductor["version_id"]


def test_observation_universe_sync_fails_closed_and_supports_explicit_stale_mode(
    client, app, monkeypatch
) -> None:
    deployed = client.get("/api/settings/universe").json()

    class DeployedResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return deployed

    app.state.settings.observation_universe_source_url = "https://deployed.example?token=redacted"
    monkeypatch.setattr(
        "app.services.observation.httpx.get",
        lambda url, timeout: DeployedResponse(),
    )
    first = client.post("/api/observation/groups/sync")
    assert first.status_code == 200, first.text
    revision_id = first.json()["universe_revision_id"]

    def unavailable(url, timeout):
        raise httpx.ConnectError("secret-token-must-not-leak", request=httpx.Request("GET", url))

    monkeypatch.setattr("app.services.observation.httpx.get", unavailable)
    failed = client.post("/api/observation/groups/sync")
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "observation_universe_sync_failed"
    assert "secret-token" not in failed.text
    assert "token=redacted" not in failed.text

    app.state.settings.observation_allow_stale_universe = True
    stale = client.post("/api/observation/groups/sync")
    assert stale.status_code == 200, stale.text
    assert stale.json()["universe_freshness"] == "stale"
    assert stale.json()["universe_revision_id"] == revision_id
    assert stale.json()["warnings"]


def test_observation_run_reuses_one_frozen_decision_for_overlapping_groups(client) -> None:
    cutoff = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    session_dates = completed_session_dates(cutoff, "XNYS", count=260)
    with client.app.state.session_factory() as session:
        repository = DailyEvidenceRepository(session)
        for symbol, start in (("INTC", 20.0), ("AMD", 80.0), ("QQQ", 300.0)):
            repository.upsert_bars(_bars(symbol, session_dates, start), source="fixture", collected_at=cutoff)
        session.commit()

    for group_id, symbols in (("chips", ["INTC", "AMD"]), ("turnarounds", ["INTC"])):
        response = client.post(
            "/api/observation/groups",
            json={
                "group_id": group_id,
                "display_name": group_id,
                "symbols": symbols,
                "benchmark_symbols": ["QQQ"],
            },
        )
        assert response.status_code == 201, response.text

    run = client.post(
        "/api/observation/runs",
        json={
            "group_ids": ["chips", "turnarounds"],
            "trading_date": session_dates[-1].isoformat(),
            "cutoff_time": cutoff.isoformat(),
            "request_intent_id": "overlap-1",
        },
    )
    assert run.status_code == 201, run.text
    body = run.json()
    assert body["status"] == "succeeded"
    assert len({item["dataset_id"] for item in body["group_snapshots"]}) == 1

    projection = client.get(
        f"/api/observation/runs/{body['run_id']}/strategies/trend_momentum_v1"
    ).json()
    intc_rows = [item for item in projection["rows"] if item["symbol"] == "INTC"]
    assert len(intc_rows) == 2
    assert len({item["decision_id"] for item in intc_rows}) == 1


def test_relative_strength_projection_rejects_mixed_benchmarks(client) -> None:
    cutoff = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    session_dates = completed_session_dates(cutoff, "XNYS", count=260)
    with client.app.state.session_factory() as session:
        repository = DailyEvidenceRepository(session)
        for symbol, start in (("INTC", 20.0), ("AMD", 80.0), ("QQQ", 300.0), ("SPY", 500.0)):
            repository.upsert_bars(_bars(symbol, session_dates, start), source="fixture", collected_at=cutoff)
        session.commit()

    for group_id, symbol, benchmark in (
        ("qqq-relative", "INTC", "QQQ"),
        ("spy-relative", "AMD", "SPY"),
    ):
        created = client.post(
            "/api/observation/groups",
            json={
                "group_id": group_id,
                "display_name": group_id,
                "symbols": [symbol],
                "benchmark_symbols": [benchmark],
            },
        )
        assert created.status_code == 201, created.text

    run = client.post(
        "/api/observation/runs",
        json={
            "group_ids": ["qqq-relative", "spy-relative"],
            "trading_date": session_dates[-1].isoformat(),
            "cutoff_time": cutoff.isoformat(),
            "request_intent_id": "mixed-benchmark",
        },
    ).json()

    projection = client.get(
        f"/api/observation/runs/{run['run_id']}/indicators/relative_strength_20d"
    )
    assert projection.status_code == 409
    assert projection.json()["error"]["code"] == "cross_section_benchmark_conflict"
