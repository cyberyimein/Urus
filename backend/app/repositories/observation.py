from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.time import utc_now
from app.models.observation import (
    GroupDailySnapshotModel,
    ObservationGroupVersionModel,
    ObservationRunModel,
    ObservationUniverseRevisionModel,
)
from app.schemas.observation import ObservationGroupCreateRequest


class ObservationUniverseRevisionRepository:
    """Persist and query the immutable source-to-local Universe audit chain."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, revision_id: str) -> ObservationUniverseRevisionModel | None:
        return self.session.get(ObservationUniverseRevisionModel, revision_id)

    def latest(self, source_url: str) -> ObservationUniverseRevisionModel | None:
        return self.session.scalar(
            select(ObservationUniverseRevisionModel)
            .where(ObservationUniverseRevisionModel.source_url == source_url)
            .order_by(ObservationUniverseRevisionModel.fetched_at.desc())
            .limit(1)
        )

    def by_content(
        self,
        *,
        source_url: str,
        content_sha256: str,
    ) -> ObservationUniverseRevisionModel | None:
        return self.session.scalar(
            select(ObservationUniverseRevisionModel).where(
                ObservationUniverseRevisionModel.source_url == source_url,
                ObservationUniverseRevisionModel.content_sha256 == content_sha256,
            )
        )

    def save(
        self,
        *,
        source_url: str,
        upstream_version_id: str | None,
        upstream_revision: int | None,
        local_universe_version_id: str,
        content_sha256: str,
    ) -> ObservationUniverseRevisionModel:
        existing = self.by_content(source_url=source_url, content_sha256=content_sha256)
        if existing is not None:
            return existing
        model = ObservationUniverseRevisionModel(
            id=str(uuid4()),
            source_url=source_url,
            upstream_version_id=upstream_version_id,
            upstream_revision=upstream_revision,
            local_universe_version_id=local_universe_version_id,
            content_sha256=content_sha256,
            fetched_at=utc_now(),
        )
        self.session.add(model)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.by_content(source_url=source_url, content_sha256=content_sha256)
            if existing is None:
                raise
            return existing
        self.session.refresh(model)
        return model


class ObservationGroupRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, group_id: str, version_id: str | None = None) -> ObservationGroupVersionModel | None:
        statement = select(ObservationGroupVersionModel).where(
            ObservationGroupVersionModel.group_id == group_id
        )
        if version_id:
            statement = statement.where(ObservationGroupVersionModel.id == version_id)
        else:
            statement = statement.where(ObservationGroupVersionModel.status == "active").order_by(
                ObservationGroupVersionModel.version.desc()
            )
        return self.session.scalar(statement)

    @staticmethod
    def is_legacy_self_selected(model: ObservationGroupVersionModel) -> bool:
        """Identify the obsolete manual self-selection group without deleting it."""

        return model.source == "manual" and bool(
            set(model.tags or []) & {"self-selected", "user-selected", "user-qualified"}
        )

    def list_active(
        self,
        *,
        include_legacy_self_selected: bool = False,
    ) -> list[ObservationGroupVersionModel]:
        rows = list(
            self.session.scalars(
                select(ObservationGroupVersionModel)
                .where(ObservationGroupVersionModel.status == "active")
                .order_by(
                    ObservationGroupVersionModel.display_order,
                    ObservationGroupVersionModel.display_name,
                    ObservationGroupVersionModel.version.desc(),
                )
            )
        )
        if not include_legacy_self_selected:
            rows = [row for row in rows if not self.is_legacy_self_selected(row)]
        seen: set[str] = set()
        result: list[ObservationGroupVersionModel] = []
        for row in rows:
            if row.group_id not in seen:
                seen.add(row.group_id)
                result.append(row)
        return result

    def retire_synced_except(self, active_group_ids: set[str]) -> None:
        changed = False
        for row in self.list_active():
            if row.source == "universe" and row.group_id not in active_group_ids:
                row.status = "retired"
                changed = True
        if changed:
            self.session.commit()

    def ensure_default(self, universe_items: list[dict[str, Any]]) -> ObservationGroupVersionModel | None:
        """Keep the old bootstrap hook side-effect free.

        Observation groups are now created only by Universe projection sync or
        explicit user actions. In particular, a missing active group must not
        be replaced with the obsolete manual self-selection group.
        """

        del universe_items
        active = self.list_active()
        return active[0] if active else None

    def save(
        self,
        request: ObservationGroupCreateRequest,
        *,
        source: str = "manual",
        universe_revision_id: str | None = None,
    ) -> ObservationGroupVersionModel:
        source = str(source or "manual").strip().lower()
        if source not in {"manual", "universe"}:
            raise ValueError("观察组来源必须是 manual 或 universe")
        current = self.get(request.group_id)
        if current is not None and request.base_version_id != current.id:
            raise AppError(
                "观察组已被其他页面更新，请刷新后再保存。",
                code="observation_group_version_conflict",
                status_code=409,
                details={"current_version_id": current.id},
            )
        latest_version = self.session.scalar(
            select(func.max(ObservationGroupVersionModel.version)).where(
                ObservationGroupVersionModel.group_id == request.group_id
            )
        ) or 0
        if current is not None:
            current_definition = self.definition(current)
            requested_definition = self.definition(
                request,
                source=source,
                universe_revision_id=universe_revision_id,
            )
            if self.digest(current_definition) == self.digest(requested_definition):
                return current
            current.status = "retired"
        now = utc_now()
        definition = self.definition(
            request,
            source=source,
            universe_revision_id=universe_revision_id,
        )
        model = ObservationGroupVersionModel(
            id=str(uuid4()),
            group_id=request.group_id,
            version=int(latest_version) + 1,
            status="active",
            source=source,
            universe_revision_id=universe_revision_id,
            display_name=request.display_name,
            description=request.description,
            symbols=request.symbols,
            benchmark_symbols=request.benchmark_symbols,
            tags=request.tags,
            display_order=request.display_order,
            content_sha256=self.digest(definition),
            created_at=now,
            activated_at=now,
        )
        self.session.add(model)
        self.session.commit()
        return model

    @staticmethod
    def definition(
        value: ObservationGroupVersionModel | ObservationGroupCreateRequest,
        *,
        source: str | None = None,
        universe_revision_id: str | None = None,
    ) -> dict[str, Any]:
        if isinstance(value, ObservationGroupVersionModel):
            return {
                "group_id": value.group_id,
                "display_name": value.display_name,
                "description": value.description,
                "symbols": list(value.symbols or []),
                "benchmark_symbols": list(value.benchmark_symbols or []),
                "tags": list(value.tags or []),
                "display_order": value.display_order,
                "source": value.source,
                "universe_revision_id": value.universe_revision_id,
            }
        return {
            **value.model_dump(mode="json", exclude={"base_version_id"}),
            "source": source or "manual",
            "universe_revision_id": universe_revision_id,
        }

    @staticmethod
    def digest(value: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def response(model: ObservationGroupVersionModel) -> dict[str, Any]:
        tags = list(model.tags or [])
        display_name = model.display_name
        description = model.description
        # Keep active groups created by the previous Phase C UI readable while
        # exposing the new taxonomy to every client. Historical rows and run
        # references remain untouched; only the API presentation is migrated.
        if model.source == "universe" and (
            "indicator-recommendation" in tags or "watchlist" in tags
        ):
            display_name = "指标推荐"
            if description == "由当前部署 Universe 的 equity_watchlist 自动同步。":
                description = "由当前部署 Universe 自动生成的指标推荐列表；不能手工编辑。"
        elif model.source == "manual" and (
            "self-selected" in tags
            or "user-selected" in tags
            or "user-qualified" in tags
        ) and model.display_name in {"核心观察组", "核心关注列表", "自选组"}:
            display_name = "自选组"
            if description == "来自 Universe 的用户观察列表；组内基本面资格由用户维护。":
                description = "用户维护的自选组；组内标的只用于本地观察，不属于指标推荐。"
        return {
            "version_id": model.id,
            "group_id": model.group_id,
            "version": model.version,
            "status": model.status,
            "source": model.source,
            "universe_revision_id": model.universe_revision_id,
            "display_name": display_name,
            "description": description,
            "symbols": list(model.symbols or []),
            "benchmark_symbols": list(model.benchmark_symbols or []),
            "tags": tags,
            "display_order": model.display_order,
            "content_sha256": model.content_sha256,
            "created_at": model.created_at,
            "activated_at": model.activated_at,
        }


class ObservationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_run(self, run_id: str) -> ObservationRunModel | None:
        return self.session.get(ObservationRunModel, run_id)

    def get_snapshot(self, snapshot_id: str) -> GroupDailySnapshotModel | None:
        """Return one immutable group snapshot referenced by an Observation Run."""

        return self.session.get(GroupDailySnapshotModel, snapshot_id)

    def snapshot_for_run(
        self,
        run_id: str,
        *,
        group_id: str | None = None,
        snapshot_id: str | None = None,
    ) -> tuple[ObservationRunModel, GroupDailySnapshotModel] | None:
        """Resolve an immutable snapshot only through the exact Run manifest."""

        run = self.get_run(run_id)
        if run is None:
            return None
        for item in list((run.payload_json or {}).get("group_snapshots") or []):
            if item.get("status") != "succeeded" or not item.get("snapshot_id"):
                continue
            if snapshot_id is not None and str(item.get("snapshot_id")) != str(snapshot_id):
                continue
            if group_id is not None and str(item.get("group_id")) != str(group_id):
                continue
            snapshot = self.get_snapshot(str(item["snapshot_id"]))
            if snapshot is None:
                continue
            if group_id is not None and snapshot.group_id != group_id:
                continue
            if item.get("dataset_id") and str(item.get("dataset_id")) != str(snapshot.dataset_id):
                continue
            if item.get("group_version_id") and str(item.get("group_version_id")) != str(snapshot.group_version_id):
                continue
            return run, snapshot
        return None

    def get_by_idempotency(self, key: str) -> ObservationRunModel | None:
        return self.session.scalar(
            select(ObservationRunModel).where(ObservationRunModel.idempotency_key == key)
        )

    def list_runs(self, limit: int = 50) -> list[ObservationRunModel]:
        return list(
            self.session.scalars(
                select(ObservationRunModel)
                .order_by(ObservationRunModel.created_at.desc())
                .limit(limit)
            )
        )

    def latest_completed_run(self, trading_date: date) -> ObservationRunModel | None:
        """Return the latest usable Observation Run for one trading date."""

        return self.session.scalar(
            select(ObservationRunModel)
            .where(
                ObservationRunModel.trading_date == trading_date,
                ObservationRunModel.status.in_(("succeeded", "mixed", "partial")),
            )
            .order_by(
                ObservationRunModel.completed_at.desc(),
                ObservationRunModel.created_at.desc(),
            )
            .limit(1)
        )

    def create_run(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        trigger_mode: str,
        trading_date: date,
        cutoff_time: datetime,
        group_ids: list[str],
        group_version_ids: list[str],
        universe_revision_id: str | None = None,
        universe_freshness: str = "unknown",
        universe_source_url: str | None = None,
    ) -> ObservationRunModel:
        model = ObservationRunModel(
            id=run_id,
            idempotency_key=idempotency_key,
            status="running",
            trigger_mode=trigger_mode,
            universe_revision_id=universe_revision_id,
            universe_freshness=universe_freshness,
            universe_source_url=universe_source_url,
            trading_date=trading_date,
            cutoff_time=cutoff_time,
            group_ids=group_ids,
            group_version_ids=group_version_ids,
            content_sha256="",
            payload_json={},
            created_at=utc_now(),
        )
        self.session.add(model)
        self.session.commit()
        return model

    def save_snapshot(
        self,
        *,
        group: ObservationGroupVersionModel,
        dataset_id: str,
        trading_date: date,
        payload: dict[str, Any],
        content_sha256: str,
        snapshot_schema_version: str,
    ) -> GroupDailySnapshotModel:
        existing = self.session.scalar(
            select(GroupDailySnapshotModel).where(
                GroupDailySnapshotModel.group_version_id == group.id,
                GroupDailySnapshotModel.dataset_id == dataset_id,
                GroupDailySnapshotModel.snapshot_schema_version == snapshot_schema_version,
            )
        )
        if existing is not None:
            return existing
        model = GroupDailySnapshotModel(
            id=str(uuid4()),
            group_version_id=group.id,
            dataset_id=dataset_id,
            group_id=group.group_id,
            group_version=group.version,
            trading_date=trading_date,
            snapshot_schema_version=snapshot_schema_version,
            content_sha256=content_sha256,
            payload_json=payload,
            created_at=utc_now(),
        )
        self.session.add(model)
        self.session.commit()
        return model

    def latest_snapshot(
        self,
        group_id: str,
        group_version_id: str | None = None,
    ) -> GroupDailySnapshotModel | None:
        filters = [GroupDailySnapshotModel.group_id == group_id]
        if group_version_id is not None:
            filters.append(GroupDailySnapshotModel.group_version_id == group_version_id)
        return self.session.scalar(
            select(GroupDailySnapshotModel)
            .where(*filters)
            .order_by(GroupDailySnapshotModel.trading_date.desc(), GroupDailySnapshotModel.created_at.desc())
        )

    def previous_snapshot(
        self,
        *,
        group_id: str,
        group_version_id: str,
        trading_date: date,
    ) -> GroupDailySnapshotModel | None:
        return self.session.scalar(
            select(GroupDailySnapshotModel)
            .where(
                GroupDailySnapshotModel.group_id == group_id,
                GroupDailySnapshotModel.group_version_id == group_version_id,
                GroupDailySnapshotModel.trading_date < trading_date,
            )
            .order_by(GroupDailySnapshotModel.trading_date.desc(), GroupDailySnapshotModel.created_at.desc())
        )

    def finish_run(
        self,
        model: ObservationRunModel,
        *,
        status: str,
        payload: dict[str, Any],
        content_sha256: str,
        error_message: str | None = None,
    ) -> ObservationRunModel:
        model.status = status
        model.payload_json = payload
        model.content_sha256 = content_sha256
        model.error_message = error_message
        model.completed_at = utc_now()
        self.session.commit()
        return model

    @staticmethod
    def run_response(model: ObservationRunModel) -> dict[str, Any]:
        payload = dict(model.payload_json or {})
        return {
            "run_id": model.id,
            "status": model.status,
            "trigger_mode": model.trigger_mode,
            "universe_revision_id": model.universe_revision_id,
            "universe_freshness": model.universe_freshness,
            "universe_source_url": model.universe_source_url,
            "trading_date": model.trading_date,
            "idempotency_key": model.idempotency_key,
            "group_ids": list(model.group_ids or []),
            "group_version_ids": list(model.group_version_ids or []),
            "group_snapshots": list(payload.get("group_snapshots") or []),
            "report": dict(payload.get("report") or {}),
            "options": dict(payload.get("options") or {}),
            "options_alignment": payload.get("options_alignment"),
            "options_collection": dict(payload.get("options_collection") or {}),
            "group_count": len(model.group_ids or []),
            "successful_group_count": int(payload.get("successful_group_count", len(payload.get("group_snapshots") or []))),
            "failed_group_count": int(payload.get("failed_group_count", 0)),
            "content_sha256": model.content_sha256,
            "created_at": model.created_at,
            "completed_at": model.completed_at,
            "error_message": model.error_message,
        }
