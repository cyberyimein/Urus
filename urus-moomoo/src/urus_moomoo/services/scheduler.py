from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as datetime_time, timedelta
import time
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

from urus_moomoo.config import CollectionTask
from urus_moomoo.services.calendar import MoomooTradingCalendar
from urus_moomoo.storage.sqlite import CollectorStore


def _schedule_time(value: str) -> tuple[int, int] | None:
    text = value.strip()
    if len(text) == 5 and text[2] == ":" and all(part.isdigit() for part in text.split(":")):
        hour, minute = (int(part) for part in text.split(":"))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
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
        runner: Callable[[CollectionTask, datetime], None],
        clock: Callable[[], datetime] | None = None,
        store: CollectorStore | None = None,
        catch_up_seconds: float = 3600.0,
    ) -> None:
        self.tasks = tuple(tasks)
        self.timezone = ZoneInfo(timezone)
        self.calendar = calendar
        self.runner = runner
        self.clock = clock or (lambda: datetime.now(self.timezone))
        self.store = store
        self.catch_up = timedelta(seconds=max(0.0, catch_up_seconds))
        self.completed: set[tuple[str, str]] = set()

    def due(self, now: datetime | None = None) -> list[DueTask]:
        current = (now or self.clock()).astimezone(self.timezone)
        result: list[DueTask] = []
        start_date = (current - self.catch_up).date()
        day = start_date
        while day <= current.date():
            if self.calendar.is_session(day):
                for task in self.tasks:
                    if not task.enabled:
                        continue
                    scheduled_at = self._task_time(task, day)
                    if scheduled_at is None or not (
                        timedelta(0) <= current - scheduled_at <= self.catch_up
                    ):
                        continue
                    if self.store and self.store.scheduler_completed(task.name, scheduled_at):
                        continue
                    if (task.name, scheduled_at.isoformat()) in self.completed:
                        continue
                    result.append(DueTask(task, scheduled_at))
            day += timedelta(days=1)
        return result

    def run_once(self, now: datetime | None = None) -> list[str]:
        ran: list[str] = []
        for due in self.due(now):
            self.runner(due.task, due.scheduled_at)
            if self.store:
                self.store.mark_scheduler_completed(due.task.name, due.scheduled_at)
            self.completed.add((due.task.name, due.scheduled_at.isoformat()))
            ran.append(due.task.name)
        return ran

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
