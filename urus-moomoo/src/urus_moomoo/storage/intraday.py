from __future__ import annotations

from datetime import UTC, date, datetime
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo


CAPTURE_SCHEMA_VERSION = "intraday-option-capture-v2"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda item: item.isoformat() if isinstance(item, (date, datetime)) else str(item),
    ).encode("utf-8")


def _utc(value: object, timezone: ZoneInfo) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(UTC)


def _db_time(value: datetime) -> str:
    return value.astimezone(UTC).replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class CanonicalIntradayStore:
    """Write standalone captures into Urus's established consumer-facing schema."""

    def __init__(
        self,
        path: str | Path,
        *,
        raw_payload_dir: str | Path | None,
        market_timezone: str,
    ) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.raw_payload_dir = (
            Path(raw_payload_dir).expanduser() if raw_payload_dir is not None else None
        )
        self.market_timezone = ZoneInfo(market_timezone)
        self.connection = sqlite3.connect(self.path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.execute("PRAGMA foreign_keys=ON")
        required = {
            "intraday_option_capture_runs",
            "intraday_option_capture_attempts",
            "intraday_underlying_snapshots",
            "intraday_option_contracts",
            "intraday_option_contract_snapshots",
        }
        present = {
            str(row[0])
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        missing = sorted(required - present)
        if missing:
            raise RuntimeError("canonical intraday schema is missing: " + ", ".join(missing))

    def close(self) -> None:
        self.connection.close()

    def persist(
        self,
        *,
        scheduled_at: datetime,
        capture_tier: str,
        symbols: list[str],
        band_percent: float,
        payload: dict[str, Any],
    ) -> dict[str, str]:
        scheduled_utc = scheduled_at.astimezone(UTC)
        trading_date = scheduled_at.astimezone(self.market_timezone).date().isoformat()
        requested = list(dict.fromkeys(str(item).strip().upper() for item in symbols))
        spec = {
            "schema_version": CAPTURE_SCHEMA_VERSION,
            "symbols": requested,
            "capture_tier": capture_tier,
            "band_percent": band_percent,
        }
        spec_hash = hashlib.sha256(_canonical(spec)).hexdigest()
        existing = self.connection.execute(
            """
            SELECT id, status, selected_attempt_id, content_sha256
            FROM intraday_option_capture_runs
            WHERE trading_date = ? AND scheduled_at = ? AND capture_tier = ?
              AND schema_version = ? AND capture_spec_hash = ?
            """,
            (
                trading_date,
                _db_time(scheduled_utc),
                capture_tier,
                CAPTURE_SCHEMA_VERSION,
                spec_hash,
            ),
        ).fetchone()
        if existing is not None and existing["status"] == "succeeded":
            return {
                "run_id": str(existing["id"]),
                "attempt_id": str(existing["selected_attempt_id"]),
                "content_sha256": str(existing["content_sha256"]),
            }

        encoded = _canonical(payload)
        digest = hashlib.sha256(encoded).hexdigest()
        captured_at = _utc(payload.get("captured_at"), self.market_timezone) or datetime.now(UTC)
        run_id = str(existing["id"]) if existing is not None else str(uuid4())
        attempt_id = str(uuid4())
        attempt_number = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM intraday_option_capture_attempts WHERE capture_run_id = ?",
                (run_id,),
            ).fetchone()[0]
        ) + 1
        raw_path = self._write_raw(run_id, attempt_id, captured_at, encoded)
        completed_symbols: list[str] = []
        expirations: dict[str, list[str]] = {}
        option_times: list[datetime] = []
        quality = dict(payload.get("quality_summary") or {})
        warnings = [str(item) for item in payload.get("warnings", [])]
        status = "succeeded" if bool(quality.get("complete")) else "partial"

        with self.connection:
            if existing is None:
                self.connection.execute(
                    """
                    INSERT INTO intraday_option_capture_runs
                    (id, schema_version, capture_spec_hash, trading_date, scheduled_at,
                     market_session, symbols_requested, symbols_completed,
                     expirations_requested, request_band_percent, capture_tier, status,
                     is_late_capture, warnings, quality_summary, created_at)
                    VALUES (?, ?, ?, ?, ?, 'regular', ?, '[]', '{}', ?, ?, 'collecting', ?, '[]', '{}', ?)
                    """,
                    (
                        run_id,
                        CAPTURE_SCHEMA_VERSION,
                        spec_hash,
                        trading_date,
                        _db_time(scheduled_utc),
                        _json(requested),
                        band_percent,
                        capture_tier,
                        int((captured_at - scheduled_utc).total_seconds() > 45),
                        _db_time(datetime.now(UTC)),
                    ),
                )
            self.connection.execute(
                """
                INSERT INTO intraday_option_capture_attempts
                (id, capture_run_id, attempt_number, started_at, status)
                VALUES (?, ?, ?, ?, 'collecting')
                """,
                (attempt_id, run_id, attempt_number, _db_time(datetime.now(UTC))),
            )

            for bar in payload.get("underlying_bars", []):
                self._persist_bar(run_id, attempt_id, bar, captured_at)

            for item in payload.get("symbols", []):
                symbol = str(item["symbol"]).upper()
                spot = float(item["spot"])
                completed_symbols.append(symbol)
                expirations[symbol] = []
                quote_time = _utc(item.get("spot_time"), self.market_timezone)
                received_at = _utc(item.get("spot_received_at"), self.market_timezone) or captured_at
                requested_at = (
                    _utc(item.get("spot_request_started_at"), self.market_timezone) or received_at
                )
                age = int((received_at - quote_time).total_seconds() * 1000) if quote_time else None
                state = (
                    "clock_skew" if age is not None and age < 0
                    else "ready" if age is not None and age <= 30_000
                    else "stale" if age is not None
                    else "unknown_quote_time"
                )
                self.connection.execute(
                    """
                    INSERT INTO intraday_underlying_snapshots
                    (capture_run_id, capture_attempt_id, symbol, spot, quote_time,
                     request_started_at, received_at, quote_age_ms, data_state)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id, attempt_id, symbol, spot,
                        _db_time(quote_time) if quote_time else None,
                        _db_time(requested_at), _db_time(received_at), age, state,
                    ),
                )
                for expiry_payload in item.get("expirations", []):
                    expiration = date.fromisoformat(str(expiry_payload["expiration"]))
                    expirations[symbol].append(expiration.isoformat())
                    for row in expiry_payload.get("contracts", []):
                        option_time = _utc(row.get("quote_time"), self.market_timezone)
                        if option_time is not None:
                            option_times.append(option_time)
                        self._persist_contract(
                            run_id, attempt_id, symbol, spot, expiration, row,
                            option_time=option_time, captured_at=captured_at,
                        )

            self.connection.execute(
                """
                UPDATE intraday_option_capture_runs
                SET captured_at = ?, provider_time_min = ?, provider_time_max = ?,
                    symbols_completed = ?, expirations_requested = ?, status = ?,
                    warnings = ?, content_sha256 = ?, selected_attempt_id = ?,
                    quality_summary = ?
                WHERE id = ?
                """,
                (
                    _db_time(captured_at),
                    _db_time(min(option_times)) if option_times else None,
                    _db_time(max(option_times)) if option_times else None,
                    _json(sorted(completed_symbols)), _json(expirations), status,
                    _json(warnings), digest, attempt_id, _json(quality), run_id,
                ),
            )
            self.connection.execute(
                """
                UPDATE intraday_option_capture_attempts
                SET completed_at = ?, status = ?, raw_payload_path = ?, content_sha256 = ?
                WHERE id = ?
                """,
                (_db_time(datetime.now(UTC)), status, str(raw_path) if raw_path else None, digest, attempt_id),
            )
        return {"run_id": run_id, "attempt_id": attempt_id, "content_sha256": digest}

    def _write_raw(
        self, run_id: str, attempt_id: str, captured_at: datetime, encoded: bytes
    ) -> Path | None:
        if self.raw_payload_dir is None:
            return None
        directory = self.raw_payload_dir / captured_at.date().isoformat()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{run_id}.{attempt_id}.json.gz"
        temporary = path.with_suffix(path.suffix + ".tmp")
        with gzip.open(temporary, "wb", compresslevel=6) as handle:
            handle.write(encoded)
        temporary.replace(path)
        return path

    def _persist_bar(
        self,
        run_id: str,
        attempt_id: str,
        row: dict[str, Any],
        captured_at: datetime,
    ) -> None:
        bar_time = _utc(row.get("bar_time"), self.market_timezone)
        if bar_time is None:
            return
        symbol = str(row["symbol"]).upper()
        provider = str(row["provider"])
        values = {key: row.get(key) for key in ("open", "high", "low", "close", "volume", "turnover", "vwap")}
        digest = hashlib.sha256(_canonical(values)).hexdigest()
        self.connection.execute(
            """
            INSERT OR IGNORE INTO intraday_underlying_minute_bar_revisions
            (capture_run_id, capture_attempt_id, symbol, bar_time, provider,
             content_sha256, payload, received_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, attempt_id, symbol, _db_time(bar_time), provider, digest, _json(values), _db_time(captured_at)),
        )
        self.connection.execute(
            """
            INSERT INTO intraday_underlying_minute_bars
            (symbol, bar_time, open, high, low, close, volume, turnover, vwap,
             session, provider, received_at, quality_state)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'regular', ?, ?, ?)
            ON CONFLICT(symbol, bar_time, provider) DO UPDATE SET
              open=excluded.open, high=excluded.high, low=excluded.low,
              close=excluded.close, volume=excluded.volume, turnover=excluded.turnover,
              vwap=excluded.vwap, received_at=excluded.received_at,
              quality_state=excluded.quality_state
            """,
            (
                symbol, _db_time(bar_time), float(row["open"]), float(row["high"]),
                float(row["low"]), float(row["close"]), row.get("volume"),
                row.get("turnover"), row.get("vwap"), provider, _db_time(captured_at),
                str(row.get("quality_state") or "ready"),
            ),
        )

    def _persist_contract(
        self,
        run_id: str,
        attempt_id: str,
        symbol: str,
        spot: float,
        expiration: date,
        row: dict[str, Any],
        *,
        option_time: datetime | None,
        captured_at: datetime,
    ) -> None:
        code = str(row["contract_code"])
        received_at = _utc(row.get("received_at"), self.market_timezone) or captured_at
        request_started_at = _utc(row.get("request_started_at"), self.market_timezone) or received_at
        self.connection.execute(
            """
            INSERT INTO intraday_option_contracts
            (contract_code, underlying_symbol, option_type, expiration, strike,
             contract_multiplier, exercise_type, first_observed_at, last_observed_at,
             provider_metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(contract_code) DO UPDATE SET last_observed_at=excluded.last_observed_at
            """,
            (
                code, symbol, str(row["option_type"]).upper(), expiration.isoformat(),
                float(row["strike"]), float(row.get("contract_multiplier") or 100),
                row.get("exercise_type"), _db_time(captured_at), _db_time(captured_at),
                _json(dict(row.get("provider_metadata") or {})),
            ),
        )
        bid, ask = row.get("bid"), row.get("ask")
        warnings = [str(item) for item in row.get("warnings", [])]
        mid = None
        if bid is not None and ask is not None and 0 <= float(bid) <= float(ask):
            mid = (float(bid) + float(ask)) / 2
        elif bid is not None or ask is not None:
            warnings.append("invalid_or_crossed_market")
        age = int((received_at - option_time).total_seconds() * 1000) if option_time else None
        state = "ready"
        if mid is None:
            state = "invalid_quote"
        elif float(bid) == 0 or float(ask) == 0:
            state = "zero_quote"
            warnings.append("zero_bid_or_ask")
        elif age is not None and age < 0:
            state = "clock_skew"
        elif age is None:
            state = "unknown_quote_time"
        elif age > 30_000:
            state = "stale"
        self.connection.execute(
            """
            INSERT INTO intraday_option_contract_snapshots
            (capture_run_id, capture_attempt_id, underlying_symbol, underlying_spot,
             contract_code, days_to_expiry, bid, ask, bid_size, ask_size, last,
             mark_or_mid, volume, open_interest, implied_volatility, delta, gamma,
             vega, theta, rho, quote_time, request_started_at, received_at,
             quote_age_ms, data_state, warnings)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, attempt_id, symbol, spot, code,
                int(row.get("days_to_expiry", (expiration - received_at.date()).days)),
                bid, ask, row.get("bid_size"), row.get("ask_size"), row.get("last"),
                mid, row.get("volume"), row.get("open_interest"),
                row.get("implied_volatility"), row.get("delta"), row.get("gamma"),
                row.get("vega"), row.get("theta"), row.get("rho"),
                _db_time(option_time) if option_time else None,
                _db_time(request_started_at), _db_time(received_at), age, state, _json(warnings),
            ),
        )
