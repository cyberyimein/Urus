"""Point-in-time opportunity quality research for the v3 product boundary.

This module deliberately evaluates *views of opportunities*.  It does not
model delivery, user response, orders, fills, positions, or P&L.  The input is
raw frozen daily evidence and the output is an auditable chain of scans,
structures, view snapshots, and forward price labels.

The implementation is intentionally small and deterministic.  It is useful as
an offline research seam while the product-side view projection is kept in
``app.buy_opportunities.view_projection``.  A synthetic fixture can therefore
exercise the complete chain without making synthetic data look like evidence
of live profitability.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from random import Random
from statistics import fmean, median
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.buy_opportunities.breakout import build_s1_setup
from app.buy_opportunities.contracts import (
    EvidenceBar,
    FrozenEvidence,
    ScanRecord,
    SetupRecord,
    TriggerRecord,
)
from app.buy_opportunities.evidence import InMemoryDailyEvidenceReader
from app.buy_opportunities.lifecycle import TradingSessionCalendar
from app.buy_opportunities.scanner import scan_universe
from app.decision_harness.contracts import content_sha256, normalise_symbol

from .manifest import ResearchManifest


SCHEMA_VERSION = "urus.opportunity_quality.v3"
LABEL_SCHEMA_VERSION = "urus.opportunity_quality.label.v3"
ASSESSMENT_SCHEMA_VERSION = "urus.opportunity_quality.assessment.v3"
HORIZONS: tuple[int, ...] = (1, 2, 5, 10, 20)
SUPPORTED_MODES = {"daily"}
SNAPSHOT_STATUSES = {
    "preparing",
    "triggered_unverified",
    "in_entry_zone",
    "extended",
    "waiting",
    "stale",
    "invalidated",
    "expired",
    "unavailable",
}
DEFAULT_PRIMARY_VIEW_STATUSES: tuple[str, ...] = (
    "triggered_unverified",
    "in_entry_zone",
)


class OpportunityQualityError(ValueError):
    """Raised when a v3 research input cannot be evaluated safely."""


def _aware(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OpportunityQualityError(f"{name} 必须包含时区")
    return value


def _parse_dt(value: datetime | str, *, name: str) -> datetime:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    return _aware(parsed, name=name)


def _sha(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    return value


def _dump_model(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return _json_value(dict(value))
    raise TypeError(f"不支持的研究记录类型：{type(value)!r}")


def _date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if "T" in value or " " in value:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    return date.fromisoformat(value)


def _finite_positive(value: object, *, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise OpportunityQualityError(f"{name} 必须是有限正数") from exc
    if not isfinite(result) or result <= 0:
        raise OpportunityQualityError(f"{name} 必须是有限正数")
    return result


@dataclass(frozen=True)
class QualityConfig:
    """Frozen cohort policy for one v3 quality run."""

    run_id: str
    pool_version: str
    created_at: datetime
    knowledge_at: datetime
    development_start: date
    development_end: date
    lock_start: date
    lock_end: date
    mode: str = "daily"
    market_timezone: str = "America/New_York"
    evaluation_as_of: datetime | None = None
    rules_hash: str = "s1_breakout_v1"
    dataset_id: str | None = None
    seed: int = 5000
    bootstrap_replicates: int = 5000
    bootstrap_block_sessions: int = 20
    primary_source_mode: str | None = None
    primary_data_source: str | None = None
    primary_horizon: int = 1
    # Candidate views are registered explicitly so rejected/full-pool views
    # cannot silently become the primary opportunity sample.  The assessment
    # applies this set to the snapshot_status field and also requires a setup.
    primary_view_statuses: tuple[str, ...] = DEFAULT_PRIMARY_VIEW_STATUSES

    def __post_init__(self) -> None:
        _aware(self.created_at, name="created_at")
        _aware(self.knowledge_at, name="knowledge_at")
        if self.evaluation_as_of is not None:
            _aware(self.evaluation_as_of, name="evaluation_as_of")
        if self.mode not in SUPPORTED_MODES:
            raise OpportunityQualityError(f"当前只支持 daily，收到 {self.mode!r}")
        if not self.market_timezone or not isinstance(self.market_timezone, str):
            raise OpportunityQualityError("market_timezone 必须是非空时区名称")
        try:
            ZoneInfo(self.market_timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise OpportunityQualityError(
                f"market_timezone 不是可识别的 IANA 时区：{self.market_timezone!r}"
            ) from exc
        if self.development_start > self.development_end:
            raise OpportunityQualityError("development 日期范围非法")
        if self.lock_start > self.lock_end:
            raise OpportunityQualityError("lock 日期范围非法")
        if not (self.development_end < self.lock_start or self.lock_end < self.development_start):
            raise OpportunityQualityError("development 与 lock cohort 不能重叠")
        if self.evaluation_as_of is not None and self.evaluation_as_of < self.knowledge_at:
            raise OpportunityQualityError("evaluation_as_of 不能早于 knowledge_at")
        if self.bootstrap_replicates <= 0 or self.bootstrap_block_sessions <= 0:
            raise OpportunityQualityError("bootstrap 参数必须为正数")
        if self.primary_horizon not in HORIZONS:
            raise OpportunityQualityError(
                f"primary_horizon 必须是 {HORIZONS} 之一"
            )
        statuses = tuple(dict.fromkeys(str(item) for item in self.primary_view_statuses))
        if not statuses:
            raise OpportunityQualityError("primary_view_statuses 不能为空")
        unknown_statuses = set(statuses) - SNAPSHOT_STATUSES
        if unknown_statuses:
            raise OpportunityQualityError(
                "primary_view_statuses 包含未知 view status："
                + ", ".join(sorted(unknown_statuses))
            )
        object.__setattr__(self, "primary_view_statuses", statuses)

    @property
    def evaluation_cutoff(self) -> datetime:
        return self.evaluation_as_of or self.knowledge_at

    def cohort_for(self, viewed_at: datetime) -> str | None:
        _aware(viewed_at, name="viewed_at")
        # Cohort dates are market-session dates.  ``viewed_at`` is often
        # serialized in UTC, and an evening ET view can therefore fall on the
        # following UTC date.  Converting before taking ``date()`` prevents a
        # lock/development boundary from moving by one session.
        day = viewed_at.astimezone(ZoneInfo(self.market_timezone)).date()
        if self.development_start <= day <= self.development_end:
            return "development"
        if self.lock_start <= day <= self.lock_end:
            return "lock"
        return None

    @property
    def effective_primary_view_statuses(self) -> tuple[str, ...]:
        """Return the statuses registered for the primary sample.

        The policy is intentionally data-driven.  Daily projection normally
        emits ``triggered_unverified`` for a completed-close view, while a
        caller may still register ``in_entry_zone`` for a separately supplied
        view record.  Filtering belongs to the assessment, rather than
        silently changing the registration based on mode.
        """

        return self.primary_view_statuses


@dataclass(frozen=True)
class ForwardSessionLabel:
    """A non-transactional price-path label anchored at one view snapshot."""

    snapshot_id: str
    cohort: str | None
    economic_opportunity_id: str
    symbol: str
    source_mode: str
    data_source: str
    price_basis: str
    reference_date: date
    reference_at: datetime
    reference_price: float
    horizon_sessions: int
    target_date: date | None
    evaluation_as_of: datetime
    status: str
    snapshot_status: str
    setup_present: bool
    target_price: float | None = None
    reference_price_return: float | None = None
    benchmark_return: float | None = None
    benchmark_excess_return: float | None = None
    benchmark_status: str = "unknown"
    observed_through: date | None = None
    coverage_sessions: int = 0
    expected_sessions: int = 0
    missing_session_dates: tuple[date, ...] = ()
    mfe: float | None = None
    mae: float | None = None
    interval_first_touch_date: date | None = None
    interval_observed_sessions: int = 0
    invalidated_date: date | None = None
    ambiguous_reason: str | None = None
    reason_codes: tuple[str, ...] = ()

    def model_dump(self) -> dict[str, Any]:
        payload = {
                "schema_version": LABEL_SCHEMA_VERSION,
                "snapshot_id": self.snapshot_id,
                "cohort": self.cohort,
                "economic_opportunity_id": self.economic_opportunity_id,
                "symbol": self.symbol,
                "source_mode": self.source_mode,
                "data_source": self.data_source,
                "price_basis": self.price_basis,
                "reference_date": self.reference_date,
                "reference_at": self.reference_at,
                "reference_price": self.reference_price,
                "horizon_sessions": self.horizon_sessions,
                "target_date": self.target_date,
                "evaluation_as_of": self.evaluation_as_of,
                "status": self.status,
                "snapshot_status": self.snapshot_status,
                "setup_present": self.setup_present,
                "target_price": self.target_price,
                "reference_price_return": self.reference_price_return,
                "benchmark_return": self.benchmark_return,
                "benchmark_excess_return": self.benchmark_excess_return,
                "benchmark_status": self.benchmark_status,
                "observed_through": self.observed_through,
                "coverage_sessions": self.coverage_sessions,
                "expected_sessions": self.expected_sessions,
                "missing_session_dates": list(self.missing_session_dates),
                "mfe": self.mfe,
                "mae": self.mae,
                "interval_first_touch_date": self.interval_first_touch_date,
                "interval_observed_sessions": self.interval_observed_sessions,
                "invalidated_date": self.invalidated_date,
                "ambiguous_reason": self.ambiguous_reason,
                "reason_codes": list(self.reason_codes),
            }
        payload["content_sha256"] = content_sha256(_json_value(payload))
        return _json_value(payload)


@dataclass
class QualityRunResult:
    manifest: ResearchManifest
    scans: list[dict[str, Any]] = field(default_factory=list)
    setups: list[dict[str, Any]] = field(default_factory=list)
    triggers: list[dict[str, Any]] = field(default_factory=list)
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    labels: list[dict[str, Any]] = field(default_factory=list)
    assessment: dict[str, Any] = field(default_factory=dict)

    def records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for record_type, items in (
            ("scan", self.scans),
            ("setup", self.setups),
            ("trigger", self.triggers),
            ("view_snapshot", self.snapshots),
            ("forward_label", self.labels),
        ):
            records.extend({"type": record_type, **item} for item in items)
        records.append({"type": "assessment", **self.assessment})
        return records


def _normalise_evidence(raw: Mapping[str, Any]) -> FrozenEvidence:
    if isinstance(raw, FrozenEvidence):
        return raw
    return FrozenEvidence(**raw)


def _evidence_through(
    evidence: FrozenEvidence,
    *,
    market_as_of: date,
    knowledge_at: datetime,
) -> FrozenEvidence:
    """Return only evidence the view could have known at its market cutoff."""

    bars = [
        bar
        for bar in evidence.bars
        if bar.date <= market_as_of and bar.available_at <= knowledge_at
    ]
    # A frozen package normally has one reference per bar date.  Resolve by
    # the complete bar identity anyway: a package may contain revisions for a
    # date, and exposing every same-date reference would make an older,
    # invisible revision appear to support the current view.
    refs_by_identity = {
        (
            item.bar_date,
            item.source,
            item.source_revision,
            item.available_at,
            item.content_sha256,
        ): item
        for item in evidence.source_manifest
    }
    selected_refs = []
    for bar in bars:
        ref = refs_by_identity.get(
            (
                bar.date,
                bar.source,
                bar.source_revision,
                bar.available_at,
                bar.content_sha256,
            )
        )
        if ref is not None:
            selected_refs.append(ref)
    return FrozenEvidence(
        symbol=evidence.symbol,
        asset_type=evidence.asset_type,
        benchmark=evidence.benchmark,
        source_mode=evidence.source_mode,
        data_source=evidence.data_source,
        price_basis=evidence.price_basis,
        price_mapping_id=evidence.price_mapping_id,
        knowledge_at=knowledge_at,
        trading_date=market_as_of,
        quality_status=evidence.quality_status if bars else "unavailable",
        warnings=list(evidence.warnings),
        bars=bars,
        source_manifest=selected_refs,
    )


def _stable_opportunity_id(setup: SetupRecord) -> str:
    """Use the core setup identity as the economic opportunity identity.

    The setup contract is the owner of economic identity.  Research must not
    invent a second fingerprint that silently merges or splits structures.
    """

    return setup.setup_id


def _fallback_snapshot(
    *,
    symbol: str,
    scan: ScanRecord,
    evidence: FrozenEvidence | None,
    viewed_at: datetime,
    reason: str,
    rules_hash: str,
) -> dict[str, Any]:
    latest = evidence.bars[-1] if evidence and evidence.bars else None
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": f"{symbol}:snapshot:{content_sha256({'scan_id': scan.scan_id, 'viewed_at': viewed_at.isoformat()})[:24]}",
        "economic_opportunity_id": None,
        "symbol": symbol,
        "setup_id": scan.setup_id,
        "trigger_id": None,
        "scan_id": scan.scan_id,
        "rules_hash": rules_hash,
        "viewed_at": viewed_at.isoformat(),
        "knowledge_at": viewed_at.isoformat(),
        "market_as_of": latest.date.isoformat() if latest else None,
        "source_mode": scan.source_mode,
        "data_source": evidence.data_source if evidence else "unknown",
        "quality": evidence.quality_status if evidence else "unavailable",
        "freshness": "unknown",
        "status": "unavailable",
        "upper_trigger": None,
        "invalidation_stop": None,
        "atr": None,
        "upper_limit": None,
        "reference_price": float(latest.close) if latest else None,
        "reference_kind": "daily_close" if latest else None,
        "distance_to_trigger_atr": None,
        "condition": "无法从当前冻结证据判断预定条件。",
        "invalidation": reason,
        "event_risk": {"status": "unknown"},
        "research_support": "unknown",
        "reason_codes": list(scan.rejection_codes) or [reason],
        "evidence_refs": [item.model_dump(mode="json") for item in scan.source_manifest],
    }


def _complete_snapshot_hash(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Map the view contract's ``view_id`` to a research snapshot once.

    A snapshot is a research record around an immutable view.  The mapping is
    explicit so labels never use a second time-dependent identity, and the
    final hash covers the complete payload including cohort and economic ID.
    """

    output = dict(payload)
    output["snapshot_id"] = str(output.get("view_id") or "")
    if not output["snapshot_id"]:
        output["snapshot_id"] = "snapshot:" + content_sha256(output)[:24]
    output["content_sha256"] = content_sha256(
        {key: _json_value(value) for key, value in output.items() if key != "content_sha256"}
    )
    return output


