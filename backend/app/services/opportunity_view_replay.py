"""Bounded, short-lived workers for the read-only opportunity view.

The daily chart projection can be large enough that loading it in the API
process and replaying it there leaves Python's allocator holding a large heap
after a request has finished.  This module keeps that work in a child process
which opens the database itself and exits after one request.  Only the compact
JSON view for the requested symbol crosses the process boundary.

The coordinator is deliberately a small single-flight runner instead of a
process pool.  A pool would keep the replay heap attached to long-lived
workers, while an unbounded submission queue would turn a burst of browser
requests into retained callables and promises.  At most ``max_workers``
children may run, and a completed request is removed from the in-flight map.
"""

from __future__ import annotations

import logging
import multiprocessing
import threading
import time
from collections.abc import Mapping
from concurrent.futures import Future, TimeoutError as FutureTimeout
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import Settings


logger = logging.getLogger(__name__)

DEFAULT_MAX_WORKERS = 2
DEFAULT_TIMEOUT_SECONDS = 180.0


class OpportunityReplayError(RuntimeError):
    """A worker could not produce a bounded opportunity view."""


class OpportunityReplayBusy(OpportunityReplayError):
    """All bounded replay slots are currently occupied."""


class OpportunityReplayTimeout(TimeoutError, OpportunityReplayError):
    """A worker exceeded its request budget and was terminated."""


class OpportunityReplayCancelled(OpportunityReplayError):
    """All callers left an in-flight replay, so its child was stopped."""


class _ReplayEntry:
    def __init__(self) -> None:
        self.future: Future[dict[str, Any]] = Future()
        self.waiters = 0
        self.cancel_requested = False


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="python"))
    return value


def _settings_for_worker(
    *,
    database_url: str,
    market_timezone: str,
    market_calendar: str,
    daily_min_history_bars: int,
) -> Settings:
    """Create a minimal settings object without inheriting mutable app state."""

    return Settings(
        app_env="opportunity-view-worker",
        database_url=database_url,
        market_timezone=market_timezone,
        market_calendar=market_calendar,
        daily_min_history_bars=daily_min_history_bars,
        observation_universe_source_url="",
    )


def _readonly_database(database_url: str):
    """Return a read-only SQLite engine, or the normal engine for other DBs.

    A child never writes through this engine.  SQLite's URI ``mode=ro`` makes
    that invariant enforceable and also prevents a missing path from silently
    creating a new empty database in a worker.
    """

    parsed = make_url(database_url)
    if not parsed.drivername.startswith("sqlite"):
        from app.core.database import create_database

        return create_database(database_url)
    database = parsed.database
    if not database or database == ":memory:":
        from app.core.database import create_database

        return create_database(database_url)
    # SQLAlchemy's SQLite URI form is ``sqlite:///file:/absolute/path?...``.
    # Resolve relative paths against the API process working directory, which
    # is inherited by the child and is the same base used by the API engine.
    path = Path(database).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    readonly_url = f"sqlite+pysqlite:///file:{path.as_posix()}?mode=ro&uri=true"
    engine = create_engine(
        readonly_url,
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
        pool_pre_ping=True,
    )
    return engine, sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _worker_entry(
    connection,
    *,
    database_url: str,
    dataset_id: str,
    symbol: str | None,
    viewed_at: str,
    market_timezone: str,
    market_calendar: str,
    daily_min_history_bars: int,
) -> None:
    """Read/replay one dataset in a child and send only JSON to the parent."""

    engine = None
    try:
        # Lazy imports keep the parent module cheap and make this function
        # safe under the ``spawn`` start method used on macOS.
        from app.core.time import as_utc
        from app.decision_harness.market_evidence import DailyMarketEvidenceService
        from app.services.opportunity_views import build_dataset_opportunity_view

        settings = _settings_for_worker(
            database_url=database_url,
            market_timezone=market_timezone,
            market_calendar=market_calendar,
            daily_min_history_bars=daily_min_history_bars,
        )
        engine, factory = _readonly_database(database_url)
        with factory() as session:
            daily = DailyMarketEvidenceService(session, settings)
            dataset = daily.get_dataset(dataset_id)
            if dataset is None:
                raise OpportunityReplayError("找不到 Daily Decision Dataset")
            chart = daily.get_replay_chart(dataset_id, symbol)
            payload = build_dataset_opportunity_view(
                dataset,
                chart,
                viewed_at=as_utc(datetime.fromisoformat(viewed_at)),
                market_timezone=market_timezone,
                symbol=symbol,
            )
        connection.send({"ok": True, "payload": _jsonable(payload)})
    except Exception as exc:  # pragma: no cover - exact provider errors vary
        logger.warning("opportunity view worker failed: %s", exc)
        try:
            connection.send(
                {
                    "ok": False,
                    "error": str(exc)[:500] or exc.__class__.__name__,
                }
            )
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        if engine is not None:
            engine.dispose()
        try:
            connection.close()
        except (OSError, BrokenPipeError):
            pass


