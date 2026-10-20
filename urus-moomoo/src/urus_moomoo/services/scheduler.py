from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, time as datetime_time, timedelta
import logging
import sqlite3
import time
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from urus_moomoo.config import CollectionTask
from urus_moomoo.services.calendar import MoomooTradingCalendar
from urus_moomoo.services.intraday_equities import normalize_equity_shard_symbols
from urus_moomoo.storage.sqlite import CollectorStore


logger = logging.getLogger(__name__)


_INTRADAY_SCHEDULE_PARAMS = {
    "interval_seconds",
    "start_margin_seconds",
    "end_margin_seconds",
    "anchor_times",
    "fast_band_percent",
    "structure_band_percent",
    "equity_shards_enabled",
    "equity_shard_symbols",
    "equity_shard_budget_seconds",
}
_EQUITY_SHARD_OFFSETS = (("G1", 60), ("G2", 120), ("G3", 180))
_EQUITY_SHARD_WINDOW_SECONDS = 60
_EQUITY_SHARD_DEFAULT_BUDGET_SECONDS = 45.0


def _schedule_time(value: str) -> tuple[int, int] | None:
    text = value.strip()
    if len(text) == 5 and text[2] == ":" and all(part.isdigit() for part in text.split(":")):
        hour, minute = (int(part) for part in text.split(":"))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    return None


def _as_timezone(value: Any, timezone: ZoneInfo) -> datetime:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if not isinstance(value, datetime):
        raise TypeError(f"calendar returned a non-datetime session time: {value!r}")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone)
    return value.astimezone(timezone)


