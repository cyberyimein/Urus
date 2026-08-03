from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.core.time import to_iso


PROMPT_CONFIG_PATH = Path(__file__).with_name("prompts") / "scheduled_events.yaml"


@lru_cache(maxsize=1)
def load_scheduled_event_prompts() -> dict[str, Any]:
    with PROMPT_CONFIG_PATH.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    if not isinstance(config, dict) or config.get("language") != "en":
        raise ValueError("Scheduled-event prompt configuration must use English.")
    for section in ("schedule", "result"):
        if not isinstance(config.get(section), dict):
            raise ValueError(f"Scheduled-event prompt section is missing: {section}")
    rules = config["result"].get("event_type_rules", {})
    if not isinstance(rules, dict):
        raise ValueError("Result event_type_rules must be a mapping.")
    for event_type, rule in rules.items():
        if not isinstance(rule, dict) or not isinstance(rule.get("expected_facts"), list):
            raise ValueError(f"Result rule {event_type} must define expected_facts as an array.")
        expected = set(rule["expected_facts"])
        for key in ("required_actual_facts", "required_actual_any_of"):
            values = rule.get(key, [])
            if not isinstance(values, list) or not set(values).issubset(expected):
                raise ValueError(f"Result rule {event_type}.{key} must be an expected-facts array.")
    return config


def render_schedule_prompt(
    context: Any,
    *,
    category: str,
    definitions: list[Any],
    targets: list[dict[str, str]],
    now: datetime,
) -> str:
    config = load_scheduled_event_prompts()
    prompt = deepcopy(config["schedule"])
    prompt["language"] = config["language"]
    prompt["request"] = {
        "operation": "discover_schedule",
        "category": category,
        "time_window": {
            "start": to_iso(now),
            "end": to_iso(now + timedelta(days=context.event_horizon_days)),
        },
        "definitions": [
            {
                "definition_key": item.key,
                "event_type": item.event_type,
                "title": item.title,
                "description": item.description,
                "cadence": item.cadence,
                "preferred_sources": list(item.preferred_sources),
            }
            for item in definitions
        ],
        "targets": targets,
    }
    return yaml.safe_dump(
        prompt,
        sort_keys=False,
        allow_unicode=False,
        width=120,
    )


def render_result_prompt(event: Any) -> str:
    config = load_scheduled_event_prompts()
    prompt = deepcopy(config["result"])
    prompt["language"] = config["language"]
    event_rules = prompt.pop("event_type_rules", {})
    if event.event_type in event_rules:
        prompt["event_type_rules"] = {event.event_type: event_rules[event.event_type]}
    prompt["request"] = {
        "operation": "collect_result",
        "event_key": event.event_key,
        "event_type": event.event_type,
        "title": event.title,
        "subject_type": event.subject_type,
        "subject": event.subject,
        "scheduled_at": to_iso(event.scheduled_at),
        "result_expected_at": to_iso(event.result_expected_at),
    }
    return yaml.safe_dump(
        prompt,
        sort_keys=False,
        allow_unicode=False,
        width=120,
    )


def event_result_rules(event_type: str) -> dict[str, Any]:
    rules = load_scheduled_event_prompts()["result"].get("event_type_rules", {})
    value = rules.get(event_type, {}) if isinstance(rules, dict) else {}
    return deepcopy(value) if isinstance(value, dict) else {}
