from __future__ import annotations

from datetime import datetime, timedelta
from math import isfinite
from statistics import fmean
from typing import Any

from pydantic import field_validator, model_validator

from app.decision_harness.contracts import content_sha256

from .contracts import (
    FrozenEvidence,
    OpportunityModel,
    SetupRecord,
    TriggerEvaluation,
    TriggerRecord,
)


S1_FAMILY_VERSION = "s1_breakout_v1"
S1_PARAMETERS = {
    "atr_window": 14,
    "range_window": 20,
    "base_window": 5,
    "stop_atr_offset": 0.25,
    "upper_atr_offset": 0.50,
    "min_structure_risk_atr": 0.50,
    "max_structure_risk_atr": 3.0,
    "max_valid_sessions": 5,
}


def _true_range(current: Any, previous_close: float) -> float:
    return max(
        float(current.high) - float(current.low),
        abs(float(current.high) - previous_close),
        abs(float(current.low) - previous_close),
    )


def calculate_s1_levels(evidence: FrozenEvidence) -> dict[str, float]:
    """Calculate the frozen S1 structure from completed daily bars."""

    bars = evidence.bars
    if len(bars) < 61:
        raise ValueError("S1 需要至少 61 根完整日线")
    true_ranges = [
        _true_range(bars[index], float(bars[index - 1].close))
        for index in range(len(bars) - 14, len(bars))
    ]
    atr = fmean(true_ranges)
    if atr <= 0:
        raise ValueError("S1 ATR 必须大于 0")
    upper_trigger = max(float(item.high) for item in bars[-20:])
    invalidation_stop = min(float(item.low) for item in bars[-5:]) - 0.25 * atr
    upper_limit = upper_trigger + 0.50 * atr
    risk_distance = upper_trigger - invalidation_stop
    risk_atr = risk_distance / atr
    if not 0.5 <= risk_atr <= 3.0:
        raise ValueError(f"S1 结构风险距离 {risk_atr:.4f} ATR 不在允许范围内")
    if not invalidation_stop < upper_trigger <= upper_limit:
        raise ValueError("S1 结构价格关系非法")
    return {
        "upper_trigger": round(upper_trigger, 8),
        "invalidation_stop": round(invalidation_stop, 8),
        "atr": round(atr, 8),
        "upper_limit": round(upper_limit, 8),
        "risk_atr": round(risk_atr, 8),
    }


def build_s1_setup(
    *,
    evidence: FrozenEvidence,
    origin_scan_id: str,
    stream: str = "s1_daily",
    trigger_timeframe: str = "15m",
) -> SetupRecord:
    levels = calculate_s1_levels(evidence)
    bars = evidence.bars
    latest_date = bars[-1].date
    highest_index = max(
        range(len(bars) - 20, len(bars)),
        key=lambda index: (float(bars[index].high), str(bars[index].date)),
    )
    parameter_hash = content_sha256(S1_PARAMETERS)
    identity = {
        "symbol": evidence.symbol,
        "family_version": S1_FAMILY_VERSION,
        "parameter_hash": parameter_hash,
        "benchmark_mapping": evidence.benchmark,
        "trigger_timeframe": trigger_timeframe,
        "anchor_pivot_bar_id": str(bars[highest_index].date),
        "base_start_bar_id": str(bars[-5].date),
        **levels,
        "price_basis": evidence.price_basis,
        "price_mapping_id": evidence.price_mapping_id,
        "stream": stream,
    }
    fingerprint = content_sha256(identity)
    setup_id = f"{evidence.symbol}:{S1_FAMILY_VERSION}:{fingerprint[:20]}"
    return SetupRecord(
        setup_id=setup_id,
        fingerprint=fingerprint,
        stream=stream,
        symbol=evidence.symbol,
        family_version=S1_FAMILY_VERSION,
        parameter_hash=parameter_hash,
        benchmark_mapping=evidence.benchmark,
        trigger_timeframe=trigger_timeframe,
        anchor_pivot_bar_id=str(bars[highest_index].date),
        base_start_bar_id=str(bars[-5].date),
        observed_at=latest_date,
        knowledge_at=evidence.knowledge_at,
        valid_from=latest_date + timedelta(days=1),
        valid_sessions=int(S1_PARAMETERS["max_valid_sessions"]),
        upper_trigger=levels["upper_trigger"],
        invalidation_stop=levels["invalidation_stop"],
        atr=levels["atr"],
        upper_limit=levels["upper_limit"],
        price_basis=evidence.price_basis,
        price_mapping_id=evidence.price_mapping_id,
        origin_scan_id=origin_scan_id,
        content_sha256=content_sha256(
            {
                "setup_id": setup_id,
                "fingerprint": fingerprint,
                **levels,
                "observed_at": latest_date.isoformat(),
            }
        ),
    )


