from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

from app.decision_harness.contracts import content_sha256
from app.schemas.observation import ObservationSourceCaptures


CAPTURE_SCHEMA_VERSION = "urus.moomoo.capture.v2"
CAPTURE_PROVIDER = "moomoo_openapi"


@dataclass(frozen=True)
class LoadedCapture:
    capture_id: str
    kind: str
    captured_at: datetime
    content_sha256: str
    requested_symbols: tuple[str, ...]
    data: dict[str, Any]
    task_name: str
    trading_date: date

    def provenance(self) -> dict[str, str]:
        return {
            "capture_id": self.capture_id,
            "kind": self.kind,
            "schema_version": CAPTURE_SCHEMA_VERSION,
            "provider": CAPTURE_PROVIDER,
            "captured_at": self.captured_at.isoformat(),
            "content_sha256": self.content_sha256,
            "task_name": self.task_name,
            "trading_date": self.trading_date.isoformat(),
        }


class SidecarCaptureStore:
    """Read immutable capture contracts from the sidecar's SQLite boundary."""

    def __init__(self, path: str | Path, *, market_timezone: str) -> None:
        self.path = Path(path).expanduser()
        self.market_timezone = ZoneInfo(market_timezone)

    def load_pair(
        self,
        locators: ObservationSourceCaptures,
        *,
        trading_date: date,
    ) -> tuple[LoadedCapture, LoadedCapture]:
        market = self.load(
            locators.market_capture_id,
            expected_kind="instrument_cards",
            expected_task_name="market-post-close",
            expected_sha256=locators.market_content_sha256,
            trading_date=trading_date,
        )
        options = self.load(
            locators.options_capture_id,
            expected_kind="options_snapshot",
            expected_task_name="options-snapshot-post-close",
            expected_sha256=locators.options_content_sha256,
            trading_date=trading_date,
        )
        return market, options

    def load(
        self,
        capture_id: str,
        *,
        expected_kind: str,
        expected_task_name: str,
        expected_sha256: str,
        trading_date: date,
    ) -> LoadedCapture:
        if not self.path.is_file():
            raise ValueError(f"Sidecar capture database is missing: {self.path}")
        try:
            connection = sqlite3.connect(
                f"file:{self.path}?mode=ro", uri=True, timeout=5.0
            )
            try:
                row = connection.execute(
                    "SELECT schema_version, kind, captured_at, payload_json "
                    "FROM raw_captures WHERE capture_id = ?",
                    (capture_id,),
                ).fetchone()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise ValueError(f"Cannot read sidecar capture database: {exc}") from exc
        if row is None:
            raise ValueError(f"Sidecar capture not found: {capture_id}")

        row_schema, row_kind, row_captured_at, raw_payload = row
        try:
            contract = json.loads(str(raw_payload))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Sidecar capture {capture_id} contains invalid JSON") from exc
        if not isinstance(contract, dict):
            raise ValueError(f"Sidecar capture {capture_id} must be a JSON object")

        checks = {
            "row schema_version": (str(row_schema), CAPTURE_SCHEMA_VERSION),
            "row kind": (str(row_kind), expected_kind),
            "contract schema_version": (
                str(contract.get("schema_version") or ""),
                CAPTURE_SCHEMA_VERSION,
            ),
            "contract capture_id": (str(contract.get("capture_id") or ""), capture_id),
            "contract kind": (str(contract.get("kind") or ""), expected_kind),
            "contract provider": (
                str(contract.get("provider") or ""),
                CAPTURE_PROVIDER,
            ),
            "contract task_name": (
                str(contract.get("task_name") or ""),
                expected_task_name,
            ),
        }
        for label, (actual, expected) in checks.items():
            if actual != expected:
                raise ValueError(
                    f"Sidecar capture {capture_id} {label} mismatch: "
                    f"expected {expected!r}, got {actual!r}"
                )

        data = contract.get("data")
        if not isinstance(data, dict):
            raise ValueError(f"Sidecar capture {capture_id} data must be a JSON object")
        digest = content_sha256(data)
        embedded_digest = str(contract.get("content_sha256") or "")
        if embedded_digest != digest or digest != expected_sha256:
            raise ValueError(f"Sidecar capture {capture_id} content_sha256 mismatch")
        contract_trading_date = str(contract.get("trading_date") or "")
        if contract_trading_date != trading_date.isoformat():
            raise ValueError(
                f"Sidecar capture {capture_id} does not belong to trading date "
                f"{trading_date.isoformat()}"
            )
        raw_timestamp = str(contract.get("captured_at") or row_captured_at or "")
        try:
            captured_at = datetime.fromisoformat(raw_timestamp)
        except ValueError as exc:
            raise ValueError(f"Sidecar capture {capture_id} has invalid captured_at") from exc
        if captured_at.tzinfo is None:
            raise ValueError(f"Sidecar capture {capture_id} captured_at must be timezone-aware")
        symbols = tuple(
            dict.fromkeys(
                str(item).strip().upper()
                for item in list(contract.get("requested_symbols") or [])
                if str(item).strip()
            )
        )
        if not symbols:
            raise ValueError(f"Sidecar capture {capture_id} has no requested_symbols")
        if data.get("available") is not True:
            raise ValueError(f"Sidecar capture {capture_id} is not available")
        if data.get("unavailable_symbols"):
            raise ValueError(f"Sidecar capture {capture_id} has unavailable symbols")
        rows_key = "instruments" if expected_kind == "instrument_cards" else "symbols"
        returned = {
            str(item.get("symbol") or "").strip().upper()
            for item in data.get(rows_key, [])
            if isinstance(item, dict) and str(item.get("symbol") or "").strip()
        }
        missing = sorted(set(symbols) - returned)
        if missing:
            raise ValueError(
                f"Sidecar capture {capture_id} does not contain symbols: {', '.join(missing)}"
            )
        if expected_kind == "instrument_cards" and data.get("quality_status") != "ok":
            raise ValueError(f"Sidecar capture {capture_id} has partial market quality")
        return LoadedCapture(
            capture_id=capture_id,
            kind=expected_kind,
            captured_at=captured_at,
            content_sha256=digest,
            requested_symbols=symbols,
            data=dict(data),
            task_name=str(contract.get("task_name") or ""),
            trading_date=trading_date,
        )


class SidecarMarketCaptureAdapter:
    def __init__(self, capture: LoadedCapture) -> None:
        self.capture = capture

    def assert_covers(self, symbols: list[str]) -> None:
        _assert_symbol_coverage(self.capture, symbols)

    def instrument_cards(self, symbols: list[str]) -> dict[str, Any]:
        self.assert_covers(symbols)
        return dict(self.capture.data)

    def close(self) -> None:
        return None


class SidecarOptionsCaptureAdapter:
    def __init__(self, capture: LoadedCapture) -> None:
        self.capture = capture

    def assert_covers(self, symbols: list[str]) -> None:
        _assert_symbol_coverage(self.capture, symbols)

    def options_snapshot(self, symbols: list[str] | None = None) -> dict[str, Any]:
        self.assert_covers(list(symbols or []))
        return dict(self.capture.data)

    def close(self) -> None:
        return None


def _assert_symbol_coverage(capture: LoadedCapture, symbols: list[str]) -> None:
    required = {
        str(item).strip().upper() for item in symbols if str(item).strip()
    }
    missing = sorted(required - set(capture.requested_symbols))
    if missing:
        raise ValueError(
            f"Sidecar {capture.kind} capture {capture.capture_id} does not cover symbols: "
            + ", ".join(missing)
        )
