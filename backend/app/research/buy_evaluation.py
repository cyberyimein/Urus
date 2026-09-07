from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, time
from math import isfinite, sqrt
from random import Random
from typing import Any, Iterable, Literal, Mapping, Sequence

from pydantic import Field, field_validator, model_validator

from app.buy_opportunities.contracts import AssessmentRecord, EvidenceBar, OpportunityModel
from app.decision_harness.contracts import content_sha256


class EvaluationConfig(OpportunityModel):
    strategy_version: str = Field(min_length=1, max_length=128)
    primary_policy: str = Field(min_length=1, max_length=64)
    primary_variant: str = Field(default="s1", min_length=1, max_length=64)
    primary_source_mode: str = Field(default="unknown", min_length=1, max_length=64)
    primary_response_minutes: int = Field(default=15, ge=0, le=1440)
    primary_exit_policy: str = Field(default="unknown", min_length=1, max_length=64)
    primary_funds_policy: str = Field(default="unknown", min_length=1, max_length=64)
    primary_user_policy: str = Field(default="unknown", min_length=1, max_length=64)
    primary_parameter_hash: str = Field(default="unknown", min_length=1, max_length=128)
    baseline_name: str = Field(default="benchmark_hold", min_length=1, max_length=64)
    variants: list[str] = Field(default_factory=lambda: ["s1"])
    response_minutes: list[int] = Field(default_factory=lambda: [1, 5, 15, 30])
    cost_multipliers: list[float] = Field(default_factory=lambda: [0.0, 0.5, 1.0, 2.0])
    development_start: date
    development_end: date
    lock_start: date
    lock_end: date
    min_sample_size: int = Field(default=30, ge=1, le=100_000)
    min_date_blocks: int = Field(default=2, ge=1, le=100)
    block_days: int = Field(default=20, ge=1, le=500)
    seed: int = 0

    @field_validator("response_minutes")
    @classmethod
    def validate_response_minutes(cls, value: list[int]) -> list[int]:
        if any(item < 0 or item > 1440 for item in value):
            raise ValueError("response_minutes 必须位于 0 到 1440 分钟")
        return sorted(set(value))

    @field_validator("cost_multipliers")
    @classmethod
    def validate_cost_multipliers(cls, value: list[float]) -> list[float]:
        if any(not isfinite(item) or item < 0 for item in value):
            raise ValueError("cost_multipliers 必须是非负有限数字")
        return sorted(set(value))

    @model_validator(mode="after")
    def validate_dates_and_variants(self) -> "EvaluationConfig":
        if self.development_start > self.development_end:
            raise ValueError("development 日期范围非法")
        if self.lock_start > self.lock_end:
            raise ValueError("lock 日期范围非法")
        if len(self.variants) > 3:
            raise ValueError("主策略加单条件消融最多三个 variant")
        return self


class EvaluationRow(OpportunityModel):
    opportunity_id: str = Field(min_length=1, max_length=128)
    symbol: str = Field(min_length=1, max_length=32)
    variant: str = Field(default="s1", min_length=1, max_length=64)
    data_source: Literal["real", "synthetic", "unknown"]
    source_mode: Literal[
        "observed_pit",
        "historical_reconstruction",
        "daily_execution_proxy",
        "unknown",
    ] = "unknown"
    strategy_version: str = "unknown"
    parameter_hash: str = "unknown"
    execution_policy: str = "unknown"
    exit_policy: str = "unknown"
    funds_policy: str = "unknown"
    user_policy: str = "unknown"
    entry_date: date | None = None
    exit_date: date | None = None
    entry_price: float | None = None
    exit_price: float | None = None
    quantity: float = Field(default=0, ge=0)
    planned_risk: float | None = None
    gross_pnl: float | None = None
    net_pnl: float | None = None
    cost: float = Field(default=0, ge=0)
    status: Literal[
        "filled",
        "stop",
        "gap_stop",
        "structure_failed",
        "time_exit",
        "pending",
        "censored",
        "ambiguous",
        "not_filled",
        "cancelled",
    ]
    response_minutes: float | None = None
    benchmark_return: float | None = None
    cash_return: float = 0.0
    alert_count: int = Field(default=1, ge=0)
    rejection_codes: list[str] = Field(default_factory=list)
    missed_by_attention: bool = False
    missed_by_event: bool = False
    missed_by_existing_position: bool = False
    coverage_unknown: bool = False
    policy_version: str = "unknown"
    exit_reason: str | None = None

    @field_validator(
        "entry_price",
        "exit_price",
        "planned_risk",
        "gross_pnl",
        "net_pnl",
        "cost",
        "response_minutes",
        "benchmark_return",
        "cash_return",
    )
    @classmethod
    def validate_numbers(cls, value: float | None, info) -> float | None:
        if value is None:
            return None
        if not isfinite(value):
            raise ValueError(f"{info.field_name} 必须是有限数字")
        if info.field_name in {"entry_price", "exit_price", "planned_risk", "cost"} and value < 0:
            raise ValueError(f"{info.field_name} 不能为负")
        return value

    @model_validator(mode="after")
    def validate_row(self) -> "EvaluationRow":
        if self.status == "filled" and (
            self.entry_date is None
            or self.entry_price is None
            or self.quantity <= 0
        ):
            raise ValueError("filled evaluation row 必须包含 entry 和正数量")
        if self.exit_date is not None and self.entry_date is not None and self.exit_date < self.entry_date:
            raise ValueError("exit_date 不能早于 entry_date")
        if self.status in {"pending", "censored", "ambiguous"} and self.net_pnl is not None:
            raise ValueError("未成熟结果不能伪造 net_pnl")
        return self

    @property
    def net_r(self) -> float | None:
        if self.net_pnl is None or self.planned_risk in (None, 0):
            return None
        return self.net_pnl / self.planned_risk


