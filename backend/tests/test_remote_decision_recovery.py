from __future__ import annotations

import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import Base
from app.decision_harness.contracts import content_sha256
from app.integrations.anomalo_workflow import FakeWorkflowAdapter
from app.repositories.remote_decision import RemoteDecisionRepository
from app.services.remote_decision_supervisor import RemoteDecisionSupervisor


def _settings() -> Settings:
    return Settings(
        app_env="test",
        anomalo_workflow_enabled=True,
        anomalo_workflow_fake_adapter=True,
        remote_decision_max_polls=3,
    )


def _input_payload() -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "urus.remote_decision_input.v1",
        "intent": {
            "type": "instrument_arbitration",
            "trigger_mode": "user",
            "trigger_source": "instrument_page",
        },
        "scope": {"scope_type": "instrument", "scope_id": "INTC", "symbol": "INTC"},
        "dataset": {
            "dataset_id": "dataset-1",
            "schema_version": "urus.daily_decision_dataset.v1",
            "content_sha256": "d" * 64,
            "trading_date": "2026-08-25",
            "cutoff_time": "2026-08-25T20:00:00+00:00",
        },
        "evidence": {},
        "strategy_decisions": [],
        "deterministic_synthesis": {},
        "quality": {"status": "ok"},
        "constraints": {
            "allowed_symbols": ["INTC"],
            "allow_latest_data_lookup": False,
            "allow_symbol_expansion": False,
        },
        "rows": [],
        "evidence_refs": [],
    }
    payload["input_sha256"] = content_sha256(payload)
    return payload


def _create_run(session: Session, *, status: str = "queued", remote_id: str | None = None):
    payload = _input_payload()
    model = RemoteDecisionRepository(session).create_run(
        {
            "intent_type": "instrument_arbitration",
            "request_intent_id": f"intent-{status}-{remote_id or 'new'}",
            "idempotency_key": f"key-{status}-{remote_id or 'new'}",
            "scope_type": "instrument",
            "scope_id": "INTC",
            "dataset_id": "dataset-1",
            "source_locator_json": {"dataset_id": "dataset-1", "symbol": "INTC"},
            "workflow_ref": "urus-instrument-arbitration@3",
            "definition_hash": "1" * 64,
            "compiled_hash": "2" * 64,
            "input_schema_version": "urus.remote_decision_input.v1",
            "input_sha256": payload["input_sha256"],
            "input_json": payload,
            "metadata_json": {"source": "test"},
            "trigger_source": "instrument_page",
            "preflight_fingerprint": "3" * 64,
            "anomalo_run_id": remote_id,
        }
    )
    if status != "queued":
        model.status = status
        session.commit()
    return model


