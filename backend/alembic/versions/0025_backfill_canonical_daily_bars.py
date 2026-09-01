"""Backfill canonical daily bars from legacy instrument snapshots.

Revision ID: 0025_backfill_canonical_daily_bars
Revises: 0024_remote_decision_workflows
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision = "0025_backfill_canonical_daily_bars"
down_revision = "0024_remote_decision_workflows"
branch_labels = None
depends_on = None

LEGACY_SOURCE = "legacy_instrument_snapshot"
DEFAULT_EXCHANGE = "XNYS"
DEFAULT_MARKET_TIMEZONE = "America/New_York"


def _as_date(value: object) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value))


def _as_utc(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value).strip()
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _source_revision(
    *,
    bar_date: date,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    turnover: float | None,
    turnover_rate: float | None,
    adjustment: str,
) -> str:
    payload = {
        "date": bar_date.isoformat(),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "turnover": turnover,
        "turnover_rate": turnover_rate,
        "adjustment": adjustment,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    required = {"daily_bars", "instrument_snapshots", "instrument_daily_bars"}
    if not required.issubset(tables):
        # Local databases created only from the current ORM do not carry the
        # removed tables. The schema migration must remain a safe no-op there.
        return

    metadata = sa.MetaData()
    snapshots = sa.Table("instrument_snapshots", metadata, autoload_with=bind)
    legacy_bars = sa.Table("instrument_daily_bars", metadata, autoload_with=bind)
    canonical_bars = sa.Table("daily_bars", metadata, autoload_with=bind)

    snapshot_rows = bind.execute(
        sa.select(
            snapshots.c.id,
            snapshots.c.symbol,
            snapshots.c.asset_type,
            snapshots.c.captured_at,
        ).order_by(
            snapshots.c.symbol.asc(),
            snapshots.c.captured_at.desc(),
            snapshots.c.id.desc(),
        )
    ).mappings()
    latest_by_symbol: dict[str, dict[str, object]] = {}
    for row in snapshot_rows:
        symbol = str(row["symbol"] or "").strip().upper()
        if symbol:
            latest_by_symbol.setdefault(symbol, dict(row))
    if not latest_by_symbol:
        return

    snapshot_by_id = {str(row["id"]): row for row in latest_by_symbol.values()}
    bar_rows = bind.execute(
        sa.select(legacy_bars).where(
            legacy_bars.c.instrument_snapshot_id.in_(snapshot_by_id.keys())
        ).order_by(legacy_bars.c.bar_date.asc(), legacy_bars.c.id.asc())
    ).mappings()

    existing = bind.execute(
        sa.select(
            canonical_bars.c.symbol,
            canonical_bars.c.exchange,
            canonical_bars.c.bar_date,
            canonical_bars.c.adjustment,
            canonical_bars.c.source,
            canonical_bars.c.source_revision,
        ).where(canonical_bars.c.source == LEGACY_SOURCE)
    ).mappings()
    existing_keys = {
        (
            str(row["symbol"]),
            str(row["exchange"]),
            row["bar_date"],
            str(row["adjustment"]),
            str(row["source"]),
            str(row["source_revision"]),
        )
        for row in existing
    }

    values: list[dict[str, object]] = []
    for raw in bar_rows:
        snapshot = snapshot_by_id.get(str(raw["instrument_snapshot_id"]))
        if snapshot is None:
            continue
        symbol = str(snapshot["symbol"] or "").strip().upper()
        if not symbol:
            continue
        bar_date = _as_date(raw["bar_date"])
        adjustment = str(raw["adjustment"] or "QFQ").strip().upper() or "QFQ"
        open_price = float(raw["open"])
        high = float(raw["high"])
        low = float(raw["low"])
        close = float(raw["close"])
        volume = float(raw["volume"])
        turnover = float(raw["turnover"]) if raw["turnover"] is not None else None
        turnover_rate = (
            float(raw["turnover_rate"]) if raw["turnover_rate"] is not None else None
        )
        source_revision = _source_revision(
            bar_date=bar_date,
            open_price=open_price,
            high=high,
            low=low,
            close=close,
            volume=volume,
            turnover=turnover,
            turnover_rate=turnover_rate,
            adjustment=adjustment,
        )
        key = (
            symbol,
            DEFAULT_EXCHANGE,
            bar_date,
            adjustment,
            LEGACY_SOURCE,
            source_revision,
        )
        if key in existing_keys:
            continue
        existing_keys.add(key)
        values.append(
            {
                "id": str(uuid4()),
                "symbol": symbol,
                "exchange": DEFAULT_EXCHANGE,
                "asset_type": str(snapshot["asset_type"] or "equity"),
                "bar_date": bar_date,
                "market_timezone": DEFAULT_MARKET_TIMEZONE,
                "adjustment": adjustment,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "turnover": turnover,
                "turnover_rate": turnover_rate,
                "source": LEGACY_SOURCE,
                "source_revision": source_revision,
                "collected_at": _as_utc(snapshot["captured_at"]),
                "corrected_at": None,
                "quality_status": "ok",
                "content_sha256": source_revision,
            }
        )

    if values:
        bind.execute(canonical_bars.insert(), values)


def downgrade() -> None:
    # The backfill is additive and intentionally is not reversed; removing
    # canonical rows would discard data collected after the migration.
    pass
