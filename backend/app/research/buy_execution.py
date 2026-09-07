from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from math import floor, isfinite
from typing import Iterable, Literal, Mapping
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from app.buy_opportunities.breakout import IntradayObservation
from app.buy_opportunities.contracts import (
    EntryIntentRecord,
    ExecutionRecord,
    ExitRecord,
    OpportunityModel,
    SetupRecord,
    TriggerRecord,
)
from app.decision_harness.contracts import content_sha256


def _hashable(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {key: _hashable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_hashable(item) for item in value]
    return value


def _price_mapping_matches(quote: QuoteObservation, expected: str | None) -> bool:
    return (
        quote.price_mapping_id == expected
        if expected is not None
        else quote.price_mapping_id is None
    )


class QuoteObservation(OpportunityModel):
    quote_id: str = Field(min_length=1, max_length=128)
    symbol: str | None = None
    source_mode: Literal[
        "observed_pit",
        "historical_reconstruction",
        "daily_execution_proxy",
    ] | None = None
    price_basis: Literal["raw", "adjusted"] | None = None
    price_mapping_id: str | None = None
    timestamp: datetime
    received_at: datetime
    bid: float
    ask: float

    @field_validator("timestamp", "received_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("quote 时间必须包含时区")
        return value

    @field_validator("bid", "ask")
    @classmethod
    def validate_price(cls, value: float) -> float:
        if not isfinite(value) or value < 0:
            raise ValueError("quote 价格必须是非负有限数字")
        return value

    @model_validator(mode="after")
    def validate_quote(self) -> "QuoteObservation":
        if self.received_at < self.timestamp:
            raise ValueError("quote received_at 不能早于 quote timestamp")
        if self.bid > self.ask:
            raise ValueError("quote bid 不能高于 ask")
        return self


class PriceMapping(OpportunityModel):
    """A frozen, auditable conversion between raw and adjusted quote units."""

    mapping_id: str = Field(min_length=1, max_length=128)
    symbol: str = Field(min_length=1, max_length=32)
    from_basis: Literal["raw", "adjusted"]
    to_basis: Literal["raw", "adjusted"]
    price_factor: float = Field(gt=0)
    quantity_factor: float = Field(gt=0)
    cash_dividend_per_share: float = Field(default=0, ge=0)
    source_revision: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_direction(self) -> "PriceMapping":
        if self.from_basis == self.to_basis:
            raise ValueError("price mapping 必须转换 raw 与 adjusted 之间的口径")
        return self


def convert_quote_observation(
    quote: QuoteObservation,
    *,
    target_basis: Literal["raw", "adjusted"],
    target_mapping_id: str | None,
    mapping: PriceMapping | None,
) -> QuoteObservation:
    """Convert a quote only when its frozen mapping identity is fully compatible."""

    if quote.price_basis in {None, target_basis}:
        return quote
    if mapping is None:
        return quote
    if (
        quote.price_mapping_id != mapping.mapping_id
        or quote.price_basis != mapping.from_basis
        or target_basis != mapping.to_basis
    ):
        return quote
    if quote.symbol is not None and quote.symbol != mapping.symbol:
        raise ValueError("price mapping symbol 与 quote symbol 不一致")
    return quote.model_copy(
        update={
            "bid": quote.bid * mapping.price_factor,
            "ask": quote.ask * mapping.price_factor,
            "price_basis": target_basis,
            "price_mapping_id": target_mapping_id or mapping.mapping_id,
        }
    )


def convert_price_quantity(
    *,
    price: float,
    quantity: float,
    from_basis: Literal["raw", "adjusted"],
    to_basis: Literal["raw", "adjusted"],
    mapping: PriceMapping,
) -> tuple[float, float, float]:
    """Convert price and share units and return the separate dividend cash flow."""

    if from_basis == to_basis:
        return price, quantity, 0.0
    if from_basis != mapping.from_basis or to_basis != mapping.to_basis:
        raise ValueError("price mapping direction 与转换请求不一致")
    return (
        price * mapping.price_factor,
        quantity * mapping.quantity_factor,
        mapping.cash_dividend_per_share * quantity,
    )


class ExecutionPolicy(OpportunityModel):
    name: str = Field(min_length=1, max_length=64)
    response_minutes: int = Field(default=15, ge=0, le=1440)
    intent_ttl_minutes: int = Field(default=45, ge=1, le=1440)
    quote_max_age_seconds: int = Field(default=60, ge=1, le=3600)
    execution_delay_seconds: int = Field(default=1, ge=0, le=3600)
    execution_wait_seconds: int = Field(default=60, ge=1, le=3600)
    slippage_bps: float = Field(default=0.0, ge=0, le=1000)
    fee_bps: float = Field(default=10.0, ge=0, le=1000)
    market_timezone: str = "America/New_York"
    cutoff_hour: int = Field(default=15, ge=0, le=23)
    cutoff_minute: int = Field(default=45, ge=0, le=59)
    exit_mode: Literal["automatic_protection", "manual"] = "automatic_protection"

    @property
    def policy_hash(self) -> str:
        return content_sha256(self.model_dump(mode="json"))


class PaperPortfolioPolicy(OpportunityModel):
    risk_fraction: float = Field(default=0.0025, gt=0, le=1)
    max_symbol_notional_fraction: float = Field(default=0.20, gt=0, le=1)
    max_total_notional_fraction: float = Field(default=1.0, gt=0, le=2)
    max_theme_notional_fraction: float = Field(default=1.0, gt=0, le=2)
    max_theme_symbols: int = Field(default=1, ge=1, le=100)
    max_concurrent_symbols: int = Field(default=3, ge=1, le=100)
    max_total_risk_fraction: float = Field(default=0.0075, gt=0, le=1)
    fractional_share_precision: int = Field(default=3, ge=0, le=6)
    capital_policy: str = Field(default="fifo", min_length=1, max_length=32)
    display_policy: str = Field(default="mark_to_market", min_length=1, max_length=32)

    @property
    def policy_hash(self) -> str:
        return content_sha256(self.model_dump(mode="json"))


class PaperQuantityPlan(OpportunityModel):
    status: str
    quantity: float = Field(ge=0)
    risk_budget: float = Field(ge=0)
    planned_risk: float = Field(ge=0)
    notional: float = Field(ge=0)
    reasons: list[str] = Field(default_factory=list)


class ExecutionEligibility(OpportunityModel):
    status: str
    asset_type: str
    source_mode: str
    holding_period_event: bool = False
    two_day_entry_block: bool = False
    reasons: list[str] = Field(default_factory=list)


def user_ready_at(
    *,
    delivered_at: datetime | None,
    response_minutes: int,
) -> datetime | None:
    """Convert a registered response policy into a visible ready timestamp."""

    if delivered_at is None:
        return None
    if response_minutes < 0:
        raise ValueError("response_minutes 不能为负")
    return delivered_at + timedelta(minutes=response_minutes)


def evaluate_execution_eligibility(
    *,
    asset_type: str,
    source_mode: str,
    event_codes: Iterable[str] = (),
    coverage_status: str = "ok",
    has_existing_position: bool = False,
    days_since_exit: int | None = None,
) -> ExecutionEligibility:
    """Keep asset, event, coverage and re-entry restrictions independently visible."""

    reasons: list[str] = []
    events = set(event_codes)
    holding_period_event = "holding_period_event" in events
    two_day_entry_block = False
    if asset_type not in {"equity", "etf"}:
        reasons.append("asset_type_ineligible")
    if coverage_status != "ok":
        reasons.append("coverage_unknown")
    if "E0" in events and asset_type != "etf":
        reasons.append("event_E0_block")
    if holding_period_event:
        reasons.append("holding_period_event")
    if "earnings_within_2_sessions" in events:
        two_day_entry_block = True
        reasons.append("two_day_entry_block")
    if has_existing_position:
        reasons.append("existing_position")
    status = (
        "asset_ineligible"
        if "asset_type_ineligible" in reasons
        else "coverage_unknown"
        if "coverage_unknown" in reasons
        else "event_blocked"
        if "event_E0_block" in reasons
        else "reentry_blocked"
        if two_day_entry_block
        else "position_blocked"
        if has_existing_position
        else "eligible"
    )
    return ExecutionEligibility(
        status=status,
        asset_type=asset_type,
        source_mode=source_mode,
        holding_period_event=holding_period_event,
        two_day_entry_block=two_day_entry_block,
        reasons=reasons,
    )


def first_qualified_quote(
    quotes: Iterable[QuoteObservation],
    *,
    ready_at: datetime,
    max_age_seconds: int = 60,
    symbol: str | None = None,
    price_basis: str | None = None,
    price_mapping_id: str | None = None,
    source_mode: str | None = None,
) -> QuoteObservation | None:
    """Return the first quote the user could have observed after ready_at."""

    ordered = sorted(quotes, key=lambda item: (item.received_at, item.quote_id))
    for quote in ordered:
        if quote.received_at < ready_at:
            continue
        age = (quote.received_at - quote.timestamp).total_seconds()
        if symbol is not None and quote.symbol not in {None, symbol}:
            continue
        if price_basis is not None and quote.price_basis not in {None, price_basis}:
            continue
        if not _price_mapping_matches(quote, price_mapping_id):
            continue
        if source_mode is not None and quote.source_mode not in {None, source_mode}:
            continue
        if 0 <= age <= max_age_seconds and quote.bid > 0 and quote.ask > 0:
            return quote
    return None


def _cutoff_for(created_at: datetime, policy: ExecutionPolicy) -> datetime:
    local = created_at.astimezone(ZoneInfo(policy.market_timezone))
    cutoff = datetime.combine(
        local.date(),
        time(policy.cutoff_hour, policy.cutoff_minute),
        tzinfo=ZoneInfo(policy.market_timezone),
    )
    return cutoff.astimezone(UTC)


def _round_quantity(value: float, precision: int) -> float:
    factor = 10**precision
    return floor(max(0.0, value) * factor) / factor


def plan_paper_quantity(
    *,
    nav: float,
    cash: float,
    quote_ask: float,
    stop: float,
    open_symbol_notional: float = 0.0,
    open_total_notional: float = 0.0,
    open_total_risk: float = 0.0,
    open_symbol_count: int = 0,
    policy: PaperPortfolioPolicy | None = None,
) -> PaperQuantityPlan:
    policy = policy or PaperPortfolioPolicy()
    if not all(isfinite(value) for value in (nav, cash, quote_ask, stop)):
        raise ValueError("paper 资金和价格必须是有限数字")
    if nav <= 0 or cash < 0 or quote_ask <= 0 or stop <= 0:
        return PaperQuantityPlan(
            status="unavailable",
            quantity=0,
            risk_budget=0,
            planned_risk=0,
            notional=0,
            reasons=["invalid_budget_or_price"],
        )
    if quote_ask <= stop:
        return PaperQuantityPlan(
            status="unavailable",
            quantity=0,
            risk_budget=0,
            planned_risk=0,
            notional=0,
            reasons=["quote_at_or_below_stop"],
        )
    if open_symbol_count >= policy.max_concurrent_symbols:
        return PaperQuantityPlan(
            status="blocked",
            quantity=0,
            risk_budget=nav * policy.risk_fraction,
            planned_risk=0,
            notional=0,
            reasons=["max_concurrent_symbols"],
        )
    risk_budget = nav * policy.risk_fraction
    remaining_total_risk = max(0.0, nav * policy.max_total_risk_fraction - open_total_risk)
    risk_budget = min(risk_budget, remaining_total_risk)
    risk_per_share = quote_ask - stop
    quantity_by_risk = risk_budget / risk_per_share if risk_per_share else 0.0
    remaining_symbol_notional = max(
        0.0,
        nav * policy.max_symbol_notional_fraction - open_symbol_notional,
    )
    remaining_total_notional = max(
        0.0,
        nav * policy.max_total_notional_fraction - open_total_notional,
    )
    quantity = min(
        quantity_by_risk,
        remaining_symbol_notional / quote_ask,
        remaining_total_notional / quote_ask,
        cash / quote_ask,
    )
    quantity = _round_quantity(quantity, policy.fractional_share_precision)
    planned_risk = quantity * risk_per_share
    notional = quantity * quote_ask
    if quantity <= 0:
        return PaperQuantityPlan(
            status="blocked",
            quantity=0,
            risk_budget=risk_budget,
            planned_risk=0,
            notional=0,
            reasons=["capital_constraint"],
        )
    return PaperQuantityPlan(
        status="ready",
        quantity=quantity,
        risk_budget=risk_budget,
        planned_risk=planned_risk,
        notional=notional,
        reasons=[],
    )


def build_q1_entry_intent(
    *,
    trigger: TriggerRecord,
    setup: SetupRecord,
    created_at: datetime,
    delivered_at: datetime | None,
    user_ready_at: datetime | None,
    quotes: Iterable[QuoteObservation],
    nav: float,
    cash: float,
    policy: ExecutionPolicy | None = None,
    portfolio: PaperPortfolioPolicy | None = None,
    source_mode: str = "historical_reconstruction",
    price_basis: str = "adjusted",
    price_mapping_id: str | None = None,
    eligibility: ExecutionEligibility | None = None,
    asset_type: str = "equity",
    coverage_status: str = "ok",
    event_codes: Iterable[str] = (),
    has_existing_position: bool = False,
    open_symbol_notional: float = 0.0,
    open_total_notional: float = 0.0,
    open_total_risk: float = 0.0,
    open_symbol_count: int = 0,
) -> tuple[EntryIntentRecord, PaperQuantityPlan]:
    """Create one response-based Q1 intent; never searches for a later better quote."""

    policy = policy or ExecutionPolicy(name="q1")
    portfolio = portfolio or PaperPortfolioPolicy()
    if source_mode not in {"observed_pit", "historical_reconstruction", "daily_execution_proxy"}:
        raise ValueError("source_mode 不受支持")
    if price_basis not in {"raw", "adjusted"}:
        raise ValueError("price_basis 不受支持")
    if created_at < trigger.known_at:
        raise ValueError("created_at 不能早于 trigger known_at")
    if delivered_at is not None and delivered_at < created_at:
        raise ValueError("delivered_at 不能早于 created_at")
    if user_ready_at is not None:
        if delivered_at is None or user_ready_at < delivered_at:
            raise ValueError("user_ready_at 必须在 delivered_at 之后")
    if eligibility is None or has_existing_position:
        eligibility = evaluate_execution_eligibility(
            asset_type=asset_type,
            source_mode=source_mode,
            event_codes=event_codes,
            coverage_status=coverage_status,
            has_existing_position=has_existing_position,
        )
    cutoff = _cutoff_for(delivered_at or created_at, policy)
    expiry = (
        min(
            delivered_at + timedelta(minutes=policy.intent_ttl_minutes),
            cutoff,
        )
        if delivered_at is not None
        else min(
            created_at + timedelta(minutes=policy.intent_ttl_minutes),
            cutoff,
        )
    )
    cutoff_passed = delivered_at is not None and delivered_at >= cutoff
    if expiry <= created_at:
        expiry = created_at + timedelta(microseconds=1)
    common = {
        "intent_id": f"{trigger.trigger_id}:intent:{policy.policy_hash[:20]}",
        "trigger_id": trigger.trigger_id,
        "symbol": setup.symbol,
        "source_mode": source_mode,
        "price_basis": price_basis,
        "price_mapping_id": price_mapping_id or setup.price_mapping_id,
        "user_policy": policy.name,
        "trigger_occurred_at": trigger.occurred_at,
        "trigger_known_at": trigger.known_at,
        "created_at": created_at,
        "delivered_at": delivered_at,
        "ready_at": user_ready_at,
        "expires_at": expiry,
        "q0_quote_id": None,
        "q0_timestamp": None,
        "q0_received_at": None,
        "q0_known_at": None,
        "limit_price": setup.upper_limit,
        "q0_bid": None,
        "q0_ask": None,
        "planned_quantity": 0.0,
        "risk_budget": 0.0,
        "planned_risk": 0.0,
        "status": "cancelled",
        "reason_code": None,
        "content_sha256": "",
    }
    if setup.status != "armed":
        common["reason_code"] = "setup_not_active"
    elif trigger.kind in {"late_discovery", "cross_unobserved"}:
        common["reason_code"] = f"{trigger.kind}_not_fresh_entry"
    elif trigger.extension_status == "missed_extension":
        common["reason_code"] = "trigger_above_upper_limit"
    elif eligibility.status != "eligible":
        common["reason_code"] = f"eligibility_{eligibility.status}"
    elif delivered_at is None:
        common["reason_code"] = "delivery_unknown"
    elif cutoff_passed:
        common["status"] = "expired"
        common["reason_code"] = "market_cutoff_passed"
    elif user_ready_at is None:
        common["reason_code"] = "user_not_ready"
    elif user_ready_at >= expiry:
        common["status"] = "expired"
        common["reason_code"] = "response_after_expiry"
    else:
        q0 = first_qualified_quote(
            (quote for quote in quotes if quote.received_at <= expiry),
            ready_at=user_ready_at,
            max_age_seconds=policy.quote_max_age_seconds,
            symbol=setup.symbol,
            price_basis=price_basis,
            price_mapping_id=price_mapping_id or setup.price_mapping_id,
            source_mode=source_mode,
        )
        if q0 is None:
            common["reason_code"] = "quote_unavailable"
        else:
            common["q0_bid"] = q0.bid
            common["q0_ask"] = q0.ask
            common["q0_quote_id"] = q0.quote_id
            common["q0_timestamp"] = q0.timestamp
            common["q0_received_at"] = q0.received_at
            common["q0_known_at"] = q0.received_at
            if not setup.upper_trigger <= q0.ask <= setup.upper_limit:
                common["reason_code"] = "price_outside_plan"
            else:
                planning_ask = (
                    q0.ask
                    * (1 + policy.slippage_bps / 10_000)
                    * (1 + policy.fee_bps / 10_000)
                )
                quantity_plan = plan_paper_quantity(
                    nav=nav,
                    cash=cash,
                    quote_ask=planning_ask,
                    stop=setup.invalidation_stop,
                    open_symbol_notional=open_symbol_notional,
                    open_total_notional=open_total_notional,
                    open_total_risk=open_total_risk,
                    open_symbol_count=open_symbol_count,
                    policy=portfolio,
                )
                common["planned_quantity"] = quantity_plan.quantity
                common["risk_budget"] = quantity_plan.risk_budget
                common["planned_risk"] = quantity_plan.planned_risk
                common["status"] = "ready" if quantity_plan.quantity > 0 else "cancelled"
                common["reason_code"] = (
                    None if quantity_plan.quantity > 0 else quantity_plan.reasons[0]
                )
                common["content_sha256"] = content_sha256(
                    _hashable(
                        {key: value for key, value in common.items() if key != "content_sha256"}
                    )
                )
                return EntryIntentRecord(**common), quantity_plan
    common["content_sha256"] = content_sha256(
        _hashable({key: value for key, value in common.items() if key != "content_sha256"})
    )
    return EntryIntentRecord(**common), PaperQuantityPlan(
        status="unavailable",
        quantity=0,
        risk_budget=0,
        planned_risk=0,
        notional=0,
        reasons=[str(common["reason_code"])],
    )


def _not_filled_execution(
    *,
    intent: EntryIntentRecord,
    policy: ExecutionPolicy,
    reason_code: str,
    source_mode: str,
) -> ExecutionRecord:
    identity = {
        "intent_id": intent.intent_id,
        "policy_hash": policy.policy_hash,
        "reason_code": reason_code,
    }
    return ExecutionRecord(
        execution_id=f"{intent.intent_id}:execution:{content_sha256(identity)[:20]}",
        intent_id=intent.intent_id,
        policy_hash=policy.policy_hash,
        source_mode=source_mode,
        price_mapping_id=intent.price_mapping_id,
        fill_quantity=0,
        status="not_filled",
        reason_code=reason_code,
        content_sha256=content_sha256(identity),
    )


def execute_q1(
    *,
    intent: EntryIntentRecord,
    setup: SetupRecord,
    q1_quotes: Iterable[QuoteObservation],
    sent_at: datetime,
    policy: ExecutionPolicy,
) -> ExecutionRecord:
    if intent.status != "ready":
        raise ValueError("只有 ready entry intent 才能发送 Q1 意图")
    if intent.ready_at is None or sent_at < intent.ready_at:
        return _not_filled_execution(
            intent=intent,
            policy=policy,
            reason_code="sent_before_user_ready",
            source_mode=intent.source_mode,
        )
    if intent.q0_received_at is None or sent_at < intent.q0_received_at:
        return _not_filled_execution(
            intent=intent,
            policy=policy,
            reason_code="sent_before_q0_known",
            source_mode=intent.source_mode,
        )
    if sent_at < intent.created_at or sent_at >= intent.expires_at:
        return _not_filled_execution(
            intent=intent,
            policy=policy,
            reason_code="intent_expired",
            source_mode=intent.source_mode,
        )
    eligible_after = sent_at + timedelta(seconds=policy.execution_delay_seconds)
    deadline = min(
        eligible_after + timedelta(seconds=policy.execution_wait_seconds),
        intent.expires_at,
    )
    q1 = next(
        (
            quote
            for quote in sorted(q1_quotes, key=lambda item: (item.received_at, item.quote_id))
            if eligible_after <= quote.received_at <= deadline
            and quote.timestamp >= sent_at
            and quote.received_at >= (intent.q0_known_at or intent.q0_received_at)
            and quote.symbol in {None, intent.symbol}
            and quote.price_basis in {None, intent.price_basis}
            and _price_mapping_matches(quote, intent.price_mapping_id)
            and quote.source_mode in {None, intent.source_mode}
            and quote.bid > 0
            and quote.ask > 0
            and (quote.received_at - quote.timestamp).total_seconds()
            <= policy.quote_max_age_seconds
        ),
        None,
    )
    identity = {
        "intent_id": intent.intent_id,
        "sent_at": sent_at.isoformat(),
        "policy_hash": policy.policy_hash,
        "q1_quote_id": q1.quote_id if q1 else None,
    }
    execution_id = f"{intent.intent_id}:execution:{content_sha256(identity)[:20]}"
    if q1 is None:
        return ExecutionRecord(
            execution_id=execution_id,
            intent_id=intent.intent_id,
            policy_hash=policy.policy_hash,
            source_mode=intent.source_mode,
            price_mapping_id=intent.price_mapping_id,
            fill_quantity=0,
            status="not_filled",
            reason_code="q1_quote_unavailable",
            content_sha256=content_sha256(identity),
        )
    if q1.ask > setup.upper_limit:
        return ExecutionRecord(
            execution_id=execution_id,
            intent_id=intent.intent_id,
            policy_hash=policy.policy_hash,
            source_mode=intent.source_mode,
            price_mapping_id=intent.price_mapping_id,
            fill_quantity=0,
            status="not_filled",
            reason_code="q1_above_upper_limit",
            content_sha256=content_sha256({**identity, "ask": q1.ask}),
        )
    fill_price = q1.ask * (1 + policy.slippage_bps / 10_000)
    if fill_price > setup.upper_limit:
        return ExecutionRecord(
            execution_id=execution_id,
            intent_id=intent.intent_id,
            policy_hash=policy.policy_hash,
            source_mode=intent.source_mode,
            price_mapping_id=intent.price_mapping_id,
            fill_quantity=0,
            status="not_filled",
            reason_code="slippage_above_upper_limit",
            content_sha256=content_sha256({**identity, "ask": q1.ask}),
        )
    cost = fill_price * intent.planned_quantity * policy.fee_bps / 10_000
    planned_risk = (float(intent.q0_ask or fill_price) - setup.invalidation_stop) * intent.planned_quantity
    return ExecutionRecord(
        execution_id=execution_id,
        intent_id=intent.intent_id,
        policy_hash=policy.policy_hash,
        source_mode=intent.source_mode,
        price_mapping_id=intent.price_mapping_id,
        fill_at=q1.received_at,
        fill_price=fill_price,
        fill_quantity=intent.planned_quantity,
        requested_quantity=intent.planned_quantity,
        fill_status="full",
        requested_price=q1.ask,
        slippage=fill_price - q1.ask,
        fees=cost,
        cost=cost,
        planned_risk=max(0.0, planned_risk),
        status="filled",
        reason_code="breach_during_latency" if q1.ask < setup.upper_trigger else None,
        content_sha256=content_sha256({**identity, "ask": q1.ask, "fill_price": fill_price}),
    )


def execute_m1(
    *,
    intent: EntryIntentRecord,
    setup: SetupRecord,
    minute_bars: Iterable[IntradayObservation],
    policy: ExecutionPolicy,
) -> ExecutionRecord:
    if intent.status != "ready":
        raise ValueError("只有 ready entry intent 才能执行 M1")
    if intent.ready_at is None:
        return _not_filled_execution(
            intent=intent,
            policy=policy,
            reason_code="user_ready_unknown",
            source_mode=intent.source_mode,
        )
    ordered = sorted(minute_bars, key=lambda item: (item.end_at, item.bar_id))
    first = next(
        (
            bar
            for bar in ordered
            if bar.complete and bar.available_at >= intent.ready_at
        ),
        None,
    )
    second = (
        next(
            (
                bar
                for bar in ordered
                if first
                and bar.start_at >= first.available_at
                and bar.complete
            ),
            None,
        )
        if first
        else None
    )
    identity = {
        "intent_id": intent.intent_id,
        "policy_hash": policy.policy_hash,
        "first_bar": first.bar_id if first else None,
        "second_bar": second.bar_id if second else None,
    }
    execution_id = f"{intent.intent_id}:m1:{content_sha256(identity)[:20]}"
    if second is None:
        return ExecutionRecord(
            execution_id=execution_id,
            intent_id=intent.intent_id,
            policy_hash=policy.policy_hash,
            source_mode=intent.source_mode,
            price_mapping_id=intent.price_mapping_id,
            fill_quantity=0,
            status="not_filled",
            reason_code="next_minute_unavailable",
            content_sha256=content_sha256(identity),
        )
    fill_price = second.open * (1 + policy.slippage_bps / 10_000)
    if fill_price > setup.upper_limit:
        return ExecutionRecord(
            execution_id=execution_id,
            intent_id=intent.intent_id,
            policy_hash=policy.policy_hash,
            source_mode=intent.source_mode,
            price_mapping_id=intent.price_mapping_id,
            fill_quantity=0,
            status="not_filled",
            reason_code="next_minute_above_upper_limit",
            content_sha256=content_sha256({**identity, "open": second.open}),
        )
    return ExecutionRecord(
        execution_id=execution_id,
        intent_id=intent.intent_id,
        policy_hash=policy.policy_hash,
        source_mode=intent.source_mode,
        price_mapping_id=intent.price_mapping_id,
        fill_at=second.start_at,
        fill_price=fill_price,
        fill_quantity=intent.planned_quantity,
        requested_quantity=intent.planned_quantity,
        fill_status="full",
        requested_price=second.open,
        slippage=fill_price - second.open,
        fees=fill_price * intent.planned_quantity * policy.fee_bps / 10_000,
        cost=fill_price * intent.planned_quantity * policy.fee_bps / 10_000,
        planned_risk=(float(first.close) - setup.invalidation_stop) * intent.planned_quantity
        if first
        else None,
        status="filled",
        content_sha256=content_sha256({**identity, "fill_price": fill_price}),
    )


def execute_d1(
    *,
    intent: EntryIntentRecord,
    setup: SetupRecord,
    next_session_open: float,
    fill_at: datetime,
    policy: ExecutionPolicy,
) -> ExecutionRecord:
    if intent.status != "ready":
        raise ValueError("只有 ready entry intent 才能执行 D1")
    if intent.ready_at is None or fill_at <= intent.ready_at:
        return _not_filled_execution(
            intent=intent,
            policy=policy,
            reason_code="session_open_before_user_ready",
            source_mode=intent.source_mode,
        )
    if fill_at.date() <= intent.created_at.date():
        return _not_filled_execution(
            intent=intent,
            policy=policy,
            reason_code="same_session_open_not_committed",
            source_mode=intent.source_mode,
        )
    identity = {
        "intent_id": intent.intent_id,
        "policy_hash": policy.policy_hash,
        "fill_at": fill_at.isoformat(),
        "open": next_session_open,
    }
    execution_id = f"{intent.intent_id}:d1:{content_sha256(identity)[:20]}"
    if next_session_open > setup.upper_limit:
        return ExecutionRecord(
            execution_id=execution_id,
            intent_id=intent.intent_id,
            policy_hash=policy.policy_hash,
            source_mode=intent.source_mode,
            price_mapping_id=intent.price_mapping_id,
            fill_quantity=0,
            status="not_filled",
            reason_code="next_session_open_above_upper_limit",
            content_sha256=content_sha256(identity),
        )
    fill_price = next_session_open * (1 + policy.slippage_bps / 10_000)
    if fill_price > setup.upper_limit:
        return ExecutionRecord(
            execution_id=execution_id,
            intent_id=intent.intent_id,
            policy_hash=policy.policy_hash,
            source_mode=intent.source_mode,
            price_mapping_id=intent.price_mapping_id,
            fill_quantity=0,
            status="not_filled",
            reason_code="next_session_slippage_above_upper_limit",
            content_sha256=content_sha256(identity),
        )
    return ExecutionRecord(
        execution_id=execution_id,
        intent_id=intent.intent_id,
        policy_hash=policy.policy_hash,
        source_mode=intent.source_mode,
        price_mapping_id=intent.price_mapping_id,
        fill_at=fill_at,
        fill_price=fill_price,
        fill_quantity=intent.planned_quantity,
        requested_quantity=intent.planned_quantity,
        fill_status="full",
        requested_price=next_session_open,
        slippage=fill_price - next_session_open,
        fees=fill_price * intent.planned_quantity * policy.fee_bps / 10_000,
        cost=fill_price * intent.planned_quantity * policy.fee_bps / 10_000,
        planned_risk=(setup.upper_limit - setup.invalidation_stop) * intent.planned_quantity,
        status="filled",
        content_sha256=content_sha256({**identity, "fill_price": fill_price}),
    )


def simulate_long_exit(
    *,
    execution: ExecutionRecord,
    setup: SetupRecord,
    daily_bars: Iterable,
    sell_quotes: Mapping[date, QuoteObservation] | None = None,
    policy: ExecutionPolicy | None = None,
    max_hold_sessions: int = 20,
    post_fill_intraday: Iterable[IntradayObservation] = (),
) -> ExitRecord:
    """Apply stop, structure failure, and time exit without future selection."""

    if execution.status != "filled" or execution.fill_at is None or execution.fill_price is None:
        raise ValueError("只有 filled execution 才能计算退出")
    policy = policy or ExecutionPolicy(name="q1")
    sell_quotes = sell_quotes or {}
    if policy.exit_mode == "manual":
        return _exit_record(
            execution=execution,
            status="pending",
            exit_at=None,
            exit_price=None,
            reason_code="manual_exit_not_simulated",
            policy=policy,
        )
    ordered_bars = sorted(daily_bars, key=lambda item: item.date)
    fill_date = execution.fill_at.date()
    after_fill = [
        bar
        for bar in ordered_bars
        if bar.date > fill_date
        or (bar.date == fill_date and bar.available_at > execution.fill_at)
    ]
    has_fill_day = any(bar.date == fill_date for bar in after_fill)
    intraday = sorted(post_fill_intraday, key=lambda item: (item.start_at, item.bar_id))
    for offset, bar in enumerate(after_fill):
        session_number = (1 if has_fill_day else 2) + offset
        if bar.date == fill_date:
            post_fill_bars = [
                item
                for item in intraday
                if item.end_at > execution.fill_at
                and item.available_at <= bar.available_at
            ]
            if post_fill_bars:
                if any(item.open <= setup.invalidation_stop for item in post_fill_bars):
                    return _exit_record(
                        execution=execution,
                        status="gap_stop",
                        exit_at=next(
                            item.start_at
                            for item in post_fill_bars
                            if item.open <= setup.invalidation_stop
                        ),
                        exit_price=setup.invalidation_stop,
                        reason_code="post_fill_gap_stop",
                        policy=policy,
                    )
                if any(item.low <= setup.invalidation_stop for item in post_fill_bars):
                    return _exit_record(
                        execution=execution,
                        status="stop",
                        exit_at=next(
                            item.end_at
                            for item in post_fill_bars
                            if item.low <= setup.invalidation_stop
                        ),
                        exit_price=setup.invalidation_stop,
                        reason_code="post_fill_protective_stop",
                        policy=policy,
                    )
            elif bar.low <= setup.invalidation_stop:
                return _exit_record(
                    execution=execution,
                    status="ambiguous",
                    exit_at=bar.available_at,
                    exit_price=None,
                    reason_code="same_day_stop_path_ambiguous",
                    policy=policy,
                )
        elif bar.open <= setup.invalidation_stop:
            return _exit_record(
                execution=execution,
                status="gap_stop",
                exit_at=bar.available_at,
                exit_price=bar.open,
                reason_code="gap_through_stop",
                policy=policy,
            )
        if bar.date != fill_date and bar.low <= setup.invalidation_stop:
            return _exit_record(
                execution=execution,
                status="stop",
                exit_at=bar.available_at,
                exit_price=setup.invalidation_stop,
                reason_code="protective_stop",
                policy=policy,
            )
        if bar.close < setup.upper_trigger:
            quote = sell_quotes.get(bar.date)
            if (
                quote is None
                or quote.received_at < bar.available_at
                or quote.symbol not in {None, setup.symbol}
                or quote.price_basis not in {None, setup.price_basis}
                or not _price_mapping_matches(quote, setup.price_mapping_id)
            ):
                return _exit_record(
                    execution=execution,
                    status="censored",
                    exit_at=None,
                    exit_price=None,
                    reason_code="exit_quote_missing",
                    policy=policy,
                )
            return _exit_record(
                execution=execution,
                status="structure_failed",
                exit_at=quote.received_at,
                exit_price=quote.bid,
                reason_code="daily_close_below_trigger",
                policy=policy,
            )
        if session_number >= max_hold_sessions:
            quote = sell_quotes.get(bar.date)
            if (
                quote is None
                or quote.received_at < bar.available_at
                or quote.symbol not in {None, setup.symbol}
                or quote.price_basis not in {None, setup.price_basis}
                or not _price_mapping_matches(quote, setup.price_mapping_id)
            ):
                return _exit_record(
                    execution=execution,
                    status="censored",
                    exit_at=None,
                    exit_price=None,
                    reason_code="time_exit_quote_missing",
                    policy=policy,
                )
            return _exit_record(
                execution=execution,
                status="time_exit",
                exit_at=quote.received_at,
                exit_price=quote.bid,
                reason_code="maximum_holding_sessions",
                policy=policy,
            )
    return _exit_record(
        execution=execution,
        status="pending",
        exit_at=None,
        exit_price=None,
        reason_code="holding_window_not_mature",
        policy=policy,
    )


def _exit_record(
    *,
    execution: ExecutionRecord,
    status: str,
    exit_at: datetime | None,
    exit_price: float | None,
    reason_code: str,
    policy: ExecutionPolicy,
) -> ExitRecord:
    executable_exit_price = (
        exit_price * (1 - policy.slippage_bps / 10_000)
        if exit_price is not None
        else None
    )
    gross_pnl = (
        (executable_exit_price - float(execution.fill_price)) * execution.fill_quantity
        if executable_exit_price is not None and execution.fill_price is not None
        else None
    )
    exit_cost = (
        executable_exit_price * execution.fill_quantity * policy.fee_bps / 10_000
        if executable_exit_price is not None
        else None
    )
    net_pnl = (
        gross_pnl - float(execution.cost or 0) - float(exit_cost or 0)
        if gross_pnl is not None
        else None
    )
    identity = {
        "execution_id": execution.execution_id,
        "status": status,
        "exit_at": exit_at.isoformat() if exit_at else None,
        "exit_price": executable_exit_price,
        "reason_code": reason_code,
    }
    exit_hash = content_sha256(identity)
    return ExitRecord(
        exit_id=f"exit:{exit_hash[:48]}",
        execution_id=execution.execution_id,
        status=status,
        exit_at=exit_at,
        exit_price=executable_exit_price,
        quantity=execution.fill_quantity if executable_exit_price is not None else 0,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        reason_code=reason_code,
        content_sha256=exit_hash,
    )
