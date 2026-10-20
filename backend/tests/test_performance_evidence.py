from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import event, func, select

from app.decision_harness.contracts import content_sha256
from app.decision_harness.market_evidence import DailyMarketEvidenceService
from app.decision_harness.strategies import deterministic_synthesis
from app.models.daily_evidence import DailyBarModel, DecisionChartProjectionModel
from app.repositories.daily_evidence import DailyEvidenceRepository
from app.repositories.strategy_decisions import StrategyDecisionRepository


def _bars(count=520):
    return [dict(
        symbol="AAA", date=(date(2025, 1, 1) + timedelta(days=i)).isoformat(),
        open=100, high=102, low=99, close=101, volume=1_000_000,
    ) for i in range(count)]


def test_batch_import_deduplicates_preserves_order_time_and_transaction(client, app):
    queries = []

    def record(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement.split()[0])

    event.listen(app.state.engine, "before_cursor_execute", record)
    try:
        cutoff = datetime(2026, 1, 1, tzinfo=UTC)
        bars = _bars()
        with app.state.session_factory() as session:
            repo = DailyEvidenceRepository(session)
            imported = repo.upsert_bars([*bars, bars[0]], source="fixture", collected_at=cutoff)
            assert len(imported) == 521
            assert imported[0] is imported[-1]
            ids = [row.id for row in imported]
            assert queries.count("SELECT") <= 12
            assert queries.count("INSERT") <= 6
            session.commit()
        queries.clear()
        with app.state.session_factory() as session:
            repo = DailyEvidenceRepository(session)
            repeated = repo.upsert_bars([*bars, bars[0]], source="fixture", collected_at=cutoff + timedelta(days=1))
            assert [row.id for row in repeated] == ids
            assert all(row.collected_at.replace(tzinfo=UTC) == cutoff for row in repeated)
            assert queries.count("SELECT") <= 6
            assert queries.count("INSERT") == 0
            revision = {**bars[0], "close": 100.5}
            revised = repo.upsert_bars([revision], source="fixture", collected_at=cutoff + timedelta(hours=1))
            assert revised[0].id != ids[0]
            assert repo.bars(["AAA"], cutoff_time=cutoff)["AAA"][0].close == 101
            session.rollback()
        with app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(DailyBarModel)) == 520
            with pytest.raises(ValueError):
                DailyEvidenceRepository(session).upsert_bars(
                    [*_bars(1), {**bars[0], "high": 0}], source="invalid"
                )
            assert session.scalar(select(func.count()).select_from(DailyBarModel)) == 520
    finally:
        event.remove(app.state.engine, "before_cursor_execute", record)


def test_selected_chart_and_replay_paths_preserve_frozen_evidence_without_loading_full_chart(client, app):
    cutoff = datetime(2026, 8, 24, 23, tzinfo=UTC)
    with app.state.session_factory() as session:
        repo = DailyEvidenceRepository(session)
        repo.upsert_bars([{**row, "date": "2026-08-24"} for row in _bars(1)], source="fixture", collected_at=cutoff)
        evidence = DailyMarketEvidenceService(session, app.state.settings).freeze(
            scope_type="instrument", scope_id="AAA", symbols=["AAA"], cutoff_time=cutoff,
        )
        dataset_id = evidence["dataset"]["dataset_id"]
        # Historical charts may contain unrelated, very large instruments.
        chart = repo.chart(dataset_id)
        chart.payload_json = {**chart.payload_json, "instruments": {
            **chart.payload_json["instruments"], "OTHER": {"unrelated": "x" * 200_000},
        }}
        session.commit()
    loaded = []

    def record(model, context):
        loaded.append(model.id)

    event.listen(DecisionChartProjectionModel, "load", record)
    try:
        response = client.get(f"/api/daily-evidence/datasets/{dataset_id}/instruments/aaa/evidence")
        assert response.status_code == 200
        selected = response.json()
        chart = selected["chart"]
        assert list(chart["instruments"]) == ["AAA"]
        assert chart["schema_version"] == "urus.instrument_chart_projection.v1"
        assert chart["symbol"] == "AAA"
        assert chart["content_sha256"] == content_sha256({k: v for k, v in chart.items() if k != "content_sha256"})
        assert chart["instruments"]["AAA"] == evidence["chart"]["instruments"]["AAA"]
        assert chart["overlays"] == evidence["chart"]["overlays"]
        assert selected["strategy_decisions"] == evidence["strategy_decisions"]
        assert selected["deterministic_synthesis"] == evidence["deterministic_synthesis"]
        assert loaded == []
        with app.state.session_factory() as session:
            daily = DailyMarketEvidenceService(session, app.state.settings)
            minimal = daily.get_replay_chart(dataset_id, "AAA")
            assert minimal["instruments"]["AAA"]["price"]["bars"] == chart["instruments"]["AAA"]["price"]["bars"]
            assert "series" not in minimal["instruments"]["AAA"]
            assert daily.get_replay_chart(dataset_id, "MISSING")["instruments"] == {}
        assert loaded == []
        assert client.get(f"/api/daily-evidence/datasets/{dataset_id}/instruments/OTHER/evidence").status_code == 422
        assert client.get("/api/daily-evidence/datasets/missing/instruments/AAA/evidence").status_code == 404
    finally:
        event.remove(DecisionChartProjectionModel, "load", record)


