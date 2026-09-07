"""Collect index-ETF point-in-time option facts into the dedicated research store."""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, time as wall_time, timedelta
from functools import lru_cache
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import time
from typing import Any
from zoneinfo import ZoneInfo

import exchange_calendars as xcals


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings  # noqa: E402
from app.integrations.moomoo import OpenDMarketAdapter  # noqa: E402
from app.integrations.moomoo_intraday_options import MoomooIntradayOptionsAdapter  # noqa: E402
from app.intraday_options.collector import IntradayOptionCaptureService  # noqa: E402
from app.intraday_options.maintenance import create_daily_backup  # noqa: E402
from app.intraday_options.storage import IntradayOptionStore  # noqa: E402
from app.services.market_data_collection import MoomooCollectionCoordinator  # noqa: E402
from app.services.trading_calendar import build_moomoo_calendar  # noqa: E402


ANCHORS = {
    wall_time(9, 35),
    wall_time(10, 0),
    wall_time(12, 30),
    wall_time(15, 0),
    wall_time(15, 30),
    wall_time(15, 50),
}
logger = logging.getLogger("urus.intraday_options")


@lru_cache(maxsize=4)
def exchange_calendar(name: str) -> Any:
    return xcals.get_calendar(name)


def build_runtime_calendar(
    settings: Settings,
) -> tuple[Any, OpenDMarketAdapter | None]:
    """Use Moomoo dates while retaining local session timestamps."""

    exchange = exchange_calendar(settings.market_calendar)
    if not settings.moomoo_enabled:
        return exchange, None

    adapter: OpenDMarketAdapter | None = None
    try:
        # This context is kept alive only for cached calendar reads. The
        # collection coordinator is intentionally reserved for each capture
        # so the long-running scheduler cannot hold its host-wide lock.
        adapter = OpenDMarketAdapter(
            settings.moomoo_host,
            settings.moomoo_port,
            market_timezone=settings.market_timezone,
            sdk_home=Path(settings.moomoo_sdk_home),
            market_symbols=[
                item.strip()
                for item in settings.moomoo_market_symbols.split(",")
                if item.strip()
            ],
            history_request_interval_seconds=settings.moomoo_history_request_interval_seconds,
            snapshot_request_interval_seconds=settings.moomoo_snapshot_request_interval_seconds,
            trading_calendar_request_interval_seconds=(
                settings.moomoo_trading_calendar_request_interval_seconds
            ),
        )
        calendar = build_moomoo_calendar(adapter, settings.market_calendar)
        market_today = datetime.now(ZoneInfo(settings.market_timezone)).date()
        calendar.prefetch(market_today - timedelta(days=3), market_today + timedelta(days=3))
        return calendar, adapter
    except Exception:
        if adapter is not None:
            adapter.close()
        raise


def session_bounds(calendar: Any, market_date: date) -> tuple[datetime, datetime] | None:
    if not calendar.is_session(market_date):
        return None
    if hasattr(calendar, "session_open"):
        opened = calendar.session_open(market_date)
        closed = calendar.session_close(market_date)
    else:
        row = calendar.schedule.loc[market_date.isoformat()]
        opened = row["open"]
        closed = row["close"]
    if opened is None or closed is None:
        return None
    if hasattr(opened, "to_pydatetime"):
        opened = opened.to_pydatetime()
    if hasattr(closed, "to_pydatetime"):
        closed = closed.to_pydatetime()
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=UTC)
    if closed.tzinfo is None:
        closed = closed.replace(tzinfo=UTC)
    return opened.astimezone(UTC), closed.astimezone(UTC)


