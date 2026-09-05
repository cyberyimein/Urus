"""Lazy, read-only health checks for the operational SQLite database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from typing import Any, Callable
from urllib.parse import quote

from sqlalchemy.engine import make_url


HEALTH_SCHEMA_VERSION = "urus.database_health.v1"
DEFAULT_INTERVAL_HOURS = 24.0
MAX_MESSAGE_LENGTH = 1000


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _short_message(value: object) -> str:
    message = " ".join(str(value or "").split())
    return message[:MAX_MESSAGE_LENGTH]


class DatabaseHealthMonitor:
    """Run one full SQLite check per interval and cache the result off-database.

    The result is deliberately stored in a small JSON sidecar instead of the
    database being inspected. A corrupt SQLite file therefore cannot prevent
    the UI from seeing the failure state.
    """

    def __init__(
        self,
        database_url: str,
        *,
        status_path: str | Path | None = None,
        interval_hours: float = DEFAULT_INTERVAL_HOURS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database_path = self._resolve_database_path(database_url)
        self.status_path = self._resolve_status_path(status_path)
        self.interval = timedelta(hours=max(1.0, float(interval_hours)))
        self._clock = clock or _utc_now
        self._lock = threading.Lock()

    @staticmethod
    def _resolve_database_path(database_url: str) -> Path | None:
        try:
            url = make_url(database_url)
        except Exception:
            return None
        if not url.drivername.startswith("sqlite") or url.database in {None, ":memory:"}:
            return None
        return Path(str(url.database)).expanduser().resolve()

    def _resolve_status_path(self, status_path: str | Path | None) -> Path | None:
        if status_path:
            return Path(status_path).expanduser().resolve()
        if self.database_path is None:
            return None
        return self.database_path.with_name(f"{self.database_path.stem}.health.json")

    def get(self) -> dict[str, Any]:
        """Return the cached result or perform the overdue read-only check."""

        with self._lock:
            now = self._clock().astimezone(UTC)
            cached = self._load()
            if cached is not None and self._is_fresh(cached, now):
                return cached
            result = self._inspect(now)
            self._save(result)
            return result

    def _is_fresh(self, snapshot: dict[str, Any], now: datetime) -> bool:
        checked_at = _parse_datetime(snapshot.get("checked_at"))
        if checked_at is None:
            return False
        return checked_at + self.interval > now

    def _load(self) -> dict[str, Any] | None:
        if self.status_path is None or not self.status_path.is_file():
            return None
        try:
            payload = json.loads(self.status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def _save(self, snapshot: dict[str, Any]) -> None:
        if self.status_path is None:
            return
        try:
            self.status_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.status_path.parent,
                prefix=f".{self.status_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                json.dump(snapshot, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
                temporary_path = Path(handle.name)
            os.replace(temporary_path, self.status_path)
        except OSError:
            # The API can still return the in-memory result when the sidecar
            # directory is unavailable or read-only.
            try:
                if "temporary_path" in locals():
                    temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _inspect(self, checked_at: datetime) -> dict[str, Any]:
        started = time.monotonic()
        result: dict[str, Any] = {
            "schema_version": HEALTH_SCHEMA_VERSION,
            "status": "error",
            "checked_at": checked_at.isoformat(),
            "next_check_at": (checked_at + self.interval).isoformat(),
            "integrity_check": None,
            "foreign_key_violations": None,
            "duration_ms": None,
            "database_size_bytes": None,
            "message": None,
        }
        if self.database_path is None:
            result["status"] = "unsupported"
            result["message"] = "当前数据库不是可执行完整性检查的文件型 SQLite。"
            result["duration_ms"] = int((time.monotonic() - started) * 1000)
            return result

        try:
            result["database_size_bytes"] = self.database_path.stat().st_size
            uri = f"file:{quote(str(self.database_path), safe='/')}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=5.0)
            try:
                connection.execute("PRAGMA busy_timeout=5000")
                integrity_rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
                integrity_error = next(
                    (item for item in integrity_rows if item.strip().lower() != "ok"),
                    None,
                )
                result["integrity_check"] = "ok" if integrity_error is None else _short_message(integrity_error)
                foreign_key_rows = list(connection.execute("PRAGMA foreign_key_check"))
                result["foreign_key_violations"] = len(foreign_key_rows)
                if integrity_error is None and not foreign_key_rows:
                    result["status"] = "ok"
                elif integrity_error is not None:
                    result["message"] = f"SQLite 完整性检查失败：{_short_message(integrity_error)}"
                else:
                    result["message"] = f"发现 {len(foreign_key_rows)} 条外键一致性问题。"
            finally:
                connection.close()
        except Exception as exc:  # sqlite errors must be visible to the UI
            result["message"] = _short_message(exc)
            result["integrity_check"] = result["integrity_check"] or "check_failed"
        result["duration_ms"] = int((time.monotonic() - started) * 1000)
        return result
