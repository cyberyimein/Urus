from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import load_config
from .integrations.moomoo import OpenDMarketAdapter
from .integrations.moomoo_intraday_options import MoomooIntradayOptionsAdapter
from .integrations.moomoo_options import MoomooOptionsAdapter
from .runtime import CollectorRuntime
from .services.calendar import build_moomoo_calendar
from .services.scheduler import CollectorScheduler


class _Providers:
    def __init__(self, market: OpenDMarketAdapter, options: Any | None = None) -> None:
        self.market = market
        self.options = options

    def __getattr__(self, name: str) -> Any:
        target = self.options if name.startswith(("options_", "capture_")) else self.market
        if target is None:
            raise AttributeError(name)
        return getattr(target, name)

    def close(self) -> None:
        if self.options is not None:
            self.options.close()
        self.market.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the standalone urus-moomoo collector")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("validate", "run-once", "schedule"),
        default="validate",
    )
    parser.add_argument("--config", help="Path to the local-only task TOML")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    enabled = sum(task.enabled for task in config.tasks)
    print(
        f"urus-moomoo config ok: {enabled}/{len(config.tasks)} tasks enabled; "
        f"output={Path(config.output_dir)}"
    )
    if args.command != "validate":
        runtime = _build_runtime(config)
        try:
            if args.command == "run-once":
                now = datetime.now(ZoneInfo(config.timezone))
                for task in config.tasks:
                    if task.enabled:
                        runtime.collect_task(task, now)
            else:
                calendar = build_moomoo_calendar(
                    runtime.provider, config.exchange_calendar, cache_path=config.calendar_cache
                )
                CollectorScheduler(
                    config.tasks,
                    timezone=config.timezone,
                    calendar=calendar,
                    runner=runtime.collect_task,
                    store=runtime.store,
                    catch_up_seconds=config.catch_up_seconds,
                    market_timezone=config.market_timezone,
                ).run_forever(poll_seconds=config.poll_seconds)
        finally:
            runtime.close()
    return 0


def _build_runtime(config) -> CollectorRuntime:
    host = _required_env_or_config(config, "opend_host")
    port = int(_required_env_or_config(config, "opend_port"))

    def factory(coordinator, admission):
        symbols = [symbol for task in config.tasks if task.enabled for symbol in task.symbols]
        market = OpenDMarketAdapter(
            host=host,
            port=port,
            market_symbols=symbols,
            rate_limiter=coordinator,
            history_admission=admission,
        )
        option_operations = {
            task.operation for task in config.tasks if task.enabled and task.operation != "instrument_cards"
        } & {
            "options_snapshot",
            "capture_intraday",
            "capture_session_close",
            "capture_eod_options",
        }
        if not option_operations:
            return _Providers(market)
        settings = dict(config.options or {})
        option_symbols = [
            symbol for task in config.tasks if task.enabled and task.operation in option_operations
            for symbol in task.symbols
        ]
        adapter = (
            MoomooIntradayOptionsAdapter
            if option_operations
            & {"capture_intraday", "capture_session_close", "capture_eod_options"}
            else MoomooOptionsAdapter
        )
        options = adapter(
            host=host,
            port=port,
            symbols=list(dict.fromkeys(option_symbols)),
            rate_limiter=coordinator,
            **settings,
        )
        return _Providers(market, options)

    return CollectorRuntime.from_factory(config, factory)


def _required_env_or_config(config, key: str) -> str:
    value = getattr(config, key, None)
    if value:
        return str(value)
    raise ValueError(f"runtime.{key} is required for run-once; keep it in the local task config")


if __name__ == "__main__":
    raise SystemExit(main())
