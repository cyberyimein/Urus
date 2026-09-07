from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from urus_moomoo.config import CollectionTask, CollectorConfig
from urus_moomoo.contracts import capture_contract
from urus_moomoo.services.pacing import CollectionCoordinator
from urus_moomoo.storage.sqlite import CollectorStore


class CollectorRuntime:
    """Small composition root: provider in, versioned raw output out."""

    def __init__(self, provider: Any, config: CollectorConfig) -> None:
        self.provider = provider
        output_dir = Path(config.output_dir).expanduser()
        self.store = CollectorStore(config.sqlite_path or output_dir / "collector.sqlite")
        self.coordinator = CollectionCoordinator(config.lock_path or output_dir / "collection.lock")
        if hasattr(provider, "_rate_limiter") and getattr(provider, "_rate_limiter") is None:
            provider._rate_limiter = self.coordinator

    def collect_task(self, task: CollectionTask, scheduled_at: datetime) -> dict[str, object]:
        with self.coordinator:
            payload = self.provider.instrument_cards(list(task.symbols))
            contract = capture_contract(kind=task.name, payload=payload, requested_symbols=list(task.symbols))
            self.store.write_raw(contract["capture_id"], task.name, contract, schema_version=str(contract["schema_version"]))
            for snapshot in payload.get("history_snapshots", []):
                if not isinstance(snapshot, dict):
                    continue
                history = snapshot.get("history")
                if isinstance(history, dict) and isinstance(history.get("bars"), list):
                    self.store.write_daily_bars([bar for bar in history["bars"] if isinstance(bar, dict)])
            return {"scheduled_at": scheduled_at.isoformat(), "contract": contract}

    def close(self) -> None:
        self.store.close()
