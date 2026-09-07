from __future__ import annotations

from datetime import date, datetime
from typing import Iterable, Literal, Mapping

from pydantic import Field, model_validator

from app.buy_opportunities.contracts import OpportunityModel

from .buy_execution import PaperPortfolioPolicy


class LedgerEvent(OpportunityModel):
    event_id: str = Field(min_length=1, max_length=160)
    opportunity_id: str = Field(min_length=1, max_length=160)
    symbol: str = Field(min_length=1, max_length=32)
    theme: str | None = None
    event_at: datetime
    side: Literal["entry", "exit"]
    status: Literal["filled", "cancelled", "expired", "rejected"]
    quantity: float = Field(ge=0)
    price: float = Field(ge=0)
    fee: float = Field(default=0, ge=0)
    planned_risk: float = Field(default=0, ge=0)
    display_order: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_filled_values(self) -> "LedgerEvent":
        if self.status == "filled" and (self.quantity <= 0 or self.price <= 0):
            raise ValueError("filled ledger event 必须包含正数量和正价格")
        return self


class LedgerPosition(OpportunityModel):
    symbol: str
    opportunity_id: str
    theme: str | None = None
    quantity: float = Field(gt=0)
    entry_price: float = Field(gt=0)
    entry_fee: float = Field(ge=0)
    planned_risk: float = Field(ge=0)
    opened_at: datetime


class LedgerMark(OpportunityModel):
    marked_at: datetime
    symbol: str
    price: float = Field(gt=0)


class LedgerSnapshot(OpportunityModel):
    marked_at: datetime
    cash: float
    gross_exposure: float
    nav: float
    drawdown: float = Field(ge=0)
    positions: dict[str, float] = Field(default_factory=dict)


class PortfolioLedger(OpportunityModel):
    initial_cash: float = Field(gt=0)
    cash: float
    realized_pnl: float = 0
    positions: dict[str, LedgerPosition] = Field(default_factory=dict)
    events: list[LedgerEvent] = Field(default_factory=list)
    rejected_event_ids: list[str] = Field(default_factory=list)
    snapshots: list[LedgerSnapshot] = Field(default_factory=list)
    peak_nav: float
    max_drawdown: float = 0


def _mark_values(
    *,
    positions: Mapping[str, LedgerPosition],
    marks: Mapping[str, float],
) -> tuple[float, float]:
    gross_exposure = 0.0
    market_value = 0.0
    for symbol, position in positions.items():
        price = float(marks.get(symbol, position.entry_price))
        value = position.quantity * price
        gross_exposure += value
        market_value += value
    return gross_exposure, market_value


def _record_snapshot(
    ledger: PortfolioLedger,
    marked_at: datetime,
    marks: Mapping[str, float],
) -> PortfolioLedger:
    gross_exposure, market_value = _mark_values(
        positions=ledger.positions,
        marks=marks,
    )
    nav = ledger.cash + market_value
    peak_nav = max(ledger.peak_nav, nav)
    drawdown = max(0.0, peak_nav - nav)
    snapshot = LedgerSnapshot(
        marked_at=marked_at,
        cash=round(ledger.cash, 8),
        gross_exposure=round(gross_exposure, 8),
        nav=round(nav, 8),
        drawdown=round(drawdown, 8),
        positions={
            symbol: round(position.quantity * float(marks.get(symbol, position.entry_price)), 8)
            for symbol, position in ledger.positions.items()
        },
    )
    return ledger.model_copy(
        update={
            "snapshots": [*ledger.snapshots, snapshot],
            "peak_nav": peak_nav,
            "max_drawdown": max(ledger.max_drawdown, drawdown),
        }
    )