class OpportunityReplayCoordinator:
    """Run bounded one-shot replay workers with single-flight deduplication."""

    def __init__(
        self,
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.max_workers = int(max_workers)
        self.timeout_seconds = float(timeout_seconds)
        self._slots = threading.BoundedSemaphore(self.max_workers)
        self._lock = threading.Lock()
        self._inflight: dict[tuple[str, ...], _ReplayEntry] = {}

    @property
    def inflight_count(self) -> int:
        with self._lock:
            return len(self._inflight)

    def run(
        self,
        *,
        key: tuple[str, ...],
        database_url: str,
        dataset_id: str,
        symbol: str | None,
        viewed_at: datetime,
        market_timezone: str,
        market_calendar: str,
        daily_min_history_bars: int,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Return a result, sharing an in-flight request for the same key.

        The key intentionally excludes the precise ``viewed_at`` seconds.  A
        caller refreshes the returned timestamp in the API service, while the
        key still includes the local viewing date so freshness cannot be
        carried across a trading-date boundary.
        """

        owner = False
        with self._lock:
            entry = self._inflight.get(key)
            if entry is None:
                if not self._slots.acquire(blocking=False):
                    raise OpportunityReplayBusy("机会视图重放繁忙，请稍后重试")
                entry = _ReplayEntry()
                self._inflight[key] = entry
                owner = True
            else:
                # A new caller arriving before the cancellation manager has
                # stopped the child means the work is still useful.
                entry.cancel_requested = False
            entry.waiters += 1
        if owner:
            thread = threading.Thread(
                target=self._execute,
                kwargs={
                    "key": key,
                    "entry": entry,
                    "database_url": database_url,
                    "dataset_id": dataset_id,
                    "symbol": symbol,
                    "viewed_at": viewed_at,
                    "market_timezone": market_timezone,
                    "market_calendar": market_calendar,
                    "daily_min_history_bars": daily_min_history_bars,
                },
                name="opportunity-view-replay",
                daemon=True,
            )
            try:
                thread.start()
            except Exception as exc:
                with self._lock:
                    self._inflight.pop(key, None)
                    entry.waiters -= 1
                self._slots.release()
                entry.future.set_exception(exc)
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise OpportunityReplayCancelled("机会视图请求已断开")
                try:
                    return entry.future.result(timeout=0.1)
                except FutureTimeout:
                    continue
        finally:
            with self._lock:
                current = self._inflight.get(key)
                if current is entry:
                    entry.waiters = max(0, entry.waiters - 1)
                    if entry.waiters == 0 and not entry.future.done():
                        entry.cancel_requested = True

    def _execute(self, *, key: tuple[str, ...], entry: _ReplayEntry, **kwargs: Any) -> None:
        try:
            result = self._run_process(
                should_cancel=lambda: self._cancel_requested(key, entry),
                **kwargs,
            )
        except Exception as exc:
            if not entry.future.done():
                entry.future.set_exception(exc)
        else:
            if not entry.future.done():
                entry.future.set_result(result)
        finally:
            with self._lock:
                if self._inflight.get(key) is entry:
                    self._inflight.pop(key, None)
            self._slots.release()

    def _cancel_requested(self, key: tuple[str, ...], entry: _ReplayEntry) -> bool:
        with self._lock:
            return (
                self._inflight.get(key) is entry
                and entry.cancel_requested
                and entry.waiters == 0
            )

    def _run_process(
        self,
        *,
        database_url: str,
        dataset_id: str,
        symbol: str | None,
        viewed_at: datetime,
        market_timezone: str,
        market_calendar: str,
        daily_min_history_bars: int,
        should_cancel=None,
    ) -> dict[str, Any]:
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=False)
        process = context.Process(
            target=_worker_entry,
            kwargs={
                "connection": child,
                "database_url": database_url,
                "dataset_id": dataset_id,
                "symbol": symbol,
                "viewed_at": viewed_at.isoformat(),
                "market_timezone": market_timezone,
                "market_calendar": market_calendar,
                "daily_min_history_bars": daily_min_history_bars,
            },
            name="urus-opportunity-view-worker",
        )
        started = time.monotonic()
        try:
            process.start()
        except Exception:
            try:
                child.close()
            finally:
                parent.close()
                process.close()
            raise
        child.close()
        message: dict[str, Any] | None = None
        try:
            while True:
                if parent.poll(0.05):
                    message = parent.recv()
                    break
                if not process.is_alive():
                    break
                if should_cancel is not None and should_cancel():
                    raise OpportunityReplayCancelled("机会视图请求已断开")
                if time.monotonic() - started >= self.timeout_seconds:
                    raise OpportunityReplayTimeout("机会视图重放超时")
            process.join(timeout=2.0)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5.0)
            if message is None:
                raise OpportunityReplayError(
                    f"机会视图 worker 异常退出（exitcode={process.exitcode}）"
                )
            if not message.get("ok"):
                raise OpportunityReplayError(str(message.get("error") or "worker failed"))
            payload = message.get("payload")
            if not isinstance(payload, dict):
                raise OpportunityReplayError("worker 返回的机会视图格式无效")
            return payload
        except OpportunityReplayTimeout:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5.0)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=5.0)
            raise
        except OpportunityReplayCancelled:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5.0)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=5.0)
            raise
        finally:
            try:
                parent.close()
            except (OSError, BrokenPipeError):
                pass
            if process.is_alive():
                process.terminate()
                process.join(timeout=5.0)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=5.0)
            # A failed spawn can leave a child handle without a useful exit
            # code; close it here so repeated browser refreshes do not retain
            # process resources in the API process.
            try:
                process.close()
            except (ValueError, OSError):
                pass


_DEFAULT_COORDINATOR = OpportunityReplayCoordinator()


def default_opportunity_replay_coordinator() -> OpportunityReplayCoordinator:
    return _DEFAULT_COORDINATOR


__all__ = [
    "DEFAULT_MAX_WORKERS",
    "DEFAULT_TIMEOUT_SECONDS",
    "OpportunityReplayBusy",
    "OpportunityReplayCancelled",
    "OpportunityReplayCoordinator",
    "OpportunityReplayError",
    "OpportunityReplayTimeout",
    "default_opportunity_replay_coordinator",
]
