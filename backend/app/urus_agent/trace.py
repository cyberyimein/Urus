from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import uuid4

from app.core.time import utc_now


TraceNodeType = str


@dataclass
class TraceNodeRecord:
    """A provider-neutral, observable execution node.

    Raw provider responses are kept separately as model turns; a node only
    carries bounded summaries so the graph endpoint remains cheap to load.
    """

    id: str
    node_type: TraceNodeType
    label: str
    decision_run_id: str | None = None
    status: str = "running"
    lane: str = "Preparation"
    parent_node_id: str | None = None
    depends_on_node_ids: list[str] = field(default_factory=list)
    sequence: int = 0
    input_summary: dict[str, Any] = field(default_factory=dict)
    output_summary: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime = field(default_factory=utc_now)
    completed_at: datetime | None = None


class TraceSink(Protocol):
    def start_node(
        self,
        *,
        node_type: TraceNodeType,
        label: str,
        lane: str,
        parent_node_id: str | None = None,
        depends_on_node_ids: list[str] | None = None,
        input_summary: dict[str, Any] | None = None,
        sequence: int | None = None,
        decision_run_id: str | None = None,
    ) -> str: ...

    def finish_node(
        self,
        node_id: str,
        *,
        output_summary: dict[str, Any] | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None: ...

    def fail_node(
        self,
        node_id: str,
        *,
        error_code: str,
        error_message: str,
        output_summary: dict[str, Any] | None = None,
    ) -> None: ...


class InMemoryTraceSink:
    """Deterministic trace adapter for Runtime tests and orchestration."""

    def __init__(self) -> None:
        self.nodes: list[TraceNodeRecord] = []
        self._sequence = 0

    def start_node(
        self,
        *,
        node_type: TraceNodeType,
        label: str,
        lane: str,
        parent_node_id: str | None = None,
        depends_on_node_ids: list[str] | None = None,
        input_summary: dict[str, Any] | None = None,
        sequence: int | None = None,
        decision_run_id: str | None = None,
    ) -> str:
        self._sequence += 1
        node = TraceNodeRecord(
            id=str(uuid4()),
            decision_run_id=decision_run_id,
            node_type=node_type,
            label=label,
            lane=lane,
            parent_node_id=parent_node_id,
            depends_on_node_ids=list(depends_on_node_ids or []),
            sequence=sequence if sequence is not None else self._sequence,
            input_summary=dict(input_summary or {}),
        )
        self.nodes.append(node)
        return node.id

    def _get(self, node_id: str) -> TraceNodeRecord:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise KeyError(node_id)

    def finish_node(
        self,
        node_id: str,
        *,
        output_summary: dict[str, Any] | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        node = self._get(node_id)
        node.status = "succeeded"
        node.output_summary = dict(output_summary or {})
        node.evidence_refs = list(evidence_refs or [])
        node.metrics = dict(metrics or {})
        node.completed_at = utc_now()

    def fail_node(
        self,
        node_id: str,
        *,
        error_code: str,
        error_message: str,
        output_summary: dict[str, Any] | None = None,
    ) -> None:
        node = self._get(node_id)
        node.status = "failed"
        node.error_code = error_code
        node.error_message = error_message
        node.output_summary = dict(output_summary or {})
        node.completed_at = utc_now()

    def extend(self, nodes: list[TraceNodeRecord]) -> None:
        """Merge isolated worker traces into this sink with stable sequencing."""

        for node in nodes:
            self._sequence += 1
            node.sequence = self._sequence
            self.nodes.append(node)