def _project_view(
    *,
    setup: SetupRecord,
    trigger: TriggerRecord | None,
    evidence: FrozenEvidence,
    viewed_at: datetime,
    knowledge_at: datetime,
    scan_id: str,
    calendar: Sequence[date],
    dataset_id: str | None,
    rules_hash: str,
) -> dict[str, Any]:
    """Call the product projection without importing any outcome code."""

    try:
        from app.buy_opportunities.view_projection import project_opportunity_view
    except ImportError as exc:  # pragma: no cover - gives a precise integration error
        raise OpportunityQualityError(
            "R04 需要 app.buy_opportunities.view_projection.project_opportunity_view"
        ) from exc
    trading_calendar = (
        TradingSessionCalendar(session_dates=list(calendar)) if calendar else None
    )
    effective_rules_hash = (
        rules_hash
        if len(rules_hash) == 64
        and all(ch in "0123456789abcdef" for ch in rules_hash.lower())
        else content_sha256({"rules_hash": rules_hash})
    )
    view = project_opportunity_view(
        setup,
        trigger,
        evidence=evidence,
        viewed_at=viewed_at,
        knowledge_at=knowledge_at,
        calendar=trading_calendar,
        rules_hash=effective_rules_hash,
        dataset_id=dataset_id,
        # The product projection requires the setup's origin scan.  The
        # per-view scan remains available to the research record as
        # ``evaluation_scan_id``.
        scan_id=setup.origin_scan_id,
    )
    payload = _dump_model(view)
    payload.setdefault("schema_version", SCHEMA_VERSION)
    payload["content_sha256"] = payload.get("content_sha256") or content_sha256(payload)
    return payload


def _calendar_for(
    calendars: Mapping[str, Sequence[date | str]],
    symbol: str,
    bars: Sequence[EvidenceBar],
) -> tuple[list[date], str]:
    values = calendars.get(symbol)
    if values:
        dates = sorted({_date(item) for item in values})
        return dates, "provided"
    # Observed bars are not proof of a complete exchange calendar.  Treating
    # them as one would silently mature a horizon after a missing session.
    return [], "unknown"


def _viewed_at_for_date(
    day: date,
    *,
    evidence: FrozenEvidence,
    explicit: Mapping[date, datetime] | None = None,
) -> datetime:
    if explicit and day in explicit:
        return _aware(explicit[day], name="viewed_at")
    for bar in evidence.bars:
        if bar.date == day and bar.session_close_at is not None:
            # The close is the market boundary; the view is only allowed once
            # the complete bar is actually available, so use its receipt time.
            return bar.available_at
    for bar in evidence.bars:
        if bar.date == day and bar.available_at <= evidence.knowledge_at:
            return bar.available_at
    raise OpportunityQualityError(
        f"{evidence.symbol} 缺少 {day.isoformat()} 的真实 available/session close 时间"
    )


def _bar_map(evidence: FrozenEvidence, *, cutoff: datetime) -> dict[date, EvidenceBar]:
    return {
        bar.date: bar
        for bar in evidence.bars
        if (
            bar.available_at <= cutoff
            and bar.date <= cutoff.date()
            and bar.session_close_at is not None
            and bar.session_close_at <= cutoff
        )
    }


def _expected_sessions(
    *,
    calendar: Sequence[date],
    reference_date: date,
    horizon: int,
) -> list[date]:
    future = [day for day in sorted(set(calendar)) if day > reference_date]
    return future[:horizon]


