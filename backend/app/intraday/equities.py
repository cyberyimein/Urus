"""Pure helpers for projecting completed intraday equity bars."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
import math
from statistics import fmean
from typing import Any
from zoneinfo import ZoneInfo


_MINUTE = timedelta(minutes=1)
_FIVE_MINUTES = timedelta(minutes=5)
_EXPECTED_MINUTES_PER_BAR = 5
_NUMERIC_FIELDS = ("open", "high", "low", "close", "volume", "turnover")


def aggregate_completed_bars(
    minute_bars: Sequence[Mapping[str, Any]],
    *,
    session_open: datetime,
    session_close: datetime,
    cutoff: datetime,
) -> list[dict[str, Any]]:
    """Return complete, session-anchored 5-minute bars from explicit UTC ranges.

    Input rows must carry timezone-aware ``bar_start_at`` and ``bar_end_at``
    values. Missing, conflicting, or invalid minutes invalidate their own
    five-minute window; later windows remain aligned to the session open.
    """

    session_open_utc = _aware_utc(session_open, "session_open")
    session_close_utc = _aware_utc(session_close, "session_close")
    cutoff_utc = _aware_utc(cutoff, "cutoff")
    if session_close_utc <= session_open_utc:
        raise ValueError("session_close must be after session_open")

    windows: dict[tuple[str, datetime], list[tuple[datetime, dict[str, Any] | None]]] = defaultdict(list)
    for row in minute_bars:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        try:
            bar_start = _aware_utc(row.get("bar_start_at"), "bar_start_at")
            bar_end = _aware_utc(row.get("bar_end_at"), "bar_end_at")
        except (TypeError, ValueError):
            continue
        if bar_end - bar_start != _MINUTE:
            continue
        if bar_start < session_open_utc or bar_end > min(session_close_utc, cutoff_utc):
            continue

        elapsed = bar_start - session_open_utc
        window_index = int(elapsed.total_seconds() // _FIVE_MINUTES.total_seconds())
        window_start = session_open_utc + window_index * _FIVE_MINUTES
        window_end = window_start + _FIVE_MINUTES
        if window_end > session_close_utc or window_end > cutoff_utc:
            continue

        values, valid = _validated_minute_values(row)
        if row.get("quality_state", "ready") != "ready":
            valid = False
        windows[(symbol, window_start)].append((bar_start, values if valid else None))

    completed: list[dict[str, Any]] = []
    for (symbol, window_start), rows in sorted(
        windows.items(), key=lambda item: (item[0][1], item[0][0])
    ):
        by_minute: dict[datetime, dict[str, Any] | None] = {}
        conflict = False
        for bar_start, values in rows:
            if values is None:
                conflict = True
                continue
            previous = by_minute.get(bar_start)
            if previous is not None and previous != values:
                conflict = True
            else:
                by_minute[bar_start] = values

        expected_starts = [
            window_start + index * _MINUTE
            for index in range(_EXPECTED_MINUTES_PER_BAR)
        ]
        if conflict or any(start not in by_minute for start in expected_starts):
            continue

        constituents = [by_minute[start] for start in expected_starts]
        # The window completeness check above guarantees these are mappings.
        first = constituents[0]
        last = constituents[-1]
        volume = sum(float(row["volume"]) for row in constituents)
        turnover = sum(float(row["turnover"]) for row in constituents)
        completed.append(
            {
                "symbol": symbol,
                "bar_start_at": window_start.isoformat(),
                "bar_end_at": (window_start + _FIVE_MINUTES).isoformat(),
                "open": float(first["open"]),
                "high": max(float(row["high"]) for row in constituents),
                "low": min(float(row["low"]) for row in constituents),
                "close": float(last["close"]),
                "volume": volume,
                "turnover": turnover,
                "vwap": turnover / volume if volume > 0 else None,
                "minute_count": _EXPECTED_MINUTES_PER_BAR,
                "quality_state": "ready",
            }
        )
    return completed


def build_intraday_projection(
    contract: Mapping[str, Any],
    *,
    now: datetime,
    market_timezone: str = "America/New_York",
    market_calendar: str = "XNYS",
) -> dict[str, Any]:
    """Build the deterministic read model for one validated P2 capture."""

    from app.analytics.technical import calculate_macd_projection
    from app.decision_harness.contracts import content_sha256
    import exchange_calendars as xcals

    now_utc = _aware_utc(now, "now")
    data = contract.get("data")
    if not isinstance(data, Mapping):
        raise ValueError("capture data must be an object")
    minute_timestamp_policy = data.get("minute_timestamp_policy")
    minute_timestamp_boundary_verified = (
        isinstance(minute_timestamp_policy, Mapping)
        and minute_timestamp_policy.get("provider_boundary_verified") is True
    )
    if contract.get("schema_version") != "urus.moomoo.capture.v2":
        raise ValueError("unsupported capture envelope schema")
    if contract.get("kind") != "capture_intraday_equities":
        raise ValueError("unexpected capture kind")
    if contract.get("provider") != "moomoo_openapi":
        raise ValueError("unexpected capture provider")
    if not str(contract.get("task_name") or "").strip():
        raise ValueError("capture task_name must not be empty")
    digest = content_sha256(data)
    if str(contract.get("content_sha256") or "") != digest:
        raise ValueError("capture content hash mismatch")
    compact_references = data.get("schema_version") == "moomoo-intraday-equities-refs-v1"
    if compact_references:
        from app.intraday.references import resolved_capture_data

        data = resolved_capture_data(contract)
    elif data.get("schema_version") != "moomoo-intraday-equities-v1":
        raise ValueError("unsupported intraday equity data schema")
    if data.get("provider") != "moomoo_openapi":
        raise ValueError("unexpected intraday equity data provider")

    trading_day = date.fromisoformat(str(contract.get("trading_date") or ""))
    if str(data.get("trading_date") or "") != trading_day.isoformat():
        raise ValueError("capture trading date mismatch")
    scheduled_at = _aware_utc(contract.get("scheduled_at"), "scheduled_at")
    captured_at = _aware_utc(contract.get("captured_at"), "captured_at")
    data_scheduled_at = _aware_utc(data.get("scheduled_at"), "data.scheduled_at")
    cutoff = _aware_utc(data.get("evidence_cutoff"), "evidence_cutoff")
    if data_scheduled_at != scheduled_at:
        raise ValueError("capture scheduled_at mismatch")
    if cutoff != scheduled_at:
        raise ValueError("capture evidence_cutoff must match scheduled_at")
    if cutoff > captured_at:
        raise ValueError("capture evidence_cutoff is later than captured_at")
    if trading_day != scheduled_at.astimezone(ZoneInfo(market_timezone)).date():
        raise ValueError("capture trading date does not match scheduled slot timezone date")

    requested_symbols = _normalized_symbols(contract.get("requested_symbols"))
    if not requested_symbols:
        raise ValueError("capture requested_symbols must not be empty")
    quote_symbols = _normalized_symbols(data.get("quote_symbols"))
    minute_symbols = _normalized_symbols(data.get("minute_symbols"))
    requested_set = set(requested_symbols)
    if not set(quote_symbols).issubset(requested_set):
        raise ValueError("quote_symbols exceed capture requested_symbols")
    if not set(minute_symbols).issubset(set(quote_symbols)):
        raise ValueError("minute_symbols exceed quote_symbols")
    if any(symbol not in requested_set for symbol in minute_symbols):
        raise ValueError("minute_symbols exceed capture requested_symbols")
    data_requested_symbols = _normalized_symbols(data.get("requested_symbols"))
    if data_requested_symbols and not set(data_requested_symbols).issubset(requested_set):
        raise ValueError("data requested_symbols exceed capture requested_symbols")

    calendar = xcals.get_calendar(market_calendar)
    if not calendar.is_session(trading_day):
        raise ValueError("capture trading date is not an XNYS session")
    session_open = _calendar_datetime(calendar.session_open(trading_day), "session_open")
    session_close = _calendar_datetime(calendar.session_close(trading_day), "session_close")
    if cutoff < session_open or cutoff > session_close:
        raise ValueError("capture evidence_cutoff is outside the regular session")

    raw_bars = data.get("minute_bars", [])
    if not isinstance(raw_bars, list):
        raise ValueError("minute_bars must be a list")
    if any(not isinstance(row, Mapping) for row in raw_bars):
        raise ValueError("minute_bars entries must be objects")
    minute_bars = [dict(row) for row in raw_bars]
    group_by_symbol = {
        str(symbol).strip().upper(): str(role).strip().upper()
        for symbol, role in dict(data.get("group_by_symbol") or {}).items()
        if str(symbol).strip()
    }
    if not set(group_by_symbol).issubset(set(quote_symbols)):
        raise ValueError("group_by_symbol contains symbols outside quote_symbols")
    display_group_by_symbol = {
        str(symbol).strip().upper(): str(group).strip()
        for symbol, group in dict(data.get("display_group_by_symbol") or {}).items()
        if str(symbol).strip()
    }
    if not set(display_group_by_symbol).issubset(set(quote_symbols)):
        raise ValueError("display_group_by_symbol contains symbols outside quote_symbols")
    returned_symbols = [
        str(row.get("symbol") or "").strip().upper()
        for row in minute_bars
        if str(row.get("symbol") or "").strip()
    ]
    if len(returned_symbols) != len(minute_bars):
        raise ValueError("minute_bars entries must include a symbol")
    if not set(returned_symbols).issubset(set(minute_symbols)):
        raise ValueError("minute_bars contain symbols outside minute_symbols")
    symbols = list(dict.fromkeys([*quote_symbols, *minute_symbols, *returned_symbols]))

    symbol_rows: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in symbols}
    invalid_by_symbol: dict[str, int] = {symbol: 0 for symbol in symbols}
    for row in minute_bars:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        symbol_rows.setdefault(symbol, []).append(row)
        try:
            bar_start = _aware_utc(row.get("bar_start_at"), "bar_start_at")
            bar_end = _aware_utc(row.get("bar_end_at"), "bar_end_at")
        except (TypeError, ValueError):
            invalid_by_symbol[symbol] = invalid_by_symbol.get(symbol, 0) + 1
            continue
        if bar_start < session_open or bar_end > min(session_close, cutoff):
            continue
        _values, valid = _validated_minute_values(row)
        if not valid or row.get("quality_state", "ready") != "ready":
            invalid_by_symbol[symbol] = invalid_by_symbol.get(symbol, 0) + 1

    all_projected: dict[str, dict[str, Any]] = {}
    expected_window_count = _expected_window_count(session_open, session_close, cutoff)
    expected_minute_count = _expected_minute_count(session_open, session_close, cutoff)
    expected_latest_end = session_open + expected_window_count * _FIVE_MINUTES
    total_missing_windows = 0
    total_invalid_bars = sum(invalid_by_symbol.values())
    total_available_windows = 0
    for symbol in symbols:
        rows = symbol_rows.get(symbol, [])
        bars = aggregate_completed_bars(
            rows,
            session_open=session_open,
            session_close=session_close,
            cutoff=cutoff,
        )
        continuous_bars = _continuous_suffix(bars)
        requested_minutes = symbol in minute_symbols
        missing_windows = max(0, expected_window_count - len(bars)) if requested_minutes else 0
        total_missing_windows += missing_windows
        if requested_minutes:
            total_available_windows += len(bars)

        features, reasons = _features_for_symbol(
            rows,
            bars,
            continuous_bars,
            symbol=symbol,
            session_open=session_open,
            session_close=session_close,
            cutoff=cutoff,
            expected_minute_count=expected_minute_count,
        )
        last_as_of = _aware_utc(bars[-1]["bar_end_at"], "bar_end_at") if bars else None
        tail_is_stale = bool(
            requested_minutes
            and expected_window_count
            and last_as_of != expected_latest_end
        )
        if not requested_minutes and group_by_symbol.get(symbol) == "D":
            symbol_status = "quote_only"
        elif tail_is_stale:
            symbol_status = "stale"
        elif missing_windows or invalid_by_symbol.get(symbol, 0):
            symbol_status = "partial"
        elif bars:
            symbol_status = "ok"
        else:
            symbol_status = "unavailable"
        all_projected[symbol] = {
            "symbol": symbol,
            "role": group_by_symbol.get(symbol),
            "observation_group": display_group_by_symbol.get(symbol),
            "status": symbol_status,
            "quote": None,
            "as_of": continuous_bars[-1]["bar_end_at"] if continuous_bars else None,
            "coverage": {
                "minute_requested": requested_minutes,
                "minute_bars_received": len(rows),
                "bars_5m_expected": expected_window_count if requested_minutes else 0,
                "bars_5m_available": len(bars),
                "missing_bars_5m": missing_windows,
                "invalid_minute_bars": invalid_by_symbol.get(symbol, 0),
            },
            "bars": bars,
            "features": features,
            "reasons": reasons,
        }

    benchmark_bars = all_projected.get("SPY", {}).get("bars", [])
    for symbol in symbols:
        if symbol == "SPY" or symbol not in minute_symbols:
            continue
        item = all_projected[symbol]
        continuous = _continuous_suffix(item["bars"])
        benchmark_continuous = _continuous_suffix(benchmark_bars)
        rs15, rs30, rs_reasons = _relative_strength(
            continuous,
            benchmark_continuous,
            as_of=continuous[-1]["bar_end_at"] if continuous else None,
        )
        item["features"]["rs15"] = rs15
        item["features"]["rs30"] = rs30
        if rs15 is not None:
            item["reasons"].pop("rs15", None)
        if rs30 is not None:
            item["reasons"].pop("rs30", None)
        item["reasons"].update(rs_reasons)

    quote_rows = data.get("quotes", [])
    if not isinstance(quote_rows, list) or any(
        not isinstance(row, Mapping) for row in quote_rows
    ):
        raise ValueError("quotes must be a list of objects")
    quote_returned = {
        str(row.get("symbol") or "").strip().upper()
        for row in quote_rows
        if isinstance(row, Mapping) and str(row.get("symbol") or "").strip()
    }
    if not quote_returned.issubset(set(quote_symbols)):
        raise ValueError("quotes contain symbols outside quote_symbols")
    quote_by_symbol = {
        str(row.get("symbol") or "").strip().upper(): dict(row)
        for row in quote_rows
        if str(row.get("symbol") or "").strip()
    }
    quote_receipts = _quote_receipt_times(data.get("provider_raw"))
    for symbol, item in all_projected.items():
        item["quote"] = _quote_projection(
            quote_by_symbol.get(symbol),
            provider=str(data.get("provider") or contract.get("provider") or "unknown"),
            receipt_time=quote_receipts.get(symbol),
            market_timezone=market_timezone,
        )
    breadth = _pool_breadth(
        all_projected,
        minute_rows_by_symbol=symbol_rows,
        quote_symbols=quote_symbols,
        minute_symbols=minute_symbols,
        session_open=session_open,
        session_close=session_close,
        cutoff=cutoff,
        expected_latest_end=expected_latest_end,
        expected_window_count=expected_window_count,
    )

    freshness_age = (now_utc - cutoff).total_seconds()
    stale_minute_symbols = [
        symbol
        for symbol in minute_symbols
        if expected_window_count
        and all_projected.get(symbol, {}).get("as_of")
        != expected_latest_end.isoformat()
    ]
    freshness_status = (
        "clock_skew" if freshness_age < 0
        else "stale" if freshness_age > 600 or stale_minute_symbols
        else "fresh"
    )
    source_quality = data.get("quality_summary")
    source_complete = bool(
        isinstance(source_quality, Mapping) and source_quality.get("complete") is True
    )
    quality_status = (
        "partial"
        if not source_complete or total_missing_windows or total_invalid_bars
        else "ok"
    )
    coverage_status = quality_status
    overall_status = "stale" if freshness_status == "stale" else quality_status
    quality_warnings = data.get("quality_warnings", [])
    if not isinstance(quality_warnings, list):
        quality_warnings = []
    summary_warnings = (
        source_quality.get("quality_warnings", [])
        if isinstance(source_quality, Mapping)
        else []
    )
    if not isinstance(summary_warnings, list):
        summary_warnings = []
    warnings = [
        str(item)
        for item in [*quality_warnings, *summary_warnings]
        if str(item)
    ]
    if not isinstance(minute_timestamp_policy, Mapping):
        warnings.append("分钟线时间边界策略缺失，无法确认 provider 的时间戳语义。")
    elif minute_timestamp_policy.get("provider_boundary_verified") is not True:
        warnings.append("provider 分钟线时间边界尚未验证；当前按采集契约中的 UTC start/end 投影。")
    if total_missing_windows:
        warnings.append(f"{total_missing_windows} 个 5 分钟区间覆盖不完整。")
    if total_invalid_bars:
        warnings.append(f"{total_invalid_bars} 根分钟线非法，已从 ready 投影中排除。")
    if freshness_status == "clock_skew":
        warnings.append("证据截止点晚于投影时间，时钟顺序异常。")
    if freshness_status == "stale":
        if freshness_age > 600:
            warnings.append("证据截止点已超过 10 分钟。")
        if stale_minute_symbols:
            warnings.append(
                "分钟线尾部未覆盖预期的最新完整 5 分钟区间："
                + ", ".join(stale_minute_symbols)
                + "。"
            )

    for item in all_projected.values():
        item["features"]["rvol"] = None
        item["reasons"]["rvol"] = (
            "History unavailable：缺少过去 20 个交易日相同时段的历史量能基线。"
        )

    if compact_references:
        from app.intraday.history_features import enrich_indicator_history

        historical_bars = data.get("historical_5m_bars", [])
        adjustment = next(
            (str(ref["adjustment"]) for ref in data["equity_data_refs"] if ref["kind"] == "bars"),
            "none",
        )
        for symbol, item in all_projected.items():
            enriched = enrich_indicator_history(
                item["bars"],
                [row for row in historical_bars if row.get("symbol") == symbol],
                symbol=symbol, trading_date=trading_day, cutoff=cutoff,
                market_calendar=market_calendar, adjustment=adjustment,
            )
            item["features"].update(enriched["features"])
            for name, value in enriched["features"].items():
                if value is not None:
                    item["reasons"].pop(name, None)
            if enriched["features"]["macd_signal_eligible"]:
                item["reasons"].pop("macd_warmup", None)
            item["reasons"].update(enriched["reasons"])

    return {
        "schema_version": "urus.intraday.projection.v1",
        "formula_version": "urus.intraday.equities.v1",
        "status": overall_status,
        "source": {
            "capture_id": str(contract.get("capture_id") or ""),
            "content_sha256": digest,
            "kind": str(contract.get("kind") or ""),
            "schema_version": str(contract.get("schema_version") or ""),
            "provider": str(contract.get("provider") or ""),
            "task_name": str(contract.get("task_name") or ""),
            "trading_date": trading_day.isoformat(),
            "scheduled_at": scheduled_at.isoformat(),
            "cutoff": cutoff.isoformat(),
            "captured_at": captured_at.isoformat(),
            "stored_at": str(contract.get("_stored_at") or "") or None,
        },
        "freshness": {
            "status": freshness_status,
            "age_seconds": freshness_age,
            "max_age_seconds": 600,
            "as_of": cutoff.isoformat(),
            "expected_latest_bar_end": expected_latest_end.isoformat(),
            "stale_minute_symbols": stale_minute_symbols,
        },
        "quality": {
            "status": quality_status,
            "source_complete": source_complete,
            "invalid_minute_bars": total_invalid_bars,
            "minute_timestamp_boundary_verified": minute_timestamp_boundary_verified,
        },
        "coverage": {
            "status": coverage_status,
            "requested_symbols": len(quote_symbols),
            "quote_symbols_returned": len(quote_returned),
            "minute_symbols_requested": len(minute_symbols),
            "minute_symbols_with_bars": sum(
                bool(item["bars"]) for symbol, item in all_projected.items()
                if symbol in minute_symbols
            ),
            "minute_bars_received": len(minute_bars),
            "bars_5m_expected": expected_window_count * len(minute_symbols),
            "bars_5m_available": total_available_windows,
            "missing_bars_5m": total_missing_windows,
            "invalid_minute_bars": total_invalid_bars,
            "expected_minutes_per_symbol": expected_minute_count,
        },
        "symbols": all_projected,
        "breadth": breadth,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _calendar_datetime(value: Any, field: str) -> datetime:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    return _aware_utc(value, field)


def _normalized_symbols(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return list(dict.fromkeys(str(item).strip().upper() for item in value if str(item).strip()))


def _quote_receipt_times(provider_raw: object) -> dict[str, str]:
    if not isinstance(provider_raw, Mapping):
        return {}
    batches = provider_raw.get("market_snapshot_batches")
    if not isinstance(batches, list):
        return {}
    receipts: dict[str, str] = {}
    for batch in batches:
        if not isinstance(batch, Mapping):
            continue
        response_received_at = str(batch.get("response_received_at") or "").strip()
        records = batch.get("records")
        if not response_received_at or not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, Mapping):
                continue
            raw_symbol = record.get("symbol") or record.get("code")
            symbol = str(raw_symbol or "").strip().upper()
            if symbol.startswith("US."):
                symbol = symbol[3:]
            if symbol:
                receipts[symbol] = response_received_at
    return receipts


def _quote_projection(
    row: Mapping[str, Any] | None,
    *,
    provider: str,
    receipt_time: str | None,
    market_timezone: str,
) -> dict[str, Any]:
    if row is None:
        return {
            "last_price": None,
            "provider": provider,
            "quote_time": None,
            "quote_time_raw": None,
            "quote_time_status": "unknown",
            "received_at": receipt_time,
            "quality_status": "unavailable",
            "price_basis": "unknown",
        }
    raw_price = row.get("last_price")
    try:
        last_price = float(raw_price) if raw_price is not None else None
    except (TypeError, ValueError, OverflowError):
        last_price = None
    if last_price is not None and (not math.isfinite(last_price) or last_price <= 0):
        last_price = None
    raw_quote_time = str(
        row.get("quote_time")
        or row.get("update_time")
        or " ".join(
            part
            for part in (
                str(row.get("data_date") or "").strip(),
                str(row.get("data_time") or "").strip(),
            )
            if part
        )
    ).strip() or None
    quote_time = _normalize_quote_time(raw_quote_time, market_timezone=market_timezone)
    received_at = str(row.get("received_at") or receipt_time or "").strip() or None
    row_provider = str(row.get("provider") or "").strip()
    row_source = str(row.get("source") or "").strip()
    quote_provider = row_provider or row_source or provider
    is_known_moomoo_snapshot = (
        provider == "moomoo_openapi"
        and row_provider in {"", "moomoo_openapi"}
        and row_source in {"", "moomoo_opend_snapshot"}
    )
    return {
        "last_price": last_price,
        "provider": quote_provider,
        "quote_time": quote_time,
        "quote_time_raw": raw_quote_time,
        "quote_time_status": "known" if quote_time else "unknown",
        "received_at": received_at,
        "quality_status": (
            str(row.get("quality_status") or row.get("quality_state") or "available")
            if last_price is not None
            else "unavailable"
        ),
        "price_basis": "raw" if is_known_moomoo_snapshot else "unknown",
    }


def _normalize_quote_time(value: str | None, *, market_timezone: str) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(market_timezone))
    return parsed.astimezone(UTC).isoformat()


def _expected_window_count(
    session_open: datetime,
    session_close: datetime,
    cutoff: datetime,
) -> int:
    end = min(session_close, cutoff)
    if end <= session_open:
        return 0
    return int((end - session_open).total_seconds() // _FIVE_MINUTES.total_seconds())


def _expected_minute_count(
    session_open: datetime,
    session_close: datetime,
    cutoff: datetime,
) -> int:
    end = min(session_close, cutoff).replace(second=0, microsecond=0)
    if end <= session_open:
        return 0
    return int((end - session_open).total_seconds() // _MINUTE.total_seconds())


def _continuous_suffix(bars: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    clean = [dict(bar) for bar in bars]
    clean.sort(key=lambda bar: _aware_utc(bar.get("bar_start_at"), "bar_start_at"))
    if not clean:
        return []
    suffix = [clean[-1]]
    for bar in reversed(clean[:-1]):
        next_start = _aware_utc(suffix[0].get("bar_start_at"), "bar_start_at")
        bar_end = _aware_utc(bar.get("bar_end_at"), "bar_end_at")
        if bar_end != next_start:
            break
        suffix.insert(0, bar)
    return suffix


def _features_for_symbol(
    minute_rows: Sequence[Mapping[str, Any]],
    bars: Sequence[Mapping[str, Any]],
    continuous_bars: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    session_open: datetime,
    session_close: datetime,
    cutoff: datetime,
    expected_minute_count: int,
) -> tuple[dict[str, Any], dict[str, str]]:
    reasons: dict[str, str] = {}
    features: dict[str, Any] = {}
    closes = [float(bar["close"]) for bar in continuous_bars]
    as_of = continuous_bars[-1].get("bar_end_at") if continuous_bars else None

    if len(closes) >= 5:
        ma5 = fmean(closes[-5:])
        latest_close = closes[-1]
        features["ma5"] = ma5
        features["ma5_distance_pct"] = (latest_close / ma5 - 1.0) * 100.0
    else:
        features["ma5"] = None
        features["ma5_distance_pct"] = None
        reasons["ma5"] = f"需要 5 根连续完整 5 分钟 bar；当前有 {len(closes)} 根。"
        reasons["ma5_distance_pct"] = reasons["ma5"]

    if len(closes) >= 6:
        current_ma5 = fmean(closes[-5:])
        previous_ma5 = fmean(closes[-6:-1])
        features["ma5_slope"] = current_ma5 - previous_ma5
    else:
        features["ma5_slope"] = None
        reasons["ma5_slope"] = "MA5 slope 需要两根相邻的 MA5，即 6 根连续完整 5 分钟 bar。"

    atr_series = _wilder_atr_series(continuous_bars, period=14)
    features["atr14"] = atr_series[-1] if atr_series else None
    if features["atr14"] is None:
        reasons["atr14"] = f"Wilder ATR14 需要 14 根连续完整 5 分钟 bar；当前有 {len(continuous_bars)} 根。"

    session_vwap, vwap_reason = _session_vwap(
        minute_rows,
        symbol=symbol,
        session_open=session_open,
        session_close=session_close,
        cutoff=cutoff,
        expected_minute_count=expected_minute_count,
    )
    features["session_vwap"] = session_vwap
    if vwap_reason:
        reasons["session_vwap"] = vwap_reason

    features["macd"] = _macd_summary(continuous_bars)
    features["macd_warmup_bars"] = len(continuous_bars)
    features["macd_signal_eligible"] = len(continuous_bars) >= 100
    features["rs15"] = None
    features["rs30"] = None
    features["rvol"] = None
    if not features["macd_signal_eligible"]:
        reasons["macd_warmup"] = "MACD 需要 100 根合格 5 分钟 bar 才能满足策略预热。"
    reasons["rs15"] = "缺少 SPY benchmark 的同截止连续 15 分钟窗口。"
    reasons["rs30"] = "缺少 SPY benchmark 的同截止连续 30 分钟窗口。"
    reasons["rvol"] = "History unavailable：缺少过去 20 个交易日相同时段的历史量能基线。"
    if as_of is not None:
        features["as_of"] = as_of
    return features, reasons


def _wilder_atr_series(
    bars: Sequence[Mapping[str, Any]],
    *,
    period: int,
) -> list[float | None]:
    ranges: list[float] = []
    for index, bar in enumerate(bars):
        high = float(bar["high"])
        low = float(bar["low"])
        if index == 0:
            ranges.append(high - low)
            continue
        previous_close = float(bars[index - 1]["close"])
        ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    result: list[float | None] = [None] * len(ranges)
    if len(ranges) < period:
        return result
    current = fmean(ranges[:period])
    result[period - 1] = current
    for index in range(period, len(ranges)):
        current = (current * (period - 1) + ranges[index]) / period
        result[index] = current
    return result


def _macd_summary(bars: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    from app.analytics.technical import calculate_macd_projection

    return calculate_macd_projection(
        [float(bar["close"]) for bar in bars],
        dates=[str(bar["bar_end_at"]) for bar in bars],
        source="urus_intraday_equities",
    )


def _session_vwap(
    rows: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    session_open: datetime,
    session_close: datetime,
    cutoff: datetime,
    expected_minute_count: int,
) -> tuple[float | None, str | None]:
    limit = min(session_close, cutoff)
    expected_starts = [session_open + index * _MINUTE for index in range(expected_minute_count)]
    by_start: dict[datetime, dict[str, float] | None] = {}
    duplicate_conflicts: set[datetime] = set()
    for row in rows:
        if str(row.get("symbol") or "").strip().upper() != symbol:
            continue
        try:
            start = _aware_utc(row.get("bar_start_at"), "bar_start_at")
            end = _aware_utc(row.get("bar_end_at"), "bar_end_at")
        except (TypeError, ValueError):
            continue
        if start < session_open or end > limit or end - start != _MINUTE:
            continue
        values, valid = _validated_minute_values(row)
        candidate = values if valid and row.get("quality_state", "ready") == "ready" else None
        previous = by_start.get(start)
        if start in by_start:
            if previous != candidate:
                duplicate_conflicts.add(start)
        else:
            by_start[start] = candidate

    missing = [
        start
        for start in expected_starts
        if start not in by_start or by_start[start] is None or start in duplicate_conflicts
    ]
    if missing:
        return None, f"session VWAP missing minutes / 缺少或包含非法分钟（{len(missing)} 分钟）。"
    values = [by_start[start] for start in expected_starts]
    total_volume = sum(float(item["volume"]) for item in values)
    total_turnover = sum(float(item["turnover"]) for item in values)
    if total_volume <= 0:
        return None, "session VWAP 的累计成交量为零。"
    return total_turnover / total_volume, None


def _relative_strength(
    bars: Sequence[Mapping[str, Any]],
    benchmark: Sequence[Mapping[str, Any]],
    *,
    as_of: str | None,
) -> tuple[float | None, float | None, dict[str, str]]:
    reasons: dict[str, str] = {}
    results: dict[int, float | None] = {}
    for minutes, count in ((15, 4), (30, 7)):
        symbol_window = list(bars[-count:])
        benchmark_window = list(benchmark[-count:])
        if len(symbol_window) < count or len(benchmark_window) < count:
            results[minutes] = None
            reasons[f"rs{minutes}"] = f"缺少连续的 5 分钟 bar 或 SPY benchmark，无法计算 RS{minutes}。"
            continue
        if _window_end(symbol_window) != _window_end(benchmark_window):
            results[minutes] = None
            reasons[f"rs{minutes}"] = f"RS{minutes} 缺少与 SPY 相同的截止点。"
            continue
        if not _is_contiguous(symbol_window) or not _is_contiguous(benchmark_window):
            results[minutes] = None
            reasons[f"rs{minutes}"] = f"RS{minutes} 需要连续的标的与 SPY 时间窗口。"
            continue
        if _aware_utc(symbol_window[0]["bar_start_at"], "bar_start_at") != _aware_utc(
            benchmark_window[0]["bar_start_at"], "bar_start_at"
        ):
            results[minutes] = None
            reasons[f"rs{minutes}"] = f"RS{minutes} 缺少与 SPY 相同的起始点。"
            continue
        symbol_start = float(symbol_window[0]["close"])
        benchmark_start = float(benchmark_window[0]["close"])
        symbol_end = float(symbol_window[-1]["close"])
        benchmark_end = float(benchmark_window[-1]["close"])
        if symbol_start <= 0 or benchmark_start <= 0:
            results[minutes] = None
            reasons[f"rs{minutes}"] = f"RS{minutes} 起始价格无效。"
            continue
        symbol_return = (symbol_end / symbol_start - 1.0) * 100.0
        benchmark_return = (benchmark_end / benchmark_start - 1.0) * 100.0
        results[minutes] = symbol_return - benchmark_return
    if as_of is not None and any(results.values()):
        # The shared endpoint is part of the calculated metric identity.
        _ = as_of
    return results[15], results[30], reasons


def _window_end(window: Sequence[Mapping[str, Any]]) -> datetime | None:
    if not window:
        return None
    return _aware_utc(window[-1].get("bar_end_at"), "bar_end_at")


def _is_contiguous(window: Sequence[Mapping[str, Any]]) -> bool:
    for left, right in zip(window, window[1:]):
        if _aware_utc(left.get("bar_end_at"), "bar_end_at") != _aware_utc(
            right.get("bar_start_at"), "bar_start_at"
        ):
            return False
    return True


def _pool_breadth(
    symbols: Mapping[str, Mapping[str, Any]],
    *,
    minute_rows_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    quote_symbols: Sequence[str],
    minute_symbols: Sequence[str],
    session_open: datetime,
    session_close: datetime,
    cutoff: datetime,
    expected_latest_end: datetime,
    expected_window_count: int,
) -> dict[str, Any]:
    requested_set = set(quote_symbols)
    minute_set = set(minute_symbols)
    common_as_of = expected_latest_end if expected_window_count else None
    eligible_symbols: list[str] = []
    up = down = flat = 0
    up_30m = down_30m = flat_30m = 0
    up_30m_denominator = 0
    above_vwap = 0
    vwap_denominator = 0
    if common_as_of is not None:
        for symbol in quote_symbols:
            if symbol not in requested_set or symbol not in minute_set:
                continue
            item = symbols.get(symbol, {})
            raw_bars = item.get("bars")
            if not isinstance(raw_bars, list):
                continue
            common_bars = [
                dict(bar)
                for bar in raw_bars
                if _aware_utc(bar.get("bar_end_at"), "bar_end_at") <= common_as_of
            ]
            expected_starts = [
                session_open + index * _FIVE_MINUTES
                for index in range(expected_window_count)
            ]
            actual_starts = [
                _aware_utc(bar.get("bar_start_at"), "bar_start_at")
                for bar in common_bars
            ]
            if actual_starts != expected_starts:
                continue
            eligible_symbols.append(symbol)
            first_open = float(common_bars[0]["open"])
            latest_close = float(common_bars[-1]["close"])
            if latest_close > first_open:
                up += 1
            elif latest_close < first_open:
                down += 1
            else:
                flat += 1

            if len(common_bars) >= 7:
                up_30m_denominator += 1
                start_close = float(common_bars[-7]["close"])
                end_close = float(common_bars[-1]["close"])
                if end_close > start_close:
                    up_30m += 1
                elif end_close < start_close:
                    down_30m += 1
                else:
                    flat_30m += 1

            common_minutes = _expected_minute_count(
                session_open,
                session_close,
                common_as_of,
            )
            vwap, _reason = _session_vwap(
                minute_rows_by_symbol.get(symbol, []),
                symbol=symbol,
                session_open=session_open,
                session_close=session_close,
                cutoff=common_as_of,
                expected_minute_count=common_minutes,
            )
            if vwap is not None:
                vwap_denominator += 1
                if latest_close > vwap:
                    above_vwap += 1

    eligible = len(eligible_symbols)
    return {
        "scope": "configured_observation_pool",
        "method": "complete_session_open_to_common_as_of_close",
        "cutoff": cutoff.isoformat(),
        "as_of": common_as_of.isoformat() if common_as_of else None,
        "requested_count": len(quote_symbols),
        "effective_denominator": eligible,
        "effective_symbols": eligible_symbols,
        "up_count": up,
        "down_count": down,
        "flat_count": flat,
        "up_fraction": up / eligible if eligible else None,
        "up_over_30m": {
            "as_of": common_as_of.isoformat() if common_as_of else None,
            "numerator": up_30m,
            "down_count": down_30m,
            "flat_count": flat_30m,
            "effective_denominator": up_30m_denominator,
            "fraction": up_30m / up_30m_denominator if up_30m_denominator else None,
        },
        "above_session_vwap": {
            "as_of": common_as_of.isoformat() if common_as_of else None,
            "numerator": above_vwap,
            "effective_denominator": vwap_denominator,
            "fraction": above_vwap / vwap_denominator if vwap_denominator else None,
        },
        "rs_improving": {
            "as_of": common_as_of.isoformat() if common_as_of else None,
            "numerator": None,
            "effective_denominator": 0,
            "fraction": None,
            "status": "unavailable",
            "reason": "缺少上一调度槽，无法判断 RS 是否改善。",
        },
    }


def _aware_utc(value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO datetime") from exc
    else:
        raise TypeError(f"{field} must be a timezone-aware datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC)


def _validated_minute_values(row: Mapping[str, Any]) -> tuple[dict[str, float], bool]:
    values: dict[str, float] = {}
    for field in _NUMERIC_FIELDS:
        raw_value = row.get(field)
        if raw_value is None or raw_value == "":
            return {}, False
        try:
            value = float(raw_value)
        except (TypeError, ValueError, OverflowError):
            return {}, False
        if not math.isfinite(value):
            return {}, False
        if field in {"open", "high", "low", "close"} and value <= 0:
            return {}, False
        if field in {"volume", "turnover"} and value < 0:
            return {}, False
        values[field] = value

    if not (
        values["low"]
        <= min(values["open"], values["close"])
        <= max(values["open"], values["close"])
        <= values["high"]
    ):
        return {}, False
    return values, True