class IntradayObservation(OpportunityModel):
    """A complete, point-in-time intraday bar used only for trigger decisions."""

    bar_id: str
    start_at: datetime
    end_at: datetime
    available_at: datetime
    open: float
    high: float
    low: float
    close: float
    complete: bool = True

    @field_validator("start_at", "end_at", "available_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("盘中观察时间必须包含时区")
        return value

    @field_validator("open", "high", "low", "close")
    @classmethod
    def validate_price(cls, value: float) -> float:
        if not isfinite(value) or value < 0:
            raise ValueError("盘中观察价格必须是非负有限数字")
        return value

    @model_validator(mode="after")
    def validate_observation(self) -> "IntradayObservation":
        if self.start_at >= self.end_at:
            raise ValueError("盘中观察 start_at 必须早于 end_at")
        if self.available_at < self.end_at:
            raise ValueError("盘中观察 available_at 不能早于完整 bar 结束")
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("盘中观察 OHLC 关系非法")
        return self


def evaluate_s1_trigger(
    *,
    setup: SetupRecord,
    observation: IntradayObservation,
    previous_close: float | None,
    known_at: datetime,
    session_number: int,
    crossed_while_unobserved: bool = False,
) -> TriggerEvaluation:
    """Evaluate one complete bar without reading a clock or future outcome."""

    if session_number < 1:
        raise ValueError("session_number 必须从 1 开始")
    if setup.status != "armed":
        status = (
            "expired"
            if setup.status == "expired"
            else "invalidated"
            if setup.status == "invalidated"
            else "no_trigger"
        )
        return TriggerEvaluation(
            status=status,
            setup_id=setup.setup_id,
            observed_at=observation.available_at,
            reason_code=f"setup_{setup.status}_not_active",
        )
    if session_number > setup.valid_sessions:
        return TriggerEvaluation(
            status="expired",
            setup_id=setup.setup_id,
            observed_at=observation.available_at,
            reason_code="setup_expired",
        )
    if not observation.complete:
        return TriggerEvaluation(
            status="no_trigger",
            setup_id=setup.setup_id,
            observed_at=observation.available_at,
            reason_code="incomplete_bar",
        )
    if observation.available_at > known_at:
        raise ValueError("known_at 不能早于盘中观察的 available_at")
    if observation.low <= setup.invalidation_stop:
        return TriggerEvaluation(
            status="invalidated",
            setup_id=setup.setup_id,
            observed_at=observation.available_at,
            reason_code="setup_stop_touched_before_trigger",
        )

    current_close = float(observation.close)
    if previous_close is None:
        if current_close > setup.upper_trigger:
            return _trigger_evaluation(
                setup=setup,
                observation=observation,
                known_at=known_at,
                kind="late_discovery",
                previous_close=None,
                extension_status=_extension_status(current_close, setup.upper_limit),
            )
        return TriggerEvaluation(
            status="no_trigger",
            setup_id=setup.setup_id,
            observed_at=observation.available_at,
            reason_code="no_prior_observation",
        )
    if crossed_while_unobserved and current_close > setup.upper_trigger:
        return _trigger_evaluation(
            setup=setup,
            observation=observation,
            known_at=known_at,
            kind="cross_unobserved",
            previous_close=previous_close,
            extension_status=_extension_status(current_close, setup.upper_limit),
        )
    if previous_close <= setup.upper_trigger < current_close:
        kind = "open_gap_cross" if observation.open > setup.upper_trigger else "continuous_cross"
        return _trigger_evaluation(
            setup=setup,
            observation=observation,
            known_at=known_at,
            kind=kind,
            previous_close=previous_close,
            extension_status=_extension_status(current_close, setup.upper_limit),
        )
    return TriggerEvaluation(
        status="no_trigger",
        setup_id=setup.setup_id,
        observed_at=observation.available_at,
        reason_code="price_did_not_cross",
    )


def _extension_status(current_close: float, upper_limit: float) -> str:
    return "missed_extension" if current_close > upper_limit else "within_limit"


def _trigger_evaluation(
    *,
    setup: SetupRecord,
    observation: IntradayObservation,
    known_at: datetime,
    kind: str,
    previous_close: float | None,
    extension_status: str,
) -> TriggerEvaluation:
    identity = {
        "setup_id": setup.setup_id,
        "bar_id": observation.bar_id,
        "kind": kind,
        "previous_close": previous_close,
        "current_close": observation.close,
        "extension_status": extension_status,
    }
    trigger_id = f"{setup.setup_id}:trigger:{content_sha256(identity)[:20]}"
    payload = {
        "trigger_id": trigger_id,
        "setup_id": setup.setup_id,
        "symbol": setup.symbol,
        "kind": kind,
        "observed_bar_id": observation.bar_id,
        "occurred_at": observation.end_at,
        "known_at": known_at,
        "previous_close": previous_close,
        "current_close": observation.close,
        "extension_status": extension_status,
    }
    trigger = TriggerRecord(
        **payload,
        content_sha256=content_sha256(
            {key: value.isoformat() if isinstance(value, datetime) else value for key, value in payload.items()}
        ),
    )
    return TriggerEvaluation(
        status="triggered",
        setup_id=setup.setup_id,
        observed_at=observation.available_at,
        trigger=trigger,
    )


def can_rebuild_s1_setup(
    *,
    previous_setup: SetupRecord,
    evidence: FrozenEvidence,
    completed_bars_since_previous_setup: int,
) -> bool:
    """Allow a new structure only after three new bars and a new anchor/base."""

    if completed_bars_since_previous_setup < 3:
        return False
    if not evidence.bars:
        return False
    try:
        levels = calculate_s1_levels(evidence)
    except ValueError:
        return False
    bars = evidence.bars
    highest_index = max(
        range(len(bars) - 20, len(bars)),
        key=lambda index: (float(bars[index].high), str(bars[index].date)),
    )
    new_anchor = str(bars[highest_index].date)
    new_base = str(bars[-5].date)
    return (
        new_anchor > previous_setup.anchor_pivot_bar_id
        or new_base > previous_setup.base_start_bar_id
    ) and (
        levels["upper_trigger"] != previous_setup.upper_trigger
        or levels["invalidation_stop"] != previous_setup.invalidation_stop
    )
