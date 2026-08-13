from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.urus_agent.contracts import AgentTask, AgentToolResult, ToolSpec
from app.urus_agent.evidence import EvidenceStore


@dataclass(frozen=True)
class ToolContext:
    task: AgentTask
    evidence: EvidenceStore


@dataclass(frozen=True)
class RegisteredTool:
    spec: ToolSpec
    handler: Callable[[ToolContext, dict[str, Any]], dict[str, Any]]
    skills: frozenset[str]


def successful_result(
    name: str,
    result: dict[str, Any],
    *,
    evidence: dict[str, Any] | None = None,
    quality: dict[str, Any] | None = None,
) -> AgentToolResult:
    return AgentToolResult(
        ok=True,
        tool=name,
        data=result,
        evidence=evidence,
        quality=quality or {},
    )
