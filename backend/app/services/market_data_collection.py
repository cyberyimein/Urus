from __future__ import annotations

from datetime import UTC, datetime, timedelta
import fcntl
import json
from pathlib import Path
from time import sleep
from typing import IO, Callable

from app.core.config import Settings
from app.core.time import as_utc, utc_now


class MoomooCollectionCoordinator:
    """Serialize OpenD collection on one host and pace provider requests.

    Urus runs one personal collection pipeline at a time. A file lock is a
    smaller boundary for that deployment model than durable jobs, leases and
    database commits around every provider call. The same locked file stores
    only the next allowed timestamp for each rate class so a quick process
    restart cannot bypass a provider window.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        lock_path: str | Path | None = None,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        configured = lock_path or getattr(
            settings, "moomoo_collection_lock_path", "data/moomoo_collection.lock"
        )
        configured_path = Path(configured).expanduser()
        backend_root = Path(__file__).resolve().parents[2]
        self.path = (
            configured_path
            if configured_path.is_absolute()
            else backend_root / configured_path
        )
        self._sleeper = sleeper
        self._handle: IO[str] | None = None
        self._state: dict[str, str] = {}

    @property
    def locked(self) -> bool:
        return self._handle is not None

    def acquire_collection_lock(self) -> None:
        if self._handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0)
            raw = handle.read().strip()
            try:
                decoded = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                # A killed process may leave a partially written timestamp
                # payload. The OS lock is still authoritative; recover with an
                # empty pacing state instead of blocking all future collection.
                decoded = {}
            timestamps = decoded.get("next_allowed_at", {}) if isinstance(decoded, dict) else {}
            self._state = {
                str(key): str(value)
                for key, value in timestamps.items()
                if isinstance(key, str) and isinstance(value, str)
            }
        except Exception:
            handle.close()
            raise
        self._handle = handle

    def acquire_rate_slot(
        self,
        rate_class: str,
        interval_seconds: float,
        *,
        now: datetime | None = None,
        sleeper: Callable[[float], None] | None = None,
        minimum_wait_seconds: float = 0.0,
    ) -> float:
        self.acquire_collection_lock()
        current = as_utc(now or utc_now())
        next_allowed = self._parse_timestamp(self._state.get(rate_class)) or current
        wait_seconds = max(
            0.0,
            (next_allowed - current).total_seconds(),
            float(minimum_wait_seconds),
        )
        reserved_at = max(current, next_allowed) + timedelta(
            seconds=max(0.01, float(interval_seconds))
        )
        self._state[rate_class] = reserved_at.isoformat()
        self._persist_state()
        if wait_seconds > 0:
            (sleeper or self._sleeper)(wait_seconds)
        return wait_seconds

    def close(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            self._persist_state()
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._handle = None

    def __enter__(self) -> "MoomooCollectionCoordinator":
        self.acquire_collection_lock()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _persist_state(self) -> None:
        if self._handle is None:
            return
        self._handle.seek(0)
        self._handle.truncate()
        json.dump(
            {"next_allowed_at": self._state},
            self._handle,
            ensure_ascii=False,
            sort_keys=True,
        )
        self._handle.flush()

    @staticmethod
    def _parse_timestamp(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return as_utc(parsed)