def _label_one(
    *,
    snapshot: Mapping[str, Any],
    evidence: FrozenEvidence,
    benchmark: FrozenEvidence | None,
    calendar: Sequence[date],
    benchmark_calendar: Sequence[date] | None,
    horizon: int,
    evaluation_as_of: datetime,
) -> ForwardSessionLabel | None:
    reference_date_raw = snapshot.get("market_as_of")
    reference_price_raw = snapshot.get("reference_price")
    if reference_date_raw is None or reference_price_raw is None:
        return None
    try:
        reference_date = _date(reference_date_raw)
        reference_price = _finite_positive(reference_price_raw, name="reference_price")
        reference_at = _parse_dt(
            str(snapshot.get("reference_at") or snapshot.get("viewed_at") or evidence.knowledge_at.isoformat()),
            name="reference_at",
        )
    except OpportunityQualityError:
        return None
    if reference_at > evaluation_as_of:
        # A label cannot be formed from a view that was not yet observable at
        # the declared evaluation cutoff.
        return None
    expected = _expected_sessions(
        calendar=calendar,
        reference_date=reference_date,
        horizon=horizon,
    )
    if not calendar:
        # A bar sequence cannot stand in for an exchange calendar.  Keep the
        # label auditable, but do not invent a target date or mature it.
        target_date = None
        expected_for_label = []
        reason_codes = ("calendar_unknown",)
    elif len(expected) < horizon:
        # The supplied calendar itself has not reached this horizon.  Preserve
        # a censored/pending label rather than treating an absent date as a
        # zero return or shortening the requested horizon.
        target_date = expected[-1] if expected else None
        expected_for_label = expected
        reason_codes = ("calendar_horizon_not_available",)
    else:
        target_date = expected[horizon - 1]
        expected_for_label = expected
        reason_codes = ()

    bars = _bar_map(evidence, cutoff=evaluation_as_of)
    future_observed = [bars[day] for day in expected_for_label if day in bars]
    missing = tuple(day for day in expected_for_label if day not in bars)
    coverage = len(future_observed)
    observed_through = future_observed[-1].date if future_observed else None
    target_bar = bars.get(target_date) if target_date is not None else None
    if target_bar is not None and not missing and len(expected_for_label) == horizon:
        status = "mature"
    elif target_date is not None:
        # Date-only comparison is insufficient on the target session: a
        # missing bar at 00:01 ET is pending, while a missing bar at 18:00 ET
        # is censored.  Prefer the target bar's official close even when the
        # bar itself is unavailable at the evaluation cutoff.
        target_candidate = next(
            (bar for bar in evidence.bars if bar.date == target_date),
            None,
        )
        target_close_at = (
            target_candidate.session_close_at if target_candidate is not None else None
        )
        before_target_close = (
            evaluation_as_of.date() < target_date
            or (
                target_close_at is not None
                and evaluation_as_of < target_close_at
            )
        )
        if before_target_close:
            status = "pending"
            reason_codes = (*reason_codes, "evaluation_as_of_before_target_session_close")
        else:
            status = "censored"
            reason_codes = (*reason_codes, "missing_future_session_bar")
            if target_close_at is None:
                reason_codes = (*reason_codes, "target_session_close_unknown")
    else:
        status = "censored"
        reason_codes = (*reason_codes, "missing_future_session_bar")

    if target_bar is not None and status in {"mature", "ambiguous"}:
        target_price = float(target_bar.close)
        reference_return = target_price / reference_price - 1.0
    else:
        target_price = float(target_bar.close) if target_bar is not None else None
        reference_return = None

    path = future_observed
    mfe = max((float(bar.high) / reference_price - 1.0 for bar in path), default=None)
    mae = min((float(bar.low) / reference_price - 1.0 for bar in path), default=None)
    if status not in {"mature", "ambiguous"}:
        # The partial path is useful for audit but is not a completed horizon
        # statistic; leave the public MFE/MAE fields unknown.
        mfe = None
        mae = None

    L = snapshot.get("upper_trigger")
    S = snapshot.get("invalidation_stop")
    U = snapshot.get("upper_limit")
    interval_first: date | None = None
    interval_observed = 0
    invalidated_date: date | None = None
    ambiguous_reason: str | None = None
    for bar in path:
        if L is not None and U is not None and float(bar.high) >= float(L) and float(bar.low) <= float(U):
            interval_observed += 1
            if interval_first is None:
                interval_first = bar.date
        if S is not None and float(bar.low) <= float(S) and invalidated_date is None:
            invalidated_date = bar.date
        if S is not None and U is not None and float(bar.low) <= float(S) and float(bar.high) >= float(U):
            ambiguous_reason = "same_daily_bar_touched_invalidation_and_upper_limit"
    # A partial future path may already touch both barriers, but that does not
    # make an unfinished horizon mature.  Keep maturity and path ambiguity
    # separate so pending/censored rows cannot contaminate completed estimates.
    if ambiguous_reason is not None and status == "mature":
        status = "ambiguous"
        reason_codes = (*reason_codes, "daily_bar_path_order_unknown")

    benchmark_return: float | None = None
    benchmark_status = "unknown"
    if benchmark is not None:
        # A benchmark return is only comparable when both frozen packages
        # expose the same source class, data class, and price basis.  Mapping
        # IDs belong to each symbol's own corporate-action mapping and must be
        # auditable, but they do not need to be equal across symbols.
        if benchmark.source_mode != evidence.source_mode:
            benchmark_status = "unknown_incomparable_source_mode"
        elif benchmark.data_source != evidence.data_source:
            benchmark_status = "unknown_incomparable_data_source"
        elif benchmark.price_basis != evidence.price_basis:
            benchmark_status = "unknown_incomparable_price_basis"
        elif benchmark.price_mapping_id is None or evidence.price_mapping_id is None:
            benchmark_status = "unknown_price_mapping_unregistered"
        elif benchmark_calendar is None:
            benchmark_status = "unknown_benchmark_calendar"
        elif target_bar is not None and target_date is not None and expected_for_label:
            benchmark_bars = _bar_map(benchmark, cutoff=evaluation_as_of)
            benchmark_expected = _expected_sessions(
                calendar=benchmark_calendar,
                reference_date=reference_date,
                horizon=horizon,
            )
            b_ref = benchmark_bars.get(reference_date)
            b_target = benchmark_bars.get(target_date)
            if b_ref is not None and b_target is not None and len(benchmark_expected) >= horizon:
                benchmark_return = float(b_target.close) / float(b_ref.close) - 1.0
                benchmark_status = "known"
            else:
                benchmark_status = "unknown_missing_matched_benchmark_bar"
    excess = (
        reference_return - benchmark_return
        if reference_return is not None and benchmark_return is not None
        else None
    )
    return ForwardSessionLabel(
        snapshot_id=str(snapshot["snapshot_id"]),
        cohort=str(snapshot.get("cohort")) if snapshot.get("cohort") is not None else None,
        economic_opportunity_id=str(
            snapshot.get("economic_opportunity_id") or snapshot["snapshot_id"]
        ),
        symbol=str(snapshot["symbol"]),
        source_mode=str(snapshot.get("source_mode") or evidence.source_mode),
        data_source=str(snapshot.get("data_source") or evidence.data_source),
        price_basis=evidence.price_basis,
        reference_date=reference_date,
        reference_at=reference_at,
        reference_price=reference_price,
        horizon_sessions=horizon,
        target_date=target_date,
        evaluation_as_of=evaluation_as_of,
        status=status,
        snapshot_status=str(snapshot.get("status") or "unknown"),
        setup_present=bool(snapshot.get("setup_id")),
        target_price=target_price,
        reference_price_return=reference_return,
        benchmark_return=benchmark_return,
        benchmark_excess_return=excess,
        benchmark_status=benchmark_status,
        observed_through=observed_through,
        coverage_sessions=coverage,
        expected_sessions=len(expected_for_label),
        missing_session_dates=missing,
        mfe=mfe,
        mae=mae,
        interval_first_touch_date=interval_first,
        interval_observed_sessions=interval_observed,
        invalidated_date=invalidated_date,
        ambiguous_reason=ambiguous_reason,
        reason_codes=reason_codes,
    )


