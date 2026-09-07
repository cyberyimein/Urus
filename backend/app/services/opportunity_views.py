"""Read-only adapters for the daily opportunity view API.

The daily dataset and chart projection are immutable inputs.  This module only
adapts those two frozen payloads into the v3 replay/view contracts; it does not
collect market data, persist a derived view, or import the research outcome
packages.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from typing import Any

from app.buy_opportunities.contracts import FrozenEvidence, EvidenceBar
from app.buy_opportunities.daily_replay import DailyReplayError, replay_daily_views
from app.buy_opportunities.evidence import freeze_daily_evidence
from app.buy_opportunities.lifecycle import TradingSessionCalendar
from app.buy_opportunities.view_contracts import OpportunityView, view_content_hash
from app.buy_opportunities.view_projection import project_daily_view
from app.core.config import Settings
from app.core.time import as_utc, utc_now
from app.decision_harness.market_evidence import DailyMarketEvidenceService
from app.services.opportunity_view_replay import (
    OpportunityReplayBusy,
    OpportunityReplayCancelled,
    OpportunityReplayError,
    OpportunityReplayTimeout,
    default_opportunity_replay_coordinator,
)


RESPONSE_SCHEMA = "urus.daily_opportunity_view_response.v1"
SOURCE_MODE = "historical_reconstruction"
DATA_SOURCE = "unknown"
DEFAULT_EXCHANGE = "XNYS"
DEFAULT_ADJUSTMENT = "QFQ"

def _visible_quality_codes(values: Sequence[object]) -> list[str]:
    """Keep the API's diagnostic vocabulary intact; the UI filters metadata.

    The response remains useful to operators and tests that need to diagnose
    a historical reconstruction.  Product-facing components decide which
    diagnostics belong in the main card and which stay in the trace section.
    """
    return list(
        dict.fromkeys(
            str(item)
            for item in values
            if item is not None
        )
    )


def _value(item: object, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _unique_symbols(values: Sequence[object] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        symbol = str(value or "").strip().upper()
        if symbol and symbol not in result:
            result.append(symbol)
    return result


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value is None:
        return None
    else:
        raw = str(value).strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return as_utc(parsed)


def _parse_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _iso(value: object) -> str | None:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None:
        return None
    return str(value)


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _manifest_by_symbol(dataset: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    manifests = dataset.get("bar_manifest")
    if not isinstance(manifests, Sequence) or isinstance(manifests, (str, bytes)):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for item in manifests:
        if not isinstance(item, Mapping):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        if symbol:
            result[symbol] = item
    return result


def _quality_for_instrument(
    dataset: Mapping[str, Any],
    instrument: Mapping[str, Any] | None,
    manifest: Mapping[str, Any] | None,
    symbol: str,
) -> tuple[str, list[str]]:
    """Map the frozen chart quality vocabulary to FrozenEvidence's vocabulary."""

    dataset_quality = dataset.get("quality")
    dataset_symbols = (
        dataset_quality.get("symbols")
        if isinstance(dataset_quality, Mapping)
        else None
    )
    dataset_symbol_quality = (
        dataset_symbols.get(symbol)
        if isinstance(dataset_symbols, Mapping)
        else None
    )
    # The dataset quality is the frozen source of truth.  Chart projections
    # from older payloads may omit the per-instrument quality block entirely.
    quality = dataset_symbol_quality or _value(instrument, "quality", {})
    if not isinstance(quality, Mapping):
        quality = {}
    raw_status = str(
        quality.get("status")
        or _value(manifest, "quality_status", "")
        or "ok"
    ).strip().lower()
    warnings: list[str] = []
    raw_warnings = quality.get("warnings")
    if isinstance(raw_warnings, Sequence) and not isinstance(raw_warnings, (str, bytes)):
        warnings.extend(str(item) for item in raw_warnings)
    if raw_status in {"missing", "conflicted", "failed", "unavailable"}:
        return "unavailable", warnings + [f"chart_quality_{raw_status}"]
    if raw_status in {"partial", "stale"}:
        return "partial", warnings + [f"chart_quality_{raw_status}"]
    return "ok", warnings


@lru_cache(maxsize=8)
def _exchange_calendar(exchange: str) -> Any:
    # exchange_calendars is intentionally loaded at the adapter boundary.  A
    # normal API request that does not read a daily dataset should not allocate
    # its pandas-backed calendars.
    import exchange_calendars as xcals

    return xcals.get_calendar(exchange)


