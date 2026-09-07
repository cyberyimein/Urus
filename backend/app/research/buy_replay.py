from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any, Sequence

from app.buy_opportunities.breakout import IntradayObservation
from app.buy_opportunities.contracts import (
    EntryIntentRecord,
    ScanRecord,
    SetupRecord,
    TriggerEvaluation,
)
from app.buy_opportunities.evidence import DailyEvidenceReader

from .buy_evaluation import (
    EvaluationRow,
    ForwardLabel,
    evaluation_row_from_records,
    independent_forward_labels,
)
from .buy_execution import (
    ExecutionPolicy,
    ExecutionRecord,
    ExecutionEligibility,
    PriceMapping,
    PaperPortfolioPolicy,
    QuoteObservation,
    build_q1_entry_intent,
    convert_quote_observation,
    execute_q1,
    simulate_long_exit,
)
from .portfolio_ledger import (
    LedgerEvent,
    LedgerMark,
    PortfolioLedger,
    replay_portfolio_ledger,
)
from app.buy_opportunities.lifecycle import (
    TradingSessionCalendar,
    advance_s1_lifecycle,
    start_s1_lifecycle,
)
from app.buy_opportunities.scanner import scan_universe


class FrozenReplayResult:
    """The complete replay chain, retained as inspectable artifacts."""

    def __init__(
        self,
        *,
        scans: list[ScanRecord],
        setups: list[SetupRecord],
        trigger_evaluations: list[TriggerEvaluation],
        intents: list[EntryIntentRecord],
        executions: list[ExecutionRecord],
        exits: list[Any],
        rows: list[EvaluationRow],
        ledger: PortfolioLedger,
        labels: list[ForwardLabel],
        label_diagnostics: list[dict[str, str]],
        diagnostics: list[dict[str, Any]],
    ):
        self.scans = scans
        self.setups = setups
        self.trigger_evaluations = trigger_evaluations
        self.intents = intents
        self.executions = executions
        self.exits = exits
        self.rows = rows
        self.ledger = ledger
        self.labels = labels
        self.label_diagnostics = label_diagnostics
        self.diagnostics = diagnostics


def validate_replay_references(result: FrozenReplayResult) -> None:
    scan_ids = {scan.scan_id for scan in result.scans}
    setup_ids = {setup.setup_id for setup in result.setups}
    trigger_ids = {
        evaluation.trigger.trigger_id
        for evaluation in result.trigger_evaluations
        if evaluation.trigger is not None
    }
    intent_ids = {intent.intent_id for intent in result.intents}
    execution_ids = {execution.execution_id for execution in result.executions}
    if any(setup.origin_scan_id not in scan_ids for setup in result.setups):
        raise ValueError("setup.origin_scan_id 无法解析到 scan")
    if any(
        evaluation.setup_id not in setup_ids
        for evaluation in result.trigger_evaluations
    ):
        raise ValueError("trigger evaluation 引用了未知 setup")
    if any(intent.trigger_id not in trigger_ids for intent in result.intents):
        raise ValueError("entry intent 引用了未知 trigger")
    if any(execution.intent_id not in intent_ids for execution in result.executions):
        raise ValueError("execution 引用了未知 intent")
    if any(exit_record.execution_id not in execution_ids for exit_record in result.exits):
        raise ValueError("exit 引用了未知 execution")


def _as_sequence(value: Iterable[Any] | None) -> list[Any]:
    return list(value or ())


def _not_filled_row(
    *,
    opportunity_id: str,
    symbol: str,
    entry_date,
    execution: ExecutionRecord,
    intent: EntryIntentRecord,
    policy: ExecutionPolicy,
    data_source: str,
) -> EvaluationRow:
    return EvaluationRow(
        opportunity_id=opportunity_id,
        symbol=symbol,
        data_source=data_source,
        source_mode=execution.source_mode,
        entry_date=entry_date,
        quantity=0,
        planned_risk=intent.planned_risk,
        cost=execution.cost or 0,
        status="not_filled",
        response_minutes=policy.response_minutes,
        execution_policy=policy.name,
        exit_policy=policy.exit_mode,
        funds_policy="paper",
        user_policy=intent.user_policy,
        policy_version=policy.policy_hash,
    )


