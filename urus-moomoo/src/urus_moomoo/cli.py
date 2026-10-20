from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import load_config
from .collection_runner import build_collection_runner
from .integrations.moomoo import OpenDMarketAdapter
from .integrations.moomoo_intraday_options import MoomooIntradayOptionsAdapter
from .integrations.moomoo_options import MoomooOptionsAdapter
from .publisher import ObservationPublisher
from .intraday_pool import IntradayPoolPlanLoader
from .runtime import CollectorRuntime
from .services.calendar import build_moomoo_calendar
from .services.scheduler import CollectorScheduler


class _Providers:
    def __init__(self, market: OpenDMarketAdapter, options: Any | None = None) -> None:
        self.market = market
        self.options = options

    def __getattr__(self, name: str) -> Any:
        if name == "capture_intraday_equities":
            target = self.market
        else:
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
        choices=("validate", "preflight", "run-once", "schedule", "recover-close", "audit"),
        default="validate",
    )
    parser.add_argument("--config", help="Path to the local-only task TOML")
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--apply", action="store_true", help="Write recovered close data; default is preview")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    enabled = sum(task.enabled for task in config.tasks)
    print(
        f"urus-moomoo config ok: {enabled}/{len(config.tasks)} tasks enabled; "
        f"output={Path(config.output_dir)}"
    )
    if args.command in {"recover-close", "audit"}:
        import json
        from .recovery import recover_close, audit_dates

        if args.command == "recover-close":
            if args.date is None:
                parser.error("recover-close requires --date")
            result = recover_close(config, args.date, apply=args.apply)
        else:
            if args.start is None or args.end is None:
                parser.error("audit requires --start and --end (range when tasks were enabled)")
            result = audit_dates(config, args.start, args.end)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "preflight":
        from .preflight import run_preflight

        result = run_preflight(config)
        print(
            "observation preflight ok: "
            f"date={result['trading_date']} groups={result['group_count']} "
            f"market_symbols={result['market_symbol_count']} "
            f"options_symbols={result['options_symbol_count']}"
        )
        return 0
    if args.command != "validate":
        runtime = _build_runtime(config)
        publisher = ObservationPublisher.from_config(config, capture_reader=runtime.store.read_raw)
        publisher_settings = dict(config.publisher or {})
        api_url = publisher.api_url if publisher is not None else str(
            publisher_settings.get("api_url", "")
        ).strip()
        api_timeout = float(publisher_settings.get("timeout_seconds", 15.0))
        pool_plan_loader = IntradayPoolPlanLoader(
            api_url=api_url,
            # Plan reads run inside the due-task runner, after Eoflora's
            # attempts in the tick. Keep the cold-start read bounded so it
            # cannot consume a complete equity shard window.
            timeout_seconds=min(api_timeout, 3.0),
            store=runtime.store,
        )
        collect_and_publish = build_collection_runner(
            runtime,
            publisher,
            pool_plan_loader,
            market_timezone=config.market_timezone,
        )

        try:
            if args.command == "run-once":
                now = datetime.now(ZoneInfo(config.timezone))
                for task in config.tasks:
                    if task.enabled:
                        collect_and_publish(task, now)
            else:
                calendar = build_moomoo_calendar(
                    runtime.provider, config.exchange_calendar, cache_path=config.calendar_cache
                )

                def on_quiet() -> None:
                    if publisher is not None:
                        publisher.retry_pending()

                CollectorScheduler(
                    config.tasks,
                    timezone=config.timezone,
                    calendar=calendar,
                    runner=collect_and_publish,
                    store=runtime.store,
                    catch_up_seconds=config.catch_up_seconds,
                    intraday_catch_up_seconds=config.intraday_catch_up_seconds,
                    market_timezone=config.market_timezone,
                    on_idle=runtime.coordinator.close,
                    on_quiet=on_quiet,
                    quiet_budget_seconds=(3 * publisher.timeout_seconds + config.poll_seconds + 5
                                          if publisher else 45),
                ).run_forever(poll_seconds=config.poll_seconds)
        finally:
            if publisher is not None:
                publisher.close()
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
