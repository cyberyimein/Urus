"""CLI wrapper for the canonical Stage 4B packet module."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.urus_agent.packet import build_decision_packet, project_decision_packet


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Stage 4B strategy pair JSON path.")
    parser.add_argument("--output", default=None, help="Output path; defaults beside the input.")
    parser.add_argument("--mode", choices=("full", "equity", "options"), default="full")
    parser.add_argument("--symbols", default="", help="Comma-separated symbol projection. Required for options mode.")
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    pair = json.loads(input_path.read_text(encoding="utf-8"))
    packet = project_decision_packet(
        build_decision_packet(pair),
        mode=args.mode,
        symbols={value.strip() for value in args.symbols.split(",") if value.strip()},
    )
    projection_suffix = "" if args.mode == "full" else f"-{args.mode}"
    if args.mode == "options":
        projection_suffix += "-" + "-".join(packet["projection"]["symbols"]).lower()
    output_path = Path(args.output) if args.output else input_path.with_name(
        f"{input_path.stem}-decision-packet{projection_suffix}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(output_path),
        "schema_version": packet["schema_version"],
        "bytes": output_path.stat().st_size,
        "source_bytes": input_path.stat().st_size,
        "content_sha256": packet["content_sha256"],
        "projection": packet.get("projection", {"mode": "full", "symbols": []}),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
