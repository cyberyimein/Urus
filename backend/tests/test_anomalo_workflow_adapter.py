from __future__ import annotations

import pytest
import httpx

from app.integrations.anomalo_workflow import HttpAnomaloWorkflowAdapter, WorkflowAdapterError


def test_ndjson_decoder_handles_split_json_chunks() -> None:
    events = HttpAnomaloWorkflowAdapter.split_ndjson([
        b'{"sequence":1,"type":"run.que',
        b'ued"}\n{"sequence":2,"type":"run.succeeded"}',
    ])
    assert [item["sequence"] for item in events] == [1, 2]


def test_workflow_ref_requires_integer_version() -> None:
    with pytest.raises(Exception):
        # Calling the constructor is intentionally avoided; the helper is
        # exercised by a lightweight stream path in integration tests.
        from app.integrations.anomalo_workflow import _split_ref

        _split_ref("urus-review@latest")


@pytest.mark.anyio
async def test_http_adapter_uses_runtime_bearer_and_exact_ref() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            201,
            json={"run": {"run_id": "run-1", "status": "queued", "target_ref": "urus-review@1", "target_hash": "sha256:" + "a" * 64}},
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = HttpAnomaloWorkflowAdapter("https://anomalo.test", "secret", client=client)
    result = await adapter.start(
        workflow_ref="urus-review@1",
        input_payload={"ok": True},
        idempotency_key="key-1",
        metadata={"source": "urus"},
        compiled_hash="a" * 64,
    )
    assert result.run_id == "run-1"
    assert seen[0].url.path == "/api/workflows/urus-review/versions/1/runs"
    assert seen[0].headers["authorization"] == "Bearer secret"
    await client.aclose()


@pytest.mark.anyio
async def test_http_stream_adapter_exposes_run_id_before_terminal_event() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            content=(
                b'{"run_id":"run-stream","runtime_kind":"workflow",'
                b'"target_ref":"urus-review@1","sequence":1,"type":"run.queued",'
                b'"data":{"status":"queued"}}\n'
                b'{"run_id":"run-stream","runtime_kind":"workflow",'
                b'"target_ref":"urus-review@1","sequence":2,"type":"run.succeeded",'
                b'"data":{"status":"succeeded"}}\n'
            ),
            headers={"content-type": "application/x-ndjson"},
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = HttpAnomaloWorkflowAdapter("https://anomalo.test", "secret", client=client)
    events = [
        event
        async for event in adapter.start_stream(
            workflow_ref="urus-review@1",
            input_payload={"ok": True},
            idempotency_key="key-stream",
            metadata={"source": "urus"},
            compiled_hash="a" * 64,
        )
    ]
    assert events[0]["run_id"] == "run-stream"
    assert seen[0].url.path == "/api/workflows/urus-review/versions/1/runs/stream"
    await client.aclose()


@pytest.mark.anyio
async def test_http_stream_adapter_surfaces_streaming_error_body() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error_code": "workflow_ref_forbidden",
                "message": "Workflow urus-review@2 is not allowed for this service client.",
            },
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = HttpAnomaloWorkflowAdapter("https://anomalo.test", "secret", client=client)
    with pytest.raises(WorkflowAdapterError) as error:
        _ = [
            event
            async for event in adapter.start_stream(
                workflow_ref="urus-review@2",
                input_payload={"ok": True},
                idempotency_key="key-forbidden",
                metadata={"source": "urus"},
                compiled_hash="a" * 64,
            )
        ]
    assert error.value.code == "workflow_ref_forbidden"
    assert "not allowed for this service client" in error.value.message
    await client.aclose()
