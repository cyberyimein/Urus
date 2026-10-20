from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Sequence


SCHEMA_VERSION = 5


class CollectorStore:
    """Private raw store and stable read boundary for downstream consumers."""

    def __init__(
        self,
        path: str | Path,
        *,
        market_calendar: str = "XNYS",
        market_timezone: str = "America/New_York",
        session_dates_provider: Callable[[str, int], Sequence[str]] | None = None,
    ) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self._migrate()
        from urus_moomoo.storage.equity_history import EquityHistoryStore

        self.equity_history = EquityHistoryStore(
            self.connection,
            self.path,
            market_calendar=market_calendar,
            market_timezone=market_timezone,
            session_dates_provider=session_dates_provider,
        )

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
            CREATE TABLE IF NOT EXISTS intraday_collection_plans (
                trading_date TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL
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

    def get_meta(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM collector_meta WHERE key = ?",
            (str(key),),
        ).fetchone()
        return str(row["value"]) if row is not None else None

    def set_meta(self, key: str, value: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO collector_meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(key), str(value)),
            )

    def delete_meta(self, key: str) -> None:
        with self.connection:
            self.connection.execute(
                "DELETE FROM collector_meta WHERE key = ?",
                (str(key),),
            )

    def get_intraday_collection_plan(self, trading_date: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT plan_json FROM intraday_collection_plans WHERE trading_date = ?",
            (str(trading_date),),
        ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(str(row["plan_json"]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"frozen intraday collection plan is invalid for {trading_date}"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError(
                f"frozen intraday collection plan is invalid for {trading_date}"
            )
        return payload

    def freeze_intraday_collection_plan(
        self, trading_date: str, plan: dict[str, Any]
    ) -> dict[str, Any]:
        """Persist the first accepted pool plan for a market date permanently."""

        _validate_intraday_collection_plan(trading_date, plan)
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO intraday_collection_plans "
                "(trading_date, plan_id, plan_json, recorded_at) VALUES (?, ?, ?, ?)",
                (
                    trading_date,
                    str(plan["plan_id"]),
                    json.dumps(plan, ensure_ascii=False, sort_keys=True),
                    datetime.now(UTC).isoformat(),
                ),
            )
        stored = self.get_intraday_collection_plan(trading_date)
        if stored is None:
            raise RuntimeError(
                f"failed to freeze intraday collection plan for {trading_date}"
            )
        return stored

    def write_raw(
        self,
        capture_id: str,
        kind: str,
        payload: dict[str, Any],
        *,
        schema_version: str,
        equity_data_refs: list[dict[str, Any]] | None = None,
        capture_trading_date: str | None = None,
    ) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        references = equity_data_refs or []
        trading_date = capture_trading_date or _capture_trading_date(payload)
        with self.connection:
            existing = self.connection.execute(
                "SELECT payload_json FROM raw_captures WHERE capture_id = ?",
                (capture_id,),
            ).fetchone()
            if references and existing is not None and str(existing["payload_json"]) != encoded:
                raise ValueError("equity raw capture id already exists with different content")
            self.connection.execute(
                "INSERT OR IGNORE INTO raw_captures VALUES (?, ?, ?, ?, ?)",
                (capture_id, schema_version, kind, datetime.now(UTC).isoformat(), encoded),
            )
            if references:
                if not trading_date:
                    raise ValueError("equity reference capture requires a trading date")
                self.equity_history.register_capture_refs(
                    capture_id,
                    trading_date,
                    references,
                    commit=False,
                )

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
        equity_data_refs: list[dict[str, Any]] | None = None,
        capture_trading_date: str | None = None,
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
        references = equity_data_refs or []
        trading_date = capture_trading_date or _capture_trading_date(payload)
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
            if references:
                if not trading_date:
                    raise ValueError("equity reference capture requires a trading date")
                self.equity_history.register_capture_refs(
                    capture_id,
                    trading_date,
                    references,
                    commit=False,
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

    def read_scheduled_raw(
        self,
        task_name: str,
        scheduled_at: datetime,
    ) -> dict[str, Any] | None:
        """Read the raw contract currently recorded for one scheduled slot."""

        row = self.connection.execute(
            """
            SELECT scheduled.capture_id, scheduled.kind, scheduled.completed,
                   raw.schema_version, raw.payload_json
            FROM scheduled_captures AS scheduled
            JOIN raw_captures AS raw ON raw.capture_id = scheduled.capture_id
            WHERE scheduled.task_name = ? AND scheduled.scheduled_at = ?
            """,
            (task_name, scheduled_at.isoformat()),
        ).fetchone()
        if row is None:
            return None

        contract = json.loads(str(row["payload_json"]))
        if not isinstance(contract, dict):
            raise RuntimeError("scheduled raw capture contract is not a JSON object")
        return {
            "capture_id": str(row["capture_id"]),
            "kind": str(row["kind"]),
            "completed": bool(row["completed"]),
            "schema_version": str(row["schema_version"]),
            "contract": contract,
        }

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "CollectorStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _capture_trading_date(payload: dict[str, Any]) -> str | None:
    data = payload.get("data")
    if isinstance(data, dict):
        value = data.get("trading_date")
        if isinstance(value, str) and value.strip():
            return value.strip()[:10]
    return None


def _validate_intraday_collection_plan(
    trading_date: str, plan: dict[str, Any]
) -> None:
    if not isinstance(plan, dict):
        raise ValueError("intraday collection plan must be an object")
    if str(plan.get("schema_version") or "") != "urus.intraday.collection_plan.v1":
        raise ValueError("unsupported intraday collection plan schema")
    if str(plan.get("status") or "") != "configured":
        raise ValueError("intraday collection plan is unavailable")
    if str(plan.get("trading_date") or "") != trading_date:
        raise ValueError("intraday collection plan trading_date mismatch")
    try:
        datetime.strptime(trading_date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("intraday collection plan trading_date is invalid") from exc
    plan_id = str(plan.get("plan_id") or "")
    if len(plan_id) != 64 or any(character not in "0123456789abcdef" for character in plan_id):
        raise ValueError("intraday collection plan id must be a lowercase SHA-256")

    member_symbols = _plan_symbols(plan.get("member_symbols"), "member_symbols")
    quote_symbols = _plan_symbols(plan.get("quote_symbols"), "quote_symbols")
    minute_symbols = _plan_symbols(plan.get("minute_symbols"), "minute_symbols")
    benchmarks = {"SPY", "QQQ", "IWM"}
    if not benchmarks.issubset(member_symbols):
        raise ValueError("intraday collection plan is missing a fixed benchmark")
    if not set(member_symbols).issubset(quote_symbols):
        raise ValueError("intraday collection plan quote_symbols omit members")
    if not set(member_symbols).issubset(minute_symbols):
        raise ValueError("intraday collection plan minute_symbols omit members")
    display_groups = plan.get("display_group_by_symbol")
    if not isinstance(display_groups, dict) or set(display_groups) != set(member_symbols):
        raise ValueError("intraday collection plan display groups do not match members")
    if any(not str(display_groups[symbol]).strip() for symbol in member_symbols):
        raise ValueError("intraday collection plan contains an empty display group")
    canonical = json.dumps(
        {key: value for key, value in plan.items() if key != "plan_id"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    expected_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if plan_id != expected_id:
        raise ValueError("intraday collection plan content does not match plan_id")


def _plan_symbols(value: object, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"intraday collection plan {field} must be a list")
    symbols: list[str] = []
    for raw in value:
        symbol = str(raw or "").strip().upper()
        if not symbol or symbol in symbols:
            raise ValueError(f"intraday collection plan {field} has invalid symbols")
        symbols.append(symbol)
    if not symbols:
        raise ValueError(f"intraday collection plan {field} must not be empty")
    return symbols