def test_supervisor_recovers_run_after_remote_id(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'recovery.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    adapter = FakeWorkflowAdapter()
    session_factory = lambda: Session(engine)
    payload = _input_payload()
    receipt = asyncio.run(
        adapter.start(
            workflow_ref="urus-instrument-arbitration@3",
            input_payload=payload,
            idempotency_key="remote-key",
            metadata={"source": "test"},
            compiled_hash="2" * 64,
        )
    )
    with session_factory() as session:
        model = _create_run(session, status="running", remote_id=receipt.run_id)
        local_run_id = model.id

    async def exercise() -> None:
        supervisor = RemoteDecisionSupervisor(session_factory, _settings(), adapter)
        await supervisor.start()
        await asyncio.wait_for(supervisor.queue.join(), timeout=2)
        await supervisor.shutdown()

    asyncio.run(exercise())
    with session_factory() as session:
        recovered = RemoteDecisionRepository(session).get_run(local_run_id)
        assert recovered is not None
        assert recovered.status == "accepted"
        assert recovered.latest_event_sequence == 4
    engine.dispose()


def test_supervisor_recovers_succeeded_run_before_artifact_finalisation(tmp_path) -> None:
    """A crash after remote success must not leave the UI polling forever."""

    engine = create_engine(f"sqlite:///{tmp_path / 'succeeded-recovery.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    adapter = FakeWorkflowAdapter()
    session_factory = lambda: Session(engine)
    payload = _input_payload()
    receipt = asyncio.run(
        adapter.start(
            workflow_ref="urus-instrument-arbitration@3",
            input_payload=payload,
            idempotency_key="succeeded-recovery-key",
            metadata={"source": "test"},
            compiled_hash="2" * 64,
        )
    )
    with session_factory() as session:
        model = _create_run(session, status="succeeded", remote_id=receipt.run_id)
        local_run_id = model.id

    async def exercise() -> None:
        supervisor = RemoteDecisionSupervisor(session_factory, _settings(), adapter)
        await supervisor.start()
        await asyncio.wait_for(supervisor.queue.join(), timeout=2)
        await supervisor.shutdown()

    asyncio.run(exercise())
    with session_factory() as session:
        recovered = RemoteDecisionRepository(session).get_run(local_run_id)
        assert recovered is not None
        assert recovered.status == "accepted"
        assert RemoteDecisionRepository(session).get_artifact(local_run_id) is not None
    engine.dispose()


class _BlockingStreamingAdapter(FakeWorkflowAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.stop_calls: list[str] = []

    async def start_stream(self, *, workflow_ref, input_payload, idempotency_key, metadata, compiled_hash):
        run_id = "stream-run-1"
        self.runs[run_id] = {
            "run_id": run_id,
            "idempotency_key": idempotency_key,
            "status": "running",
            "runtime_kind": "workflow",
            "target_ref": workflow_ref,
            "target_hash": compiled_hash,
            "output": None,
            "events": [],
        }
        event = {
            "run_id": run_id,
            "runtime_kind": "workflow",
            "target_ref": workflow_ref,
            "target_hash": compiled_hash,
            "sequence": 1,
            "type": "run.started",
            "data": {"status": "running"},
        }
        self.runs[run_id]["events"].append(event)
        self.started.set()
        yield event
        await self.release.wait()
        if self.runs[run_id]["status"] == "stopped":
            stopped = {
                "run_id": run_id,
                "runtime_kind": "workflow",
                "target_ref": workflow_ref,
                "target_hash": compiled_hash,
                "sequence": 2,
                "type": "run.stopped",
                "data": {"status": "stopped"},
            }
            self.runs[run_id]["events"].append(stopped)
            yield stopped

    async def stop(self, run_id: str, *, reason: str = "user_stop"):
        self.stop_calls.append(run_id)
        result = await super().stop(run_id, reason=reason)
        self.release.set()
        return result


def test_stop_reaches_remote_run_while_streaming_start_is_open(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'stream-stop.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    adapter = _BlockingStreamingAdapter()
    session_factory = lambda: Session(engine)
    with session_factory() as session:
        model = _create_run(session)
        local_run_id = model.id

    async def exercise() -> None:
        supervisor = RemoteDecisionSupervisor(session_factory, _settings(), adapter)
        await supervisor.start()
        await asyncio.wait_for(adapter.started.wait(), timeout=2)
        await supervisor.stop_run(local_run_id)
        await asyncio.wait_for(supervisor.queue.join(), timeout=2)
        await supervisor.shutdown()

    asyncio.run(exercise())
    with session_factory() as session:
        stopped = RemoteDecisionRepository(session).get_run(local_run_id)
        assert stopped is not None
        assert stopped.status == "stopped"
        assert adapter.stop_calls == ["stream-run-1"]
    engine.dispose()


def test_stop_queued_run_without_remote_id_never_submits(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'stop.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = lambda: Session(engine)
    adapter = FakeWorkflowAdapter()
    with session_factory() as session:
        model = _create_run(session)
        local_run_id = model.id

    async def exercise():
        supervisor = RemoteDecisionSupervisor(session_factory, _settings(), adapter)
        stopped = await supervisor.stop_run(local_run_id)
        await supervisor.shutdown()
        return stopped

    stopped = asyncio.run(exercise())
    assert stopped is not None
    assert stopped.status == "stopped"
    assert adapter.runs == {}
    engine.dispose()


def test_duplicate_remote_event_is_idempotent(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'events.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        model = _create_run(session)
        repository = RemoteDecisionRepository(session)
        event_payload = {"sequence": 1, "event_type": "run.queued", "data": {"status": "queued"}}
        first = repository.add_event(model, event_payload)
        second = repository.add_event(model, event_payload)
        assert first.id == second.id
        assert model.latest_event_sequence == 1
        assert len(repository.list_events(model.id)) == 1
    engine.dispose()