class ForwardLabel(OpportunityModel):
    opportunity_id: str
    symbol: str
    horizon_sessions: int = Field(ge=1)
    status: str
    entry_date: date
    observed_date: date | None = None
    entry_price: float
    reference_price: float | None = None
    gross_return: float | None = None
    net_return: float | None = None
    mfe_return: float | None = None
    mae_return: float | None = None
    source_mode: str
    reason_code: str | None = None
    content_sha256: str = Field(min_length=64, max_length=64)


class CoverageReport(OpportunityModel):
    scan_status_counts: dict[str, int] = Field(default_factory=dict)
    rejection_counts: dict[str, int] = Field(default_factory=dict)
    missing_data_count: int = 0
    attention_miss_count: int = 0
    event_miss_count: int = 0
    existing_position_miss_count: int = 0
    coverage_unknown_count: int = 0
    source_counts: dict[str, int] = Field(default_factory=dict)


def evaluation_row_from_records(
    *,
    opportunity_id: str,
    symbol: str,
    entry_date: date,
    execution,
    exit_record,
    variant: str = "s1",
    data_source: Literal["real", "synthetic", "unknown"] = "unknown",
    response_minutes: float | None = None,
    benchmark_return: float | None = None,
    policy_version: str = "unknown",
    strategy_version: str = "unknown",
    parameter_hash: str = "unknown",
    execution_policy: str = "unknown",
    exit_policy: str = "unknown",
    funds_policy: str = "unknown",
    user_policy: str = "unknown",
) -> EvaluationRow:
    """Convert execution artifacts without changing their frozen economics."""

    if execution.status != "filled":
        return EvaluationRow(
            opportunity_id=opportunity_id,
            symbol=symbol,
            variant=variant,
            data_source=data_source,
            source_mode=execution.source_mode,
            entry_date=entry_date,
            quantity=0,
            planned_risk=execution.planned_risk,
            cost=execution.cost or 0,
            status=execution.status,
            response_minutes=response_minutes,
            benchmark_return=benchmark_return,
            policy_version=policy_version,
            strategy_version=strategy_version,
            parameter_hash=parameter_hash,
            execution_policy=execution_policy,
            exit_policy=exit_policy,
            funds_policy=funds_policy,
            user_policy=user_policy,
        )
    return EvaluationRow(
        opportunity_id=opportunity_id,
        symbol=symbol,
        variant=variant,
        data_source=data_source,
        entry_date=entry_date,
        exit_date=exit_record.exit_at.date() if exit_record.exit_at else None,
        entry_price=execution.fill_price,
        exit_price=exit_record.exit_price,
        quantity=execution.fill_quantity,
        planned_risk=execution.planned_risk,
        gross_pnl=exit_record.gross_pnl,
        net_pnl=exit_record.net_pnl,
        cost=max(
            execution.cost or 0.0,
            (
                (exit_record.gross_pnl - exit_record.net_pnl)
                if exit_record.gross_pnl is not None and exit_record.net_pnl is not None
                else 0.0
            ),
        ),
        status=exit_record.status,
        response_minutes=response_minutes,
        benchmark_return=benchmark_return,
        policy_version=policy_version,
        source_mode=execution.source_mode,
        strategy_version=strategy_version,
        parameter_hash=parameter_hash,
        execution_policy=execution_policy,
        exit_policy=exit_policy,
        funds_policy=funds_policy,
        user_policy=user_policy,
        exit_reason=exit_record.reason_code,
    )


