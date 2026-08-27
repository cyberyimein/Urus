from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Protocol
from uuid import uuid4

import httpx


@dataclass(frozen=True)
class WorkflowStartResult:
    run_id: str
    status: str
    target_ref: str
    target_hash: str | None
    runtime_kind: str | None = None
    output: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    events: list[dict[str, Any]] | None = None


class AnomaloWorkflowAdapter(Protocol):
    async def start(
        self,
        *,
        workflow_ref: str,
        input_payload: dict[str, Any],
        idempotency_key: str,
        metadata: dict[str, Any],
        compiled_hash: str,
    ) -> WorkflowStartResult: ...

    async def start_stream(
        self,
        *,
        workflow_ref: str,
        input_payload: dict[str, Any],
        idempotency_key: str,
        metadata: dict[str, Any],
        compiled_hash: str,
    ) -> AsyncIterator[dict[str, Any]]: ...

    async def get_run(self, run_id: str) -> dict[str, Any]: ...

    async def get_events(self, run_id: str, *, after_sequence: int = 0) -> list[dict[str, Any]]: ...

    async def stop(self, run_id: str, *, reason: str = "user_stop") -> dict[str, Any]: ...

    async def close(self) -> None: ...


class WorkflowAdapterError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class HttpAnomaloWorkflowAdapter:
    """Runtime-only client for Anomalo's published Workflow interface."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        connect_timeout_seconds: float = 10.0,
        read_timeout_seconds: float = 1200.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError("Anomalo Workflow base URL is required")
        if not token.strip():
            raise ValueError("Anomalo Workflow token is required")
        self.base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(read_timeout_seconds, connect=connect_timeout_seconds),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        if client is not None:
            self.client.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})

    @staticmethod
    def split_ndjson(chunks: list[bytes | str]) -> list[dict[str, Any]]:
        """Decode NDJSON even when a network chunk splits a JSON line."""

        buffer = ""
        events: list[dict[str, Any]] = []
        for chunk in chunks:
            buffer += chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if line:
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise WorkflowAdapterError("invalid_ndjson", "Anomalo 返回了无效 NDJSON。") from exc
                    if isinstance(value, dict):
                        events.append(value)
        if buffer.strip():
            try:
                value = json.loads(buffer.strip())
            except json.JSONDecodeError as exc:
                raise WorkflowAdapterError("invalid_ndjson", "Anomalo 返回了不完整 NDJSON。") from exc
            if isinstance(value, dict):
                events.append(value)
        return events

    async def start(
        self,
        *,
        workflow_ref: str,
        input_payload: dict[str, Any],
        idempotency_key: str,
        metadata: dict[str, Any],
        compiled_hash: str,
    ) -> WorkflowStartResult:
        name, version = _split_ref(workflow_ref)
        response = await self._request(
            "POST",
            f"/api/workflows/{name}/versions/{version}/runs",
            json={"input": input_payload, "idempotency_key": idempotency_key, "metadata": metadata},
        )
        run = response.get("run") if isinstance(response, dict) else None
        if not isinstance(run, dict) or not run.get("run_id"):
            raise WorkflowAdapterError("invalid_workflow_response", "Anomalo 返回缺少 run_id。")
        return WorkflowStartResult(
            run_id=str(run["run_id"]),
            status=str(run.get("status") or "queued"),
            target_ref=str(run.get("target_ref") or ""),
            target_hash=_strip_hash(run.get("target_hash")),
            runtime_kind=str(run.get("runtime_kind")) if run.get("runtime_kind") is not None else None,
            output=run.get("output") if isinstance(run.get("output"), dict) else None,
            usage=run.get("usage") if isinstance(run.get("usage"), dict) else None,
            events=[item for item in response.get("events", []) if isinstance(item, dict)]
            if isinstance(response.get("events"), list)
            else None,
        )

    async def start_stream(
        self,
        *,
        workflow_ref: str,
        input_payload: dict[str, Any],
        idempotency_key: str,
        metadata: dict[str, Any],
        compiled_hash: str,
    ) -> AsyncIterator[dict[str, Any]]:
        name, version = _split_ref(workflow_ref)
        try:
            async with self.client.stream(
                "POST",
                f"{self.base_url}/api/workflows/{name}/versions/{version}/runs/stream",
                json={"input": input_payload, "idempotency_key": idempotency_key, "metadata": metadata},
            ) as response:
                await self._raise_for_status(response, path=f"/api/workflows/{name}/versions/{version}/runs/stream")
                async for line in response.aiter_lines():
                    if line.strip():
                        try:
                            parsed = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise WorkflowAdapterError("invalid_ndjson", "Anomalo 返回了无效 NDJSON。") from exc
                        if isinstance(parsed, dict):
                            yield parsed
        except WorkflowAdapterError:
            raise
        except httpx.TimeoutException as exc:
            raise WorkflowAdapterError("workflow_unavailable", "Anomalo Workflow 请求超时。", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise WorkflowAdapterError("workflow_unavailable", "Anomalo Workflow 网络不可用。", retryable=True) from exc

    async def get_run(self, run_id: str) -> dict[str, Any]:
        response = await self._request("GET", f"/api/runs/{run_id}")
        run = response.get("run") if isinstance(response, dict) else response
        return dict(run) if isinstance(run, dict) else {}

    async def get_events(self, run_id: str, *, after_sequence: int = 0) -> list[dict[str, Any]]:
        response = await self._request("GET", f"/api/runs/{run_id}/events?after_sequence={max(0, after_sequence)}")
        values = response.get("events") if isinstance(response, dict) else response
        return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []

    async def stop(self, run_id: str, *, reason: str = "user_stop") -> dict[str, Any]:
        return await self._request("POST", f"/api/runs/{run_id}/stop", json={"reason": reason})

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self.client.request(method, f"{self.base_url}{path}", **kwargs)
            await self._raise_for_status(response, path=path)
            body = response.json()
        except WorkflowAdapterError:
            raise
        except httpx.TimeoutException as exc:
            raise WorkflowAdapterError("workflow_unavailable", "Anomalo Workflow 请求超时。", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise WorkflowAdapterError("workflow_unavailable", "Anomalo Workflow 网络不可用。", retryable=True) from exc
        except ValueError as exc:
            raise WorkflowAdapterError("invalid_workflow_response", "Anomalo 返回了无效 JSON。") from exc
        return body if isinstance(body, dict) else {}

    async def _raise_for_status(self, response: httpx.Response, *, path: str = "") -> None:
        if response.is_success:
            return
        code = "workflow_unavailable" if response.status_code in {408, 429, 500, 502, 503, 504} else "workflow_runtime_error"
        if response.status_code == 401:
            code = "unauthorized"
        elif response.status_code == 403:
            code = "workflow_ref_forbidden"
        elif response.status_code == 404:
            code = "run_not_found" if path.startswith("/api/runs/") else "workflow_not_found"
        elif response.status_code == 409:
            code = "idempotency_key_reused"
        try:
            # ``start_stream`` hands us a streaming response.  httpx does not
            # make ``response.json()`` available until the body has been
            # consumed, so read the error body before decoding it.  Without
            # this, a useful 401/403/404 from Anomalo is replaced by the
            # misleading ``ResponseNotRead`` exception.
            await response.aread()
            body = response.json()
        except ValueError:
            body = {}
        remote_code = body.get("error_code") if isinstance(body, dict) else None
        if remote_code in {"invalid_workflow_run_request", "unauthorized", "workflow_ref_forbidden", "workflow_not_found", "run_not_found", "idempotency_key_reused", "forbidden"}:
            code = "workflow_ref_forbidden" if remote_code == "forbidden" else str(remote_code)
        message = "Anomalo Workflow 请求失败。"
        if isinstance(body, dict):
            message = str(body.get("error") or body.get("message") or message)
        raise WorkflowAdapterError(code, _safe_message(message), retryable=code == "workflow_unavailable")

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()


class FakeWorkflowAdapter:
    """Deterministic offline adapter using the same lifecycle as HTTP."""

    def __init__(self, *, delay_seconds: float = 0.0) -> None:
        self.delay_seconds = delay_seconds
        self.runs: dict[str, dict[str, Any]] = {}

    async def start(
        self,
        *,
        workflow_ref: str,
        input_payload: dict[str, Any],
        idempotency_key: str,
        metadata: dict[str, Any],
        compiled_hash: str,
    ) -> WorkflowStartResult:
        existing = next((item for item in self.runs.values() if item.get("idempotency_key") == idempotency_key), None)
        if existing is not None:
            return WorkflowStartResult(
                run_id=str(existing["run_id"]), status=str(existing["status"]), target_ref=workflow_ref,
                target_hash=compiled_hash, runtime_kind="workflow", output=existing.get("output"), events=list(existing.get("events") or []),
            )
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        run_id = f"fake_{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        intent_payload = input_payload.get("intent") if isinstance(input_payload.get("intent"), dict) else {}
        intent = str(intent_payload.get("type") or input_payload.get("intent_type") or "")
        artifact = _fake_artifact(input_payload, intent, compiled_hash, now)
        events = [
            {"sequence": 1, "timestamp": now, "type": "run.queued", "data": {"status": "queued"}},
            {"sequence": 2, "timestamp": now, "type": "workflow.run.started", "data": {"workflow_ref": workflow_ref, "compiled_hash": compiled_hash}},
            {"sequence": 3, "timestamp": now, "type": "workflow.run.succeeded", "data": {"output": artifact}},
            {"sequence": 4, "timestamp": now, "type": "run.succeeded", "data": {"status": "succeeded"}},
        ]
        record = {"run_id": run_id, "idempotency_key": idempotency_key, "status": "succeeded", "runtime_kind": "workflow", "target_ref": workflow_ref, "target_hash": compiled_hash, "output": artifact, "events": events, "metadata": metadata}
        self.runs[run_id] = record
        return WorkflowStartResult(run_id=run_id, status="succeeded", target_ref=workflow_ref, target_hash=compiled_hash, runtime_kind="workflow", output=artifact, events=events)

    async def start_stream(
        self,
        *,
        workflow_ref: str,
        input_payload: dict[str, Any],
        idempotency_key: str,
        metadata: dict[str, Any],
        compiled_hash: str,
    ) -> AsyncIterator[dict[str, Any]]:
        receipt = await self.start(
            workflow_ref=workflow_ref,
            input_payload=input_payload,
            idempotency_key=idempotency_key,
            metadata=metadata,
            compiled_hash=compiled_hash,
        )
        for event in receipt.events or []:
            yield {
                "run_id": receipt.run_id,
                "runtime_kind": receipt.runtime_kind,
                "target_ref": receipt.target_ref,
                "target_hash": receipt.target_hash,
                **event,
            }

    async def get_run(self, run_id: str) -> dict[str, Any]:
        return dict(self.runs.get(run_id) or {})

    async def get_events(self, run_id: str, *, after_sequence: int = 0) -> list[dict[str, Any]]:
        return [event for event in (self.runs.get(run_id, {}).get("events") or []) if int(event.get("sequence", 0)) > after_sequence]

    async def stop(self, run_id: str, *, reason: str = "user_stop") -> dict[str, Any]:
        run = self.runs.get(run_id)
        if not run:
            return {}
        run["status"] = "stopped"
        sequence = max([int(event.get("sequence", 0)) for event in run.get("events", [])] or [0]) + 1
        run.setdefault("events", []).append({"sequence": sequence, "timestamp": datetime.now(timezone.utc).isoformat(), "type": "run.stopped", "data": {"status": "stopped", "reason": reason}})
        return dict(run)

    async def close(self) -> None:
        return None


def _split_ref(workflow_ref: str) -> tuple[str, str]:
    name, separator, version = workflow_ref.rpartition("@")
    if not separator or not name or not version.isdigit() or int(version) < 1:
        raise WorkflowAdapterError("invalid_workflow_ref", "Workflow Ref 必须是 name@integer-version。")
    return name, version


def _strip_hash(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text.removeprefix("sha256:")


def _safe_message(value: str) -> str:
    lowered = value.lower()
    if "bearer " in lowered or "token" in lowered or "authorization" in lowered:
        return "Anomalo Workflow 请求失败。"
    return value[:500]


def _fake_artifact(input_payload: dict[str, Any], intent: str, compiled_hash: str, now: str) -> dict[str, Any]:
    rows = list(input_payload.get("rows") or [])
    cards = []
    finding_type = {
        "indicator_attention": "extreme_value",
        "strategy_attention": "score_outlier",
    }.get(intent, "quality_anomaly")
    for rank, row in enumerate(rows[:3], start=1):
        card_id = row.get("id")
        if not card_id:
            continue
        card = {"rank": rank, "card_id": card_id, "group_id": row.get("group_id"), "symbol": row.get("symbol"), "finding_type": "quality_anomaly" if not row.get("valid", True) else finding_type, "severity": "low", "why_notable": "离线 Fake Adapter 结果，仅用于契约与展示测试。", "suggested_drilldown": "打开对应冻结证据。", "evidence_refs": list(row.get("evidence_refs") or [])}
        if intent == "strategy_attention" and row.get("decision_id"):
            card["strategy_decision_id"] = row["decision_id"]
        cards.append(card)
    return {
        "schema_version": "urus.remote_decision_artifact.v1",
        "intent_type": intent,
        "scope": dict(input_payload.get("scope") or {}),
        "dataset_id": input_payload.get("dataset_id") or (input_payload.get("dataset") or {}).get("dataset_id"),
        "input_sha256": input_payload.get("input_sha256") or "0" * 64,
        "completeness": "complete" if rows else "insufficient_evidence",
        "decision": {"stance": "neutral", "action": "watch", "summary": "Fake Adapter result"},
        "summary": "离线 Fake Adapter 结果，仅用于契约与展示测试。",
        "confidence": 0.5,
        "notable_cards": cards,
        "coverage_gaps": [],
        "warnings": ["FAKE ADAPTER：未访问 Anomalo 网络。"],
        "evidence_refs": list(input_payload.get("evidence_refs") or []),
        "generated_at": now,
    }
