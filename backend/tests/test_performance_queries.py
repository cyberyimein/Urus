from datetime import UTC, date, datetime, timedelta

from sqlalchemy import event

from app.decision_harness.contracts import FEATURE_VERSION
from app.decision_harness.cross_section import CrossSectionService
from app.decision_harness.discovery import DiscoveryProjectionService
from app.models.observation import GroupDailySnapshotModel, ObservationRunModel
from app.repositories.observation import ObservationRepository


def _snapshot(index, *, group="theme", version="version", trading_date=None):
    return GroupDailySnapshotModel(
        id=f"snapshot-{group}-{index}", group_id=group, group_version_id=version,
        group_version=1, dataset_id=f"dataset-{index}",
        trading_date=trading_date or date(2026, 1, 1) + timedelta(days=index),
        snapshot_schema_version="v4", content_sha256=f"hash-{index}",
        created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=index),
        payload_json={
            "feature_version": FEATURE_VERSION,
            "group": {"group_id": group, "version_id": version, "benchmark_symbols": ["QQQ"]},
            "symbols": [{"symbol": "AAA", "valid": True, "rsi14": 20 if index == 0 else 50}],
        },
    )


def _run(*, payload, groups=None, id="run"):
    return ObservationRunModel(
        id=id, idempotency_key=id, status="succeeded", trigger_mode="scheduled",
        trading_date=date(2026, 8, 24), cutoff_time=datetime(2026, 8, 24, 23, tzinfo=UTC),
        group_ids=groups or ["theme"], group_version_ids=["version"],
        content_sha256="run-hash", payload_json=payload,
        created_at=datetime(2026, 8, 24, 23, tzinfo=UTC),
    )


def test_latest_and_previous_snapshot_load_only_selected_record(client, app):
    factory = app.state.session_factory
    with factory() as session:
        session.add_all([_snapshot(i) for i in range(100)])
        session.add(_snapshot(101, version="other"))
        session.commit()
    loaded = []

    def record(model, context):
        loaded.append(model.id)

    event.listen(GroupDailySnapshotModel, "load", record)
    try:
        with factory() as session:
            repo = ObservationRepository(session)
            assert repo.previous_snapshot(
                group_id="theme", group_version_id="version", trading_date=date(2026, 3, 1)
            ).id == "snapshot-theme-58"
            assert loaded == ["snapshot-theme-58"]
        loaded.clear()
        with factory() as session:
            repo = ObservationRepository(session)
            assert repo.latest_snapshot("theme", group_version_id="version").id == "snapshot-theme-99"
            assert loaded == ["snapshot-theme-99"]
            assert repo.latest_snapshot("missing") is None
    finally:
        event.remove(GroupDailySnapshotModel, "load", record)


def test_discovery_reuses_context_and_preserves_each_lens_projection(client, app):
    factory = app.state.session_factory
    manifest = []
    with factory() as session:
        for group in ("one", "two", "three"):
            session.add_all([
                _snapshot(0, group=group, version=f"version-{group}"),
                _snapshot(1, group=group, version=f"version-{group}"),
            ])
            manifest.append({"status": "succeeded", "snapshot_id": f"snapshot-{group}-1"})
        session.add(_run(payload={"group_snapshots": manifest}))
        session.commit()
    with factory() as session:
        service = CrossSectionService(session)
        expected = {
            lens["id"]: service.indicator_projection("run", lens["id"])
            for lens in service.indicator_catalog()
        }
    with factory() as session:
        service = CrossSectionService(session)
        context = service.load_context("run")
        for lens_id, projection in expected.items():
            assert service.indicator_projection_from_context(context, lens_id) == projection

    queries = []

    def record(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    event.listen(app.state.engine, "before_cursor_execute", record)
    try:
        with factory() as session:
            result = DiscoveryProjectionService(session).build("run")
        assert len(queries) <= 10  # One run + one load per current/previous/group version.
        assert result["comparison"]["status"] == "ok"
        assert any(item["lens"]["id"] == "rsi14" for item in result["events"])
    finally:
        event.remove(app.state.engine, "before_cursor_execute", record)


def test_run_summaries_do_not_load_run_payload_and_keep_detail_api(client, app):
    with app.state.session_factory() as session:
        session.add(_run(payload={
            "group_snapshots": [{"status": "succeeded"}],
            "options": {"large": "x" * 200_000},
            "report": {"summary": {"quality_issue_count": 3}},
            "failed_group_count": 0,
        }))
        session.commit()
    loaded = []

    def record(model, context):
        loaded.append(model.id)

    event.listen(ObservationRunModel, "load", record)
    try:
        response = client.get("/api/observation/run-summaries?limit=1")
        assert response.status_code == 200
        summary = response.json()[0]
        assert loaded == []
        assert summary["run_id"] == "run"
        assert summary["group_count"] == summary["successful_group_count"] == 1
        assert summary["failed_group_count"] == 0
        assert summary["quality_issue_count"] == 3
        assert not {"options", "report", "group_snapshots", "payload_json"} & summary.keys()
        assert len(response.content) < 1000
    finally:
        event.remove(ObservationRunModel, "load", record)
    detail = client.get("/api/observation/runs/run")
    assert detail.status_code == 200
    assert len(detail.json()["options"]["large"]) == 200_000
    assert client.get("/api/observation/run-summaries?limit=0").status_code == 422
