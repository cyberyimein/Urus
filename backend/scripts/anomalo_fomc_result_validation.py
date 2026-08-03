"""One-shot live FOMC result validation.

This calls Anomalo's structured result path for a manually seeded historical
event. It validates the same Pydantic contract used by Urus and never writes
to the Urus database.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.events.contracts import EventResultOutput, result_response_format
from app.events.prompts import render_result_prompt
from app.integrations.anomalo import AnomaloAdapter, AnomaloRequest, HttpAnomaloAdapter


def _message(event_key: str, scheduled_at: datetime, result_expected_at: datetime) -> str:
    return render_result_prompt(
        SimpleNamespace(
            event_key=event_key,
            event_type="fomc_decision",
            title="FOMC rate decision",
            subject_type="market",
            subject="market",
            scheduled_at=scheduled_at,
            result_expected_at=result_expected_at,
        )
    )


def _has_thought_marker(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in ("<think>", "</think>", "chain of thought", "思考过程"))


def main() -> int:
    base_url = os.getenv("ANOMALO_BASE_URL", "https://agent.yimeinforge.com")
    agent_name = os.getenv("ANOMALO_TEST_AGENT", "scheduled-event-investigator")
    timeout_seconds = float(os.getenv("ANOMALO_TEST_TIMEOUT_SECONDS", "600"))
    event_key = os.getenv("ANOMALO_TEST_EVENT_KEY", "macro:fomc_decision:2026-07-29")
    now = datetime.now(UTC)
    scheduled_at = now - timedelta(days=5)
    result_expected_at = scheduled_at
    adapter: AnomaloAdapter = HttpAnomaloAdapter(
        base_url=base_url,
        timeout_seconds=timeout_seconds,
    )
    request = AnomaloRequest(
        session_id=f"urus-fomc-result-validation-{uuid4()}",
        agent=agent_name,
        message=_message(event_key, scheduled_at, result_expected_at),
        response_format=result_response_format(),
    )
    try:
        response = adapter.investigate(request)
        print(f"agent={agent_name}")
        print(f"event_key={event_key}")
        print(f"output_format={response.output_format!r}")
        print(f"output_present={response.output is not None}")
        print(f"error_code={response.error_code!r}")
        print(f"error_message={response.error_message!r}")
        if response.final_text:
            print(f"final_text_has_thought_marker={_has_thought_marker(response.final_text)}")
            print(f"final_text_prefix={response.final_text[:240]!r}")
        events = response.events or []
        tool_errors = [
            event for event in events if isinstance(event, dict) and event.get("type") == "tool.error"
        ]
        print(f"anomalo_event_count={len(events)}")
        print(f"tool_error_count={len(tool_errors)}")
        if tool_errors:
            print("tool_error_note=tool.error 已保留为工具链告警；不覆盖结构化 output 的有效性。")
        for index, event in enumerate(events):
            if not isinstance(event, dict):
                continue
            data = event.get("data")
            data_keys = sorted(data.keys()) if isinstance(data, dict) else []
            print(f"anomalo_event_{index}=type:{event.get('type')!r},data_keys:{data_keys}")

        if response.error_code or response.output is None:
            return 2
        if not isinstance(response.output, dict):
            print("raw_output_is_object=false")
            return 3
        required_keys = {
            "operation",
            "event_key",
            "result_status",
            "facts",
            "sources",
        }
        missing_keys = sorted(required_keys - set(response.output))
        print(f"raw_output_missing_keys={missing_keys}")
        if missing_keys:
            print(f"raw_output={json.dumps(response.output, ensure_ascii=False, sort_keys=True)}")
            return 3
        try:
            parsed = EventResultOutput.model_validate(response.output)
        except Exception as exc:
            print("schema_valid=false")
            print(f"schema_error={exc}")
            return 4
        print("schema_valid=true")
        print(f"operation={parsed.operation}")
        print(f"result_status={parsed.result_status}")
        print(f"fact_count={len(parsed.facts)}")
        print(f"source_count={len(parsed.sources)}")
        print(f"next_check_at={parsed.next_check_at}")
        if parsed.event_key != event_key:
            print(f"event_key_match=false received={parsed.event_key}")
            return 5
        print("event_key_match=true")
        if parsed.result_status in {"confirmed", "revised"} and not any(
            fact.actual not in (None, "") for fact in parsed.facts
        ):
            print("confirmed_result_has_actual=false")
            return 6
        print("confirmed_result_has_actual=true")
        return 0
    finally:
        close = getattr(adapter, "close", None)
        if close:
            close()


if __name__ == "__main__":
    sys.exit(main())