def independent_forward_labels(
    *,
    opportunity_id: str,
    symbol: str,
    entry_date: date,
    entry_price: float,
    bars: Sequence[EvidenceBar],
    horizons: Iterable[int] = (1, 2, 5, 10, 20),
    source_mode: str = "historical_reconstruction",
    coverage_complete: bool = True,
    cost_rate: float = 0.0,
    session_dates: Sequence[date] | None = None,
) -> list[ForwardLabel]:
    """Produce independent close labels; exit logic is intentionally not consulted."""

    ordered = sorted(bars, key=lambda item: item.date)
    index_by_date = {bar.date: index for index, bar in enumerate(ordered)}
    if entry_date not in index_by_date:
        raise ValueError("entry_date 不在冻结日线中")
    expected_dates = sorted(set(session_dates or [bar.date for bar in ordered]))
    expected_index = {day: index for index, day in enumerate(expected_dates)}
    expected_entry_index = expected_index.get(entry_date)
    if expected_entry_index is None:
        raise ValueError("entry_date 不在交易日历中")
    bars_by_date = {bar.date: bar for bar in ordered}
    labels: list[ForwardLabel] = []
    for horizon in sorted(set(horizons)):
        expected_target_index = expected_entry_index + horizon
        status = "observed"
        target_date = (
            expected_dates[expected_target_index]
            if expected_target_index < len(expected_dates)
            else None
        )
        target = bars_by_date.get(target_date) if target_date else None
        reason_code = None
        if target is None:
            if target_date is not None and target_date <= expected_dates[-1]:
                status = "censored"
                reason_code = "coverage_gap"
            else:
                status = "pending" if coverage_complete else "censored"
                reason_code = "future_bar_unavailable" if coverage_complete else "coverage_gap"
        gross_return = (target.close / entry_price - 1) if target else None
        net_return = gross_return - cost_rate if gross_return is not None else None
        horizon_bars = [
            bar
            for bar in ordered
            if entry_date < bar.date <= target.date
        ] if target else []
        mfe_return = (
            max(bar.high for bar in horizon_bars) / entry_price - 1
            if horizon_bars
            else None
        )
        mae_return = (
            min(bar.low for bar in horizon_bars) / entry_price - 1
            if horizon_bars
            else None
        )
        payload = {
            "opportunity_id": opportunity_id,
            "symbol": symbol,
            "horizon_sessions": horizon,
            "status": status,
            "entry_date": entry_date.isoformat(),
            "observed_date": target.date.isoformat() if target else None,
            "entry_price": entry_price,
            "reference_price": target.close if target else None,
            "gross_return": gross_return,
            "net_return": net_return,
            "mfe_return": mfe_return,
            "mae_return": mae_return,
            "source_mode": source_mode,
            "reason_code": reason_code,
        }
        labels.append(
            ForwardLabel(
                **payload,
                content_sha256=content_sha256(payload),
            )
        )
    return labels


def synchronized_block_bootstrap(
    rows: Iterable[EvaluationRow],
    *,
    block_days: int = 20,
    samples: int = 5_000,
    seed: int = 0,
    trading_dates: Sequence[date] | None = None,
) -> dict[str, Any]:
    """Bootstrap realized P&L by contiguous trading-session blocks."""

    if block_days < 1 or samples < 1:
        raise ValueError("block_days 和 samples 必须大于 0")
    realized = [
        row
        for row in rows
        if row.net_pnl is not None and row.entry_date is not None
    ]
    dates = sorted(
        set(trading_dates or ())
        or {
            row.entry_date
            for row in realized
            if row.entry_date is not None
        }
    )
    if len(dates) < block_days:
        return {
            "status": "unknown",
            "reason": "insufficient_trading_sessions",
            "block_days": block_days,
            "observed_block_count": 0,
            "sample_count": 0,
            "seed": seed,
        }
    blocks = [
        dates[index : index + block_days]
        for index in range(0, len(dates), block_days)
        if len(dates[index : index + block_days]) == block_days
    ]
    if len(blocks) < 2:
        return {
            "status": "unknown",
            "reason": "insufficient_synchronized_blocks",
            "block_days": block_days,
            "observed_block_count": len(blocks),
            "sample_count": 0,
            "seed": seed,
        }
    pnl_by_date: Counter[date] = Counter()
    for row in realized:
        pnl_by_date[row.entry_date] += row.net_pnl or 0.0
    rng = Random(seed)
    values: list[float] = []
    for _ in range(samples):
        sampled = [blocks[rng.randrange(len(blocks))] for _ in blocks]
        values.append(
            sum(pnl_by_date[day] for block in sampled for day in block)
        )
    values.sort()
    low_index = max(0, int(samples * 0.025) - 1)
    high_index = min(samples - 1, int(samples * 0.975))
    return {
        "status": "observed",
        "block_days": block_days,
        "observed_block_count": len(blocks),
        "sample_count": samples,
        "seed": seed,
        "mean": sum(values) / len(values),
        "interval_low": values[low_index],
        "interval_high": values[high_index],
        "observed_net_pnl": sum(row.net_pnl or 0 for row in realized),
    }