def _build_diagnostic_snapshots(
    *,
    scans: list[ScanRecord],
    setups: list[SetupRecord],
    trigger_evaluations: list[TriggerEvaluation],
    intents: list[EntryIntentRecord],
    observations_by_symbol: Mapping[str, Iterable[IntradayObservation]],
    session_dates_by_symbol: Mapping[str, Sequence[date]],
) -> list[dict[str, Any]]:
    scan_by_symbol = {scan.symbol: scan for scan in scans}
    evaluations_by_bar: dict[str, TriggerEvaluation] = {}
    for evaluation in trigger_evaluations:
        if evaluation.trigger is not None and evaluation.trigger.observed_bar_id:
            evaluations_by_bar[evaluation.trigger.observed_bar_id] = evaluation
    intents_by_trigger = {intent.trigger_id: intent for intent in intents}
    snapshots: list[dict[str, Any]] = []
    for symbol, scan in sorted(scan_by_symbol.items()):
        observations = _as_sequence(observations_by_symbol.get(symbol))
        dates = sorted(
            set(session_dates_by_symbol.get(symbol, ()))
            or {observation.end_at.date() for observation in observations}
            or {scan.knowledge_at.date()}
        )
        for session_date in dates:
            at_10 = next(
                (
                    observation
                    for observation in sorted(
                        observations,
                        key=lambda item: (item.start_at, item.bar_id),
                    )
                    if observation.end_at.date() == session_date
                    and observation.start_at.hour == 10
                    and observation.start_at.minute == 0
                ),
                None,
            )
            base = {
                "date": session_date.isoformat(),
                "hour": 10,
                "minute": 0,
                "symbol": symbol,
                "scan_id": scan.scan_id,
                "source_mode": scan.source_mode,
                "scan_status": scan.status,
                "rejection_codes": list(scan.rejection_codes),
                "missed_by_attention": False,
                "missed_by_event": any(
                    code.startswith("event") or "earnings" in code
                    for code in scan.rejection_codes
                ),
                "missed_by_existing_position": False,
                "coverage_unknown": scan.status in {"coverage_miss", "unavailable"}
                or "coverage_miss" in scan.rejection_codes,
            }
            if scan.status != "eligible":
                base.update(
                    status="scan_rejected",
                    reason_code=scan.rejection_codes[0]
                    if scan.rejection_codes
                    else "scan_not_eligible",
                )
                snapshots.append(base)
                continue
            if at_10 is None:
                base.update(
                    status="no_10_00_observation",
                    reason_code=(
                        "daily_proxy_no_intraday"
                        if scan.source_mode == "daily_execution_proxy"
                        else "intraday_observation_missing"
                    ),
                )
                snapshots.append(base)
                continue
            evaluation = evaluations_by_bar.get(at_10.bar_id)
            if evaluation is None:
                base.update(status="unobserved", reason_code="trigger_evaluation_missing")
            elif evaluation.trigger is None:
                base.update(
                    status=evaluation.status,
                    reason_code=evaluation.reason_code or "not_triggered",
                )
            else:
                intent = intents_by_trigger.get(evaluation.trigger.trigger_id)
                if intent is None:
                    base.update(status="triggered", reason_code="entry_intent_missing")
                else:
                    reason = intent.reason_code or "entry_intent_ready"
                    base.update(
                        status=(
                            "entry_rejected"
                            if intent.status != "ready"
                            else "entry_ready"
                        ),
                        reason_code=reason,
                        opportunity_id=evaluation.trigger.trigger_id,
                        setup_id=evaluation.setup_id,
                        missed_by_existing_position=reason == "eligibility_position_blocked",
                    )
            snapshots.append(base)
    return snapshots


