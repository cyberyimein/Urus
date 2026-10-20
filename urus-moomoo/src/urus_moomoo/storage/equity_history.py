from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
from zoneinfo import ZoneInfo


EQUITY_REFS_SCHEMA = "moomoo-intraday-equities-refs-v1"
EQUITY_PROVIDER = "moomoo_openapi"
DEFAULT_METRICS_BUDGET_BYTES = 150 * 1024 * 1024
DEFAULT_RAW_BUDGET_BYTES = 512 * 1024 * 1024
DEFAULT_TOTAL_BUDGET_BYTES = 1024 * 1024 * 1024


class CleanupDeadlineExceeded(RuntimeError):
    """Raised when the bounded equity cleanup window expires."""

_BAR_VALUE_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _normalize_quote_row(source: Mapping[str, Any]) -> dict[str, Any]:
    """Represent missing provider floats as JSON null without changing the input."""
    nonfinite_fields: list[str] = []

    def clean(value: Any, path: str) -> Any:
        if isinstance(value, float) and not math.isfinite(value):
            nonfinite_fields.append(path)
            return None
        if isinstance(value, Mapping):
            return {
                key: clean(item, f"{path}.{key}" if path else str(key))
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [clean(item, f"{path}[{index}]") for index, item in enumerate(value)]
        return value

    row = clean(source, "")
    if nonfinite_fields:
        existing = row.get("nonfinite_fields")
        previous = (
            [item for item in existing if isinstance(item, str)]
            if isinstance(existing, list)
            else []
        )
        row["nonfinite_fields"] = sorted(set(previous + nonfinite_fields))
    return row


def _utc_datetime(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
        str(value).replace("Z", "+00:00")
    )
    if parsed.tzinfo is None:
        raise ValueError("equity timestamps must include a timezone")
    return parsed.astimezone(UTC)


def _utc_text(value: datetime | str) -> str:
    return _utc_datetime(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _number(value: object, *, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _normalize_bar(
    row: Mapping[str, Any],
    *,
    timeframe: str,
    trading_date: str,
    adjustment: str,
    market_timezone: str,
) -> dict[str, Any]:
    symbol = str(row.get("symbol") or "").strip().upper()
    if not symbol:
        raise ValueError("equity bar symbol must not be empty")
    bar_end = row.get("bar_end_at")
    bar_start = row.get("bar_start_at")
    if not bar_end or not bar_start:
        raise ValueError("equity bar requires bar_start_at and bar_end_at")
    start_text = _utc_text(str(bar_start))
    end_text = _utc_text(str(bar_end))
    if start_text >= end_text:
        raise ValueError("equity bar start must precede its end")
    start_utc = _utc_datetime(start_text)
    end_utc = _utc_datetime(end_text)
    expected_duration = timedelta(minutes=1 if timeframe == "1m" else 5)
    if end_utc - start_utc != expected_duration:
        raise ValueError(f"{timeframe} bar duration is invalid")
    if any((start_utc.second, start_utc.microsecond, end_utc.second, end_utc.microsecond)):
        raise ValueError("equity bar timestamps must align to whole minutes")
    if start_utc.astimezone(ZoneInfo(market_timezone)).date().isoformat() != trading_date:
        raise ValueError("equity bar start does not match its trading date")

    values = {field: _number(row.get(field), field=field) for field in _BAR_VALUE_FIELDS}
    if any(values[field] <= 0 for field in ("open", "high", "low", "close")):
        raise ValueError("equity OHLC values must be positive")
    if values["volume"] < 0 or values["turnover"] < 0:
        raise ValueError("equity volume and turnover must not be negative")
    if not (
        values["low"]
        <= min(values["open"], values["close"])
        <= max(values["open"], values["close"])
        <= values["high"]
    ):
        raise ValueError("equity bar OHLC values are inconsistent")
    raw_vwap = row.get("vwap")
    vwap = (
        _number(raw_vwap, field="vwap")
        if raw_vwap is not None
        else values["turnover"] / values["volume"]
        if values["volume"]
        else None
    )
    return {
        "provider": EQUITY_PROVIDER,
        "symbol": symbol,
        "timeframe": timeframe,
        "adjustment": adjustment,
        "trading_date": date.fromisoformat(trading_date).isoformat(),
        "bar_start_at": start_text,
        "bar_end_at": end_text,
        **values,
        "vwap": vwap,
        "quality_state": str(row.get("quality_state") or "ready"),
    }


class EquityHistoryStore:
    """Stock-only normalized history, immutable revisions and reference resolution."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        database_path: str | Path,
        *,
        market_calendar: str = "XNYS",
        market_timezone: str = "America/New_York",
        session_dates_provider: Callable[[str, int], Sequence[str]] | None = None,
    ) -> None:
        self.connection = connection
        self.database_path = Path(database_path).expanduser()
        self.market_calendar = str(market_calendar or "XNYS")
        self.market_timezone = str(market_timezone or "America/New_York")
        self.session_dates_provider = session_dates_provider
        self._migrate()

    def _migrate(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS equity_data_watermarks (
                watermark INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT NOT NULL,
                change_kind TEXT NOT NULL,
                trading_date TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS ux_equity_session_watermark_date
              ON equity_data_watermarks(trading_date)
              WHERE change_kind = 'trading_session';

            CREATE TABLE IF NOT EXISTS equity_bar_revisions (
                watermark INTEGER PRIMARY KEY
                  REFERENCES equity_data_watermarks(watermark),
                provider TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL CHECK (timeframe IN ('1m', '5m')),
                adjustment TEXT NOT NULL,
                bar_end_at TEXT NOT NULL,
                trading_date TEXT NOT NULL,
                bar_json TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                source_capture_id TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_equity_bar_asof
              ON equity_bar_revisions
                 (provider, symbol, timeframe, adjustment, bar_end_at, watermark);
            CREATE INDEX IF NOT EXISTS ix_equity_bar_retention
              ON equity_bar_revisions(timeframe, trading_date, watermark);

            CREATE TABLE IF NOT EXISTS equity_quote_batches (
                task_name TEXT NOT NULL,
                scheduled_at TEXT NOT NULL,
                trading_date TEXT NOT NULL,
                watermark INTEGER NOT NULL
                  REFERENCES equity_data_watermarks(watermark),
                captured_at TEXT NOT NULL,
                quotes_json TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                source_capture_id TEXT NOT NULL,
                PRIMARY KEY (task_name, scheduled_at)
            );
            CREATE INDEX IF NOT EXISTS ix_equity_quote_retention
              ON equity_quote_batches(trading_date, watermark);

            CREATE TABLE IF NOT EXISTS equity_capture_refs (
                capture_id TEXT NOT NULL,
                capture_trading_date TEXT NOT NULL,
                ref_id TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('bars', 'quotes')),
                timeframe TEXT NOT NULL,
                ref_json TEXT NOT NULL,
                PRIMARY KEY (capture_id, ref_id)
            );
            CREATE INDEX IF NOT EXISTS ix_equity_capture_refs_ttl
              ON equity_capture_refs(capture_trading_date, capture_id);
            """
        )
        self.connection.commit()

    def record_trading_session(self, trading_date: str) -> int:
        normalized_date = date.fromisoformat(trading_date).isoformat()
        existing = self.connection.execute(
            "SELECT watermark FROM equity_data_watermarks "
            "WHERE trading_date = ? AND change_kind = 'trading_session'",
            (normalized_date,),
        ).fetchone()
        if existing is not None:
            return int(existing["watermark"])
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO equity_data_watermarks(recorded_at, change_kind, trading_date) "
                "VALUES (?, 'trading_session', ?)",
                (datetime.now(UTC).isoformat(), normalized_date),
            )
        return int(cursor.lastrowid)

    def latest_watermark(self) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(watermark), 0) AS value FROM equity_data_watermarks"
        ).fetchone()
        return int(row["value"])

    def recent_trading_dates(self, current_date: str, *, count: int) -> list[str]:
        current = date.fromisoformat(current_date).isoformat()
        requested_count = max(1, int(count))
        if self.session_dates_provider is not None:
            source_dates = self.session_dates_provider(current, requested_count)
        else:
            import exchange_calendars as xcals

            calendar = xcals.get_calendar(self.market_calendar)
            start = date.fromisoformat(current) - timedelta(days=max(45, requested_count * 3))
            sessions = calendar.sessions_in_range(start.isoformat(), current)
            source_dates = [session.date().isoformat() for session in sessions]

        valid_dates: set[str] = set()
        for session_date in source_dates:
            try:
                normalized = date.fromisoformat(str(session_date)).isoformat()
            except ValueError:
                continue
            if normalized <= current:
                valid_dates.add(normalized)
        return sorted(valid_dates)[-requested_count:]

    def upsert_bars(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        timeframe: str,
        trading_date: str,
        source_capture_id: str,
        provider: str = EQUITY_PROVIDER,
        adjustment: str = "none",
        observed_at: datetime | str | None = None,
        strict: bool = True,
    ) -> dict[str, int]:
        if timeframe not in {"1m", "5m"}:
            raise ValueError("equity bar timeframe must be '1m' or '5m'")
        if provider != EQUITY_PROVIDER:
            raise ValueError(f"unsupported equity provider: {provider}")
        normalized_date = date.fromisoformat(trading_date).isoformat()
        observed_text = _utc_text(observed_at or datetime.now(UTC))
        counts = {"inserted": 0, "revised": 0, "unchanged": 0}
        highest_watermark = self.record_trading_session(normalized_date)
        with self.connection:
            for raw_row in rows:
                if not isinstance(raw_row, Mapping):
                    if strict:
                        raise TypeError("equity bars must be mappings")
                    counts["invalid"] = counts.get("invalid", 0) + 1
                    continue
                try:
                    normalized = _normalize_bar(
                        raw_row,
                        timeframe=timeframe,
                        trading_date=normalized_date,
                        adjustment=adjustment,
                        market_timezone=self.market_timezone,
                    )
                except (TypeError, ValueError):
                    if strict:
                        raise
                    counts["invalid"] = counts.get("invalid", 0) + 1
                    continue
                digest = _sha256(normalized)
                key = (
                    provider,
                    normalized["symbol"],
                    timeframe,
                    adjustment,
                    normalized["bar_end_at"],
                )
                latest = self.connection.execute(
                    "SELECT watermark, content_sha256 FROM equity_bar_revisions "
                    "WHERE provider = ? AND symbol = ? AND timeframe = ? "
                    "AND adjustment = ? AND bar_end_at = ? "
                    "ORDER BY watermark DESC LIMIT 1",
                    key,
                ).fetchone()
                if latest is not None and str(latest["content_sha256"]) == digest:
                    counts["unchanged"] += 1
                    highest_watermark = max(highest_watermark, int(latest["watermark"]))
                    continue

                change_kind = "bar_revision" if latest is not None else "bar_insert"
                cursor = self.connection.execute(
                    "INSERT INTO equity_data_watermarks(recorded_at, change_kind, trading_date) "
                    "VALUES (?, ?, ?)",
                    (observed_text, change_kind, normalized_date),
                )
                watermark = int(cursor.lastrowid)
                self.connection.execute(
                    """
                    INSERT INTO equity_bar_revisions
                      (watermark, provider, symbol, timeframe, adjustment, bar_end_at,
                       trading_date, bar_json, content_sha256, observed_at, source_capture_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        watermark,
                        provider,
                        normalized["symbol"],
                        timeframe,
                        adjustment,
                        normalized["bar_end_at"],
                        normalized_date,
                        _canonical_json(normalized),
                        digest,
                        observed_text,
                        str(source_capture_id),
                    ),
                )
                counts["inserted" if latest is None else "revised"] += 1
                highest_watermark = max(highest_watermark, watermark)
        counts["watermark"] = highest_watermark
        return counts

    def aggregate_5m_from_1m(
        self,
        *,
        symbols: Sequence[str],
        trading_date: str,
        source_capture_id: str,
        market_timezone: str,
        observed_at: datetime | str | None = None,
    ) -> dict[str, int]:
        normalized_symbols = sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})
        if not normalized_symbols:
            return {"inserted": 0, "revised": 0, "unchanged": 0, "watermark": self.latest_watermark()}
        normalized_date = date.fromisoformat(trading_date).isoformat()
        placeholders = ",".join("?" for _ in normalized_symbols)
        watermark = self.latest_watermark()
        rows = self.connection.execute(
            f"""
            WITH ranked AS (
              SELECT watermark, bar_json,
                     ROW_NUMBER() OVER (
                       PARTITION BY provider, symbol, timeframe, adjustment, bar_end_at
                       ORDER BY watermark DESC
                     ) AS revision_rank
              FROM equity_bar_revisions
              WHERE watermark <= ? AND provider = ? AND timeframe = '1m'
                AND adjustment = 'none' AND trading_date = ?
                AND symbol IN ({placeholders})
            )
            SELECT bar_json FROM ranked WHERE revision_rank = 1
            ORDER BY json_extract(bar_json, '$.symbol'), json_extract(bar_json, '$.bar_end_at')
            """,
            (watermark, EQUITY_PROVIDER, normalized_date, *normalized_symbols),
        ).fetchall()
        timezone = ZoneInfo(market_timezone)
        grouped: dict[tuple[str, datetime], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            bar = json.loads(str(row["bar_json"]))
            end_local = _utc_datetime(str(bar["bar_end_at"])).astimezone(timezone)
            session_open = end_local.replace(hour=9, minute=30, second=0, microsecond=0)
            offset_minutes = int((end_local - session_open).total_seconds() // 60)
            if not 1 <= offset_minutes <= 390:
                continue
            bucket_offset = ((offset_minutes + 4) // 5) * 5
            bucket_end = session_open + timedelta(minutes=bucket_offset)
            grouped[(str(bar["symbol"]), bucket_end)].append(bar)

        five_minute_bars: list[dict[str, Any]] = []
        for (symbol, bucket_end), members in sorted(grouped.items()):
            ordered = sorted(members, key=lambda item: str(item["bar_end_at"]))
            expected_ends = [
                _utc_text(bucket_end - timedelta(minutes=offset))
                for offset in range(4, -1, -1)
            ]
            actual_ends = [str(item["bar_end_at"]) for item in ordered]
            if actual_ends != expected_ends or any(
                item.get("quality_state") != "ready" for item in ordered
            ):
                continue
            volume = sum(float(item["volume"]) for item in ordered)
            turnover = sum(float(item["turnover"]) for item in ordered)
            five_minute_bars.append(
                {
                    "symbol": symbol,
                    "bar_start_at": _utc_text(bucket_end - timedelta(minutes=5)),
                    "bar_end_at": _utc_text(bucket_end),
                    "open": float(ordered[0]["open"]),
                    "high": max(float(item["high"]) for item in ordered),
                    "low": min(float(item["low"]) for item in ordered),
                    "close": float(ordered[-1]["close"]),
                    "volume": volume,
                    "turnover": turnover,
                    "vwap": turnover / volume if volume else None,
                    "quality_state": "ready",
                }
            )
        return self.upsert_bars(
            five_minute_bars,
            timeframe="5m",
            trading_date=normalized_date,
            source_capture_id=source_capture_id,
            observed_at=observed_at,
        )

    def make_bar_ref(
        self,
        *,
        provider: str,
        symbols: Sequence[str],
        timeframe: str,
        adjustment: str,
        from_time: datetime | str,
        through_time: datetime | str,
        asof_revision_watermark: int | None = None,
        capture_trading_date: str,
    ) -> dict[str, Any]:
        if timeframe not in {"1m", "5m"}:
            raise ValueError("bar ref timeframe must be '1m' or '5m'")
        if provider != EQUITY_PROVIDER:
            raise ValueError(f"unsupported equity provider: {provider}")
        normalized_symbols = sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})
        start_text = _utc_text(from_time)
        end_text = _utc_text(through_time)
        if start_text > end_text:
            raise ValueError("bar ref range must be ordered")
        watermark = (
            self.latest_watermark()
            if asof_revision_watermark is None
            else int(asof_revision_watermark)
        )
        expected_dates = (
            self.recent_trading_dates(capture_trading_date, count=21)
            if timeframe == "5m"
            else None
        )
        rows, version_watermarks, trading_dates = self._read_bar_rows(
            provider=provider,
            symbols=normalized_symbols,
            timeframe=timeframe,
            adjustment=adjustment,
            from_time=start_text,
            through_time=end_text,
            watermark=watermark,
            trading_dates=expected_dates,
        )
        descriptor: dict[str, Any] = {
            "schema_version": EQUITY_REFS_SCHEMA,
            "kind": "bars",
            "provider": provider,
            "symbols": normalized_symbols,
            "timeframe": timeframe,
            "adjustment": adjustment,
            "from": start_text,
            "through": end_text,
            "capture_trading_date": date.fromisoformat(capture_trading_date).isoformat(),
            "asof_revision_watermark": watermark,
            "row_count": len(rows),
            "content_sha256": _sha256(rows),
            "status": "available" if rows else "unavailable",
            "trading_dates": trading_dates,
        }
        if timeframe == "5m":
            descriptor["trading_dates"] = expected_dates or []
            descriptor["observed_trading_dates"] = trading_dates
            descriptor["expected_trade_days"] = 21
            descriptor["history_status"] = "ready" if len(trading_dates) >= 21 else "warming"
        elif timeframe == "1m":
            descriptor["expected_trade_days"] = 1
        if not rows:
            descriptor["unavailable_reason"] = "no_rows"
        descriptor["ref_id"] = "sha256:" + _sha256(descriptor)
        return descriptor

    def write_quote_batch(
        self,
        *,
        task_name: str,
        scheduled_at: datetime,
        trading_date: str,
        capture_id: str,
        captured_at: datetime | str,
        quotes: Sequence[Mapping[str, Any]],
        requested_symbols: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        normalized_schedule = _utc_text(scheduled_at)
        normalized_date = date.fromisoformat(trading_date).isoformat()
        captured_text = _utc_text(captured_at)
        self.record_trading_session(normalized_date)
        existing = self.connection.execute(
            "SELECT watermark, trading_date, captured_at, quotes_json, content_sha256 "
            "FROM equity_quote_batches WHERE task_name = ? AND scheduled_at = ?",
            (str(task_name), normalized_schedule),
        ).fetchone()
        existing_received_at: dict[tuple[str, str], str] = {}
        if existing is not None:
            if str(existing["trading_date"]) != normalized_date:
                raise ValueError("quote batch slot already exists for a different trading date")
            try:
                existing_rows = json.loads(str(existing["quotes_json"]))
            except json.JSONDecodeError as exc:
                raise ValueError("stored quote batch is not valid JSON") from exc
            if not isinstance(existing_rows, list):
                raise ValueError("stored quote batch rows are not a list")
            for existing_row in existing_rows:
                if not isinstance(existing_row, dict):
                    raise ValueError("stored quote batch rows must be objects")
                identity = (
                    str(existing_row.get("symbol") or "").strip().upper(),
                    str(existing_row.get("quote_code") or ""),
                )
                received_at = existing_row.get("received_at")
                if identity[0] and received_at:
                    existing_received_at[identity] = _utc_text(received_at)

        quote_rows: list[dict[str, Any]] = []
        for source_row in quotes:
            if not isinstance(source_row, Mapping):
                raise TypeError("equity quote rows must be mappings")
            row = _normalize_quote_row(source_row)
            symbol = str(row.get("symbol") or "").strip().upper()
            if not symbol:
                raise ValueError("equity quote symbol must not be empty")
            row["symbol"] = symbol
            identity = (symbol, str(row.get("quote_code") or ""))
            row["received_at"] = _utc_text(
                row.get("received_at")
                or existing_received_at.get(identity)
                or captured_text
            )
            quote_rows.append(row)
        quote_rows.sort(
            key=lambda row: (
                str(row["symbol"]),
                str(row.get("quote_code") or ""),
                _canonical_json(row),
            )
        )
        quote_symbols = [str(row["symbol"]) for row in quote_rows]
        if len(quote_symbols) != len(set(quote_symbols)):
            raise ValueError("equity quote batch must contain one row per symbol")
        encoded = _canonical_json(quote_rows)
        digest = _sha256(quote_rows)
        if existing is not None:
            if str(existing["content_sha256"]) != digest or str(existing["quotes_json"]) != encoded:
                raise ValueError("quote batch already exists with different content for this slot")
            return self._quote_ref(
                task_name=str(task_name),
                scheduled_at=normalized_schedule,
                trading_date=normalized_date,
                captured_at=str(existing["captured_at"]),
                watermark=int(existing["watermark"]),
                rows=quote_rows,
                requested_symbols=requested_symbols,
            )

        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO equity_data_watermarks(recorded_at, change_kind, trading_date) "
                "VALUES (?, 'quote_batch', ?)",
                (captured_text, normalized_date),
            )
            watermark = int(cursor.lastrowid)
            self.connection.execute(
                """
                INSERT INTO equity_quote_batches
                  (task_name, scheduled_at, trading_date, watermark, captured_at,
                   quotes_json, content_sha256, source_capture_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(task_name),
                    normalized_schedule,
                    normalized_date,
                    watermark,
                    captured_text,
                    encoded,
                    digest,
                    str(capture_id),
                ),
            )
        return self._quote_ref(
            task_name=str(task_name),
            scheduled_at=normalized_schedule,
            trading_date=normalized_date,
            captured_at=captured_text,
            watermark=watermark,
            rows=quote_rows,
            requested_symbols=requested_symbols,
        )

    def _quote_ref(
        self,
        *,
        task_name: str,
        scheduled_at: str,
        trading_date: str,
        captured_at: str,
        watermark: int,
        rows: list[dict[str, Any]],
        requested_symbols: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        symbols = sorted(
            {str(symbol).strip().upper() for symbol in requested_symbols or () if str(symbol).strip()}
            | {str(row["symbol"]) for row in rows}
        )
        descriptor: dict[str, Any] = {
            "schema_version": EQUITY_REFS_SCHEMA,
            "kind": "quotes",
            "provider": EQUITY_PROVIDER,
            "task_name": task_name,
            "scheduled_at": scheduled_at,
            "symbols": symbols,
            "timeframe": "snapshot",
            "from": captured_at,
            "through": captured_at,
            "capture_trading_date": trading_date,
            "asof_revision_watermark": watermark,
            "row_count": len(rows),
            "content_sha256": _sha256(rows),
            "status": "available" if rows else "unavailable",
        }
        if not rows:
            descriptor["unavailable_reason"] = "no_quotes"
        descriptor["ref_id"] = "sha256:" + _sha256(descriptor)
        return descriptor

    def _validate_reference_rows(
        self,
        reference: Mapping[str, Any],
        rows: list[dict[str, Any]],
        *,
        version_watermarks: Sequence[int] = (),
    ) -> dict[str, Any]:
        versions = [int(watermark) for watermark in version_watermarks]

        def result(
            available: bool,
            reason: str | None,
            valid_rows: list[dict[str, Any]],
        ) -> dict[str, Any]:
            return {
                "available": available,
                "reason": reason,
                "rows": valid_rows,
                "version_watermarks": versions,
            }

        digest = _sha256(rows)
        try:
            row_count = int(reference.get("row_count", -1))
        except (TypeError, ValueError, OverflowError):
            return result(False, "invalid_ref_row_count", [])
        if len(rows) != row_count:
            return result(False, "ref_row_count_mismatch", [])
        if digest != str(reference.get("content_sha256") or ""):
            return result(False, "ref_digest_mismatch", [])
        if not rows:
            if not self._reference_id_matches(reference):
                return result(False, "ref_id_mismatch", [])
            return result(
                False,
                str(reference.get("unavailable_reason") or "ref_empty"),
                [],
            )
        if not self._reference_id_matches(reference):
            return result(False, "ref_id_mismatch", [])
        return result(True, None, rows)

    def _resolve_bar_ref_details(self, reference: Mapping[str, Any]) -> dict[str, Any]:
        if reference.get("schema_version") != EQUITY_REFS_SCHEMA:
            return {
                "available": False,
                "reason": "unsupported_ref_schema",
                "rows": [],
                "version_watermarks": [],
            }
        try:
            rows, versions, _ = self._read_bar_rows(
                provider=str(reference["provider"]),
                symbols=[str(item) for item in reference["symbols"]],
                timeframe=str(reference["timeframe"]),
                adjustment=str(reference["adjustment"]),
                from_time=str(reference["from"]),
                through_time=str(reference["through"]),
                watermark=int(reference["asof_revision_watermark"]),
                trading_dates=(
                    [str(item) for item in reference["trading_dates"]]
                    if reference.get("timeframe") == "5m"
                    else None
                ),
            )
        except ValueError as exc:
            reason = (
                "stored_bar_digest_mismatch"
                if str(exc) == "stored_bar_digest_mismatch"
                else "invalid_bar_ref"
            )
            return {
                "available": False,
                "reason": reason,
                "rows": [],
                "version_watermarks": [],
            }
        except (KeyError, TypeError, OverflowError):
            return {
                "available": False,
                "reason": "invalid_bar_ref",
                "rows": [],
                "version_watermarks": [],
            }
        return self._validate_reference_rows(
            reference,
            rows,
            version_watermarks=versions,
        )

    def resolve_ref(self, reference: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(reference, Mapping):
            return {"available": False, "reason": "invalid_ref", "rows": []}
        if reference.get("schema_version") != EQUITY_REFS_SCHEMA:
            return {"available": False, "reason": "unsupported_ref_schema", "rows": []}
        if reference.get("kind") == "bars":
            result = self._resolve_bar_ref_details(reference)
            return {
                "available": bool(result["available"]),
                "reason": result["reason"],
                "rows": result["rows"],
            }
        elif reference.get("kind") == "quotes":
            try:
                row = self.connection.execute(
                    """
                    SELECT watermark, captured_at, quotes_json, content_sha256
                    FROM equity_quote_batches
                    WHERE task_name = ? AND scheduled_at = ? AND watermark <= ?
                    """,
                    (
                        str(reference["task_name"]),
                        _utc_text(str(reference["scheduled_at"])),
                        int(reference["asof_revision_watermark"]),
                    ),
                ).fetchone()
            except (KeyError, TypeError, ValueError, OverflowError):
                return {"available": False, "reason": "invalid_quote_ref", "rows": []}
            if row is None:
                return {"available": False, "reason": "quote_batch_missing", "rows": []}
            try:
                requested_watermark = int(reference["asof_revision_watermark"])
                stored_watermark = int(row["watermark"])
            except (KeyError, TypeError, ValueError, OverflowError):
                return {"available": False, "reason": "invalid_quote_ref", "rows": []}
            if stored_watermark != requested_watermark:
                return {"available": False, "reason": "quote_watermark_mismatch", "rows": []}
            try:
                rows = json.loads(str(row["quotes_json"]))
            except (KeyError, TypeError, json.JSONDecodeError):
                return {"available": False, "reason": "quote_payload_invalid", "rows": []}
            if not isinstance(rows, list):
                return {"available": False, "reason": "quote_payload_invalid", "rows": []}
            if _sha256(rows) != str(row["content_sha256"]):
                return {"available": False, "reason": "stored_quote_digest_mismatch", "rows": []}
        else:
            return {"available": False, "reason": "unsupported_ref_kind", "rows": []}

        result = self._validate_reference_rows(reference, rows)
        return {
            "available": bool(result["available"]),
            "reason": result["reason"],
            "rows": result["rows"],
        }

    @staticmethod
    def _reference_id_matches(reference: Mapping[str, Any]) -> bool:
        try:
            descriptor = dict(reference)
            stored_ref_id = str(descriptor.pop("ref_id"))
        except (KeyError, TypeError):
            return False
        return stored_ref_id == "sha256:" + _sha256(descriptor)

    def register_capture_refs(
        self,
        capture_id: str,
        capture_trading_date: str,
        references: Sequence[Mapping[str, Any]],
        *,
        commit: bool = True,
    ) -> None:
        normalized_date = date.fromisoformat(capture_trading_date).isoformat()
        for reference in references:
            if reference.get("schema_version") != EQUITY_REFS_SCHEMA:
                raise ValueError("equity capture refs must use refs-v1")
            encoded = _canonical_json(dict(reference))
            existing = self.connection.execute(
                "SELECT ref_json FROM equity_capture_refs WHERE capture_id = ? AND ref_id = ?",
                (str(capture_id), str(reference["ref_id"])),
            ).fetchone()
            if existing is not None:
                if str(existing["ref_json"]) != encoded:
                    raise ValueError("capture reference identity conflicts with stored descriptor")
                continue
            self.connection.execute(
                """
                INSERT INTO equity_capture_refs
                  (capture_id, capture_trading_date, ref_id, kind, timeframe, ref_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(capture_id),
                    normalized_date,
                    str(reference["ref_id"]),
                    str(reference["kind"]),
                    str(reference["timeframe"]),
                    encoded,
                ),
            )
        if commit:
            self.connection.commit()

    def measure_capacity(self) -> dict[str, int]:
        def _sum(query: str, params: tuple[Any, ...] = ()) -> int:
            row = self.connection.execute(query, params).fetchone()
            return int(row["value"] or 0)

        one_minute = _sum(
            "SELECT SUM(LENGTH(CAST(bar_json AS BLOB)) + LENGTH(CAST(content_sha256 AS BLOB)) "
            "+ LENGTH(CAST(source_capture_id AS BLOB))) AS value "
            "FROM equity_bar_revisions WHERE timeframe = '1m'"
        )
        five_minute = _sum(
            "SELECT SUM(LENGTH(CAST(bar_json AS BLOB)) + LENGTH(CAST(content_sha256 AS BLOB)) "
            "+ LENGTH(CAST(source_capture_id AS BLOB))) AS value "
            "FROM equity_bar_revisions WHERE timeframe = '5m'"
        )
        quotes = _sum(
            "SELECT SUM(LENGTH(CAST(quotes_json AS BLOB)) + LENGTH(CAST(content_sha256 AS BLOB))) AS value "
            "FROM equity_quote_batches"
        )
        refs = _sum("SELECT SUM(LENGTH(CAST(ref_json AS BLOB))) AS value FROM equity_capture_refs")
        raw_refs = 0
        for row in self.connection.execute(
            "SELECT payload_json FROM raw_captures WHERE kind = 'capture_intraday_equities'"
        ).fetchall():
            payload_json = str(row["payload_json"])
            try:
                payload = json.loads(payload_json)
            except json.JSONDecodeError:
                continue
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, dict) and data.get("schema_version") == EQUITY_REFS_SCHEMA:
                raw_refs += len(payload_json.encode("utf-8"))
        main_size = self.database_path.stat().st_size if self.database_path.exists() else 0
        wal_path = Path(str(self.database_path) + "-wal")
        wal_size = wal_path.stat().st_size if wal_path.exists() else 0
        equity_table_index_bytes: int | None = None
        try:
            object_names = [
                str(row["name"])
                for row in self.connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE name LIKE 'equity_%' OR tbl_name LIKE 'equity_%'"
                ).fetchall()
            ]
            if object_names:
                placeholders = ",".join("?" for _ in object_names)
                size_row = self.connection.execute(
                    "SELECT COALESCE(SUM(pgsize), 0) AS value FROM dbstat "
                    f"WHERE name IN ({placeholders})",
                    tuple(object_names),
                ).fetchone()
                equity_table_index_bytes = int(size_row["value"] or 0)
        except sqlite3.OperationalError:
            # SQLite builds without dbstat still report exact logical bytes and
            # the actual main database/WAL sizes above.
            equity_table_index_bytes = None
        logical_total = one_minute + five_minute + quotes + refs + raw_refs
        return {
            "equity_1m_bytes": one_minute,
            "equity_5m_bytes": five_minute,
            "equity_quote_bytes": quotes,
            "equity_ref_bytes": refs,
            "equity_refs_v1_raw_bytes": raw_refs,
            "equity_logical_bytes": logical_total,
            "equity_table_index_bytes": equity_table_index_bytes,
            "equity_table_index_bytes_exact": equity_table_index_bytes is not None,
            "main_database_bytes": main_size,
            "wal_bytes": wal_size,
        }

    @staticmethod
    def capacity_policy(
        capacity: Mapping[str, int],
        *,
        metrics_budget_bytes: int = DEFAULT_METRICS_BUDGET_BYTES,
        raw_budget_bytes: int = DEFAULT_RAW_BUDGET_BYTES,
        total_budget_bytes: int = DEFAULT_TOTAL_BUDGET_BYTES,
        warning_ratio: float = 0.90,
        projected_metrics_bytes: int = 0,
        projected_raw_bytes: int = 0,
    ) -> dict[str, Any]:
        metrics_current = int(capacity.get("equity_5m_bytes", 0))
        raw_current = (
            int(capacity.get("equity_1m_bytes", 0))
            + int(capacity.get("equity_quote_bytes", 0))
            + int(capacity.get("equity_ref_bytes", 0))
            + int(capacity.get("equity_refs_v1_raw_bytes", 0))
        )
        total_current = int(capacity.get("equity_logical_bytes", metrics_current + raw_current))
        projected_metrics = max(0, int(projected_metrics_bytes))
        projected_raw = max(0, int(projected_raw_bytes))
        metrics_used = metrics_current + projected_metrics
        raw_used = raw_current + projected_raw
        total_used = total_current + projected_metrics + projected_raw
        defer_warmup = (
            metrics_used >= metrics_budget_bytes * warning_ratio
            or raw_used >= raw_budget_bytes * warning_ratio
            or total_used >= total_budget_bytes * warning_ratio
        )
        drop_d_symbols = (
            defer_warmup
            or raw_used >= raw_budget_bytes * warning_ratio
            or total_used >= total_budget_bytes * warning_ratio
        )
        skip_all = (
            metrics_used >= metrics_budget_bytes
            or raw_used >= raw_budget_bytes
            or total_used >= total_budget_bytes
        )
        return {
            "metrics_used_bytes": metrics_used,
            "metrics_current_bytes": metrics_current,
            "projected_metrics_bytes": projected_metrics,
            "metrics_budget_bytes": metrics_budget_bytes,
            "raw_used_bytes": raw_used,
            "raw_current_bytes": raw_current,
            "projected_raw_bytes": projected_raw,
            "raw_budget_bytes": raw_budget_bytes,
            "total_used_bytes": total_used,
            "total_current_bytes": total_current,
            "total_budget_bytes": total_budget_bytes,
            "near_budget": drop_d_symbols,
            "defer_warmup": defer_warmup,
            "drop_d_symbols": drop_d_symbols and not skip_all,
            "skip_all_equities": skip_all,
            "reason_code": "skipped_capacity" if skip_all else "deferred_low_priority" if drop_d_symbols else None,
        }

    def cleanup_expired(
        self,
        current_trading_date: str,
        *,
        dry_run: bool = True,
        deadline_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        if not dry_run:
            raise ValueError("use apply_cleanup with a fresh dry-run plan")
        with self._cleanup_deadline_guard(deadline_check):
            self._check_cleanup_deadline(deadline_check)
            return self._cleanup_plan(
                current_trading_date,
                deadline_check=deadline_check,
            )

    def apply_cleanup(
        self,
        plan: Mapping[str, Any],
        *,
        deadline_check: Callable[[], bool] | None = None,
        before_commit: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if not bool(plan.get("dry_run")):
            raise ValueError("cleanup apply requires a dry-run plan")
        current_date = str(plan.get("current_trading_date") or "")
        with self._cleanup_deadline_guard(deadline_check):
            self._check_cleanup_deadline(deadline_check)
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                fresh = self._cleanup_plan(
                    current_date,
                    deadline_check=deadline_check,
                )
                self._check_cleanup_deadline(deadline_check)
                if fresh["plan_sha256"] != plan.get("plan_sha256"):
                    raise RuntimeError("equity cleanup plan is stale; run a new dry-run")
                if fresh["blocked"]:
                    raise RuntimeError("equity cleanup is blocked by an invalid active reference")
                candidates = fresh["candidates"]
                deleted: dict[str, int] = {}
                for row in candidates["scheduled_captures"]:
                    self._check_cleanup_deadline(deadline_check)
                    self.connection.execute(
                        "DELETE FROM scheduled_captures WHERE task_name = ? AND scheduled_at = ? "
                        "AND capture_id = ? AND kind = 'capture_intraday_equities'",
                        (row["task_name"], row["scheduled_at"], row["capture_id"]),
                    )
                deleted["scheduled_captures"] = len(candidates["scheduled_captures"])
                for row in candidates["scheduler_runs"]:
                    self._check_cleanup_deadline(deadline_check)
                    self.connection.execute(
                        "DELETE FROM scheduler_runs WHERE task_name = ? AND scheduled_at = ? AND capture_id = ?",
                        (row["task_name"], row["scheduled_at"], row["capture_id"]),
                    )
                deleted["scheduler_runs"] = len(candidates["scheduler_runs"])
                for capture_id, ref_id in candidates["equity_capture_refs"]:
                    self._check_cleanup_deadline(deadline_check)
                    self.connection.execute(
                        "DELETE FROM equity_capture_refs WHERE capture_id = ? AND ref_id = ?",
                        (capture_id, ref_id),
                    )
                deleted["equity_capture_refs"] = len(candidates["equity_capture_refs"])
                for capture_id in candidates["raw_captures"]:
                    self._check_cleanup_deadline(deadline_check)
                    self.connection.execute(
                        "DELETE FROM raw_captures WHERE capture_id = ? AND kind = 'capture_intraday_equities' "
                        "AND json_extract(payload_json, '$.data.schema_version') = ?",
                        (capture_id, EQUITY_REFS_SCHEMA),
                    )
                deleted["raw_captures"] = len(candidates["raw_captures"])
                for task_name, scheduled_at in candidates["equity_quote_batches"]:
                    self._check_cleanup_deadline(deadline_check)
                    self.connection.execute(
                        "DELETE FROM equity_quote_batches WHERE task_name = ? AND scheduled_at = ?",
                        (task_name, scheduled_at),
                    )
                deleted["equity_quote_batches"] = len(candidates["equity_quote_batches"])
                for watermark in candidates["equity_bar_revisions"]:
                    self._check_cleanup_deadline(deadline_check)
                    self.connection.execute(
                        "DELETE FROM equity_bar_revisions WHERE watermark = ?",
                        (watermark,),
                    )
                deleted["equity_bar_revisions"] = len(candidates["equity_bar_revisions"])
                for watermark in candidates["equity_data_watermarks"]:
                    self._check_cleanup_deadline(deadline_check)
                    self.connection.execute(
                        "DELETE FROM equity_data_watermarks WHERE watermark = ? "
                        "AND NOT EXISTS (SELECT 1 FROM equity_bar_revisions WHERE watermark = ?) "
                        "AND NOT EXISTS (SELECT 1 FROM equity_quote_batches WHERE watermark = ?)",
                        (watermark, watermark, watermark),
                    )
                deleted["equity_data_watermarks"] = len(candidates["equity_data_watermarks"])
                self._check_cleanup_deadline(deadline_check)
                if before_commit is not None:
                    before_commit()
                self._check_cleanup_deadline(deadline_check)
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise
        return {"dry_run": False, "current_trading_date": current_date, "deleted": deleted}

    @contextmanager
    def _cleanup_deadline_guard(
        self,
        deadline_check: Callable[[], bool] | None,
    ) -> Iterator[None]:
        if deadline_check is None:
            yield
            return

        deadline_reached = [False]

        def progress_handler() -> int:
            try:
                expired = bool(deadline_check())
            except Exception:
                expired = True
            if expired:
                deadline_reached[0] = True
                return 1
            return 0

        self.connection.set_progress_handler(progress_handler, 1000)
        try:
            yield
        except sqlite3.OperationalError as exc:
            if deadline_reached[0]:
                raise CleanupDeadlineExceeded("equity cleanup deadline exceeded") from exc
            raise
        finally:
            self.connection.set_progress_handler(None, 0)

    @staticmethod
    def _check_cleanup_deadline(deadline_check: Callable[[], bool] | None) -> None:
        if deadline_check is not None and deadline_check():
            raise CleanupDeadlineExceeded("equity cleanup deadline exceeded")

    def _checked_cleanup_rows(
        self,
        rows: Iterable[sqlite3.Row],
        deadline_check: Callable[[], bool] | None,
    ) -> Iterable[sqlite3.Row]:
        for row in rows:
            self._check_cleanup_deadline(deadline_check)
            yield row

    def _cleanup_plan(
        self,
        current_trading_date: str,
        *,
        deadline_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        self._check_cleanup_deadline(deadline_check)
        current = date.fromisoformat(current_trading_date).isoformat()
        keep_two = self.recent_trading_dates(current, count=2)
        keep_twenty_one = self.recent_trading_dates(current, count=21)
        self._check_cleanup_deadline(deadline_check)
        active_refs = self.connection.execute(
            "SELECT capture_id, ref_id, ref_json FROM equity_capture_refs "
            "WHERE capture_trading_date IN (" + ",".join("?" for _ in keep_two) + ")",
            tuple(keep_two),
        ).fetchall()
        protected_bars: set[int] = set()
        protected_quotes: set[tuple[str, str]] = set()
        protected_watermarks: set[int] = set()
        invalid_active_refs: list[dict[str, str]] = []
        resolved_references: dict[str, tuple[str, dict[str, Any]]] = {}
        for row in active_refs:
            self._check_cleanup_deadline(deadline_check)
            resolution_details: dict[str, Any] | None = None
            version_watermarks: Sequence[int] = ()
            resolved_now = False
            try:
                reference = json.loads(str(row["ref_json"]))
                if not isinstance(reference, Mapping):
                    raise ValueError("active reference descriptor must be an object")
                ref_id = str(reference.get("ref_id") or "")
                if not ref_id or ref_id != str(row["ref_id"]):
                    invalid_active_refs.append(
                        {
                            "capture_id": str(row["capture_id"]),
                            "ref_id": str(row["ref_id"]),
                            "reason": "ref_id_mismatch",
                        }
                    )
                    continue

                descriptor_identity = _canonical_json(dict(reference))
                cached = resolved_references.get(ref_id)
                if cached is not None and cached[0] != descriptor_identity:
                    invalid_active_refs.append(
                        {
                            "capture_id": str(row["capture_id"]),
                            "ref_id": ref_id,
                            "reason": "descriptor_identity_mismatch",
                        }
                    )
                    continue
                if cached is not None:
                    resolved = cached[1]
                else:
                    if not self._reference_id_matches(reference):
                        invalid_active_refs.append(
                            {
                                "capture_id": str(row["capture_id"]),
                                "ref_id": ref_id,
                                "reason": "ref_id_mismatch",
                            }
                        )
                        continue
                    if reference.get("kind") == "bars":
                        resolution_details = self._resolve_bar_ref_details(reference)
                        version_watermarks = resolution_details.get("version_watermarks", [])
                    else:
                        resolution_details = self.resolve_ref(reference)
                    resolved = {
                        "available": bool(resolution_details.get("available")),
                        "reason": resolution_details.get("reason"),
                    }
                    resolved_references[ref_id] = (descriptor_identity, resolved)
                    resolved_now = True

                if (
                    reference.get("status") == "available"
                    and not bool(resolved.get("available"))
                ):
                    invalid_active_refs.append(
                        {
                            "capture_id": str(row["capture_id"]),
                            "ref_id": str(row["ref_id"]),
                            "reason": str(resolved.get("reason") or "unavailable"),
                        }
                    )
                    resolution_details = None
                    version_watermarks = ()
                    continue
                protected_watermarks.add(int(reference["asof_revision_watermark"]))
                if reference.get("kind") == "bars" and resolved_now:
                    for watermark in version_watermarks:
                        self._check_cleanup_deadline(deadline_check)
                        protected_bars.add(int(watermark))
                elif reference.get("kind") == "quotes":
                    protected_quotes.add(
                        (str(reference["task_name"]), _utc_text(str(reference["scheduled_at"])))
                    )
                resolution_details = None
                version_watermarks = ()
            except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                invalid_active_refs.append(
                    {
                        "capture_id": str(row["capture_id"]),
                        "ref_id": str(row["ref_id"]),
                        "reason": "malformed_reference",
                    }
                )
                continue

        candidates: dict[str, Any] = {
            "equity_bar_revisions": [],
            "equity_quote_batches": [],
            "equity_capture_refs": [],
            "raw_captures": [],
            "scheduled_captures": [],
            "scheduler_runs": [],
            "equity_data_watermarks": [],
        }
        for row in self.connection.execute(
            "SELECT watermark, timeframe, trading_date FROM equity_bar_revisions"
        ).fetchall():
            self._check_cleanup_deadline(deadline_check)
            watermark = int(row["watermark"])
            retained_dates = keep_two if row["timeframe"] == "1m" else keep_twenty_one
            if str(row["trading_date"]) not in retained_dates and watermark not in protected_bars:
                candidates["equity_bar_revisions"].append(watermark)

        for row in self.connection.execute(
            "SELECT task_name, scheduled_at, trading_date FROM equity_quote_batches"
        ).fetchall():
            self._check_cleanup_deadline(deadline_check)
            identity = (str(row["task_name"]), str(row["scheduled_at"]))
            if str(row["trading_date"]) not in keep_two and identity not in protected_quotes:
                candidates["equity_quote_batches"].append(identity)

        for row in self.connection.execute(
            "SELECT capture_id, ref_id, capture_trading_date FROM equity_capture_refs"
        ).fetchall():
            self._check_cleanup_deadline(deadline_check)
            if str(row["capture_trading_date"]) not in keep_two:
                candidates["equity_capture_refs"].append(
                    (str(row["capture_id"]), str(row["ref_id"]))
                )

        expired_capture_ids: set[str] = set()
        for row in self.connection.execute(
            "SELECT capture_id, payload_json FROM raw_captures WHERE kind = 'capture_intraday_equities'"
        ).fetchall():
            self._check_cleanup_deadline(deadline_check)
            capture_id = str(row["capture_id"])
            try:
                contract = json.loads(str(row["payload_json"]))
                data = contract.get("data") if isinstance(contract, dict) else None
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict) or data.get("schema_version") != EQUITY_REFS_SCHEMA:
                continue
            capture_date = str(data.get("trading_date") or "")
            if capture_date and capture_date not in keep_two:
                expired_capture_ids.add(capture_id)
        candidates["raw_captures"] = sorted(expired_capture_ids)

        if expired_capture_ids:
            self._check_cleanup_deadline(deadline_check)
            placeholders = ",".join("?" for _ in expired_capture_ids)
            candidates["scheduled_captures"] = [
                {
                    "task_name": str(row["task_name"]),
                    "scheduled_at": str(row["scheduled_at"]),
                    "capture_id": str(row["capture_id"]),
                }
                for row in self._checked_cleanup_rows(
                    self.connection.execute(
                        "SELECT task_name, scheduled_at, capture_id FROM scheduled_captures "
                        f"WHERE capture_id IN ({placeholders}) AND kind = 'capture_intraday_equities'",
                        tuple(sorted(expired_capture_ids)),
                    ).fetchall(),
                    deadline_check,
                )
            ]
            candidates["scheduler_runs"] = [
                {
                    "task_name": str(row["task_name"]),
                    "scheduled_at": str(row["scheduled_at"]),
                    "capture_id": str(row["capture_id"]),
                }
                for row in self._checked_cleanup_rows(
                    self.connection.execute(
                        "SELECT task_name, scheduled_at, capture_id FROM scheduler_runs "
                        f"WHERE capture_id IN ({placeholders})",
                        tuple(sorted(expired_capture_ids)),
                    ).fetchall(),
                    deadline_check,
                )
            ]

        removable_bars = set(candidates["equity_bar_revisions"])
        removable_quotes = set(candidates["equity_quote_batches"])
        removable_quote_watermarks: set[int] = set()
        if removable_quotes:
            quote_keys = sorted(removable_quotes)
            quote_filter = " OR ".join("(task_name = ? AND scheduled_at = ?)" for _ in quote_keys)
            quote_parameters = tuple(value for key in quote_keys for value in key)
            removable_quote_watermarks = {
                int(row["watermark"])
                for row in self._checked_cleanup_rows(
                    self.connection.execute(
                        "SELECT watermark FROM equity_quote_batches WHERE " + quote_filter,
                        quote_parameters,
                    ).fetchall(),
                    deadline_check,
                )
            }
        self._check_cleanup_deadline(deadline_check)
        all_data_watermarks = {
            int(row["watermark"])
            for row in self._checked_cleanup_rows(
                self.connection.execute(
                    "SELECT watermark FROM equity_bar_revisions UNION "
                    "SELECT watermark FROM equity_quote_batches"
                ).fetchall(),
                deadline_check,
            )
        }
        used_watermarks = {
            int(row["watermark"])
            for row in self._checked_cleanup_rows(
                self.connection.execute(
                    "SELECT watermark FROM equity_bar_revisions"
                ).fetchall(),
                deadline_check,
            )
            if int(row["watermark"]) not in removable_bars
        }
        used_watermarks.update(
            int(row["watermark"])
            for row in self._checked_cleanup_rows(
                self.connection.execute(
                    "SELECT task_name, scheduled_at, watermark FROM equity_quote_batches"
                ).fetchall(),
                deadline_check,
            )
            if (str(row["task_name"]), str(row["scheduled_at"])) not in removable_quotes
        )
        for row in self.connection.execute(
            "SELECT watermark, change_kind, trading_date FROM equity_data_watermarks"
        ).fetchall():
            self._check_cleanup_deadline(deadline_check)
            watermark = int(row["watermark"])
            if watermark in used_watermarks or watermark in protected_watermarks:
                continue
            stale_session = (
                row["change_kind"] == "trading_session"
                and str(row["trading_date"] or "") not in keep_twenty_one
            )
            expired_data_revision = (
                watermark in removable_bars or watermark in removable_quote_watermarks
            )
            orphan_data_watermark = (
                watermark not in all_data_watermarks
                and row["change_kind"] in {"bar_insert", "bar_revision", "quote_batch"}
            )
            if stale_session or expired_data_revision or orphan_data_watermark:
                candidates["equity_data_watermarks"].append(watermark)

        if invalid_active_refs:
            for key in candidates:
                candidates[key] = []

        self._check_cleanup_deadline(deadline_check)
        capacity = self.measure_capacity()
        self._check_cleanup_deadline(deadline_check)
        candidate_payload = {
            "current_trading_date": current,
            "retained_1m_quote_dates": keep_two,
            "retained_5m_dates": keep_twenty_one,
            "protected_bar_watermarks": sorted(protected_bars),
            "protected_quote_batches": sorted(protected_quotes),
            "candidates": candidates,
            "invalid_active_refs": invalid_active_refs,
        }
        return {
            "dry_run": True,
            "blocked": bool(invalid_active_refs),
            **candidate_payload,
            "active_ref_count": len(active_refs),
            "invalid_active_refs": invalid_active_refs,
            "counts": {key: len(value) for key, value in candidates.items()},
            "capacity": capacity,
            "expired_raw_capture_ids": list(candidates["raw_captures"]),
            "plan_sha256": _sha256(candidate_payload),
        }

    def _read_bar_rows(
        self,
        *,
        provider: str,
        symbols: Sequence[str],
        timeframe: str,
        adjustment: str,
        from_time: str,
        through_time: str,
        watermark: int,
        trading_dates: Sequence[str] | None = None,
    ) -> tuple[list[dict[str, Any]], list[int], list[str]]:
        if not symbols:
            return [], [], []
        placeholders = ",".join("?" for _ in symbols)
        date_filter = ""
        date_parameters: tuple[str, ...] = ()
        if trading_dates is not None:
            if not trading_dates:
                return [], [], []
            date_placeholders = ",".join("?" for _ in trading_dates)
            date_filter = f" AND trading_date IN ({date_placeholders})"
            date_parameters = tuple(trading_dates)
        rows = self.connection.execute(
            f"""
            WITH ranked AS (
              SELECT watermark, bar_json, trading_date, content_sha256, symbol, bar_end_at,
                     ROW_NUMBER() OVER (
                       PARTITION BY provider, symbol, timeframe, adjustment, bar_end_at
                       ORDER BY watermark DESC
                     ) AS revision_rank
              FROM equity_bar_revisions
              WHERE watermark <= ? AND provider = ? AND timeframe = ?
                AND adjustment = ? AND symbol IN ({placeholders})
                AND bar_end_at >= ? AND bar_end_at <= ?
                {date_filter}
            )
            SELECT watermark, bar_json, trading_date, content_sha256 FROM ranked
            WHERE revision_rank = 1
            ORDER BY symbol, bar_end_at
            """,
            (
                int(watermark),
                provider,
                timeframe,
                adjustment,
                *sorted(set(symbols)),
                from_time,
                through_time,
                *date_parameters,
            ),
        ).fetchall()
        values: list[dict[str, Any]] = []
        for row in rows:
            value = json.loads(str(row["bar_json"]))
            if _sha256(value) != str(row["content_sha256"]):
                raise ValueError("stored_bar_digest_mismatch")
            values.append(value)
        version_watermarks = [int(row["watermark"]) for row in rows]
        trading_dates = sorted({str(row["trading_date"]) for row in rows})
        return values, version_watermarks, trading_dates
