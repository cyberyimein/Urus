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


class WorkflowStep(Protocol):
    code: str
    label: str

    def execute(self, context: "RunContext") -> StepResult: ...