def replay_frozen_buy_opportunities(
    *,
    universe_items: Iterable[object],
    evidence_reader: DailyEvidenceReader,
    pool_version: str,
    knowledge_at: datetime,
    observations_by_symbol: Mapping[str, Iterable[IntradayObservation]],
    quotes_by_symbol: Mapping[str, Iterable[QuoteObservation]],
    delivered_at_by_trigger: Mapping[str, datetime] | None = None,
    ready_at_by_trigger: Mapping[str, datetime] | None = None,
    sent_at_by_trigger: Mapping[str, datetime] | None = None,
    previous_close_by_bar: Mapping[str, float | None] | None = None,
    sell_quotes_by_symbol: Mapping[str, Mapping] | None = None,
    outcome_knowledge_at_by_symbol: Mapping[str, datetime] | None = None,
    session_dates_by_symbol: Mapping[str, Sequence[date]] | None = None,
    policy: ExecutionPolicy | None = None,
    portfolio: PaperPortfolioPolicy | None = None,
    eligibility_by_symbol: Mapping[str, ExecutionEligibility] | None = None,
    initial_cash: float = 100_000.0,
    marks: Iterable[LedgerMark] = (),
    theme_by_symbol: Mapping[str, str | None] | None = None,
    price_mappings_by_symbol: Mapping[str, PriceMapping] | None = None,
    strategy_version: str = "s1_breakout_v1",
    parameter_hash: str = "unknown",
) -> FrozenReplayResult:
    """Replay frozen evidence through scan, trigger, response, execution, exit, and ledger."""

    policy = policy or ExecutionPolicy(name="q1")
    portfolio = portfolio or PaperPortfolioPolicy()
    delivered_at_by_trigger = delivered_at_by_trigger or {}
    ready_at_by_trigger = ready_at_by_trigger or {}
    sent_at_by_trigger = sent_at_by_trigger or {}
    previous_close_by_bar = previous_close_by_bar or {}
    sell_quotes_by_symbol = sell_quotes_by_symbol or {}
    outcome_knowledge_at_by_symbol = outcome_knowledge_at_by_symbol or {}
    session_dates_by_symbol = session_dates_by_symbol or {}
    eligibility_by_symbol = eligibility_by_symbol or {}
    theme_by_symbol = theme_by_symbol or {}
    price_mappings_by_symbol = price_mappings_by_symbol or {}
    universe_items = list(universe_items)
    scans, setups = scan_universe(
        universe_items=universe_items,
        evidence_reader=evidence_reader,
        pool_version=pool_version,
        knowledge_at=knowledge_at,
    )
    trigger_evaluations: list[TriggerEvaluation] = []
    intents: list[EntryIntentRecord] = []
    executions: list[ExecutionRecord] = []
    exits: list[Any] = []
    rows: list[EvaluationRow] = []
    labels: list[ForwardLabel] = []
    label_diagnostics: list[dict[str, str]] = []
    ledger_events: list[LedgerEvent] = []
    trigger_contexts: list[tuple[Any, SetupRecord, IntradayObservation, Any]] = []
    for setup in setups:
        symbol = setup.symbol
        scan_evidence = evidence_reader.load(symbol, knowledge_at=knowledge_at)
        calendar = (
            TradingSessionCalendar(
                session_dates=list(session_dates_by_symbol[symbol])
            )
            if symbol in session_dates_by_symbol
            else None
        )
        state = start_s1_lifecycle(setup)
        observations = sorted(
            _as_sequence(observations_by_symbol.get(symbol)),
            key=lambda item: (item.end_at, item.bar_id),
        )
        for observation in observations:
            state, evaluation = advance_s1_lifecycle(
                state=state,
                observation=observation,
                previous_close=previous_close_by_bar.get(observation.bar_id),
                known_at=observation.available_at,
                calendar=calendar,
            )
            trigger_evaluations.append(evaluation)
            if evaluation.trigger is None:
                continue
            trigger_contexts.append(
                (evaluation.trigger, setup, observation, scan_evidence)
            )

    def ledger_at(event_at: datetime) -> PortfolioLedger:
        return replay_portfolio_ledger(
            events=[
                event
                for event in ledger_events
                if event.event_at is not None and event.event_at <= event_at
            ],
            initial_cash=initial_cash,
            policy=portfolio,
        )

    def build_intent(
        *,
        trigger: Any,
        setup: SetupRecord,
        scan_evidence: Any,
        ledger: PortfolioLedger,
        delivered_at: datetime | None,
        ready_at: datetime | None,
    ) -> EntryIntentRecord:
        symbol = setup.symbol
        position = ledger.positions.get(symbol)
        total_notional = sum(
            item.quantity * item.entry_price
            for item in ledger.positions.values()
        )
        return build_q1_entry_intent(
            trigger=trigger,
            setup=setup,
            created_at=trigger.known_at,
            delivered_at=delivered_at,
            user_ready_at=ready_at,
            quotes=[
                convert_quote_observation(
                    quote,
                    target_basis=setup.price_basis,
                    target_mapping_id=setup.price_mapping_id,
                    mapping=price_mappings_by_symbol.get(symbol),
                )
                for quote in _as_sequence(quotes_by_symbol.get(symbol))
            ],
            nav=ledger.cash + total_notional,
            cash=ledger.cash,
            policy=policy,
            portfolio=portfolio,
            source_mode=scan_evidence.source_mode,
            price_basis=setup.price_basis,
            eligibility=eligibility_by_symbol.get(symbol),
            has_existing_position=position is not None,
            open_symbol_notional=(
                position.quantity * position.entry_price if position else 0.0
            ),
            open_total_notional=total_notional,
            open_total_risk=sum(
                item.planned_risk for item in ledger.positions.values()
            ),
            open_symbol_count=len(ledger.positions),
        )[0]

    for trigger, setup, observation, scan_evidence in sorted(
        trigger_contexts,
        key=lambda item: (item[0].known_at, item[0].trigger_id),
    ):
        symbol = setup.symbol
        delivered_at = delivered_at_by_trigger.get(trigger.trigger_id, trigger.known_at)
        ready_at = ready_at_by_trigger.get(trigger.trigger_id)
        if ready_at is None and delivered_at is not None:
            ready_at = delivered_at
        intent = build_intent(
            trigger=trigger,
            setup=setup,
            scan_evidence=scan_evidence,
            ledger=ledger_at(trigger.known_at),
            delivered_at=delivered_at,
            ready_at=ready_at,
        )
        if intent.status == "ready":
            default_sent_at = max(
                item
                for item in (intent.ready_at, intent.q0_known_at)
                if item is not None
            )
            sent_at = sent_at_by_trigger.get(trigger.trigger_id, default_sent_at)
            if sent_at is None:
                raise ValueError("ready intent 必须有 sent_at 才能 replay")
            intent = build_intent(
                trigger=trigger,
                setup=setup,
                scan_evidence=scan_evidence,
                ledger=ledger_at(sent_at),
                delivered_at=delivered_at,
                ready_at=ready_at,
            )
        intents.append(intent)
        if intent.status != "ready":
            execution = ExecutionRecord(
                execution_id=f"{intent.intent_id}:not_filled",
                intent_id=intent.intent_id,
                policy_hash=policy.policy_hash,
                source_mode=intent.source_mode,
                price_mapping_id=intent.price_mapping_id,
                fill_quantity=0,
                status="not_filled",
                reason_code=intent.reason_code or "intent_not_ready",
                content_sha256=intent.content_sha256,
            )
            executions.append(execution)
            ledger_events.append(
                LedgerEvent(
                    event_id=execution.execution_id,
                    opportunity_id=trigger.trigger_id,
                    symbol=symbol,
                    theme=theme_by_symbol.get(symbol),
                    event_at=intent.ready_at or trigger.known_at,
                    side="entry",
                    status="rejected",
                    quantity=0,
                    price=0,
                )
            )
            rows.append(
                _not_filled_row(
                    opportunity_id=trigger.trigger_id,
                    symbol=symbol,
                    entry_date=observation.end_at.date(),
                    execution=execution,
                    intent=intent,
                    policy=policy,
                    data_source=scan_evidence.data_source,
                )
            )
            continue
        sent_at = sent_at_by_trigger.get(
            trigger.trigger_id,
            max(
                item
                for item in (intent.ready_at, intent.q0_known_at)
                if item is not None
            ),
        )
        execution = execute_q1(
            intent=intent,
            setup=setup,
            q1_quotes=[
                convert_quote_observation(
                    quote,
                    target_basis=setup.price_basis,
                    target_mapping_id=setup.price_mapping_id,
                    mapping=price_mappings_by_symbol.get(symbol),
                )
                for quote in _as_sequence(quotes_by_symbol.get(symbol))
            ],
            sent_at=sent_at,
            policy=policy,
        )
        executions.append(execution)
        if execution.status != "filled":
            ledger_events.append(
                LedgerEvent(
                    event_id=execution.execution_id,
                    opportunity_id=trigger.trigger_id,
                    symbol=symbol,
                    theme=theme_by_symbol.get(symbol),
                    event_at=sent_at,
                    side="entry",
                    status="rejected",
                    quantity=0,
                    price=0,
                )
            )
            rows.append(
                _not_filled_row(
                    opportunity_id=trigger.trigger_id,
                    symbol=symbol,
                    entry_date=observation.end_at.date(),
                    execution=execution,
                    intent=intent,
                    policy=policy,
                    data_source=scan_evidence.data_source,
                )
            )
            continue
        evidence = evidence_reader.load(
            symbol,
            knowledge_at=outcome_knowledge_at_by_symbol.get(symbol, knowledge_at),
        )
        sell_quotes = {
            day: convert_quote_observation(
                quote,
                target_basis=setup.price_basis,
                target_mapping_id=setup.price_mapping_id,
                mapping=price_mappings_by_symbol.get(symbol),
            )
            for day, quote in (sell_quotes_by_symbol.get(symbol) or {}).items()
        }
        exit_record = simulate_long_exit(
            execution=execution,
            setup=setup,
            daily_bars=evidence.bars,
            sell_quotes=sell_quotes,
            policy=policy,
        )
        exits.append(exit_record)
        entry_date = execution.fill_at.date() if execution.fill_at else observation.end_at.date()
        rows.append(
            evaluation_row_from_records(
                opportunity_id=trigger.trigger_id,
                symbol=symbol,
                entry_date=entry_date,
                execution=execution,
                exit_record=exit_record,
                data_source=scan_evidence.data_source,
                response_minutes=policy.response_minutes,
                strategy_version=strategy_version,
                parameter_hash=parameter_hash,
                execution_policy=policy.name,
                exit_policy=policy.exit_mode,
                funds_policy=portfolio.policy_hash,
                user_policy=intent.user_policy,
                policy_version=policy.policy_hash,
            )
        )
        if entry_date in {bar.date for bar in evidence.bars}:
            labels.extend(
                independent_forward_labels(
                    opportunity_id=trigger.trigger_id,
                    symbol=symbol,
                    entry_date=entry_date,
                    entry_price=execution.fill_price,
                    bars=evidence.bars,
                    source_mode=scan_evidence.source_mode,
                    coverage_complete=evidence.quality_status == "ok",
                    session_dates=session_dates_by_symbol.get(symbol),
                )
            )
        else:
            label_diagnostics.append(
                {
                    "opportunity_id": trigger.trigger_id,
                    "symbol": symbol,
                    "status": "censored",
                    "reason": "entry_date_not_in_frozen_daily_evidence",
                }
            )
        ledger_events.append(
            LedgerEvent(
                event_id=execution.execution_id,
                opportunity_id=trigger.trigger_id,
                symbol=symbol,
                theme=theme_by_symbol.get(symbol),
                event_at=execution.fill_at,
                side="entry",
                status="filled",
                quantity=execution.fill_quantity,
                price=execution.fill_price,
                fee=execution.cost or 0,
                planned_risk=execution.planned_risk,
            )
        )
        if exit_record.exit_at is not None and exit_record.exit_price is not None:
            ledger_events.append(
                LedgerEvent(
                    event_id=exit_record.exit_id,
                    opportunity_id=trigger.trigger_id,
                    symbol=symbol,
                    theme=theme_by_symbol.get(symbol),
                    event_at=exit_record.exit_at,
                    side="exit",
                    status="filled",
                    quantity=exit_record.quantity,
                    price=exit_record.exit_price,
                    fee=0,
                )
            )

    ledger = replay_portfolio_ledger(
        events=ledger_events,
        initial_cash=initial_cash,
        policy=portfolio,
        marks=marks,
    )
    diagnostics = _build_diagnostic_snapshots(
        scans=scans,
        setups=setups,
        trigger_evaluations=trigger_evaluations,
        intents=intents,
        observations_by_symbol=observations_by_symbol,
        session_dates_by_symbol=session_dates_by_symbol,
    )
    result = FrozenReplayResult(
        scans=scans,
        setups=setups,
        trigger_evaluations=trigger_evaluations,
        intents=intents,
        executions=executions,
        exits=exits,
        rows=rows,
        ledger=ledger,
        labels=labels,
        label_diagnostics=label_diagnostics,
        diagnostics=diagnostics,
    )
    validate_replay_references(result)
    return result
