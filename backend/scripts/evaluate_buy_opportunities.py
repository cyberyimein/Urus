"""Run a deterministic offline buy-opportunity evaluation or frozen-evidence replay."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.buy_opportunities.breakout import IntradayObservation  # noqa: E402
from app.buy_opportunities.contracts import FrozenEvidence  # noqa: E402
from app.buy_opportunities.evidence import InMemoryDailyEvidenceReader  # noqa: E402
from app.research.buy_evaluation import (  # noqa: E402
    EvaluationConfig,
    EvaluationRow,
    build_assessment,
    build_daily_nav_comparison,
)
from app.research.buy_execution import (  # noqa: E402
    ExecutionPolicy,
    PaperPortfolioPolicy,
    PriceMapping,
    QuoteObservation,
)
from app.research.buy_replay import replay_frozen_buy_opportunities  # noqa: E402
from app.research.manifest import ResearchManifest, write_jsonl_atomic  # noqa: E402
from app.research.portfolio_ledger import LedgerMark  # noqa: E402


def _read_jsonl(path: Path, *, accepted_types: set[str] | None = None) -> list[dict]:
    records: list[dict] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"{path}:{line_number} 必须是 JSON object")
        record_type = record.get("type")
        if record_type in {"manifest", "assessment"}:
            continue
        if (
            accepted_types is not None
            and record_type is not None
            and record_type not in accepted_types
        ):
            continue
        record.pop("type", None)
        records.append(record)
    return records


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("时间参数必须包含时区")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="EvaluationRow JSONL input.")
    parser.add_argument(
        "--replay-input",
        type=Path,
        help="Frozen evidence replay JSON input; replaces --input.",
    )
    parser.add_argument("--output", required=True, type=Path, help="Immutable assessment JSONL output.")
    parser.add_argument("--scans", type=Path, help="Optional scan JSONL for coverage accounting.")
    parser.add_argument("--diagnostics", type=Path, help="Optional snapshot JSONL for the 10:00 grid.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--pool-version", required=True)
    parser.add_argument("--source-mode", default="historical_reconstruction")
    parser.add_argument("--created-at", required=True, help="Aware ISO timestamp.")
    parser.add_argument("--knowledge-at", required=True, help="Aware ISO timestamp.")
    parser.add_argument("--strategy-version", default="s1_breakout_v1")
    parser.add_argument("--primary-policy", default="Q1")
    parser.add_argument("--baseline-name", default="benchmark_hold")
    parser.add_argument("--development-start", required=True, type=date.fromisoformat)
    parser.add_argument("--development-end", required=True, type=date.fromisoformat)
    parser.add_argument("--lock-start", required=True, type=date.fromisoformat)
    parser.add_argument("--lock-end", required=True, type=date.fromisoformat)
    parser.add_argument("--min-sample-size", type=int, default=30)
    parser.add_argument("--primary-parameter-hash", default="unknown")
    parser.add_argument("--primary-exit-policy", default="unknown")
    parser.add_argument("--primary-funds-policy", default="unknown")
    parser.add_argument("--primary-user-policy", default="unknown")
    parser.add_argument("--primary-response-minutes", type=int, default=15)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--outcome-knowledge-at", help="Aware ISO timestamp for outcome replay.")
    return parser.parse_args()


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _aware_mapping(raw: dict[str, str] | None) -> dict[str, datetime]:
    return {key: _parse_datetime(value) for key, value in (raw or {}).items()}


def _run_replay(
    args: argparse.Namespace,
) -> tuple[list[EvaluationRow], list[dict], dict[str, str], dict]:
    payload = json.loads(args.replay_input.read_text(encoding="utf-8"))
    evidence = {
        symbol: FrozenEvidence(**item)
        for symbol, item in payload.get("evidence", {}).items()
    }
    observations = {
        symbol: [IntradayObservation(**item) for item in items]
        for symbol, items in payload.get("observations", {}).items()
    }
    quotes = {
        symbol: [QuoteObservation(**item) for item in items]
        for symbol, items in payload.get("quotes", {}).items()
    }
    sell_quotes = {
        symbol: {
            date.fromisoformat(day): QuoteObservation(**item)
            for day, item in items.items()
        }
        for symbol, items in payload.get("sell_quotes", {}).items()
    }
    marks = [LedgerMark(**item) for item in payload.get("marks", [])]
    raw_mappings = payload.get("price_mappings", payload.get("price_mapping", {}))
    if raw_mappings and "mapping_id" in raw_mappings:
        raw_mappings = {raw_mappings["symbol"]: raw_mappings}
    price_mappings = {
        symbol: PriceMapping(**item)
        for symbol, item in (raw_mappings or {}).items()
    }
    session_dates = {
        symbol: [date.fromisoformat(day) for day in days]
        for symbol, days in payload.get("calendar", {}).items()
    }
    result = replay_frozen_buy_opportunities(
        universe_items=payload.get("universe", []),
        evidence_reader=InMemoryDailyEvidenceReader(evidence),
        pool_version=args.pool_version,
        knowledge_at=_parse_datetime(args.knowledge_at),
        observations_by_symbol=observations,
        quotes_by_symbol=quotes,
        delivered_at_by_trigger=_aware_mapping(payload.get("delivered_at_by_trigger")),
        ready_at_by_trigger=_aware_mapping(payload.get("ready_at_by_trigger")),
        sent_at_by_trigger=_aware_mapping(payload.get("sent_at_by_trigger")),
        previous_close_by_bar=payload.get("previous_close_by_bar"),
        sell_quotes_by_symbol=sell_quotes,
        outcome_knowledge_at_by_symbol=(
            {
                symbol: _parse_datetime(args.outcome_knowledge_at)
                for symbol in evidence
            }
            if args.outcome_knowledge_at
            else {}
        ),
        session_dates_by_symbol=session_dates,
        theme_by_symbol=payload.get("theme_by_symbol", {}),
        price_mappings_by_symbol=price_mappings,
        policy=ExecutionPolicy(name=args.primary_policy),
        portfolio=PaperPortfolioPolicy(),
        initial_cash=float(payload.get("initial_cash", 100_000.0)),
        marks=marks,
        parameter_hash=args.primary_parameter_hash,
    )
    trading_dates = sorted(
        {
            day
            for days in session_dates.values()
            for day in days
        }
        or {
            bar.date
            for frozen in evidence.values()
            for bar in frozen.bars
        }
    )
    benchmark_returns = {
        date.fromisoformat(day): float(value)
        for day, value in payload.get("benchmark_returns", {}).items()
    }
    cash_returns = {
        date.fromisoformat(day): float(value)
        for day, value in payload.get("cash_returns", {}).items()
    }
    marks_by_date: dict[date, dict[str, float]] = {}
    for mark in marks:
        marks_by_date.setdefault(mark.marked_at.date(), {})[mark.symbol] = mark.price
    daily_nav = build_daily_nav_comparison(
        result.rows,
        initial_cash=float(payload.get("initial_cash", 100_000.0)),
        trading_dates=trading_dates,
        benchmark_returns=benchmark_returns,
        cash_returns=cash_returns,
        marks_by_date=marks_by_date,
        portfolio_policy=PaperPortfolioPolicy(),
    )
    records: list[dict] = []
    for record_type, values in (
        ("scan", result.scans),
        ("setup", result.setups),
        ("trigger_evaluation", result.trigger_evaluations),
        ("entry_intent", result.intents),
        ("execution", result.executions),
        ("exit", result.exits),
        ("evaluation_row", result.rows),
        ("forward_label", result.labels),
    ):
        records.extend(
            {"type": record_type, **record.model_dump(mode="json")}
            for record in values
        )
    records.append({"type": "portfolio_ledger", **result.ledger.model_dump(mode="json")})
    records.append({"type": "daily_nav_comparison", **daily_nav})
    records.extend(
        {"type": "label_diagnostic", **diagnostic}
        for diagnostic in result.label_diagnostics
    )
    records.extend(
        {"type": "diagnostic_snapshot", **diagnostic}
        for diagnostic in result.diagnostics
    )
    return result.rows, records, {
        "replay_input": _hash_file(args.replay_input),
        **{
            name: _hash_json(payload.get(name, "unavailable"))
            for name in (
                "universe",
                "evidence",
                "observations",
                "quotes",
                "sell_quotes",
                "marks",
                "calendar",
                "theme_by_symbol",
                "benchmark_returns",
                "cash_returns",
            )
        },
        "price_mapping": _hash_json(
            payload.get("price_mappings", payload.get("price_mapping", "unavailable"))
        ),
        "scans": _hash_json(
            [scan.model_dump(mode="json") for scan in result.scans]
        ),
        "diagnostics": _hash_json(result.diagnostics),
    }, daily_nav


def main() -> None:
    args = parse_args()
    if bool(args.input) == bool(args.replay_input):
        raise SystemExit("必须且只能提供 --input 或 --replay-input")
    replay_records: list[dict] = []
    daily_nav_comparison: dict | None = None
    if args.replay_input:
        rows, replay_records, input_hashes, daily_nav_comparison = _run_replay(args)
        scans = [record for record in replay_records if record["type"] == "scan"]
        diagnostics = [
            record
            for record in replay_records
            if record["type"] == "diagnostic_snapshot"
        ]
    else:
        input_records = _read_jsonl(args.input, accepted_types={"evaluation_row"})
        rows = [EvaluationRow(**record) for record in input_records]
        scans = _read_jsonl(args.scans, accepted_types={"scan"}) if args.scans else []
        diagnostics = _read_jsonl(args.diagnostics) if args.diagnostics else []
        input_hashes = {
            "evaluation_rows": _hash_file(args.input),
            "calendar": "unavailable",
            "price_mapping": "unavailable",
        }
    code_hash = hashlib.sha256(
        b"".join(
            path.read_bytes()
            for path in (
                BACKEND_DIR / "scripts" / "evaluate_buy_opportunities.py",
                BACKEND_DIR / "app" / "buy_opportunities" / "scanner.py",
                BACKEND_DIR / "app" / "buy_opportunities" / "evidence.py",
                BACKEND_DIR / "app" / "buy_opportunities" / "breakout.py",
                BACKEND_DIR / "app" / "buy_opportunities" / "lifecycle.py",
                BACKEND_DIR / "app" / "research" / "buy_replay.py",
                BACKEND_DIR / "app" / "research" / "buy_execution.py",
                BACKEND_DIR / "app" / "research" / "portfolio_ledger.py",
                BACKEND_DIR / "app" / "research" / "buy_evaluation.py",
                BACKEND_DIR / "app" / "research" / "manifest.py",
                BACKEND_DIR / "app" / "decision_harness" / "strategies.py",
                BACKEND_DIR / "app" / "decision_harness" / "setup_projection.py",
            )
        )
    ).hexdigest()
    scan_hash = (
        _hash_file(args.scans)
        if args.scans
        else input_hashes.get("scans", "unavailable")
    )
    diagnostic_hash = (
        _hash_file(args.diagnostics)
        if args.diagnostics
        else input_hashes.get("diagnostics", "unavailable")
    )
    config = EvaluationConfig(
        strategy_version=args.strategy_version,
        primary_policy=args.primary_policy,
        baseline_name=args.baseline_name,
        development_start=args.development_start,
        development_end=args.development_end,
        lock_start=args.lock_start,
        lock_end=args.lock_end,
        min_sample_size=args.min_sample_size,
        primary_source_mode=args.source_mode,
        primary_response_minutes=args.primary_response_minutes,
        primary_exit_policy=args.primary_exit_policy,
        primary_funds_policy=args.primary_funds_policy,
        primary_user_policy=args.primary_user_policy,
        primary_parameter_hash=args.primary_parameter_hash,
        seed=args.seed,
    )
    created_at = _parse_datetime(args.created_at)
    knowledge_at = _parse_datetime(args.knowledge_at)
    manifest = ResearchManifest.build(
        run_id=args.run_id,
        created_at=created_at,
        pool_version=args.pool_version,
        knowledge_at=knowledge_at,
        source_mode=args.source_mode,
        parameters={
            **config.model_dump(mode="json"),
            "execution_policy": ExecutionPolicy(name=args.primary_policy).model_dump(
                mode="json"
            ),
            "portfolio_policy": PaperPortfolioPolicy().model_dump(mode="json"),
        },
        universe_symbols=sorted({row.symbol for row in rows}),
        input_hashes={
            **input_hashes,
            "scans": scan_hash,
            "diagnostics": diagnostic_hash,
            "code": code_hash,
        },
    )
    assessment = build_assessment(
        assessment_id=f"{args.run_id}:assessment",
        manifest_hash=manifest.content_sha256,
        config=config,
        rows=rows,
        scans=scans,
        diagnostic_snapshots=diagnostics,
        daily_nav_comparison=daily_nav_comparison,
    )
    write_jsonl_atomic(
        args.output,
        manifest=manifest,
        records=[
            {"type": "assessment", **assessment.model_dump(mode="json")},
            *(
                record
                for record in replay_records
                if record["type"] != "evaluation_row"
            ),
            *(
                {"type": "evaluation_row", **row.model_dump(mode="json")}
                for row in rows
            ),
        ],
    )
    print(
        json.dumps(
            {
                "run_id": args.run_id,
                "output": str(args.output),
                "row_count": len(rows),
                "applicability": assessment.applicability,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
