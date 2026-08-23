from __future__ import annotations

from datetime import date, datetime
from math import isfinite
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.decision_harness.contracts import compact_bar, content_sha256, normalise_symbol
from app.models.daily_evidence import (
    DailyBarModel,
    DailyDecisionDatasetModel,
    DailyIndicatorSnapshotModel,
    DecisionChartProjectionModel,
)
from app.models.instruments import InstrumentDailyBarModel, InstrumentSnapshotModel


class DailyEvidenceRepository:
    """Persistence boundary for canonical bars and immutable daily evidence."""

    def __init__(self, session: Session):
        self.session = session

    def upsert_bars(
        self,
        bars: Iterable[dict[str, Any]],
        *,
        source: str,
        exchange: str = "XNYS",
        market_timezone: str = "America/New_York",
        adjustment: str = "QFQ",
        asset_type: str = "equity",
        collected_at: datetime | None = None,
    ) -> list[DailyBarModel]:
        collected_at = collected_at or utc_now()
        default_adjustment = adjustment
        result: list[DailyBarModel] = []
        for raw in bars:
            row = self._normalise_bar(raw)
            symbol = normalise_symbol(str(raw.get("symbol") or ""))
            row_adjustment = str(raw.get("adjustment") or default_adjustment).strip().upper()
            source_revision = content_sha256(
                {**row, "date": row["date"].isoformat(), "adjustment": row_adjustment}
            )
            statement = select(DailyBarModel).where(
                DailyBarModel.symbol == symbol,
                DailyBarModel.exchange == exchange,
                DailyBarModel.bar_date == row["date"],
                DailyBarModel.adjustment == row_adjustment,
                DailyBarModel.source == source,
                DailyBarModel.source_revision == source_revision,
            )
            existing = self.session.scalar(statement)
            if existing is not None:
                result.append(existing)
                continue
            model = DailyBarModel(
                id=str(uuid4()),
                symbol=symbol,
                exchange=exchange,
                asset_type=asset_type,
                bar_date=row["date"],
                market_timezone=market_timezone,
                adjustment=row_adjustment,
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
                turnover=row["turnover"],
                turnover_rate=row["turnover_rate"],
                source=source,
                source_revision=source_revision,
                collected_at=collected_at,
                quality_status="ok",
                content_sha256=source_revision,
            )
            self.session.add(model)
            result.append(model)
        self.session.flush()
        return result

    @staticmethod
    def _normalise_bar(raw: dict[str, Any]) -> dict[str, Any]:
        try:
            bar_date = date.fromisoformat(str(raw["date"]))
            open_price = float(raw["open"])
            high = float(raw["high"])
            low = float(raw["low"])
            close = float(raw["close"])
            volume = float(raw.get("volume") or 0)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"日 K 字段无效：{raw}") from exc
        numeric_values = (open_price, high, low, close, volume)
        if not all(isfinite(value) for value in numeric_values):
            raise ValueError(f"日 K 价格或成交量必须是有限数字：{raw}")
        if min(open_price, high, low, close) < 0 or volume < 0:
            raise ValueError(f"日 K 价格或成交量不能为负数：{raw}")
        if high < max(open_price, close) or low > min(open_price, close):
            raise ValueError(f"日 K OHLC 关系非法：{raw}")
        return {
            "date": bar_date,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "turnover": float(raw["turnover"]) if raw.get("turnover") is not None else None,
            "turnover_rate": (
                float(raw["turnover_rate"]) if raw.get("turnover_rate") is not None else None
            ),
        }

    def sync_legacy_snapshot_bars(
        self,
        persistence_payload: dict[str, Any],
        *,
        source: str = "moomoo_opend_history",
        market_timezone: str = "America/New_York",
        collected_at: datetime | None = None,
    ) -> int:
        """Copy a 3A persistence payload without duplicating the history on reads."""

        rows: list[dict[str, Any]] = []
        for item in persistence_payload.get("symbols", []):
            if not isinstance(item, dict):
                continue
            symbol = item.get("symbol")
            for bar in (item.get("history") or {}).get("bars", []):
                if not isinstance(bar, dict):
                    continue
                rows.append({"symbol": symbol, **bar})
        if not rows:
            return 0
        models = self.upsert_bars(
            rows,
            source=source,
            market_timezone=market_timezone,
            collected_at=collected_at,
        )
        return len(models)

    def bars(
        self,
        symbols: Iterable[str],
        *,
        through_date: date | None = None,
        source: str | None = None,
        adjustment: str = "QFQ",
    ) -> dict[str, list[DailyBarModel]]:
        normalized = list(dict.fromkeys(normalise_symbol(item) for item in symbols))
        if not normalized:
            return {}
        filters = [DailyBarModel.symbol.in_(normalized)]
        if through_date is not None:
            filters.append(DailyBarModel.bar_date <= through_date)
        if source is not None:
            filters.append(DailyBarModel.source == source)
        filters.append(DailyBarModel.adjustment == adjustment.strip().upper())
        statement = select(DailyBarModel).where(and_(*filters)).order_by(
            DailyBarModel.symbol.asc(), DailyBarModel.bar_date.asc(), DailyBarModel.collected_at.desc()
        )
        grouped: dict[str, list[DailyBarModel]] = {symbol: [] for symbol in normalized}
        seen: set[tuple[str, date, str]] = set()
        for row in self.session.scalars(statement):
            # The newest collected source wins for a logical bar.  A changed
            # source revision remains stored, so frozen datasets can retain
            # their content hash while current reads use the latest revision.
            key = (row.symbol, row.bar_date, row.adjustment)
            if key in seen:
                continue
            seen.add(key)
            grouped.setdefault(row.symbol, []).append(row)
        return grouped

    def sync_legacy_for_symbols(
        self,
        symbols: Iterable[str],
        *,
        through_date: date | None = None,
        market_timezone: str = "America/New_York",
    ) -> int:
        """Backfill canonical rows from the newest existing 3A snapshot per symbol."""

        normalized = list(dict.fromkeys(normalise_symbol(item) for item in symbols))
        if not normalized:
            return 0
        statement = (
            select(InstrumentSnapshotModel)
            .where(InstrumentSnapshotModel.symbol.in_(normalized))
            .order_by(InstrumentSnapshotModel.symbol.asc(), InstrumentSnapshotModel.captured_at.desc())
        )
        latest: dict[str, InstrumentSnapshotModel] = {}
        for snapshot in self.session.scalars(statement):
            latest.setdefault(snapshot.symbol, snapshot)
        rows: list[dict[str, Any]] = []
        for symbol, snapshot in latest.items():
            bars_statement = select(InstrumentDailyBarModel).where(
                InstrumentDailyBarModel.instrument_snapshot_id == snapshot.id
            ).order_by(InstrumentDailyBarModel.bar_date.asc())
            for bar in self.session.scalars(bars_statement):
                if through_date is not None and bar.bar_date > through_date:
                    continue
                rows.append({"symbol": symbol, **compact_bar(bar)})
        if not rows:
            return 0
        return len(
            self.upsert_bars(
                rows,
                source="legacy_instrument_snapshot",
                market_timezone=market_timezone,
                collected_at=utc_now(),
            )
        )

    def indicator(
        self,
        *,
        symbol: str,
        bar_date: date,
        feature_version: str,
        input_bar_hash: str,
    ) -> DailyIndicatorSnapshotModel | None:
        return self.session.scalar(
            select(DailyIndicatorSnapshotModel).where(
                DailyIndicatorSnapshotModel.symbol == normalise_symbol(symbol),
                DailyIndicatorSnapshotModel.bar_date == bar_date,
                DailyIndicatorSnapshotModel.feature_version == feature_version,
                DailyIndicatorSnapshotModel.input_bar_hash == input_bar_hash,
            )
        )

    def save_indicator(
        self,
        *,
        symbol: str,
        bar_date: date,
        feature_version: str,
        input_bar_hash: str,
        payload: dict[str, Any],
        quality: dict[str, Any],
        calculated_at: datetime | None = None,
    ) -> DailyIndicatorSnapshotModel:
        existing = self.indicator(
            symbol=symbol,
            bar_date=bar_date,
            feature_version=feature_version,
            input_bar_hash=input_bar_hash,
        )
        if existing is not None:
            return existing
        model = DailyIndicatorSnapshotModel(
            id=str(uuid4()),
            symbol=normalise_symbol(symbol),
            exchange="XNYS",
            bar_date=bar_date,
            adjustment="QFQ",
            feature_version=feature_version,
            input_bar_hash=input_bar_hash,
            payload_json=payload,
            quality_json=quality,
            calculated_at=calculated_at or utc_now(),
        )
        self.session.add(model)
        self.session.flush()
        return model

    def dataset_by_hash(self, digest: str) -> DailyDecisionDatasetModel | None:
        return self.session.scalar(
            select(DailyDecisionDatasetModel).where(DailyDecisionDatasetModel.content_sha256 == digest)
        )

    def save_dataset(self, payload: dict[str, Any], *, digest: str, created_at: datetime) -> DailyDecisionDatasetModel:
        existing = self.dataset_by_hash(digest)
        if existing is not None:
            return existing
        scope = dict(payload["scope"])
        model = DailyDecisionDatasetModel(
            id=str(payload["dataset_id"]),
            schema_version=str(payload["schema_version"]),
            scope_type=str(scope["scope_type"]),
            scope_id=str(scope["scope_id"]),
            scope_version=scope.get("scope_version"),
            trading_date=date.fromisoformat(str(payload["trading_date"])),
            cutoff_time=datetime.fromisoformat(str(payload["cutoff_time"]).replace("Z", "+00:00")),
            market_timezone=str(payload["market_timezone"]),
            bar_completion_policy=str(payload["bar_completion_policy"]),
            status=str(payload["status"]),
            scope_json=scope,
            bar_manifest_json=list(payload.get("bar_manifest") or []),
            indicator_snapshot_ids=list(payload.get("indicator_snapshot_ids") or []),
            group_snapshot_ids=list(payload.get("group_snapshot_ids") or []),
            quality_json=dict(payload.get("quality") or {}),
            payload_json=payload,
            content_sha256=digest,
            created_at=created_at,
        )
        self.session.add(model)
        self.session.flush()
        return model

    def dataset(self, dataset_id: str) -> DailyDecisionDatasetModel | None:
        return self.session.get(DailyDecisionDatasetModel, dataset_id)

    def chart(self, dataset_id: str) -> DecisionChartProjectionModel | None:
        return self.session.scalar(
            select(DecisionChartProjectionModel).where(DecisionChartProjectionModel.dataset_id == dataset_id)
        )

    def save_chart(
        self,
        *,
        dataset_id: str,
        scope_type: str,
        scope_id: str,
        payload: dict[str, Any],
        digest: str,
        created_at: datetime,
    ) -> DecisionChartProjectionModel:
        existing = self.chart(dataset_id)
        if existing is not None:
            return existing
        model = DecisionChartProjectionModel(
            id=str(uuid4()),
            dataset_id=dataset_id,
            schema_version=str(payload["schema_version"]),
            scope_type=scope_type,
            scope_id=scope_id,
            payload_json=payload,
            content_sha256=digest,
            created_at=created_at,
        )
        self.session.add(model)
        self.session.flush()
        return model
