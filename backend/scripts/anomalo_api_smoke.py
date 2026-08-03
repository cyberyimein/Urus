"""One-shot Anomalo structured-output smoke test.

This script deliberately does not touch Urus' database. It checks the real
HTTP endpoint and prints only response metadata plus the structured output.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from uuid import uuid4

import httpx


def main() -> int:
    base_url = os.getenv("ANOMALO_BASE_URL", "https://agent.yimeinforge.com").rstrip("/")
    agent = os.getenv("ANOMALO_TEST_AGENT", "scheduled-event-investigator")
    session_id = f"urus-api-smoke-{uuid4()}"
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "urus_api_smoke",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": ["smoke_test"]},
                    "value": {"type": "string", "enum": ["ok"]},
                },
                "required": ["operation", "value"],
                "additionalProperties": False,
            },
        },
    }
    request_payload = {
        "message": (
            "This is a structured-output smoke test. Return exactly one JSON object: "
            '{"operation":"smoke_test","value":"ok"}. '
            "Do not output reasoning, Markdown, or any text outside the JSON object."
        ),
        "session_id": session_id,
        "response_format": response_format,
    }
    url = f"{base_url}/api/agents/{agent}/chat"
    print(f"url={url}")
    print(f"session_id={session_id}")
    try:
        response = httpx.post(url, json=request_payload, timeout=45.0)
    except httpx.HTTPError as exc:
        print(f"transport_error={exc}")
        return 2

    print(f"http_status={response.status_code}")
    try:
        body = response.json()
    except ValueError:
        print(f"response_is_json=false body_prefix={response.text[:500]!r}")
        return 3

    print("response_is_json=true")
    if not isinstance(body, dict):
        print(f"response_type={type(body).__name__}")
        return 4
    print(f"top_level_keys={sorted(body.keys())}")
    output = body.get("output")
    print(f"output_present={output is not None}")
    print(f"output_format={body.get('output_format')!r}")
    print(f"final_text_present={bool(body.get('final_text'))}")
    events = body.get("events")
    if isinstance(events, list):
        print(f"event_count={len(events)}")
        for index, event in enumerate(events[-12:]):
            if isinstance(event, dict):
                print(
                    f"event_{index}=type:{event.get('type')!r},"
                    f"keys:{sorted(event.keys())}"
                )
                if event.get("type") == "run.error":
                    print(f"event_{index}_data={json.dumps(event.get('data'), ensure_ascii=False)}")
    run = body.get("run")
    if isinstance(run, dict) and run.get("error"):
        print(f"run_error={json.dumps(run['error'], ensure_ascii=False)}")
    if output is not None:
        print(f"output={json.dumps(output, ensure_ascii=False, sort_keys=True)}")
    if body.get("final_text"):
        text = str(body["final_text"])
        markers = ("<think>", "</think>", "chain of thought", "思考过程")
        print(f"final_text_has_thought_marker={any(marker in text.lower() for marker in markers)}")
        print(f"final_text_prefix={text[:240]!r}")

    if response.status_code >= 400:
        return 5
    if output != {"operation": "smoke_test", "value": "ok"}:
        print("structured_output_match=false")
        return 6
    print("structured_output_match=true")
    print(f"checked_at={datetime.now(UTC).isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
