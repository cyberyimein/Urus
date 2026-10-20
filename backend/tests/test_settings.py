from __future__ import annotations

from app.api.settings import _response
from app.core.config import Settings
from app.core.time import utc_now
from app.models.runtime_settings import RuntimeSettingsModel
from app.repositories.runtime_settings import apply_payload, environment_payload


def test_environment_payload_contains_only_the_current_schedule() -> None:
    settings = Settings(scheduled_post_close_enabled=False)

    assert environment_payload(settings) == {
        "schedule": {"post_close_observation": {"enabled": False}}
    }


def test_settings_expose_environment_defaults(client) -> None:
    response = client.get("/api/settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "environment"
    assert payload["revision"] == 0
    assert payload["schedule"] == {"post_close_observation": {"enabled": True}}


def test_settings_update_persists_and_updates_running_config(client, app) -> None:
    payload = client.get("/api/settings").json()
    payload["schedule"]["post_close_observation"]["enabled"] = False
    payload = {"revision": payload["revision"], "schedule": payload["schedule"]}

    response = client.put("/api/settings", json=payload)

    assert response.status_code == 200
    updated = response.json()
    assert updated["source"] == "runtime"
    assert updated["revision"] == 1
    assert updated["schedule"] == {"post_close_observation": {"enabled": False}}
    assert app.state.settings.scheduled_post_close_enabled is False

    reread = client.get("/api/settings").json()
    assert reread["revision"] == 1
    assert reread["schedule"]["post_close_observation"]["enabled"] is False


def test_settings_reject_stale_revision(client) -> None:
    payload = client.get("/api/settings").json()
    update = {"revision": payload["revision"], "schedule": payload["schedule"]}
    assert client.put("/api/settings", json=update).status_code == 200

    stale = dict(update)
    stale["revision"] = 0
    conflict = client.put("/api/settings", json=stale)

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "settings_revision_conflict"


def test_schedule_update_preserves_intraday_pool_payload(client, app) -> None:
    intraday_pool = {
        "schema_version": "urus.intraday.pool_settings.v1",
        "revision": 5,
        "active": {"source": "runtime", "member_symbols": ["SPY", "QQQ", "IWM"]},
        "pending": None,
    }
    with app.state.session_factory() as session:
        session.add(
            RuntimeSettingsModel(
                id=1,
                payload={
                    "schedule": {"post_close_observation": {"enabled": True}},
                    "intraday_pool": intraday_pool,
                },
                revision=3,
                updated_at=utc_now(),
            )
        )
        session.commit()

    current = client.get("/api/settings").json()
    update = {
        "revision": current["revision"],
        "schedule": {"post_close_observation": {"enabled": False}},
    }
    response = client.put("/api/settings", json=update)

    assert response.status_code == 200
    with app.state.session_factory() as session:
        saved = session.get(RuntimeSettingsModel, 1)
        assert saved is not None
        assert saved.payload["intraday_pool"] == intraday_pool
        assert saved.payload["schedule"]["post_close_observation"]["enabled"] is False


def test_removed_persisted_schedule_fields_are_ignored() -> None:
    settings = Settings(scheduled_post_close_enabled=True)
    apply_payload(
        settings,
        {
            "schedule": {
                "post_close_observation": {"enabled": False},
                "removed_slot": {"enabled": False},
            }
        },
    )

    response = _response(
        settings,
        RuntimeSettingsModel(
            id=1,
            payload={
                "schedule": {
                    "post_close_observation": {"enabled": False},
                    "removed_slot": {"enabled": False},
                }
            },
            revision=1,
            updated_at=utc_now(),
        ),
    )

    assert settings.scheduled_post_close_enabled is False
    assert response.schedule.post_close_observation.enabled is False


def test_legacy_post_close_review_preserves_enabled_state() -> None:
    settings = Settings(scheduled_post_close_enabled=True)
    legacy = RuntimeSettingsModel(
        id=1,
        payload={"schedule": {"post_close_review": {"enabled": False}}},
        revision=1,
        updated_at=utc_now(),
    )

    apply_payload(settings, legacy.payload)
    response = _response(settings, legacy)

    assert settings.scheduled_post_close_enabled is False
    assert response.schedule.post_close_observation.enabled is False
