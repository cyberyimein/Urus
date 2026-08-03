from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.integrations.anomalo import AnomaloAdapter
from app.integrations.decision import DecisionAdapter
from app.integrations.fred import DailyMacroAdapter
from app.integrations.moomoo import MarketCollectorAdapter, MoomooAdapter
from app.integrations.moomoo_options import OptionsCollectorAdapter
from app.workflows.base import StepResult


@dataclass
class RunContext:
    run_id: str
    run_type: str
    cutoff_time: datetime
    symbols: list[str]
    instrument_symbols: list[str] = field(default_factory=lambda: ["INTC", "SMH"])
    simulate_macro_event: bool = False
    simulate_instrument_event: bool = False
    fail_step: str | None = None
    market_adapter: MarketCollectorAdapter | None = None
    macro_adapter: DailyMacroAdapter | None = None
    moomoo_adapter: MoomooAdapter | None = None
    options_adapter: OptionsCollectorAdapter | None = None
    anomalo_adapter: AnomaloAdapter | None = None
    decision_adapter: DecisionAdapter | None = None
    results: dict[str, StepResult] = field(default_factory=dict)
    snapshot_id: str | None = None

    def should_fail(self, code: str) -> bool:
        return self.fail_step == code
