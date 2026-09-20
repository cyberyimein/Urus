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
from .publisher import ObservationPublisher
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
        publisher = ObservationPublisher.from_config(config, capture_reader=runtime.store.read_raw)

        def collect_and_publish(task, scheduled_at):
            if publisher is not None:
                resumed_capture_id = publisher.resume_if_ready(task, scheduled_at)
                if resumed_capture_id is not None:
                    return {
                        "scheduled_at": scheduled_at.isoformat(),
                        "contract": {"capture_id": resumed_capture_id},
                        "completed": True,
                    }
                task = publisher.prepare_task(task, scheduled_at)
            if task.operation.strip() == "instrument_cards":
                # The publisher freezes the current Universe immediately before
                # collection.  Extend the request-time history scope to that
                # exact plan; otherwise dynamically discovered symbols are
                # rejected as outside_scope by HistoryAdmission.
                prepare = getattr(runtime.history_admission, "prepare_symbols", None)
                if callable(prepare):
                    prepare(task.symbols)
            result = runtime.collect_task(task, scheduled_at)
            if publisher is not None and not publisher.observe(task, scheduled_at, result):
                result["completed"] = False
            return result

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

                def on_idle() -> None:
                    # A prior API outage must not depend on the scheduler's
                    # narrow market catch-up window.  Retry exact, immutable
                    # capture pairs independently; collection errors remain
                    # isolated so the calendar loop stays alive.
                    if publisher is not None:
                        try:
                            publisher.retry_pending()
                        except Exception as exc:
                            print(f"observation publication retry deferred: {exc}")
                    runtime.coordinator.close()

                CollectorScheduler(
                    config.tasks,
                    timezone=config.timezone,
                    calendar=calendar,
                    runner=collect_and_publish,
                    store=runtime.store,
                    catch_up_seconds=config.catch_up_seconds,
                    intraday_catch_up_seconds=config.intraday_catch_up_seconds,
                    market_timezone=config.market_timezone,
                    on_idle=on_idle,
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
