from __future__ import annotations

from datetime import date, datetime
from math import isfinite
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import and_, func, insert, select, tuple_
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.time import as_utc, utc_now
from app.decision_harness.contracts import compact_bar, content_sha256, normalise_symbol
from app.models.daily_evidence import (
    DailyBarModel,
    DailyDecisionDatasetModel,
    DailyIndicatorSnapshotModel,
    DecisionChartProjectionModel,
)
from app.models.market_data_capacity import HistoryCollectionStateModel


BAR_IDENTITY_COLUMNS = (
    "symbol", "exchange", "bar_date", "adjustment", "source", "source_revision"
)
BAR_BATCH_SIZE = 100  # Six parameters per key, below older SQLite's 999 limit.
BarIdentity = tuple[str, str, date, str, str, str]


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
        ordered_keys: list[BarIdentity] = []
        prepared: dict[BarIdentity, dict[str, Any]] = {}
        for raw in bars:
            row = self._normalise_bar(raw)
            symbol = normalise_symbol(str(raw.get("symbol") or ""))
            row_adjustment = str(raw.get("adjustment") or default_adjustment).strip().upper()
            source_revision = content_sha256(
                {**row, "date": row["date"].isoformat(), "adjustment": row_adjustment}
            )
            key = (symbol, exchange, row["date"], row_adjustment, source, source_revision)
            ordered_keys.append(key)
            if key in prepared:
                continue
            prepared[key] = dict(
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
        # Make earlier pending ORM writes visible to the Core batch insert.
        # This remains inside the caller's transaction; no batch commits.
        self.session.flush()
        keys = list(prepared)
        models: dict[BarIdentity, DailyBarModel] = {}
        identity = tuple_(*(getattr(DailyBarModel, name) for name in BAR_IDENTITY_COLUMNS))
        dialect = self.session.get_bind().dialect.name
        insert_factory = {"sqlite": sqlite_insert, "postgresql": postgres_insert}.get(dialect, insert)
        for start in range(0, len(keys), BAR_BATCH_SIZE):
            batch = keys[start:start + BAR_BATCH_SIZE]
            for model in self.session.scalars(select(DailyBarModel).where(identity.in_(batch))):
                models[tuple(getattr(model, name) for name in BAR_IDENTITY_COLUMNS)] = model
            missing = [key for key in batch if key not in models]
            if not missing:
                continue
            statement = insert_factory(DailyBarModel.__table__)
            if dialect in {"sqlite", "postgresql"}:
                statement = statement.on_conflict_do_nothing(index_elements=BAR_IDENTITY_COLUMNS)
            self.session.execute(statement, [prepared[key] for key in missing])
            # Read back both inserted rows and rows won by a concurrent writer.
            for model in self.session.scalars(select(DailyBarModel).where(identity.in_(missing))):
                models[tuple(getattr(model, name) for name in BAR_IDENTITY_COLUMNS)] = model
        return [models[key] for key in ordered_keys]

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

    def persist_collected_history(
        self,
        collected_payload: dict[str, Any],
        *,
        source: str = "moomoo_opend_history",
        market_timezone: str = "America/New_York",
        collected_at: datetime | None = None,
    ) -> int:
        """Persist normalized daily bars returned by the market collection seam."""

        rows: list[dict[str, Any]] = []
        for item in collected_payload.get("history_snapshots", []):
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
        # Keep the quota projection in the same transaction as canonical bar
        # persistence.  Only states that were created by the admission layer
        # are updated; direct and mock collectors keep the same contract.
        self._reconcile_history_capacity_states(
            {str(row.get("symbol") or "") for row in rows},
            collected_at=collected_at or utc_now(),
        )
        return len(models)

    def _reconcile_history_capacity_states(
        self,
        symbols: Iterable[str],
        *,
        collected_at: datetime,
    ) -> int:
        normalized = list(dict.fromkeys(normalise_symbol(symbol) for symbol in symbols if symbol))
        if not normalized:
            return 0
        states = list(
            self.session.scalars(
                select(HistoryCollectionStateModel).where(
                    HistoryCollectionStateModel.provider == "moomoo_openapi",
                    HistoryCollectionStateModel.symbol.in_(normalized),
                )
            )
        )
        if not states:
            return 0
        bars = self.bars(normalized)
        changed = 0
        observed_at = as_utc(collected_at)
        for state in states:
            if not state.desired_history:
                continue
            rows = bars.get(state.symbol, [])
            if not rows:
                continue
            state.bar_count = len(rows)
            state.latest_bar_date = rows[-1].bar_date
            required = state.required_through_date
            if required is not None and state.latest_bar_date < required:
                state.access_state = "retry_wait"
                state.quality_state = "stale"
                state.reason_code = "history_target_not_reached"
                state.message = (
                    f"历史 K 线最新日期 {state.latest_bar_date.isoformat()} "
                    f"早于目标 {required.isoformat()}。"
                )
                state.last_attempt_at = state.last_attempt_at or observed_at
                state.updated_at = observed_at
                changed += 1
                continue
            state.access_state = "acquired"
            minimum = max(1, int(state.minimum_bar_count or 260))
            state.quality_state = "ready" if len(rows) >= minimum else "partial"
            state.reason_code = "history_short_sample" if state.quality_state == "partial" else None
            state.message = (
                "已取得历史 K 线，但样本长度低于完整策略要求。"
                if state.quality_state == "partial"
                else None
            )
            state.last_attempt_at = state.last_attempt_at or observed_at
            state.last_success_at = observed_at
            state.updated_at = observed_at
            changed += 1
        self.session.flush()
        return changed

    def bars(
        self,
        symbols: Iterable[str],
        *,
        through_date: date | None = None,
        cutoff_time: datetime | None = None,
        source: str | None = None,
        adjustment: str = "QFQ",
    ) -> dict[str, list[DailyBarModel]]:
        normalized = list(dict.fromkeys(normalise_symbol(item) for item in symbols))
        if not normalized:
            return {}
        filters = [DailyBarModel.symbol.in_(normalized)]
        if through_date is not None:
            filters.append(DailyBarModel.bar_date <= through_date)
        if cutoff_time is not None:
            filters.append(DailyBarModel.collected_at <= as_utc(cutoff_time))
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

    def dataset_for_scope_date(
        self,
        *,
        scope_type: str,
        scope_id: str,
        trading_date: date,
        scope_version: int | None = None,
    ) -> DailyDecisionDatasetModel | None:
        """Return the newest immutable dataset for one exact scope/date.

        A daily comparison must not silently fall back to an older session when
        the immediately previous trading day is missing.  Callers can therefore
        distinguish an exact T-1 baseline from a stale or unavailable one.
        """

        filters = [
            DailyDecisionDatasetModel.scope_type == scope_type,
            DailyDecisionDatasetModel.scope_id == scope_id,
            DailyDecisionDatasetModel.trading_date == trading_date,
        ]
        if scope_version is not None:
            filters.append(DailyDecisionDatasetModel.scope_version == scope_version)
        return self.session.scalar(
            select(DailyDecisionDatasetModel)
            .where(*filters)
            .order_by(DailyDecisionDatasetModel.created_at.desc())
            .limit(1)
        )

    def previous_dataset(
        self,
        *,
        scope_type: str,
        scope_id: str,
        trading_date: date,
        scope_version: int | None = None,
    ) -> DailyDecisionDatasetModel | None:
        """Return the newest dataset strictly before ``trading_date``."""

        filters = [
            DailyDecisionDatasetModel.scope_type == scope_type,
            DailyDecisionDatasetModel.scope_id == scope_id,
            DailyDecisionDatasetModel.trading_date < trading_date,
        ]
        if scope_version is not None:
            filters.append(DailyDecisionDatasetModel.scope_version == scope_version)
        return self.session.scalar(
            select(DailyDecisionDatasetModel)
            .where(*filters)
            .order_by(
                DailyDecisionDatasetModel.trading_date.desc(),
                DailyDecisionDatasetModel.created_at.desc(),
            )
            .limit(1)
        )

    def chart(self, dataset_id: str) -> DecisionChartProjectionModel | None:
        return self.session.scalar(
            select(DecisionChartProjectionModel).where(DecisionChartProjectionModel.dataset_id == dataset_id)
        )

    def chart_instrument(
        self, dataset_id: str, symbol: str, *, replay_only: bool = False
    ) -> dict[str, Any] | None:
        """Project JSON paths in SQL so other instruments never enter Python."""

        payload = DecisionChartProjectionModel.payload_json
        instrument = payload["instruments"][symbol]
        if replay_only:
            # The opportunity adapter consumes only these instrument fields.
            fields = {
                "instrument_type": (
                    func.json_typeof(instrument)
                    if self.session.get_bind().dialect.name == "postgresql"
                    else func.json_type(instrument)
                ),
                "bars": instrument["price"]["bars"],
                "quality": instrument["quality"],
                "asset_type": instrument["asset_type"],
            }
        else:
            fields = {
                "instrument": instrument,
                **{name: payload[name] for name in (
                    "scope", "timezone", "quality", "overlays", "state_segments", "events"
                )},
            }
        row = self.session.execute(
            select(
                DecisionChartProjectionModel.content_sha256.label("parent_stored_content_sha256"),
                *(expression.label(name) for name, expression in fields.items()),
            ).where(DecisionChartProjectionModel.dataset_id == dataset_id)
        ).mappings().first()
        if row is None:
            return None
        if replay_only:
            if row["instrument_type"] != "object":
                return {"dataset_id": dataset_id, "instruments": {}}
            return {"dataset_id": dataset_id, "instruments": {
                symbol: {"price": {"bars": row["bars"]}, "quality": row["quality"],
                         "asset_type": row["asset_type"]}
            }}
        if not isinstance(row["instrument"], dict):
            return None
        return {
            "schema_version": "urus.instrument_chart_projection.v1",
            "dataset_id": dataset_id,
            "symbol": symbol,
            "parent_stored_content_sha256": row["parent_stored_content_sha256"],
            "scope": row["scope"], "timezone": row["timezone"], "quality": row["quality"],
            "instruments": {symbol: row["instrument"]},
            **{name: [item for item in (row[name] or []) if item.get("symbol") in {None, symbol}]
               for name in ("overlays", "state_segments", "events")},
        }

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