def _percentiles(values: Sequence[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    def at(fraction: float) -> float:
        index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
        return ordered[index]
    return {
        "min": ordered[0],
        "p25": at(0.25),
        "median": median(ordered),
        "mean": fmean(ordered),
        "p75": at(0.75),
        "max": ordered[-1],
    }


BOOTSTRAP_BLOCK_SESSIONS: tuple[int, ...] = (10, 20, 40)
MIN_EFFECTIVE_BOOTSTRAP_BLOCKS = 10


def _bootstrap_rows(
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Keep one completed outcome per economic opportunity and horizon.

    A repeated view of the same setup is useful in the descriptive view
    report, but it must not create extra observations or narrower uncertainty
    intervals.  ``economic_opportunity_id`` is the setup-owned identity; the
    snapshot id is only a defensive fallback for hand-built records.
    """

    selected: dict[tuple[str, int], dict[str, Any]] = {}
    for raw in rows:
        if str(raw.get("status")) not in {"mature", "ambiguous"}:
            continue
        value = raw.get("reference_price_return")
        try:
            numeric_value = float(value)
            horizon = int(raw.get("horizon_sessions", -1))
        except (TypeError, ValueError):
            continue
        if not isfinite(numeric_value) or horizon not in HORIZONS:
            continue
        identity = str(raw.get("economic_opportunity_id") or raw.get("snapshot_id") or "")
        if not identity:
            continue
        candidate = dict(raw)
        candidate["reference_price_return"] = numeric_value
        key = (identity, horizon)
        previous = selected.get(key)
        if previous is None or str(candidate.get("reference_at")) < str(previous.get("reference_at")):
            selected[key] = candidate
    return list(selected.values())


def synchronized_session_block_bootstrap(
    rows: Iterable[Mapping[str, Any]],
    *,
    block_sessions: int = 20,
    replicates: int = 5000,
    seed: int = 5000,
    trading_sessions: Sequence[date | datetime | str] | None = None,
) -> dict[str, Any]:
    """Estimate a fixed-horizon mean with synchronized session blocks.

    Blocks are sampled as contiguous chunks of an explicit trading-session
    sequence, and each block retains its outcome sum and count.  Resampled
    estimates therefore use ``sum / count`` and preserve the opportunity-level
    point-estimate weighting even when blocks contain different numbers of
    selected labels.  Weekends, holidays and missing sessions cannot be
    silently replaced by calendar-day arithmetic.  If no explicit sequence is
    available, the label dates are used only as an already observed session
    sequence; the result remains auditable as a limited reconstruction.

    Fewer than ten effective blocks produce a point estimate and an unknown
    uncertainty result without confidence interval fields.  This is the
    policy required for the 40-session sensitivity check and keeps a sparse
    opportunity stream from receiving false precision.
    """

    if block_sessions <= 0 or replicates <= 0:
        raise OpportunityQualityError("bootstrap block_sessions 和 replicates 必须为正数")

    completed = _bootstrap_rows(rows)
    values_by_date: dict[date, list[float]] = {}
    for item in completed:
        raw_date = item.get("reference_date") or item.get("market_as_of")
        if raw_date is None:
            continue
        try:
            session_date = _date(raw_date)
        except (TypeError, ValueError):
            continue
        values_by_date.setdefault(session_date, []).append(
            float(item["reference_price_return"])
        )

    if trading_sessions:
        sessions = sorted({_date(value) for value in trading_sessions})
    else:
        sessions = sorted(values_by_date)
    observed_values = [
        value
        for session in sessions
        for value in values_by_date.get(session, ())
    ]
    point_estimate = fmean(observed_values) if observed_values else None

    # Non-overlapping chunks make the effective-block count explicit and
    # deterministic.  A block is useful only when at least one selected
    # outcome is present in its synchronized session window.
    block_summaries: list[tuple[float, int]] = []
    for offset in range(0, len(sessions), block_sessions):
        window = sessions[offset : offset + block_sessions]
        if len(window) < block_sessions:
            continue
        block_values = [
            value
            for session in window
            for value in values_by_date.get(session, ())
        ]
        if block_values:
            block_summaries.append((sum(block_values), len(block_values)))

    result: dict[str, Any] = {
        "block_sessions": block_sessions,
        "seed": seed,
        "sample_count": 0,
        "observed_session_count": sum(bool(values_by_date.get(session)) for session in sessions),
        "observed_value_count": len(observed_values),
        "observed_block_count": len(block_summaries),
        "effective_block_count": len(block_summaries),
        "block_summaries": [
            {"sum": block_sum, "count": count}
            for block_sum, count in block_summaries
        ],
        "block_value_counts": [count for _, count in block_summaries],
        "point_estimate": point_estimate,
    }
    if not block_summaries:
        result.update(
            {
                "status": "unknown",
                "reason": "insufficient_blocks" if observed_values else "no_mature_labels",
            }
        )
        return result
    if len(block_summaries) < MIN_EFFECTIVE_BOOTSTRAP_BLOCKS:
        result.update({"status": "unknown", "reason": "insufficient_blocks"})
        return result

    rng = Random(seed)
    sampled_means: list[float] = []
    for _ in range(replicates):
        sampled = [block_summaries[rng.randrange(len(block_summaries))] for _ in block_summaries]
        total = sum(item[0] for item in sampled)
        count = sum(item[1] for item in sampled)
        sampled_means.append(total / count)
    sampled_means.sort()
    low_index = max(0, min(len(sampled_means) - 1, int(replicates * 0.025)))
    high_index = max(0, min(len(sampled_means) - 1, int(replicates * 0.975) - 1))
    result.update(
        {
            "status": "observed",
            "sample_count": replicates,
            "bootstrap_mean": fmean(sampled_means),
            "interval_low": sampled_means[low_index],
            "interval_high": sampled_means[high_index],
        }
    )
    return result


# Keep a private name available to focused research tests without exposing a
# second implementation or changing the legacy buy-evaluation API.
_synchronized_block_bootstrap = synchronized_session_block_bootstrap


def _assessment(
    *,
    config: QualityConfig,
    scans: Sequence[Mapping[str, Any]],
    snapshots: Sequence[Mapping[str, Any]],
    labels: Sequence[Mapping[str, Any]],
    trading_sessions: Sequence[date | datetime | str] | None = None,
) -> dict[str, Any]:
    scan_by_id = {str(item.get("scan_id")): item for item in scans if item.get("scan_id")}
    requested_scan_ids = {
        str(item.get("evaluation_scan_id") or item.get("scan_id"))
        for item in snapshots
        if item.get("evaluation_scan_id") or item.get("scan_id")
    }
    requested_scans = [
        scan_by_id[scan_id]
        for scan_id in sorted(requested_scan_ids)
        if scan_id in scan_by_id
    ]
    # ``scans`` includes the replay's warmup/history records.  Keep those for
    # traceability, but base current view coverage and rejection diagnostics on
    # the scans explicitly linked from the requested snapshots.
    scan_status_all_history = Counter(str(item.get("status")) for item in scans)
    rejection_codes_all_history = Counter(
        code
        for item in scans
        for code in item.get("rejection_codes", [])
    )
    scan_status = Counter(str(item.get("status")) for item in requested_scans)
    rejection_codes = Counter(
        code
        for item in requested_scans
        for code in item.get("rejection_codes", [])
    )
    snapshot_status = Counter(str(item.get("status")) for item in snapshots)
    snapshot_sources = Counter(
        (str(item.get("source_mode")), str(item.get("data_source")))
        for item in snapshots
    )

    def _horizon_value(item: Mapping[str, Any]) -> int:
        try:
            return int(item.get("horizon_sessions", -1))
        except (TypeError, ValueError):
            return -1

    def _maturity_rows(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        return [item for item in rows if item.get("status") in {"mature", "ambiguous"}]

    def _horizon_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for horizon in HORIZONS:
            all_rows = [item for item in rows if _horizon_value(item) == horizon]
            complete_rows = _maturity_rows(all_rows)
            returns = [
                float(item["reference_price_return"])
                for item in complete_rows
                if item.get("reference_price_return") is not None
            ]
            excess = [
                float(item["benchmark_excess_return"])
                for item in complete_rows
                if item.get("benchmark_excess_return") is not None
            ]
            mfe = [float(item["mfe"]) for item in complete_rows if item.get("mfe") is not None]
            mae = [float(item["mae"]) for item in complete_rows if item.get("mae") is not None]
            benchmark_known = sum(item.get("benchmark_status") == "known" for item in all_rows)
            output[str(horizon)] = {
                "label_count": len(all_rows),
                "status_counts": dict(Counter(str(item.get("status")) for item in all_rows)),
                "reference_price_return": _percentiles(returns),
                "benchmark_excess_return": _percentiles(excess),
                "benchmark_known_count": benchmark_known,
                "benchmark_unknown_count": len(all_rows) - benchmark_known,
                "mfe": _percentiles(mfe),
                "mae": _percentiles(mae),
                "interval_first_touch_count": sum(
                    item.get("interval_first_touch_date") is not None for item in all_rows
                ),
                "invalidated_count": sum(
                    item.get("invalidated_date") is not None for item in all_rows
                ),
                "ambiguous_count": sum(
                    item.get("status") == "ambiguous" for item in all_rows
                ),
                "path_ambiguous_count": sum(
                    item.get("ambiguous_reason") is not None for item in all_rows
                ),
            }
        return output

    # Independent opportunities are counted once per horizon.  Re-opening the
    # same structure creates another view row but not another economic sample.
    def _independent(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
        selected: dict[str, dict[str, Any]] = {}
        for item in rows:
            key = f"{item.get('economic_opportunity_id')}:{item.get('horizon_sessions')}"
            previous = selected.get(key)
            if previous is None or str(item.get("reference_at")) < str(previous.get("reference_at")):
                selected[key] = dict(item)
        return selected

    def _label_has_setup(item: Mapping[str, Any]) -> bool:
        # Forward labels emitted by this module carry setup_present.  The
        # setup_id fallback keeps assessment useful for older serialized labels
        # while still rejecting an explicit missing setup identity.
        if "setup_present" in item:
            return bool(item.get("setup_present"))
        if "setup_id" in item:
            return bool(item.get("setup_id"))
        return False

    # The primary cohort is locked by policy.  No fallback to a mixed source,
    # development data, another horizon, a rejected view, or a setup-less
    # label is allowed.  ``snapshot_status`` is the view status; ``status`` is
    # the independent forward-label maturity state and must not be confused
    # with the registered candidate filter.
    registered_statuses = set(config.effective_primary_view_statuses)
    primary_labels = [
        item
        for item in labels
        if item.get("cohort") == "lock"
        and item.get("snapshot_status") in registered_statuses
        and _label_has_setup(item)
        and config.primary_source_mode is not None
        and item.get("source_mode") == config.primary_source_mode
        and config.primary_data_source is not None
        and item.get("data_source") == config.primary_data_source
        and _horizon_value(item) == config.primary_horizon
    ]
    primary_locked = bool(
        isinstance(config.primary_source_mode, str)
        and config.primary_source_mode.strip()
        and isinstance(config.primary_data_source, str)
        and config.primary_data_source.strip()
        and config.primary_horizon in HORIZONS
    )
    if not primary_locked:
        primary_labels = []
    primary_independent = _independent(primary_labels)
    all_independent = _independent(labels)

    data_sources = {str(item.get("data_source")) for item in snapshots}
    if "synthetic" in data_sources:
        applicability = "research_only"
    else:
        applicability = "unknown"
    limitations = [
        "view_snapshot参考价格变化是价格路径代理，不是成交或用户盈亏。",
        "触及预定区间不等于用户能以该价格参与。",
        "没有信息推送、响应、订单或持仓字段。",
        "研究资格不会由策略分数自动升级为有盈利支持。",
    ]
    if "unknown" in data_sources or not data_sources:
        limitations.append("source/data_source覆盖不足，研究适用性为 unknown。")
    if all(item.get("benchmark_status") != "known" for item in primary_labels):
        limitations.append("没有匹配的 benchmark 证据，超额比较保持 unknown。")
    primary_status = (
        "locked"
        if primary_locked and primary_labels
        else ("unknown" if primary_locked else "unknown_unregistered_primary_source")
    )
    primary_metrics = _horizon_metrics(primary_labels)
    primary_economic = {
        "label_count": len(primary_independent),
        "status_counts": dict(Counter(str(item.get("status")) for item in primary_independent.values())),
        "horizons": _horizon_metrics(list(primary_independent.values())),
    }
    primary_filter_counts = {
        "registered_statuses": sorted(registered_statuses),
        "source_mode": config.primary_source_mode,
        "data_source": config.primary_data_source,
        "horizon_sessions": config.primary_horizon,
        "eligible_label_count": len(primary_labels),
        "eligible_independent_count": len(primary_independent),
        "excluded": {
            "wrong_cohort": sum(item.get("cohort") != "lock" for item in labels),
            "wrong_snapshot_status": sum(
                item.get("cohort") == "lock"
                and item.get("snapshot_status") not in registered_statuses
                for item in labels
            ),
            "setup_missing": sum(
                item.get("cohort") == "lock"
                and item.get("snapshot_status") in registered_statuses
                and not _label_has_setup(item)
                for item in labels
            ),
            "source_mode_mismatch": sum(
                item.get("cohort") == "lock"
                and item.get("snapshot_status") in registered_statuses
                and _label_has_setup(item)
                and item.get("source_mode") != config.primary_source_mode
                for item in labels
            ),
            "data_source_mismatch": sum(
                item.get("cohort") == "lock"
                and item.get("snapshot_status") in registered_statuses
                and _label_has_setup(item)
                and item.get("source_mode") == config.primary_source_mode
                and item.get("data_source") != config.primary_data_source
                for item in labels
            ),
            "horizon_mismatch": sum(
                item.get("cohort") == "lock"
                and item.get("snapshot_status") in registered_statuses
                and _label_has_setup(item)
                and item.get("source_mode") == config.primary_source_mode
                and item.get("data_source") == config.primary_data_source
                and _horizon_value(item) != config.primary_horizon
                for item in labels
            ),
        },
    }
    # Use the independent opportunity unit for uncertainty so a repeated
    # opening of the same setup cannot shrink the interval.  The explicit
    # calendar is passed by the full runner; hand-built assessment tests may
    # omit it and then use the observed reference dates as a limited fallback.
    uncertainty_by_block = {
        str(block_sessions): synchronized_session_block_bootstrap(
            primary_independent.values(),
            block_sessions=block_sessions,
            replicates=config.bootstrap_replicates,
            seed=config.seed,
            trading_sessions=trading_sessions,
        )
        for block_sessions in BOOTSTRAP_BLOCK_SESSIONS
    }
    unknown_uncertainty = next(
        (
            item
            for item in uncertainty_by_block.values()
            if item.get("status") != "observed"
        ),
        None,
    )
    uncertainty = {
        "status": "observed" if unknown_uncertainty is None else "unknown",
        "method": "synchronized_trading_session_block_bootstrap",
        "horizon_sessions": config.primary_horizon,
        "block_sessions": config.bootstrap_block_sessions,
        "sensitivity_blocks": list(BOOTSTRAP_BLOCK_SESSIONS),
        "replicates": config.bootstrap_replicates,
        "seed": config.seed,
        "by_block_sessions": uncertainty_by_block,
    }
    # Named entries make the primary 20-session result and its two registered
    # sensitivities easy to consume without requiring callers to know the
    # internal mapping key.
    uncertainty.update(
        {
            f"bootstrap_{block_sessions}_session": uncertainty_by_block[str(block_sessions)]
            for block_sessions in BOOTSTRAP_BLOCK_SESSIONS
        }
    )
    if unknown_uncertainty is not None:
        uncertainty["reason"] = unknown_uncertainty.get("reason", "unknown")
    assessment = {
        "schema_version": ASSESSMENT_SCHEMA_VERSION,
        "assessment_id": f"{config.run_id}:assessment",
        "primary_cohort": {
            "status": primary_status,
            "unit": "view_snapshot",
            "cohort": "lock",
            "source_mode": config.primary_source_mode,
            "data_source": config.primary_data_source,
            "horizon_sessions": config.primary_horizon,
            "view_statuses": list(config.effective_primary_view_statuses),
            "setup_required": True,
        },
        "applicability": applicability,
        "research_support": "research_only" if applicability == "research_only" else "unknown",
        "metrics": {
            "scan_count": len(requested_scans),
            "requested_view_scan_count": len(requested_scans),
            "scan_count_all_history": len(scans),
            "scan_status_counts": dict(scan_status),
            "rejection_code_counts": dict(rejection_codes),
            "scan_status_counts_all_history": dict(scan_status_all_history),
            "rejection_code_counts_all_history": dict(rejection_codes_all_history),
            "snapshot_count": len(snapshots),
            "snapshot_status_counts": dict(snapshot_status),
            "snapshot_source_counts": {f"{mode}:{source}": count for (mode, source), count in snapshot_sources.items()},
            "economic_opportunity_count": len({item.get("economic_opportunity_id") for item in snapshots if item.get("economic_opportunity_id")}),
            "label_count": len(labels),
            "independent_label_count": len(all_independent),
            "independent_label_counts_by_horizon": dict(Counter(str(item.get("horizon_sessions")) for item in all_independent.values())),
            "descriptive_all_views": {
                "horizons": _horizon_metrics(labels),
                "cohort_units": {
                    "view_snapshots": len(snapshots),
                    "economic_opportunities": len({item.get("economic_opportunity_id") for item in snapshots if item.get("economic_opportunity_id")}),
                },
            },
            "primary_lock_cohort": {
                "label_count": len(primary_labels),
                "independent_label_count": len(primary_independent),
                "horizons": primary_metrics,
                "independent": primary_economic,
            },
            "cohort_units_all": {
                "view_snapshots": len(snapshots),
                "economic_opportunities": len({item.get("economic_opportunity_id") for item in snapshots if item.get("economic_opportunity_id")}),
            },
            "full_pool_diagnostics": {
                "scan_count": len(requested_scans),
                "requested_view_scan_count": len(requested_scans),
                "scan_count_all_history": len(scans),
                "scan_status_counts": dict(scan_status),
                "rejection_code_counts": dict(rejection_codes),
                "scan_status_counts_all_history": dict(scan_status_all_history),
                "rejection_code_counts_all_history": dict(rejection_codes_all_history),
                "snapshot_count": len(snapshots),
                "snapshot_status_counts": dict(snapshot_status),
                "label_count": len(labels),
            },
            "primary_candidate_diagnostics": primary_filter_counts,
        },
        "baseline": {
            "name": "matched_benchmark_hold",
            "status": "known" if any(item.get("benchmark_status") == "known" for item in primary_labels) else "unknown",
            "missing_is_zero": False,
        },
        "uncertainty": uncertainty,
        "filter_diagnostics": {
            "status": "not_applicable_without_registered_filter_variant",
            "removed_loss": None,
            "missed_profit": None,
        },
        "limitations": limitations,
    }
    assessment["content_sha256"] = content_sha256(
        {key: _json_value(value) for key, value in assessment.items() if key != "content_sha256"}
    )
    return assessment


def _validate_view_timestamps(*, viewed_at: datetime, knowledge_at: datetime, evaluation_as_of: datetime) -> None:
    if knowledge_at > viewed_at:
        raise OpportunityQualityError("snapshot knowledge_at 不能晚于 viewed_at")
    if viewed_at > evaluation_as_of:
        raise OpportunityQualityError("snapshot viewed_at 不能晚于 evaluation_as_of")


def _effective_rules_hash(value: str) -> str:
    if len(value) == 64 and all(ch in "0123456789abcdef" for ch in value.lower()):
        return value.lower()
    return content_sha256({"rules_hash": value})


def _module_file_hash(path: str) -> str:
    try:
        from pathlib import Path

        return sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def _item_symbol(item: object) -> str:
    raw = item.get("symbol") if isinstance(item, Mapping) else getattr(item, "symbol", "")
    return normalise_symbol(str(raw or ""))


def _as_record(value: Any, model_type: type[Any]) -> Any:
    if value is None or isinstance(value, model_type):
        return value
    if isinstance(value, Mapping):
        return model_type(**dict(value))
    return value


def _asof_evidence(
    evidence: FrozenEvidence | None,
    *,
    market_as_of: date | None,
    knowledge_at: datetime,
) -> FrozenEvidence | None:
    if evidence is None:
        return None
    cutoff = market_as_of or knowledge_at.date()
    return _evidence_through(evidence, market_as_of=cutoff, knowledge_at=knowledge_at)


def _shared_daily_replay(
    *,
    replay: Any,
    universe_items: Sequence[object],
    evidence_by_symbol: Mapping[str, FrozenEvidence],
    calendars: Mapping[str, Sequence[date]],
    view_days: Sequence[date],
    view_times: Mapping[date, datetime],
    config: QualityConfig,
) -> Any:
    """Invoke the one canonical daily lifecycle coordinator.

    ``daily_replay.py`` owns setup aging, first-trigger identity, invalidation,
    and session transitions.  This adapter intentionally has no trigger logic
    of its own.  The accepted callable signature is documented here so the
    product and research seams share one state machine:

    ``replay_daily_views(universe_items=..., evidence_by_symbol=...,
    calendars=..., view_days=..., view_times=..., pool_version=...,
    knowledge_at=...)``.
    """

    if replay is None:
        try:
            # Use the canonical multi-symbol replay wrapper.  It retains every
            # authorized scan and binds each row to the exact setup/trigger
            # state visible at that view cutoff.
            from app.buy_opportunities.daily_replay import replay_daily_views
        except ImportError as exc:  # pragma: no cover - integration guard
            raise OpportunityQualityError(
                "R04 需要 app.buy_opportunities.daily_replay.replay_daily_views"
            ) from exc
        return replay_daily_views(
            universe_items=universe_items,
            evidence_by_symbol=evidence_by_symbol,
            calendars=calendars,
            view_days=list(view_days),
            view_times=dict(view_times),
            pool_version=config.pool_version,
            knowledge_at=config.knowledge_at,
        )
    if not callable(replay):
        raise OpportunityQualityError("daily_replay 必须是可调用的共享生命周期协调器")
    return replay(
        universe_items=universe_items,
        evidence_by_symbol=evidence_by_symbol,
        calendars=calendars,
        view_days=list(view_days),
        view_times=dict(view_times),
        pool_version=config.pool_version,
        knowledge_at=config.knowledge_at,
    )


def _scan_for_daily_view(
    *,
    item: object,
    evidence: FrozenEvidence | None,
    view_day: date,
    viewed_at: datetime,
    pool_version: str,
) -> tuple[ScanRecord, SetupRecord | None, FrozenEvidence | None]:
    """Build a scan-only row for missing calendar/evidence edge cases.

    Trigger and lifecycle state are deliberately absent here; the canonical
    daily replay owns those transitions whenever an explicit calendar and
    complete evidence are available.
    """

    symbol = _item_symbol(item)
    if evidence is None:
        scans, setups = scan_universe(
            universe_items=[item],
            evidence_reader=InMemoryDailyEvidenceReader({}),
            pool_version=pool_version,
            knowledge_at=viewed_at,
        )
        return scans[0], setups[0] if setups else None, None
    prior = [bar for bar in evidence.bars if bar.date < view_day]
    prior_evidence = _evidence_through(
        evidence,
        market_as_of=prior[-1].date if prior else view_day,
        knowledge_at=viewed_at,
    )
    scans, setups = scan_universe(
        universe_items=[item],
        evidence_reader=InMemoryDailyEvidenceReader({symbol: prior_evidence}),
        pool_version=pool_version,
        knowledge_at=viewed_at,
        source_mode=evidence.source_mode,
    )
    current_evidence = _evidence_through(
        evidence,
        market_as_of=view_day,
        knowledge_at=viewed_at,
    )
    return scans[0], setups[0] if setups else None, current_evidence


def _default_shared_daily_replay(
    *,
    universe_items: Sequence[object],
    evidence_by_symbol: Mapping[str, FrozenEvidence],
    calendars: Mapping[str, Sequence[date]],
    view_days: Sequence[date],
    view_times: Mapping[date, datetime],
    config: QualityConfig,
) -> dict[str, Any]:
    """Adapt the core per-symbol replay into the R04 fixed-view cohort.

    ``replay_daily_opportunities`` is called separately at each view cutoff so
    a later bar cannot affect an earlier lifecycle state.  This is a thin
    adapter only; setup aging, trigger identity, invalidation and rebuilds
    remain in ``buy_opportunities.daily_replay``.
    """

    try:
        from app.buy_opportunities.daily_replay import replay_daily_opportunities
    except ImportError as exc:  # pragma: no cover - integration guard
        raise OpportunityQualityError(
            "R04 需要 app.buy_opportunities.daily_replay.replay_daily_opportunities"
        ) from exc

    rows: list[dict[str, Any]] = []
    scans: list[ScanRecord] = []
    setups: list[SetupRecord] = []
    triggers: list[TriggerRecord] = []
    for item in universe_items:
        symbol = _item_symbol(item)
        evidence = evidence_by_symbol.get(symbol)
        explicit_calendar = list(calendars.get(symbol, ()))
        for view_day in view_days:
            viewed_at = view_times[view_day]
            replay_result = None
            step = None
            if evidence is not None and explicit_calendar:
                replay_result = replay_daily_opportunities(
                    evidence,
                    TradingSessionCalendar(session_dates=explicit_calendar),
                    pool_version=config.pool_version,
                    knowledge_at=viewed_at,
                )
                step = replay_result.step_for_date(view_day)
            if step is not None:
                scan = step.scan
                setup = step.setup
                trigger = step.trigger
                current_evidence = _evidence_through(
                    evidence,
                    market_as_of=view_day,
                    knowledge_at=viewed_at,
                )
            else:
                scan, setup, current_evidence = _scan_for_daily_view(
                    item=item,
                    evidence=evidence,
                    view_day=view_day,
                    viewed_at=viewed_at,
                    pool_version=config.pool_version,
                )
                trigger = None
            scans.append(scan)
            if setup is not None:
                setups.append(setup)
            if trigger is not None:
                triggers.append(trigger)
            rows.append(
                {
                    "symbol": symbol,
                    "viewed_at": viewed_at,
                    "knowledge_at": viewed_at,
                    "market_as_of": view_day,
                    "scan": scan,
                    "setup": setup,
                    "trigger": trigger,
                    "evidence": current_evidence,
                    "full_evidence": evidence,
                    "calendar": explicit_calendar,
                }
            )
    return {"views": rows, "scans": scans, "setups": setups, "triggers": triggers}


def _extract_replay_rows(
    raw_result: Any,
    *,
    evidence_by_symbol: Mapping[str, FrozenEvidence],
    calendars: Mapping[str, Sequence[date]],
    view_days: Sequence[date],
    view_times: Mapping[date, datetime],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize the shared replay result without changing its lifecycle data."""

    def _field(value: Any, key: str, default: Any = None) -> Any:
        if isinstance(value, Mapping):
            return value.get(key, default)
        return getattr(value, key, default)

    scans_raw = _field(raw_result, "scans", []) or []
    setups_raw = _field(raw_result, "setups", []) or []
    triggers_raw = _field(raw_result, "triggers", []) or []
    rows_raw = (
        _field(raw_result, "views", None)
        or _field(raw_result, "rows", None)
        or _field(raw_result, "snapshots", None)
        or (raw_result if isinstance(raw_result, Sequence) and not isinstance(raw_result, (str, bytes)) else [])
    )
    scans = [_dump_model(item) for item in scans_raw]
    setups = [_dump_model(item) for item in setups_raw]
    triggers = [_dump_model(item) for item in triggers_raw]
    scans_by_id = {str(item.get("scan_id")): item for item in scans}
    setups_by_id = {str(item.get("setup_id")): item for item in setups}
    triggers_by_id = {str(item.get("trigger_id")): item for item in triggers}
    rows: list[dict[str, Any]] = []
    for raw in rows_raw or []:
        scan_value = _field(raw, "scan")
        setup_value = _field(raw, "setup")
        trigger_value = _field(raw, "trigger")
        snapshot_value = _field(raw, "snapshot") or _field(raw, "view")
        snapshot = _dump_model(snapshot_value) if snapshot_value is not None else None
        scan = _dump_model(scan_value) if scan_value is not None else None
        setup = _dump_model(setup_value) if setup_value is not None else None
        trigger = _dump_model(trigger_value) if trigger_value is not None else None
        scan_id = _field(raw, "scan_id") or (snapshot or {}).get("scan_id")
        setup_id = _field(raw, "setup_id") or (snapshot or {}).get("setup_id")
        trigger_id = _field(raw, "trigger_id") or (snapshot or {}).get("trigger_id")
        scan = scan or scans_by_id.get(str(scan_id))
        setup = setup or setups_by_id.get(str(setup_id))
        trigger = trigger or triggers_by_id.get(str(trigger_id))
        symbol = normalise_symbol(
            str(_field(raw, "symbol") or (snapshot or {}).get("symbol") or (scan or {}).get("symbol") or "")
        )
        if not symbol:
            continue
        viewed_value = _field(raw, "viewed_at") or (snapshot or {}).get("viewed_at")
        viewed_at = (
            _parse_dt(viewed_value, name="viewed_at")
            if viewed_value is not None
            else view_times.get(_date(_field(raw, "view_date")), None)
        )
        if viewed_at is None:
            continue
        knowledge_value = _field(raw, "knowledge_at") or (snapshot or {}).get("knowledge_at")
        knowledge_at = (
            _parse_dt(knowledge_value, name="knowledge_at")
            if knowledge_value is not None
            else viewed_at
        )
        market_value = _field(raw, "market_as_of") or (snapshot or {}).get("market_as_of")
        market_as_of = _date(market_value) if market_value is not None else viewed_at.date()
        evidence = _as_record(_field(raw, "evidence") or _field(raw, "view_evidence"), FrozenEvidence)
        evidence = evidence or evidence_by_symbol.get(symbol)
        evidence = _asof_evidence(evidence, market_as_of=market_as_of, knowledge_at=knowledge_at)
        full_evidence = _as_record(
            _field(raw, "full_evidence") or _field(raw, "outcome_evidence"),
            FrozenEvidence,
        ) or evidence_by_symbol.get(symbol)
        calendar = list(calendars.get(symbol, ()))
        rows.append(
            {
                "snapshot": snapshot,
                "scan": scan,
                "setup": setup,
                "trigger": trigger,
                "symbol": symbol,
                "viewed_at": viewed_at,
                "knowledge_at": knowledge_at,
                "market_as_of": market_as_of,
                "evidence": evidence,
                "full_evidence": full_evidence,
                "calendar": calendar,
            }
        )
    return rows, scans, setups, triggers


def run_opportunity_quality(
    *,
    payload: Mapping[str, Any],
    config: QualityConfig,
    daily_replay: Any = None,
) -> QualityRunResult:
    """Run the complete v3 daily research chain from raw frozen evidence.

    ``payload`` contains only raw evidence and cohort inputs.  In particular,
    it must not contain precomputed P&L, fills, response delays, positions, or
    order records.  The function accepts optional explicit ``viewed_at`` times;
    otherwise each available completed daily bar after the 61-bar warmup is a
    fixed daily view candidate.
    """

    if any(key in payload for key in ("pnl", "fills", "orders", "response_minutes", "positions")):
        raise OpportunityQualityError("v3 主入口不接受交易/P&L/推送字段")
    raw_evidence = payload.get("evidence")
    if not isinstance(raw_evidence, Mapping) or not raw_evidence:
        raise OpportunityQualityError("payload.evidence 必须是非空 symbol→FrozenEvidence 映射")
    evidence_by_symbol = {
        normalise_symbol(str(symbol)): _normalise_evidence(item)
        for symbol, item in raw_evidence.items()
    }
    universe_items = list(payload.get("universe") or [
        {"symbol": symbol, "asset_type": evidence.asset_type, "enabled": True}
        for symbol, evidence in evidence_by_symbol.items()
    ])
    calendars_raw = payload.get("calendar") or {}
    calendars: dict[str, list[date]] = {
        normalise_symbol(str(symbol)): sorted({_date(item) for item in values})
        for symbol, values in calendars_raw.items()
    }
    benchmark_raw = payload.get("benchmarks") or {}
    benchmarks = {
        normalise_symbol(str(symbol)): _normalise_evidence(item)
        for symbol, item in benchmark_raw.items()
    }

    explicit_times: dict[date, datetime] = {}
    for value in payload.get("viewed_at", []) or []:
        parsed = _parse_dt(value, name="viewed_at")
        explicit_times[parsed.date()] = parsed
    if explicit_times:
        view_days = sorted(explicit_times)
        view_times = explicit_times
    else:
        # Derive fixed daily views only from bars with a real receipt time.
        # If a calendar is supplied, it is the authoritative set of sessions.
        candidate_days = sorted(
            {
                day
                for values in calendars.values()
                for day in values
            }
            or {
                bar.date
                for evidence in evidence_by_symbol.values()
                for bar in evidence.bars
            }
        )
        view_times = {}
        for day in candidate_days:
            source = next(
                (evidence for evidence in evidence_by_symbol.values() if any(bar.date == day for bar in evidence.bars)),
                None,
            )
            if source is None:
                continue
            try:
                view_times[day] = _viewed_at_for_date(day, evidence=source)
            except OpportunityQualityError:
                continue
        view_days = sorted(
            day for day, viewed_at in view_times.items() if config.cohort_for(viewed_at)
        )
    if not view_days:
        raise OpportunityQualityError("没有落在 development/lock cohort 内的 daily 查看截面")
    for day in view_days:
        viewed_at = view_times.get(day)
        if viewed_at is None:
            raise OpportunityQualityError(f"查看截面 {day} 缺少真实 viewed_at")
        if config.knowledge_at < viewed_at:
            raise OpportunityQualityError(
                "knowledge_at 早于 viewed_at；不能为了运行研究而提升旧 Dataset 的知识上界"
            )
        _validate_view_timestamps(
            viewed_at=viewed_at,
            knowledge_at=min(config.knowledge_at, viewed_at),
            evaluation_as_of=config.evaluation_cutoff,
        )

    # The shared replay returns one row for every authorized symbol and view
    # session, including rejected and coverage-missing scans.  It owns the
    # daily lifecycle; this module only projects and labels those rows.
    raw_replay = _shared_daily_replay(
        replay=daily_replay,
        universe_items=universe_items,
        evidence_by_symbol=evidence_by_symbol,
        calendars=calendars,
        view_days=view_days,
        view_times=view_times,
        config=config,
    )
    replay_rows, scans, setups, triggers = _extract_replay_rows(
        raw_replay,
        evidence_by_symbol=evidence_by_symbol,
        calendars=calendars,
        view_days=view_days,
        view_times=view_times,
    )
    scans = list(scans)
    setups = list(setups)
    triggers = list(triggers)
    snapshots: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    snapshot_context: dict[str, tuple[FrozenEvidence, FrozenEvidence | None, list[date]]] = {}
    effective_rules_hash = _effective_rules_hash(config.rules_hash)
    seen_view_keys: set[tuple[str, date]] = set()
    expected_view_keys = {
        (_item_symbol(item), day)
        for item in universe_items
        for day in view_days
    }
    replay_keys = {
        (str(row.get("symbol")), row.get("viewed_at").date())
        for row in replay_rows
        if row.get("viewed_at") is not None
    }
    missing_view_keys = sorted(expected_view_keys - replay_keys)
    if missing_view_keys:
        raise OpportunityQualityError(
            "daily replay 未返回完整 Universe/view 截面："
            + ", ".join(f"{symbol}@{day.isoformat()}" for symbol, day in missing_view_keys[:10])
        )
    for row in replay_rows:
        symbol = str(row["symbol"])
        viewed_at = row["viewed_at"]
        knowledge_at = row["knowledge_at"]
        _validate_view_timestamps(
            viewed_at=viewed_at,
            knowledge_at=knowledge_at,
            evaluation_as_of=config.evaluation_cutoff,
        )
        key = (symbol, viewed_at.date())
        if key in seen_view_keys:
            raise OpportunityQualityError(f"daily replay 返回重复查看截面：{symbol} {viewed_at.date()}")
        seen_view_keys.add(key)
        scan_value = row.get("scan")
        scan = _as_record(scan_value, ScanRecord) if scan_value is not None else None
        setup_value = row.get("setup")
        setup = _as_record(setup_value, SetupRecord) if setup_value is not None else None
        trigger_value = row.get("trigger")
        trigger = _as_record(trigger_value, TriggerRecord) if trigger_value is not None else None
        evidence = row.get("evidence")
        full_evidence = row.get("full_evidence") or evidence_by_symbol.get(symbol)
        # Only the manifest's explicit calendar can mature a session horizon;
        # a replay row's observed bar dates are never promoted to a calendar.
        calendar = list(calendars.get(symbol, ()))
        if scan is None:
            # Shared replay should always emit this, but retaining an explicit
            # error protects the full-pool denominator from silent omissions.
            raise OpportunityQualityError(f"daily replay 缺少 {symbol} 的 scan")
        if setup is not None:
            view_payload = _project_view(
                setup=setup,
                trigger=trigger,
                evidence=evidence,
                viewed_at=viewed_at,
                knowledge_at=knowledge_at,
                scan_id=scan.scan_id,
                calendar=calendar,
                dataset_id=config.dataset_id,
                rules_hash=effective_rules_hash,
            )
            view_payload["economic_opportunity_id"] = _stable_opportunity_id(setup)
            view_payload["cohort"] = config.cohort_for(viewed_at)
            view_payload["calendar_source"] = "provided" if symbol in calendars else "unknown"
            view_payload["evaluation_scan_id"] = scan.scan_id
        else:
            view_payload = _fallback_snapshot(
                symbol=symbol,
                scan=scan,
                evidence=evidence,
                viewed_at=viewed_at,
                reason=scan.rejection_codes[0] if scan.rejection_codes else "scan_not_eligible",
                rules_hash=effective_rules_hash,
            )
            view_payload["cohort"] = config.cohort_for(viewed_at)
            view_payload["calendar_source"] = "provided" if symbol in calendars else "unknown"
            view_payload["evaluation_scan_id"] = scan.scan_id
        view_payload = _complete_snapshot_hash(view_payload)
        snapshots.append(view_payload)
        if full_evidence is not None:
            snapshot_context[str(view_payload["snapshot_id"])] = (
                full_evidence,
                benchmarks.get(full_evidence.benchmark or "") if full_evidence.benchmark else None,
                calendar,
            )

    # Labels are computed only after all snapshots are frozen.  The view got an
    # as-of evidence prefix above; labels get the full frozen evidence but are
    # filtered by evaluation_as_of and future session dates here.
    for snapshot in snapshots:
        if snapshot.get("status") == "unavailable" and snapshot.get("reference_price") is None:
            continue
        context = snapshot_context.get(str(snapshot["snapshot_id"]))
        if context is None:
            continue
        evidence, benchmark, calendar = context
        benchmark_calendar = (
            calendars.get(benchmark.symbol) if benchmark is not None else None
        )
        for horizon in HORIZONS:
            label = _label_one(
                snapshot=snapshot,
                evidence=evidence,
                benchmark=benchmark,
                calendar=calendar,
                benchmark_calendar=benchmark_calendar,
                horizon=horizon,
                evaluation_as_of=config.evaluation_cutoff,
            )
            if label is not None:
                labels.append(label.model_dump())

    assessment_trading_sessions = sorted(
        {day for values in calendars.values() for day in values}
    )
    assessment = _assessment(
        config=config,
        scans=scans,
        snapshots=snapshots,
        labels=labels,
        trading_sessions=assessment_trading_sessions,
    )
    # Hash the complete domain payload before adding the manifest linkage.
    # The manifest itself records this digest; excluding the linkage avoids a
    # circular hash while still covering every scan/setup/trigger/snapshot,
    # label and assessment value that drives the report.
    final_payload_hash = _sha(
        {
            "scans": _json_value(scans),
            "setups": _json_value(setups),
            "triggers": _json_value(triggers),
            "snapshots": _json_value(snapshots),
            "labels": _json_value(labels),
            "assessment": _json_value(assessment),
        }
    )
    input_hashes = {
        "raw_payload": _sha(_json_value(payload)),
        "universe": _sha(_json_value(universe_items)),
        "calendar": _sha(_json_value(calendars)),
        "rules": effective_rules_hash,
        "source_modes": _sha(sorted({e.source_mode for e in evidence_by_symbol.values()})),
        "data_sources": _sha(sorted({e.data_source for e in evidence_by_symbol.values()})),
        "code_quality": _module_file_hash(__file__),
        "final_payload": final_payload_hash,
        "config": _sha(
            {
                "mode": config.mode,
                "market_timezone": config.market_timezone,
                "evaluation_as_of": config.evaluation_cutoff.isoformat(),
                "development": [config.development_start, config.development_end],
                "lock": [config.lock_start, config.lock_end],
                "primary_source_mode": config.primary_source_mode,
                "primary_data_source": config.primary_data_source,
                "primary_horizon": config.primary_horizon,
                "primary_view_statuses": list(config.primary_view_statuses),
                "seed": config.seed,
                "bootstrap_replicates": config.bootstrap_replicates,
                "bootstrap_block_sessions": config.bootstrap_block_sessions,
                "dataset_id": config.dataset_id,
            }
        ),
    }
    try:
        from app.buy_opportunities import breakout, scanner, view_projection

        input_hashes.update(
            {
                "code_breakout": _module_file_hash(breakout.__file__),
                "code_scanner": _module_file_hash(scanner.__file__),
                "code_view_projection": _module_file_hash(view_projection.__file__),
            }
        )
    except (ImportError, AttributeError):
        input_hashes["code_view_projection"] = ""
    try:
        from app.buy_opportunities import daily_replay

        input_hashes["code_daily_replay"] = _module_file_hash(daily_replay.__file__)
    except (ImportError, AttributeError):
        input_hashes["code_daily_replay"] = ""
    try:
        from app.research import manifest as research_manifest

        input_hashes["code_manifest"] = _module_file_hash(research_manifest.__file__)
    except (ImportError, AttributeError):
        input_hashes["code_manifest"] = ""
    manifest = ResearchManifest.build(
        run_id=config.run_id,
        created_at=config.created_at,
        pool_version=config.pool_version,
        knowledge_at=config.knowledge_at,
        source_mode=(
            next(iter({e.source_mode for e in evidence_by_symbol.values()}))
            if len({e.source_mode for e in evidence_by_symbol.values()}) == 1
            else "mixed"
        ),
        parameters={
            "schema_version": SCHEMA_VERSION,
            "mode": config.mode,
            "market_timezone": config.market_timezone,
            "rules_hash": effective_rules_hash,
            "evaluation_as_of": config.evaluation_cutoff.isoformat(),
            "development": [config.development_start.isoformat(), config.development_end.isoformat()],
            "lock": [config.lock_start.isoformat(), config.lock_end.isoformat()],
            "horizons": list(HORIZONS),
            "bootstrap_block_sessions": config.bootstrap_block_sessions,
            "bootstrap_replicates": config.bootstrap_replicates,
            "seed": config.seed,
            "primary_source_mode": config.primary_source_mode,
            "primary_data_source": config.primary_data_source,
            "primary_horizon": config.primary_horizon,
            "primary_view_statuses": list(config.primary_view_statuses),
            "dataset_id": config.dataset_id,
            "final_payload_hash": final_payload_hash,
        },
        universe_symbols=[
            normalise_symbol(str(item.get("symbol") if isinstance(item, Mapping) else getattr(item, "symbol", "")))
            for item in universe_items
        ],
        input_hashes=input_hashes,
    )
    assessment["manifest_hash"] = manifest.content_sha256
    assessment["content_sha256"] = content_sha256(
        {key: _json_value(value) for key, value in assessment.items() if key != "content_sha256"}
    )
    return QualityRunResult(
        manifest=manifest,
        scans=scans,
        setups=setups,
        triggers=triggers,
        snapshots=snapshots,
        labels=labels,
        assessment=assessment,
    )


__all__ = [
    "ASSESSMENT_SCHEMA_VERSION",
    "BOOTSTRAP_BLOCK_SESSIONS",
    "DEFAULT_PRIMARY_VIEW_STATUSES",
    "ForwardSessionLabel",
    "HORIZONS",
    "LABEL_SCHEMA_VERSION",
    "OpportunityQualityError",
    "QualityConfig",
    "QualityRunResult",
    "SCHEMA_VERSION",
    "synchronized_session_block_bootstrap",
    "run_opportunity_quality",
]
