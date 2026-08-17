from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


_RESPONSE_PREVIEW_LIMIT = 2_000
_RESPONSE_ID_PATTERN = re.compile(r'"id"\s*:\s*"([^"\r\n]{1,256})"')


@dataclass(frozen=True)
class ProviderResponse:
    message: dict[str, Any]
    raw: dict[str, Any]
    model: str | None = None
    usage: dict[str, Any] | None = None


class LLMProvider(Protocol):
    provider_name: str
    model: str | None

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        response_format: dict[str, Any],
    ) -> ProviderResponse: ...


class OpenRouterProvider:
    provider_name = "openrouter"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout_seconds: float = 1200.0,
        max_completion_tokens: int | None = None,
        temperature: float = 0.1,
        input_cost_per_million: float = 0.0,
        cached_input_cost_per_million: float = 0.0,
        cache_write_cost_per_million: float = 0.0,
        output_cost_per_million: float = 0.0,
        max_retries: int = 1,
        retry_backoff_seconds: float = 0.5,
        reasoning: dict[str, Any] | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OpenRouter API key is required")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        parsed_max_tokens = int(max_completion_tokens or 0)
        self.max_completion_tokens = (
            max(1000, parsed_max_tokens) if parsed_max_tokens > 0 else None
        )
        self.temperature = temperature
        self.input_cost_per_million = max(0.0, float(input_cost_per_million))
        self.cached_input_cost_per_million = max(
            0.0, float(cached_input_cost_per_million)
        )
        self.cache_write_cost_per_million = max(
            0.0, float(cache_write_cost_per_million)
        )
        self.output_cost_per_million = max(0.0, float(output_cost_per_million))
        self.max_retries = max(0, min(int(max_retries), 2))
        self.retry_backoff_seconds = max(0.0, min(float(retry_backoff_seconds), 5.0))
        self.reasoning = dict(reasoning) if reasoning is not None else None
        self._client = http_client

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        response_format: dict[str, Any],
    ) -> ProviderResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "response_format": response_format,
        }
        if self.max_completion_tokens is not None:
            payload["max_tokens"] = self.max_completion_tokens
        if self.reasoning is not None:
            payload["reasoning"] = self.reasoning
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://urus.local",
            "X-Title": "Urus Agent",
        }
        client = self._client or httpx.Client(timeout=self.timeout_seconds)
        last_response: httpx.Response | None = None
        try:
            for attempt in range(self.max_retries + 1):
                try:
                    response = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                    last_response = response
                    response.raise_for_status()
                    body = response.json()
                    if not isinstance(body, dict):
                        raise ValueError("OpenRouter response JSON must be an object")
                    break
                except httpx.TimeoutException as exc:
                    if attempt < self.max_retries:
                        time.sleep(self.retry_backoff_seconds * (attempt + 1))
                        continue
                    raise TimeoutError("provider_timeout: OpenRouter request timed out") from exc
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    retryable = status == 429 or status >= 500
                    if retryable and attempt < self.max_retries:
                        time.sleep(self.retry_backoff_seconds * (attempt + 1))
                        continue
                    code = "provider_rate_limited" if status == 429 else "provider_error"
                    raise RuntimeError(
                        f"{code}: OpenRouter returned HTTP {status}; "
                        f"{_response_diagnostics(exc.response)}"
                    ) from exc
                except ValueError as exc:
                    diagnostic = _response_diagnostics(last_response)
                    if attempt < self.max_retries:
                        time.sleep(self.retry_backoff_seconds * (attempt + 1))
                        continue
                    raise RuntimeError(
                        "provider_error: OpenRouter returned an invalid JSON response; "
                        f"parse_error={json.dumps(str(exc), ensure_ascii=False)} "
                        f"attempts={attempt + 1}; {diagnostic}"
                    ) from exc
                except httpx.HTTPError as exc:
                    if isinstance(exc, httpx.TransportError) and attempt < self.max_retries:
                        time.sleep(self.retry_backoff_seconds * (attempt + 1))
                        continue
                    raise RuntimeError(f"provider_error: {exc}") from exc
            else:  # pragma: no cover - defensive loop guard
                raise RuntimeError("provider_error: OpenRouter request exhausted retries")
        except (TimeoutError, RuntimeError):
            raise
        finally:
            if self._client is None:
                client.close()
        choices = body.get("choices") or []
        if not choices or not isinstance(choices[0].get("message"), dict):
            provider_id = body.get("id") if isinstance(body, dict) else None
            diagnostic = _response_diagnostics(last_response, body=body)
            if provider_id and "request_id=" not in diagnostic:
                diagnostic += f" provider_id={provider_id}"
            raise RuntimeError(
                "provider_error: OpenRouter response has no assistant message; "
                f"{diagnostic}"
            )
        return ProviderResponse(
            message=choices[0]["message"],
            raw=body,
            model=body.get("model") or self.model,
            usage=body.get("usage") if isinstance(body.get("usage"), dict) else None,
        )


def _response_diagnostics(
    response: httpx.Response | None,
    *,
    body: Any = None,
) -> str:
    """Return bounded, non-secret diagnostics for a provider response.

    The response body is intentionally limited to a short prefix.  This is
    persisted in a trace error, so it must be useful for diagnosing gateways
    and upstream providers without copying an entire model response into the
    error record.
    """

    if response is None:
        return "status_code=unknown content_type=unknown request_id=unknown body_prefix=<none>"
    header_request_id = next(
        (
            response.headers.get(name)
            for name in (
                "x-request-id",
                "x-openrouter-request-id",
                "x-openrouter-id",
                "request-id",
            )
            if response.headers.get(name)
        ),
        None,
    )
    if body is None:
        raw_text = response.text
        try:
            parsed_body = response.json()
        except ValueError:
            parsed_body = None
    else:
        raw_text = json.dumps(body, ensure_ascii=False, separators=(",", ":"), default=str)
        parsed_body = body
    body_request_id = (
        parsed_body.get("id")
        if isinstance(parsed_body, dict) and parsed_body.get("id")
        else None
    )
    if not body_request_id:
        match = _RESPONSE_ID_PATTERN.search(raw_text[:_RESPONSE_PREVIEW_LIMIT])
        body_request_id = match.group(1) if match else None
    request_id = header_request_id or body_request_id
    preview = " ".join(raw_text[:_RESPONSE_PREVIEW_LIMIT].split())
    if len(raw_text) > _RESPONSE_PREVIEW_LIMIT:
        preview += "…"
    return (
        f"status_code={response.status_code} "
        f"content_type={response.headers.get('content-type', 'unknown')} "
        f"request_id={request_id or 'unknown'} "
        f"body_prefix={json.dumps(preview, ensure_ascii=False)}"
    )


class FakeLLMProvider:
    """Deterministic provider used by tests and local framework validation."""

    provider_name = "fake"

    def __init__(self, responses: list[dict[str, Any]], model: str = "fake-model") -> None:
        self.responses = list(responses)
        self.model = model
        self.requests: list[dict[str, Any]] = []

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        response_format: dict[str, Any],
    ) -> ProviderResponse:
        self.requests.append({"messages": messages, "tools": tools, "response_format": response_format})
        if not self.responses:
            raise RuntimeError("provider_error: fake provider has no response")
        value = self.responses.pop(0)
        message = value.get("message") if isinstance(value.get("message"), dict) else {"role": "assistant", "content": json.dumps(value, ensure_ascii=False)}
        usage = value.get("usage") if isinstance(value.get("usage"), dict) else {}
        return ProviderResponse(message=message, raw=value, model=self.model, usage=usage)
