"""Deterministic replay of the S1 opportunity lifecycle on complete daily bars.

This module is the shared daily boundary for the scan, view and research
layers.  It deliberately consumes one already frozen :class:`FrozenEvidence`
package and an explicit :class:`TradingSessionCalendar`.  It does not read a
repository, collect data, create orders, or compute a forward outcome.

The important temporal rule is that a session's setup is built from bars
strictly before that session.  The current complete close can then be used to
observe a trigger.  A closed setup remains in the replay result after its
first trigger so a read-only view can still explain that opportunity; a new
setup is considered only after three later complete calendar sessions and a
changed anchor/base and level set.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from bisect import bisect_left, bisect_right
from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.decision_harness.contracts import content_sha256, normalise_symbol

from .breakout import build_s1_setup
from .contracts import (
    EvidenceBar,
    EvidenceReference,
    FrozenEvidence,
    OpportunityModel,
    ScanRecord,
    SetupRecord,
    TriggerEvaluation,
    TriggerRecord,
)
from .evidence import InMemoryDailyEvidenceReader
from .lifecycle import TradingSessionCalendar
from .scanner import scan_universe


SCHEMA_VERSION = "urus.buy_opportunity.daily_replay.v1"
STEP_SCHEMA_VERSION = "urus.buy_opportunity.daily_replay_step.v1"
DAILY_REBUILD_WAIT_SESSIONS = 3


class DailyReplayError(ValueError):
    """Raised when a daily replay input would weaken its time boundary."""


def _jsonable(value: Any) -> Any:
    """Convert model/date values to the canonical hash representation."""

    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="python"))
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def _digest(value: Any) -> str:
    return content_sha256(_jsonable(value))


def _aware(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DailyReplayError(f"{name} 必须包含时区")
    return value


def _setup_with_status(setup: SetupRecord, status: str) -> SetupRecord:
    payload = setup.model_dump(mode="python")
    payload["status"] = status
    payload.pop("content_sha256", None)
    payload["content_sha256"] = _digest(payload)
    return SetupRecord(**payload)


class DailyLifecycleState(OpportunityModel):
    """The daily equivalent of ``S1LifecycleState``.

    ``session_number`` is calculated from the explicit calendar.  It is never
    the number of bars seen, which would silently turn a holiday or a missing
    session into an extra opportunity window.
    """

    schema_version: Literal["urus.buy_opportunity.daily_lifecycle.v1"] = (
        "urus.buy_opportunity.daily_lifecycle.v1"
    )
    status: Literal["armed", "resolved", "invalidated", "expired", "unavailable"]
    setup_id: str | None = None
    trigger_id: str | None = None
    session_number: int | None = Field(default=None, ge=0)
    completed_sessions: int = Field(default=0, ge=0)
    last_observed_bar_id: str | None = None
    last_known_at: datetime | None = None
    reason_codes: list[str] = Field(default_factory=list)
    transition_hash: str = Field(min_length=64, max_length=64)

    @field_validator("last_known_at")
    @classmethod
    def validate_last_known_at(cls, value: datetime | None) -> datetime | None:
        return _aware(value, name="last_known_at") if value is not None else None


class DailyReplayStep(OpportunityModel):
    """One explicit calendar-session cross-section of a replay."""

    schema_version: Literal["urus.buy_opportunity.daily_replay_step.v1"] = (
        STEP_SCHEMA_VERSION
    )
    date: date
    scan: ScanRecord
    setup: SetupRecord | None = None
    trigger: TriggerRecord | None = None
    trigger_evaluation: TriggerEvaluation | None = None
    lifecycle_state: DailyLifecycleState
    next_setup: SetupRecord | None = None
    candidate_setup: SetupRecord | None = None
    observation_bar_id: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    content_sha256: str = Field(min_length=64, max_length=64)

    @property
    def session_date(self) -> date:
        """Readable alias used by callers that call the field a session date."""

        return self.date

    @property
    def state(self) -> DailyLifecycleState:
        """Short alias for the lifecycle state in a view adapter."""

        return self.lifecycle_state


class DailyReplayResult(OpportunityModel):
    """All inspectable scan/setup/trigger states from one frozen daily input."""

    schema_version: Literal["urus.buy_opportunity.daily_replay.v1"] = SCHEMA_VERSION
    symbol: str
    asset_type: Literal["equity", "etf"]
    source_mode: Literal[
        "observed_pit", "historical_reconstruction", "daily_execution_proxy"
    ]
    data_source: Literal["real", "synthetic", "unknown"]
    price_basis: Literal["raw", "adjusted"]
    price_mapping_id: str | None = None
    knowledge_at: datetime
    market_as_of: date | None = None
    calendar: TradingSessionCalendar
    scans: list[ScanRecord] = Field(default_factory=list)
    setups: list[SetupRecord] = Field(default_factory=list)
    triggers: list[TriggerRecord] = Field(default_factory=list)
    trigger_evaluations: list[TriggerEvaluation] = Field(default_factory=list)
    steps: list[DailyReplayStep] = Field(default_factory=list)
    latest_setup: SetupRecord | None = None
    latest_trigger: TriggerRecord | None = None
    inactive_setup_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    excluded_bar_dates: list[date] = Field(default_factory=list)
    content_sha256: str = Field(min_length=64, max_length=64)

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        return normalise_symbol(value)

    @field_validator("knowledge_at")
    @classmethod
    def validate_knowledge_at(cls, value: datetime) -> datetime:
        return _aware(value, name="knowledge_at")

    @model_validator(mode="after")
    def validate_references(self) -> "DailyReplayResult":
        scan_ids = {scan.scan_id for scan in self.scans}
        setup_ids = {setup.setup_id for setup in self.setups}
        trigger_ids = {trigger.trigger_id for trigger in self.triggers}
        if self.latest_setup is not None and self.latest_setup.setup_id not in setup_ids:
            raise ValueError("latest_setup 必须出现在 setups 中")
        if self.latest_trigger is not None and self.latest_trigger.trigger_id not in trigger_ids:
            raise ValueError("latest_trigger 必须出现在 triggers 中")
        if any(trigger.setup_id not in setup_ids for trigger in self.triggers):
            raise ValueError("trigger 引用了未知 setup")
        if any(setup.origin_scan_id not in scan_ids for setup in self.setups):
            raise ValueError("setup.origin_scan_id 无法解析到 scan")

        # A scan may expose a candidate setup while the lifecycle is still
        # carrying a different active setup.  Keep both objects in the
        # result's registries and reject only genuinely dangling references.
        if any(
            scan.setup_id is not None and scan.setup_id not in setup_ids
            for scan in self.scans
        ):
            raise ValueError("scan.setup_id 无法解析到 setup")
        scans_by_id = {scan.scan_id: scan for scan in self.scans}
        if any(
            scans_by_id[setup.origin_scan_id].setup_id != setup.setup_id
            for setup in self.setups
        ):
            raise ValueError("setup.origin_scan_id 与 scan.setup_id 不一致")

        for step in self.steps:
            if step.scan.scan_id not in scan_ids:
                raise ValueError("step.scan 无法解析到 scans")
            for setup in (step.setup, step.next_setup, step.candidate_setup):
                if setup is not None and setup.setup_id not in setup_ids:
                    raise ValueError("step setup 无法解析到 setups")
            if step.trigger is not None:
                if step.trigger.trigger_id not in trigger_ids:
                    raise ValueError("step.trigger 无法解析到 triggers")
                if step.trigger.setup_id not in setup_ids:
                    raise ValueError("step.trigger.setup_id 无法解析到 setup")
            if step.trigger_evaluation is not None:
                evaluation = step.trigger_evaluation
                if evaluation.setup_id not in setup_ids:
                    raise ValueError("trigger_evaluation.setup_id 无法解析到 setup")
                if evaluation.trigger is not None:
                    if evaluation.trigger.trigger_id not in trigger_ids:
                        raise ValueError("trigger_evaluation.trigger 无法解析到 triggers")
                    if evaluation.trigger.setup_id != evaluation.setup_id:
                        raise ValueError("trigger_evaluation.trigger 与 setup 不一致")
            state = step.lifecycle_state
            if state.setup_id is not None and state.setup_id not in setup_ids:
                raise ValueError("lifecycle_state.setup_id 无法解析到 setup")
            if state.trigger_id is not None and state.trigger_id not in trigger_ids:
                raise ValueError("lifecycle_state.trigger_id 无法解析到 trigger")
        return self

    @property
    def latest_step(self) -> DailyReplayStep | None:
        return self.steps[-1] if self.steps else None

    @property
    def latest_opportunity(self) -> tuple[SetupRecord | None, TriggerRecord | None]:
        """Return the latest economic setup/trigger without dropping resolved ones."""

        return self.latest_setup, self.latest_trigger

    @property
    def setup_by_id(self) -> dict[str, SetupRecord]:
        return {setup.setup_id: setup for setup in self.setups}

    def step_for_date(self, value: date) -> DailyReplayStep | None:
        for step in self.steps:
            if step.date == value:
                return step
        return None

    # These aliases make the result convenient for both the view and research
    # adapters without creating a second replay implementation.
    @property
    def frames(self) -> list[DailyReplayStep]:
        return self.steps

    @property
    def lifecycle(self) -> list[DailyLifecycleState]:
        return [step.lifecycle_state for step in self.steps]


def _evidence_window(
    evidence: FrozenEvidence,
    bars: Sequence[EvidenceBar],
    *,
    knowledge_at: datetime,
) -> FrozenEvidence:
    """Build a smaller frozen window without changing provenance."""

    selected_dates = {bar.date for bar in bars}
    refs = [item for item in evidence.source_manifest if item.bar_date in selected_dates]
    quality = evidence.quality_status if bars else "unavailable"
    trading_date = bars[-1].date if bars else None
    return FrozenEvidence(
        symbol=evidence.symbol,
        asset_type=evidence.asset_type,
        benchmark=evidence.benchmark,
        source_mode=evidence.source_mode,
        data_source=evidence.data_source,
        price_basis=evidence.price_basis,
        price_mapping_id=evidence.price_mapping_id,
        knowledge_at=knowledge_at,
        trading_date=trading_date,
        quality_status=quality,
        warnings=list(evidence.warnings),
        bars=list(bars),
        source_manifest=refs,
    )


def _rebind_daily_setup_scan(
    scan: ScanRecord,
    setup: SetupRecord,
) -> tuple[ScanRecord, SetupRecord]:
    """Replace scanner's default 15m setup identity with the daily identity."""

    payload = scan.model_dump(mode="python")
    payload["setup_id"] = setup.setup_id
    features = dict(payload.get("features") or {})
    features.update(
        {
            "upper_trigger": setup.upper_trigger,
            "invalidation_stop": setup.invalidation_stop,
            "atr": setup.atr,
            "upper_limit": setup.upper_limit,
            "trigger_timeframe": "daily",
        }
    )
    payload["features"] = features
    digest_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"scan_id", "content_sha256"}
    }
    digest = _digest(digest_payload)
    payload["scan_id"] = f"{setup.symbol}:scan:{digest[:20]}"
    payload["content_sha256"] = digest
    rebound_scan = ScanRecord(**payload)

    setup_payload = setup.model_dump(mode="python")
    setup_payload["origin_scan_id"] = rebound_scan.scan_id
    setup_payload.pop("content_sha256", None)
    setup_payload["content_sha256"] = _digest(setup_payload)
    return rebound_scan, SetupRecord(**setup_payload)


