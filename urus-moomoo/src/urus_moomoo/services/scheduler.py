from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, time as datetime_time, timedelta
import logging
import time
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from urus_moomoo.config import CollectionTask
from urus_moomoo.services.calendar import MoomooTradingCalendar
from urus_moomoo.storage.sqlite import CollectorStore


logger = logging.getLogger(__name__)


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
        market_timezone: str | None = None,
    ) -> None:
        self.tasks = tuple(tasks)
        self.timezone = ZoneInfo(timezone)
        self.market_timezone = ZoneInfo(market_timezone or timezone)
        self.calendar = calendar
        self.runner = runner
        self.clock = clock or (lambda: datetime.now(self.timezone))
        self.store = store
        self.catch_up = timedelta(seconds=max(0.0, catch_up_seconds))
        self.completed: set[tuple[str, str]] = set()

    def due(self, now: datetime | None = None) -> list[DueTask]:
        current = (now or self.clock()).astimezone(self.timezone)
        result: list[DueTask] = []
        market_date = current.astimezone(self.market_timezone).date()
        start_date = (current - self.catch_up).astimezone(self.market_timezone).date()
        day = start_date
        while day <= market_date:
            if self.calendar.is_session(day):
                for task in self.tasks:
                    if not task.enabled:
                        continue
                    for scheduled_at, scheduled_task in self._task_times(task, day):
                        if scheduled_at is None or not (
                            timedelta(0) <= current - scheduled_at <= self.catch_up
                        ):
                            continue
                        if self.store and self.store.scheduler_completed(task.name, scheduled_at):
                            continue
                        if (task.name, scheduled_at.isoformat()) in self.completed:
                            continue
                        result.append(DueTask(scheduled_task, scheduled_at))
            day += timedelta(days=1)
        return result

    def run_once(self, now: datetime | None = None) -> list[str]:
        ran: list[str] = []
        for due in self.due(now):
            try:
                result = self.runner(due.task, due.scheduled_at)
            except Exception:
                logger.exception(
                    "scheduled task failed: task=%s scheduled_at=%s",
                    due.task.name,
                    due.scheduled_at.isoformat(),
                )
                continue
            if isinstance(result, Mapping) and result.get("completed") is False:
                logger.warning(
                    "scheduled task incomplete: task=%s scheduled_at=%s",
                    due.task.name,
                    due.scheduled_at.isoformat(),
                )
                continue
            if self.store:
                self.store.mark_scheduler_completed(due.task.name, due.scheduled_at)
            self.completed.add((due.task.name, due.scheduled_at.isoformat()))
            ran.append(due.task.name)
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
            end_margin = max(0.0, float(params.get("end_margin_seconds", 300)))
            cursor = _as_timezone(opened, self.timezone) + timedelta(seconds=start_margin)
            final = _as_timezone(closed, self.timezone) - timedelta(seconds=end_margin)
            anchors = {str(value) for value in params.get("anchor_times", [])}
            fast_band = params.get("fast_band_percent")
            structure_band = params.get("structure_band_percent")
            result: list[tuple[datetime | None, CollectionTask]] = []
            while cursor <= final:
                key = cursor.astimezone(self.market_timezone).strftime("%H:%M")
                tier = "structure" if key in anchors else "fast"
                capture_params = dict(params)
                capture_params["capture_tier"] = tier
                if tier == "structure" and structure_band is not None:
                    capture_params["band_percent"] = structure_band
                elif tier == "fast" and fast_band is not None:
                    capture_params["band_percent"] = fast_band
                result.append((cursor, replace(task, params=capture_params)))
                cursor += timedelta(seconds=interval)
            return result
        scheduled = self._task_time(task, session_date)
        return [(scheduled, task)]

    def _task_time(self, task: CollectionTask, session_date) -> datetime | None:
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
        while not (stop and stop()):
            self.run_once()
            time.sleep(max(0.1, poll_seconds))


class PostCloseScheduler(CollectorScheduler):
    pass


class IntradayOptionsScheduler(CollectorScheduler):
    pass
