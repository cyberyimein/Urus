from __future__ import annotations

import argparse

from .config import load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate local urus-moomoo task configuration")
    parser.add_argument("--config", help="Path to the local-only task TOML")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    enabled = sum(task.enabled for task in config.tasks)
    print(f"urus-moomoo config ok: {enabled}/{len(config.tasks)} tasks enabled")
    return 0