def _scan_candidate(
    *,
    evidence: FrozenEvidence,
    bars: Sequence[EvidenceBar],
    pool_version: str,
    knowledge_at: datetime,
    universe_item: object | None = None,
    include_scan_manifests: bool = True,
) -> tuple[ScanRecord, SetupRecord | None]:
    """Run the existing eligibility scan and build a daily setup if eligible."""

    bounded_bars = [
        bar
        for bar in bars
        if bar.available_at <= knowledge_at
        and bar.date <= knowledge_at.date()
        and (bar.session_close_at is None or bar.session_close_at <= knowledge_at)
    ]
    window = _evidence_window(evidence, bounded_bars, knowledge_at=knowledge_at)
    reader = _ReplayEvidenceReader(window)
    scan_item = universe_item or {
        "symbol": evidence.symbol,
        "asset_type": evidence.asset_type,
        "enabled": True,
        "collection": {"daily_history": True},
    }
    scans, _ = scan_universe(
        universe_items=[scan_item],
        evidence_reader=reader,
        pool_version=pool_version,
        knowledge_at=knowledge_at,
        source_mode=evidence.source_mode,
        include_source_manifest=include_scan_manifests,
    )
    scan = scans[0]
    if scan.status != "eligible":
        return scan, None
    try:
        daily_setup = build_s1_setup(
            evidence=window,
            origin_scan_id="pending",
            stream="s1_daily",
            trigger_timeframe="daily",
        )
    except ValueError:
        # The scanner and setup use the same structure formula.  Retaining a
        # rejected scan is safer than manufacturing a partially valid setup if
        # a future scanner policy changes independently.
        failed_payload = scan.model_dump(mode="python")
        failed_payload.update(
            {
                "status": "rejected",
                "setup_id": None,
                "rejection_codes": ["daily_setup_build_failed"],
            }
        )
        failed_payload.pop("content_sha256", None)
        failed_payload["content_sha256"] = _digest(failed_payload)
        return ScanRecord(**failed_payload), None
    return _rebind_daily_setup_scan(scan, daily_setup)


