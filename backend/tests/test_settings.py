from __future__ import annotations


def _update_payload(client):
    response = client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    return {
        "revision": payload["revision"],
        "schedule": payload["schedule"],
        "models": payload["models"],
    }


def test_settings_expose_environment_defaults(client) -> None:
    response = client.get("/api/settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "environment"
    assert payload["revision"] == 0
    assert payload["schedule"]["pre_market"] == {
        "enabled": True,
        "skip_ai_decision": False,
    }
    assert payload["schedule"]["pre_close"]["skip_ai_decision"] is True
    assert payload["models"]["ai_decision_model"]
    assert payload["models"]["anomalo_retrieval_agent"] == "scheduled-event-investigator"


def test_settings_update_persists_and_updates_running_config(client, app) -> None:
    payload = _update_payload(client)
    payload["schedule"]["pre_market"]["enabled"] = False
    payload["schedule"]["post_close_review"]["skip_ai_decision"] = True
    payload["models"]["ai_decision_model"] = "openai/gpt-oss-120b"
    payload["models"]["anomalo_retrieval_agent"] = "research-agent-v2"

    response = client.put("/api/settings", json=payload)

    assert response.status_code == 200
    updated = response.json()
    assert updated["source"] == "runtime"
    assert updated["revision"] == 1
    assert updated["schedule"]["pre_market"]["enabled"] is False
    assert updated["schedule"]["post_close_review"]["skip_ai_decision"] is True
    assert updated["models"]["ai_decision_model"] == "openai/gpt-oss-120b"
    assert app.state.settings.urus_agent_model == "openai/gpt-oss-120b"
    assert app.state.settings.anomalo_scheduled_agent == "research-agent-v2"

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