def capture_slots(
    market_date: date, calendar: Any, market_timezone: ZoneInfo
) -> list[tuple[datetime, str]]:
    bounds = session_bounds(calendar, market_date)
    if bounds is None:
        return []
    opened, closed = bounds
    cursor = opened.astimezone(market_timezone).replace(second=0, microsecond=0) + timedelta(
        minutes=5
    )
    final = closed.astimezone(market_timezone) - timedelta(minutes=5)
    slots: list[tuple[datetime, str]] = []
    while cursor <= final:
        tier = "structure" if cursor.timetz().replace(tzinfo=None) in ANCHORS else "fast"
        slots.append((cursor, tier))
        cursor += timedelta(minutes=5)
    return slots


def due_capture_slots(
    now: datetime,
    calendar: Any,
    market_timezone: ZoneInfo,
    *,
    catch_up: timedelta,
) -> list[tuple[datetime, str]]:
    market_now = now.astimezone(market_timezone)
    return [
        (scheduled_at, tier)
        for scheduled_at, tier in capture_slots(market_now.date(), calendar, market_timezone)
        if timedelta(0) <= market_now - scheduled_at <= catch_up
    ]


def configure_logging(settings: Settings) -> None:
    raw_dir = Path(settings.intraday_options_raw_payload_dir).expanduser()
    log_dir = raw_dir.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    rotating = RotatingFileHandler(
        log_dir / "collector.log", maxBytes=5_000_000, backupCount=6, encoding="utf-8"
    )
    rotating.setFormatter(formatter)
    logger.addHandler(stream)
    logger.addHandler(rotating)


def build_adapter(settings: Settings, coordinator: MoomooCollectionCoordinator):
    return MoomooIntradayOptionsAdapter(
        host=settings.moomoo_host,
        port=settings.moomoo_port,
        symbols=settings.intraday_options_symbol_list,
        target_dtes=[0, 1],
        max_dte=7,
        strike_range_percent=settings.intraday_options_structure_band_percent,
        batch_size=settings.options_snapshot_batch_size,
        snapshot_interval_seconds=settings.options_snapshot_interval_seconds,
        option_chain_interval_seconds=settings.options_chain_interval_seconds,
        option_metadata_interval_seconds=settings.options_metadata_interval_seconds,
        history_request_interval_seconds=settings.moomoo_history_request_interval_seconds,
        min_quote_coverage=settings.intraday_options_min_quote_coverage,
        rate_limiter=coordinator,
    )


def run_capture(
    settings: Settings,
    store: IntradayOptionStore,
    *,
    scheduled_at: datetime,
    tier: str,
    now: datetime | None = None,
) -> str:
    coordinator = MoomooCollectionCoordinator(settings)
    adapter = build_adapter(settings, coordinator)
    band = (
        settings.intraday_options_structure_band_percent
        if tier == "structure"
        else settings.intraday_options_fast_band_percent
    )
    try:
        result = IntradayOptionCaptureService(
            store, ZoneInfo(settings.market_timezone)
        ).collect(
            adapter,
            scheduled_at=scheduled_at,
            capture_tier=tier,
            symbols=settings.intraday_options_symbol_list,
            band_percent=band,
            now=now,
        )
    finally:
        adapter.close()
        coordinator.close()
    logger.info(
        "capture run_id=%s tier=%s scheduled_at=%s status=%s skipped=%s",
        result.run_id,
        tier,
        scheduled_at.isoformat(),
        result.status,
        result.skipped,
    )
    if result.error_message:
        logger.error("capture failed run_id=%s error=%s", result.run_id, result.error_message)
    return result.status


