from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from urus_moomoo.config import CollectionTask


PUBLISH_KINDS = frozenset({"instrument_cards", "options_snapshot"})
EXPECTED_TASK_NAMES = {
    "instrument_cards": "market-post-close",
    "options_snapshot": "options-snapshot-post-close",
}


class ObservationPublisher:
    """Join exact close captures and submit their immutable locators to Urus."""

    def __init__(
        self,
        *,
        api_url: str,
        timeout_seconds: float,
        state_path: str | Path,
        market_timezone: str,
        capture_reader: Callable[[str], Mapping[str, Any] | None],
        capture_database_path: str | Path | None = None,
        post: Callable[[str, bytes, float], tuple[int, Mapping[str, Any]]] | None = None,
        plan_loader: Callable[[str], Mapping[str, Any]] | None = None,
        request_fields: Mapping[str, Any] | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        if not self.api_url:
            raise ValueError("publisher.api_url must not be empty")
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.market_timezone = ZoneInfo(market_timezone)
        self.capture_reader = capture_reader
        self.capture_database_path = (
            Path(capture_database_path).expanduser() if capture_database_path else None
        )
        self.post = post or _post_json
        self.plan_loader = plan_loader or self._fetch_plan
        self.request_fields = dict(request_fields or {})
        path = Path(state_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS observation_collection_plans (
                trading_date TEXT PRIMARY KEY,
                plan_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS observation_publish_captures (
                trading_date TEXT NOT NULL,
                kind TEXT NOT NULL,
                capture_id TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                PRIMARY KEY (trading_date, kind)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS observation_publish_runs (
                trading_date TEXT PRIMARY KEY,
                market_capture_id TEXT NOT NULL,
                options_capture_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                published_at TEXT NOT NULL
            )
            """
        )
        self.connection.commit()
        self.discover_completed_captures()

    @classmethod
    def from_config(
        cls,
        config: Any,
        *,
        capture_reader: Callable[[str], Mapping[str, Any] | None],
    ) -> "ObservationPublisher | None":
        settings = dict(config.publisher or {})
        api_url = str(settings.pop("api_url", "")).strip()
        if not api_url:
            return None
        timeout = float(settings.pop("timeout_seconds", 15.0))
        state_path = settings.pop(
            "state_path", Path(config.output_dir).expanduser() / "observation-publisher.sqlite"
        )
        allowed = {
            "group_ids",
            "universe_revision_id",
            "universe_freshness",
            "universe_source_url",
        }
        unknown = set(settings) - allowed
        if unknown:
            raise ValueError(f"unknown publisher settings: {', '.join(sorted(unknown))}")
        return cls(
            api_url=api_url,
            timeout_seconds=timeout,
            state_path=state_path,
            market_timezone=config.market_timezone,
            capture_reader=capture_reader,
            capture_database_path=(
                config.sqlite_path
                or Path(config.output_dir).expanduser() / "collector.sqlite"
            ),
            request_fields=settings,
        )

    def prepare_task(self, task: CollectionTask, scheduled_at: datetime) -> CollectionTask:
        """Freeze the collection plan before collecting either publishable capture."""

        kind = task.operation.strip()
        if kind not in PUBLISH_KINDS:
            return task
        trading_date = scheduled_at.astimezone(self.market_timezone).date().isoformat()
        plan = self._plan(trading_date)
        key = "market_symbols" if kind == "instrument_cards" else "options_symbols"
        symbols = tuple(str(item).strip().upper() for item in plan.get(key, []) if str(item).strip())
        if not symbols:
            raise RuntimeError(f"collection plan has no {key} for {trading_date}")
        return replace(task, symbols=tuple(dict.fromkeys(symbols)))

    def observe(
        self,
        task: CollectionTask,
        scheduled_at: datetime,
        result: Mapping[str, Any],
    ) -> bool:
        """Record one exact complete capture; publish when its date-pair is ready.

        Returns False only when the just-collected capture is incomplete. Network and
        response failures raise so the scheduler leaves that task retryable.
        """

        kind = task.operation.strip()
        if kind not in PUBLISH_KINDS:
            return True
        contract = result.get("contract")
        if not isinstance(contract, Mapping) or not _is_complete(contract, kind, task.name):
            return False
        capture_id = str(contract.get("capture_id") or "").strip()
        stored = self.capture_reader(capture_id) if capture_id else None
        if stored is None:
            return False
        stored_contract = _stored_contract(stored)
        if stored_contract != dict(contract):
            raise RuntimeError(f"stored capture does not match collected contract: {capture_id}")
        trading_date = scheduled_at.astimezone(self.market_timezone).date().isoformat()
        if str(stored_contract.get("trading_date") or "") != trading_date:
            raise RuntimeError(
                f"stored capture trading_date mismatch: {capture_id}"
            )
        digest = _verified_content_sha256(stored_contract)
        plan = self._plan(trading_date)
        plan_key = "market_symbols" if kind == "instrument_cards" else "options_symbols"
        required = {str(item).upper() for item in plan.get(plan_key, [])}
        requested = {str(item).upper() for item in stored_contract.get("requested_symbols", [])}
        if not required or not required <= requested:
            raise RuntimeError(f"capture does not cover collection plan {plan_key}: {capture_id}")
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO observation_publish_captures
                    (trading_date, kind, capture_id, content_sha256, recorded_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(trading_date, kind) DO UPDATE SET
                    capture_id=excluded.capture_id,
                    content_sha256=excluded.content_sha256,
                    recorded_at=excluded.recorded_at
                """,
                (trading_date, kind, capture_id, digest, datetime.now(UTC).isoformat()),
            )
        self.publish_if_ready(trading_date)
        return True

    def resume_if_ready(self, task: CollectionTask, scheduled_at: datetime) -> str | None:
        """Retry a previously recorded exact pair without collecting replacement data."""

        kind = task.operation.strip()
        if kind not in PUBLISH_KINDS:
            return None
        trading_date = scheduled_at.astimezone(self.market_timezone).date().isoformat()
        row = self.connection.execute(
            """
            SELECT capture_id FROM observation_publish_captures
            WHERE trading_date = ? AND kind = ?
            """,
            (trading_date, kind),
        ).fetchone()
        if row is None:
            return None
        run_id = self.publish_if_ready(trading_date)
        return str(row["capture_id"]) if run_id is not None else None

    def publish_if_ready(self, trading_date: str) -> str | None:
        rows = self.connection.execute(
            """
            SELECT kind, capture_id, content_sha256
            FROM observation_publish_captures
            WHERE trading_date = ? AND kind IN ('instrument_cards', 'options_snapshot')
            """,
            (trading_date,),
        ).fetchall()
        captures = {str(row["kind"]): row for row in rows}
        if set(captures) != PUBLISH_KINDS:
            return None
        market = captures["instrument_cards"]
        options = captures["options_snapshot"]
        existing = self.connection.execute(
            """
            SELECT run_id FROM observation_publish_runs
            WHERE trading_date = ? AND market_capture_id = ? AND options_capture_id = ?
            """,
            (trading_date, market["capture_id"], options["capture_id"]),
        ).fetchone()
        if existing is not None:
            return str(existing["run_id"])
        plan = self._plan(trading_date)
        body = {
            **self.request_fields,
            "trading_date": trading_date,
            "trigger_mode": "scheduled",
            "collection_plan_id": plan["plan_id"],
            "group_ids": plan["group_ids"],
            "group_version_ids": plan["group_version_ids"],
            "universe_revision_id": plan["universe_revision_id"],
            "universe_freshness": plan["universe_freshness"],
            "universe_source_url": plan.get("universe_source_url"),
            "source_captures": {
                "market_capture_id": market["capture_id"],
                "market_content_sha256": market["content_sha256"],
                "options_capture_id": options["capture_id"],
                "options_content_sha256": options["content_sha256"],
            },
        }
        encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        status_code, response = self.post(
            f"{self.api_url}/api/observation/runs", encoded, self.timeout_seconds
        )
        source = response.get("source_captures")
        response_market = source.get("market") if isinstance(source, Mapping) else None
        response_options = source.get("options") if isinstance(source, Mapping) else None
        status = str(response.get("status") or "")
        run_id = str(response.get("run_id") or "").strip()
        if (
            status_code != 201
            or status != "succeeded"
            or not run_id
            or not isinstance(response_market, Mapping)
            or not isinstance(response_options, Mapping)
            or response_market.get("capture_id") != market["capture_id"]
            or response_options.get("capture_id") != options["capture_id"]
        ):
            raise RuntimeError(
                f"Urus rejected observation publication: HTTP {status_code}, "
                f"status={status or 'missing'}, run_id={run_id or 'missing'}"
            )
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO observation_publish_runs VALUES (?, ?, ?, ?, ?)",
                (
                    trading_date,
                    market["capture_id"],
                    options["capture_id"],
                    run_id,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return run_id

    def retry_pending(self) -> list[str]:
        """Discover exact completed v2 slots and retry every unpublished ready pair."""

        self.discover_completed_captures()
        dates = [
            str(row["trading_date"])
            for row in self.connection.execute(
                """
                SELECT DISTINCT c.trading_date
                FROM observation_publish_captures c
                LEFT JOIN observation_publish_runs r ON r.trading_date = c.trading_date
                WHERE r.trading_date IS NULL
                ORDER BY c.trading_date
                """
            ).fetchall()
        ]
        published = []
        for trading_date in dates:
            run_id = self.publish_if_ready(trading_date)
            if run_id is not None:
                published.append(run_id)
        return published

    def discover_completed_captures(self) -> None:
        """Recover only fixed scheduler slots linked to immutable v2 captures."""

        path = self.capture_database_path
        if path is None or not path.is_file():
            return
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT s.task_name, s.capture_id, r.payload_json
                FROM scheduler_runs s
                JOIN raw_captures r ON r.capture_id = s.capture_id
                WHERE s.task_name IN ('market-post-close', 'options-snapshot-post-close')
                  AND r.schema_version = 'urus.moomoo.capture.v2'
                """
            ).fetchall()
        finally:
            connection.close()
        recovered: dict[tuple[str, str], tuple[str, str]] = {}
        for row in rows:
            contract = _stored_contract(row)
            kind = str(contract.get("kind") or "")
            task_name = str(row["task_name"])
            trading_date = str(contract.get("trading_date") or "")
            if (
                kind not in PUBLISH_KINDS
                or task_name != EXPECTED_TASK_NAMES[kind]
                or contract.get("task_name") != task_name
                or not trading_date
                or not _is_complete(contract, kind, task_name)
            ):
                continue
            digest = _verified_content_sha256(contract)
            key = (trading_date, kind)
            value = (str(row["capture_id"]), digest)
            if key in recovered and recovered[key] != value:
                raise RuntimeError(f"ambiguous completed captures for {trading_date} {kind}")
            recovered[key] = value
        with self.connection:
            for (trading_date, kind), (capture_id, digest) in recovered.items():
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO observation_publish_captures
                        (trading_date, kind, capture_id, content_sha256, recorded_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (trading_date, kind, capture_id, digest, datetime.now(UTC).isoformat()),
                )

    def _plan(self, trading_date: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT plan_json FROM observation_collection_plans WHERE trading_date = ?",
            (trading_date,),
        ).fetchone()
        if row is not None:
            return dict(json.loads(str(row["plan_json"])))
        plan = dict(self.plan_loader(trading_date))
        required = {
            "plan_id", "trading_date", "universe_revision_id", "universe_freshness",
            "group_ids", "group_version_ids", "market_symbols", "options_symbols",
        }
        if required - set(plan) or str(plan.get("trading_date")) != trading_date:
            raise RuntimeError(f"invalid collection plan for {trading_date}")
        with self.connection:
            self.connection.execute(
                "INSERT INTO observation_collection_plans VALUES (?, ?, ?)",
                (trading_date, json.dumps(plan, ensure_ascii=False, sort_keys=True), datetime.now(UTC).isoformat()),
            )
        return plan

    def _fetch_plan(self, trading_date: str) -> Mapping[str, Any]:
        sync_status, sync = self.post(
            f"{self.api_url}/api/observation/groups/sync", b"{}", self.timeout_seconds
        )
        revision = str(sync.get("universe_revision_id") or "")
        if sync_status != 200 or not revision:
            raise RuntimeError("Urus Universe sync failed before collection")
        query = urlencode({"trading_date": trading_date, "universe_revision_id": revision})
        status, plan = _get_json(
            f"{self.api_url}/api/observation/collection-plan?{query}", self.timeout_seconds
        )
        if status != 200:
            raise RuntimeError(f"Urus collection plan failed: HTTP {status}")
        return plan

    def close(self) -> None:
        self.connection.close()


def _stored_contract(row: Mapping[str, Any]) -> dict[str, Any]:
    try:
        payload = row["payload_json"]
    except (KeyError, IndexError):
        payload = row.get("payload_json")
    parsed = json.loads(str(payload))
    if not isinstance(parsed, dict):
        raise RuntimeError("stored capture payload is not an object")
    return parsed


def _content_sha256(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _verified_content_sha256(contract: Mapping[str, Any]) -> str:
    data = contract.get("data")
    if not isinstance(data, Mapping):
        raise RuntimeError("stored capture data is not an object")
    actual = _content_sha256(data)
    expected = str(contract.get("content_sha256") or "")
    if actual != expected:
        raise RuntimeError(
            f"stored capture content_sha256 mismatch: {contract.get('capture_id') or 'unknown'}"
        )
    return actual


def _is_complete(contract: Mapping[str, Any], kind: str, task_name: str) -> bool:
    if (
        contract.get("kind") != kind
        or task_name != EXPECTED_TASK_NAMES[kind]
        or contract.get("task_name") != task_name
        or not contract.get("capture_id")
    ):
        return False
    requested = {str(item).upper() for item in contract.get("requested_symbols", [])}
    data = contract.get("data")
    if not isinstance(data, Mapping) or not data.get("available"):
        return False
    if kind == "instrument_cards" and data.get("quality_status") != "ok":
        return False
    if data.get("unavailable_symbols"):
        return False
    key = "instruments" if kind == "instrument_cards" else "symbols"
    returned = {
        str(item.get("symbol") or "").upper()
        for item in data.get(key, [])
        if isinstance(item, Mapping)
    }
    return bool(requested) and requested <= returned


def _post_json(url: str, body: bytes, timeout_seconds: float) -> tuple[int, Mapping[str, Any]]:
    request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = int(response.status)
            payload = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"Urus observation publication failed: HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Urus observation publication failed: {exc}") from exc
    try:
        parsed = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Urus observation publication returned invalid JSON") from exc
    if not isinstance(parsed, Mapping):
        raise RuntimeError("Urus observation publication returned a non-object response")
    return status, parsed


def _get_json(url: str, timeout_seconds: float) -> tuple[int, Mapping[str, Any]]:
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = int(response.status)
            payload = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(
            f"Urus observation collection plan failed: HTTP {exc.code}: {detail}"
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Urus observation collection plan failed: {exc}") from exc
    try:
        parsed = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Urus observation collection plan returned invalid JSON") from exc
    if not isinstance(parsed, Mapping):
        raise RuntimeError("Urus observation collection plan returned a non-object response")
    return status, parsed
