from __future__ import annotations

from app.api.settings import _response
from app.core.config import Settings
from app.core.time import utc_now
from app.models.runtime_settings import RuntimeSettingsModel
from app.repositories.runtime_settings import apply_payload, environment_payload


def _update_payload(client):
    response = client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    return {
        "revision": payload["revision"],
        "schedule": payload["schedule"],
        "models": payload["models"],
    }


def test_legacy_runtime_settings_preserve_environment_prices() -> None:
    settings = Settings(
        urus_agent_input_cost_per_million=2.5,
        urus_agent_cached_input_cost_per_million=0.25,
        urus_agent_cache_write_cost_per_million=3.0,
        urus_agent_output_cost_per_million=10.0,
    )
    legacy_payload = environment_payload(settings)
    for key in (
        "input_cost_per_million",
        "cached_input_cost_per_million",
        "cache_write_cost_per_million",
        "output_cost_per_million",
    ):
        legacy_payload["models"].pop(key)

    apply_payload(settings, legacy_payload)
    response = _response(
        settings,
        RuntimeSettingsModel(
            id=1,
            payload=legacy_payload,
            revision=1,
            updated_at=utc_now(),
        ),
    )

    assert settings.urus_agent_input_cost_per_million == 2.5
    assert response.models.input_cost_per_million == 2.5
    assert response.models.cached_input_cost_per_million == 0.25
    assert response.models.cache_write_cost_per_million == 3.0
    assert response.models.output_cost_per_million == 10.0


def test_settings_expose_environment_defaults(client) -> None:
    response = client.get("/api/settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "environment"
    assert payload["revision"] == 0
    assert payload["schedule"]["pre_market"] == {
        "enabled": True,
        "skip_ai_decision": True,
    }
    assert payload["schedule"]["pre_close"]["skip_ai_decision"] is True
    assert payload["schedule"]["post_close_observation"]["skip_ai_decision"] is True
    assert payload["models"]["ai_decision_model"]
    assert payload["models"]["anomalo_retrieval_agent"] == "scheduled-event-investigator"
    assert payload["models"]["input_cost_per_million"] == 0
    assert payload["models"]["cached_input_cost_per_million"] == 0
    assert payload["models"]["cache_write_cost_per_million"] == 0
    assert payload["models"]["output_cost_per_million"] == 0


def test_settings_update_persists_and_updates_running_config(client, app) -> None:
    payload = _update_payload(client)
    payload["schedule"]["pre_market"]["enabled"] = False
    payload["schedule"]["post_close_observation"]["skip_ai_decision"] = True
    payload["models"]["ai_decision_model"] = "openai/gpt-oss-120b"
    payload["models"]["anomalo_retrieval_agent"] = "research-agent-v2"
    payload["models"]["input_cost_per_million"] = 2.5
    payload["models"]["cached_input_cost_per_million"] = 0.25
    payload["models"]["cache_write_cost_per_million"] = 3.0
    payload["models"]["output_cost_per_million"] = 10.0

    response = client.put("/api/settings", json=payload)

    assert response.status_code == 200
    updated = response.json()
    assert updated["source"] == "runtime"
    assert updated["revision"] == 1
    assert updated["schedule"]["pre_market"]["enabled"] is False
    assert updated["schedule"]["post_close_observation"]["skip_ai_decision"] is True
    assert updated["models"]["ai_decision_model"] == "openai/gpt-oss-120b"
    assert app.state.settings.urus_agent_model == "openai/gpt-oss-120b"
    assert app.state.settings.anomalo_scheduled_agent == "research-agent-v2"
    assert app.state.settings.urus_agent_input_cost_per_million == 2.5
    assert app.state.settings.urus_agent_cached_input_cost_per_million == 0.25
    assert app.state.settings.urus_agent_cache_write_cost_per_million == 3.0
    assert app.state.settings.urus_agent_output_cost_per_million == 10.0

    reread = client.get("/api/settings").json()
    assert reread["revision"] == 1
    assert reread["schedule"]["pre_market"]["enabled"] is False


def test_settings_reject_tail_ai_and_stale_revision(client) -> None:
    payload = _update_payload(client)
    payload["schedule"]["pre_close"]["skip_ai_decision"] = False
    invalid = client.put("/api/settings", json=payload)
    assert invalid.status_code == 422

    valid = _update_payload(client)
    assert client.put("/api/settings", json=valid).status_code == 200
    stale = dict(valid)
    stale["revision"] = 0
    conflict = client.put("/api/settings", json=stale)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "settings_revision_conflict"


def test_legacy_post_close_review_setting_is_migrated_to_observation() -> None:
    settings = Settings()
    payload = environment_payload(settings)
    observation = payload["schedule"].pop("post_close_observation")
    payload["schedule"]["post_close_review"] = {
        **observation,
        "skip_ai_decision": False,
    }

    apply_payload(settings, payload)

    assert settings.scheduled_post_close_enabled is True
    assert settings.scheduled_post_close_skip_ai_decision is True
