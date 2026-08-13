from __future__ import annotations

from dataclasses import dataclass
import json
from datetime import datetime
from typing import Any, Callable, Protocol

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.urus_agent.coordinator import CoordinatorRequest, DecisionCoordinator
from app.urus_agent.providers.openrouter import LLMProvider


@dataclass(frozen=True)
class DecisionRequest:
    session_id: str
    evidence: dict[str, object]
    task_type: str = "equity_ranking"
    symbols: list[str] | None = None
    target_symbol: str | None = None
    cutoff_time: datetime | None = None
    requested_skill: str = "urus-equity-decision"
    dataset_key: str | None = None
    workflow_run_id: str | None = None
    source_snapshot_ids: list[str] | None = None
    source_run_ids: list[str] | None = None
    decision_packet: dict[str, Any] | None = None
    decision_phase: str = "pre_close"
    trading_date: str = ""
    parent_session_id: str | None = None
    analysis_metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class DecisionResponse:
    stance: str
    confidence: float | None
    summary: str
    is_mock: bool
    result: Any = None
    session_id: str | None = None
    technical_report: dict[str, Any] | None = None
    decision_report: dict[str, Any] | None = None


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


class UrusDecisionAdapter:
    """OpenRouter-backed adapter for the non-chat Urus Agent runtime."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        provider: LLMProvider | None = None,
        provider_factory: Callable[[], LLMProvider] | None = None,
    ) -> None:
        if provider is None and provider_factory is None and not settings.openrouter_api_key:
            raise ValueError("OPENROUTER_API_KEY is required when URUS_AGENT_ENABLED=true")
        self.session = session
        self.settings = settings
        self.provider = provider
        self.provider_factory = provider_factory

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        cutoff = request.cutoff_time or datetime.now().astimezone()
        workflow_run_id = request.workflow_run_id or request.session_id.removeprefix("urus-").removesuffix("-step-4")
        coordinator_result = DecisionCoordinator(
            self.session,
            self.settings,
            provider=self.provider,
            provider_factory=self.provider_factory,
        ).execute(
            CoordinatorRequest(
                workflow_run_id=workflow_run_id,
                cutoff_time=cutoff,
                evidence=request.evidence,
                symbols=request.symbols or [],
                dataset_key=request.dataset_key or f"run:{workflow_run_id}",
                source_snapshot_ids=request.source_snapshot_ids or [],
                source_run_ids=request.source_run_ids or [workflow_run_id],
                decision_packet=request.decision_packet,
                decision_phase=request.decision_phase,
                trading_date=request.trading_date,
                parent_session_id=request.parent_session_id,
                analysis_metadata=request.analysis_metadata or {},
            )
        )
        result = coordinator_result.equity_result
        if result is None:
            raise RuntimeError("decision coordinator did not return an equity result")
        report = coordinator_result.decision_report
        return DecisionResponse(
            stance=str(report.get("status") or result.status),
            confidence=report.get("market_regime", {}).get("confidence"),
            summary=json.dumps(report, ensure_ascii=False),
            is_mock=False,
            result=result,
            session_id=coordinator_result.session_id,
            technical_report=coordinator_result.technical_report,
            decision_report=report,
        )
