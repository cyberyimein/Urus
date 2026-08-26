from __future__ import annotations

from datetime import UTC, date, datetime
import hashlib
import logging
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
from sqlalchemy.exc import IntegrityError

from app.core.time import as_utc, utc_now
from app.core.errors import AppError
from app.decision_harness.contracts import content_sha256
from app.decision_harness.group_observation import build_group_snapshot
from app.decision_harness.market_evidence import DailyMarketEvidenceService
from app.decision_harness.observation_report import build_observation_report
from app.repositories.observation import (
    ObservationGroupRepository,
    ObservationRepository,
    ObservationUniverseRevisionRepository,
)
from app.repositories.universe import InstrumentUniverseRepository
from app.schemas.observation import ObservationGroupCreateRequest
from app.schemas.universe import UniverseResponse, UniverseUpdate
from app.schemas.observation import ObservationRunCreateRequest
from app.services.capital_flow import is_trading_session_date, latest_completed_session_date


logger = logging.getLogger(__name__)


class ObservationGroupSyncService:
    """Project the active Universe into versioned deterministic observation groups."""

    def __init__(self, session, settings) -> None:
        self.settings = settings
        self.universe = InstrumentUniverseRepository(session)
        self.groups = ObservationGroupRepository(session)
        self.revisions = ObservationUniverseRevisionRepository(session)

    def sync(self) -> dict[str, Any]:
        source_url = str(self.settings.observation_universe_source_url or "").strip()
        if not source_url:
            return self.sync_local(source="local", freshness="local")

        root = self._canonical_source_url(source_url)
        url = f"{root}/settings/universe"
        try:
            response = httpx.get(
                url,
                timeout=float(self.settings.observation_universe_sync_timeout_seconds),
            )
            response.raise_for_status()
            upstream = UniverseResponse.model_validate(response.json())
            content_sha = self.universe.content_digest(upstream.items)
            if content_sha != upstream.content_sha256:
                raise ValueError("上游 content_sha256 与规范化内容不一致")
        except (httpx.HTTPError, TypeError, ValueError, KeyError) as exc:
            logger.warning("Phase C Universe sync failed source=%s kind=%s", root, self._failure_kind(exc))
            if self.settings.observation_allow_stale_universe:
                stale = self.revisions.latest(root)
                if stale is not None:
                    stale_universe = self.universe.get(stale.local_universe_version_id)
                    if stale_universe is not None:
                        result = self.sync_local(
                            source="stale",
                            freshness="stale",
                            source_url=root,
                            universe=stale_universe,
                            revision=stale,
                            warnings=[
                                "上游关注列表同步失败，当前运行复用了最近一次成功的 Universe Revision。",
                                f"失败类型：{self._failure_kind(exc)}。",
                            ],
                        )
                        result["sync_error"] = self._failure_kind(exc)
                        return result
            raise AppError(
                "已部署 Universe 同步失败，本次观察已停止。",
                code="observation_universe_sync_failed",
                status_code=503,
                details={"source_url": root, "reason": self._failure_kind(exc)},
            ) from exc

        current = self.universe.active()
        update = UniverseUpdate(
            base_version_id=current.id if current is not None else None,
            items=upstream.items,
        )
        local_universe = self.universe.save(update, source="upstream")
        revision = self.revisions.save(
            source_url=root,
            upstream_version_id=upstream.version_id,
            upstream_revision=upstream.revision,
            local_universe_version_id=local_universe.id,
            content_sha256=content_sha,
        )
        result = self.sync_local(
            source="upstream",
            freshness="fresh",
            source_url=root,
            universe=local_universe,
            revision=revision,
        )
        result["upstream_universe_version_id"] = upstream.version_id
        result["upstream_universe_revision"] = upstream.revision
        return result

    def sync_local(
        self,
        *,
        source: str = "local",
        freshness: str = "local",
        source_url: str | None = None,
        universe: Any | None = None,
        revision: Any | None = None,
        warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        universe = universe or self.universe.ensure_default(self.settings)
        if revision is None:
            revision = self.revisions.save(
                source_url=source_url or "local",
                upstream_version_id=None,
                upstream_revision=None,
                local_universe_version_id=universe.id,
                content_sha256=universe.content_sha256,
            )
        response = self.universe.response(universe)
        watchlist = [
            item
            for item in response.items
            if item.enabled
            and item.roles.equity_watchlist
        ]
        if not watchlist:
            raise ValueError("当前 Universe 没有启用的 equity watchlist 标的")

        definitions: list[ObservationGroupCreateRequest] = []
        core_benchmarks = self._benchmarks(watchlist)
        definitions.append(
            ObservationGroupCreateRequest(
                group_id="core-watchlist",
                display_name="指标推荐",
                description="由当前部署 Universe 自动生成的指标推荐列表；不能手工编辑。",
                symbols=[item.symbol for item in watchlist],
                benchmark_symbols=core_benchmarks,
                tags=["universe-synced", "indicator-recommendation", "watchlist"],
                display_order=0,
            )
        )

        themes: dict[str, list[Any]] = {}
        for item in watchlist:
            for theme in item.themes:
                themes.setdefault(theme, []).append(item)
        for position, (theme, members) in enumerate(themes.items(), start=1):
            definitions.append(
                ObservationGroupCreateRequest(
                    group_id=self._theme_group_id(theme),
                    display_name=theme,
                    description=f"由当前部署 Universe 自动同步的 {theme} 关注组。",
                    symbols=[item.symbol for item in members],
                    benchmark_symbols=self._benchmarks(members),
                    tags=["universe-synced", "theme", f"theme:{theme}"],
                    display_order=position * 10,
                )
            )

        saved = []
        for definition in definitions:
            current = self.groups.get(definition.group_id)
            if current is not None and current.source != "universe":
                # A manually-created group owns its ID.  Do not silently turn
                # it into an upstream projection when a tag happens to have
                # the same slug.
                definition = definition.model_copy(
                    update={"group_id": self._available_synced_group_id(definition.group_id)}
                )
                current = self.groups.get(definition.group_id)
            saved.append(
                self.groups.save(
                    definition.model_copy(
                        update={"base_version_id": current.id if current is not None else None}
                    ),
                    source="universe",
                    universe_revision_id=revision.id,
                )
            )
        self.groups.retire_synced_except({item.group_id for item in saved})
        return {
            "source": source,
            "source_url": source_url,
            "universe_revision_id": revision.id,
            "universe_version_id": response.version_id,
            "universe_revision": response.revision,
            "universe_freshness": freshness,
            "symbol_count": len(watchlist),
            "group_count": len(saved),
            "warnings": list(warnings or []),
            "upstream_universe_version_id": revision.upstream_version_id,
            "upstream_universe_revision": revision.upstream_revision,
            "groups": [self.groups.response(item) for item in saved],
        }

    @staticmethod
    def _canonical_source_url(value: str) -> str:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("关注列表来源必须是 http(s) URL")
        hostname = parsed.hostname
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        netloc = hostname
        if parsed.port is not None:
            netloc = f"{netloc}:{parsed.port}"
        path = parsed.path.rstrip("/")
        if not path.endswith("/api"):
            path = f"{path}/api" if path else "/api"
        return urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))

    @staticmethod
    def _failure_kind(exc: Exception) -> str:
        if isinstance(exc, httpx.TimeoutException):
            return "timeout"
        if isinstance(exc, httpx.HTTPStatusError):
            return "http_error"
        if isinstance(exc, httpx.HTTPError):
            return "network_error"
        if isinstance(exc, ValueError):
            return "invalid_payload"
        return "request_error"

    def _available_synced_group_id(self, group_id: str) -> str:
        base = f"universe-{group_id}"[:64]
        candidate = base
        suffix = 2
        while True:
            existing = self.groups.get(candidate)
            if existing is None or existing.source == "universe":
                return candidate
            tail = f"-{suffix}"
            candidate = f"{base[:64 - len(tail)]}{tail}"
            suffix += 1

    @staticmethod
    def _benchmarks(items: list[Any]) -> list[str]:
        values = [item.benchmarks.relative_strength or "QQQ" for item in items]
        return list(dict.fromkeys(value for value in values if value))

    @staticmethod
    def _theme_group_id(theme: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", theme.lower()).strip("-")
        if slug:
            return f"theme-{slug}"[:64]
        digest = hashlib.sha256(theme.encode("utf-8")).hexdigest()[:12]
        return f"theme-{digest}"


class ObservationRunService:
    """Freeze every selected observation group and derive one immutable run."""

    def __init__(self, session, settings) -> None:
        self.session = session
        self.settings = settings
        self.groups = ObservationGroupRepository(session)
        self.repository = ObservationRepository(session)
        self.universe_revisions = ObservationUniverseRevisionRepository(session)
        self.daily = DailyMarketEvidenceService(session, settings)

    def create_run(
        self,
        request: ObservationRunCreateRequest,
        *,
        bar_source: Any | None = None,
        universe_sync: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cutoff = as_utc(request.cutoff_time or utc_now())
        latest_completed = latest_completed_session_date(cutoff, self.settings.market_calendar)
        trading_date = request.trading_date or latest_completed
        if trading_date > latest_completed:
            raise ValueError(
                f"{trading_date.isoformat()} 尚未完成收市；当前最后完整交易日为 {latest_completed.isoformat()}"
            )
        if not is_trading_session_date(trading_date, self.settings.market_calendar):
            raise ValueError(f"{trading_date.isoformat()} 不是 {self.settings.market_calendar} 的交易日")

        selected_ids = request.group_ids or [item.group_id for item in self.groups.list_active()]
        selected = []
        for group_id in selected_ids:
            group = self.groups.get(group_id)
            if group is None:
                raise ValueError(f"找不到 active observation group：{group_id}")
            selected.append(group)
        if not selected:
            raise ValueError("至少需要一个 active observation group")
        universe_revision_id = request.universe_revision_id
        universe_freshness = "unknown"
        universe_source_url: str | None = None
        if universe_sync is not None:
            universe_revision_id = str(universe_sync.get("universe_revision_id") or "") or None
            universe_freshness = str(universe_sync.get("universe_freshness") or "unknown")
            universe_source_url = universe_sync.get("source_url")
        if universe_revision_id:
            universe_revision = self.universe_revisions.get(universe_revision_id)
            if universe_revision is None:
                raise ValueError(f"找不到 Universe Revision：{universe_revision_id}")
            mismatched_groups = [
                group.group_id
                for group in selected
                if group.source == "universe"
                and group.universe_revision_id != universe_revision_id
            ]
            if mismatched_groups:
                raise ValueError(
                    "Observation Run 选择的自动观察组不属于声明的 Universe Revision："
                    + ", ".join(sorted(mismatched_groups))
                )
            if universe_sync is None:
                universe_source_url = request.universe_source_url or universe_revision.source_url
                universe_freshness = request.universe_freshness or (
                    "fresh" if universe_revision.source_url != "local" else "local"
                )
        version_ids = [item.id for item in selected]
        canonical_version_ids = sorted(version_ids)
        intent = request.request_intent_id or (
            f"scheduled:{trading_date.isoformat()}:{','.join(canonical_version_ids)}"
            if request.trigger_mode == "scheduled"
            else str(uuid4())
        )
        idempotency_key = content_sha256(
            {
                "request_intent_id": intent,
                "trigger_mode": request.trigger_mode,
                "trading_date": trading_date.isoformat(),
                "group_version_ids": canonical_version_ids,
                "universe_revision_id": universe_revision_id,
                "universe_freshness": universe_freshness,
            }
        )
        existing = self.repository.get_by_idempotency(idempotency_key)
        if existing is not None:
            return self.repository.run_response(existing)

        try:
            run = self.repository.create_run(
                run_id=str(uuid4()),
                idempotency_key=idempotency_key,
                trigger_mode=request.trigger_mode,
                trading_date=trading_date,
                cutoff_time=cutoff,
                group_ids=[item.group_id for item in selected],
                group_version_ids=version_ids,
                universe_revision_id=universe_revision_id,
                universe_freshness=universe_freshness,
                universe_source_url=universe_source_url,
            )
        except IntegrityError:
            # Two scheduled/manual requests can pass the read-before-create
            # check at the same time.  The unique idempotency key is the
            # source of truth; return the winner instead of creating a second
            # run or leaking a transaction error to the caller.
            self.session.rollback()
            existing = self.repository.get_by_idempotency(idempotency_key)
            if existing is None:
                raise
            return self.repository.run_response(existing)
        snapshots: list[dict[str, Any]] = []
        report_sources: list[dict[str, Any]] = []
        try:
            requested_symbols = list(
                dict.fromkeys(
                    symbol
                    for group in selected
                    for symbol in list(group.symbols or [])
                )
            )
            benchmark_symbols = list(
                dict.fromkeys(
                    symbol
                    for group in selected
                    for symbol in list(group.benchmark_symbols or [])
                )
            )
            evidence = self.daily.freeze(
                scope_type="observation_run",
                scope_id=run.id,
                symbols=requested_symbols,
                benchmark_symbols=benchmark_symbols,
                trading_date=trading_date,
                cutoff_time=cutoff,
                bar_source=bar_source,
            )
            for group in selected:
                try:
                    group_definition = self.groups.response(group)
                    previous = self.repository.previous_snapshot(
                        group_id=group.group_id,
                        group_version_id=group.id,
                        trading_date=trading_date,
                    )
                    payload = build_group_snapshot(
                        group_definition,
                        evidence,
                        previous_snapshot=previous.payload_json if previous else None,
                    )
                    snapshot = self.repository.save_snapshot(
                        group=group,
                        dataset_id=str(evidence["dataset"]["dataset_id"]),
                        trading_date=trading_date,
                        payload=payload,
                        content_sha256=str(payload["content_sha256"]),
                        snapshot_schema_version=str(payload["schema_version"]),
                    )
                    persisted_payload = dict(snapshot.payload_json or payload)
                    snapshots.append(
                        {
                            "status": "succeeded",
                            "snapshot_id": snapshot.id,
                            "group_id": group.group_id,
                            "group_version_id": group.id,
                            "dataset_id": snapshot.dataset_id,
                            "content_sha256": snapshot.content_sha256,
                            "group_decision": persisted_payload.get("group_decision", {}),
                            "changes": persisted_payload.get("changes", {}),
                            "summary": {
                                "median_20d": (persisted_payload.get("features") or {}).get("returns_percent", {}).get("20d", {}).get("median"),
                                "breadth_ma20": (persisted_payload.get("features") or {}).get("breadth", {}).get("above_ma20"),
                                "relative_20d": (persisted_payload.get("features") or {}).get("relative_strength", {}).get("median_excess_20d"),
                                "leader_concentration": (persisted_payload.get("features") or {}).get("leader_concentration"),
                            },
                        }
                    )
                    report_sources.append(
                        {
                            "status": "succeeded",
                            "group_id": group.group_id,
                            "snapshot_id": snapshot.id,
                            "dataset_id": snapshot.dataset_id,
                            "payload": persisted_payload,
                        }
                    )
                except Exception as exc:
                    self.session.rollback()
                    snapshots.append(
                        {
                            "status": "failed",
                            "group_id": group.group_id,
                            "group_version_id": group.id,
                            "error_message": str(exc)[:1000],
                        }
                    )
                    report_sources.append(
                        {
                            "status": "failed",
                            "group_id": group.group_id,
                            "error_message": str(exc)[:1000],
                        }
                    )
            successful = [item for item in snapshots if item.get("status") != "failed"]
            failed = [item for item in snapshots if item.get("status") == "failed"]
            run_status = "succeeded" if not failed else "mixed" if successful else "failed"
            run_error = "; ".join(
                f"{item.get('group_id')}: {item.get('error_message')}"
                for item in failed
                if item.get("error_message")
            ) or None
            report = build_observation_report(
                run_id=run.id,
                trading_date=trading_date.isoformat(),
                snapshots=report_sources,
                provenance={
                    "universe_revision_id": universe_revision_id,
                    "universe_freshness": universe_freshness,
                    "universe_source_url": universe_source_url,
                    "dataset_ids": sorted(
                        {
                            str(item.get("dataset_id"))
                            for item in report_sources
                            if item.get("dataset_id")
                        }
                    ),
                    "group_version_ids": sorted(version_ids),
                },
            )
            payload = {
                "schema_version": "urus.observation_run.v1",
                "run_id": run.id,
                "trading_date": trading_date.isoformat(),
                "group_snapshots": snapshots,
                "group_count": len(snapshots),
                "successful_group_count": len(successful),
                "failed_group_count": len(failed),
                "universe": {
                    "revision_id": universe_revision_id,
                    "freshness": universe_freshness,
                    "source_url": universe_source_url,
                },
                "report": report,
                "created_at": datetime.now(UTC).isoformat(),
            }
            digest = content_sha256({key: value for key, value in payload.items() if key != "created_at"})
            finished = self.repository.finish_run(
                run,
                status=run_status,
                payload=payload,
                content_sha256=digest,
                error_message=run_error,
            )
            return self.repository.run_response(finished)
        except Exception as exc:
            failed = self.repository.finish_run(
                run,
                status="failed",
                payload={"schema_version": "urus.observation_run.v1", "group_snapshots": snapshots},
                content_sha256=content_sha256({"run_id": run.id, "status": "failed", "snapshots": snapshots}),
                error_message=str(exc)[:1000],
            )
            raise ValueError(f"Observation Run 执行失败：{failed.error_message}") from exc

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        model = self.repository.get_run(run_id)
        return self.repository.run_response(model) if model else None
