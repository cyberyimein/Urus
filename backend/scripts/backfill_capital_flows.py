"""Backfill the latest completed Moomoo capital-flow sessions into SQLite."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
import os
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

from app.core.config import Settings  # noqa: E402
from app.core.database import create_database  # noqa: E402
from app.integrations.moomoo import OpenDMarketAdapter  # noqa: E402
from app.repositories.capital_flows import CapitalFlowRepository  # noqa: E402
from app.services.capital_flow import CapitalFlowService  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=5, help="Completed sessions to seed (default: 5).")
    parser.add_argument("--host", help="Override MOOMOO_HOST.")
    parser.add_argument("--port", type=int, help="Override MOOMOO_PORT.")
    parser.add_argument("--symbols", help="Comma-separated ETF symbols; defaults to settings.")
    parser.add_argument("--database-url", help="Override DATABASE_URL; must be SQLite.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = Settings()
    database_url = args.database_url or settings.database_url
    if not database_url.startswith("sqlite"):
        raise SystemExit("backfill_capital_flows.py only writes to a configured SQLite database")
    days = max(1, min(args.days, 30))
    symbols = (
        [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
        if args.symbols
        else settings.capital_flow_symbol_list
    )
    _, session_factory = create_database(database_url)
    adapter = OpenDMarketAdapter(
        host=args.host or settings.moomoo_host,
        port=args.port or settings.moomoo_port,
        market_timezone=settings.market_timezone,
        history_days=settings.moomoo_history_days,
        sdk_home=Path(settings.moomoo_sdk_home),
        market_symbols=symbols,
    )
    try:
        with session_factory() as session:
            service = CapitalFlowService(
                CapitalFlowRepository(session),
                adapter,
                symbols=symbols,
                calendar_name=settings.market_calendar,
                cache_days=settings.capital_flow_cache_days,
                projection_days=settings.capital_flow_projection_days,
            )
            result = service.backfill(datetime.now(UTC), days=days)
            print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        adapter.close()


if __name__ == "__main__":
    main()
