from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any


SCHEMA_VERSION = 3


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
            CREATE TABLE IF NOT EXISTS scheduler_runs (
                task_name TEXT NOT NULL, scheduled_at TEXT NOT NULL, completed_at TEXT NOT NULL,
                capture_id TEXT,
                PRIMARY KEY(task_name, scheduled_at)
            );
            CREATE TABLE IF NOT EXISTS scheduled_captures (
                task_name TEXT NOT NULL, scheduled_at TEXT NOT NULL,
                capture_id TEXT NOT NULL, kind TEXT NOT NULL,
                completed INTEGER NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(task_name, scheduled_at),
                FOREIGN KEY(capture_id) REFERENCES raw_captures(capture_id)
            );
            """
        )
        scheduler_columns = {
            str(row["name"])
            for row in self.connection.execute("PRAGMA table_info(scheduler_runs)").fetchall()
        }
        if "capture_id" not in scheduler_columns:
            self.connection.execute("ALTER TABLE scheduler_runs ADD COLUMN capture_id TEXT")
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_scheduler_runs_capture_id ON scheduler_runs(capture_id)"
        )
        self.connection.execute("INSERT OR REPLACE INTO collector_meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
        self.connection.commit()

    def write_raw(self, capture_id: str, kind: str, payload: dict[str, Any], *, schema_version: str) -> None:
        self.connection.execute("INSERT OR IGNORE INTO raw_captures VALUES (?, ?, ?, ?, ?)", (capture_id, schema_version, kind, datetime.now(UTC).isoformat(), json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)))
        self.connection.commit()

    def write_scheduled_raw(
        self,
        task_name: str,
        scheduled_at: datetime,
        capture_id: str,
        kind: str,
        payload: dict[str, Any],
        *,
        schema_version: str,
        completed: bool,
    ) -> bool:
        """Persist one bounded scheduled capture and atomically record completion.

        The first incomplete payload is retained for diagnosis. Further incomplete
        retries do not repeatedly serialize another large raw package; a completed
        retry appends an immutable raw capture, advances the scheduled-task pointer,
        and records scheduler completion in the same transaction.
        """

        scheduled_text = scheduled_at.isoformat()
        existing = self.connection.execute(
            "SELECT completed FROM scheduled_captures WHERE task_name = ? AND scheduled_at = ?",
            (task_name, scheduled_text),
        ).fetchone()
        if existing is not None and (bool(existing["completed"]) or not completed):
            return False

        now = datetime.now(UTC).isoformat()
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO raw_captures VALUES (?, ?, ?, ?, ?)",
                (capture_id, schema_version, kind, now, encoded),
            )
            self.connection.execute(
                """
                INSERT INTO scheduled_captures
                    (task_name, scheduled_at, capture_id, kind, completed, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_name, scheduled_at) DO UPDATE SET
                    capture_id = excluded.capture_id,
                    kind = excluded.kind,
                    completed = excluded.completed,
                    updated_at = excluded.updated_at
                """,
                (task_name, scheduled_text, capture_id, kind, int(completed), now),
            )
            if completed:
                self.connection.execute(
                    """
                    INSERT INTO scheduler_runs
                        (task_name, scheduled_at, completed_at, capture_id)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(task_name, scheduled_at) DO UPDATE SET
                        completed_at = excluded.completed_at,
                        capture_id = excluded.capture_id
                    """,
                    (task_name, scheduled_text, now, capture_id),
                )
        return True

    def recover_eod_scheduler_completion(
        self,
        task_name: str,
        scheduled_at: datetime,
        *,
        trading_date: str,
        requested_symbols: tuple[str, ...],
    ) -> bool:
        """Register a pre-v3 complete EOD raw package after an interrupted run."""

        if self.scheduler_completed(task_name, scheduled_at):
            return True
        rows = self.connection.execute(
            """
            SELECT capture_id, json_extract(payload_json, '$.requested_symbols') AS symbols
            FROM raw_captures
            WHERE kind = 'capture_eod_options'
              AND json_extract(payload_json, '$.data.trading_date') = ?
              AND json_extract(payload_json, '$.data.complete') = 1
            ORDER BY captured_at DESC
            """,
            (trading_date,),
        ).fetchall()
        expected_symbols = {
            str(symbol).strip().upper() for symbol in requested_symbols if str(symbol).strip()
        }
        row = next(
            (
                candidate
                for candidate in rows
                if {
                    str(symbol).strip().upper()
                    for symbol in json.loads(str(candidate["symbols"]) or "[]")
                    if str(symbol).strip()
                }
                == expected_symbols
            ),
            None,
        )
        if row is None:
            return False
        self.mark_scheduler_completed(task_name, scheduled_at, capture_id=str(row["capture_id"]))
        return True

    def write_daily_bars(self, bars: list[dict[str, Any]], *, symbol: str | None = None) -> None:
        for bar in bars:
            row_symbol = str(bar.get("symbol") or symbol or "").upper()
            bar_date = str(bar.get("date", bar.get("bar_date", bar.get("time_key", ""))))[:10]
            if not row_symbol or not bar_date:
                continue
            self.connection.execute("INSERT OR REPLACE INTO daily_bars VALUES (?, ?, ?)", (row_symbol, bar_date, json.dumps(bar, ensure_ascii=False, sort_keys=True, default=str)))
        self.connection.commit()

    def scheduler_completed(self, task_name: str, scheduled_at: datetime) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM scheduler_runs WHERE task_name = ? AND scheduled_at = ?",
            (task_name, scheduled_at.isoformat()),
        ).fetchone()
        return row is not None

    def mark_scheduler_completed(
        self,
        task_name: str,
        scheduled_at: datetime,
        *,
        capture_id: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO scheduler_runs (task_name, scheduled_at, completed_at, capture_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(task_name, scheduled_at) DO UPDATE SET
                capture_id = COALESCE(scheduler_runs.capture_id, excluded.capture_id)
            """,
            (
                task_name,
                scheduled_at.isoformat(),
                datetime.now(UTC).isoformat(),
                capture_id,
            ),
        )
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
