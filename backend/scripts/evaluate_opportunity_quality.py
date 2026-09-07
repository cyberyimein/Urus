"""Evaluate v3 opportunity quality from raw frozen daily evidence.

The input is intentionally different from the legacy ``evaluate_buy`` CLI:
it contains raw frozen bars, a Universe, a calendar, and optional benchmark
bars.  It does not accept precomputed P&L, fills, response delays, or orders.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.research.manifest import write_jsonl_atomic  # noqa: E402
from app.research.opportunity_quality import (  # noqa: E402
    DEFAULT_PRIMARY_VIEW_STATUSES,
    OpportunityQualityError,
    QualityConfig,
    run_opportunity_quality,
)


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("时间参数必须包含时区")
    return parsed


def _read_payload(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} 不是有效 JSON：{exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit("输入文件根节点必须是 JSON object")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="原始冻结证据 JSON。")
    parser.add_argument("--output", required=True, type=Path, help="不可变 JSONL 研究产物。")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--pool-version", required=True)
    parser.add_argument("--created-at", required=True, type=_parse_dt)
    parser.add_argument("--knowledge-at", required=True, type=_parse_dt)
    parser.add_argument("--evaluation-as-of", type=_parse_dt)
    parser.add_argument("--development-start", required=True, type=date.fromisoformat)
    parser.add_argument("--development-end", required=True, type=date.fromisoformat)
    parser.add_argument("--lock-start", required=True, type=date.fromisoformat)
    parser.add_argument("--lock-end", required=True, type=date.fromisoformat)
    parser.add_argument("--mode", default="daily", choices=("daily",))
    parser.add_argument("--market-timezone", default="America/New_York")
    parser.add_argument("--rules-hash", default="s1_breakout_v1")
    parser.add_argument("--dataset-id")
    parser.add_argument("--primary-source-mode")
    parser.add_argument("--primary-data-source")
    parser.add_argument("--primary-horizon", type=int, default=1)
    parser.add_argument("--seed", type=int, default=5000)
    parser.add_argument("--bootstrap-replicates", type=int, default=5000)
    parser.add_argument(
        "--primary-view-status",
        dest="primary_view_statuses",
        action="append",
        choices=("triggered_unverified", "in_entry_zone"),
        help="注册主 cohort 可接受的 view status；可重复传入。",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = _read_payload(args.input)
    config = QualityConfig(
        run_id=args.run_id,
        pool_version=args.pool_version,
        created_at=args.created_at,
        knowledge_at=args.knowledge_at,
        evaluation_as_of=args.evaluation_as_of,
        development_start=args.development_start,
        development_end=args.development_end,
        lock_start=args.lock_start,
        lock_end=args.lock_end,
        mode=args.mode,
        market_timezone=args.market_timezone,
        rules_hash=args.rules_hash,
        dataset_id=args.dataset_id,
        seed=args.seed,
        bootstrap_replicates=args.bootstrap_replicates,
        primary_source_mode=args.primary_source_mode,
        primary_data_source=args.primary_data_source,
        primary_horizon=args.primary_horizon,
        primary_view_statuses=(
            tuple(args.primary_view_statuses)
            if args.primary_view_statuses
            else DEFAULT_PRIMARY_VIEW_STATUSES
        ),
    )
    try:
        result = run_opportunity_quality(payload=payload, config=config)
        write_jsonl_atomic(args.output, manifest=result.manifest, records=result.records())
    except (OpportunityQualityError, ValueError, TypeError) as exc:
        raise SystemExit(f"R04 opportunity quality failed: {exc}") from exc
    print(
        json.dumps(
            {
                "output": str(args.output),
                "manifest_hash": result.manifest.content_sha256,
                "assessment": result.assessment,
                "counts": {
                    "scans": len(result.scans),
                    "scan_count_all_history": len(result.scans),
                    "requested_view_scan_count": result.assessment["metrics"].get(
                        "requested_view_scan_count", 0
                    ),
                    "setups": len(result.setups),
                    "triggers": len(result.triggers),
                    "snapshots": len(result.snapshots),
                    "labels": len(result.labels),
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
