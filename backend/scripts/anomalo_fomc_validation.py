"""One-shot live FOMC scheduled-event validation.

Calls the configured Anomalo preset Agent, validates ``output`` with the same
Pydantic contract used by Urus, and does not write to SQLite.
"""

from __future__ import annotations

import os
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.events.contracts import EventDiscoveryOutput, discovery_response_format
from app.events.definitions import DEFAULT_EVENT_DEFINITIONS
from app.events.prompts import render_schedule_prompt
from app.integrations.anomalo import AnomaloAdapter, AnomaloRequest, HttpAnomaloAdapter


def _message() -> str:
    now = datetime.now(UTC)
    definition = next(
        item for item in DEFAULT_EVENT_DEFINITIONS if item.key == "macro:fomc_decision"
    )
    return render_schedule_prompt(
        SimpleNamespace(event_horizon_days=120),
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


def _has_thought_marker(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in ("<think>", "</think>", "chain of thought", "思考过程"))


def main() -> int:
    base_url = os.getenv("ANOMALO_BASE_URL", "https://agent.yimeinforge.com")
    agent_name = os.getenv("ANOMALO_TEST_AGENT", "scheduled-event-investigator")
    timeout_seconds = float(os.getenv("ANOMALO_TEST_TIMEOUT_SECONDS", "300"))
    adapter: AnomaloAdapter = HttpAnomaloAdapter(
        base_url=base_url,
        timeout_seconds=timeout_seconds,
    )
    request = AnomaloRequest(
        session_id=f"urus-fomc-validation-{uuid4()}",
        agent=agent_name,
        message=_message(),
        response_format=discovery_response_format(),
    )
    try:
        response = adapter.investigate(request)
        print(f"agent={agent_name}")
        print(f"output_format={response.output_format!r}")
        print(f"output_present={response.output is not None}")
        print(f"final_text_present={bool(response.final_text)}")
        print(f"error_code={response.error_code!r}")
        print(f"error_message={response.error_message!r}")
        if response.final_text:
            print(f"final_text_has_thought_marker={_has_thought_marker(response.final_text)}")
            print(f"final_text_prefix={response.final_text[:240]!r}")
        if response.events:
            print(f"anomalo_event_count={len(response.events)}")
            for index, event in enumerate(response.events):
                if not isinstance(event, dict):
                    continue
                event_type = event.get("type")
                data = event.get("data")
                data_keys = sorted(data.keys()) if isinstance(data, dict) else []
                print(f"anomalo_event_{index}=type:{event_type!r},data_keys:{data_keys}")
        if response.error_code or response.output is None:
            return 2
        if not isinstance(response.output, dict):
            print("raw_output_is_object=false")
            return 3
        required_keys = {"operation", "generated_at", "events", "missing_definitions", "notes"}
        missing_keys = sorted(required_keys - set(response.output))
        print(f"raw_output_missing_keys={missing_keys}")
        if missing_keys:
            print(f"raw_output={json.dumps(response.output, ensure_ascii=False, sort_keys=True)}")
            return 3
        try:
            parsed = EventDiscoveryOutput.model_validate(response.output)
        except Exception as exc:
            print(f"schema_valid=false")
            print(f"schema_error={exc}")
            return 4
        print("schema_valid=true")
        print(f"operation={parsed.operation}")
        print(f"event_count={len(parsed.events)}")
        print(f"missing_definitions={parsed.missing_definitions}")
        print(f"notes={parsed.notes}")
        if not parsed.events:
            print("fomc_expected_event_check=false")
            print("fomc_expected_event_error=macro:fomc_decision returned no events")
            return 5
        print("fomc_expected_event_check=true")
        for index, event in enumerate(parsed.events, start=1):
            print(
                f"event_{index}={event.event_key}"
                f" status={event.status}"
                f" scheduled_at={event.scheduled_at}"
                f" confidence={event.confidence}"
                f" sources={len(event.sources)}"
            )
        return 0
    finally:
        close = getattr(adapter, "close", None)
        if close:
            close()


if __name__ == "__main__":
    sys.exit(main())
