from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import logging
from typing import Any
from zoneinfo import ZoneInfo

from urus_moomoo.config import CollectionTask
from urus_moomoo.intraday_pool import IntradayPoolPlanLoader
from urus_moomoo.publisher import ObservationPublisher
from urus_moomoo.runtime import CollectorRuntime


logger = logging.getLogger(__name__)
_POOL_OPERATIONS = {
    "capture_intraday_equities",
    "maintain_intraday_equities",
    "finalize_intraday_equities",
}


def build_collection_runner(
    runtime: CollectorRuntime,
    publisher: ObservationPublisher | None,
    pool_plan_loader: IntradayPoolPlanLoader | None,
    *,
    market_timezone: str,
) -> Callable[[CollectionTask, datetime], dict[str, object]]:
    """Compose pool freezing, provider execution, and close-capture publishing."""

    timezone = ZoneInfo(market_timezone)

    def collect_and_publish(
        task: CollectionTask, scheduled_at: datetime
    ) -> dict[str, object]:
        if publisher is not None:
            resumed_capture_id = publisher.resume_if_ready(task, scheduled_at)
            if resumed_capture_id is not None:
                return {
                    "scheduled_at": scheduled_at.isoformat(),
                    "contract": {"capture_id": resumed_capture_id},
                    "completed": True,
                }
            task = publisher.prepare_task(task, scheduled_at)

        operation = task.operation.strip()
        if operation in _POOL_OPERATIONS:
            if pool_plan_loader is None:
                error = RuntimeError("intraday collection plan unavailable: missing plan loader")
            else:
                try:
                    trading_date = scheduled_at.astimezone(timezone).date()
                    task = pool_plan_loader.prepare_task(task, trading_date)
                    error = None
                except Exception as exc:
                    error = exc
            if error is not None:
                terminal_skip = operation in {
                    "capture_intraday_equities",
                    "finalize_intraday_equities",
                }
                logger.error(
                    "intraday task unavailable; no legacy symbol fallback: task=%s date=%s reason=%s",
                    task.name,
                    scheduled_at.astimezone(timezone).date().isoformat(),
                    error,
                )
                return {
                    "scheduled_at": scheduled_at.isoformat(),
                    "completed": False,
                    "terminal": terminal_skip,
                    "intraday_pool_unavailable": True,
                    "skip_reason": "intraday_plan_unavailable" if terminal_skip else None,
                    "error": str(error),
                }

        if operation in {
            "instrument_cards",
            "capture_intraday_equities",
            "maintain_intraday_equities",
        }:
            prepare_symbols = getattr(runtime.history_admission, "prepare_symbols", None)
            if callable(prepare_symbols):
                prepare_symbols(task.symbols)

        result = runtime.collect_task(task, scheduled_at)
        if publisher is not None and not publisher.observe(task, scheduled_at, result):
            result["completed"] = False
        return result

    return collect_and_publish
