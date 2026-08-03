"""Initialize the SQLite scheduled-event calendar for 1B and 3B.

This is an explicit, slow administrative operation. Daily workflow runs only
fill gaps after this command has populated the event ledger.

Examples:
    ANOMALO_BASE_URL=https://agent.example \
      .venv/bin/python scripts/initialize_event_schedules.py
    .venv/bin/python scripts/initialize_event_schedules.py --category macro
    .venv/bin/python scripts/initialize_event_schedules.py --force
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings
from app.core.database import Base, create_database
from app.events.initializer import EventScheduleInitializer
from app.integrations.anomalo import HttpAnomaloAdapter
from app.repositories.events import EventRepository


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--category",
        choices=("all", "macro", "instrument"),
        default="all",
        help="Calendar scope; default is both 1B macro and 3B instrument events.",
    )
    parser.add_argument(
        "--symbols",
        default=None,
        help="Comma-separated 3B symbols; default comes from EVENT_INSTRUMENT_SYMBOLS.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Refresh every requested target even when SQLite already has a future event.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1,
        help="Maximum definition+subject targets per Agent call; default 1 avoids the 300-second Agent limit.",
    )
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--agent", default=None)
    parser.add_argument("--timeout-seconds", type=float, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings_kwargs: dict[str, object] = {}
    if args.database_url:
        settings_kwargs["database_url"] = args.database_url
    if args.base_url:
        settings_kwargs["anomalo_base_url"] = args.base_url
    if args.agent:
        settings_kwargs["anomalo_scheduled_agent"] = args.agent
    if args.timeout_seconds is not None:
        settings_kwargs["anomalo_timeout_seconds"] = args.timeout_seconds
    settings = Settings(**settings_kwargs)
    if not settings.anomalo_base_url:
        print("ANOMALO_BASE_URL or --base-url is required for schedule initialization.", file=sys.stderr)
        return 2

    categories = ("macro", "instrument") if args.category == "all" else (args.category,)
    symbols = (
        [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
        if args.symbols is not None
        else settings.event_instrument_symbol_list
    )
    engine, session_factory = create_database(settings.database_url)
    Base.metadata.create_all(bind=engine)
    adapter = HttpAnomaloAdapter(
        settings.anomalo_base_url,
        timeout_seconds=settings.anomalo_timeout_seconds,
    )
    try:
        with session_factory() as session:
            def report_progress(payload: dict[str, object]) -> None:
                print(
                    "[schedule-init] "
                    + json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
                    flush=True,
                )

            result = EventScheduleInitializer(
                EventRepository(session),
                adapter,
                agent=settings.anomalo_scheduled_agent,
                horizon_days=settings.event_discovery_horizon_days,
                batch_size=args.batch_size,
            ).initialize(
                categories=categories,
                instrument_symbols=symbols,
                force=args.force,
                progress=report_progress,
            )
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2, default=str))
            return 0 if result.status in {"succeeded", "partial"} else 1
    finally:
        adapter.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
