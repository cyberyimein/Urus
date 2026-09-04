from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.time import as_utc, utc_now
from app.intraday_options.models import (
    IntradayOptionBase,
    IntradayOptionCaptureAttemptModel,
    IntradayOptionCaptureRunModel,
    IntradayOptionContractModel,
    IntradayOptionContractSnapshotModel,
    IntradayOptionOutcomeModel,
    IntradayUnderlyingMinuteBarModel,
    IntradayUnderlyingMinuteBarRevisionModel,
    IntradayUnderlyingSnapshotModel,
)


CAPTURE_SCHEMA_VERSION = "intraday-option-capture-v2"


def _json_default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"cannot encode {type(value).__name__}")


def _canonical_payload(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")


def _parse_provider_time(value: object, market_timezone: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=market_timezone)
    return parsed.astimezone(UTC)


class IntradayOptionStore:
    """Dedicated SQLite/SQLAlchemy store; never registers tables in Urus's main DB."""

    def __init__(self, database_url: str, raw_payload_dir: str | Path) -> None:
        url = make_url(database_url)
        if url.drivername.startswith("sqlite") and url.database not in {None, ":memory:"}:
            Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self.raw_payload_dir = Path(raw_payload_dir).expanduser().resolve()
        self.raw_payload_dir.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False} if url.drivername.startswith("sqlite") else {},
            pool_pre_ping=True,
        )
        if url.drivername.startswith("sqlite"):
            event.listen(self.engine, "connect", self._configure_sqlite)
        IntradayOptionBase.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False
        )

    @staticmethod
    def _configure_sqlite(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    def close(self) -> None:
        self.engine.dispose()

    def begin_capture(
        self,
        *,
        trading_date: date,
        scheduled_at: datetime,
        capture_tier: str,
        symbols: list[str],
        band_percent: float,
        is_late_capture: bool,
    ) -> tuple[IntradayOptionCaptureRunModel, IntradayOptionCaptureAttemptModel, bool]:
        scheduled_at = as_utc(scheduled_at)
        spec = {
            "schema_version": CAPTURE_SCHEMA_VERSION,
            "symbols": symbols,
            "capture_tier": capture_tier,
            "band_percent": band_percent,
        }
        capture_spec_hash = hashlib.sha256(_canonical_payload(spec)).hexdigest()
        with self.session_factory.begin() as session:
            existing = session.scalar(
                select(IntradayOptionCaptureRunModel).where(
                    IntradayOptionCaptureRunModel.trading_date == trading_date,
                    IntradayOptionCaptureRunModel.scheduled_at == scheduled_at,
                    IntradayOptionCaptureRunModel.capture_tier == capture_tier,
                    IntradayOptionCaptureRunModel.schema_version == CAPTURE_SCHEMA_VERSION,
                    IntradayOptionCaptureRunModel.capture_spec_hash == capture_spec_hash,
                )
            )
            if existing is not None and existing.status == "succeeded":
                return existing, IntradayOptionCaptureAttemptModel(), True
            run = existing or IntradayOptionCaptureRunModel(
                id=str(uuid4()),
                schema_version=CAPTURE_SCHEMA_VERSION,
                capture_spec_hash=capture_spec_hash,
                trading_date=trading_date,
                scheduled_at=scheduled_at,
                market_session="regular",
                symbols_requested=symbols,
                symbols_completed=[],
                expirations_requested={},
                request_band_percent=band_percent,
                capture_tier=capture_tier,
                status="scheduled",
                is_late_capture=is_late_capture,
                warnings=[],
                quality_summary={},
                created_at=utc_now(),
            )
            if existing is None:
                session.add(run)
                session.flush()
            attempt_number = int(
                session.scalar(
                    select(func.count(IntradayOptionCaptureAttemptModel.id)).where(
                        IntradayOptionCaptureAttemptModel.capture_run_id == run.id
                    )
                )
                or 0
            ) + 1
            attempt = IntradayOptionCaptureAttemptModel(
                id=str(uuid4()),
                capture_run_id=run.id,
                attempt_number=attempt_number,
                started_at=utc_now(),
                status="collecting",
            )
            session.add(attempt)
            run.status = "collecting"
            session.flush()
            return run, attempt, False

    def persist_failure(self, run_id: str, attempt_id: str, error: str) -> None:
        with self.session_factory.begin() as session:
            run = session.get(IntradayOptionCaptureRunModel, run_id)
            attempt = session.get(IntradayOptionCaptureAttemptModel, attempt_id)
            if run is not None:
                run.status = "failed"
                run.warnings = list(run.warnings or []) + [error]
            if attempt is not None:
                attempt.status = "failed"
                attempt.error_message = error
                attempt.completed_at = utc_now()

    def persist_success(
        self,
        run_id: str,
        attempt_id: str,
        payload: dict[str, Any],
        *,
        market_timezone: Any,
    ) -> IntradayOptionCaptureRunModel:
        encoded = _canonical_payload(payload)
        digest = hashlib.sha256(encoded).hexdigest()
        captured_at = as_utc(datetime.fromisoformat(str(payload["captured_at"])))
        raw_path = self._write_raw_payload(run_id, attempt_id, captured_at, encoded)
        option_provider_times: list[datetime] = []
        completed_symbols: list[str] = []
        expirations: dict[str, list[str]] = {}

        with self.session_factory.begin() as session:
            run = session.get(IntradayOptionCaptureRunModel, run_id)
            attempt = session.get(IntradayOptionCaptureAttemptModel, attempt_id)
            if run is None or attempt is None:
                raise RuntimeError("capture run or attempt disappeared before persistence")

            for bar in payload.get("underlying_bars", []):
                bar_time = _parse_provider_time(bar.get("bar_time"), market_timezone)
                if bar_time is None:
                    continue
                self._upsert_bar(
                    session, bar, bar_time, captured_at,
                    run_id=run_id, attempt_id=attempt_id,
                )

            for symbol_payload in payload.get("symbols", []):
                symbol = str(symbol_payload["symbol"]).upper()
                completed_symbols.append(symbol)
                expirations[symbol] = []
                spot = float(symbol_payload["spot"])
                spot_quote_time = _parse_provider_time(
                    symbol_payload.get("spot_time"), market_timezone
                )
                spot_received_at = (
                    _parse_provider_time(symbol_payload.get("spot_received_at"), market_timezone)
                    or captured_at
                )
                spot_request_started_at = (
                    _parse_provider_time(
                        symbol_payload.get("spot_request_started_at"), market_timezone
                    )
                    or spot_received_at
                )
                spot_age_ms = (
                    int((spot_received_at - spot_quote_time).total_seconds() * 1000)
                    if spot_quote_time is not None else None
                )
                session.add(IntradayUnderlyingSnapshotModel(
                    capture_run_id=run_id,
                    capture_attempt_id=attempt_id,
                    symbol=symbol,
                    spot=spot,
                    quote_time=spot_quote_time,
                    request_started_at=spot_request_started_at,
                    received_at=spot_received_at,
                    quote_age_ms=spot_age_ms,
                    data_state=("clock_skew" if spot_age_ms is not None and spot_age_ms < 0
                                else "ready" if spot_age_ms is not None and spot_age_ms <= 30_000
                                else "stale" if spot_age_ms is not None else "unknown_quote_time"),
                ))
                for expiry_payload in symbol_payload.get("expirations", []):
                    expiration = date.fromisoformat(str(expiry_payload["expiration"]))
                    expirations[symbol].append(expiration.isoformat())
                    for row in expiry_payload.get("contracts", []):
                        quote_time = _parse_provider_time(row.get("quote_time"), market_timezone)
                        if quote_time is not None:
                            option_provider_times.append(quote_time)
                        self._upsert_contract(
                            session,
                            symbol=symbol,
                            expiration=expiration,
                            row=row,
                            observed_at=captured_at,
                        )
                        received_at = (
                            _parse_provider_time(row.get("received_at"), market_timezone)
                            or captured_at
                        )
                        self._insert_snapshot(
                            session,
                            run_id=run_id,
                            attempt_id=attempt_id,
                            symbol=symbol,
                            spot=spot,
                            expiration=expiration,
                            row=row,
                            quote_time=quote_time,
                            received_at=received_at,
                        )

            warnings = [str(item) for item in payload.get("warnings", [])]
            requested = set(run.symbols_requested)
            completed = set(completed_symbols)
            unavailable_bars = {
                str(item).upper()
                for item in payload.get("minute_bar_unavailable_symbols", [])
            }
            quality = dict(payload.get("quality_summary") or {})
            payload_complete = quality.get("complete")
            if payload_complete is None:
                payload_complete = requested == completed and not unavailable_bars
            run.status = "succeeded" if bool(payload_complete) else "partial"
            run.captured_at = captured_at
            run.provider_time_min = min(option_provider_times) if option_provider_times else None
            run.provider_time_max = max(option_provider_times) if option_provider_times else None
            run.symbols_completed = sorted(completed)
            run.expirations_requested = expirations
            run.warnings = warnings
            run.content_sha256 = digest
            run.selected_attempt_id = attempt_id
            run.quality_summary = quality
            attempt.status = run.status
            attempt.completed_at = utc_now()
            attempt.raw_payload_path = str(raw_path)
            attempt.content_sha256 = digest
            session.flush()
            return run

    def _write_raw_payload(
        self, run_id: str, attempt_id: str, captured_at: datetime, encoded: bytes
    ) -> Path:
        directory = self.raw_payload_dir / captured_at.date().isoformat()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{run_id}.{attempt_id}.json.gz"
        temporary = path.with_suffix(path.suffix + ".tmp")
        with gzip.open(temporary, "wb", compresslevel=6) as handle:
            handle.write(encoded)
        temporary.replace(path)
        return path

    @staticmethod
    def _upsert_bar(
        session: Session,
        row: dict[str, Any],
        bar_time: datetime,
        received_at: datetime,
        *,
        run_id: str,
        attempt_id: str,
    ) -> None:
        normalized = {
            key: row.get(key)
            for key in ("open", "high", "low", "close", "volume", "turnover", "vwap")
        }
        digest = hashlib.sha256(_canonical_payload(normalized)).hexdigest()
        revision = session.scalar(
            select(IntradayUnderlyingMinuteBarRevisionModel.id).where(
                IntradayUnderlyingMinuteBarRevisionModel.symbol == str(row["symbol"]).upper(),
                IntradayUnderlyingMinuteBarRevisionModel.bar_time == bar_time,
                IntradayUnderlyingMinuteBarRevisionModel.provider == str(row["provider"]),
                IntradayUnderlyingMinuteBarRevisionModel.content_sha256 == digest,
            )
        )
        if revision is None:
            session.add(IntradayUnderlyingMinuteBarRevisionModel(
                capture_run_id=run_id,
                capture_attempt_id=attempt_id,
                symbol=str(row["symbol"]).upper(),
                bar_time=bar_time,
                provider=str(row["provider"]),
                content_sha256=digest,
                payload=normalized,
                received_at=received_at,
            ))
        existing = session.scalar(
            select(IntradayUnderlyingMinuteBarModel).where(
                IntradayUnderlyingMinuteBarModel.symbol == str(row["symbol"]).upper(),
                IntradayUnderlyingMinuteBarModel.bar_time == bar_time,
                IntradayUnderlyingMinuteBarModel.provider == str(row["provider"]),
            )
        )
        if existing is not None:
            existing.open = float(row["open"])
            existing.high = float(row["high"])
            existing.low = float(row["low"])
            existing.close = float(row["close"])
            existing.volume = row.get("volume")
            existing.turnover = row.get("turnover")
            existing.vwap = row.get("vwap")
            existing.received_at = received_at
            existing.quality_state = str(row.get("quality_state") or "ready")
            return
        session.add(
            IntradayUnderlyingMinuteBarModel(
                symbol=str(row["symbol"]).upper(),
                bar_time=bar_time,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=row.get("volume"),
                turnover=row.get("turnover"),
                vwap=row.get("vwap"),
                session="regular",
                provider=str(row["provider"]),
                received_at=received_at,
                quality_state=str(row.get("quality_state") or "ready"),
            )
        )

    @staticmethod
    def _upsert_contract(
        session: Session,
        *,
        symbol: str,
        expiration: date,
        row: dict[str, Any],
        observed_at: datetime,
    ) -> None:
        code = str(row["contract_code"])
        existing = session.get(IntradayOptionContractModel, code)
        if existing is not None:
            existing.last_observed_at = max(as_utc(existing.last_observed_at), observed_at)
            return
        session.add(
            IntradayOptionContractModel(
                contract_code=code,
                underlying_symbol=symbol,
                option_type=str(row["option_type"]).upper(),
                expiration=expiration,
                strike=float(row["strike"]),
                contract_multiplier=float(row.get("contract_multiplier") or 100.0),
                exercise_type=row.get("exercise_type"),
                first_observed_at=observed_at,
                last_observed_at=observed_at,
                provider_metadata=dict(row.get("provider_metadata") or {}),
            )
        )

    @staticmethod
    def _insert_snapshot(
        session: Session,
        *,
        run_id: str,
        attempt_id: str,
        symbol: str,
        spot: float,
        expiration: date,
        row: dict[str, Any],
        quote_time: datetime | None,
        received_at: datetime,
    ) -> None:
        code = str(row["contract_code"])
        exists = session.scalar(
            select(IntradayOptionContractSnapshotModel.id).where(
                IntradayOptionContractSnapshotModel.capture_attempt_id == attempt_id,
                IntradayOptionContractSnapshotModel.contract_code == code,
            )
        )
        if exists is not None:
            return
        bid = row.get("bid")
        ask = row.get("ask")
        warnings = [str(item) for item in row.get("warnings", [])]
        mid: float | None = None
        zero_quote = bid is not None and ask is not None and (
            float(bid) == 0 or float(ask) == 0
        )
        if bid is not None and ask is not None and 0 <= float(bid) <= float(ask):
            mid = (float(bid) + float(ask)) / 2
        elif bid is not None or ask is not None:
            warnings.append("invalid_or_crossed_market")
        quote_age_ms = (
            int((received_at - quote_time).total_seconds() * 1000)
            if quote_time is not None else None
        )
        state = "ready"
        if mid is None:
            state = "invalid_quote"
        elif zero_quote:
            state = "zero_quote"
            warnings.append("zero_bid_or_ask")
        elif quote_age_ms is not None and quote_age_ms < 0:
            state = "clock_skew"
            warnings.append("provider_quote_time_is_after_response_time")
        elif quote_age_ms is None:
            state = "unknown_quote_time"
        elif quote_age_ms > 30_000:
            state = "stale"
            warnings.append("quote_older_than_30_seconds")
        session.add(
            IntradayOptionContractSnapshotModel(
                capture_run_id=run_id,
                capture_attempt_id=attempt_id,
                underlying_symbol=symbol,
                underlying_spot=spot,
                contract_code=code,
                days_to_expiry=(expiration - received_at.date()).days,
                bid=bid,
                ask=ask,
                bid_size=row.get("bid_size"),
                ask_size=row.get("ask_size"),
                last=row.get("last"),
                mark_or_mid=mid,
                volume=row.get("volume"),
                open_interest=row.get("open_interest"),
                implied_volatility=row.get("implied_volatility"),
                delta=row.get("delta"),
                gamma=row.get("gamma"),
                vega=row.get("vega"),
                theta=row.get("theta"),
                rho=row.get("rho"),
                quote_time=quote_time,
                request_started_at=(
                    _parse_provider_time(row.get("request_started_at"), received_at.tzinfo)
                    or received_at
                ),
                received_at=received_at,
                quote_age_ms=quote_age_ms,
                data_state=state,
                warnings=warnings,
            )
        )

    def finalize_trading_day(
        self,
        trading_date: date,
        payload: dict[str, Any],
        *,
        market_timezone: Any,
    ) -> int:
        """Persist final complete bars and supervised close outcomes for every accepted run."""
        captured_at = as_utc(datetime.fromisoformat(str(payload["captured_at"])))
        encoded = _canonical_payload(payload)
        self._write_raw_payload(
            f"session-close-{trading_date.isoformat()}",
            str(uuid4()),
            captured_at,
            encoded,
        )
        closes = {str(k).upper(): float(v) for k, v in payload.get("official_closes", {}).items()}
        day_start = datetime.combine(trading_date, time.min, tzinfo=market_timezone).astimezone(UTC)
        day_end = day_start + timedelta(days=1)
        with self.session_factory.begin() as session:
            for bar in payload.get("underlying_bars", []):
                bar_time = _parse_provider_time(bar.get("bar_time"), market_timezone)
                if bar_time is not None:
                    self._upsert_bar(
                        session, bar, bar_time, captured_at,
                        run_id="session-close", attempt_id=trading_date.isoformat(),
                    )
            runs = session.scalars(
                select(IntradayOptionCaptureRunModel).where(
                    IntradayOptionCaptureRunModel.trading_date == trading_date,
                    IntradayOptionCaptureRunModel.status == "succeeded",
                )
            ).all()
            inserted = 0
            for run in runs:
                for symbol in run.symbols_completed:
                    close = closes.get(symbol)
                    if close is None or not run.selected_attempt_id:
                        continue
                    spot_row = session.scalar(select(IntradayUnderlyingSnapshotModel).where(
                        IntradayUnderlyingSnapshotModel.capture_attempt_id == run.selected_attempt_id,
                        IntradayUnderlyingSnapshotModel.symbol == symbol,
                    ))
                    if spot_row is None:
                        continue
                    future_bars = session.scalars(select(IntradayUnderlyingMinuteBarModel).where(
                        IntradayUnderlyingMinuteBarModel.symbol == symbol,
                        IntradayUnderlyingMinuteBarModel.bar_time >= run.scheduled_at,
                        IntradayUnderlyingMinuteBarModel.bar_time >= day_start,
                        IntradayUnderlyingMinuteBarModel.bar_time < day_end,
                    )).all()
                    highs = [item.high for item in future_bars]
                    lows = [item.low for item in future_bars]
                    existing = session.scalar(select(IntradayOptionOutcomeModel).where(
                        IntradayOptionOutcomeModel.capture_run_id == run.id,
                        IntradayOptionOutcomeModel.symbol == symbol,
                    ))
                    values = {
                        "entry_spot": spot_row.spot,
                        "official_close": close,
                        "close_source": "moomoo_unadjusted_last_regular_minute",
                        "close_return": close / spot_row.spot - 1,
                        "absolute_move": abs(close - spot_row.spot),
                        "max_up_move": max(highs) - spot_row.spot if highs else None,
                        "max_down_move": min(lows) - spot_row.spot if lows else None,
                        "finalized_at": captured_at,
                    }
                    if existing is None:
                        session.add(IntradayOptionOutcomeModel(
                            capture_run_id=run.id, trading_date=trading_date,
                            symbol=symbol, **values,
                        ))
                        inserted += 1
                    else:
                        for key, value in values.items():
                            setattr(existing, key, value)
            return inserted
