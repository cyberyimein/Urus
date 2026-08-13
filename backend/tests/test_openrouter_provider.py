from __future__ import annotations

import json

import httpx
import pytest

from app.urus_agent.providers.openrouter import OpenRouterProvider


def _response_body() -> dict:
    return {
        "id": "gen-test",
        "model": "deepseek/deepseek-v4-flash-0731",
        "choices": [{"message": {"role": "assistant", "content": "{}"}}],
    }


def test_openrouter_omits_completion_cap_when_unset_and_reads_request_id() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json=_response_body(),
            headers={"content-type": "application/json", "x-request-id": "req-test"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenRouterProvider(
        api_key="test-key",
        model="deepseek/deepseek-v4-flash-0731",
        http_client=client,
    )
    result = provider.complete([], tools=[], response_format={"type": "json_schema"})
    client.close()

    assert result.message["content"] == "{}"
    assert "max_tokens" not in seen[0]


def test_openrouter_sends_explicit_reasoning_policy() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_response_body())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenRouterProvider(
        api_key="test-key",
        model="deepseek/deepseek-v4-flash-0731",
        reasoning={"enabled": False, "exclude": True},
        http_client=client,
    )
    provider.complete([], tools=[], response_format={"type": "json_schema"})
    client.close()

    assert seen[0]["reasoning"] == {"enabled": False, "exclude": True}


def test_openrouter_retries_non_json_response_and_succeeds() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                content=b"<upstream gateway failure>",
                headers={"content-type": "text/plain", "x-request-id": "req-bad"},
            )
        return httpx.Response(
            200,
            json=_response_body(),
            headers={"content-type": "application/json", "x-request-id": "req-good"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenRouterProvider(
        api_key="test-key",
        model="test-model",
        max_retries=1,
        retry_backoff_seconds=0,
        http_client=client,
    )
    result = provider.complete([], tools=[], response_format={"type": "json_schema"})
    client.close()

    assert calls == 2
    assert result.raw["id"] == "gen-test"


def test_openrouter_reports_non_json_response_diagnostics_after_retries() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502,
            content=b"upstream body prefix",
            headers={"content-type": "text/plain", "x-request-id": "req-502"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenRouterProvider(
        api_key="test-key",
        model="test-model",
        max_retries=0,
        http_client=client,
    )

    with pytest.raises(RuntimeError, match="status_code=502") as error:
        provider.complete([], tools=[], response_format={"type": "json_schema"})
    client.close()

    message = str(error.value)
    assert "content_type=text/plain" in message
    assert "request_id=req-502" in message
    assert "upstream body prefix" in message


def test_openrouter_preserves_json_parse_error_and_response_diagnostics() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'{"id":"gen-malformed", "choices": [',
            headers={"content-type": "application/json"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenRouterProvider(
        api_key="test-key",
        model="test-model",
        max_retries=0,
        http_client=client,
    )

    with pytest.raises(RuntimeError, match="invalid JSON response") as error:
        provider.complete([], tools=[], response_format={"type": "json_schema"})
    client.close()

    message = str(error.value)
    assert "status_code=200" in message
    assert "content_type=application/json" in message
    assert "request_id=gen-malformed" in message
    assert "parse_error=" in message
    assert "choices" in message
