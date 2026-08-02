from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from app.models import StepStatus

if TYPE_CHECKING:
    from app.workflows.context import RunContext


@dataclass
class StepResult:
    status: StepStatus
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None
    data_state: str | None = None


class WorkflowStep(Protocol):
    code: str
    label: str

    def execute(self, context: "RunContext") -> StepResult: ...


def data_state_for(result: StepResult) -> str:
    """Resolve data availability separately from workflow execution status."""
    if result.data_state:
        return result.data_state
    payload_state = result.payload.get("data_state")
    if isinstance(payload_state, str) and payload_state:
        return payload_state
    if result.status == StepStatus.SKIPPED:
        return "skipped"
    if result.status == StepStatus.PLACEHOLDER:
        return "placeholder"
    if result.status == StepStatus.UNAVAILABLE:
        return "unavailable"
    if result.status == StepStatus.SUCCEEDED:
        return "live" if result.payload.get("is_mock") is False else "mock"
    return "unavailable"
