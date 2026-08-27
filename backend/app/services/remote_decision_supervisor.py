from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from app.core.config import Settings
from app.decision_harness.remote_workflow import validate_artifact
from app.integrations.anomalo_workflow import (
    AnomaloWorkflowAdapter,
    WorkflowAdapterError,
    WorkflowStartResult,
)
from app.models.remote_decision import RemoteDecisionRunModel
from app.repositories.remote_decision import (
    ACTIVE_STATUSES,
    RECOVERABLE_STATUSES,
    RemoteDecisionRepository,
)

logger = logging.getLogger(__name__)


class RemoteDecisionSupervisor:
    """Bounded local queue that drives the Anomalo Workflow lifecycle."""

    def __init__(
        self,
        session_factory,
        settings: Settings,
        adapter: AnomaloWorkflowAdapter,
        *,
        queue_size: int = 64,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.adapter = adapter
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=max(1, queue_size))
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._queued_ids: set[str] = set()
        self._stop_sent_remote_ids: set[str] = set()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._worker(), name="urus-remote-decision-supervisor")
        await self.recover()

    async def recover(self) -> int:
        with self.session_factory() as session:
            ids = [item.id for item in RemoteDecisionRepository(session).recoverable_runs()]
        queued = 0
        for local_run_id in ids:
            if await self.enqueue(local_run_id):
                queued += 1
        return queued

    async def enqueue(self, local_run_id: str) -> bool:
        if local_run_id in self._queued_ids:
            return False
        try:
            self.queue.put_nowait(local_run_id)
        except asyncio.QueueFull:
            with self.session_factory() as session:
                model = RemoteDecisionRepository(session).get_run(local_run_id)
                if model is not None and model.status in ACTIVE_STATUSES:
                    RemoteDecisionRepository(session).transition(
                        model,
                        "failed",
                        error_code="local_queue_full",
                        safe_error_message="本地 Workflow 队列已满，请稍后重试。",
                    )
            return False
        self._queued_ids.add(local_run_id)
        return True

    async def stop_run(self, local_run_id: str) -> RemoteDecisionRunModel | None:
        stopped_without_remote = False
        remote_id: str | None = None
        with self.session_factory() as session:
            repository = RemoteDecisionRepository(session)
            model = repository.get_run(local_run_id)
            if model is None:
                return None
            if model.status == "queued" and not model.anomalo_run_id:
                repository.transition(model, "stopped", remote_status="stopped")
                stopped_without_remote = True
            if model.status in {"queued", "submitting", "running"}:
                repository.transition(model, "stopping")
                remote_id = model.anomalo_run_id
            elif not stopped_without_remote:
                return model
        if stopped_without_remote:
            # Return a freshly loaded instance; callers may use a sessionmaker
            # with expire_on_commit=True, so the committed object above must
            # not escape its closed session with expired attributes.
            with self.session_factory() as session:
                return RemoteDecisionRepository(session).get_run(local_run_id)
        if remote_id:
            already_sent = remote_id in self._stop_sent_remote_ids
            if not already_sent:
                self._stop_sent_remote_ids.add(remote_id)
            try:
                if not already_sent:
                    await self.adapter.stop(remote_id, reason="user_stop")
            except WorkflowAdapterError as exc:
                self._stop_sent_remote_ids.discard(remote_id)
                with self.session_factory() as session:
                    model = RemoteDecisionRepository(session).get_run(local_run_id)
                    if model is not None:
                        RemoteDecisionRepository(session).transition(
                            model,
                            "failed",
                            error_code=exc.code,
                            safe_error_message=exc.message,
                        )
        await self.enqueue(local_run_id)
        with self.session_factory() as session:
            return RemoteDecisionRepository(session).get_run(local_run_id)

    async def shutdown(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()
            self._task = None
        await self.adapter.close()

    async def _worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                local_run_id = await asyncio.wait_for(self.queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            self._queued_ids.discard(local_run_id)
            try:
                await self._process(local_run_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("remote decision worker failed local_run_id=%s", local_run_id)
                self._mark_failed(local_run_id, "worker_error", "AI Workflow Worker 发生未预期错误。")
            finally:
                self.queue.task_done()

    async def _process(self, local_run_id: str) -> None:
        with self.session_factory() as session:
            model = RemoteDecisionRepository(session).get_run(local_run_id)
            if model is None or model.status not in RECOVERABLE_STATUSES:
                return
            remote_id = model.anomalo_run_id
            input_payload = dict(model.input_json)
            workflow_ref = model.workflow_ref
            idempotency_key = model.idempotency_key
            metadata = dict(model.metadata_json)
            compiled_hash = model.compiled_hash
            latest_sequence = int(model.latest_event_sequence or 0)
        if not remote_id:
            with self.session_factory() as session:
                model = RemoteDecisionRepository(session).get_run(local_run_id)
                if model is None or model.status not in RECOVERABLE_STATUSES:
                    return
                # A stop request can race with the worker before the remote
                # run receives an ID. Never submit a run that the user has
                # already asked us to stop.
                if model.status == "stopping":
                    RemoteDecisionRepository(session).transition(model, "stopped", remote_status="stopped")
                    return
                if model.status == "queued":
                    RemoteDecisionRepository(session).transition(model, "submitting")
            try:
                receipt = await self._start_workflow(
                    workflow_ref=workflow_ref,
                    input_payload=input_payload,
                    idempotency_key=idempotency_key,
                    metadata=metadata,
                    compiled_hash=compiled_hash,
                    local_run_id=local_run_id,
                )
            except WorkflowAdapterError as exc:
                # A streaming start can fail after Anomalo has already issued
                # a Run ID. In that case the Run is still recoverable; switch
                # to the normal event/status poll instead of manufacturing a
                # local failure and losing the remote execution.
                with self.session_factory() as session:
                    model = RemoteDecisionRepository(session).get_run(local_run_id)
                    known_remote_id = model.anomalo_run_id if model is not None else None
                    latest_sequence = int(model.latest_event_sequence or 0) if model is not None else latest_sequence
                if known_remote_id:
                    await self._poll(local_run_id, known_remote_id, latest_sequence)
                    return
                self._mark_failed(local_run_id, exc.code, exc.message)
                return
            with self.session_factory() as session:
                model = RemoteDecisionRepository(session).get_run(local_run_id)
                if model is None:
                    return
                repository = RemoteDecisionRepository(session)
                identity_error = _remote_identity_error(
                    receipt.target_ref,
                    receipt.target_hash,
                    model.workflow_ref,
                    model.compiled_hash,
                    runtime_kind=receipt.runtime_kind,
                )
                if identity_error is not None:
                    repository.transition(model, "failed", error_code=identity_error[0], safe_error_message=identity_error[1])
                    return
                repository.update_remote(model, anomalo_run_id=receipt.run_id, remote_status=receipt.status)
                if receipt.events:
                    self._persist_events(repository, model, receipt.events)
                # The object is detached after commit, but all fields needed by
                # the following step are copied before leaving this context.
                latest_sequence = int(model.latest_event_sequence or 0)
            if receipt.status in {"succeeded", "failed", "stopped"}:
                await self._handle_terminal(
                    local_run_id,
                    receipt.status,
                    receipt.output or _output_from_events(receipt.events or []),
                    receipt.events or [],
                )
                if receipt.status in {"succeeded", "failed", "stopped"}:
                    return
            remote_id = receipt.run_id

        await self._poll(local_run_id, remote_id, latest_sequence)

    async def _start_workflow(
        self,
        *,
        workflow_ref: str,
        input_payload: dict[str, Any],
        idempotency_key: str,
        metadata: dict[str, Any],
        compiled_hash: str,
        local_run_id: str,
    ) -> WorkflowStartResult:
        """Start a Workflow while making its remote ID visible immediately.

        Anomalo's regular ``runs`` endpoint is a terminal-result request. The
        stream endpoint is therefore required for Urus: it yields the Run ID
        and lifecycle events before execution completes, allowing a concurrent
        stop request to reach Anomalo instead of waiting behind a long HTTP
        request.
        """

        stream_start = getattr(self.adapter, "start_stream", None)
        if not callable(stream_start):
            return await self.adapter.start(
                workflow_ref=workflow_ref,
                input_payload=input_payload,
                idempotency_key=idempotency_key,
                metadata=metadata,
                compiled_hash=compiled_hash,
            )

        events: list[dict[str, Any]] = []
        remote_id: str | None = None
        stop_sent = False
        async for raw_event in stream_start(
            workflow_ref=workflow_ref,
            input_payload=input_payload,
            idempotency_key=idempotency_key,
            metadata=metadata,
            compiled_hash=compiled_hash,
        ):
            if not isinstance(raw_event, dict):
                continue
            events.append(raw_event)
            remote_id = remote_id or _event_run_id(raw_event)
            if remote_id:
                with self.session_factory() as session:
                    model = RemoteDecisionRepository(session).get_run(local_run_id)
                    if model is not None:
                        repository = RemoteDecisionRepository(session)
                        event_status = _event_status(raw_event)
                        if model.anomalo_run_id is None:
                            repository.update_remote(
                                model,
                                anomalo_run_id=remote_id,
                                remote_status=event_status,
                            )
                        elif event_status:
                            repository.update_remote(model, remote_status=event_status)
                        if event_status == "running" and model.status in {"queued", "submitting"}:
                            repository.transition(model, "running", remote_status=event_status)
                        elif event_status == "stopping" and model.status in {"submitting", "running"}:
                            repository.transition(model, "stopping", remote_status=event_status)
                        # The first event normally carries the ID; replaying
                        # the accumulated list also covers a server that sends
                        # one ID-less preamble before the first lifecycle event.
                        self._persist_events(repository, model, events)
                        stop_requested = model.status == "stopping"
                    else:
                        stop_requested = False
                if stop_requested and not stop_sent and remote_id not in self._stop_sent_remote_ids:
                    self._stop_sent_remote_ids.add(remote_id)
                    try:
                        await self.adapter.stop(remote_id, reason="user_stop")
                    except WorkflowAdapterError:
                        self._stop_sent_remote_ids.discard(remote_id)
                        raise
                    stop_sent = True

        if not remote_id:
            raise WorkflowAdapterError("invalid_workflow_response", "Anomalo 返回缺少 run_id。")

        run = await self.adapter.get_run(remote_id)
        first_event = events[0] if events else {}
        first_data = first_event.get("data") if isinstance(first_event.get("data"), dict) else {}
        target_ref = str(run.get("target_ref") or first_event.get("target_ref") or workflow_ref)
        target_hash = run.get("target_hash") or first_event.get("target_hash") or first_data.get("compiled_hash") or compiled_hash
        runtime_kind = run.get("runtime_kind") or first_event.get("runtime_kind") or "workflow"
        status = str(run.get("status") or _event_status(events[-1]) or "queued")
        output = run.get("output") if isinstance(run.get("output"), dict) else _output_from_events(events)
        usage = run.get("usage") if isinstance(run.get("usage"), dict) else None
        return WorkflowStartResult(
            run_id=remote_id,
            status=status,
            target_ref=target_ref,
            target_hash=str(target_hash) if target_hash is not None else None,
            runtime_kind=str(runtime_kind) if runtime_kind is not None else None,
            output=output,
            usage=usage,
            events=events,
        )

    async def _poll(self, local_run_id: str, remote_id: str, latest_sequence: int) -> None:
        max_polls = max(1, int(getattr(self.settings, "remote_decision_max_polls", 4800)))
        for _ in range(max_polls):
            with self.session_factory() as session:
                model = RemoteDecisionRepository(session).get_run(local_run_id)
                if model is None or model.status in {"stopped", "failed", "accepted", "rejected_result"}:
                    return
                latest_sequence = max(latest_sequence, int(model.latest_event_sequence or 0))
            try:
                events = await self.adapter.get_events(remote_id, after_sequence=latest_sequence)
                run = await self.adapter.get_run(remote_id)
            except WorkflowAdapterError as exc:
                if exc.retryable and _ < 2:
                    await asyncio.sleep(0.1)
                    continue
                self._mark_failed(local_run_id, exc.code, exc.message)
                return
            if events:
                with self.session_factory() as session:
                    model = RemoteDecisionRepository(session).get_run(local_run_id)
                    if model is not None:
                        self._persist_events(RemoteDecisionRepository(session), model, events)
                        latest_sequence = int(model.latest_event_sequence or latest_sequence)
            status = str(run.get("status") or "")
            identity_error = None
            stop_requested = False
            with self.session_factory() as session:
                model = RemoteDecisionRepository(session).get_run(local_run_id)
                if model is not None:
                    repository = RemoteDecisionRepository(session)
                    identity_error = _remote_identity_error(
                        run.get("target_ref"),
                        run.get("target_hash"),
                        model.workflow_ref,
                        model.compiled_hash,
                        runtime_kind=run.get("runtime_kind"),
                    )
                    if identity_error is not None:
                        repository.transition(model, "failed", error_code=identity_error[0], safe_error_message=identity_error[1])
                        return
                    stop_requested = model.status == "stopping" and status not in {"succeeded", "failed", "stopped"}
                    # The start receipt is allowed to be queued. Promote the
                    # local state as soon as the remote Run reports running so
                    # the UI and recovery logic expose the real lifecycle.
                    if status == "running" and model.status in {"queued", "submitting"}:
                        repository.transition(model, "running", remote_status=status)
                    elif status == "stopping" and model.status in {"submitting", "running"}:
                        repository.transition(model, "stopping", remote_status=status)
                    elif status:
                        repository.update_remote(model, remote_status=status)
            if stop_requested and remote_id not in self._stop_sent_remote_ids:
                self._stop_sent_remote_ids.add(remote_id)
                try:
                    await self.adapter.stop(remote_id, reason="user_stop")
                except WorkflowAdapterError:
                    # Keep the local state stopping and retry on the next
                    # poll. A transient stop failure must not strand a Run
                    # until the full workflow timeout.
                    self._stop_sent_remote_ids.discard(remote_id)
            output = run.get("output") if isinstance(run.get("output"), dict) else _output_from_events(events)
            if output is None:
                # A restarted supervisor may already have consumed the output
                # event before this poll's ``after_sequence`` cursor. Reuse
                # the durable, redacted event journal before rejecting a
                # perfectly valid succeeded Run as output-missing.
                with self.session_factory() as session:
                    stored_events = RemoteDecisionRepository(session).list_events(local_run_id, after_sequence=0)
                    output = _output_from_stored_events(stored_events)
            if status in {"succeeded", "failed", "stopped"}:
                await self._handle_terminal(local_run_id, status, output, events)
                return
            await asyncio.sleep(0.25)
        self._mark_failed(local_run_id, "workflow_timeout", "Anomalo Workflow 在受控等待窗口内未完成。")

    async def _handle_terminal(
        self,
        local_run_id: str,
        remote_status: str,
        output: dict[str, Any] | None,
        events: Iterable[dict[str, Any]],
    ) -> None:
        with self.session_factory() as session:
            repository = RemoteDecisionRepository(session)
            model = repository.get_run(local_run_id)
            if model is None:
                return
            if model.anomalo_run_id:
                self._stop_sent_remote_ids.discard(model.anomalo_run_id)
            if remote_status == "failed":
                repository.transition(model, "failed", remote_status=remote_status, error_code="workflow_runtime_error", safe_error_message="Anomalo Workflow 执行失败。")
                return
            if remote_status == "stopped":
                if model.status != "stopped":
                    repository.transition(model, "stopped", remote_status=remote_status)
                return
            if model.status == "queued":
                repository.transition(model, "submitting", remote_status="running")
                model = repository.get_run(local_run_id)
            if model is not None and model.status == "submitting":
                repository.transition(model, "running", remote_status="running")
                model = repository.get_run(local_run_id)
            if model is None:
                return
            if model.status in {"running", "stopping"}:
                repository.transition(model, "succeeded", remote_status="succeeded")
                model = repository.get_run(local_run_id)
            if model is None:
                return
            existing_artifact = repository.get_artifact(local_run_id)
            if existing_artifact is not None and model.status == "succeeded":
                final_status = "accepted" if existing_artifact.validation_status == "accepted" else "rejected_result"
                repository.transition(model, final_status, remote_status="succeeded")
                return
            if output is None:
                repository.transition(model, "rejected_result", remote_status="succeeded", error_code="output_missing", safe_error_message="Workflow 成功但未返回 Artifact。")
                return
            artifact, issue = validate_artifact(model, output)
            if artifact is None:
                repository.save_artifact(model, artifact=output, artifact_sha256=_hash_payload(output), validation_status="rejected")
                model = repository.get_run(local_run_id)
                if model is not None:
                    repository.transition(model, "rejected_result", remote_status="succeeded", error_code=issue.code if issue else "output_schema_invalid", safe_error_message=issue.message if issue else "Artifact 校验失败。")
                return
            repository.save_artifact(model, artifact=artifact.model_dump(mode="json"), artifact_sha256=_hash_payload(artifact.model_dump(mode="json")), validation_status="accepted")
            model = repository.get_run(local_run_id)
            if model is not None and model.status == "succeeded":
                repository.transition(model, "accepted", remote_status="succeeded")

    def _persist_events(self, repository: RemoteDecisionRepository, model: RemoteDecisionRunModel, events: Iterable[dict[str, Any]]) -> None:
        for raw in sorted((item for item in events if isinstance(item, dict)), key=lambda item: int(item.get("sequence", 0))):
            sequence = int(raw.get("sequence") or 0)
            if sequence <= 0:
                continue
            timestamp = raw.get("timestamp")
            event_timestamp = None
            if timestamp:
                try:
                    event_timestamp = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                except ValueError:
                    event_timestamp = None
            data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
            repository.add_event(
                model,
                {
                    "sequence": sequence,
                    "event_type": raw.get("type") or "unknown",
                    "event_timestamp": event_timestamp,
                    "node_id": data.get("node_id") or raw.get("node_id"),
                    "attempt": data.get("attempt") or raw.get("attempt"),
                    "child_run_id": data.get("child_run_id") or raw.get("child_run_id"),
                    "data": data,
                },
            )

    def _mark_failed(self, local_run_id: str, code: str, message: str) -> None:
        with self.session_factory() as session:
            model = RemoteDecisionRepository(session).get_run(local_run_id)
            if model is None or model.status in {"accepted", "rejected_result", "failed", "stopped"}:
                return
            try:
                RemoteDecisionRepository(session).transition(model, "failed", error_code=code, safe_error_message=message)
            except ValueError:
                logger.exception("could not mark remote decision failed local_run_id=%s", local_run_id)


def _output_from_events(events: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    output = None
    for event in events:
        data = event.get("data") if isinstance(event, dict) else None
        if isinstance(data, dict) and isinstance(data.get("output"), dict):
            output = data["output"]
    return output


def _output_from_stored_events(events: Iterable[Any]) -> dict[str, Any] | None:
    output = None
    for event in events:
        data = getattr(event, "safe_data_json", None)
        if isinstance(data, dict) and isinstance(data.get("output"), dict):
            output = data["output"]
    return output


def _event_run_id(event: dict[str, Any]) -> str | None:
    value = event.get("run_id")
    if value is None and isinstance(event.get("data"), dict):
        value = event["data"].get("run_id")
    return str(value) if value else None


def _event_status(event: dict[str, Any]) -> str | None:
    value = event.get("status")
    data = event.get("data")
    if value is None and isinstance(data, dict):
        value = data.get("status")
    if value is None:
        event_type = str(event.get("type") or "")
        if event_type.endswith(".queued") or event_type == "run.queued":
            value = "queued"
        elif event_type.endswith(".started") or event_type == "run.started":
            value = "running"
        elif event_type.endswith(".succeeded") or event_type == "run.succeeded":
            value = "succeeded"
        elif event_type.endswith(".failed") or event_type == "run.failed":
            value = "failed"
        elif event_type.endswith(".stopped") or event_type == "run.stopped":
            value = "stopped"
    return str(value) if value else None


def _hash_payload(value: dict[str, Any]) -> str:
    import hashlib
    import json

    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _remote_identity_error(
    target_ref: Any,
    target_hash: Any,
    workflow_ref: str,
    compiled_hash: str,
    *,
    runtime_kind: Any = None,
) -> tuple[str, str] | None:
    if runtime_kind != "workflow":
        return "runtime_kind_mismatch", "Anomalo 返回的运行类型不是 Workflow。"
    if workflow_ref and not target_ref:
        return "workflow_ref_mismatch", "Anomalo 返回缺少 Workflow Ref。"
    if workflow_ref and str(target_ref) != workflow_ref:
        return "workflow_ref_mismatch", "Anomalo 返回的 Workflow Ref 与绑定不一致。"
    if compiled_hash and not target_hash:
        return "compiled_hash_mismatch", "Anomalo 返回缺少 compiled hash。"
    if compiled_hash and target_hash:
        observed = str(target_hash).removeprefix("sha256:")
        expected = str(compiled_hash).removeprefix("sha256:")
        if observed != expected:
            return "compiled_hash_mismatch", "Anomalo 返回的 compiled hash 与绑定不一致。"
    return None
