from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from typing import Any
from uuid import uuid4


SCHEMA_VERSION = "urus.moomoo.capture.v2"
DAILY_SCHEMA_VERSION = "urus.moomoo.daily.v1"
OPTIONS_SCHEMA_VERSION = "urus.moomoo.options.v1"


def capture_contract(
    *,
    kind: str,
    payload: dict[str, Any],
    requested_symbols: list[str],
    task_name: str,
    scheduled_at: datetime,
    trading_date: str,
) -> dict[str, Any]:
    """Return the only payload shape consumers should read."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return {
        "schema_version": SCHEMA_VERSION,
        "capture_id": str(uuid4()),
        "kind": kind,
        "task_name": task_name,
        "scheduled_at": scheduled_at.isoformat(),
        "trading_date": trading_date,
        "content_sha256": hashlib.sha256(encoded).hexdigest(),
        "provider": "moomoo_openapi",
        "captured_at": datetime.now(UTC).isoformat(),
        "requested_symbols": list(dict.fromkeys(str(item).strip().upper() for item in requested_symbols if str(item).strip())),
        "data": payload,
    }


def daily_contract(payload: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": DAILY_SCHEMA_VERSION, "provider": "moomoo_openapi", "data": payload}


def options_contract(payload: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": OPTIONS_SCHEMA_VERSION, "provider": "moomoo_openapi", "data": payload}