def _calendar_dates_and_closes(
    *,
    exchange: str,
    start: date,
    end: date,
) -> tuple[TradingSessionCalendar, dict[date, datetime]]:
    if start > end:
        return TradingSessionCalendar(session_dates=[start]), {}
    calendar = _exchange_calendar(exchange)
    sessions = calendar.sessions_in_range(start.isoformat(), end.isoformat())
    dates = [item.date() for item in sessions]
    closes: dict[date, datetime] = {}
    for session in sessions:
        session_date = session.date()
        close = calendar.session_close(session).to_pydatetime()
        if close.tzinfo is None or close.utcoffset() is None:
            close = close.replace(tzinfo=UTC)
        closes[session_date] = close.astimezone(UTC)
    return TradingSessionCalendar(session_dates=dates), closes


def _asset_type(
    dataset: Mapping[str, Any],
    instrument: Mapping[str, Any] | None,
    symbol: str,
) -> str:
    candidate = _value(instrument, "asset_type")
    if candidate is None:
        scope = dataset.get("scope")
        if isinstance(scope, Mapping):
            asset_types = scope.get("asset_types")
            if isinstance(asset_types, Mapping):
                candidate = asset_types.get(symbol)
    candidate = str(candidate or "equity").strip().lower()
    # ``market`` is the only non-stock asset type representable by the shared
    # ScanRecord contract.  It keeps an explicitly unsupported asset out of
    # the equity/ETF setup path instead of silently relabelling it equity.
    return candidate if candidate in {"equity", "etf", "market"} else "market"


def _price_basis(dataset: Mapping[str, Any], manifest: Mapping[str, Any] | None) -> str:
    explicit = dataset.get("price_basis")
    if explicit is None:
        explicit = _value(manifest, "price_basis")
    if str(explicit or "").strip().lower() in {"raw", "adjusted"}:
        return str(explicit).strip().lower()
    adjustment = str(_value(manifest, "adjustment", DEFAULT_ADJUSTMENT) or DEFAULT_ADJUSTMENT)
    return "raw" if adjustment.strip().upper() in {"NONE", "RAW"} else "adjusted"


def _bars_from_chart(
    *,
    symbol: str,
    instrument: Mapping[str, Any],
    manifest: Mapping[str, Any] | None,
    cutoff: datetime,
    target_date: date | None,
    calendar: TradingSessionCalendar,
    session_closes: Mapping[date, datetime],
    price_basis: str,
) -> tuple[list[EvidenceBar], list[str]]:
    """Rebuild only timing fields that the chart projection omitted.

    The chart contains frozen OHLCV values but no per-bar availability,
    collection timestamp, source revision, or session identity.  Those fields
    therefore remain explicitly unknown except for the dataset cutoff and the
    official exchange session close used for completeness checks.
    """

    price = instrument.get("price")
    raw_bars = price.get("bars") if isinstance(price, Mapping) else None
    if not isinstance(raw_bars, Sequence) or isinstance(raw_bars, (str, bytes)):
        return [], ["chart_price_bars_missing"]
    source = str(_value(manifest, "source", "frozen_chart") or "frozen_chart").strip()
    if not source:
        source = "frozen_chart"
    adjustment = str(
        _value(manifest, "adjustment", DEFAULT_ADJUSTMENT) or DEFAULT_ADJUSTMENT
    ).strip().upper()
    warnings = [
        "historical_reconstruction",
        "per_bar_available_at_unavailable",
        "per_bar_source_revision_unavailable",
        "per_bar_session_id_unavailable",
    ]
    output: list[EvidenceBar] = []
    calendar_dates = set(calendar.session_dates)
    for raw in raw_bars:
        if not isinstance(raw, Mapping):
            warnings.append("chart_bar_invalid")
            continue
        bar_date = _parse_date(raw.get("date"))
        if bar_date is None:
            warnings.append("chart_bar_date_invalid")
            continue
        # A dataset is frozen through its declared trading_date.  A later chart
        # bar must not silently turn an old dataset into a newer view.
        if target_date is not None and bar_date > target_date:
            warnings.append("chart_bar_after_dataset_trading_date")
            continue
        if bar_date > cutoff.date():
            warnings.append("chart_bar_after_dataset_cutoff")
            continue
        if bar_date not in calendar_dates:
            warnings.append("chart_bar_outside_exchange_calendar")
            continue
        if bar_date not in session_closes:
            warnings.append("chart_session_close_unavailable")
            continue
        numbers = {
            field: _finite_float(raw.get(field))
            for field in ("open", "high", "low", "close", "volume")
        }
        if any(value is None for value in numbers.values()):
            warnings.append("chart_bar_ohlcv_invalid")
            continue
        turnover = _finite_float(raw.get("turnover"))
        turnover_rate = _finite_float(raw.get("turnover_rate"))
        try:
            output.append(
                EvidenceBar(
                    date=bar_date,
                    open=numbers["open"],
                    high=numbers["high"],
                    low=numbers["low"],
                    close=numbers["close"],
                    volume=numbers["volume"],
                    turnover=turnover,
                    turnover_rate=turnover_rate,
                    adjustment=adjustment,
                    # This is a reconstruction cutoff, not an observation time
                    # claimed by the original provider.
                    available_at=cutoff,
                    collected_at=cutoff,
                    source=source,
                    source_revision="unknown",
                    session_id=None,
                    session_open_at=None,
                    session_close_at=session_closes[bar_date],
                )
            )
        except ValueError:
            warnings.append("chart_bar_ohlcv_invalid")
    output.sort(key=lambda item: item.date)
    return output, list(dict.fromkeys(warnings))


