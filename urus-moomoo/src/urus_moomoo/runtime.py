from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
import hashlib
import json
import logging
import math
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from urus_moomoo.config import CollectionTask, CollectorConfig
from urus_moomoo.contracts import capture_contract
from urus_moomoo.services.intraday_equities import (
    build_minute_timestamp_policy,
    build_intraday_equity_plan,
    normalize_equity_shard_symbols,
)
from urus_moomoo.services.calendar import exchange_session_time_calendar
from urus_moomoo.services.pacing import CollectionCoordinator
from urus_moomoo.services.quota import HistoryAdmission, HistoryCapacityService
from urus_moomoo.storage.sqlite import CollectorStore
from urus_moomoo.storage.intraday import CanonicalIntradayStore
from urus_moomoo.storage.equity_history import (
    CleanupDeadlineExceeded,
    EQUITY_REFS_SCHEMA,
)


logger = logging.getLogger(__name__)


class CollectorRuntime:
    """Small composition root: provider in, versioned raw output out."""

    def __init__(
        self,
        provider: Any,
        config: CollectorConfig,
        *,
        provider_factory: Callable[[CollectionCoordinator, HistoryAdmission], Any] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self.clock = clock or (lambda: datetime.now(UTC))
        self._active_equity_slot: tuple[str, str] | None = None
        output_dir = Path(config.output_dir).expanduser()
        self.store = CollectorStore(
            config.sqlite_path or output_dir / "collector.sqlite",
            market_calendar=config.exchange_calendar,
            market_timezone=config.market_timezone,
        )
        self.intraday_store = (
            CanonicalIntradayStore(
                config.intraday_sqlite_path,
                raw_payload_dir=config.intraday_raw_payload_dir,
                market_timezone=config.market_timezone,
            )
            if config.intraday_sqlite_path is not None
            else None
        )
        self.coordinator = CollectionCoordinator(config.lock_path or output_dir / "collection.lock")
        capacity = HistoryCapacityService(settings=config, store=self.store)
        self.provider = provider
        admission = HistoryAdmission(capacity, quota_reader=self._quota_snapshot)
        self.history_admission = admission
        if provider_factory is not None:
            self.provider = provider_factory(self.coordinator, admission)
        history_symbols = [
            symbol
            for task in config.tasks
            if task.enabled and task.operation in {"instrument_cards", "capture_intraday_equities"}
            for symbol in task.symbols
        ]
        admission.prepare_symbols(history_symbols)

    @classmethod
    def from_factory(
        cls,
        config: CollectorConfig,
        factory: Callable[[CollectionCoordinator, HistoryAdmission], Any],
    ) -> "CollectorRuntime":
        return cls(None, config, provider_factory=factory)

    def collect_task(self, task: CollectionTask, scheduled_at: datetime) -> dict[str, object]:
        operation = task.operation.strip()
        params = dict(task.params or {})
        if operation == "maintain_intraday_equities":
            return self._maintain_equity_history(task, scheduled_at)
        if operation == "finalize_intraday_equities":
            return self._finalize_equity_slot(task, scheduled_at)
        if operation == "capture_intraday_equities":
            if params.get("windowed_equity_shard"):
                return self._collect_equity_shard(task, scheduled_at)
            return self._collect_equity_capture(task, scheduled_at)
        with self.coordinator:
            self._reset_collection_run()
            payload = self._collect(task, scheduled_at)
            if task.operation.strip() == "capture_intraday" and self.intraday_store is not None:
                params = dict(task.params or {})
                self.intraday_store.persist(
                    scheduled_at=scheduled_at,
                    capture_tier=str(params["capture_tier"]),
                    symbols=list(task.symbols),
                    band_percent=float(params["band_percent"]),
                    payload=payload,
                )
            contract = capture_contract(
                kind=task.operation,
                payload=payload,
                requested_symbols=list(task.symbols),
                task_name=task.name,
                scheduled_at=scheduled_at,
                trading_date=scheduled_at.astimezone(
                    ZoneInfo(self.config.market_timezone)
                ).date().isoformat(),
            )
            completed = self._operation_completed(task.operation, payload)
            if task.operation.strip() == "capture_eod_options":
                saved = self.store.write_scheduled_raw(
                    task.name,
                    scheduled_at,
                    str(contract["capture_id"]),
                    task.operation,
                    contract,
                    schema_version=str(contract["schema_version"]),
                    completed=completed,
                )
            else:
                self.store.write_raw(
                    contract["capture_id"],
                    task.operation,
                    contract,
                    schema_version=str(contract["schema_version"]),
                )
            if (task.operation.strip() == "capture_session_close" and completed
                    and self.intraday_store is not None):
                self.intraday_store.finalize_trading_day(
                    scheduled_at.astimezone(ZoneInfo(self.config.market_timezone)).date(), payload
                )
            snapshots = payload.get("history_snapshots", payload.get("instruments", []))
            for snapshot in snapshots if isinstance(snapshots, list) else []:
                if not isinstance(snapshot, dict):
                    continue
                history = snapshot.get("history")
                if isinstance(history, dict) and isinstance(history.get("bars"), list):
                    self.store.write_daily_bars(
                        [bar for bar in history["bars"] if isinstance(bar, dict)],
                        symbol=str(snapshot.get("symbol") or ""),
                    )
            return {
                "scheduled_at": scheduled_at.isoformat(),
                "contract": contract,
                "completed": completed,
            }

    def _collect_equity_shard(
        self,
        task: CollectionTask,
        execution_scheduled_at: datetime,
    ) -> dict[str, object]:
        params = dict(task.params or {})
        source_task_name = str(params.get("source_task_name") or task.name.rsplit(":", 1)[0])
        shard_id = str(params.get("shard_id") or "").upper()
        slot_cutoff = _parse_datetime(params.get("slot_cutoff_at"), self.config.market_timezone)
        if not source_task_name or shard_id not in {"G1", "G2", "G3"} or slot_cutoff is None:
            raise ValueError("windowed equity shards require source task, G1/G2/G3, and slot cutoff")
        trading_date = slot_cutoff.astimezone(
            ZoneInfo(self.config.market_timezone)
        ).date().isoformat()
        maintenance_pending = self._equity_maintenance_pending(trading_date)
        capture_id = _equity_shard_capture_id(source_task_name, slot_cutoff, shard_id)
        stored = self.store.read_raw(capture_id)
        if stored is not None:
            contract = json.loads(str(stored["payload_json"]))
            if not isinstance(contract, dict):
                raise RuntimeError("stored equity shard contract is not an object")
            return {
                "scheduled_at": execution_scheduled_at.isoformat(),
                "contract": contract,
                "completed": True,
                "terminal": True,
                "maintenance_pending": maintenance_pending,
            }

        deadline = _parse_datetime(params.get("request_budget_deadline_at"), self.config.market_timezone)
        request_timeout = max(0.1, float(params.get("request_timeout_seconds", 3.0)))
        pacing_seconds = max(0.0, float(params.get("pacing_interval_seconds", 0.55)))
        now = _aware_utc(self.clock())
        skip_reason = str(params.get("skip_reason") or "") or None
        capacity_policy: dict[str, Any] | None = None
        deferred_capacity_symbols: list[str] = []
        effective_task = task
        if skip_reason is None:
            requested_minutes = [
                str(symbol).strip().upper()
                for symbol in params.get("shard_symbols", [])
                if str(symbol).strip()
            ]
            quote_symbols = list(task.symbols) if shard_id == "G1" else []
            capacity = self.store.equity_history.measure_capacity()
            projected_metrics = len(requested_minutes) * 512
            projected_raw = (
                8192
                + len(requested_minutes) * 5 * 512
                + len(quote_symbols) * 1536
            )
            capacity_policy = self.store.equity_history.capacity_policy(
                capacity,
                projected_metrics_bytes=projected_metrics,
                projected_raw_bytes=projected_raw,
            )
            if capacity_policy["skip_all_equities"]:
                return {
                    "scheduled_at": execution_scheduled_at.isoformat(),
                    "contract": None,
                    "completed": True,
                    "terminal": True,
                    "skipped_capacity": True,
                    "capacity_policy": capacity_policy,
                    "maintenance_pending": maintenance_pending,
                }
            if capacity_policy["drop_d_symbols"]:
                groups = self._business_group_by_symbol(params, task.symbols)
                d_symbols = {
                    symbol for symbol, group in groups.items() if str(group).upper() == "D"
                }
                deferred_capacity_symbols = sorted(
                    symbol for symbol in requested_minutes if symbol in d_symbols
                )
                if deferred_capacity_symbols:
                    effective_params = dict(params)
                    effective_params["shard_symbols"] = [
                        symbol
                        for symbol in requested_minutes
                        if symbol not in set(deferred_capacity_symbols)
                    ]
                    effective_task = replace(task, params=effective_params)
                    if not effective_params["shard_symbols"] and shard_id != "G1":
                        skip_reason = "deferred_capacity"
        lock_wait_seconds = (
            max(0.0, (deadline - now).total_seconds() - request_timeout - pacing_seconds)
            if deadline is not None
            else 0.0
        )
        if skip_reason is None and deadline is not None and now >= deadline:
            skip_reason = "skipped_budget"
        lock_acquired = False
        if skip_reason is None:
            lock_acquired = self.coordinator.acquire_with_timeout(lock_wait_seconds)
            if not lock_acquired:
                skip_reason = "skipped_priority"
        if lock_acquired:
            self._prepare_equity_shard_run(source_task_name, slot_cutoff, shard_id)

        try:
            if skip_reason:
                payload = self._skipped_equity_shard_payload(
                    task,
                    slot_cutoff=slot_cutoff,
                    shard_id=shard_id,
                    reason=skip_reason,
                )
                payload["provider_capture_attempted"] = False
            else:
                payload = self._collect(effective_task, slot_cutoff)
                payload["provider_capture_attempted"] = True
                if deferred_capacity_symbols:
                    _add_unavailable_minutes(payload, deferred_capacity_symbols, "capacity")
        except Exception as exc:
            logger.exception(
                "windowed equity shard failed: task=%s cutoff=%s shard=%s",
                source_task_name,
                slot_cutoff.isoformat(),
                shard_id,
            )
            payload = self._skipped_equity_shard_payload(
                task,
                slot_cutoff=slot_cutoff,
                shard_id=shard_id,
                reason="provider_error",
                warning=str(exc),
            )
            payload["provider_capture_attempted"] = False
        finally:
            if lock_acquired:
                self.coordinator.close()

        if capacity_policy is not None:
            payload["storage_capacity_policy"] = capacity_policy
        try:
            payload, references = self._compact_equity_shard_payload(
                payload,
                task=task,
                slot_cutoff=slot_cutoff,
                capture_id=capture_id,
                source_task_name=source_task_name,
                shard_id=shard_id,
            )
        except Exception:
            logger.exception(
                "equity shard persistence failed: task=%s cutoff=%s shard=%s",
                source_task_name,
                slot_cutoff.isoformat(),
                shard_id,
            )
            payload = self._skipped_equity_shard_payload(
                task,
                slot_cutoff=slot_cutoff,
                shard_id=shard_id,
                reason="storage_error",
                warning="股票规范化存储失败；该分片未发布引用。",
            )
            payload["schema_version"] = EQUITY_REFS_SCHEMA
            payload["equity_data_refs"] = []
            payload["provider_capture_attempted"] = False
            payload["maintenance_pending"] = maintenance_pending
            references = []

        contract = capture_contract(
            kind="capture_intraday_equities",
            payload=payload,
            requested_symbols=list(task.symbols),
            task_name=f"{source_task_name}:{shard_id}",
            scheduled_at=slot_cutoff,
            trading_date=slot_cutoff.astimezone(ZoneInfo(self.config.market_timezone))
            .date()
            .isoformat(),
        )
        contract["capture_id"] = capture_id
        self.store.write_raw(
            capture_id,
            "capture_intraday_equities",
            contract,
            schema_version=str(contract["schema_version"]),
            equity_data_refs=references,
            capture_trading_date=str(contract["trading_date"]),
        )
        return {
            "scheduled_at": execution_scheduled_at.isoformat(),
            "contract": contract,
            # A shard attempt is terminal even when its provider coverage is partial.
            "completed": True,
            "terminal": True,
            "maintenance_pending": maintenance_pending,
        }

    def _collect_equity_capture(
        self,
        task: CollectionTask,
        scheduled_at: datetime,
    ) -> dict[str, object]:
        existing = self.store.read_scheduled_raw(task.name, scheduled_at)
        if existing is not None and bool(existing.get("completed")):
            contract = existing.get("contract")
            if isinstance(contract, dict):
                return {
                    "scheduled_at": scheduled_at.isoformat(),
                    "contract": contract,
                    "completed": True,
                    "terminal": True,
                    "maintenance_pending": self._equity_maintenance_pending(
                        scheduled_at.astimezone(
                            ZoneInfo(self.config.market_timezone)
                        ).date().isoformat()
                    ),
                }

        params = dict(task.params or {})
        trading_date = scheduled_at.astimezone(
            ZoneInfo(self.config.market_timezone)
        ).date().isoformat()
        maintenance_pending = self._equity_maintenance_pending(trading_date)
        groups = params.get("groups")
        if isinstance(groups, dict):
            plan = build_intraday_equity_plan(
                groups,
                slot_index=int(params.get("slot_index", 0)),
                allowed_symbols=task.symbols,
            )
            quote_symbols = list(plan["quote_symbols"])
            minute_symbols = list(plan["minute_symbols"])
            group_by_symbol = dict(plan["group_by_symbol"])
        else:
            quote_symbols = list(params.get("quote_symbols") or task.symbols)
            minute_symbols = list(
                params.get("minute_symbols") or quote_symbols
            )
            group_by_symbol = {
                str(symbol).strip().upper(): str(group).strip().upper()
                for symbol, group in dict(params.get("group_by_symbol") or {}).items()
            }

        quote_symbols = [str(symbol).strip().upper() for symbol in quote_symbols]
        minute_symbols = [str(symbol).strip().upper() for symbol in minute_symbols]
        capacity = self.store.equity_history.measure_capacity()
        capacity_policy = self.store.equity_history.capacity_policy(
            capacity,
            projected_metrics_bytes=len(minute_symbols) * 512,
            projected_raw_bytes=8192 + len(minute_symbols) * 5 * 512 + len(quote_symbols) * 1536,
        )
        if capacity_policy["skip_all_equities"]:
            return {
                "scheduled_at": scheduled_at.isoformat(),
                "contract": None,
                "completed": True,
                "terminal": True,
                "skipped_capacity": True,
                "capacity_policy": capacity_policy,
                "maintenance_pending": maintenance_pending,
            }

        deferred_capacity_symbols: list[str] = []
        if capacity_policy["drop_d_symbols"]:
            deferred_capacity_symbols = sorted(
                symbol for symbol in minute_symbols if group_by_symbol.get(symbol) == "D"
            )
            minute_symbols = [
                symbol for symbol in minute_symbols if symbol not in set(deferred_capacity_symbols)
            ]

        provider_params = {
            key: value
            for key, value in params.items()
            if key
            not in {
                "groups",
                "quote_symbols",
                "minute_symbols",
                "group_by_symbol",
                "display_group_by_symbol",
                "pool_plan_id",
                "pool_revision",
                "equity_retention_apply",
            }
        }
        provider_params.update(
            {
                "quote_symbols": quote_symbols,
                "minute_symbols": minute_symbols,
                "group_by_symbol": group_by_symbol,
                "capture_quotes": bool(params.get("capture_quotes", True)),
            }
        )
        effective_task = replace(task, params=provider_params)
        capture_id = _equity_capture_id(task.name, scheduled_at)
        try:
            with self.coordinator:
                self._reset_collection_run()
                payload = self._collect(effective_task, scheduled_at)
            payload["provider_capture_attempted"] = True
            if deferred_capacity_symbols:
                _add_unavailable_minutes(payload, deferred_capacity_symbols, "capacity")
        except Exception as exc:
            logger.exception("non-windowed equity capture failed: task=%s", task.name)
            payload = {
                "schema_version": "moomoo-intraday-equities-v1",
                "provider": "moomoo_openapi",
                "captured_at": _aware_utc(self.clock()).isoformat(),
                "trading_date": scheduled_at.astimezone(
                    ZoneInfo(self.config.market_timezone)
                ).date().isoformat(),
                "scheduled_at": scheduled_at.isoformat(),
                "evidence_cutoff": scheduled_at.isoformat(),
                "requested_symbols": list(task.symbols),
                "quote_symbols": quote_symbols,
                "minute_symbols": minute_symbols,
                "quotes": [],
                "unavailable_quote_symbols": quote_symbols,
                "minute_bars": [],
                "invalid_minute_bars": [],
                "minute_bar_unavailable_symbols": minute_symbols,
                "quality_summary": {
                    "complete": False,
                    "status": "partial",
                    "minute_symbols_requested": len(minute_symbols),
                    "minute_bars_returned": 0,
                    "invalid_minute_bars": 0,
                    "unavailable_quote_symbols": quote_symbols,
                    "unavailable_minute_symbols": minute_symbols,
                    "quality_warnings": [str(exc)],
                },
                "slot_shard_status": "provider_error",
                "provider_capture_attempted": False,
            }

        payload["storage_capacity_policy"] = capacity_policy
        try:
            compact_payload, references = self._compact_equity_shard_payload(
                payload,
                task=effective_task,
                slot_cutoff=scheduled_at,
                capture_id=capture_id,
                source_task_name=task.name,
                shard_id="G1",
            )
        except Exception:
            logger.exception("non-windowed equity persistence failed: task=%s", task.name)
            compact_payload = {
                "schema_version": EQUITY_REFS_SCHEMA,
                "provider": "moomoo_openapi",
                "captured_at": _aware_utc(self.clock()).isoformat(),
                "trading_date": scheduled_at.astimezone(
                    ZoneInfo(self.config.market_timezone)
                ).date().isoformat(),
                "scheduled_at": scheduled_at.isoformat(),
                "evidence_cutoff": scheduled_at.isoformat(),
                "requested_symbols": list(task.symbols),
                "quote_symbols": quote_symbols,
                "minute_symbols": minute_symbols,
                "equity_data_refs": [],
                "quality_summary": {
                    "complete": False,
                    "status": "partial",
                    "quality_warnings": ["股票规范化存储失败；未发布引用。"],
                },
                "slot_shard_status": "storage_error",
                "maintenance_pending": maintenance_pending,
            }
            references = []

        compact_payload["display_group_by_symbol"] = dict(
            params.get("display_group_by_symbol") or {}
        )
        compact_payload["pool_plan_id"] = params.get("pool_plan_id")
        compact_payload["pool_revision"] = params.get("pool_revision")

        contract = capture_contract(
            kind="capture_intraday_equities",
            payload=compact_payload,
            requested_symbols=list(task.symbols),
            task_name=task.name,
            scheduled_at=scheduled_at,
            trading_date=scheduled_at.astimezone(
                ZoneInfo(self.config.market_timezone)
            ).date().isoformat(),
        )
        contract["capture_id"] = capture_id
        completed = bool(
            isinstance(compact_payload.get("quality_summary"), dict)
            and compact_payload["quality_summary"].get("complete")
        )
        saved = self.store.write_scheduled_raw(
            task.name,
            scheduled_at,
            capture_id,
            "capture_intraday_equities",
            contract,
            schema_version=str(contract["schema_version"]),
            completed=completed,
            equity_data_refs=references,
            capture_trading_date=str(contract["trading_date"]),
        )
        if not saved:
            stored = self.store.read_scheduled_raw(task.name, scheduled_at)
            if stored is not None and isinstance(stored.get("contract"), dict):
                contract = stored["contract"]
                completed = bool(stored["completed"])
        return {
            "scheduled_at": scheduled_at.isoformat(),
            "contract": contract,
            "completed": completed,
            "terminal": True,
            "maintenance_pending": maintenance_pending,
        }

    def _compact_equity_shard_payload(
        self,
        payload: dict[str, object],
        *,
        task: CollectionTask,
        slot_cutoff: datetime,
        capture_id: str,
        source_task_name: str,
        shard_id: str,
    ) -> tuple[dict[str, object], list[dict[str, Any]]]:
        trading_date = slot_cutoff.astimezone(ZoneInfo(self.config.market_timezone)).date()
        trading_date_text = trading_date.isoformat()
        captured_at = _parse_datetime(payload.get("captured_at"), self.config.market_timezone)
        captured_at = _aware_utc(captured_at or self.clock())
        provider_attempted = bool(payload.get("provider_capture_attempted"))
        history = self.store.equity_history
        payload_minute_symbols = payload.get("minute_symbols")
        if isinstance(payload_minute_symbols, list):
            requested_minutes = [
                str(symbol).strip().upper()
                for symbol in payload_minute_symbols
                if str(symbol).strip()
            ]
        else:
            params = dict(task.params or {})
            requested_minutes = [
                str(symbol).strip().upper()
                for symbol in params.get("shard_symbols", [])
                if str(symbol).strip()
            ]
        if provider_attempted:
            history.record_trading_session(trading_date_text)

        source_bars = payload.get("minute_bars")
        source_bars = source_bars if isinstance(source_bars, list) else []
        minute_scope = set(requested_minutes)
        dictionary_rows = [row for row in source_bars if isinstance(row, dict)]
        non_object_bar_count = len(source_bars) - len(dictionary_rows)
        normalized_rows = [
            row
            for row in dictionary_rows
            if str(row.get("symbol") or "").strip().upper() in minute_scope
        ]
        out_of_scope_bar_count = len(dictionary_rows) - len(normalized_rows)
        bar_write = history.upsert_bars(
            normalized_rows,
            timeframe="1m",
            trading_date=trading_date_text,
            source_capture_id=capture_id,
            observed_at=captured_at,
            strict=False,
        ) if provider_attempted and requested_minutes else {"inserted": 0, "revised": 0, "unchanged": 0}
        aggregate = history.aggregate_5m_from_1m(
            symbols=requested_minutes,
            trading_date=trading_date_text,
            source_capture_id=capture_id,
            market_timezone=self.config.market_timezone,
            observed_at=captured_at,
        ) if provider_attempted and requested_minutes else {"inserted": 0, "revised": 0, "unchanged": 0}

        market_timezone = ZoneInfo(self.config.market_timezone)
        current_open = datetime.combine(trading_date, time(9, 30), tzinfo=market_timezone)
        references: list[dict[str, Any]] = []
        # A failed or skipped shard must not point at rows collected by an
        # earlier slot. Bar refs cover a cumulative session range, so creating
        # them without a provider attempt could make stale rows look current.
        if provider_attempted and requested_minutes:
            references.append(
                history.make_bar_ref(
                    provider="moomoo_openapi",
                    symbols=requested_minutes,
                    timeframe="1m",
                    adjustment="none",
                    from_time=current_open,
                    through_time=slot_cutoff,
                    capture_trading_date=trading_date_text,
                )
            )
            retained_dates = history.recent_trading_dates(trading_date_text, count=21)
            history_open_date = date.fromisoformat(retained_dates[0]) if retained_dates else trading_date
            history_open = datetime.combine(
                history_open_date,
                time(9, 30),
                tzinfo=market_timezone,
            )
            references.append(
                history.make_bar_ref(
                    provider="moomoo_openapi",
                    symbols=requested_minutes,
                    timeframe="5m",
                    adjustment="none",
                    from_time=history_open,
                    through_time=slot_cutoff,
                    capture_trading_date=trading_date_text,
                )
            )

        quote_count = 0
        invalid_quote_count = 0
        quote_storage_failed = False
        quote_unavailable_symbols: list[str] = []
        if provider_attempted and shard_id == "G1":
            source_quotes = payload.get("quotes")
            source_quotes = source_quotes if isinstance(source_quotes, list) else []
            quote_scope = {str(symbol).strip().upper() for symbol in task.symbols}
            quote_by_symbol: dict[str, dict[str, Any]] = {}
            duplicate_quote_symbols: set[str] = set()
            seen_quote_symbols: set[str] = set()
            for source_quote in source_quotes:
                if not isinstance(source_quote, dict):
                    invalid_quote_count += 1
                    continue
                quote_row = dict(source_quote)
                symbol = str(quote_row.get("symbol") or "").strip().upper()
                if not symbol or symbol not in quote_scope:
                    invalid_quote_count += 1
                    continue
                if symbol in seen_quote_symbols:
                    duplicate_quote_symbols.add(symbol)
                    invalid_quote_count += 1
                    continue
                seen_quote_symbols.add(symbol)
                raw_price = quote_row.get("last_price")
                try:
                    price = float(raw_price)
                except (TypeError, ValueError, OverflowError):
                    price = float("nan")
                if isinstance(raw_price, bool) or not math.isfinite(price) or price <= 0:
                    invalid_quote_count += 1
                    continue
                quote_row["symbol"] = symbol
                quote_by_symbol[symbol] = quote_row
            for symbol in duplicate_quote_symbols:
                quote_by_symbol.pop(symbol, None)
            quote_rows = list(quote_by_symbol.values())
            quote_unavailable_symbols = [
                str(symbol)
                for symbol in payload.get("unavailable_quote_symbols", [])
                if str(symbol).strip()
            ] if isinstance(payload.get("unavailable_quote_symbols"), list) else []
            quote_unavailable_symbols.extend(sorted(quote_scope - set(quote_by_symbol)))
            quote_unavailable_symbols = list(dict.fromkeys(quote_unavailable_symbols))
            payload["unavailable_quote_symbols"] = quote_unavailable_symbols
            for row in quote_rows:
                row["received_at"] = self._equity_quote_received_at(
                    payload,
                    row,
                    fallback=captured_at,
                )
            quote_capture_times = [
                _parse_datetime(row.get("received_at"), "UTC")
                for row in quote_rows
                if row.get("received_at")
            ]
            quote_batch_captured_at = max(
                [captured_at, *(_aware_utc(value) for value in quote_capture_times if value is not None)]
            )
            try:
                quote_ref = history.write_quote_batch(
                    task_name=source_task_name,
                    scheduled_at=slot_cutoff,
                    trading_date=trading_date_text,
                    capture_id=capture_id,
                    captured_at=quote_batch_captured_at,
                    quotes=quote_rows,
                    requested_symbols=list(task.symbols),
                )
            except Exception:
                # Quotes are independent evidence: preserve this shard's valid
                # bar refs even when its quote batch cannot be persisted.
                logger.exception(
                    "equity quote persistence failed: task=%s cutoff=%s; preserving bar refs",
                    source_task_name, slot_cutoff.isoformat(),
                )
                quote_storage_failed = True
                quote_unavailable_symbols = list(task.symbols)
                payload["unavailable_quote_symbols"] = quote_unavailable_symbols
            else:
                references.append(quote_ref)
                quote_count = len(quote_rows)

        original_quality = payload.get("quality_summary")
        quality = dict(original_quality) if isinstance(original_quality, dict) else {}
        provider_invalid_count = _as_nonnegative_int(quality.get("invalid_minute_bars"))
        invalid_rows = payload.get("invalid_minute_bars")
        reported_invalid_rows = len(invalid_rows) if isinstance(invalid_rows, list) else 0
        invalid_count = max(provider_invalid_count, reported_invalid_rows)
        invalid_count += (
            non_object_bar_count
            + out_of_scope_bar_count
            + int(bar_write.get("invalid", 0))
        )
        quality["minute_bars_returned"] = _as_nonnegative_int(
            quality.get("minute_bars_returned", len(normalized_rows))
        )
        quality["invalid_minute_bars"] = invalid_count
        if provider_attempted and shard_id == "G1":
            quality["unavailable_quote_symbols"] = quote_unavailable_symbols
            quality["quote_coverage"] = (
                (len(task.symbols) - len(quote_unavailable_symbols)) / len(task.symbols)
                if task.symbols
                else 1.0
            )
            if quote_unavailable_symbols:
                quality["complete"] = False
                quality["status"] = "partial"
                payload["slot_shard_status"] = "partial"
                warnings = list(quality.get("quality_warnings", []))
                warnings.append("G1 报价范围未完整或含重复行；未发布缺失报价。")
                quality["quality_warnings"] = warnings
            if quote_storage_failed:
                quality["quote_storage_status"] = "error"
                warnings = list(quality.get("quality_warnings", []))
                warnings.append("G1 报价存储失败；已保留有效分钟线引用，未使用旧槽报价。")
                quality["quality_warnings"] = warnings
            if invalid_quote_count:
                quality["complete"] = False
                quality["status"] = "partial"
                payload["slot_shard_status"] = "partial"
                quality["invalid_quote_rows"] = invalid_quote_count
                warnings = list(quality.get("quality_warnings", []))
                warnings.append(f"{invalid_quote_count} 条无效报价行未进入规范化存储。")
                quality["quality_warnings"] = warnings
        quality["stored_minute_bar_count"] = len(normalized_rows) - int(bar_write.get("invalid", 0))
        quality["stored_five_minute_revision_count"] = int(aggregate.get("inserted", 0)) + int(
            aggregate.get("revised", 0)
        )
        if invalid_count:
            quality["complete"] = False
            quality["status"] = "partial"
            payload["slot_shard_status"] = "partial"
            warnings = list(quality.get("quality_warnings", []))
            warnings.append(f"{invalid_count} 根无效分钟线未进入规范化存储。")
            quality["quality_warnings"] = warnings
        payload["quality_summary"] = quality
        payload["maintenance_pending"] = self._equity_maintenance_pending(
            trading_date_text
        )
        payload["storage_summary"] = {
            "schema_version": EQUITY_REFS_SCHEMA,
            "minute_rows_inserted": int(bar_write.get("inserted", 0)),
            "minute_rows_revised": int(bar_write.get("revised", 0)),
            "minute_rows_unchanged": int(bar_write.get("unchanged", 0)),
            "five_minute_rows_inserted": int(aggregate.get("inserted", 0)),
            "five_minute_rows_revised": int(aggregate.get("revised", 0)),
            "five_minute_rows_unchanged": int(aggregate.get("unchanged", 0)),
            "quote_rows": quote_count,
            "history_status": next(
                (
                    str(reference.get("history_status"))
                    for reference in references
                    if reference.get("kind") == "bars" and reference.get("timeframe") == "5m"
                ),
                "warming",
            ),
        }
        payload["provider_request_summary"] = _provider_request_summary(payload.get("provider_raw"))
        payload["schema_version"] = EQUITY_REFS_SCHEMA
        payload["equity_data_refs"] = references
        task_params = dict(task.params or {})
        payload["display_group_by_symbol"] = dict(
            task_params.get("display_group_by_symbol") or {}
        )
        payload["pool_plan_id"] = task_params.get("pool_plan_id")
        payload["pool_revision"] = task_params.get("pool_revision")
        payload.pop("minute_bars", None)
        payload.pop("quotes", None)
        payload.pop("invalid_minute_bars", None)
        payload.pop("provider_raw", None)
        payload.pop("provider_capture_attempted", None)
        return payload, references

    @staticmethod
    def _equity_quote_received_at(
        payload: dict[str, object],
        quote: dict[str, Any],
        *,
        fallback: datetime,
    ) -> str:
        explicit = quote.get("received_at")
        if explicit:
            parsed = _parse_datetime(explicit, "UTC")
            if parsed is not None:
                return _aware_utc(parsed).isoformat()
        quote_code = str(quote.get("quote_code") or "").strip().upper()
        provider_raw = payload.get("provider_raw")
        batches = provider_raw.get("market_snapshot_batches") if isinstance(provider_raw, dict) else None
        if isinstance(batches, list):
            for batch in batches:
                if not isinstance(batch, dict):
                    continue
                response_received_at = _parse_datetime(
                    batch.get("response_received_at"), "UTC"
                )
                if response_received_at is None:
                    continue
                codes = {
                    str(record.get("code") or "").strip().upper()
                    for record in batch.get("records", [])
                    if isinstance(record, dict)
                }
                if quote_code in codes:
                    return _aware_utc(response_received_at).isoformat()
        return fallback.isoformat()

    def _prepare_equity_shard_run(
        self,
        task_name: str,
        slot_cutoff: datetime,
        shard_id: str,
    ) -> None:
        slot_key = (task_name, slot_cutoff.isoformat())
        if self._active_equity_slot != slot_key or shard_id == "G1":
            reset = getattr(self.provider, "reset_collection_run", None)
            if callable(reset):
                reset()
            self._active_equity_slot = slot_key
        self.history_admission.reset_run()

    def _skipped_equity_shard_payload(
        self,
        task: CollectionTask,
        *,
        slot_cutoff: datetime,
        shard_id: str,
        reason: str,
        warning: str | None = None,
    ) -> dict[str, object]:
        params = dict(task.params or {})
        shard_symbols = [str(symbol).strip().upper() for symbol in params.get("shard_symbols", [])]
        minute_policy = build_minute_timestamp_policy(
            str(params.get("minute_timestamp_role") or "end"),
            params.get("minute_timestamp_verification"),
            market_timezone=self.config.market_timezone,
        )
        return {
            "schema_version": "moomoo-intraday-equities-v1",
            "provider": "moomoo_openapi",
            "captured_at": _aware_utc(self.clock()).isoformat(),
            "trading_date": slot_cutoff.astimezone(
                ZoneInfo(self.config.market_timezone)
            ).date().isoformat(),
            "scheduled_at": slot_cutoff.isoformat(),
            "evidence_cutoff": slot_cutoff.isoformat(),
            "minute_timestamp_role": minute_policy["role"],
            "minute_timestamp_policy": minute_policy,
            "slot_index": int(params.get("slot_index", 0)),
            "shard_id": shard_id,
            "requested_symbols": list(task.symbols),
            "quote_symbols": list(task.symbols),
            "minute_symbols": shard_symbols,
            "quotes": [],
            "unavailable_quote_symbols": list(task.symbols) if shard_id == "G1" else [],
            "minute_bars": [],
            "invalid_minute_bars": [],
            "minute_bar_unavailable_symbols": shard_symbols,
            "quality_summary": {
                "complete": False,
                "status": "partial",
                "skip_reason": reason,
                "unavailable_quote_symbols": list(task.symbols) if shard_id == "G1" else [],
                "unavailable_minute_symbols": shard_symbols,
                "quality_warnings": [warning] if warning else [],
                "evidence_cutoff": slot_cutoff.isoformat(),
                "minute_timestamp_role": minute_policy["role"],
                "minute_timestamp_policy": minute_policy,
            },
            "slot_shard_status": reason,
            "provider_raw": {"market_snapshot_batches": [], "history_kline_batches": []},
        }

    def _finalize_equity_slot(
        self,
        task: CollectionTask,
        execution_scheduled_at: datetime,
    ) -> dict[str, object]:
        params = dict(task.params or {})
        source_task_name = str(params.get("source_task_name") or "").strip()
        slot_cutoff = _parse_datetime(params.get("slot_cutoff_at"), self.config.market_timezone)
        if not source_task_name or slot_cutoff is None:
            raise ValueError("equity slot finalizer requires source task and cutoff")
        trading_date = slot_cutoff.astimezone(
            ZoneInfo(self.config.market_timezone)
        ).date().isoformat()
        maintenance_pending = self._equity_maintenance_pending(trading_date)
        shard_symbols = normalize_equity_shard_symbols(
            params.get("equity_shard_symbols"),
            allowed_symbols=task.symbols,
        )
        capacity = self.store.equity_history.measure_capacity()
        capacity_policy = self.store.equity_history.capacity_policy(
            capacity,
            projected_raw_bytes=8192,
        )
        if capacity_policy["skip_all_equities"]:
            return {
                "scheduled_at": execution_scheduled_at.isoformat(),
                "contract": None,
                "completed": True,
                "terminal": True,
                "skipped_capacity": True,
                "capacity_policy": capacity_policy,
                "maintenance_pending": maintenance_pending,
            }

        shard_contracts: dict[str, dict[str, Any]] = {}
        for shard_id in ("G1", "G2", "G3"):
            capture_id = _equity_shard_capture_id(source_task_name, slot_cutoff, shard_id)
            stored = self.store.read_raw(capture_id)
            if stored is None:
                continue
            contract = json.loads(str(stored["payload_json"]))
            if isinstance(contract, dict) and isinstance(contract.get("data"), dict):
                shard_contracts[shard_id] = contract

        unavailable_quotes: list[str] = list(task.symbols)
        unavailable_minutes: list[str] = []
        warnings: list[str] = []
        references: list[dict[str, Any]] = []
        reference_ids: set[str] = set()
        request_summaries: dict[str, dict[str, Any]] = {}
        minute_bars_returned = 0
        invalid_minute_bars = 0
        shard_coverage: dict[str, dict[str, object]] = {}
        shard_complete: dict[str, bool] = {}
        minute_policy = build_minute_timestamp_policy(
            str(params.get("minute_timestamp_role") or "end"),
            params.get("minute_timestamp_verification"),
            market_timezone=self.config.market_timezone,
        )

        for shard_id in ("G1", "G2", "G3"):
            contract = shard_contracts.get(shard_id)
            if contract is None:
                unavailable_minutes.extend(shard_symbols[shard_id])
                shard_complete[shard_id] = False
                shard_coverage[shard_id] = {
                    "status": "skipped_window_expired",
                    "capture_id": None,
                    "requested_symbols": shard_symbols[shard_id],
                    "returned_bar_count": 0,
                    "unavailable_symbols": shard_symbols[shard_id],
                }
                warnings.append(f"{shard_id} 未在其窗口内完成，未使用旧槽数据补齐。")
                continue

            data = contract["data"]
            quality = data.get("quality_summary")
            quality = quality if isinstance(quality, dict) else {}
            complete = bool(quality.get("complete"))
            if shard_id == "G1":
                unavailable = data.get("unavailable_quote_symbols")
                unavailable_quotes = (
                    [str(symbol) for symbol in unavailable]
                    if isinstance(unavailable, list)
                    else []
                )
            data_refs = data.get("equity_data_refs")
            data_refs = data_refs if isinstance(data_refs, list) else []
            refs_valid = True
            unavailable_ref_symbols: list[str] = []
            for reference in data_refs:
                if not isinstance(reference, dict):
                    refs_valid = False
                    warnings.append(f"{shard_id} 引用格式无效，不能发布此分片。")
                    continue
                if reference.get("status") == "available":
                    resolved = self.store.equity_history.resolve_ref(reference)
                    if not resolved.get("available"):
                        refs_valid = False
                        warnings.append(
                            f"{shard_id} 引用不可用：{resolved.get('reason') or 'unknown'}。"
                        )
                elif (
                    reference.get("kind") == "bars"
                    and reference.get("timeframe") == "1m"
                    and reference.get("status") == "unavailable"
                ):
                    refs_valid = False
                    unavailable_ref_symbols.extend(
                        str(symbol) for symbol in reference.get("symbols", [])
                    )
                reference_id = str(reference.get("ref_id") or "")
                if reference_id and reference_id not in reference_ids:
                    references.append(reference)
                    reference_ids.add(reference_id)
            if data.get("schema_version") != EQUITY_REFS_SCHEMA:
                refs_valid = False
                warnings.append(f"{shard_id} 不是 refs-v1 分片；未读取旧版内联数据。")
            if shard_id == "G1":
                quote_ref = next(
                    (
                        reference
                        for reference in data_refs
                        if isinstance(reference, dict) and reference.get("kind") == "quotes"
                    ),
                    None,
                )
                if quote_ref is None:
                    unavailable_quotes = list(task.symbols)
                else:
                    quote_symbols = {
                        str(symbol) for symbol in quote_ref.get("symbols", [])
                    }
                    unavailable_quotes = list(
                        dict.fromkeys(
                            [*unavailable_quotes, *sorted(set(task.symbols) - quote_symbols)]
                        )
                    )
                    if quote_ref.get("status") == "available":
                        quote_result = self.store.equity_history.resolve_ref(quote_ref)
                        if not quote_result.get("available"):
                            unavailable_quotes = list(task.symbols)
                            refs_valid = False
                            warnings.append(
                                "G1 quote ref 不可用；未使用旧槽报价补齐。"
                            )
                    else:
                        unavailable_quotes = list(
                            dict.fromkeys([*unavailable_quotes, *task.symbols])
                        )
            unavailable = data.get("minute_bar_unavailable_symbols")
            unavailable_symbols = (
                [str(symbol) for symbol in unavailable] if isinstance(unavailable, list) else []
            )
            unavailable_symbols = list(
                dict.fromkeys([*unavailable_symbols, *unavailable_ref_symbols])
            )
            unavailable_minutes.extend(unavailable_symbols)
            shard_returned = _as_nonnegative_int(quality.get("minute_bars_returned"))
            shard_invalid = _as_nonnegative_int(quality.get("invalid_minute_bars"))
            minute_bars_returned += shard_returned
            invalid_minute_bars += shard_invalid
            request_summary = data.get("provider_request_summary")
            if isinstance(request_summary, dict):
                request_summaries[shard_id] = request_summary
            shard_policy = data.get("minute_timestamp_policy")
            if shard_id == "G1" and isinstance(shard_policy, dict):
                minute_policy = shard_policy
            warnings.extend(str(item) for item in quality.get("quality_warnings", []) if item)
            complete = complete and refs_valid
            shard_complete[shard_id] = complete
            shard_coverage[shard_id] = {
                "status": str(data.get("slot_shard_status") or quality.get("status") or "partial"),
                "capture_id": contract.get("capture_id"),
                "requested_symbols": shard_symbols[shard_id],
                "returned_bar_count": shard_returned,
                "invalid_bar_count": shard_invalid,
                "unavailable_symbols": unavailable_symbols,
            }

        unavailable_minutes = list(dict.fromkeys(unavailable_minutes))
        slot_complete = all(shard_complete.get(shard_id, False) for shard_id in ("G1", "G2", "G3"))
        all_minute_symbols = [
            symbol for shard_id in ("G1", "G2", "G3") for symbol in shard_symbols[shard_id]
        ]
        if unavailable_quotes:
            warnings.append("G1 报价覆盖不完整；未使用旧槽快照补齐。")
        payload: dict[str, object] = {
            "schema_version": EQUITY_REFS_SCHEMA,
            "provider": "moomoo_openapi",
            "captured_at": _aware_utc(self.clock()).isoformat(),
            "trading_date": slot_cutoff.astimezone(
                ZoneInfo(self.config.market_timezone)
            ).date().isoformat(),
            "scheduled_at": slot_cutoff.isoformat(),
            "evidence_cutoff": slot_cutoff.isoformat(),
            "minute_timestamp_role": minute_policy.get("role", "end"),
            "minute_timestamp_policy": minute_policy,
            "slot_index": int(params.get("slot_index", 0)),
            "requested_symbols": list(task.symbols),
            "quote_symbols": list(task.symbols),
            "minute_symbols": all_minute_symbols,
            "group_by_symbol": self._business_group_by_symbol(params, task.symbols),
            "display_group_by_symbol": dict(
                params.get("display_group_by_symbol") or {}
            ),
            "pool_plan_id": params.get("pool_plan_id"),
            "pool_revision": params.get("pool_revision"),
            "unavailable_quote_symbols": unavailable_quotes,
            "minute_bar_unavailable_symbols": unavailable_minutes,
            "equity_data_refs": references,
            "storage_capacity": capacity,
            "storage_capacity_policy": capacity_policy,
            "provider_request_summary": request_summaries,
            "maintenance_pending": maintenance_pending,
            "quality_summary": {
                "complete": slot_complete and not unavailable_quotes and not unavailable_minutes,
                "status": "ok" if slot_complete and not unavailable_quotes and not unavailable_minutes else "partial",
                "quote_coverage": (
                    (len(task.symbols) - len(unavailable_quotes)) / len(task.symbols)
                    if task.symbols
                    else 1.0
                ),
                "minute_symbols_requested": len(all_minute_symbols),
                "minute_symbols_admitted": len(all_minute_symbols) - len(unavailable_minutes),
                "minute_bars_returned": minute_bars_returned,
                "invalid_minute_bars": invalid_minute_bars,
                "unavailable_quote_symbols": unavailable_quotes,
                "unavailable_minute_symbols": unavailable_minutes,
                "quality_warnings": warnings,
                "evidence_cutoff": slot_cutoff.isoformat(),
                "minute_timestamp_role": minute_policy.get("role", "end"),
                "minute_timestamp_policy": minute_policy,
            },
            "slot_coverage": {
                "complete": slot_complete,
                "expected_shards": ["G1", "G2", "G3"],
                "completed_shards": [
                    shard_id for shard_id in ("G1", "G2", "G3") if shard_complete.get(shard_id)
                ],
                "partial_shards": [
                    shard_id
                    for shard_id in ("G1", "G2", "G3")
                    if shard_id in shard_contracts and not shard_complete.get(shard_id)
                ],
                "missing_shards": [
                    shard_id for shard_id in ("G1", "G2", "G3") if shard_id not in shard_contracts
                ],
                "shards": shard_coverage,
            },
        }
        contract = capture_contract(
            kind="capture_intraday_equities",
            payload=payload,
            requested_symbols=list(task.symbols),
            task_name=source_task_name,
            scheduled_at=slot_cutoff,
            trading_date=slot_cutoff.astimezone(ZoneInfo(self.config.market_timezone))
            .date()
            .isoformat(),
        )
        completed = bool(payload["quality_summary"]["complete"])
        saved = self.store.write_scheduled_raw(
            source_task_name,
            slot_cutoff,
            str(contract["capture_id"]),
            "capture_intraday_equities",
            contract,
            schema_version=str(contract["schema_version"]),
            completed=completed,
            equity_data_refs=references,
            capture_trading_date=str(contract["trading_date"]),
        )
        if not saved:
            stored = self.store.read_scheduled_raw(source_task_name, slot_cutoff)
            if stored is not None and isinstance(stored.get("contract"), dict):
                contract = stored["contract"]
                completed = bool(stored["completed"])
        return {
            "scheduled_at": execution_scheduled_at.isoformat(),
            "contract": contract,
            "completed": completed,
            "terminal": True,
            "maintenance_pending": maintenance_pending,
        }

    def _maintain_equity_history(
        self,
        task: CollectionTask,
        scheduled_at: datetime,
    ) -> dict[str, object]:
        market_timezone = ZoneInfo(self.config.market_timezone)
        trading_date = scheduled_at.astimezone(market_timezone).date().isoformat()
        deadline = self._equity_maintenance_deadline(trading_date)
        self.store.set_meta("equity_retention_pending_date", trading_date)
        self.store.set_meta("equity_retention_pending_reason", "maintenance_running")

        if deadline is None:
            return self._equity_maintenance_failure(
                scheduled_at,
                trading_date,
                reason_code="maintenance_session_unavailable",
                reason="exchange session open is unavailable",
            )

        deadline_check = lambda: _aware_utc(self.clock()).astimezone(market_timezone) >= deadline
        if deadline_check():
            return self._equity_maintenance_failure(
                scheduled_at,
                trading_date,
                reason_code="maintenance_window_closed",
                reason="equity maintenance must start and finish before the final 30 minutes",
            )

        try:
            summary = self._apply_equity_retention_once(
                trading_date,
                enabled=True,
                deadline_check=deadline_check,
            )
        except CleanupDeadlineExceeded as exc:
            logger.warning("equity history maintenance deadline exceeded: task=%s", task.name)
            return self._equity_maintenance_failure(
                scheduled_at,
                trading_date,
                reason_code="maintenance_deadline_exceeded",
                reason=str(exc),
                rolled_back=True,
            )
        except Exception as exc:
            logger.exception("equity history maintenance failed: task=%s", task.name)
            return self._equity_maintenance_failure(
                scheduled_at,
                trading_date,
                reason_code="maintenance_cleanup_failed",
                reason=str(exc),
                rolled_back=True,
            )

        self.store.delete_meta("equity_retention_pending_date")
        self.store.delete_meta("equity_retention_pending_reason")
        return {
            "scheduled_at": scheduled_at.isoformat(),
            "contract": None,
            "completed": True,
            "terminal": True,
            "pending": False,
            "maintenance_pending": False,
            "trading_date": trading_date,
            "storage_retention": summary or {"enabled": True, "applied": False},
        }

    def _equity_maintenance_failure(
        self,
        scheduled_at: datetime,
        trading_date: str,
        *,
        reason_code: str,
        reason: str,
        rolled_back: bool = False,
    ) -> dict[str, object]:
        self.store.set_meta("equity_retention_pending_date", trading_date)
        self.store.set_meta("equity_retention_pending_reason", reason)
        return {
            "scheduled_at": scheduled_at.isoformat(),
            "contract": None,
            "completed": False,
            "terminal": False,
            "pending": True,
            "maintenance_pending": True,
            "rolled_back": rolled_back,
            "reason_code": reason_code,
            "warning": reason,
            "trading_date": trading_date,
        }

    def _equity_maintenance_deadline(self, trading_date: str) -> datetime | None:
        session_date = date.fromisoformat(trading_date)
        opened = exchange_session_time_calendar(self.config.exchange_calendar).session_open(
            session_date
        )
        if opened is None:
            return None
        if hasattr(opened, "to_pydatetime"):
            opened = opened.to_pydatetime()
        if not isinstance(opened, datetime):
            return None
        market_timezone = ZoneInfo(self.config.market_timezone)
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=market_timezone)
        return opened.astimezone(market_timezone) - timedelta(minutes=30)

    def _equity_maintenance_pending(self, trading_date: str) -> bool:
        last_applied_date = self.store.get_meta("equity_retention_last_applied_date")
        if not last_applied_date:
            return True
        try:
            normalized_last_date = date.fromisoformat(last_applied_date).isoformat()
            normalized_trading_date = date.fromisoformat(trading_date).isoformat()
        except ValueError:
            return True
        return normalized_last_date < normalized_trading_date

    def _apply_equity_retention_once(
        self,
        trading_date: str,
        *,
        enabled: bool,
        deadline_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any] | None:
        if not enabled:
            return None
        if deadline_check is not None and deadline_check():
            raise CleanupDeadlineExceeded("equity cleanup deadline exceeded")
        current_date = date.fromisoformat(trading_date).isoformat()
        last_applied_date = self.store.get_meta("equity_retention_last_applied_date")
        if last_applied_date:
            try:
                normalized_last_date = date.fromisoformat(last_applied_date).isoformat()
            except ValueError as exc:
                raise RuntimeError("stored equity retention date is invalid") from exc
            if normalized_last_date >= current_date:
                return {
                    "enabled": True,
                    "applied": False,
                    "reason": "already_applied_or_older_session",
                    "trading_date": current_date,
                }

        if deadline_check is None:
            plan = self.store.equity_history.cleanup_expired(current_date, dry_run=True)
        else:
            plan = self.store.equity_history.cleanup_expired(
                current_date,
                dry_run=True,
                deadline_check=deadline_check,
            )
        if not bool(plan.get("dry_run")):
            raise RuntimeError("equity retention requires a dry-run cleanup plan")
        if bool(plan.get("blocked")):
            raise RuntimeError("equity retention is blocked by an invalid active reference")
        before_commit = None
        if deadline_check is not None:
            def persist_last_applied_date() -> None:
                self.store.connection.execute(
                    "INSERT INTO collector_meta(key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    ("equity_retention_last_applied_date", current_date),
                )

            before_commit = persist_last_applied_date
        if deadline_check is None:
            applied = self.store.equity_history.apply_cleanup(plan)
            self.store.set_meta("equity_retention_last_applied_date", current_date)
        else:
            applied = self.store.equity_history.apply_cleanup(
                plan,
                deadline_check=deadline_check,
                before_commit=before_commit,
            )
        return {
            "enabled": True,
            "applied": True,
            "trading_date": current_date,
            "deleted": dict(applied.get("deleted") or {}),
        }

    def _reset_collection_run(self) -> None:
        self._active_equity_slot = None
        reset = getattr(self.provider, "reset_collection_run", None)
        if callable(reset):
            reset()
        self.history_admission.reset_run()

    @staticmethod
    def _operation_completed(operation: str, payload: dict[str, object]) -> bool:
        normalized = operation.strip()
        if normalized in {"capture_intraday", "capture_intraday_equities"}:
            summary = payload.get("quality_summary")
            return bool(summary.get("complete")) if isinstance(summary, dict) else False
        if normalized in {"capture_session_close", "capture_eod_options"}:
            return bool(payload.get("complete"))
        return True

    def _collect(self, task: CollectionTask, scheduled_at: datetime) -> dict[str, object]:
        operation = task.operation.strip()
        params = dict(task.params or {})
        if operation in {"instrument_cards", "options_snapshot"}:
            result = getattr(self.provider, operation)(list(task.symbols), **params)
        elif operation == "capital_flow_day":
            trading_date = scheduled_at.astimezone(ZoneInfo(self.config.market_timezone)).date()
            result = {
                "trading_date": trading_date.isoformat(),
                "observations": [
                    self.provider.capital_flow_day(symbol, trading_date) for symbol in task.symbols
                ],
            }
        elif operation == "capture_intraday":
            result = self.provider.capture_intraday(symbols=list(task.symbols), **params)
        elif operation == "capture_intraday_equities":
            windowed_shard = bool(params.get("windowed_equity_shard"))
            slot_cutoff = (
                _parse_datetime(params.get("slot_cutoff_at"), self.config.market_timezone)
                if windowed_shard
                else scheduled_at
            )
            if slot_cutoff is None:
                raise ValueError("windowed equity shard is missing its slot cutoff")
            trading_date = slot_cutoff.astimezone(
                ZoneInfo(self.config.market_timezone)
            ).date()
            # The scheduler owns the capture slot. Task configuration may set
            # collection options, but it cannot move evidence to another date
            # or cutoff.
            if windowed_shard:
                internal_keys = {
                    "windowed_equity_shard",
                    "source_task_name",
                    "shard_id",
                    "shard_symbols",
                    "equity_shard_symbols",
                    "slot_cutoff_at",
                    "shard_window_end_at",
                    "request_budget_deadline_at",
                    "skip_reason",
                    "capture_quotes",
                    "groups",
                    "display_group_by_symbol",
                    "pool_plan_id",
                    "pool_revision",
                    "equity_retention_apply",
                }
                params = {key: value for key, value in params.items() if key not in internal_keys}
                original = dict(task.params or {})
                params["quote_symbols"] = list(task.symbols)
                params["minute_symbols"] = list(original.get("shard_symbols", []))
                params["group_by_symbol"] = self._business_group_by_symbol(
                    original, task.symbols
                )
                params["capture_quotes"] = bool(original.get("capture_quotes"))
                params["shard_id"] = str(original.get("shard_id") or "")
                params["shard_symbols"] = list(original.get("shard_symbols", []))
                params["request_budget_deadline_at"] = _parse_datetime(
                    original.get("request_budget_deadline_at"), self.config.market_timezone
                )
                params["request_timeout_seconds"] = float(
                    original.get("request_timeout_seconds", 3.0)
                )
                params["minute_timestamp_role"] = str(
                    original.get("minute_timestamp_role") or "end"
                )
                params["minute_timestamp_verification"] = original.get(
                    "minute_timestamp_verification"
                )
            params["trading_date"] = trading_date
            params["scheduled_at"] = slot_cutoff
            params["evidence_cutoff"] = slot_cutoff
            result = self.provider.capture_intraday_equities(
                symbols=list(task.symbols),
                **params,
            )
        elif operation in {"capture_session_close", "capture_eod_options"}:
            trading_date = scheduled_at.astimezone(
                ZoneInfo(self.config.market_timezone)
            ).date()
            result = getattr(self.provider, operation)(
                symbols=list(task.symbols), trading_date=trading_date, **params
            )
        else:
            raise ValueError(f"unsupported collection operation: {operation}")
        if not isinstance(result, dict):
            raise TypeError(f"collection operation {operation} must return a mapping")
        return result

    @staticmethod
    def _business_group_by_symbol(
        params: dict[str, Any],
        allowed_symbols: tuple[str, ...] | list[str],
    ) -> dict[str, str]:
        groups = params.get("groups")
        if not isinstance(groups, dict):
            return {}
        plan = build_intraday_equity_plan(
            groups,
            slot_index=int(params.get("slot_index", 0)),
            allowed_symbols=allowed_symbols,
        )
        return dict(plan["group_by_symbol"])

    def _quota_snapshot(self) -> dict[str, object]:
        reader = getattr(self.provider, "quota_snapshot", None)
        if not callable(reader):
            return {"available": False, "warning": "quota reader unavailable"}
        result = reader()
        return result if isinstance(result, dict) else {"available": False}

    def close(self) -> None:
        try:
            close = getattr(self.provider, "close", None)
            if callable(close):
                close()
        finally:
            self.coordinator.close()
            self.store.close()
            if self.intraday_store is not None:
                self.intraday_store.close()