def block_size_sensitivity(
    rows: Iterable[EvaluationRow],
    *,
    block_sizes: Iterable[int] = (10, 20, 40),
    samples: int = 5_000,
    seed: int = 0,
    trading_dates: Sequence[date] | None = None,
) -> dict[str, dict[str, Any]]:
    return {
        str(block_size): synchronized_block_bootstrap(
            rows,
            block_days=block_size,
            samples=samples,
            seed=seed,
            trading_dates=trading_dates,
        )
        for block_size in sorted(set(block_sizes))
    }


def response_delay_comparison(
    rows: Iterable[EvaluationRow],
    *,
    config: EvaluationConfig,
) -> dict[str, Any]:
    """Compare response policies within one frozen cohort identity."""

    required = (
        config.primary_source_mode,
        config.primary_exit_policy,
        config.primary_funds_policy,
        config.primary_user_policy,
        config.primary_parameter_hash,
    )
    if any(value == "unknown" for value in required):
        return {"status": "unknown", "reason": "primary_cohort_identity_incomplete"}
    eligible = [
        row
        for row in rows
        if row.data_source == "real"
        and row.variant == config.primary_variant
        and row.source_mode == config.primary_source_mode
        and row.strategy_version == config.strategy_version
        and row.parameter_hash == config.primary_parameter_hash
        and row.execution_policy == config.primary_policy
        and row.exit_policy == config.primary_exit_policy
        and row.funds_policy == config.primary_funds_policy
        and row.user_policy == config.primary_user_policy
        and row.entry_date is not None
        and config.lock_start <= row.entry_date <= config.lock_end
        and row.exit_date is not None
        and row.exit_date <= config.lock_end
        and row.net_pnl is not None
        and row.status not in {"pending", "censored", "ambiguous"}
    ]
    by_response = {
        str(minutes): summarize_evaluations(
            [
                row
                for row in eligible
                if row.response_minutes == minutes
            ]
        )
        for minutes in config.response_minutes
    }
    return {
        "status": "observed" if any(item["opportunity_count"] for item in by_response.values()) else "unknown",
        "responses": by_response,
        "primary_response_minutes": config.primary_response_minutes,
    }


