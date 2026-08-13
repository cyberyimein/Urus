from __future__ import annotations

from copy import deepcopy


def test_universe_bootstraps_from_environment_and_is_versioned(client) -> None:
    first = client.get("/api/settings/universe")
    assert first.status_code == 200
    payload = first.json()
    assert payload["revision"] == 1
    assert payload["source"] == "environment"
    assert "INTC" in payload["derived"]["instrument_symbols"]
    assert "QQQ" in payload["derived"]["market_symbols"]

    items = payload["items"]
    intc = next(item for item in items if item["symbol"] == "INTC")
    intc["theme"] = "处理器与代工"
    intc["themes"] = ["处理器与代工"]
    saved = client.put(
        "/api/settings/universe",
        json={"base_version_id": payload["version_id"], "items": items},
    )
    assert saved.status_code == 200
    updated = saved.json()
    assert updated["revision"] == 2
    assert updated["version_id"] != payload["version_id"]
    assert next(item for item in updated["items"] if item["symbol"] == "INTC")["theme"] == "处理器与代工"
    assert len(client.get("/api/settings/universe/versions").json()) == 2


def test_universe_persists_cross_cutting_themes_and_keeps_primary_label(client) -> None:
    payload = client.get("/api/settings/universe").json()
    intc = next(item for item in payload["items"] if item["symbol"] == "INTC")
    intc["themes"] = ["半导体", "AI 基础设施", "代工"]
    # A stale legacy primary label must not be silently re-added to an
    # explicit list after the user removes it in the editor.
    intc["theme"] = "旧主题"

    saved = client.put(
        "/api/settings/universe",
        json={"base_version_id": payload["version_id"], "items": payload["items"]},
    )
    assert saved.status_code == 200
    updated = next(item for item in saved.json()["items"] if item["symbol"] == "INTC")
    assert updated["themes"] == ["半导体", "AI 基础设施", "代工"]
    assert updated["theme"] == "半导体"

    with client.app.state.session_factory() as session:
        from app.repositories.universe import InstrumentUniverseRepository

        active = InstrumentUniverseRepository(session).active()
        assert active is not None
        row = next(item for item in active.items if item.symbol == "INTC")
        assert row.themes == ["半导体", "AI 基础设施", "代工"]


def test_universe_explicit_themes_override_stale_legacy_theme(client) -> None:
    payload = client.get("/api/settings/universe").json()
    intc = next(item for item in payload["items"] if item["symbol"] == "INTC")
    intc["themes"] = ["云计算"]
    intc["theme"] = "旧主题"

    saved = client.put(
        "/api/settings/universe",
        json={"base_version_id": payload["version_id"], "items": payload["items"]},
    )
    assert saved.status_code == 200
    updated = next(item for item in saved.json()["items"] if item["symbol"] == "INTC")
    assert updated["themes"] == ["云计算"]
    assert updated["theme"] == "云计算"


def test_universe_rejects_invalid_benchmark_and_stale_save(client) -> None:
    payload = client.get("/api/settings/universe").json()
    invalid_items = payload["items"]
    next(item for item in invalid_items if item["symbol"] == "INTC")["benchmarks"]["relative_strength"] = "MISSING"
    invalid = client.post(
        "/api/settings/universe/validate",
        json={"base_version_id": payload["version_id"], "items": invalid_items},
    )
    assert invalid.status_code == 422

    clean = client.get("/api/settings/universe").json()
    clean["items"][0]["notes"] = "new version"
    assert client.put(
        "/api/settings/universe",
        json={"base_version_id": clean["version_id"], "items": clean["items"]},
    ).status_code == 200
    stale = client.put(
        "/api/settings/universe",
        json={"base_version_id": clean["version_id"], "items": clean["items"]},
    )
    assert stale.status_code == 409


def test_run_freezes_active_universe_version(client) -> None:
    universe = client.get("/api/settings/universe").json()
    created = client.post("/api/runs", json={"run_type": "pre_market"})
    assert created.status_code == 201
    run = client.get(f"/api/runs/{created.json()['run_id']}").json()
    assert run["universe_version_id"] == universe["version_id"]
    assert run["universe_content_sha256"] == universe["content_sha256"]
    snapshot = client.get(f"/api/snapshots/{run['snapshot_id']}/frontend").json()
    assert snapshot["universe"]["version_id"] == universe["version_id"]


def test_deleting_symbol_only_changes_new_universe_version(client) -> None:
    original = client.get("/api/settings/universe").json()
    assert any(item["symbol"] == "INTC" for item in original["items"])
    next_items = [item for item in original["items"] if item["symbol"] != "INTC"]

    saved = client.put(
        "/api/settings/universe",
        json={"base_version_id": original["version_id"], "items": next_items},
    )
    assert saved.status_code == 200
    assert all(item["symbol"] != "INTC" for item in saved.json()["items"])

    versions = client.get("/api/settings/universe/versions").json()
    old = next(version for version in versions if version["version_id"] == original["version_id"])
    assert any(item["symbol"] == "INTC" for item in old["items"])


def test_identical_content_can_be_restored_as_later_revision(client) -> None:
    original = client.get("/api/settings/universe").json()
    changed_items = deepcopy(original["items"])
    changed_items[0]["notes"] = "temporary change"
    changed = client.put(
        "/api/settings/universe",
        json={"base_version_id": original["version_id"], "items": changed_items},
    ).json()

    restored = client.put(
        "/api/settings/universe",
        json={"base_version_id": changed["version_id"], "items": original["items"]},
    )
    assert restored.status_code == 200
    assert restored.json()["revision"] == changed["revision"] + 1
    assert restored.json()["content_sha256"] == original["content_sha256"]
