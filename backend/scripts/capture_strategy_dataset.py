"""Capture one completed workflow snapshot as a Step 4 strategy evidence bundle."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings
from app.core.database import Base, create_database
from app.repositories.strategy import StrategyResearchRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--dataset-key", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--note", default=None)
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)

    settings = Settings(**({"database_url": args.database_url} if args.database_url else {}))
    engine, session_factory = create_database(settings.database_url)
    Base.metadata.create_all(bind=engine)
    try:
        with session_factory() as session:
            model = StrategyResearchRepository(session).capture_run(
                run_id=args.run_id,
                dataset_key=args.dataset_key,
                label=args.label,
                note=args.note,
            )
            print(
                json.dumps(
                    {
                        "id": model.id,
                        "dataset_key": model.dataset_key,
                        "label": model.label,
                        "status": model.status,
                        "source_run_id": model.source_run_id,
                        "source_snapshot_id": model.source_snapshot_id,
                        "event_collection_status": model.event_collection_status,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