def build_daily_nav_comparison(
    rows: Iterable[EvaluationRow],
    *,
    initial_cash: float,
    trading_dates: Sequence[date],
    benchmark_returns: Mapping[date, float] | None = None,
    cash_returns: Mapping[date, float] | None = None,
    marks_by_date: Mapping[date, Mapping[str, float]] | None = None,
    portfolio_policy: PaperPortfolioPolicy | None = None,
) -> dict[str, Any]:
    """Build strategy, benchmark, and cash NAVs on one shared date ledger."""

    if initial_cash <= 0:
        raise ValueError("initial_cash 必须大于 0")
    if not trading_dates:
        return {"status": "unknown", "reason": "missing_trading_dates", "points": []}
    from .buy_execution import PaperPortfolioPolicy
    from .portfolio_ledger import LedgerEvent, LedgerMark, replay_portfolio_ledger

    events: list[LedgerEvent] = []
    for row in rows:
        if (
            row.entry_date is None
            or row.exit_date is None
            or row.entry_price is None
            or row.exit_price is None
            or row.quantity <= 0
        ):
            continue
        event_base = row.opportunity_id
        entry_at = datetime.combine(row.entry_date, time.min, tzinfo=UTC)
        exit_at = datetime.combine(row.exit_date, time.min, tzinfo=UTC)
        entry_fee = row.cost if row.cost > 0 else 0.0
        events.extend(
            [
                LedgerEvent(
                    event_id=f"{event_base}:entry",
                    opportunity_id=event_base,
                    symbol=row.symbol,
                    event_at=entry_at,
                    side="entry",
                    status="filled",
                    quantity=row.quantity,
                    price=row.entry_price,
                    fee=entry_fee,
                    planned_risk=row.planned_risk or 0,
                ),
                LedgerEvent(
                    event_id=f"{event_base}:exit",
                    opportunity_id=event_base,
                    symbol=row.symbol,
                    event_at=exit_at,
                    side="exit",
                    status="filled",
                    quantity=row.quantity,
                    price=row.exit_price,
                    fee=0,
                    display_order=1,
                ),
            ]
        )
    policy = portfolio_policy or PaperPortfolioPolicy()
    marks = [
        LedgerMark(
            marked_at=datetime.combine(day, time.min, tzinfo=UTC),
            symbol=symbol,
            price=price,
        )
        for day, symbols in (marks_by_date or {}).items()
        for symbol, price in symbols.items()
    ]
    ledger = replay_portfolio_ledger(
        events=events,
        initial_cash=initial_cash,
        policy=policy,
        marks=marks,
    )
    strategy_by_date = {
        snapshot.marked_at.date(): snapshot.nav
        for snapshot in ledger.snapshots
    }
    benchmark_by_date: dict[date, float] = {}
    cash_by_date: dict[date, float] = {}
    benchmark_nav = initial_cash
    cash_nav = initial_cash
    benchmark_returns = benchmark_returns or {}
    cash_returns = cash_returns or {}
    for day in sorted(set(trading_dates)):
        benchmark_rate = benchmark_returns.get(day)
        if benchmark_rate is not None:
            benchmark_nav *= 1 + benchmark_rate
        cash_nav *= 1 + cash_returns.get(day, 0.0)
        benchmark_by_date[day] = benchmark_nav
        cash_by_date[day] = cash_nav
    points: list[dict[str, Any]] = []
    strategy_nav = initial_cash
    for day in sorted(set(trading_dates)):
        strategy_nav = strategy_by_date.get(day, strategy_nav)
        points.append(
            {
                "date": day.isoformat(),
                "strategy_nav": strategy_nav,
                "benchmark_nav": benchmark_by_date[day],
                "cash_nav": cash_by_date[day],
            }
        )
    benchmark_complete = all(day in benchmark_returns for day in set(trading_dates))
    return {
        "status": "observed" if benchmark_complete else "unavailable",
        "benchmark_status": "observed" if benchmark_complete else "unavailable",
        "points": points,
        "strategy_max_drawdown": ledger.max_drawdown,
    }


def summarize_evaluations(rows: Iterable[EvaluationRow]) -> dict[str, Any]:
    rows = list(rows)
    filled = [row for row in rows if row.status in {"filled", "stop", "gap_stop", "structure_failed", "time_exit"}]
    realized = [row for row in rows if row.net_pnl is not None]
    net_pnl = sum(row.net_pnl or 0 for row in realized)
    gross_pnl = sum(row.gross_pnl or 0 for row in realized)
    costs = sum(row.cost for row in rows)
    risk_values = [row.net_r for row in realized if row.net_r is not None]
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for row in sorted(realized, key=lambda item: (item.exit_date or date.max, item.opportunity_id)):
        cumulative += row.net_pnl or 0
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    occupancy_by_day: dict[date, float] = {}
    for row in filled:
        if row.entry_date is None:
            continue
        notional = (row.entry_price or 0) * row.quantity
        last_date = row.exit_date or row.entry_date
        current = row.entry_date
        while current <= last_date:
            occupancy_by_day[current] = occupancy_by_day.get(current, 0.0) + notional
            current = date.fromordinal(current.toordinal() + 1)
    response_curve: dict[str, dict[str, float | int]] = {}
    for row in rows:
        key = "unknown" if row.response_minutes is None else str(row.response_minutes)
        item = response_curve.setdefault(key, {"count": 0, "net_pnl": 0.0, "realized_count": 0})
        item["count"] += 1
        item["net_pnl"] += row.net_pnl or 0
        item["realized_count"] += int(row.net_pnl is not None)
    positive = sorted((row.net_pnl for row in realized if (row.net_pnl or 0) > 0), reverse=True)
    positive_total = sum(positive)
    concentration = (sum(positive[:5]) / positive_total) if positive_total else None
    return {
        "opportunity_count": len(rows),
        "filled_or_exited_count": len(filled),
        "realized_count": len(realized),
        "pending_count": sum(row.status == "pending" for row in rows),
        "censored_count": sum(row.status == "censored" for row in rows),
        "ambiguous_count": sum(row.status == "ambiguous" for row in rows),
        "not_filled_count": sum(row.status == "not_filled" for row in rows),
        "gross_pnl": gross_pnl,
        "net_pnl": net_pnl,
        "net_r": sum(risk_values) if risk_values else None,
        "average_net_r": (sum(risk_values) / len(risk_values)) if risk_values else None,
        "costs": costs,
        "capital_occupancy_notional": sum(
            (row.entry_price or 0) * row.quantity for row in filled
        ),
        "peak_capital_occupancy_notional": max(occupancy_by_day.values(), default=0.0),
        "max_drawdown": max_drawdown,
        "response_delay_curve": response_curve,
        "cost_pressure": (costs / abs(gross_pnl)) if gross_pnl else None,
        "top5_positive_pnl_concentration": concentration,
        "alert_count": sum(row.alert_count for row in rows),
    }


