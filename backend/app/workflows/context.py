from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.integrations.anomalo import AnomaloAdapter
from app.integrations.decision import DecisionAdapter
from app.integrations.fred import DailyMacroAdapter
from app.integrations.moomoo import MarketCollectorAdapter, MoomooAdapter
from app.integrations.moomoo_options import OptionsCollectorAdapter
from app.workflows.base import StepResult

if TYPE_CHECKING:
    from app.repositories.events import EventRepository
    from app.services.capital_flow import CapitalFlowService


@dataclass
class RunContext:
    run_id: str
    run_type: str
    cutoff_time: datetime
    symbols: list[str]
    instrument_symbols: list[str] = field(default_factory=lambda: ["INTC", "SMH"])
    # Daily-history collection is broader than the legacy indicator/watchlist
    # scope.  Keep both fields during the migration so mock/read-model output
    # remains stable while live collectors can honour collection.daily_history.
    history_symbols: list[str] = field(default_factory=list)
    option_symbols: list[str] = field(default_factory=list)
    event_instrument_symbols: list[str] = field(default_factory=lambda: ["INTC"])
    simulate_macro_event: bool = False
    simulate_instrument_event: bool = False
    fail_step: str | None = None
    market_adapter: MarketCollectorAdapter | None = None
    macro_adapter: DailyMacroAdapter | None = None
    moomoo_adapter: MoomooAdapter | None = None
    options_adapter: OptionsCollectorAdapter | None = None
    anomalo_adapter: AnomaloAdapter | None = None
    decision_adapter: DecisionAdapter | None = None
    decision_enabled: bool = False
    event_repository: "EventRepository | None" = None
    capital_flow_service: "CapitalFlowService | None" = None
    expected_events_enabled: bool = False
    breaking_events_enabled: bool = False
    scheduled_event_agent: str = "scheduled-event-investigator"
    breaking_event_agent: str = "breaking-event-investigator"
    event_horizon_days: int = 120
    workflow_research_variant: str = "events"
    cta_proxy_symbols: list[str] = field(default_factory=list)
    cta_market_input: dict[str, Any] = field(default_factory=dict)
    instrument_persistence_input: dict[str, Any] = field(default_factory=dict)
    results: dict[str, StepResult] = field(default_factory=dict)
    snapshot_id: str | None = None
    decision_packet: dict[str, Any] | None = None
    decision_dataset_key: str | None = None
    decision_source_run_ids: list[str] = field(default_factory=list)
    decision_source_snapshot_ids: list[str] = field(default_factory=list)
    decision_pair_status: str = "not_prepared"
    decision_pair_reason: str | None = None
    decision_phase: str = "pre_close"
    decision_trading_date: str = ""
    decision_parent_session_id: str | None = None
    trigger_type: str = "scheduled"
    analysis_mode: str = "official_cycle"
    session_context: str = "unknown"
    official_cycle: bool = True
    eligible_for_scoring: bool = True
    updates_official_cta_state: bool = True
    universe_version_id: str | None = None
    universe_content_sha256: str | None = None
    universe_items_by_symbol: dict[str, dict[str, Any]] = field(default_factory=dict)

    def should_fail(self, code: str) -> bool:
        return self.fail_step == code
