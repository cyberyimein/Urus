"""Run a Stage 4B Urus Agent decision against a frozen packet."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.urus_agent.contracts import AgentTask
from app.core.config import get_settings
from app.urus_agent.providers import FakeLLMProvider, OpenRouterProvider
from app.urus_agent.runtime import UrusAgentRuntime


def _fake_output(task: AgentTask) -> dict:
    if task.task_type == "options_structure":
        return {
            "schema_version": "urus.options_decision.v1",
            "symbol": task.target_symbol or "UNKNOWN",
            "as_of": None,
            "status": "insufficient_data",
            "gamma_regime": "unknown",
            "thesis": "Fake provider smoke test.",
            "horizon": {"expiration": None, "days_to_expiry": None},
            "structure": {"kind": "none", "execution_ready": False, "legs": [], "net_debit_or_credit": None, "max_profit": None, "max_loss": None, "breakevens": []},
            "scenario_anchors": {"spot": None, "expected_move": None, "max_pain": None, "primary_gamma_flip": None, "call_wall": None, "put_wall": None},
            "confidence": 0.0,
            "evidence": [],
            "uncertainties": ["fake provider"],
            "invalidation_conditions": [],
            "disclaimer": "Research output only; no order was placed.",
        }
    rankings = [
        {
            "rank": index,
            "symbol": symbol,
            "themes": [],
            "action": "observe",
            "strict_sepa_completeness": "not_evaluable",
            "score": 0.0,
            "confidence": 0.0,
            "thesis": "Fake provider smoke test.",
            "evidence": [],
            "risks": ["fake provider"],
            "missing_fields": [],
            "invalidation_conditions": [],
        }
        for index, symbol in enumerate(task.symbols, start=1)
    ]
    return {
        "schema_version": "urus.equity_decision.v1",
        "as_of": None,
        "status": "decision",
        "market_regime": {"classification": "unknown", "confidence": 0.0, "evidence": []},
        "rankings": rankings,
        "portfolio_warnings": ["fake provider"],
        "disclaimer": "Research output only; no order was placed.",
    }


def _optional_int(value: str | None, fallback: int | None) -> int | None:
    raw = value.strip() if isinstance(value, str) else value
    if raw in (None, ""):
        return fallback
    parsed = int(raw)
    return parsed if parsed > 0 else None


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--task-type", choices=("equity_ranking", "options_structure"), default="equity_ranking")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--target-symbol", default=None)
    parser.add_argument("--provider", choices=("fake", "openrouter"), default="fake")
    parser.add_argument("--model", default=os.getenv("URUS_AGENT_MODEL") or settings.urus_agent_model)
    parser.add_argument(
        "--stage",
        choices=("equity", "market", "theme", "synthesis", "options"),
        default=None,
        help="Select the stage-specific prompt used by this invocation.",
    )
    parser.add_argument("--theme", default=None, help="Theme name for a theme-stage validation.")
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print bounded validation metrics instead of raw model turns and tool payloads.",
    )
    parser.add_argument(
        "--metadata-file",
        default=None,
        help="JSON object merged into task.metadata (used by synthesis validation).",
    )
    parser.add_argument(
        "--save-output",
        default=None,
        help="Save the complete DecisionResult JSON for a later synthesis validation.",
    )
    args = parser.parse_args(argv)
    packet = json.loads(Path(args.input).read_text(encoding="utf-8"))
    source = packet.get("source") or {}
    observations = packet.get("observations") or {}
    pre_close = observations.get("pre_close") or {}
    symbols = [value.strip().upper() for value in args.symbols.split(",") if value.strip()]
    if not symbols:
        symbols = [str(item.get("symbol")).upper() for item in pre_close.get("instruments", []) if item.get("symbol")]
    run = (pre_close.get("run") or {}) if isinstance(pre_close, dict) else {}
    raw_cutoff = run.get("cutoff_time") or source.get("captured_at")
    cutoff = datetime.fromisoformat(str(raw_cutoff).replace("Z", "+00:00")) if raw_cutoff else datetime.now(UTC)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=UTC)
    stage = args.stage or ("options" if args.task_type == "options_structure" else "equity")
    if args.task_type == "options_structure" and stage != "options":
        parser.error("options_structure requires --stage options")
    if args.task_type == "equity_ranking" and stage == "options":
        parser.error("--stage options requires --task-type options_structure")
    metadata = {
        "scope_kind": stage,
        "theme": args.theme,
        "benchmark_symbols": [symbol for symbol in ("SPY", "QQQ", "SMH", "IGV") if symbol in symbols],
    }
    if args.metadata_file:
        loaded_metadata = json.loads(Path(args.metadata_file).read_text(encoding="utf-8"))
        if not isinstance(loaded_metadata, dict):
            parser.error("--metadata-file must contain a JSON object")
        metadata.update(loaded_metadata)
    task = AgentTask(
        task_type=args.task_type,
        dataset_key=str(source.get("dataset_key") or "frozen-packet"),
        cutoff_time=cutoff,
        symbols=symbols,
        target_symbol=args.target_symbol,
        requested_skill="urus-options-decision" if args.task_type == "options_structure" else "urus-equity-decision",
        stage=stage,
        metadata=metadata,
    )
    if args.provider == "fake":
        provider = FakeLLMProvider([{"message": {"role": "assistant", "content": json.dumps(_fake_output(task), ensure_ascii=False)}}])
    else:
        provider = OpenRouterProvider(
            api_key=os.getenv("OPENROUTER_API_KEY") or settings.openrouter_api_key or "",
            model=args.model,
            base_url=os.getenv("OPENROUTER_BASE_URL") or settings.openrouter_base_url,
            timeout_seconds=float(os.getenv("URUS_AGENT_TIMEOUT_SECONDS") or settings.urus_agent_timeout_seconds),
            max_completion_tokens=_optional_int(
                os.getenv("URUS_AGENT_MAX_COMPLETION_TOKENS"),
                settings.urus_agent_max_completion_tokens,
            ),
            temperature=float(os.getenv("URUS_AGENT_TEMPERATURE") or settings.urus_agent_temperature),
        )
    result = UrusAgentRuntime(provider).decide(task, packet)
    output = result.model_dump(mode="json")
    if args.save_output:
        Path(args.save_output).write_text(
            json.dumps(output, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.summary:
        output = {
            "status": result.status,
            "stage": stage,
            "provider": result.provider,
            "model": result.model,
            "tool_call_count": result.tool_call_count,
            "duration_ms": result.duration_ms,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "estimated_cost": result.estimated_cost,
            "error_code": result.error_code,
            "error_message": result.error_message,
            "tool_calls": [
                {
                    "name": call.get("name"),
                    "prefetched": call.get("prefetched", False),
                    "ok": (call.get("result") or {}).get("ok"),
                    "error_code": ((call.get("result") or {}).get("error") or {}).get("code"),
                }
                for call in result.tool_calls
            ],
            "output": result.output,
        }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if result.status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
