"""Export a Step 4 strategy dataset to a standalone JSON backup."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings
from app.core.database import Base, create_database
from app.models import StrategyResearchDatasetModel
from sqlalchemy import select


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--dataset-key")
    selector.add_argument("--dataset-id")
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON path; defaults to backend/data/strategy_research/<dataset-key>.json.",
    )
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)

    settings = Settings(**({"database_url": args.database_url} if args.database_url else {}))
    engine, session_factory = create_database(settings.database_url)
    Base.metadata.create_all(bind=engine)
    try:
        with session_factory() as session:
            statement = select(StrategyResearchDatasetModel)
            if args.dataset_key:
                statement = statement.where(
                    StrategyResearchDatasetModel.dataset_key == args.dataset_key
                )
            else:
                statement = statement.where(StrategyResearchDatasetModel.id == args.dataset_id)
            dataset = session.scalar(statement)
            if dataset is None:
                print("Strategy dataset not found.", file=sys.stderr)
                return 2

            backup = {
                "backup_schema": "urus.strategy_research_backup.v1",
                "exported_at": datetime.now(UTC).isoformat(),
                "dataset": {
                    "id": dataset.id,
                    "dataset_key": dataset.dataset_key,
                    "label": dataset.label,
                    "status": dataset.status,
                    "source_run_id": dataset.source_run_id,
                    "source_snapshot_id": dataset.source_snapshot_id,
                    "source_run_type": dataset.source_run_type,
                    "captured_at": dataset.captured_at.isoformat(),
                    "created_at": dataset.created_at.isoformat(),
                    "event_collection_status": dataset.event_collection_status,
                    "payload": dataset.payload,
                    "metadata_payload": dataset.metadata_payload,
                    "note": dataset.note,
                },
            }
            backup["content_sha256"] = hashlib.sha256(_canonical(backup)).hexdigest()
            output = Path(args.output) if args.output else Path(
                "data/strategy_research"
            ) / f"{dataset.dataset_key}.json"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(backup, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
            print(
                json.dumps(
                    {
                        "output": str(output),
                        "dataset_key": dataset.dataset_key,
                        "status": dataset.status,
                        "bytes": output.stat().st_size,
                        "content_sha256": backup["content_sha256"],
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
