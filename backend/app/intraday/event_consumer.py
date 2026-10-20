from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
import json
import sys
import time
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import create_database
from app.intraday.event_service import run_once


def run_consumer(
    *,
    mode: str,
    settings: Settings,
    source_db_path: str,
    session_factory: Callable[[], Session],
    max_slots: int = 12,
    interval_seconds: float = 60.0,
    run_once_fn: Callable[..., dict[str, Any]] = run_once,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
    iterations: int | None = None,
) -> int:
    """Run one explicit cycle or a small polling loop; never starts at app boot."""

    if mode not in {"once", "watch"}:
        raise ValueError("mode must be once or watch")
    if not settings.intraday_events_enabled:
        print(
            json.dumps(
                {
                    "status": "disabled",
                    "message": "intraday_events_enabled is false; no event consumer was started.",
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    if isinstance(max_slots, bool) or not isinstance(max_slots, int) or max_slots < 1:
        raise ValueError("max_slots must be a positive integer")
    if mode == "watch" and interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")

    spec: Mapping[str, Any] = {
        "market_calendar": settings.market_calendar,
        "market_timezone": settings.market_timezone,
    }
    completed_iterations = 0
    while iterations is None or completed_iterations < iterations:
        result = run_once_fn(
            source_db_path=source_db_path,
            session_factory=session_factory,
            max_slots=max_slots,
            now=now_fn(),
            spec=spec,
        )
        print(json.dumps(result, ensure_ascii=False, default=str))
        completed_iterations += 1
        if mode == "once":
            return 1 if result.get("status") == "error" else 0
        if iterations is not None and completed_iterations >= iterations:
            break
        sleep_fn(interval_seconds)
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    session_factory: Callable[[], Session] | None = None,
    run_once_fn: Callable[..., dict[str, Any]] = run_once,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    parser = argparse.ArgumentParser(description="Explicit P4 intraday event consumer")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="process one bounded cycle")
    mode.add_argument("--watch", action="store_true", help="poll using run_once only")
    parser.add_argument("--source-db-path", default=None)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--max-slots", type=int, default=12)
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    args = parser.parse_args(argv)
    effective_settings = settings or get_settings()
    source_path = args.source_db_path or effective_settings.sidecar_capture_database_path
    engine = None
    effective_session_factory = session_factory
    if effective_session_factory is None:
        database_url = args.database_url or effective_settings.database_url
        engine, effective_session_factory = create_database(database_url)
    try:
        return run_consumer(
            mode="watch" if args.watch else "once",
            settings=effective_settings,
            source_db_path=source_path,
            session_factory=effective_session_factory,
            max_slots=args.max_slots,
            interval_seconds=args.interval_seconds,
            run_once_fn=run_once_fn,
            sleep_fn=sleep_fn,
            now_fn=now_fn,
        )
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