def _apply_entry(
    ledger: PortfolioLedger,
    event: LedgerEvent,
    policy: PaperPortfolioPolicy,
) -> PortfolioLedger:
    if event.symbol in ledger.positions:
        return ledger.model_copy(
            update={
                "events": [*ledger.events, event],
                "rejected_event_ids": [*ledger.rejected_event_ids, event.event_id],
            }
        )
    max_positions = int(policy.max_concurrent_symbols)
    if len(ledger.positions) >= max_positions:
        return ledger.model_copy(
            update={
                "events": [*ledger.events, event],
                "rejected_event_ids": [*ledger.rejected_event_ids, event.event_id],
            }
        )
    notional = event.quantity * event.price
    projected_gross = sum(
        position.quantity * position.entry_price
        for position in ledger.positions.values()
    ) + notional
    max_gross = ledger.initial_cash * policy.max_total_notional_fraction
    symbol_limit = ledger.initial_cash * policy.max_symbol_notional_fraction
    projected_risk = sum(
        position.planned_risk for position in ledger.positions.values()
    ) + event.planned_risk
    max_risk = ledger.initial_cash * policy.max_total_risk_fraction
    projected_theme = (
        sum(
            position.quantity * position.entry_price
            for position in ledger.positions.values()
            if position.theme == event.theme
        )
        + notional
        if event.theme is not None
        else 0.0
    )
    theme_symbols = {
        position.symbol
        for position in ledger.positions.values()
        if event.theme is not None and position.theme == event.theme
    }
    max_theme = ledger.initial_cash * policy.max_theme_notional_fraction
    if (
        projected_gross > max_gross
        or notional > symbol_limit
        or projected_risk > max_risk
        or (event.theme is not None and projected_theme > max_theme)
        or (
            event.theme is not None
            and event.symbol not in theme_symbols
            and len(theme_symbols) >= policy.max_theme_symbols
        )
        or ledger.cash < notional + event.fee
    ):
        return ledger.model_copy(
            update={
                "events": [*ledger.events, event],
                "rejected_event_ids": [*ledger.rejected_event_ids, event.event_id],
            }
        )
    position = LedgerPosition(
        symbol=event.symbol,
        opportunity_id=event.opportunity_id,
        theme=event.theme,
        quantity=event.quantity,
        entry_price=event.price,
        entry_fee=event.fee,
        planned_risk=event.planned_risk,
        opened_at=event.event_at,
    )
    return ledger.model_copy(
        update={
            "cash": ledger.cash - notional - event.fee,
            "positions": {**ledger.positions, event.symbol: position},
            "events": [*ledger.events, event],
        }
    )


def _apply_exit(ledger: PortfolioLedger, event: LedgerEvent) -> PortfolioLedger:
    position = ledger.positions.get(event.symbol)
    if (
        position is None
        or event.opportunity_id != position.opportunity_id
        or event.quantity > position.quantity
    ):
        return ledger.model_copy(
            update={
                "events": [*ledger.events, event],
                "rejected_event_ids": [*ledger.rejected_event_ids, event.event_id],
            }
        )
    proceeds = event.quantity * event.price - event.fee
    entry_cost = event.quantity * position.entry_price
    pnl = proceeds - entry_cost - position.entry_fee * (
        event.quantity / position.quantity
    )
    remaining = position.quantity - event.quantity
    positions = dict(ledger.positions)
    if remaining <= 1e-12:
        positions.pop(event.symbol)
    else:
        positions[event.symbol] = position.model_copy(update={"quantity": remaining})
    return ledger.model_copy(
        update={
            "cash": ledger.cash + proceeds,
            "realized_pnl": ledger.realized_pnl + pnl,
            "positions": positions,
            "events": [*ledger.events, event],
        }
    )


def replay_portfolio_ledger(
    *,
    events: Iterable[LedgerEvent],
    initial_cash: float,
    policy: PaperPortfolioPolicy,
    marks: Iterable[LedgerMark] = (),
) -> PortfolioLedger:
    if initial_cash <= 0:
        raise ValueError("initial_cash 必须大于 0")
    ledger = PortfolioLedger(
        initial_cash=initial_cash,
        cash=initial_cash,
        peak_nav=initial_cash,
    )
    ordered_events = sorted(
        events,
        key=lambda event: (event.event_at, event.display_order, event.event_id),
    )
    marks_by_time: dict[datetime, list[LedgerMark]] = {}
    for mark in marks:
        marks_by_time.setdefault(mark.marked_at, []).append(mark)
    event_times = sorted({event.event_at for event in ordered_events} | set(marks_by_time))
    events_by_time: dict[datetime, list[LedgerEvent]] = {}
    for event in ordered_events:
        events_by_time.setdefault(event.event_at, []).append(event)
    for timestamp in event_times:
        for event in events_by_time.get(timestamp, []):
            if event.status != "filled":
                ledger = ledger.model_copy(
                    update={
                        "events": [*ledger.events, event],
                        "rejected_event_ids": (
                            [*ledger.rejected_event_ids, event.event_id]
                            if event.status == "rejected"
                            else ledger.rejected_event_ids
                        ),
                    }
                )
                continue
            ledger = (
                _apply_entry(ledger, event, policy)
                if event.side == "entry"
                else _apply_exit(ledger, event)
            )
        marks_at_time = {
            mark.symbol: mark.price for mark in marks_by_time.get(timestamp, [])
        }
        if marks_at_time:
            ledger = _record_snapshot(ledger, timestamp, marks_at_time)
    return ledger


def daily_nav_curve(
    *,
    ledger: PortfolioLedger,
    marks_by_date: Mapping[date, Mapping[str, float]],
) -> tuple[LedgerSnapshot, ...]:
    """Mark the shared portfolio on each supplied trading date."""

    result = ledger
    for day, marks in sorted(marks_by_date.items()):
        result = _record_snapshot(
            result,
            datetime.combine(day, datetime.min.time()),
            marks,
        )
    return tuple(result.snapshots)
