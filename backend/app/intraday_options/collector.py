from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from app.core.time import as_utc, utc_now
from app.intraday_options.storage import IntradayOptionStore


class IntradayOptionProvider(Protocol):
    def capture_intraday(
        self, *, symbols: list[str], capture_tier: str, band_percent: float
    ) -> dict[str, Any]: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class IntradayCaptureResult:
    run_id: str
    attempt_id: str | None
    status: str
    skipped: bool
    content_sha256: str | None = None
    error_message: str | None = None


class IntradayOptionCaptureService:
    def __init__(self, store: IntradayOptionStore, market_timezone: Any) -> None:
        self.store = store
        self.market_timezone = market_timezone

    def collect(
        self,
        provider: IntradayOptionProvider,
        *,
        scheduled_at: datetime,
        capture_tier: str,
        symbols: list[str],
        band_percent: float,
        late_after_seconds: float = 45.0,
        now: datetime | None = None,
    ) -> IntradayCaptureResult:
        current = as_utc(now or utc_now())
        scheduled_utc = as_utc(scheduled_at)
        normalized_symbols = list(
            dict.fromkeys(str(item).strip().upper() for item in symbols if str(item).strip())
        )
        run, attempt, skipped = self.store.begin_capture(
            trading_date=scheduled_at.astimezone(self.market_timezone).date(),
            scheduled_at=scheduled_utc,
            capture_tier=capture_tier,
            symbols=normalized_symbols,
            band_percent=band_percent,
            is_late_capture=(current - scheduled_utc).total_seconds() > late_after_seconds,
        )
        if skipped:
            return IntradayCaptureResult(
                run_id=run.id,
                attempt_id=None,
                status=run.status,
                skipped=True,
                content_sha256=run.content_sha256,
            )
        try:
            payload = provider.capture_intraday(
                symbols=normalized_symbols,
                capture_tier=capture_tier,
                band_percent=band_percent,
            )
            persisted = self.store.persist_success(
                run.id, attempt.id, payload, market_timezone=self.market_timezone
            )
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            self.store.persist_failure(run.id, attempt.id, message)
            return IntradayCaptureResult(
                run_id=run.id,
                attempt_id=attempt.id,
                status="failed",
                skipped=False,
                error_message=message,
            )
        return IntradayCaptureResult(
            run_id=persisted.id,
            attempt_id=attempt.id,
            status=persisted.status,
            skipped=False,
            content_sha256=persisted.content_sha256,
        )
