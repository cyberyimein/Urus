from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class DecisionRequest:
    session_id: str
    evidence: dict[str, object]


@dataclass(frozen=True)
class DecisionResponse:
    stance: str
    confidence: float | None
    summary: str
    is_mock: bool


class DecisionAdapter(Protocol):
    def decide(self, request: DecisionRequest) -> DecisionResponse: ...


class MockDecisionAdapter:
    """Fixed decision placeholder. It is not a decision-AI integration."""

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        return DecisionResponse(
            stance="observe",
            confidence=None,
            summary=(
                "模拟决策：证据包尚未接入真实数据，保持观察，不产生交易指令。"
            ),
            is_mock=True,
        )