class _ReplayEvidenceReader:
    """Reuse the validated window within one scan, without re-freezing its bars.

    _scan_candidate already filters visibility and constructs FrozenEvidence,
    which validates the selected bars and their manifest. The scanner can read
    that exact window directly when its manifest is already canonical. Legacy
    or noncanonical references use the normal reader to rebuild them from bars.
    """

    def __init__(self, evidence: FrozenEvidence):
        self.evidence = evidence
        # FrozenEvidence validates references by date, allowing a different
        # order, duplicates, or a session_id different from the bar. The normal
        # reader rebuilds one reference per bar in bar order; only bypass it
        # when that reconstruction would preserve the existing manifest.
        self._canonical_manifest = (
            len(evidence.source_manifest) == len(evidence.bars)
            and all(
                ref.bar_date == bar.date and ref.session_id == bar.session_id
                for ref, bar in zip(evidence.source_manifest, evidence.bars)
            )
        )

    def load(self, symbol: str, *, knowledge_at: datetime) -> FrozenEvidence:
        evidence = self.evidence
        if (
            normalise_symbol(symbol) == evidence.symbol
            and knowledge_at == evidence.knowledge_at
            and self._canonical_manifest
            and (evidence.bars or evidence.quality_status in {"unavailable", "failed"})
            and len(evidence.warnings) == len(set(evidence.warnings))
        ):
            return evidence
        return InMemoryDailyEvidenceReader({evidence.symbol: evidence}).load(
            symbol, knowledge_at=knowledge_at
        )


def _trigger_record(
    *,
    setup: SetupRecord,
    current: EvidenceBar,
    previous_close: float | None,
    kind: str,
    known_at: datetime,
    extension_status: str,
) -> TriggerRecord:
    identity = {
        "setup_id": setup.setup_id,
        "observed_bar_id": str(current.date),
        "kind": kind,
        "previous_close": previous_close,
        "current_close": current.close,
        "extension_status": extension_status,
    }
    trigger_id = f"{setup.setup_id}:trigger:{_digest(identity)[:20]}"
    payload = {
        "trigger_id": trigger_id,
        "setup_id": setup.setup_id,
        "symbol": setup.symbol,
        "kind": kind,
        "observed_bar_id": str(current.date),
        "occurred_at": current.session_close_at or current.available_at,
        "known_at": known_at,
        "previous_close": previous_close,
        "current_close": current.close,
        "extension_status": extension_status,
    }
    return TriggerRecord(
        **payload,
        content_sha256=_digest(payload),
    )