def compare_baselines(rows: Iterable[EvaluationRow]) -> dict[str, Any]:
    rows = list(rows)
    strategy = sum(row.net_pnl or 0 for row in rows)
    benchmark_values = [
        (row.entry_price or 0) * row.quantity * row.benchmark_return
        for row in rows
        if row.benchmark_return is not None
    ]
    cash = sum(
        (row.entry_price or 0) * row.quantity * row.cash_return
        for row in rows
    )
    benchmark_complete = bool(rows) and len(benchmark_values) == len(rows)
    benchmark = sum(benchmark_values) if benchmark_complete else None
    return {
        "strategy_net_pnl": strategy,
        "benchmark_hold_pnl": benchmark,
        "cash_pnl": cash,
        "excess_vs_benchmark": strategy - benchmark if benchmark is not None else None,
        "excess_vs_cash": strategy - cash,
        "benchmark_observed_count": sum(row.benchmark_return is not None for row in rows),
        "benchmark_status": "observed" if benchmark_complete else "unavailable",
    }


def cost_sensitivity(
    rows: Iterable[EvaluationRow],
    multipliers: Iterable[float] = (0.0, 0.5, 1.0, 2.0),
) -> dict[str, float]:
    rows = list(rows)
    return {
        str(multiplier): sum(
            (row.gross_pnl or 0) - row.cost * multiplier
            for row in rows
        )
        for multiplier in sorted(set(multipliers))
    }