def _datetime_param(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class DueTask:
    task: CollectionTask
    scheduled_at: datetime


class CollectorScheduler:
    """A backend-independent scheduler driven solely by local task config."""

    def __init__(
        self,
        tasks: Iterable[CollectionTask],
        *,
        timezone: str,
        calendar: MoomooTradingCalendar,
        runner: Callable[[CollectionTask, datetime], Any],
        clock: Callable[[], datetime] | None = None,
        store: CollectorStore | None = None,
        catch_up_seconds: float = 3600.0,
        intraday_catch_up_seconds: float = 600.0,
        market_timezone: str | None = None,
        on_idle: Callable[[], None] | None = None,
        on_quiet: Callable[[], None] | None = None,
        quiet_budget_seconds: float = 45.0,
    ) -> None:
        self.tasks = tuple(tasks)
        self.timezone = ZoneInfo(timezone)
        self.market_timezone = ZoneInfo(market_timezone or timezone)
        self.calendar = calendar
        self.runner = runner
        self.clock = clock or (lambda: datetime.now(self.timezone))
        self.store = store
        self.catch_up = timedelta(seconds=max(0.0, catch_up_seconds))
        self.intraday_catch_up = timedelta(seconds=max(0.0, intraday_catch_up_seconds))
        self.on_idle = on_idle
        self.on_quiet = on_quiet
        self.quiet_budget_seconds = quiet_budget_seconds
        self.next_scheduled_at: datetime | None = None
        self.completed: set[tuple[str, str]] = set()

    def due(self, now: datetime | None = None) -> list[DueTask]:
        current = (now or self.clock()).astimezone(self.timezone)
        result: list[DueTask] = []
        self.next_scheduled_at = None
        market_date = current.astimezone(self.market_timezone).date()
        start_date = (current - self.catch_up).astimezone(self.market_timezone).date()
        day = start_date
        while day <= market_date:
            if self.calendar.is_session(day):
                for task in self.tasks:
                    if not task.enabled:
                        continue
                    task_catch_up = (
                        self.intraday_catch_up
                        if task.schedule.strip().lower() == "intraday"
                        else self.catch_up
                    )
                    for scheduled_at, scheduled_task in self._task_times(task, day):
                        if scheduled_at is not None and scheduled_at > current:
                            if self.next_scheduled_at is None or scheduled_at < self.next_scheduled_at:
                                self.next_scheduled_at = scheduled_at
                        if scheduled_at is None or not (
                            self._is_due_at(current, scheduled_at, scheduled_task, task_catch_up)
                        ):
                            continue
                        if self.store and self.store.scheduler_completed(
                            scheduled_task.name, scheduled_at
                        ):
                            continue
                        if (
                            self.store
                            and task.operation.strip() == "capture_eod_options"
                            and self.store.recover_eod_scheduler_completion(
                                task.name,
                                scheduled_at,
                                trading_date=day.isoformat(),
                                requested_symbols=task.symbols,
                            )
                        ):
                            logger.info(
                                "recovered completed EOD task from raw capture: task=%s scheduled_at=%s",
                                task.name,
                                scheduled_at.isoformat(),
                            )
                            continue
                        if (scheduled_task.name, scheduled_at.isoformat()) in self.completed:
                            continue
                        result.append(DueTask(scheduled_task, scheduled_at))
            day += timedelta(days=1)
        return result

    def run_once(self, now: datetime | None = None) -> list[str]:
        ran: list[str] = []
        try:
            due_tasks = self.due(now)
        finally:
            # Calendar/provider lookups can use the same host-wide coordinator
            # as collection tasks.  They must not retain that lock while the
            # scheduler sleeps, otherwise recovery and replay processes block.
            if self.on_idle is not None:
                self.on_idle()
        # Eoflora keeps its existing anchor and runtime behavior. Finish all
        # due Eoflora attempts first so an incomplete retry can make every
        # equity shard in this tick record a priority skip. Other tasks retain
        # their configured order.
        ordered_due = [
            *[due for due in due_tasks if due.task.operation.strip() == "capture_intraday"],
            *[due for due in due_tasks if due.task.operation.strip() != "capture_intraday"],
        ]
        eoflora_outcomes: list[bool] = []
        for due in ordered_due:
            task = due.task
            params = dict(task.params or {})
            if params.get("windowed_equity_shard"):
                if any(not completed for completed in eoflora_outcomes):
                    params["skip_reason"] = "skipped_priority"
                    task = replace(task, params=params)
                else:
                    deadline = _datetime_param(params.get("request_budget_deadline_at"))
                    current = self.clock().astimezone(self.timezone)
                    if deadline is not None and current >= deadline:
                        params["skip_reason"] = "skipped_budget"
                        task = replace(task, params=params)
            try:
                result = self.runner(task, due.scheduled_at)
            except Exception:
                logger.exception(
                    "scheduled task failed: task=%s scheduled_at=%s",
                    task.name,
                    due.scheduled_at.isoformat(),
                )
                if due.task.operation.strip() == "capture_intraday":
                    eoflora_outcomes.append(False)
                continue
            task_completed = not (
                isinstance(result, Mapping) and result.get("completed") is False
            )
            terminal = bool(result.get("terminal")) if isinstance(result, Mapping) else False
            if not task_completed and not terminal:
                logger.warning(
                    "scheduled task incomplete: task=%s scheduled_at=%s",
                    task.name,
                    due.scheduled_at.isoformat(),
                )
                if due.task.operation.strip() == "capture_intraday":
                    eoflora_outcomes.append(False)
                continue
            if self.store:
                capture_id = None
                if isinstance(result, Mapping):
                    contract = result.get("contract")
                    if isinstance(contract, Mapping):
                        capture_id = str(contract.get("capture_id") or "") or None
                self.store.mark_scheduler_completed(
                    task.name,
                    due.scheduled_at,
                    capture_id=capture_id,
                )
            self.completed.add((task.name, due.scheduled_at.isoformat()))
            ran.append(task.name)
            if due.task.operation.strip() == "capture_intraday":
                eoflora_outcomes.append(task_completed)
        quiet_now = now or self.clock()
        spare_seconds = ((self.next_scheduled_at - quiet_now).total_seconds()
                         if self.next_scheduled_at else float("inf"))
        if not due_tasks and self.on_quiet is not None and spare_seconds > self.quiet_budget_seconds:
            self.on_quiet()
        return ran

    def _task_times(
        self, task: CollectionTask, session_date
    ) -> list[tuple[datetime | None, CollectionTask]]:
        params = dict(task.params or {})
        if task.schedule.strip().lower() == "intraday":
            opened = self.calendar.session_open(session_date)
            closed = self.calendar.session_close(session_date)
            if opened is None or closed is None:
                return []
            interval = max(1.0, float(params.get("interval_seconds", 300)))
            start_margin = max(0.0, float(params.get("start_margin_seconds", 300)))
            equity_capture = task.operation.strip() == "capture_intraday_equities"
            windowed_equities = equity_capture and bool(params.get("equity_shards_enabled"))
            end_margin = (
                0.0
                if windowed_equities
                else max(0.0, float(params.get("end_margin_seconds", 300)))
            )
            cursor = _as_timezone(opened, self.timezone) + timedelta(seconds=start_margin)
            final = _as_timezone(closed, self.timezone) - timedelta(seconds=end_margin)
            anchors = {str(value) for value in params.get("anchor_times", [])}
            fast_band = params.get("fast_band_percent")
            structure_band = params.get("structure_band_percent")
            result: list[tuple[datetime | None, CollectionTask]] = []
            slot_index = 0
            while cursor <= final:
                key = cursor.astimezone(self.market_timezone).strftime("%H:%M")
                tier = "structure" if key in anchors else "fast"
                capture_params = {
                    key: value
                    for key, value in params.items()
                    if key not in _INTRADAY_SCHEDULE_PARAMS
                }
                if equity_capture:
                    capture_params.pop("capture_tier", None)
                    capture_params.pop("band_percent", None)
                    capture_params["slot_index"] = slot_index
                    if windowed_equities:
                        shard_symbols = normalize_equity_shard_symbols(
                            params.get("equity_shard_symbols"),
                            allowed_symbols=task.symbols,
                        )
                        budget_seconds = max(
                            0.0,
                            float(
                                params.get(
                                    "equity_shard_budget_seconds",
                                    _EQUITY_SHARD_DEFAULT_BUDGET_SECONDS,
                                )
                            ),
                        )
                        for shard_id, offset_seconds in _EQUITY_SHARD_OFFSETS:
                            shard_start = cursor + timedelta(seconds=offset_seconds)
                            shard_params = {
                                **capture_params,
                                "windowed_equity_shard": True,
                                "source_task_name": task.name,
                                "shard_id": shard_id,
                                "shard_symbols": shard_symbols[shard_id],
                                "equity_shard_symbols": shard_symbols,
                                "slot_cutoff_at": cursor.isoformat(),
                                "shard_window_end_at": (
                                    shard_start
                                    + timedelta(seconds=_EQUITY_SHARD_WINDOW_SECONDS)
                                ).isoformat(),
                                "request_budget_deadline_at": (
                                    shard_start + timedelta(seconds=budget_seconds)
                                ).isoformat(),
                                "capture_quotes": shard_id == "G1",
                            }
                            result.append(
                                (
                                    shard_start,
                                    replace(
                                        task,
                                        name=f"{task.name}:{shard_id}",
                                        params=shard_params,
                                    ),
                                )
                            )

                        finalizer_params = {
                            **capture_params,
                            "equity_shard_finalizer": True,
                            "source_task_name": task.name,
                            "equity_shard_symbols": shard_symbols,
                            "slot_cutoff_at": cursor.isoformat(),
                        }
                        result.append(
                            (
                                cursor + timedelta(seconds=240),
                                replace(
                                    task,
                                    name=f"{task.name}:finalize",
                                    operation="finalize_intraday_equities",
                                    params=finalizer_params,
                                ),
                            )
                        )
                        cursor += timedelta(seconds=interval)
                        slot_index += 1
                        continue
                else:
                    capture_params["capture_tier"] = tier
                    if tier == "structure" and structure_band is not None:
                        capture_params["band_percent"] = structure_band
                    elif tier == "fast" and fast_band is not None:
                        capture_params["band_percent"] = fast_band
                result.append((cursor, replace(task, params=capture_params)))
                cursor += timedelta(seconds=interval)
                slot_index += 1
            return result
        scheduled = self._task_time(task, session_date)
        return [(scheduled, task)]

    @staticmethod
    def _is_windowed_shard(task: CollectionTask) -> bool:
        return bool(dict(task.params or {}).get("windowed_equity_shard"))

    @staticmethod
    def _is_due_at(
        current: datetime,
        scheduled_at: datetime,
        task: CollectionTask,
        catch_up: timedelta,
    ) -> bool:
        params = dict(task.params or {})
        is_windowed_slot = bool(
            params.get("windowed_equity_shard")
            or params.get("equity_shard_finalizer")
        )
        if is_windowed_slot:
            window_end = scheduled_at + timedelta(seconds=_EQUITY_SHARD_WINDOW_SECONDS)
            return scheduled_at <= current < window_end
        if task.schedule.strip().lower() == "pre-open":
            # Pre-open maintenance may catch up after its scheduled time, but it
            # must finish starting before the last 30 minutes of the session.
            maintenance_cutoff = scheduled_at + timedelta(
                seconds=max(0.0, task.delay_seconds) - 30 * 60
            )
            return (
                scheduled_at <= current < maintenance_cutoff
                and current - scheduled_at <= catch_up
            )
        return timedelta(0) <= current - scheduled_at <= catch_up

    def _task_time(self, task: CollectionTask, session_date) -> datetime | None:
        if task.schedule.strip().lower() == "pre-open":
            opened = self.calendar.session_open(session_date)
            return (
                _as_timezone(opened, self.timezone)
                - timedelta(seconds=max(0.0, task.delay_seconds))
                if opened is not None
                else None
            )
        if task.schedule.strip().lower() in {"after-close", "post-close"}:
            close = self.calendar.session_close(session_date)
            return (
                close.astimezone(self.timezone)
                + timedelta(seconds=task.delay_seconds)
                if close is not None
                else None
            )
        parsed = _schedule_time(task.schedule)
        return (
            datetime.combine(session_date, datetime_time(*parsed), tzinfo=self.timezone)
            if parsed
            else None
        )

    def run_forever(self, *, poll_seconds: float = 30.0, stop: Callable[[], bool] | None = None) -> None:
        failures = 0
        while not (stop and stop()):
            try:
                self.run_once()
                failures = 0
            except (OSError, RuntimeError, sqlite3.Error):
                failures += 1
                logger.exception("scheduler tick failed; retrying (consecutive=%s)", failures)
            delay = min(60.0, max(0.1, poll_seconds) * 2 ** min(failures, 6))
            time.sleep(delay)


class PostCloseScheduler(CollectorScheduler):
    pass


class IntradayOptionsScheduler(CollectorScheduler):
    pass