def _daily_trigger_evaluation(
    *,
    setup: SetupRecord,
    current: EvidenceBar,
    previous_close: float | None,
    has_prior_calendar_session: bool,
    session_number: int,
    known_at: datetime,
) -> TriggerEvaluation:
    """Evaluate one complete daily close using only prior close and this bar."""

    if session_number < 1:
        raise DailyReplayError("session_number 必须从 1 开始")
    if current.available_at > known_at:
        raise DailyReplayError("daily bar available_at 不能晚于 replay knowledge_at")
    if session_number > setup.valid_sessions:
        return TriggerEvaluation(
            status="expired",
            setup_id=setup.setup_id,
            observed_at=current.available_at,
            reason_code="setup_expired",
        )
    if current.low <= setup.invalidation_stop:
        return TriggerEvaluation(
            status="invalidated",
            setup_id=setup.setup_id,
            observed_at=current.available_at,
            reason_code="setup_stop_touched_before_trigger",
        )

    current_close = float(current.close)
    crossed = (
        current_close > setup.upper_trigger
        if previous_close is None
        else previous_close <= setup.upper_trigger < current_close
    )
    if not crossed:
        if session_number >= setup.valid_sessions:
            return TriggerEvaluation(
                status="expired",
                setup_id=setup.setup_id,
                observed_at=current.available_at,
                reason_code="setup_expired_after_final_session",
            )
        return TriggerEvaluation(
            status="no_trigger",
            setup_id=setup.setup_id,
            observed_at=current.available_at,
            reason_code=(
                "no_prior_observation"
                if previous_close is None
                else "price_did_not_cross"
            ),
        )

    if previous_close is None:
        kind = "cross_unobserved" if has_prior_calendar_session else "late_discovery"
    else:
        kind = "open_gap_cross" if current.open > setup.upper_trigger else "continuous_cross"
    extension_status = (
        "missed_extension"
        if current_close > setup.upper_limit
        else "within_limit"
    )
    trigger = _trigger_record(
        setup=setup,
        current=current,
        previous_close=previous_close,
        kind=kind,
        known_at=known_at,
        extension_status=extension_status,
    )
    return TriggerEvaluation(
        status="triggered",
        setup_id=setup.setup_id,
        observed_at=current.available_at,
        trigger=trigger,
    )


def _previous_calendar_close(
    *,
    session_date: date,
    calendar: TradingSessionCalendar,
    bars_by_date: Mapping[date, EvidenceBar],
) -> tuple[float | None, bool]:
    """Return only the immediately prior calendar session's close.

    A missing bar for that session is deliberately represented as ``None``;
    using the last available bar would hide a session gap and turn a
    cross-unobserved event into a false continuous cross.
    """

    prior_dates = [item for item in calendar.session_dates if item < session_date]
    if not prior_dates:
        return None, False
    prior_bar = bars_by_date.get(prior_dates[-1])
    return (float(prior_bar.close), True) if prior_bar is not None else (None, True)


def _count_complete_sessions_after(
    *,
    closed_date: date,
    through_date: date,
    calendar: TradingSessionCalendar,
    bars_by_date: Mapping[date, EvidenceBar],
) -> int:
    return sum(
        1
        for item in calendar.session_dates
        if closed_date < item <= through_date and item in bars_by_date
    )


def _can_rebuild_daily_setup(
    *,
    previous_setup: SetupRecord,
    candidate: SetupRecord,
    completed_sessions_since_close: int,
    closed_date: date,
) -> bool:
    """Apply the explicit three-session/new-structure re-arm rule."""

    if completed_sessions_since_close < DAILY_REBUILD_WAIT_SESSIONS:
        return False
    if candidate.setup_id == previous_setup.setup_id:
        return False
    try:
        candidate_anchor = date.fromisoformat(candidate.anchor_pivot_bar_id)
        candidate_base = date.fromisoformat(candidate.base_start_bar_id)
    except ValueError:
        # S1 currently uses ISO dates as bar IDs.  If a future bar identity is
        # not date-addressable, do not claim that it was observed after the
        # closed structure.
        return False
    if candidate_anchor <= closed_date and candidate_base <= closed_date:
        return False
    new_anchor_or_base = (
        candidate.anchor_pivot_bar_id > previous_setup.anchor_pivot_bar_id
        or candidate.base_start_bar_id > previous_setup.base_start_bar_id
    )
    new_levels = (
        candidate.upper_trigger != previous_setup.upper_trigger
        or candidate.invalidation_stop != previous_setup.invalidation_stop
    )
    return new_anchor_or_base and new_levels


def _lifecycle_state(
    *,
    setup: SetupRecord | None,
    trigger: TriggerRecord | None,
    session_number: int | None,
    completed_sessions: int,
    current: EvidenceBar | None,
    reasons: Sequence[str],
) -> DailyLifecycleState:
    status = setup.status if setup is not None else "unavailable"
    transition = {
        "status": status,
        "setup_id": setup.setup_id if setup else None,
        "trigger_id": trigger.trigger_id if trigger else None,
        "session_number": session_number,
        "completed_sessions": completed_sessions,
        "bar_id": str(current.date) if current else None,
        "known_at": current.available_at if current else None,
        "reason_codes": list(reasons),
    }
    return DailyLifecycleState(
        status=status,
        setup_id=setup.setup_id if setup else None,
        trigger_id=trigger.trigger_id if trigger else None,
        session_number=session_number,
        completed_sessions=completed_sessions,
        last_observed_bar_id=str(current.date) if current else None,
        last_known_at=current.available_at if current else None,
        reason_codes=list(reasons),
        transition_hash=_digest(transition),
    )


def _register_setup(
    setup_by_id: dict[str, SetupRecord],
    setup_order: list[str],
    setup: SetupRecord,
) -> None:
    if setup.setup_id not in setup_by_id:
        setup_order.append(setup.setup_id)
    setup_by_id[setup.setup_id] = setup


def _merge_setup_history(
    setup_by_id: dict[str, SetupRecord],
    setup: SetupRecord,
) -> None:
    """Merge a later lifecycle status without moving a setup's origin scan."""

    existing = setup_by_id.get(setup.setup_id)
    if existing is None:
        setup_by_id[setup.setup_id] = setup
        return
    if existing.origin_scan_id == setup.origin_scan_id:
        setup_by_id[setup.setup_id] = setup
        return
    payload = setup.model_dump(mode="python")
    payload["origin_scan_id"] = existing.origin_scan_id
    payload.pop("content_sha256", None)
    payload["content_sha256"] = _digest(payload)
    setup_by_id[setup.setup_id] = SetupRecord(**payload)