def date_block_estimates(
    rows: Iterable[EvaluationRow],
    *,
    block_start: date,
    block_days: int = 30,
    minimum_block_size: int = 5,
) -> list[dict[str, Any]]:
    """Return preregistered calendar blocks without inventing confidence."""

    if block_days < 1:
        raise ValueError("block_days 必须大于 0")
    blocks: dict[int, list[EvaluationRow]] = {}
    for row in rows:
        if row.entry_date is None:
            continue
        block_index = max(0, (row.entry_date - block_start).days // block_days)
        blocks.setdefault(block_index, []).append(row)
    output: list[dict[str, Any]] = []
    for index in sorted(blocks):
        block_rows = blocks[index]
        start = block_start.fromordinal(block_start.toordinal() + index * block_days)
        end = start.fromordinal(start.toordinal() + block_days - 1)
        realized = [row.net_pnl for row in block_rows if row.net_pnl is not None]
        sufficient = len(realized) >= minimum_block_size
        output.append(
            {
                "block_start": start.isoformat(),
                "block_end": end.isoformat(),
                "count": len(block_rows),
                "status": "observed" if sufficient else "unknown",
                "net_pnl": sum(realized) if sufficient else None,
                "mean_net_pnl": (
                    sum(realized) / len(realized)
                    if sufficient
                    else None
                ),
                "interval_low": _normal_interval(realized, lower=True)
                if sufficient
                else None,
                "interval_high": _normal_interval(realized, lower=False)
                if sufficient
                else None,
                "interval_method": (
                    "normal_approximation"
                    if sufficient
                    else "unknown"
                ),
                "realized_count": len(realized),
            }
        )
    return output


def _normal_interval(values: Sequence[float], *, lower: bool) -> float | None:
    if not values:
        return None
    mean = sum(values) / len(values)
    if len(values) < 2:
        return mean
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    half_width = 1.96 * sqrt(variance / len(values))
    return mean - half_width if lower else mean + half_width


def compare_filter_variant(
    *,
    baseline_rows: Iterable[EvaluationRow],
    variant_rows: Iterable[EvaluationRow],
) -> dict[str, Any]:
    baseline = list(baseline_rows)
    variant = list(variant_rows)
    baseline_ids = {row.opportunity_id for row in baseline}
    variant_ids = {row.opportunity_id for row in variant}
    missed = [row for row in baseline if row.opportunity_id not in variant_ids]
    baseline_net = sum(row.net_pnl or 0 for row in baseline)
    variant_net = sum(row.net_pnl or 0 for row in variant)
    return {
        "reduced_loss": sum(max(0.0, -(row.net_pnl or 0.0)) for row in missed),
        "missed_profit": sum(max(0.0, row.net_pnl or 0) for row in missed),
        "frequency": {
            "baseline": len(baseline),
            "variant": len(variant),
            "delta": len(variant) - len(baseline),
        },
        "net_result": {
            "baseline_net_pnl": baseline_net,
            "variant_net_pnl": variant_net,
            "delta": variant_net - baseline_net,
        },
        "removed_opportunity_count": len(baseline_ids - variant_ids),
    }


def build_coverage_report(
    *,
    scans: Iterable[Mapping[str, Any]] = (),
    rows: Iterable[EvaluationRow] = (),
) -> CoverageReport:
    scans = list(scans)
    rows = list(rows)
    rejection_counts: Counter[str] = Counter()
    for scan in scans:
        rejection_counts.update(str(code) for code in scan.get("rejection_codes", []))
    return CoverageReport(
        scan_status_counts=dict(Counter(str(scan.get("status", "unknown")) for scan in scans)),
        rejection_counts=dict(rejection_counts),
        missing_data_count=sum(
            scan.get("status") in {"coverage_miss", "unavailable"} for scan in scans
        ),
        attention_miss_count=sum(row.missed_by_attention for row in rows),
        event_miss_count=sum(row.missed_by_event for row in rows),
        existing_position_miss_count=sum(row.missed_by_existing_position for row in rows),
        coverage_unknown_count=sum(row.coverage_unknown for row in rows),
        source_counts=dict(Counter(row.data_source for row in rows)),
    )


def build_diagnostic_grid(
    snapshots: Iterable[Mapping[str, Any]],
    *,
    hour: int = 10,
    minute: int = 0,
) -> list[dict[str, Any]]:
    """Keep the fixed 10:00 diagnostic grid separate from outcome labels."""

    selected = [
        dict(snapshot)
        for snapshot in snapshots
        if snapshot.get("hour") == hour and snapshot.get("minute") == minute
    ]
    return sorted(
        selected,
        key=lambda item: (str(item.get("date", "")), str(item.get("symbol", ""))),
    )


def _primary_cohort(
    rows: Iterable[EvaluationRow],
    *,
    config: EvaluationConfig,
    variant: str | None = None,
) -> list[EvaluationRow]:
    if (
        config.primary_source_mode == "unknown"
        or config.primary_exit_policy == "unknown"
        or config.primary_funds_policy == "unknown"
        or config.primary_user_policy == "unknown"
        or config.primary_parameter_hash == "unknown"
    ):
        return []
    selected: list[EvaluationRow] = []
    for row in rows:
        if row.data_source != "real":
            continue
        if row.variant != (variant or config.primary_variant):
            continue
        if row.source_mode != config.primary_source_mode:
            continue
        if row.strategy_version != config.strategy_version:
            continue
        if row.parameter_hash != config.primary_parameter_hash:
            continue
        if row.execution_policy != config.primary_policy:
            continue
        if row.exit_policy != config.primary_exit_policy:
            continue
        if row.funds_policy != config.primary_funds_policy:
            continue
        if row.user_policy != config.primary_user_policy:
            continue
        if row.response_minutes != config.primary_response_minutes:
            continue
        if row.entry_date is None or not config.lock_start <= row.entry_date <= config.lock_end:
            continue
        if row.exit_date is None or row.exit_date > config.lock_end:
            continue
        if row.net_pnl is None or row.status in {"pending", "censored", "ambiguous"}:
            continue
        selected.append(row)
    return selected


def build_assessment(
    *,
    assessment_id: str,
    manifest_hash: str,
    config: EvaluationConfig,
    rows: Iterable[EvaluationRow],
    scans: Iterable[Mapping[str, Any]] = (),
    diagnostic_snapshots: Iterable[Mapping[str, Any]] = (),
    daily_nav_comparison: Mapping[str, Any] | None = None,
) -> AssessmentRecord:
    rows = list(rows)
    source_groups = {
        source: [row for row in rows if row.data_source == source]
        for source in sorted({row.data_source for row in rows})
    }
    summaries = {source: summarize_evaluations(group) for source, group in source_groups.items()}
    primary_rows = _primary_cohort(rows, config=config)
    primary = summarize_evaluations(primary_rows)
    baseline = compare_baselines(primary_rows)
    coverage = build_coverage_report(scans=scans, rows=rows)
    primary_blocks = date_block_estimates(
        primary_rows,
        block_start=config.lock_start,
        block_days=config.block_days,
        minimum_block_size=max(1, config.min_sample_size // max(config.min_date_blocks, 1)),
    )
    observed_blocks = sum(item["status"] == "observed" for item in primary_blocks)
    sufficient_real = (
        len(primary_rows) >= config.min_sample_size
        and observed_blocks >= config.min_date_blocks
    )
    applicability = "research_only" if sufficient_real else "unknown"
    primary_variant = primary_rows
    filter_comparisons = {
        variant: compare_filter_variant(
            baseline_rows=primary_variant,
            variant_rows=_primary_cohort(rows, config=config, variant=variant),
        )
        for variant in config.variants
        if variant != config.primary_variant
    }
    benchmark_advantage = (
        baseline["excess_vs_benchmark"]
        if baseline["benchmark_status"] == "observed"
        else None
    )
    decision = {
        "default_15m_net_value": (
            "unknown"
            if not sufficient_real
            else "absolute_loss"
            if primary["net_pnl"] <= 0
            else (
                "positive_relative_to_baseline"
                if benchmark_advantage is not None and benchmark_advantage > 0
                else "not_positive"
            )
        ),
        "absolute_net_value": primary["net_pnl"] if sufficient_real else None,
        "cash_advantage": baseline["excess_vs_cash"] if sufficient_real else None,
        "benchmark_advantage": benchmark_advantage if sufficient_real else None,
        "relative_to": ["benchmark_hold", "cash"],
        "dominant_loss_source": _dominant_loss_source(primary_rows),
        "next_step": "research_only" if applicability == "unknown" else "continue_s1_research",
    }
    metrics = {
        "primary": primary,
        "by_data_source": summaries,
        "cost_sensitivity": cost_sensitivity(rows, config.cost_multipliers),
        "primary_cost_sensitivity": cost_sensitivity(
            primary_rows,
            config.cost_multipliers,
        ),
        "response_delay_comparison": response_delay_comparison(
            rows,
            config=config,
        ),
        "date_blocks": primary_blocks,
        "bootstrap_20_session": synchronized_block_bootstrap(
            primary_rows,
            block_days=config.block_days,
            samples=5_000,
            seed=config.seed,
        ),
        "block_size_sensitivity": block_size_sensitivity(
            primary_rows,
            block_sizes=(10, 20, 40),
            samples=5_000,
            seed=config.seed,
        ),
        "primary_cohort": {
            "row_count": len(primary_rows),
            "observed_block_count": observed_blocks,
            "status": "sufficient" if sufficient_real else "unknown",
            "development_rows": sum(
                1
                for row in rows
                if row.data_source == "real"
                and row.entry_date is not None
                and config.development_start <= row.entry_date <= config.development_end
            ),
        },
        "filter_comparisons": filter_comparisons,
        "coverage": coverage.model_dump(mode="json"),
        "diagnostic_10_00": build_diagnostic_grid(diagnostic_snapshots),
        "daily_nav_comparison": dict(
            daily_nav_comparison
            or {"status": "unavailable", "reason": "not_supplied"}
        ),
        "decision_page": decision,
        "evaluation_dates": {
            "development": [config.development_start.isoformat(), config.development_end.isoformat()],
            "lock": [config.lock_start.isoformat(), config.lock_end.isoformat()],
        },
        "config": config.model_dump(mode="json"),
    }
    payload = {
        "assessment_id": assessment_id,
        "manifest_hash": manifest_hash,
        "primary_policy": config.primary_policy,
        "metrics": metrics,
        "baseline": baseline,
        "applicability": applicability,
        "limitations": [
            "real 与 synthetic 数据分组展示，synthetic 不得替代真实研究结论。",
            "pending、censored、ambiguous 结果保留，不转换为盈利或亏损。",
            "样本不足时不输出盈利支持结论。",
        ],
    }
    return AssessmentRecord(
        **payload,
        content_sha256=content_sha256(payload),
    )


def _dominant_loss_source(rows: Sequence[EvaluationRow]) -> str:
    counts = Counter()
    for row in rows:
        if row.missed_by_attention:
            counts["attention"] += 1
        if row.missed_by_event:
            counts["event"] += 1
        if row.missed_by_existing_position:
            counts["existing_position"] += 1
        if row.exit_reason in {"protective_stop", "gap_through_stop"}:
            counts["exit"] += 1
        if row.response_minutes is not None and row.response_minutes > 15:
            counts["response"] += 1
        if row.net_pnl is not None and row.net_pnl < 0:
            counts["strategy"] += 1
    return counts.most_common(1)[0][0] if counts else "unknown"
