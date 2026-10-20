"""Read and verify compact equity references without rewriting frozen captures."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import quote

from app.decision_harness.contracts import content_sha256
from app.intraday.equities import _aware_utc, _validated_minute_values


REFERENCE_SCHEMA = "moomoo-intraday-equities-refs-v1"
_MAX_REFERENCE_ROWS = 250_000


def _reference_specs(capture: Mapping[str, Any]) -> list[dict[str, Any]]:
    data = capture.get("data")
    if not isinstance(data, Mapping) or data.get("schema_version") != REFERENCE_SCHEMA:
        raise ValueError("capture is not a compact equity reference contract")
    if content_sha256(data) != capture.get("content_sha256"):
        raise ValueError("compact capture content hash mismatch")
    refs = data.get("equity_data_refs")
    if not isinstance(refs, list) or len(refs) > 16:
        raise ValueError("invalid equity reference list")
    scope = set(capture.get("requested_symbols", []))
    result = []
    total_rows = 0
    for ref in refs:
        if not isinstance(ref, dict) or ref.get("schema_version") != REFERENCE_SCHEMA:
            raise ValueError("unsupported equity reference schema")
        symbols = ref.get("symbols")
        if (
            not isinstance(symbols, list) or not symbols or len(symbols) > 400
            or any(not isinstance(symbol, str) for symbol in symbols)
            or len(set(symbols)) != len(symbols) or not set(symbols).issubset(scope)
        ):
            raise ValueError("equity reference symbol scope exceeds capture scope")
        if ref.get("provider") != "moomoo_openapi":
            raise ValueError("unexpected equity reference provider")
        if (ref.get("kind"), ref.get("timeframe")) not in {
            ("bars", "1m"), ("bars", "5m"), ("quotes", "snapshot"),
        }:
            raise ValueError("unsupported equity reference kind/timeframe")
        count, watermark = ref.get("row_count"), ref.get("asof_revision_watermark")
        if (
            isinstance(count, bool) or not isinstance(count, int) or count < 0
            or isinstance(watermark, bool) or not isinstance(watermark, int) or watermark < 0
        ):
            raise ValueError("invalid equity reference count/watermark")
        total_rows += count
        if total_rows > _MAX_REFERENCE_ROWS:
            raise ValueError("equity reference exceeds bounded row budget")
        start = _aware_utc(ref.get("from"), "reference.from")
        end = _aware_utc(ref.get("through"), "reference.through")
        captured = _aware_utc(capture.get("captured_at"), "captured_at")
        if start > end or end > captured:
            raise ValueError("reference range is invalid or later than capture")
        if ref["kind"] == "bars":
            cutoff = _aware_utc(data.get("evidence_cutoff", capture["captured_at"]), "cutoff")
            if end > cutoff:
                raise ValueError("bar reference range exceeds capture cutoff")
            if str(ref.get("adjustment") or "").lower() != "none":
                raise ValueError("equity references require the raw NONE price basis")
            dates = ref.get("trading_dates")
            if dates is not None and (
                not isinstance(dates, list) or len(dates) > 21
                or any(not isinstance(day, str) for day in dates)
            ):
                raise ValueError("invalid reference trading date window")
        digest = str(ref.get("content_sha256") or "")
        if len(digest) != 64 or any(value not in "0123456789abcdef" for value in digest):
            raise ValueError("invalid equity reference hash")
        result.append(ref)
    return result


def _validate_rows(ref: Mapping[str, Any], rows: object) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("equity reference rows must be objects")
    if len(rows) != ref["row_count"]:
        raise ValueError("equity reference missing or expired: row count mismatch")
    if content_sha256(rows) != ref["content_sha256"]:
        raise ValueError("equity reference content hash mismatch")
    seen = set()
    keys = []
    for row in rows:
        if row.get("symbol") not in ref["symbols"]:
            raise ValueError("resolved equity row exceeds reference scope")
        if ref["kind"] == "bars":
            start = _aware_utc(row.get("bar_start_at"), "bar_start_at")
            end = _aware_utc(row.get("bar_end_at"), "bar_end_at")
            expected = timedelta(minutes=1 if ref["timeframe"] == "1m" else 5)
            _, valid = _validated_minute_values(row)
            if not valid or end - start != expected:
                raise ValueError("invalid resolved equity bar")
            if not (_aware_utc(ref["from"], "from") <= end <= _aware_utc(ref["through"], "through")):
                raise ValueError("resolved equity bar exceeds reference time range")
            key = (row["symbol"], end.isoformat())
        else:
            key = (row["symbol"], "")
        if key in seen:
            raise ValueError("duplicate rows in equity reference")
        keys.append(key)
        seen.add(key)
    if keys != sorted(keys):
        raise ValueError("equity reference rows are not in canonical order")
    return rows


def hydrate_capture(connection: sqlite3.Connection, capture: Mapping[str, Any]) -> dict[str, Any]:
    refs = _reference_specs(capture)
    captured_at = _aware_utc(capture.get("captured_at"), "captured_at")
    bundles = []
    for ref in refs:
        if ref["kind"] == "bars":
            placeholders = ",".join("?" for _ in ref["symbols"])
            date_filter = ""
            dates = ref.get("trading_dates")
            date_parameters: tuple[str, ...] = ()
            if dates is not None:
                if not dates:
                    bundles.append(_validate_rows(ref, []))
                    continue
                date_filter = " AND trading_date IN (" + ",".join("?" for _ in dates) + ")"
                date_parameters = tuple(dates)
            records = connection.execute(
                "WITH ranked AS (SELECT b.*, ROW_NUMBER() OVER ("
                "PARTITION BY provider,symbol,timeframe,adjustment,bar_end_at "
                "ORDER BY watermark DESC) AS revision_rank FROM equity_bar_revisions b "
                "WHERE watermark<=? AND provider=? AND timeframe=? AND adjustment=? "
                f"AND symbol IN ({placeholders}) "
                "AND julianday(bar_end_at)>=julianday(?) AND julianday(bar_end_at)<=julianday(?)"
                + date_filter + ") "
                "SELECT bar_json,content_sha256,observed_at FROM ranked WHERE revision_rank=1 "
                "ORDER BY symbol,bar_end_at LIMIT ?",
                (ref["asof_revision_watermark"], ref["provider"], ref["timeframe"], ref["adjustment"],
                 *ref["symbols"], ref["from"], ref["through"], *date_parameters, ref["row_count"] + 1),
            ).fetchall()
            rows = []
            for record in records:
                row = json.loads(record["bar_json"])
                if content_sha256(row) != record["content_sha256"]:
                    raise ValueError("stored equity bar content hash mismatch")
                if _aware_utc(record["observed_at"], "observed_at") > captured_at:
                    raise ValueError("equity reference includes data observed later than capture")
                rows.append(row)
        else:
            record = connection.execute(
                "SELECT quotes_json,content_sha256,captured_at,watermark FROM equity_quote_batches "
                "WHERE task_name=? AND scheduled_at=?",
                (ref.get("task_name"), ref.get("scheduled_at")),
            ).fetchone()
            if record is None:
                raise ValueError("quote reference is missing or expired")
            if record["watermark"] > ref["asof_revision_watermark"]:
                raise ValueError("quote reference watermark is newer than captured revision")
            if _aware_utc(record["captured_at"], "quote captured_at") > captured_at:
                raise ValueError("quote data was observed later than capture")
            rows = json.loads(record["quotes_json"])
            if content_sha256(rows) != record["content_sha256"]:
                raise ValueError("stored quote batch content hash mismatch")
        bundles.append(_validate_rows(ref, rows))
    result = dict(capture)
    result["_resolved_equity_refs"] = bundles
    return result


def hydrate_capture_from_path(path_value: str | Path, capture: Mapping[str, Any]) -> dict[str, Any]:
    data = capture.get("data")
    if not isinstance(data, Mapping) or data.get("schema_version") != REFERENCE_SCHEMA:
        return dict(capture)
    path = Path(path_value).expanduser().resolve()
    uri = f"file:{quote(str(path), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    try:
        # Each reference pins immutable row versions and verifies its digest.
        # Do not retain a read transaction while decoding/hashing all refs:
        # the existing collector must still commit Eof data in DELETE mode.
        # Concurrent expiry produces an explicit unavailable reference.
        return hydrate_capture(connection, capture)
    finally:
        connection.close()


def resolved_capture_data(capture: Mapping[str, Any]) -> dict[str, Any]:
    refs = _reference_specs(capture)
    bundles = capture.get("_resolved_equity_refs")
    if not isinstance(bundles, list) or len(bundles) != len(refs):
        raise ValueError("equity reference data is unavailable; resolve references before projection")
    data = dict(capture["data"])
    outputs: dict[str, dict[tuple[str, str], dict[str, Any]]] = {
        "minute_bars": {}, "historical_5m_bars": {}, "quotes": {},
    }
    for ref, bundle in zip(refs, bundles, strict=True):
        rows = _validate_rows(ref, bundle)
        target = "quotes" if ref["kind"] == "quotes" else (
            "minute_bars" if ref["timeframe"] == "1m" else "historical_5m_bars"
        )
        for row in rows:
            key = (row["symbol"], str(row.get("bar_end_at") or ""))
            previous = outputs[target].get(key)
            if previous is not None and previous != row:
                raise ValueError("conflicting equity references in one capture")
            outputs[target][key] = row
    for target, rows in outputs.items():
        if data.get(target):
            raise ValueError("compact capture must not duplicate inline reference data")
        data[target] = [row for _, row in sorted(rows.items())]
    data["provider_raw"] = {}
    return data