def _register_trigger(
    trigger_by_id: dict[str, TriggerRecord],
    trigger_order: list[str],
    trigger: TriggerRecord,
) -> None:
    if trigger.trigger_id not in trigger_by_id:
        trigger_order.append(trigger.trigger_id)
    trigger_by_id[trigger.trigger_id] = trigger


def _register_scan(
    scan_by_id: dict[str, ScanRecord],
    scan_order: list[str],
    scan: ScanRecord,
) -> None:
    if scan.scan_id not in scan_by_id:
        scan_order.append(scan.scan_id)
    scan_by_id[scan.scan_id] = scan


def replay_daily_opportunities(
    evidence: FrozenEvidence,
    calendar: TradingSessionCalendar,
    *,
    pool_version: str = "daily_replay",
    knowledge_at: datetime | None = None,
    universe_item: object | None = None,
    include_scan_manifests: bool = True,
) -> DailyReplayResult:
    """Replay one frozen symbol through every visible calendar session.

    ``knowledge_at`` is optional only to replay a stricter historical prefix of
    the supplied evidence.  It cannot move the cutoff into the future.  The
    default is ``evidence.knowledge_at``.  No calendar is inferred from bars.
    """

    if not isinstance(evidence, FrozenEvidence):
        raise TypeError("daily replay 需要 FrozenEvidence")
    if not isinstance(calendar, TradingSessionCalendar):
        raise TypeError("daily replay 需要显式 TradingSessionCalendar")
    if not pool_version or not str(pool_version).strip():
        raise DailyReplayError("pool_version 不能为空")

    cutoff = _aware(knowledge_at or evidence.knowledge_at, name="knowledge_at")
    if cutoff > evidence.knowledge_at:
        raise DailyReplayError("replay knowledge_at 不能晚于 FrozenEvidence.knowledge_at")

    calendar_dates = set(calendar.session_dates)
    visible_bars = [
        bar
        for bar in evidence.bars
        if bar.available_at <= cutoff
        and bar.date <= cutoff.date()
        and (bar.session_close_at is None or bar.session_close_at <= cutoff)
    ]
    visible_bars.sort(key=lambda item: item.date)
    excluded_dates = sorted({bar.date for bar in visible_bars if bar.date not in calendar_dates})
    bars_by_date: dict[date, EvidenceBar] = {
        bar.date: bar for bar in visible_bars if bar.date in calendar_dates
    }
    if len(bars_by_date) != len([bar for bar in visible_bars if bar.date in calendar_dates]):
        raise DailyReplayError("冻结证据包含重复 session 日期")

    market_as_of = max(bars_by_date) if bars_by_date else None
    if evidence.trading_date is not None:
        market_as_of = (
            min(market_as_of, evidence.trading_date)
            if market_as_of is not None
            else None
        )
    session_dates = [
        item for item in calendar.session_dates
        if market_as_of is not None and item <= market_as_of
    ]

    replay_evidence = _evidence_window(
        evidence,
        list(bars_by_date.values()),
        knowledge_at=cutoff,
    )
    warnings = list(evidence.warnings)
    if excluded_dates:
        warnings.append("bar_outside_explicit_calendar_excluded")
    if evidence.source_mode == "historical_reconstruction":
        warnings.append("historical_reconstruction_uses_frozen_cutoff")
    if any(bar.session_close_at is None for bar in bars_by_date.values()):
        warnings.append("session_close_boundary_not_recorded_for_some_bars")

    active_setup: SetupRecord | None = None
    active_trigger: TriggerRecord | None = None
    closed_date: date | None = None
    setup_by_id: dict[str, SetupRecord] = {}
    setup_order: list[str] = []
    trigger_by_id: dict[str, TriggerRecord] = {}
    trigger_order: list[str] = []
    scan_by_id: dict[str, ScanRecord] = {}
    scan_order: list[str] = []
    evaluations: list[TriggerEvaluation] = []
    steps: list[DailyReplayStep] = []
    ordered_bars = list(bars_by_date.values())
    ordered_dates = [bar.date for bar in ordered_bars]
    cached_prefix_end = -1
    cached_candidate: tuple[ScanRecord, SetupRecord | None] | None = None

    def scan_prefix(end: int) -> tuple[ScanRecord, SetupRecord | None]:
        nonlocal cached_prefix_end, cached_candidate
        # A closing scan and the next session's opening scan often consume
        # the same prefix. Retain one result, never every historical window.
        if end != cached_prefix_end:
            cached_candidate = _scan_candidate(
                evidence=replay_evidence,
                bars=ordered_bars[:end],
                pool_version=pool_version,
                knowledge_at=cutoff,
                universe_item=universe_item,
                include_scan_manifests=include_scan_manifests,
            )
            cached_prefix_end = end
        assert cached_candidate is not None
        return cached_candidate

    for session_date in session_dates:
        prior_end = bisect_left(ordered_dates, session_date)
        scan, candidate = scan_prefix(prior_end)
        _register_scan(scan_by_id, scan_order, scan)
        # Keep a candidate that is not yet allowed to replace the active
        # lifecycle.  It is an explicit, inspectable scan candidate rather
        # than a dangling setup_id on the step's scan.
        if candidate is not None and candidate.setup_id not in setup_by_id:
            _register_setup(setup_by_id, setup_order, candidate)

        # An initial setup is usable on this session because it was formed from
        # the previous session's close.  A closed setup can be replaced at the
        # beginning of a later session only after three complete sessions.
        if active_setup is None and candidate is not None:
            active_setup = candidate
            active_trigger = None
            closed_date = None
            _register_setup(setup_by_id, setup_order, active_setup)
        elif (
            active_setup is not None
            and active_setup.status != "armed"
            and closed_date is not None
            and candidate is not None
            and _can_rebuild_daily_setup(
                previous_setup=active_setup,
                candidate=candidate,
                completed_sessions_since_close=_count_complete_sessions_after(
                    closed_date=closed_date,
                    through_date=(
                        ordered_dates[prior_end - 1] if prior_end else closed_date
                    ),
                    calendar=calendar,
                    bars_by_date=bars_by_date,
                ),
                closed_date=closed_date,
            )
        ):
            active_setup = candidate
            active_trigger = None
            closed_date = None
            _register_setup(setup_by_id, setup_order, active_setup)

        current = bars_by_date.get(session_date)
        evaluation: TriggerEvaluation | None = None
        reasons: list[str] = []
        session_number: int | None = None
        completed_sessions = 0
        frame_scan = scan

        if current is None:
            reasons.append("missing_complete_session_bar")
        elif active_setup is not None and active_setup.status == "armed":
            session_number = calendar.session_number(
                valid_from=active_setup.valid_from,
                observed_date=session_date,
            )
            if session_number is None:
                reasons.append("session_not_in_setup_window")
            else:
                previous_close, has_prior_calendar_session = _previous_calendar_close(
                    session_date=session_date,
                    calendar=calendar,
                    bars_by_date=bars_by_date,
                )
                evaluation = _daily_trigger_evaluation(
                    setup=active_setup,
                    current=current,
                    previous_close=previous_close,
                    has_prior_calendar_session=has_prior_calendar_session,
                    session_number=session_number,
                    known_at=cutoff,
                )
                evaluations.append(evaluation)
                if evaluation.status == "triggered" and evaluation.trigger is not None:
                    active_trigger = evaluation.trigger
                    _register_trigger(trigger_by_id, trigger_order, active_trigger)
                    active_setup = _setup_with_status(active_setup, "resolved")
                    closed_date = session_date
                    _register_setup(setup_by_id, setup_order, active_setup)
                elif evaluation.status == "invalidated":
                    active_setup = _setup_with_status(active_setup, "invalidated")
                    closed_date = session_date
                    _register_setup(setup_by_id, setup_order, active_setup)
                elif evaluation.status == "expired":
                    active_setup = _setup_with_status(active_setup, "expired")
                    closed_date = session_date
                    _register_setup(setup_by_id, setup_order, active_setup)
                else:
                    completed_sessions = session_number

        # A setup formed after today's close is eligible for the next session.
        # This makes the third complete post-close session count immediately,
        # while still preventing today's bar from triggering that new setup.
        next_setup: SetupRecord | None = None
        unactivated_candidate: SetupRecord | None = (
            candidate
            if candidate is not None
            and (active_setup is None or candidate.setup_id != active_setup.setup_id)
            else None
        )
        if (
            current is not None
            and active_setup is not None
            and active_setup.status != "armed"
            and closed_date is not None
        ):
            # The start-of-session scan may be rejected even though today's
            # close creates a new valid structure; build the post-close scan
            # only for this already closed lifecycle boundary.
            end_scan, end_candidate = scan_prefix(bisect_right(ordered_dates, session_date))
            _register_scan(scan_by_id, scan_order, end_scan)
            if end_candidate is not None and end_candidate.setup_id not in setup_by_id:
                _register_setup(setup_by_id, setup_order, end_candidate)
            if end_candidate is not None and _can_rebuild_daily_setup(
                previous_setup=active_setup,
                candidate=end_candidate,
                completed_sessions_since_close=_count_complete_sessions_after(
                    closed_date=closed_date,
                    through_date=session_date,
                    calendar=calendar,
                    bars_by_date=bars_by_date,
                ),
                closed_date=closed_date,
            ):
                next_setup = end_candidate
                active_setup = next_setup
                active_trigger = None
                closed_date = None
                _register_setup(setup_by_id, setup_order, active_setup)
                frame_scan = end_scan
                unactivated_candidate = None
            elif end_candidate is not None:
                unactivated_candidate = end_candidate

        if active_setup is not None:
            completed_sessions = max(
                completed_sessions,
                _count_complete_sessions_after(
                    closed_date=active_setup.observed_at,
                    through_date=session_date,
                    calendar=calendar,
                    bars_by_date=bars_by_date,
                )
                if active_setup.status == "armed"
                else 0,
            )
        if scan.status != "eligible":
            reasons.extend(scan.rejection_codes or ["scan_not_eligible"])
        if current is None and active_setup is not None:
            reasons.append("lifecycle_not_advanced_without_complete_bar")
        reasons = list(dict.fromkeys(reasons))

        lifecycle = _lifecycle_state(
            setup=active_setup,
            trigger=active_trigger,
            session_number=session_number,
            completed_sessions=completed_sessions,
            current=current,
            reasons=reasons,
        )
        step_payload: dict[str, Any] = {
            "date": session_date,
            "scan": frame_scan,
            "setup": active_setup,
            "trigger": active_trigger,
            "trigger_evaluation": evaluation,
            "lifecycle_state": lifecycle,
            "next_setup": next_setup,
            "candidate_setup": unactivated_candidate,
            "observation_bar_id": str(current.date) if current else None,
            "reason_codes": reasons,
        }
        step_payload["content_sha256"] = _digest(step_payload)
        steps.append(DailyReplayStep(**step_payload))

    setups = [setup_by_id[item] for item in setup_order]
    triggers = [trigger_by_id[item] for item in trigger_order]
    scans = [scan_by_id[item] for item in scan_order]
    latest_setup = active_setup
    latest_trigger = active_trigger
    active_setup_ids = {
        step.setup.setup_id
        for step in steps
        if step.setup is not None
    }
    active_setup_ids.update(
        step.next_setup.setup_id
        for step in steps
        if step.next_setup is not None
    )
    inactive_setup_ids = [
        item
        for item in setup_order
        if item not in active_setup_ids
    ]
    result_payload: dict[str, Any] = {
        "symbol": evidence.symbol,
        "asset_type": evidence.asset_type,
        "source_mode": evidence.source_mode,
        "data_source": evidence.data_source,
        "price_basis": evidence.price_basis,
        "price_mapping_id": evidence.price_mapping_id,
        "knowledge_at": cutoff,
        "market_as_of": market_as_of,
        "calendar": calendar,
        "scans": scans,
        "setups": setups,
        "triggers": triggers,
        "trigger_evaluations": evaluations,
        "steps": steps,
        "latest_setup": latest_setup,
        "latest_trigger": latest_trigger,
        "inactive_setup_ids": inactive_setup_ids,
        "warnings": warnings,
        "excluded_bar_dates": excluded_dates,
    }
    result_payload["content_sha256"] = _digest(result_payload)
    return DailyReplayResult(**result_payload)


