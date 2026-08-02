from __future__ import annotations

from collections.abc import Iterable

from app.workflows.base import WorkflowStep
from app.workflows.context import RunContext

DEFAULT_STEP_CODES = ("1a", "1b", "2", "3a", "3b", "4", "5")


class WorkflowPipeline:
    def __init__(self, steps: Iterable[WorkflowStep]) -> None:
        self.steps = list(steps)

    def execute(self, context: RunContext) -> None:
        for step in self.steps:
            result = step.execute(context)
            context.results[step.code] = result