@pytest.mark.parametrize("bundle_mode", ["partial", "empty", "absent"])
def test_selected_chart_clears_old_strategy_graphics_only_when_a_bundle_exists(client, app, bundle_mode):
    cutoff = datetime(2026, 8, 24, 23, tzinfo=UTC)
    shared_overlay = {"overlay_id": "shared", "kind": "marker", "price": 100}
    shared_segment = {"segment_id": "shared", "state": "technical"}
    with app.state.session_factory() as session:
        daily = DailyMarketEvidenceService(session, app.state.settings)
        frozen = daily.freeze(
            scope_type="group", scope_id="review", symbols=["AAA", "BBB"], cutoff_time=cutoff,
        )
        dataset = frozen["dataset"]
        stored = daily.repository.chart(dataset["dataset_id"])
        old_anchor = {
            "overlay_id": "old-anchor", "symbol": "AAA", "kind": "marker", "price": 100,
            "strategy_decision_id": frozen["strategy_decisions"][0]["decision_id"],
        }
        chart = {
            **stored.payload_json,
            "overlays": [*stored.payload_json["overlays"], old_anchor, shared_overlay],
            "state_segments": [*stored.payload_json["state_segments"], shared_segment],
        }
        chart["content_sha256"] = content_sha256({k: v for k, v in chart.items() if k != "content_sha256"})
        stored.payload_json = chart
        stored.content_sha256 = chart["content_sha256"]
        if bundle_mode == "absent":
            # A legacy dataset with a stored chart but no strategy bundle.
            dataset = {**dataset, "dataset_id": "legacy-no-bundle", "content_sha256": "legacy-dataset"}
            daily.repository.save_dataset(dataset, digest="legacy-dataset", created_at=cutoff)
            legacy_chart = {**chart, "dataset_id": dataset["dataset_id"]}
            daily.repository.save_chart(
                dataset_id=dataset["dataset_id"], scope_type="group", scope_id="review",
                payload=legacy_chart, digest=chart["content_sha256"], created_at=cutoff,
            )
        else:
            decisions = [
                {**item, "decision_id": f"new-{item['decision_id']}", "strategy_set_sha256": "new-set"}
                for item in frozen["strategy_decisions"]
                if bundle_mode == "partial" and item["scope"]["symbol"] == "BBB"
            ]
            StrategyDecisionRepository(session).save_bundle(
                dataset_id=dataset["dataset_id"], scope=dataset["scope"],
                strategy_set_sha256="new-set", decisions=decisions,
                synthesis=deterministic_synthesis(dataset, decisions, "new-set"),
                created_at=datetime.now(UTC) + timedelta(seconds=1),
            )
        session.commit()
        dataset_id = dataset["dataset_id"]
    response = client.get(f"/api/daily-evidence/datasets/{dataset_id}/instruments/AAA/evidence")
    assert response.status_code == 200
    selected = response.json()
    assert selected["strategy_decisions"] == []
    chart = selected["chart"]
    assert shared_overlay in chart["overlays"]
    assert shared_segment in chart["state_segments"]
    if bundle_mode == "absent":
        assert chart["strategy_set_sha256"] is None
        assert selected["deterministic_synthesis"] == {}
        assert old_anchor in chart["overlays"]
        assert any(item.get("strategy_decision_id") for item in chart["state_segments"])
    else:
        assert chart["strategy_set_sha256"] == selected["deterministic_synthesis"]["strategy_set_sha256"] == "new-set"
        assert chart["overlays"] == [shared_overlay]
        assert chart["state_segments"] == [shared_segment]
    assert chart["content_sha256"] == content_sha256({k: v for k, v in chart.items() if k != "content_sha256"})