def _item_value(item: object, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _coverage_scan(
    *,
    symbol: str,
    asset_type: str,
    pool_version: str,
    knowledge_at: datetime,
    source_mode: str = "historical_reconstruction",
    price_basis: str = "adjusted",
    reason: str = "coverage_miss",
) -> ScanRecord:
    """Retain a full-pool scan row even when its frozen evidence is absent."""

    normalized = normalise_symbol(symbol)
    payload: dict[str, Any] = {
        "pool_version": pool_version,
        "knowledge_at": knowledge_at,
        "symbol": normalized,
        "asset_type": asset_type if asset_type in {"market", "equity", "etf"} else "equity",
        "source_mode": source_mode,
        "price_basis": price_basis if price_basis in {"raw", "adjusted"} else "adjusted",
        "adjustment": "QFQ",
        "status": "coverage_miss",
        "bar_count": 0,
        "covered_through": None,
        "source_manifest": [],
        "eligibility_by_dimension": {"coverage": False},
        "features": {},
        "rejection_codes": [reason],
        "setup_id": None,
    }
    digest = _digest(payload)
    return ScanRecord(
        scan_id=f"{normalized}:scan:{digest[:20]}",
        content_sha256=digest,
        **payload,
    )


def _normalise_calendar(
    calendars: Mapping[str, TradingSessionCalendar | Sequence[date]],
    symbol: str,
) -> TradingSessionCalendar:
    raw = calendars.get(symbol)
    if raw is None:
        raise DailyReplayError(f"{symbol} 缺少显式 TradingSessionCalendar")
    if isinstance(raw, TradingSessionCalendar):
        return raw
    try:
        return TradingSessionCalendar(session_dates=list(raw))
    except (TypeError, ValueError) as exc:
        raise DailyReplayError(f"{symbol} 的交易日历非法") from exc


def _normalise_view_day(value: date) -> date:
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, date):
        raise DailyReplayError("view_days 必须是 date 序列")
    return value


