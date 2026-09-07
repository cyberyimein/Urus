from __future__ import annotations

from datetime import UTC, datetime, timedelta
import fcntl
import json
from pathlib import Path
from time import sleep
from typing import IO, Callable


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class CollectionCoordinator:
    """Host-wide mutex plus restart-safe independent rate-class pacing.

    The lock is held for the lifetime of a collection run. State is persisted
    while the lock is held, so a process restart cannot reset provider pacing.
    """

    def __init__(
        self,
        lock_path: str | Path,
        *,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        self.path = Path(lock_path).expanduser()
        self._sleeper = sleeper
        self._handle: IO[str] | None = None
        self._state: dict[str, str] = {}

    @property
    def locked(self) -> bool:
        return self._handle is not None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0)
            try:
                payload = json.loads(handle.read() or "{}")
            except json.JSONDecodeError:
                payload = {}
            timestamps = payload.get("next_allowed_at", {}) if isinstance(payload, dict) else {}
            self._state = {
                str(key): str(value)
                for key, value in timestamps.items()
                if isinstance(key, str) and isinstance(value, str)
            }
            self._handle = handle
        except Exception:
            handle.close()
            raise

    def acquire_rate_slot(
        self,
        rate_class: str,
        interval_seconds: float,
        *,
        now: datetime | None = None,
        sleeper: Callable[[float], None] | None = None,
        minimum_wait_seconds: float = 0.0,
    ) -> float:
        self.acquire()
        current = _as_utc(now or _utc_now())
        next_allowed = self._parse(self._state.get(rate_class)) or current
        wait_seconds = max(
            0.0,
            (next_allowed - current).total_seconds(),
            float(minimum_wait_seconds),
        )
        reserved_at = max(current, next_allowed) + timedelta(seconds=max(0.01, interval_seconds))
        self._state[rate_class] = reserved_at.isoformat()
        self._persist()
        if wait_seconds:
            (sleeper or self._sleeper)(wait_seconds)
        return wait_seconds

    def close(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            self._persist()
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._handle = None

    def __enter__(self) -> "CollectionCoordinator":
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _persist(self) -> None:
        if self._handle is None:
            return
        self._handle.seek(0)
        self._handle.truncate()
        json.dump({"next_allowed_at": self._state}, self._handle, sort_keys=True)
        self._handle.flush()

    @staticmethod
    def _parse(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return _as_utc(datetime.fromisoformat(value))
        except ValueError:
            return None


# The longer name keeps migration call sites readable.
MoomooCollectionCoordinator = CollectionCoordinator
