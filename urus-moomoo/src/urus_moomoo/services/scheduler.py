from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time
import time
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

from urus_moomoo.config import CollectionTask
from urus_moomoo.services.calendar import MoomooTradingCalendar


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
    ) -> None:
        self.tasks = tuple(tasks)
        self.timezone = ZoneInfo(timezone)
        self.calendar = calendar
        self.runner = runner
        self.clock = clock or (lambda: datetime.now(self.timezone))
        self.completed: set[tuple[str, str]] = set()

    def due(self, now: datetime | None = None) -> list[DueTask]:
        current = (now or self.clock()).astimezone(self.timezone)
        if not self.calendar.is_session(current.date()):
            return []
        result: list[DueTask] = []
        for task in self.tasks:
            if not task.enabled:
                continue
            scheduled_at = self._task_time(task, current)
            if scheduled_at is None:
                continue
            key = (task.name, scheduled_at.isoformat())
            if scheduled_at <= current and key not in self.completed:
                result.append(DueTask(task, scheduled_at))
        return result

    def run_once(self, now: datetime | None = None) -> list[str]:
        ran: list[str] = []
        for due in self.due(now):
            self.runner(due.task, due.scheduled_at)
            self.completed.add((due.task.name, due.scheduled_at.isoformat()))
            ran.append(due.task.name)
        return ran

    def _task_time(self, task: CollectionTask, current: datetime) -> datetime | None:
        if task.schedule.strip().lower() in {"after-close", "post-close"}:
            close = self.calendar.session_close(current.date())
            return close.astimezone(self.timezone) if close is not None else None
        parsed = _schedule_time(task.schedule)
        return datetime.combine(current.date(), datetime_time(*parsed), tzinfo=self.timezone) if parsed else None

    def run_forever(self, *, poll_seconds: float = 30.0, stop: Callable[[], bool] | None = None) -> None:
        while not (stop and stop()):
            self.run_once()
            time.sleep(max(0.1, poll_seconds))


class PostCloseScheduler(CollectorScheduler):
    pass


class IntradayOptionsScheduler(CollectorScheduler):
    pass