def _view_evidence(
    evidence: FrozenEvidence,
    *,
    market_as_of: date | None,
    knowledge_at: datetime,
    calendar: TradingSessionCalendar | None = None,
) -> FrozenEvidence:
    if market_as_of is None:
        selected: list[EvidenceBar] = []
    else:
        calendar_dates = set(calendar.session_dates) if calendar is not None else None
        selected = [
            bar
            for bar in evidence.bars
            if bar.date <= market_as_of
            and (calendar_dates is None or bar.date in calendar_dates)
            and bar.available_at <= knowledge_at
            and (bar.session_close_at is None or bar.session_close_at <= knowledge_at)
        ]
    return _evidence_window(evidence, selected, knowledge_at=knowledge_at)


def replay_daily_views(
    *,
    universe_items: Sequence[object],
    evidence_by_symbol: Mapping[str, FrozenEvidence],
    calendars: Mapping[str, TradingSessionCalendar | Sequence[date]],
    view_days: Sequence[date],
    view_times: Mapping[date, datetime],
    pool_version: str,
    knowledge_at: datetime,
    include_scan_manifests: bool = True,
) -> dict[str, Any]:
    """Replay every requested symbol and return view-ready daily rows.

    The wrapper is intentionally the integration contract shared by R02, R03
    and R04.  It replays each requested view at ``min(viewed_at,
    knowledge_at)`` so a later view cannot make a historical setup see bars
    that were unavailable at its own knowledge cutoff.  Each row contains the
    exact scan/setup/trigger objects and an evidence prefix for the projector.
    """

    cutoff = _aware(knowledge_at, name="knowledge_at")
    normalized_evidence = {
        normalise_symbol(str(symbol)): value
        for symbol, value in evidence_by_symbol.items()
    }
    if any(not isinstance(value, FrozenEvidence) for value in normalized_evidence.values()):
        raise TypeError("evidence_by_symbol 的值必须是 FrozenEvidence")

    raw_calendars = {
        normalise_symbol(str(symbol)): value
        for symbol, value in calendars.items()
    }
    normalized_calendars: dict[str, TradingSessionCalendar] = {}
    for symbol in normalized_evidence:
        normalized_calendars[symbol] = _normalise_calendar(raw_calendars, symbol)

    normalized_days = sorted({_normalise_view_day(item) for item in view_days})
    normalized_times: dict[date, datetime] = {}
    for raw_day, raw_time in view_times.items():
        day = _normalise_view_day(raw_day)
        normalized_times[day] = _aware(raw_time, name="viewed_at")
        if normalized_times[day].date() != day:
            raise DailyReplayError("view_times 的 datetime 日期必须与 view_days 一致")
    if any(day not in normalized_times for day in normalized_days):
        raise DailyReplayError("每个 view_day 都必须提供 viewed_at")
    scan_by_id: dict[str, ScanRecord] = {}
    setup_by_id: dict[str, SetupRecord] = {}
    trigger_by_id: dict[str, TriggerRecord] = {}
    views: list[dict[str, Any]] = []

    items = list(universe_items)
    for item in items:
        raw_symbol = _item_value(item, "symbol")
        if raw_symbol is None:
            raise DailyReplayError("Universe item 缺少 symbol")
        symbol = normalise_symbol(str(raw_symbol))
        evidence = normalized_evidence.get(symbol)
        asset_type = str(_item_value(item, "asset_type", "equity"))
        calendar = normalized_calendars.get(symbol)
        for day in normalized_days:
            viewed_at = normalized_times[day]
            row_knowledge_at = min(cutoff, viewed_at)
            if evidence is None:
                scan = _coverage_scan(
                    symbol=symbol,
                    asset_type=asset_type,
                    pool_version=pool_version,
                    knowledge_at=row_knowledge_at,
                )
                scan_by_id[scan.scan_id] = scan
                views.append(
                    {
                        "symbol": symbol,
                        "viewed_at": viewed_at,
                        "knowledge_at": row_knowledge_at,
                        "market_as_of": None,
                        "scan": scan,
                        "setup": None,
                        "trigger": None,
                        "evidence": None,
                        "view_date": day,
                    }
                )
                continue
            if calendar is None:
                raise DailyReplayError(f"{symbol} 缺少显式 TradingSessionCalendar")
            replay = replay_daily_opportunities(
                evidence,
                calendar,
                pool_version=pool_version,
                knowledge_at=row_knowledge_at,
                universe_item=item,
                include_scan_manifests=include_scan_manifests,
            )
            for scan in replay.scans:
                if scan.scan_id not in scan_by_id:
                    scan_by_id[scan.scan_id] = scan
            for setup in replay.setups:
                _merge_setup_history(setup_by_id, setup)
            for trigger in replay.triggers:
                trigger_by_id[trigger.trigger_id] = trigger

            step = replay.step_for_date(day)
            if step is None:
                prior_steps = [candidate for candidate in replay.steps if candidate.date <= day]
                step = prior_steps[-1] if prior_steps else None
            if step is None:
                # The requested view precedes the first visible session.  A
                # scan is still retained for the denominator and the evidence
                # remains an empty, strictly bounded prefix.
                calendar_dates = set(calendar.session_dates)
                prior_bars = [
                    bar
                    for bar in evidence.bars
                    if bar.date < day
                    and bar.date in calendar_dates
                    and bar.available_at <= row_knowledge_at
                    and (
                        bar.session_close_at is None
                        or bar.session_close_at <= row_knowledge_at
                    )
                ]
                scan, candidate = _scan_candidate(
                    evidence=evidence,
                    bars=prior_bars,
                    pool_version=pool_version,
                    knowledge_at=row_knowledge_at,
                    universe_item=item,
                )
                scan_by_id[scan.scan_id] = scan
                if candidate is not None:
                    _merge_setup_history(setup_by_id, candidate)
                setup = None
                trigger = None
                market_as_of = None
            else:
                scan = step.scan
                setup = step.setup
                trigger = step.trigger
                market_as_of = step.date if step.observation_bar_id is not None else None
                if setup is not None:
                    _merge_setup_history(setup_by_id, setup)
                if trigger is not None:
                    trigger_by_id[trigger.trigger_id] = trigger
            if market_as_of is None:
                calendar_dates = set(calendar.session_dates)
                visible_dates = [
                    bar.date
                    for bar in evidence.bars
                    if bar.date <= day
                    and bar.date in calendar_dates
                    and bar.available_at <= row_knowledge_at
                    and (
                        bar.session_close_at is None
                        or bar.session_close_at <= row_knowledge_at
                    )
                ]
                market_as_of = max(visible_dates) if visible_dates else None
            bounded_evidence = _view_evidence(
                evidence,
                market_as_of=market_as_of,
                knowledge_at=row_knowledge_at,
                calendar=calendar,
            )
            views.append(
                {
                    "symbol": symbol,
                    "viewed_at": viewed_at,
                    "knowledge_at": row_knowledge_at,
                    "market_as_of": market_as_of,
                    "scan": scan,
                    "setup": setup,
                    "trigger": trigger,
                    "evidence": bounded_evidence,
                    "view_date": day,
                    "lifecycle_state": step.lifecycle_state if step is not None else None,
                    "reason_codes": list(step.reason_codes) if step is not None else [],
                }
            )

    # Keep stable order for deterministic JSONL/hash consumers.
    scans = list(scan_by_id.values())
    setups = list(setup_by_id.values())
    triggers = list(trigger_by_id.values())
    return {
        "schema_version": SCHEMA_VERSION,
        "scans": scans,
        "setups": setups,
        "triggers": triggers,
        "views": views,
        "knowledge_at": cutoff,
        "pool_version": pool_version,
    }


# A few descriptive aliases keep the single-symbol boundary discoverable to
# callers that use "replay" or "reconstruct" terminology.
replay_daily = replay_daily_opportunities
replay_daily_evidence = replay_daily_opportunities
reconstruct_daily_opportunities = replay_daily_opportunities


__all__ = [
    "DAILY_REBUILD_WAIT_SESSIONS",
    "DailyLifecycleState",
    "DailyReplayError",
    "DailyReplayResult",
    "DailyReplayStep",
    "reconstruct_daily_opportunities",
    "replay_daily",
    "replay_daily_evidence",
    "replay_daily_opportunities",
    "replay_daily_views",
]