def _parse_datetime(value: object, timezone: str) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
    return parsed


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _as_nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _provider_request_summary(value: object) -> dict[str, object]:
    raw = value if isinstance(value, dict) else {}
    snapshot_batches = raw.get("market_snapshot_batches")
    history_batches = raw.get("history_kline_batches")
    snapshot_batches = snapshot_batches if isinstance(snapshot_batches, list) else []
    history_batches = history_batches if isinstance(history_batches, list) else []

    def _count_field(batches: list[Any], field: str) -> int:
        return sum(
            1
            for batch in batches
            if isinstance(batch, dict) and bool(batch.get(field))
        )

    return {
        "market_snapshot_batch_count": len(snapshot_batches),
        "market_snapshot_error_count": _count_field(snapshot_batches, "error"),
        "history_kline_batch_count": len(history_batches),
        "history_kline_error_count": _count_field(history_batches, "error"),
        "history_kline_skipped_count": _count_field(history_batches, "skipped_reason"),
    }


def _add_unavailable_minutes(
    payload: dict[str, object],
    symbols: list[str],
    reason: str,
) -> None:
    existing = payload.get("minute_bar_unavailable_symbols")
    unavailable = [str(item) for item in existing] if isinstance(existing, list) else []
    unavailable = list(dict.fromkeys([*unavailable, *symbols]))
    payload["minute_bar_unavailable_symbols"] = unavailable
    quality_value = payload.get("quality_summary")
    quality = dict(quality_value) if isinstance(quality_value, dict) else {}
    quality["complete"] = False
    quality["status"] = "partial"
    quality["unavailable_minute_symbols"] = unavailable
    warnings = [str(item) for item in quality.get("quality_warnings", []) if item]
    warnings.append(f"容量接近上限，跳过 D 组分钟采集：{', '.join(symbols)}。")
    quality["quality_warnings"] = warnings
    payload["quality_summary"] = quality
    payload["capacity_skip_reason"] = reason


def _equity_shard_capture_id(task_name: str, slot_cutoff: datetime, shard_id: str) -> str:
    identity = "\0".join(
        (task_name, _aware_utc(slot_cutoff).isoformat(), shard_id.upper())
    ).encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()
    return f"urus.equity-shard.v1:{digest}"


def _equity_capture_id(task_name: str, scheduled_at: datetime) -> str:
    identity = "\0".join((task_name, _aware_utc(scheduled_at).isoformat())).encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()
    return f"urus.equity-capture.v1:{digest}"
