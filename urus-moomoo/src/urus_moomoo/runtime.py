from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from urus_moomoo.config import CollectionTask, CollectorConfig
from urus_moomoo.contracts import capture_contract
from urus_moomoo.services.pacing import CollectionCoordinator
from urus_moomoo.services.quota import HistoryAdmission, HistoryCapacityService
from urus_moomoo.storage.sqlite import CollectorStore


class CollectorRuntime:
    """Small composition root: provider in, versioned raw output out."""

    def __init__(
        self,
        provider: Any,
        config: CollectorConfig,
        *,
        provider_factory: Callable[[CollectionCoordinator, HistoryAdmission], Any] | None = None,
    ) -> None:
        self.config = config
        output_dir = Path(config.output_dir).expanduser()
        self.store = CollectorStore(config.sqlite_path or output_dir / "collector.sqlite")
        self.coordinator = CollectionCoordinator(config.lock_path or output_dir / "collection.lock")
        capacity = HistoryCapacityService(settings=config, store=self.store)
        self.provider = provider
        admission = HistoryAdmission(capacity, quota_reader=self._quota_snapshot)
        self.history_admission = admission
        if provider_factory is not None:
            self.provider = provider_factory(self.coordinator, admission)
        history_symbols = [
            symbol
            for task in config.tasks
            if task.enabled and task.operation == "instrument_cards"
            for symbol in task.symbols
        ]
        admission.prepare_symbols(history_symbols)

    @classmethod
    def from_factory(
        cls,
        config: CollectorConfig,
        factory: Callable[[CollectionCoordinator, HistoryAdmission], Any],
    ) -> "CollectorRuntime":
        return cls(None, config, provider_factory=factory)

    def collect_task(self, task: CollectionTask, scheduled_at: datetime) -> dict[str, object]:
        with self.coordinator:
            payload = self._collect(task, scheduled_at)
            contract = capture_contract(
                kind=task.operation,
                payload=payload,
                requested_symbols=list(task.symbols),
            )
            self.store.write_raw(
                contract["capture_id"],
                task.operation,
                contract,
                schema_version=str(contract["schema_version"]),
            )
            snapshots = payload.get("history_snapshots", payload.get("instruments", []))
            for snapshot in snapshots if isinstance(snapshots, list) else []:
                if not isinstance(snapshot, dict):
                    continue
                history = snapshot.get("history")
                if isinstance(history, dict) and isinstance(history.get("bars"), list):
                    self.store.write_daily_bars(
                        [bar for bar in history["bars"] if isinstance(bar, dict)],
                        symbol=str(snapshot.get("symbol") or ""),
                    )
            return {"scheduled_at": scheduled_at.isoformat(), "contract": contract}

    def _collect(self, task: CollectionTask, scheduled_at: datetime) -> dict[str, object]:
        operation = task.operation.strip()
        params = dict(task.params or {})
        if operation in {"instrument_cards", "options_snapshot"}:
            result = getattr(self.provider, operation)(list(task.symbols), **params)
        elif operation == "capital_flow_day":
            trading_date = scheduled_at.astimezone(ZoneInfo(self.config.market_timezone)).date()
            result = {
                "trading_date": trading_date.isoformat(),
                "observations": [
                    self.provider.capital_flow_day(symbol, trading_date) for symbol in task.symbols
                ],
            }
        elif operation == "capture_intraday":
            result = self.provider.capture_intraday(symbols=list(task.symbols), **params)
        elif operation == "capture_session_close":
            trading_date = scheduled_at.astimezone(
                ZoneInfo(self.config.market_timezone)
            ).date()
            result = self.provider.capture_session_close(
                symbols=list(task.symbols), trading_date=trading_date, **params
            )
        else:
            raise ValueError(f"unsupported collection operation: {operation}")
        if not isinstance(result, dict):
            raise TypeError(f"collection operation {operation} must return a mapping")
        return result

    def _quota_snapshot(self) -> dict[str, object]:
        reader = getattr(self.provider, "quota_snapshot", None)
        if not callable(reader):
            return {"available": False, "warning": "quota reader unavailable"}
        result = reader()
        return result if isinstance(result, dict) else {"available": False}

    def close(self) -> None:
        try:
            close = getattr(self.provider, "close", None)
            if callable(close):
                close()
        finally:
            self.coordinator.close()
            self.store.close()
