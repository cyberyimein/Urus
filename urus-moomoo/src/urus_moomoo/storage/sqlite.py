from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any


SCHEMA_VERSION = 1


class CollectorStore:
    """Private raw store and stable read boundary for downstream consumers."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS collector_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS raw_captures (
                capture_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL,
                kind TEXT NOT NULL, captured_at TEXT NOT NULL, payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS daily_bars (
                symbol TEXT NOT NULL, bar_date TEXT NOT NULL, payload_json TEXT NOT NULL,
                PRIMARY KEY(symbol, bar_date)
            );
            CREATE TABLE IF NOT EXISTS option_captures (
                capture_id TEXT PRIMARY KEY, captured_at TEXT NOT NULL, payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS quota_snapshots (
                snapshot_id TEXT PRIMARY KEY, captured_at TEXT NOT NULL, payload_json TEXT NOT NULL
            );
            """
        )
        self.connection.execute("INSERT OR REPLACE INTO collector_meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
        self.connection.commit()

    def write_raw(self, capture_id: str, kind: str, payload: dict[str, Any], *, schema_version: str) -> None:
        self.connection.execute("INSERT OR REPLACE INTO raw_captures VALUES (?, ?, ?, ?, ?)", (capture_id, schema_version, kind, datetime.now(UTC).isoformat(), json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)))
        self.connection.commit()

    def write_daily_bars(self, bars: list[dict[str, Any]]) -> None:
        for bar in bars:
            symbol, bar_date = str(bar["symbol"]).upper(), str(bar.get("date", bar.get("bar_date")))[:10]
            self.connection.execute("INSERT OR REPLACE INTO daily_bars VALUES (?, ?, ?)", (symbol, bar_date, json.dumps(bar, ensure_ascii=False, sort_keys=True, default=str)))
        self.connection.commit()

    def write_quota_snapshot(self, snapshot_id: str, payload: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO quota_snapshots VALUES (?, ?, ?)",
            (snapshot_id, str(payload.get("captured_at") or datetime.now(UTC).isoformat()), json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)),
        )
        self.connection.commit()

    def latest_quota_snapshot(self) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM quota_snapshots ORDER BY captured_at DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def read_raw(self, capture_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM raw_captures WHERE capture_id = ?", (capture_id,)).fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "CollectorStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
