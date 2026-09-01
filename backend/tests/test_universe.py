from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

from app.models.universe import InstrumentUniverseItemModel, InstrumentUniverseVersionModel
from app.repositories.universe import InstrumentUniverseRepository


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
    # A stale primary label must not be silently re-added to an
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


def test_universe_explicit_themes_override_stale_primary_theme(client) -> None:
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


def test_legacy_universe_response_hash_matches_normalized_items() -> None:
    version = InstrumentUniverseVersionModel(
        id="legacy-version",
        revision=1,
        content_sha256="0" * 64,
        source="runtime",
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    version.items = [
        InstrumentUniverseItemModel(
            id="legacy-item",
            version_id=version.id,
            position=0,
            symbol="INTC",
            display_name="Intel",
            asset_type="equity",
            theme="半导体",
            themes=["半导体"],
            enabled=True,
            roles={
                "market_benchmark": False,
                "equity_watchlist": True,
                "options_collection": False,
                "ai_candidate": True,
                "cta_proxy": True,
                "event_tracking": True,
            },
            benchmarks={"relative_strength": "QQQ", "cta_proxy_for": "NQ"},
            collection={"quote": True, "daily_history": True, "options": False},
            notes="legacy",
        )
    ]

    response = InstrumentUniverseRepository.response(version)

    assert response.content_sha256 == InstrumentUniverseRepository.content_digest(response.items)
    assert response.content_sha256 != version.content_sha256
