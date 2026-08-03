from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
import re

import httpx


@dataclass(frozen=True)
class AnomaloRequest:
    session_id: str
    message: str
    agent: str | None = None
    response_format: dict[str, Any] | None = None


@dataclass(frozen=True)
class AnomaloResponse:
    final_text: str | None
    is_mock: bool
    disabled: bool = False
    output: Any | None = None
    output_format: str | None = None
    agent: dict[str, Any] | None = None
    events: list[dict[str, Any]] | None = None
    error_code: str | None = None
    error_message: str | None = None


class AnomaloAdapter(Protocol):
    def summarize(self, request: AnomaloRequest) -> AnomaloResponse: ...

    def investigate(self, request: AnomaloRequest) -> AnomaloResponse: ...


class MockAnomaloAdapter:
    """Offline stand-in; it never creates an HTTP client or accesses the network."""

    def summarize(self, request: AnomaloRequest) -> AnomaloResponse:
        return AnomaloResponse(
            final_text=(
                "模拟摘要：假定事件已发布，当前仅用于验证条件步骤、session_id "
                f"({request.session_id}) 和前端展示链路。"
            ),
            is_mock=True,
        )

    def investigate(self, request: AnomaloRequest) -> AnomaloResponse:
        """Return a deterministic structured fixture for offline validation.

        The coordinator still validates this payload against the same strict
        Pydantic contracts used for the real Anomalo response.
        """
        if request.response_format and "result" in request.response_format.get("json_schema", {}).get(
            "name", ""
        ):
            event_match = re.search(r"event_key=([^\s]+)", request.message)
            output: dict[str, Any] = {
                "operation": "collect_result",
                "event_key": event_match.group(1)
                if event_match
                else request.session_id.removeprefix("urus-event-").removesuffix("-result"),
                "result_status": "not_released",
                "facts": [],
                "summary": "模拟结果：尚未发现已发布结果。",
                "guidance": "在下一次复盘时重新调查。",
                "confidence": 0.5,
                "needs_follow_up": True,
                "sources": [],
            }
        else:
            output = {
                "operation": "discover_schedule",
                "generated_at": "2026-01-01T00:00:00+00:00",
                "events": [],
                "missing_definitions": [],
                "notes": ["模拟发现：未访问网页。"],
            }
        return AnomaloResponse(
            final_text=None,
            is_mock=True,
            output=output,
            output_format="json_schema",
            agent={"name": request.agent} if request.agent else None,
        )


class DisabledAnomaloAdapter:
    """Disabled behavior for a future production wiring point."""

    def summarize(self, request: AnomaloRequest) -> AnomaloResponse:
        return AnomaloResponse(final_text=None, is_mock=True, disabled=True)

    def investigate(self, request: AnomaloRequest) -> AnomaloResponse:
        return AnomaloResponse(final_text=None, is_mock=True, disabled=True)


class HttpAnomaloAdapter:
    """Small non-streaming client for an Anomalo preset Agent.

    Anomalo owns web search and the system prompt. Urus only supplies a
    stable session, task message and strict response schema; no Anomalo
    management token is needed for this endpoint.
    """

    def __init__(self, base_url: str, timeout_seconds: float = 300.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(
            timeout=httpx.Timeout(timeout_seconds, connect=min(10.0, timeout_seconds))
        )

    def summarize(self, request: AnomaloRequest) -> AnomaloResponse:
        return self._call(request)

    def investigate(self, request: AnomaloRequest) -> AnomaloResponse:
        return self._call(request)

    def _call(self, request: AnomaloRequest) -> AnomaloResponse:
        if not request.agent:
            raise ValueError("Anomalo agent name is required")
        payload: dict[str, Any] = {
            "message": request.message,
            "session_id": request.session_id,
        }
        if request.response_format:
            payload["response_format"] = request.response_format
        try:
            response = self.client.post(
                f"{self.base_url}/api/agents/{request.agent}/chat",
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            return AnomaloResponse(
                final_text=None,
                is_mock=False,
                error_code=f"http_{exc.response.status_code}",
                error_message=detail or str(exc),
            )
        except (httpx.HTTPError, ValueError) as exc:
            return AnomaloResponse(
                final_text=None,
                is_mock=False,
                error_code="request_failed",
                error_message=str(exc),
            )

        run = body.get("run") if isinstance(body, dict) else None
        error = run.get("error") if isinstance(run, dict) else None
        events = body.get("events") if isinstance(body, dict) else None
        # Recent Anomalo responses may report a terminal run.error only in the
        # event stream, without copying it to body.run.error. Preserve that
        # error so callers do not misclassify a timed-out run as empty output.
        if not error and isinstance(events, list):
            for event in reversed(events):
                if not isinstance(event, dict) or event.get("type") != "run.error":
                    continue
                data = event.get("data")
                if isinstance(data, dict):
                    error = data
                break
        return AnomaloResponse(
            final_text=body.get("final_text") if isinstance(body, dict) else None,
            is_mock=False,
            output=body.get("output") if isinstance(body, dict) else None,
            output_format=body.get("output_format") if isinstance(body, dict) else None,
            agent=body.get("agent") if isinstance(body, dict) else None,
            events=events,
            error_code=(
                (error.get("code") or error.get("error_code"))
                if isinstance(error, dict)
                else None
            ),
            error_message=(
                (error.get("message") or error.get("error"))
                if isinstance(error, dict)
                else None
            ),
        )

    def close(self) -> None:
        self.client.close()
