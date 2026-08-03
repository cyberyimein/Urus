from __future__ import annotations

import re
from datetime import UTC, datetime
from types import SimpleNamespace

import yaml

from app.events.definitions import DEFAULT_EVENT_DEFINITIONS
from app.events.prompts import (
    PROMPT_CONFIG_PATH,
    load_scheduled_event_prompts,
    render_result_prompt,
    render_schedule_prompt,
)


def test_schedule_prompt_is_english_yaml_with_four_month_window_and_array_constraints() -> None:
    now = datetime(2026, 8, 3, 6, 0, tzinfo=UTC)
    definition = next(
        item for item in DEFAULT_EVENT_DEFINITIONS if item.key == "macro:fomc_decision"
    )
    context = SimpleNamespace(event_horizon_days=120)

    message = render_schedule_prompt(
        context,
        category="macro",
        definitions=[definition],
        targets=[
            {
                "definition_key": definition.key,
                "subject_type": "market",
                "subject": "market",
            }
        ],
        now=now,
    )
    payload = yaml.safe_load(message)

    assert payload["language"] == "en"
    assert payload["request"]["time_window"] == {
        "start": "2026-08-03T06:00:00+00:00",
        "end": "2026-12-01T06:00:00+00:00",
    }
    constraints = payload["output_contract"]["field_constraints"]
    assert constraints["status"]["allowed_values"] == [
        "expected",
        "scheduled",
        "unverified",
        "postponed",
        "cancelled",
    ]
    assert isinstance(constraints["source_type"]["allowed_values"], list)
    assert payload["request"]["definitions"][0]["preferred_sources"] == [
        "Federal Reserve FOMC calendar",
        "Federal Reserve statements",
    ]


def test_result_prompt_is_yaml_and_selects_only_matching_event_rule() -> None:
    event = SimpleNamespace(
        event_key="macro:fomc_decision:2026-07-29",
        event_type="fomc_decision",
        title="FOMC rate decision",
        subject_type="market",
        subject="market",
        scheduled_at=datetime(2026, 7, 29, 18, 0, tzinfo=UTC),
        result_expected_at=datetime(2026, 7, 29, 18, 0, tzinfo=UTC),
    )

    payload = yaml.safe_load(render_result_prompt(event))

    assert payload["request"]["event_key"] == event.event_key
    assert payload["event_type_rules"] == {
        "fomc_decision": load_scheduled_event_prompts()["result"]["event_type_rules"][
            "fomc_decision"
        ]
    }
    assert payload["output_contract"]["field_constraints"]["result_status"][
        "allowed_values"
    ] == ["not_released", "partial", "confirmed", "revised"]


def test_static_prompt_configuration_contains_no_cjk_text() -> None:
    content = PROMPT_CONFIG_PATH.read_text(encoding="utf-8")
    assert re.search(r"[\u3400-\u9fff]", content) is None


def test_every_scheduled_event_type_has_a_result_fact_contract() -> None:
    rules = load_scheduled_event_prompts()["result"]["event_type_rules"]
    configured_event_types = {item.event_type for item in DEFAULT_EVENT_DEFINITIONS}

    assert set(rules) == configured_event_types
    for event_type, rule in rules.items():
        assert isinstance(rule["expected_facts"], list), event_type
        assert rule["expected_facts"], event_type
        required = set(rule.get("required_actual_facts", []))
        required_any = set(rule.get("required_actual_any_of", []))
        assert required or required_any, event_type
        assert required.union(required_any).issubset(rule["expected_facts"]), event_type
        assert rule["require_sources"] is True, event_type
