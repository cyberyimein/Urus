from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any


DAILY_DATASET_SCHEMA = "urus.daily_decision_dataset.v1"
CHART_PROJECTION_SCHEMA = "urus.decision_chart_projection.v1"
FEATURE_VERSION = "technical_v4"
BAR_COMPLETION_POLICY = "official_exchange_close_only_v1"
SUPPORTED_SCOPE_TYPES = {"instrument", "group", "observation_run"}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalise_symbol(value: str) -> str:
    symbol = str(value).strip().upper()
    if not symbol or len(symbol) > 32:
        raise ValueError("symbol 不能为空且不能超过 32 个字符")
    return symbol


def iso_date(value: date | str) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)


def iso_datetime(value: datetime) -> str:
    return value.isoformat()


def hash_bars(bars: list[dict[str, Any]]) -> str:
    """Hash only the canonical OHLCV window, not collection timestamps."""

    canonical = [
        {
            "date": iso_date(item["date"]),
            "open": float(item["open"]),
            "high": float(item["high"]),
            "low": float(item["low"]),
            "close": float(item["close"]),
            "volume": float(item.get("volume") or 0),
            "turnover": item.get("turnover"),
            "turnover_rate": item.get("turnover_rate"),
            "adjustment": str(item.get("adjustment") or "QFQ"),
        }
        for item in bars
    ]
    return content_sha256(canonical)


def compact_bar(model: Any) -> dict[str, Any]:
    return {
        "date": model.bar_date.isoformat(),
        "open": model.open,
        "high": model.high,
        "low": model.low,
        "close": model.close,
        "volume": model.volume,
        "turnover": model.turnover,
        "turnover_rate": model.turnover_rate,
        "adjustment": getattr(model, "adjustment", "QFQ"),
    }
