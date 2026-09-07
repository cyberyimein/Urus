from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_config
from .integrations.moomoo import OpenDMarketAdapter
from .runtime import CollectorRuntime


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the standalone urus-moomoo collector")
    parser.add_argument("command", nargs="?", choices=("validate", "run-once"), default="validate")
    parser.add_argument("--config", help="Path to the local-only task TOML")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    enabled = sum(task.enabled for task in config.tasks)
    print(f"urus-moomoo config ok: {enabled}/{len(config.tasks)} tasks enabled; output={Path(config.output_dir)}")
    if args.command == "run-once":
        runtime = CollectorRuntime(
            OpenDMarketAdapter(
                host=_required_env_or_config(config, "opend_host"),
                port=int(_required_env_or_config(config, "opend_port")),
                market_symbols=[symbol for task in config.tasks if task.enabled for symbol in task.symbols],
            ),
            config,
        )
        try:
            for task in config.tasks:
                if task.enabled:
                    runtime.collect_task(task, __import__("datetime").datetime.now().astimezone())
        finally:
            runtime.close()
    return 0


def _required_env_or_config(config, key: str) -> str:
    value = getattr(config, key, None)
    if value:
        return str(value)
    raise ValueError(f"runtime.{key} is required for run-once; keep it in the local task config")


if __name__ == "__main__":
    raise SystemExit(main())