def _evidence_for_chart_symbol(
    *,
    dataset: Mapping[str, Any],
    chart: Mapping[str, Any],
    symbol: str,
    cutoff: datetime,
    viewed_at: datetime,
    target_date: date | None,
    benchmark: str | None,
    warnings: list[str],
) -> tuple[FrozenEvidence | None, TradingSessionCalendar | None]:
    instruments = chart.get("instruments")
    instrument = instruments.get(symbol) if isinstance(instruments, Mapping) else None
    if not isinstance(instrument, Mapping):
        warnings.append(f"{symbol}:chart_instrument_missing")
        return None, None
    manifest = _manifest_by_symbol(dataset).get(symbol)
    exchange = str(_value(manifest, "exchange", DEFAULT_EXCHANGE) or DEFAULT_EXCHANGE).strip()
    if not exchange:
        exchange = DEFAULT_EXCHANGE
    price = instrument.get("price")
    raw_bars = price.get("bars") if isinstance(price, Mapping) else None
    raw_dates = [
        parsed
        for raw in (raw_bars if isinstance(raw_bars, Sequence) else [])
        if isinstance(raw, Mapping)
        for parsed in [_parse_date(raw.get("date"))]
        if parsed is not None
    ]
    if target_date is not None:
        raw_dates = [item for item in raw_dates if item <= target_date]
    raw_dates = [item for item in raw_dates if item <= cutoff.date()]
    if not raw_dates:
        warnings.append(f"{symbol}:chart_bars_missing_or_future")
        return None, None
    try:
        calendar, session_closes = _calendar_dates_and_closes(
            exchange=exchange,
            start=min(raw_dates),
            end=max(viewed_at.date(), cutoff.date(), viewed_at.date() + timedelta(days=7)),
        )
    except Exception as exc:
        warnings.append(f"{symbol}:exchange_calendar_unavailable:{exchange}")
        return None, None
    basis = _price_basis(dataset, manifest)
    bars, reconstruction_warnings = _bars_from_chart(
        symbol=symbol,
        instrument=instrument,
        manifest=manifest,
        cutoff=cutoff,
        target_date=target_date,
        calendar=calendar,
        session_closes=session_closes,
        price_basis=basis,
    )
    warnings.extend(f"{symbol}:{item}" for item in reconstruction_warnings)
    if not bars:
        return None, calendar
    quality_status, quality_warnings = _quality_for_instrument(
        dataset,
        instrument,
        manifest,
        symbol,
    )
    warnings.extend(f"{symbol}:{item}" for item in quality_warnings)
    if quality_status != "ok":
        # replay_daily_views rebuilds its in-memory reader from bars.  Do not
        # let that reconstruction erase a frozen dataset quality rejection
        # (for example a conflicted symbol) and accidentally create a setup.
        return None, calendar
    try:
        evidence = freeze_daily_evidence(
            symbol=symbol,
            asset_type=_asset_type(dataset, instrument, symbol),
            benchmark=benchmark,
            bars=bars,
            knowledge_at=cutoff,
            source_mode=SOURCE_MODE,
            price_basis=basis,
            # A chart payload has no mapping identity.  Preserve null rather
            # than implying that raw and adjusted prices are comparable.
            price_mapping_id=dataset.get("price_mapping_id"),
            data_source=DATA_SOURCE,
            trading_date=target_date,
            session_dates=calendar.session_dates,
            default_source=str(_value(manifest, "source", "frozen_chart") or "frozen_chart"),
            default_adjustment=str(
                _value(manifest, "adjustment", DEFAULT_ADJUSTMENT) or DEFAULT_ADJUSTMENT
            ),
        )
    except (TypeError, ValueError) as exc:
        warnings.append(f"{symbol}:frozen_evidence_invalid:{exc}")
        return None, calendar
    return evidence, calendar