def run_close_finalizer(
    settings: Settings,
    store: IntradayOptionStore,
    *,
    trading_date: date,
) -> bool:
    coordinator = MoomooCollectionCoordinator(settings)
    adapter = build_adapter(settings, coordinator)
    try:
        try:
            payload = adapter.capture_session_close(
                symbols=settings.intraday_options_symbol_list,
                trading_date=trading_date,
            )
        except Exception:
            # A close-finalizer provider error must not terminate the optional
            # intraday collector (and therefore the whole Urus container).
            logger.exception("收盘终结请求异常 date=%s；稍后重试", trading_date)
            return False
        if not payload.get("complete"):
            logger.warning(
                "收盘终结数据不完整 date=%s closes=%s",
                trading_date,
                payload.get("official_closes"),
            )
            return False
        outcomes = store.finalize_trading_day(
            trading_date, payload, market_timezone=ZoneInfo(settings.market_timezone)
        )
        manifest = create_daily_backup(
            settings.intraday_options_database_url,
            settings.intraday_options_raw_payload_dir,
            settings.intraday_options_backup_dir,
            trading_date,
            retention_days=settings.intraday_options_backup_retention_days,
        )
        logger.info(
            "收盘终结完成 date=%s outcomes=%s backup=%s",
            trading_date,
            outcomes,
            manifest,
        )
        return True
    finally:
        adapter.close()
        coordinator.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", choices=("fast", "structure"))
    parser.add_argument("--catch-up-seconds", type=float, default=240.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = Settings()
    configure_logging(settings)
    if not settings.intraday_options_enabled:
        logger.info("盘中期权采集未启用；设置 INTRADAY_OPTIONS_ENABLED=true 后启动")
        return 0
    if not settings.moomoo_enabled:
        logger.error("盘中期权采集要求 MOOMOO_ENABLED=true")
        return 2
    store = IntradayOptionStore(
        settings.intraday_options_database_url,
        settings.intraday_options_raw_payload_dir,
    )
    market_timezone = ZoneInfo(settings.market_timezone)
    calendar_adapter: OpenDMarketAdapter | None = None
    try:
        if args.once:
            now = datetime.now(market_timezone)
            status = run_capture(
                settings, store, scheduled_at=now, tier=args.once, now=now
            )
            return 0 if status in {"succeeded", "partial"} else 1
        calendar, calendar_adapter = build_runtime_calendar(settings)
        logger.info(
            "盘中期权采集已启动 symbols=%s database=%s calendar=%s",
            settings.intraday_options_symbol_list,
            settings.intraday_options_database_url,
            "moomoo_openapi" if calendar_adapter is not None else settings.market_calendar,
        )
        completed_slots: set[tuple[str, str]] = set()
        retry_after: dict[tuple[str, str], float] = {}
        finalized_dates: set[date] = set()
        finalizer_retry_after: dict[date, float] = {}
        while True:
            now = datetime.now(market_timezone)
            due = due_capture_slots(
                now,
                calendar,
                market_timezone,
                catch_up=timedelta(seconds=max(1.0, args.catch_up_seconds)),
            )
            if due:
                scheduled_at, tier = due[-1]
                key = (scheduled_at.isoformat(), tier)
                if key not in completed_slots and time.monotonic() >= retry_after.get(key, 0):
                    status = run_capture(
                        settings, store, scheduled_at=scheduled_at, tier=tier, now=now
                    )
                    if status == "succeeded":
                        completed_slots.add(key)
                        retry_after.pop(key, None)
                    else:
                        retry_after[key] = time.monotonic() + 60.0
            bounds = session_bounds(calendar, now.date())
            if bounds is not None:
                _, closed = bounds
                finalizer_due = closed + timedelta(
                    minutes=max(1, settings.intraday_options_close_finalizer_delay_minutes)
                )
                if (
                    now.astimezone(UTC) >= finalizer_due
                    and now.date() not in finalized_dates
                    and time.monotonic() >= finalizer_retry_after.get(now.date(), 0)
                ):
                    if run_close_finalizer(settings, store, trading_date=now.date()):
                        finalized_dates.add(now.date())
                        finalizer_retry_after.pop(now.date(), None)
                    else:
                        finalizer_retry_after[now.date()] = time.monotonic() + 300.0
            time.sleep(max(1.0, settings.intraday_options_poll_seconds))
    except KeyboardInterrupt:
        logger.info("盘中期权采集已停止")
        return 0
    finally:
        if calendar_adapter is not None:
            calendar_adapter.close()
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