def _dataset_symbols(dataset: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    scope = dataset.get("scope")
    if not isinstance(scope, Mapping):
        scope = {}
    symbols = _unique_symbols(
        scope.get("symbols") if isinstance(scope.get("symbols"), Sequence) else None
    )
    benchmarks = _unique_symbols(
        scope.get("benchmark_symbols")
        if isinstance(scope.get("benchmark_symbols"), Sequence)
        else []
    )
    # Explicit scope symbols are authoritative, including a symbol that also
    # appears in benchmark_symbols.  Never infer an opportunity universe from
    # chart instruments because that mapping also contains benchmarks.
    return symbols, benchmarks


def _reference_from_opportunity(opportunity: OpportunityView) -> dict[str, Any]:
    return {
        "price": opportunity.reference_price,
        "kind": (
            opportunity.reference_kind
            if opportunity.reference_kind == "daily_close"
            else "none"
        ),
        "market_as_of": _iso(opportunity.market_as_of),
    }


def _reference_from_evidence(evidence: FrozenEvidence | None) -> dict[str, Any]:
    if evidence is None or not evidence.bars:
        return {"price": None, "kind": "none", "market_as_of": None}
    latest = evidence.bars[-1]
    return {
        "price": latest.close,
        "kind": "daily_close",
        "market_as_of": _iso(latest.session_close_at or latest.date),
    }


def _unavailable_rows(symbols: Sequence[str], reason: str) -> list[dict[str, Any]]:
    return [
        {
            "symbol": symbol,
            "status": "unavailable",
            "scan": {
                "scan_id": f"{symbol}:scan:unavailable",
                "status": "coverage_miss",
                "rejection_codes": [reason],
            },
            "opportunity": None,
            "reference": {"price": None, "kind": "none", "market_as_of": None},
            "quality": {"status": "unavailable", "warnings": [reason]},
            "reason_codes": [reason, "no_setup"],
            "setup_observed_at": None,
            "setup_valid_from": None,
            "setup_invalidated_at": None,
            "plan_state": "unavailable",
        }
        for symbol in symbols
    ]


def _setup_timeline(
    setup: object | None,
    *,
    evidence: FrozenEvidence | None,
    view_date: date | None,
) -> dict[str, Any]:
    """Expose exact setup dates without changing the immutable setup model."""

    if setup is None:
        return {
            "setup_observed_at": None,
            "setup_valid_from": None,
            "setup_invalidated_at": None,
            "plan_state": "unavailable",
        }
    observed_at = _parse_date(_value(setup, "observed_at"))
    valid_from = _parse_date(_value(setup, "valid_from"))
    status = str(_value(setup, "status", "") or "").strip().lower()
    invalidated_at: date | None = None
    if status == "invalidated":
        # The lifecycle status is retained on the setup, while the first
        # breached bar is the date the user needs for a useful review.
        if evidence is not None:
            stop = _finite_float(_value(setup, "invalidation_stop"))
            if stop is not None and valid_from is not None:
                for bar in evidence.bars:
                    if bar.date >= valid_from and float(bar.low) <= stop:
                        invalidated_at = bar.date
                        break
        # A malformed/legacy replay may omit the evidence prefix.  The row's
        # observed session is still a better fallback than claiming unknown.
        invalidated_at = invalidated_at or view_date
    return {
        "setup_observed_at": _iso(observed_at),
        "setup_valid_from": _iso(valid_from),
        "setup_invalidated_at": _iso(invalidated_at),
        "plan_state": (
            "historical" if status in {"invalidated", "expired"} else "current"
        ),
    }


def _row_for_replay(
    *,
    row: Mapping[str, Any],
    dataset_id: str,
    viewed_at: datetime,
    knowledge_at: datetime,
    calendar: TradingSessionCalendar | None,
    benchmark: str | None,
    market_timezone: str,
) -> dict[str, Any]:
    symbol = str(row.get("symbol") or "").strip().upper()
    scan = row.get("scan")
    setup = row.get("setup")
    trigger = row.get("trigger")
    evidence = row.get("evidence")
    row_reasons = [str(item) for item in (row.get("reason_codes") or [])]
    evidence_value = evidence if isinstance(evidence, FrozenEvidence) else None
    view_date = _parse_date(row.get("view_date"))
    scan_id = str(_value(scan, "scan_id", f"{symbol}:scan:unavailable"))
    scan_status = str(_value(scan, "status", "coverage_miss"))
    rejection_codes = [str(item) for item in (_value(scan, "rejection_codes", []) or [])]
    if setup is None:
        reasons = _visible_quality_codes([*row_reasons, *rejection_codes, "no_setup"])
        return {
            "symbol": symbol,
            "status": "unavailable",
            "scan": {
                "scan_id": scan_id,
                "status": scan_status,
                "rejection_codes": rejection_codes,
            },
            "opportunity": None,
            "reference": _reference_from_evidence(
                evidence_value
            ),
            "quality": {
                "status": (
                    evidence_value.quality_status
                    if evidence_value is not None
                    and evidence_value.quality_status in {"ok", "partial", "unavailable"}
                    else "unavailable"
                ),
                "warnings": _visible_quality_codes(
                    [*(evidence_value.warnings if evidence_value is not None else []), *reasons]
                ),
            },
            "reason_codes": reasons,
            **_setup_timeline(None, evidence=evidence_value, view_date=view_date),
        }
    if calendar is None:
        # This is defensive only: replay normally provides a calendar for every
        # row with a setup.  Keep a structured unavailable row if malformed
        # input bypasses that invariant.
        reasons = _visible_quality_codes(
            [*row_reasons, "calendar_unavailable", "no_setup_projection"]
        )
        return {
            "symbol": symbol,
            "status": "unavailable",
            "scan": {
                "scan_id": scan_id,
                "status": scan_status,
                "rejection_codes": rejection_codes,
            },
            "opportunity": None,
            "reference": _reference_from_evidence(
                evidence_value
            ),
            "quality": {"status": "unavailable", "warnings": _visible_quality_codes(reasons)},
            "reason_codes": reasons,
            **_setup_timeline(setup, evidence=evidence_value, view_date=view_date),
        }
    try:
        opportunity = project_daily_view(
            setup,
            trigger,
            evidence=evidence_value,
            viewed_at=viewed_at,
            knowledge_at=knowledge_at,
            calendar=calendar,
            dataset_id=dataset_id,
            # The row's scan is the evaluation-frame scan.  The opportunity
            # itself remains associated with the immutable scan that created
            # its setup, as required by the view contract.
            scan_id=getattr(setup, "origin_scan_id", scan_id),
            source_mode=SOURCE_MODE,
            data_source=DATA_SOURCE,
            price_basis=getattr(setup, "price_basis", "adjusted"),
            price_mapping_id=getattr(setup, "price_mapping_id", None),
            market_timezone=market_timezone,
            research_support="unknown",
        )
    except (TypeError, ValueError) as exc:
        reasons = _visible_quality_codes(
            [*row_reasons, "view_projection_unavailable", str(exc)]
        )
        return {
            "symbol": symbol,
            "status": "unavailable",
            "scan": {
                "scan_id": scan_id,
                "status": scan_status,
                "rejection_codes": rejection_codes,
            },
            "opportunity": None,
            "reference": _reference_from_evidence(
                evidence_value
            ),
            "quality": {"status": "unavailable", "warnings": _visible_quality_codes(reasons)},
            "reason_codes": reasons,
            **_setup_timeline(setup, evidence=evidence_value, view_date=view_date),
        }
    reasons = _visible_quality_codes([*row_reasons, *opportunity.reason_codes])
    timeline = _setup_timeline(setup, evidence=evidence_value, view_date=view_date)
    opportunity_payload = opportunity.model_dump(mode="python")
    # These are view metadata fields rather than strategy inputs.  Keep them
    # alongside the projected opportunity so the UI can explain whether the
    # card is a current plan or a historical replay without reverse-engineering
    # SetupRecord internals.
    opportunity_payload.update(timeline)
    opportunity_payload["quality_warnings"] = _visible_quality_codes(
        opportunity.quality_warnings
    )
    opportunity_payload["reason_codes"] = reasons
    if isinstance(opportunity_payload.get("event_risk"), Mapping):
        event_risk = dict(opportunity_payload["event_risk"])
        event_risk["codes"] = _visible_quality_codes(event_risk.get("codes", []))
        opportunity_payload["event_risk"] = event_risk
    opportunity_payload["content_sha256"] = view_content_hash(opportunity_payload)
    return {
        "symbol": symbol,
        "status": opportunity.status,
        "scan": {
            "scan_id": scan_id,
            "status": scan_status,
            "rejection_codes": rejection_codes,
        },
        "opportunity": opportunity_payload,
        "reference": _reference_from_opportunity(opportunity),
        "quality": {
            "status": opportunity.quality,
            "warnings": _visible_quality_codes(opportunity.quality_warnings),
        },
        "reason_codes": reasons,
        **timeline,
    }


def build_dataset_opportunity_view(
    dataset: Mapping[str, Any],
    chart: Mapping[str, Any] | None,
    *,
    viewed_at: datetime,
    market_timezone: str = "America/New_York",
    symbol: str | None = None,
) -> dict[str, Any]:
    """Build the fixed API payload from two frozen Daily Evidence payloads.

    ``viewed_at`` is explicit so tests and historical replays can inject it.
    The route supplies the server's current UTC time.  A dataset cutoff that is
    later than that read time is bounded to the read time and produces an
    unavailable reconstruction instead of exposing future knowledge.
    """

    if not isinstance(dataset, Mapping):
        raise TypeError("dataset 必须是 mapping")
    if viewed_at.tzinfo is None or viewed_at.utcoffset() is None:
        raise ValueError("viewed_at 必须包含时区")
    viewed_at = as_utc(viewed_at)
    symbols, benchmark_symbols = _dataset_symbols(dataset)
    requested_symbol = str(symbol or "").strip().upper() or None
    if requested_symbol is not None:
        if requested_symbol not in symbols:
            raise ValueError(f"symbol {requested_symbol} 不属于该 Daily Decision Dataset")
        symbols = [requested_symbol]
    raw_cutoff = _parse_datetime(dataset.get("cutoff_time"))
    warnings: list[str] = []
    if raw_cutoff is None:
        warnings.append("dataset_cutoff_unavailable")
        return {
            "schema_version": RESPONSE_SCHEMA,
            "dataset_id": str(dataset.get("dataset_id") or "unknown"),
            "viewed_at": viewed_at.isoformat(),
            "knowledge_at": None,
            "views": _unavailable_rows(symbols, "dataset_cutoff_unavailable"),
            "warnings": warnings,
        }
    if raw_cutoff > viewed_at:
        warnings.append("dataset_cutoff_after_viewed_at")
    knowledge_at = min(raw_cutoff, viewed_at)
    dataset_id = str(dataset.get("dataset_id") or "")
    if not dataset_id:
        warnings.append("dataset_id_missing")
        dataset_id = "unknown"
    if not symbols:
        warnings.append("dataset_scope_symbols_missing")
    target_date = _parse_date(dataset.get("trading_date"))
    if target_date is None:
        scope = dataset.get("scope")
        target_date = _parse_date(scope.get("trading_date")) if isinstance(scope, Mapping) else None
    if target_date is not None and target_date > knowledge_at.date():
        warnings.append("dataset_trading_date_after_knowledge_cutoff")

    # replay_daily_views requires a date/time pair on one calendar date.  The
    # dataset cutoff date lets it select the latest prior complete exchange
    # session even when the cutoff is on a weekend or after the target close.
    view_day = min(viewed_at.date(), knowledge_at.date())
    replay_view_time = knowledge_at if knowledge_at.date() == view_day else viewed_at
    if replay_view_time.date() != view_day:
        replay_view_time = datetime.combine(view_day, time.min, tzinfo=UTC)
    chart_mapping = chart if isinstance(chart, Mapping) else None
    if chart_mapping is None:
        warnings.append("chart_projection_missing")

    evidence_by_symbol: dict[str, FrozenEvidence] = {}
    calendars: dict[str, TradingSessionCalendar] = {}
    calendar_by_symbol: dict[str, TradingSessionCalendar | None] = {}
    unsupported_symbols: set[str] = set()
    for symbol in symbols:
        if chart_mapping is None:
            calendar_by_symbol[symbol] = None
            continue
        symbol_benchmark = next(
            (item for item in benchmark_symbols if item != symbol),
            None,
        )
        instruments = chart_mapping.get("instruments")
        instrument = (
            instruments.get(symbol)
            if isinstance(instruments, Mapping)
            else None
        )
        if _asset_type(
            dataset,
            instrument if isinstance(instrument, Mapping) else None,
            symbol,
        ) == "market":
            unsupported_symbols.add(symbol)
            warnings.append(f"{symbol}:asset_type_ineligible")
            calendar_by_symbol[symbol] = None
            continue
        evidence, calendar = _evidence_for_chart_symbol(
            dataset=dataset,
            chart=chart_mapping,
            symbol=symbol,
            cutoff=raw_cutoff,
            viewed_at=viewed_at,
            target_date=target_date,
            benchmark=symbol_benchmark,
            warnings=warnings,
        )
        calendar_by_symbol[symbol] = calendar
        if evidence is not None:
            evidence_by_symbol[symbol] = evidence
            if calendar is not None:
                calendars[symbol] = calendar

    universe_items = [
        {
            "symbol": symbol,
            "asset_type": _asset_type(
                dataset,
                (
                    chart_mapping.get("instruments", {}).get(symbol)
                    if chart_mapping is not None
                    and isinstance(chart_mapping.get("instruments"), Mapping)
                    else None
                ),
                symbol,
            ),
            "enabled": True,
            "collection": {"daily_history": True},
        }
        for symbol in symbols
    ]
    pool_version = f"daily_dataset:{dataset_id}"
    try:
        replay = replay_daily_views(
            universe_items=universe_items,
            evidence_by_symbol=evidence_by_symbol,
            calendars=calendars,
            view_days=[view_day],
            view_times={view_day: replay_view_time},
            pool_version=pool_version,
            knowledge_at=knowledge_at,
            # The view response only needs the final replay row.  Retaining a
            # full source manifest on every historical scan is quadratic in
            # (sessions × bars) and can otherwise consume gigabytes for a
            # normal multi-symbol dataset.  Full manifests remain the default
            # for explicit research replays.
            include_scan_manifests=False,
        )
    except (DailyReplayError, TypeError, ValueError) as exc:
        # A malformed individual chart should remain a structured read result;
        # do not make GET fail with a 500 or invent an opportunity.
        warnings.append(f"daily_replay_unavailable:{exc}")
        replay = {
            "views": [
                {
                    "symbol": symbol,
                    "scan": {
                        "scan_id": f"{symbol}:scan:unavailable",
                        "status": "coverage_miss",
                        "rejection_codes": ["replay_unavailable"],
                    },
                    "setup": None,
                    "trigger": None,
                    "evidence": evidence_by_symbol.get(symbol),
                    "reason_codes": ["replay_unavailable"],
                }
                for symbol in symbols
            ]
        }

    rows_by_symbol: dict[str, dict[str, Any]] = {}
    for row in replay.get("views", []):
        symbol = str(row.get("symbol") or "").strip().upper()
        if symbol in symbols:
            rows_by_symbol[symbol] = _row_for_replay(
                row=row,
                dataset_id=dataset_id,
                viewed_at=viewed_at,
                knowledge_at=knowledge_at,
                calendar=calendar_by_symbol.get(symbol),
                benchmark=next(
                    (item for item in benchmark_symbols if item != symbol),
                    None,
                ),
                market_timezone=market_timezone,
            )
            if symbol in unsupported_symbols:
                row = rows_by_symbol[symbol]
                row["status"] = "unavailable"
                row["opportunity"] = None
                row["reference"] = {"price": None, "kind": "none", "market_as_of": None}
                row["quality"] = {
                    "status": "unavailable",
                    "warnings": list(
                        dict.fromkeys(
                            [*row["quality"].get("warnings", []), "asset_type_ineligible"]
                        )
                    ),
                }
                row["scan"]["status"] = "rejected"
                row["scan"]["rejection_codes"] = ["asset_type_ineligible"]
                row["reason_codes"] = list(
                    dict.fromkeys([*row["reason_codes"], "asset_type_ineligible"])
                )
    views: list[dict[str, Any]] = []
    for symbol in symbols:
        row = rows_by_symbol.get(symbol)
        if row is None:
            warnings.append(f"{symbol}:replay_row_missing")
            row = {
                "symbol": symbol,
                "status": "unavailable",
                "scan": {
                    "scan_id": f"{symbol}:scan:unavailable",
                    "status": "coverage_miss",
                    "rejection_codes": ["replay_row_missing"],
                },
                "opportunity": None,
                "reference": {"price": None, "kind": "none", "market_as_of": None},
                "quality": {"status": "unavailable", "warnings": ["replay_row_missing"]},
                "reason_codes": ["replay_row_missing"],
            }
        views.append(row)

    return {
        "schema_version": RESPONSE_SCHEMA,
        "dataset_id": dataset_id,
        "viewed_at": viewed_at.isoformat(),
        "knowledge_at": knowledge_at.isoformat(),
        "views": views,
        "warnings": _visible_quality_codes(warnings),
    }


def _refresh_viewed_at(payload: Mapping[str, Any], viewed_at: datetime) -> dict[str, Any]:
    """Refresh read timestamps on a single-flight result before returning it.

    Replay work is independent of the exact wall-clock second.  The
    coordinator therefore shares a result for callers on the same local market
    date, while this function ensures a cached result never reports the first
    caller's ``viewed_at`` or an old view content hash.
    """

    refreshed = dict(payload)
    viewed_iso = as_utc(viewed_at).isoformat()
    refreshed["viewed_at"] = viewed_iso
    rows = refreshed.get("views")
    if not isinstance(rows, list):
        return refreshed
    refreshed_rows: list[dict[str, Any]] = []
    for raw_row in rows:
        if not isinstance(raw_row, Mapping):
            continue
        row = dict(raw_row)
        opportunity = row.get("opportunity")
        if isinstance(opportunity, Mapping):
            value = dict(opportunity)
            value["viewed_at"] = viewed_iso
            value["content_sha256"] = view_content_hash(value)
            row["opportunity"] = value
        refreshed_rows.append(row)
    refreshed["views"] = refreshed_rows
    return refreshed


def _worker_fallback_payload(
    dataset: Mapping[str, Any],
    *,
    viewed_at: datetime,
    symbol: str | None,
    reason: str,
) -> dict[str, Any]:
    raw_cutoff = _parse_datetime(dataset.get("cutoff_time"))
    knowledge_at = min(raw_cutoff, as_utc(viewed_at)) if raw_cutoff is not None else None
    symbols, _ = _dataset_symbols(dataset)
    if symbol is not None:
        symbols = [symbol]
    return {
        "schema_version": RESPONSE_SCHEMA,
        "dataset_id": str(dataset.get("dataset_id") or "unknown"),
        "viewed_at": as_utc(viewed_at).isoformat(),
        "knowledge_at": knowledge_at.isoformat() if knowledge_at is not None else None,
        "views": _unavailable_rows(symbols, reason),
        "warnings": [reason],
    }


class DailyOpportunityViewService:
    """Read-only service facade for ``GET .../opportunity-view``."""

    def __init__(self, session, settings: Settings, *, replay_coordinator=None):
        self._daily = DailyMarketEvidenceService(session, settings)
        self._replay_coordinator = replay_coordinator or default_opportunity_replay_coordinator()

    def get_dataset_opportunity_view(
        self,
        dataset_id: str,
        *,
        viewed_at: datetime | None = None,
        symbol: str | None = None,
        cancel_event=None,
    ) -> dict[str, Any] | None:
        dataset = self._daily.get_dataset(dataset_id)
        if dataset is None:
            return None
        read_time = as_utc(viewed_at or utc_now())
        requested_symbol = str(symbol or "").strip().upper() or None
        symbols, _ = _dataset_symbols(dataset)
        if requested_symbol is not None and requested_symbol not in symbols:
            raise ValueError(f"symbol {requested_symbol} 不属于该 Daily Decision Dataset")

        # Production settings point at the file-backed operational database.
        # In that case the chart is intentionally loaded only by the child.
        # Tests with an in-memory database (or a lightweight fake service) use
        # the pure in-process adapter because a spawned child cannot see that
        # private SQLite memory image.
        bind = getattr(getattr(self._daily, "session", None), "bind", None)
        bound_url = getattr(bind, "url", None)
        database_url = str(bound_url) if bound_url is not None else None
        if not database_url:
            database_url = getattr(getattr(self._daily, "settings", None), "database_url", None)
        is_memory_database = str(database_url or "").endswith(":memory:")
        if not database_url or is_memory_database:
            chart = self._daily.get_chart(dataset_id)
            return build_dataset_opportunity_view(
                dataset,
                chart,
                viewed_at=read_time,
                market_timezone=getattr(self._daily, "market_timezone", "America/New_York"),
                symbol=requested_symbol,
            )

        market_timezone = getattr(self._daily, "market_timezone", "America/New_York")
        market_calendar = getattr(self._daily, "calendar_name", DEFAULT_EXCHANGE)
        local_date = read_time
        try:
            from zoneinfo import ZoneInfo

            local_date = read_time.astimezone(ZoneInfo(market_timezone))
        except Exception:
            # ``market_timezone`` is validated by normal Settings; retaining
            # UTC here keeps a malformed test double deterministic.
            pass
        raw_cutoff = _parse_datetime(dataset.get("cutoff_time"))
        knowledge_at = min(raw_cutoff, read_time) if raw_cutoff is not None else read_time
        key = (
            str(database_url),
            str(dataset.get("dataset_id") or dataset_id),
            requested_symbol or "*",
            knowledge_at.isoformat(),
            local_date.date().isoformat(),
        )
        try:
            payload = self._replay_coordinator.run(
                key=key,
                database_url=str(database_url),
                dataset_id=dataset_id,
                symbol=requested_symbol,
                viewed_at=read_time,
                market_timezone=market_timezone,
                market_calendar=market_calendar,
                daily_min_history_bars=int(
                    getattr(self._daily, "minimum_history_bars", 260)
                ),
                cancel_event=cancel_event,
            )
        except OpportunityReplayBusy:
            return _worker_fallback_payload(
                dataset,
                viewed_at=read_time,
                symbol=requested_symbol,
                reason="replay_busy",
            )
        except OpportunityReplayTimeout:
            return _worker_fallback_payload(
                dataset,
                viewed_at=read_time,
                symbol=requested_symbol,
                reason="replay_timeout",
            )
        except OpportunityReplayCancelled:
            # A disconnected caller must not turn cancellation into a normal
            # "unavailable" response.  Let the async route finish its cleanup
            # and emit the request-cancelled status instead.
            raise
        except OpportunityReplayError:
            return _worker_fallback_payload(
                dataset,
                viewed_at=read_time,
                symbol=requested_symbol,
                reason="replay_unavailable",
            )
        return _refresh_viewed_at(payload, read_time)


__all__ = [
    "DailyOpportunityViewService",
    "DATA_SOURCE",
    "RESPONSE_SCHEMA",
    "SOURCE_MODE",
    "build_dataset_opportunity_view",
]
