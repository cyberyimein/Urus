from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
import math
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import as_utc, utc_now
from app.models.market_data_capacity import (
    HistoryCollectionStateModel,
    HistoryQuotaSnapshotModel,
)
from app.repositories.daily_evidence import DailyEvidenceRepository


HISTORY_PROVIDER = "moomoo_openapi"
HISTORY_QUOTA_KIND = "history_candlestick"
SNAPSHOT_ID = f"{HISTORY_PROVIDER}:{HISTORY_QUOTA_KIND}"


class QuotaReader(Protocol):
    def __call__(self) -> dict[str, object]: ...


def normalize_symbol(value: object) -> str:
    text = str(value or "").strip().upper()
    return text.split(".")[-1] if "." in text else text


def history_symbols_from_items(items: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for item in items:
        if isinstance(item, dict):
            symbol = item.get("symbol")
            enabled = bool(item.get("enabled", True))
            collection = item.get("collection") or {}
            daily_history = bool(collection.get("daily_history", True))
        else:
            symbol = getattr(item, "symbol", None)
            enabled = bool(getattr(item, "enabled", True))
            collection = getattr(item, "collection", None)
            daily_history = bool(getattr(collection, "daily_history", True))
        normalized = normalize_symbol(symbol)
        if normalized and enabled and daily_history and normalized not in result:
            result.append(normalized)
    return result


def _integer(value: object) -> int | None:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _walk_detail(value: object) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in {"code", "symbol", "stock_code", "security_code"}:
                if isinstance(child, str):
                    yield normalize_symbol(child)
                elif isinstance(child, (list, tuple, set)):
                    for item in child:
                        if isinstance(item, str):
                            yield normalize_symbol(item)
            yield from _walk_detail(child)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _walk_detail(item)
    elif isinstance(value, str) and ("." in value or value.isalpha()):
        candidate = normalize_symbol(value)
        if candidate and len(candidate) <= 16:
            yield candidate


def detail_symbols(value: object) -> set[str]:
    return {item for item in _walk_detail(value) if item}


def normalize_quota_payload(value: object) -> dict[str, object]:
    raw = value if isinstance(value, dict) else {}
    history = raw.get("history") if isinstance(raw.get("history"), dict) else raw
    history = history if isinstance(history, dict) else {}
    used = _integer(history.get("used_quota", history.get("usedQuota", history.get("used"))))
    remain = _integer(
        history.get("remain_quota", history.get("remainQuota", history.get("remain")))
    )
    details = history.get("detail_list", history.get("detailList", []))
    explicit_window = history.get("window_symbols", raw.get("window_symbols", []))
    explicit_symbols = (
        {normalize_symbol(item) for item in explicit_window if normalize_symbol(item)}
        if isinstance(explicit_window, (list, tuple, set))
        else set()
    )
    available = bool(raw.get("available", True)) and (used is not None or remain is not None)
    total = _integer(history.get("total"))
    if total is None and used is not None and remain is not None:
        total = used + remain
    return {
        "available": available,
        "used": used,
        "remain": remain,
        "total": total,
        "detail": details,
        "window_symbols": sorted(detail_symbols(details) | explicit_symbols),
        "raw": raw,
        "warning": raw.get("warning"),
        "snapshot_id": raw.get("snapshot_id"),
        "captured_at": raw.get("captured_at"),
        "expires_at": raw.get("expires_at"),
    }


class HistoryCapacityService:
    """Plan history collection and project one durable state per symbol."""

    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings
        self.provider = HISTORY_PROVIDER
        self.reserve_ratio = max(
            0.0, float(getattr(settings, "moomoo_history_quota_reserve_ratio", 0.20))
        )
        self.reserve_absolute = max(
            0, int(getattr(settings, "moomoo_history_quota_reserve_absolute", 10))
        )
        self.snapshot_ttl_seconds = max(
            30.0, float(getattr(settings, "moomoo_capacity_snapshot_ttl_seconds", 300.0))
        )
        self.minimum_bar_count = max(1, int(getattr(settings, "daily_min_history_bars", 260)))

    def capture(
        self, reader: QuotaReader | None, *, now: datetime | None = None
    ) -> dict[str, object]:
        observed_at = as_utc(now or utc_now())
        if reader is None:
            payload = normalize_quota_payload(
                {"available": False, "warning": "Moomoo OpenD 额度读取器未配置。"}
            )
        else:
            try:
                payload = normalize_quota_payload(reader())
            except Exception as exc:
                payload = normalize_quota_payload(
                    {"available": False, "warning": f"Moomoo 额度查询不可用：{exc}"}
                )
        expires_at = observed_at + timedelta(seconds=self.snapshot_ttl_seconds)
        model = self.session.get(HistoryQuotaSnapshotModel, SNAPSHOT_ID)
        if model is None:
            model = HistoryQuotaSnapshotModel(
                id=SNAPSHOT_ID,
                provider=self.provider,
                quota_kind=HISTORY_QUOTA_KIND,
                observed_at=observed_at,
            )
            self.session.add(model)
        model.available = bool(payload.get("available"))
        model.used_quota = payload.get("used") if isinstance(payload.get("used"), int) else None
        model.remain_quota = (
            payload.get("remain") if isinstance(payload.get("remain"), int) else None
        )
        model.total_quota = payload.get("total") if isinstance(payload.get("total"), int) else None
        model.detail_json = {
            "detail": payload.get("detail", []),
            "window_symbols": payload.get("window_symbols", []),
            "raw": payload.get("raw", {}),
        }
        model.quality_status = "ok" if payload.get("available") else "unavailable"
        model.warning = str(payload.get("warning")) if payload.get("warning") else None
        model.observed_at = observed_at
        model.expires_at = expires_at
        self.session.flush()
        payload.update(
            snapshot_id=model.id,
            captured_at=observed_at.isoformat(),
            expires_at=expires_at.isoformat(),
        )
        return payload

    def latest_snapshot(self) -> HistoryQuotaSnapshotModel | None:
        return self.session.get(HistoryQuotaSnapshotModel, SNAPSHOT_ID)

    def build_plan(
        self,
        items: Iterable[Any],
        *,
        universe_content_sha256: str = "",
        universe_version_id: str | None = None,
        quota: dict[str, object] | None = None,
        reader: QuotaReader | None = None,
        now: datetime | None = None,
        persist: bool = True,
    ) -> dict[str, object]:
        del persist
        current = as_utc(now or utc_now())
        materialized = list(items)
        symbols = history_symbols_from_items(materialized)
        if quota is not None:
            normalized_quota = normalize_quota_payload(quota)
        elif reader is not None:
            normalized_quota = self.capture(reader, now=current)
        else:
            normalized_quota = self._from_latest(current)

        target_date = self._target_date(current)
        bars = DailyEvidenceRepository(self.session).bars(
            symbols, through_date=target_date, cutoff_time=current
        )
        window = {
            normalize_symbol(item)
            for item in normalized_quota.get("window_symbols", [])
            if normalize_symbol(item)
        }
        remain = normalized_quota.get("remain")
        remain = remain if isinstance(remain, int) else None
        total = normalized_quota.get("total")
        total = total if isinstance(total, int) else None
        reserve = self._reserve(total)
        slots_left = max(0, remain - reserve) if remain is not None and reserve is not None else 0
        item_by_symbol = {
            normalize_symbol(
                item.get("symbol") if isinstance(item, dict) else getattr(item, "symbol", "")
            ): item
            for item in materialized
        }
        plan_items: list[dict[str, object]] = []
        candidates: list[tuple[tuple[int, int, str], dict[str, object]]] = []
        for position, symbol in enumerate(symbols):
            rows = bars.get(symbol, [])
            latest = rows[-1].bar_date if rows else None
            cache_ready = bool(
                rows
                and len(rows) >= self.minimum_bar_count
                and latest is not None
                and latest >= target_date
            )
            if cache_ready:
                decision, cost, reason = "cache_hit", 0, None
            elif symbol in window:
                decision, cost, reason = "admitted", 0, None
            else:
                decision, cost, reason = "pending_quota", 1, "history_quota_reserve"
            entry: dict[str, object] = {
                "symbol": symbol,
                "cache_state": "ready" if cache_ready else "stale" if rows else "missing",
                "bar_count": len(rows),
                "latest_bar_date": latest.isoformat() if latest else None,
                "quota_cost": cost,
                "decision": decision,
                "reason_code": reason,
                "required_through_date": target_date.isoformat(),
            }
            plan_items.append(entry)
            if cost:
                candidates.append((self._priority(item_by_symbol.get(symbol), position, symbol), entry))

        if normalized_quota.get("available") and reserve is not None:
            for _, entry in sorted(candidates, key=lambda candidate: candidate[0]):
                if slots_left <= 0:
                    break
                entry["decision"] = "admitted"
                entry["reason_code"] = None
                slots_left -= 1

        pending_count = sum(item["decision"] == "pending_quota" for item in plan_items)
        warnings: list[str] = []
        if not normalized_quota.get("available"):
            warnings.append(
                str(
                    normalized_quota.get("warning")
                    or "无法读取 OpenD 历史 K 线额度；新增历史采集已暂停。"
                )
            )
        return {
            "schema_version": "urus.market_data_capacity_plan.v1",
            "plan_id": str(uuid4()),
            "provider": self.provider,
            "universe_content_sha256": universe_content_sha256,
            "universe_version_id": universe_version_id,
            "captured_at": normalized_quota.get("captured_at") or current.isoformat(),
            "expires_at": normalized_quota.get("expires_at"),
            "quota": {
                "quality_status": "ok" if normalized_quota.get("available") else "unavailable",
                "used": normalized_quota.get("used"),
                "remain": remain,
                "total": total,
                "reserve": reserve,
                "available_to_spend": slots_left,
                "snapshot_id": normalized_quota.get("snapshot_id"),
                "window_symbols": sorted(window),
            },
            "summary": {
                "desired_history_count": len(symbols),
                "cache_ready_count": sum(item["cache_state"] == "ready" for item in plan_items),
                "zero_cost_refresh_count": sum(
                    item["decision"] == "admitted" and int(item["quota_cost"]) == 0
                    for item in plan_items
                ),
                "new_slot_count": sum(int(item["quota_cost"]) for item in plan_items),
                "admitted_new_slot_count": sum(
                    item["decision"] == "admitted" and int(item["quota_cost"]) == 1
                    for item in plan_items
                ),
                "pending_quota_count": pending_count,
            },
            "symbols": plan_items,
            "warnings": warnings,
        }

    def apply_plan(
        self,
        plan: dict[str, object],
        *,
        universe_version_id: str | None = None,
        run_id: str | None = None,
        now: datetime | None = None,
        reconcile_desired: bool = True,
    ) -> dict[str, dict[str, object]]:
        del run_id
        current = as_utc(now or utc_now())
        quota = plan.get("quota") if isinstance(plan.get("quota"), dict) else {}
        snapshot_id = quota.get("snapshot_id") if isinstance(quota, dict) else None
        desired_symbols: set[str] = set()
        states: dict[str, dict[str, object]] = {}
        for item in plan.get("symbols", []):
            if not isinstance(item, dict):
                continue
            symbol = normalize_symbol(item.get("symbol"))
            if not symbol:
                continue
            desired_symbols.add(symbol)
            state = self._state(symbol)
            if state is None:
                state = HistoryCollectionStateModel(
                    id=str(uuid4()), provider=self.provider, symbol=symbol, updated_at=current
                )
                self.session.add(state)
            state.desired_history = True
            state.universe_version_id = universe_version_id
            state.capacity_snapshot_id = str(snapshot_id) if snapshot_id else None
            state.required_through_date = self._parse_date(item.get("required_through_date"))
            state.minimum_bar_count = self.minimum_bar_count
            state.bar_count = int(item.get("bar_count") or 0)
            state.latest_bar_date = self._parse_date(item.get("latest_bar_date"))
            state.quota_cost = int(item.get("quota_cost") or 0)
            state.updated_at = current
            decision = str(item.get("decision") or "pending_quota")
            if decision == "cache_hit":
                state.access_state = "acquired"
                state.quality_state = "ready"
                state.reason_code = None
                state.message = None
                state.last_success_at = state.last_success_at or current
            elif decision == "admitted":
                state.access_state = "admitted"
                state.quality_state = "ready" if state.bar_count >= self.minimum_bar_count else "unknown"
                state.reason_code = None
                state.message = "历史 K 线已准入，将在本次串行采集中获取。"
            else:
                state.access_state = "pending_quota"
                state.quality_state = "stale" if state.bar_count else "unknown"
                state.reason_code = str(item.get("reason_code") or "history_quota_reserve")
                state.message = "当前历史 K 线额度已保留安全余量；后续每日采集会自动重试。"
                state.first_deferred_at = state.first_deferred_at or current
            states[symbol] = self.serialize_state(state)

        if reconcile_desired:
            rows = list(
                self.session.scalars(
                    select(HistoryCollectionStateModel).where(
                        HistoryCollectionStateModel.provider == self.provider,
                        ~HistoryCollectionStateModel.symbol.in_(desired_symbols or {""}),
                    )
                )
            )
            for state in rows:
                state.desired_history = False
                state.access_state = "disabled"
                state.quality_state = "stale" if state.bar_count else "unknown"
                state.reason_code = "history_disabled"
                state.message = "该标的已关闭每日历史 K 线采集。"
                state.quota_cost = 0
                state.required_through_date = None
                state.updated_at = current
                states[state.symbol] = self.serialize_state(state)
        self.session.flush()
        return states

    def projection(self, *, now: datetime | None = None) -> dict[str, object]:
        current = as_utc(now or utc_now())
        snapshot = self.latest_snapshot()
        rows = list(
            self.session.scalars(
                select(HistoryCollectionStateModel)
                .where(HistoryCollectionStateModel.provider == self.provider)
                .order_by(HistoryCollectionStateModel.symbol.asc())
            )
        )
        warnings: list[str] = []
        if not self.settings.moomoo_enabled:
            capacity: dict[str, object] = {
                "provider": self.provider,
                "enabled": False,
                "quality_status": "disabled",
                "warning": None,
            }
        else:
            capacity = self.serialize_snapshot(snapshot)
            capacity["enabled"] = True
            if snapshot and snapshot.warning:
                warnings.append(snapshot.warning)
            if snapshot and snapshot.expires_at and as_utc(snapshot.expires_at) <= current:
                capacity["quality_status"] = "stale"
                capacity["warning"] = "Moomoo 历史 K 线额度快照已过期，等待下一次刷新。"
                warnings.append(str(capacity["warning"]))
        return {
            "provider": self.provider,
            "captured_at": as_utc(snapshot.observed_at).isoformat() if snapshot else None,
            "capacity": capacity,
            "states": {row.symbol: self.serialize_state(row) for row in rows},
            "warnings": warnings,
            "as_of": current.isoformat(),
        }

    def mark_attempt(self, symbol: str, *, now: datetime | None = None) -> None:
        state = self._state(normalize_symbol(symbol))
        if state is None:
            return
        current = as_utc(now or utc_now())
        state.access_state = "collecting"
        state.last_attempt_at = current
        state.updated_at = current
        self.session.flush()

    def mark_failure(self, symbol: str, message: str, *, now: datetime | None = None) -> None:
        state = self._state(normalize_symbol(symbol))
        if state is None:
            return
        current = as_utc(now or utc_now())
        state.access_state = "retry_wait"
        state.quality_state = "stale" if state.bar_count else "unknown"
        state.reason_code = "history_provider_error"
        state.message = message[:1000]
        state.last_attempt_at = current
        state.updated_at = current
        self.session.flush()

    def serialize_snapshot(self, snapshot: HistoryQuotaSnapshotModel | None) -> dict[str, object]:
        if snapshot is None:
            return {"quality_status": "unavailable", "warning": "尚未读取 OpenD 历史额度。"}
        detail = snapshot.detail_json if isinstance(snapshot.detail_json, dict) else {}
        return {
            "id": snapshot.id,
            "provider": snapshot.provider,
            "quota_kind": snapshot.quota_kind,
            "available": snapshot.available,
            "used": snapshot.used_quota,
            "remain": snapshot.remain_quota,
            "total": snapshot.total_quota,
            "reserve": self._reserve(snapshot.total_quota),
            "detail": detail.get("detail", []),
            "window_symbols": detail.get("window_symbols", []),
            "quality_status": snapshot.quality_status,
            "warning": snapshot.warning,
            "captured_at": as_utc(snapshot.observed_at).isoformat(),
            "expires_at": as_utc(snapshot.expires_at).isoformat() if snapshot.expires_at else None,
        }

    @staticmethod
    def serialize_state(state: HistoryCollectionStateModel) -> dict[str, object]:
        return {
            "symbol": state.symbol,
            "provider": state.provider,
            "access_state": state.access_state,
            "quality_state": state.quality_state,
            "reason_code": state.reason_code,
            "message": state.message,
            "desired_history": state.desired_history,
            "bar_count": state.bar_count,
            "latest_bar_date": state.latest_bar_date.isoformat() if state.latest_bar_date else None,
            "required_through_date": (
                state.required_through_date.isoformat() if state.required_through_date else None
            ),
            "minimum_bar_count": state.minimum_bar_count,
            "quota_cost": state.quota_cost,
            "first_deferred_at": (
                state.first_deferred_at.isoformat() if state.first_deferred_at else None
            ),
            "last_attempt_at": state.last_attempt_at.isoformat() if state.last_attempt_at else None,
            "last_success_at": state.last_success_at.isoformat() if state.last_success_at else None,
            "updated_at": as_utc(state.updated_at).isoformat(),
        }

    def _from_latest(self, now: datetime) -> dict[str, object]:
        snapshot = self.latest_snapshot()
        if snapshot is None or (snapshot.expires_at and as_utc(snapshot.expires_at) < now):
            return {"available": False, "warning": "没有新鲜的 Moomoo 历史 K 线额度快照。"}
        detail = snapshot.detail_json if isinstance(snapshot.detail_json, dict) else {}
        return {
            "available": snapshot.available,
            "used": snapshot.used_quota,
            "remain": snapshot.remain_quota,
            "total": snapshot.total_quota,
            "detail": detail.get("detail", []),
            "window_symbols": detail.get("window_symbols", []),
            "snapshot_id": snapshot.id,
            "captured_at": as_utc(snapshot.observed_at).isoformat(),
            "expires_at": as_utc(snapshot.expires_at).isoformat() if snapshot.expires_at else None,
            "warning": snapshot.warning,
        }

    def _target_date(self, now: datetime) -> date:
        try:
            from app.services.capital_flow import latest_completed_session_date

            return latest_completed_session_date(now, self.settings.market_calendar)
        except Exception:
            return now.date()

    def _state(self, symbol: str) -> HistoryCollectionStateModel | None:
        return self.session.scalar(
            select(HistoryCollectionStateModel).where(
                HistoryCollectionStateModel.provider == self.provider,
                HistoryCollectionStateModel.symbol == symbol,
            )
        )

    def _reserve(self, total: int | None) -> int | None:
        if total is None:
            return None
        return max(self.reserve_absolute, math.ceil(total * self.reserve_ratio))

    @staticmethod
    def _priority(item: Any, position: int, symbol: str) -> tuple[int, int, str]:
        roles = item.get("roles", {}) if isinstance(item, dict) else getattr(item, "roles", None)
        roles = roles or {}
        getter = roles.get if isinstance(roles, dict) else lambda key, default=False: getattr(roles, key, default)
        rank = 0 if getter("market_benchmark", False) else 1 if getter("equity_watchlist", False) else 2 if getter("ai_candidate", False) else 3
        return rank, position, symbol

    @staticmethod
    def _parse_date(value: object) -> date | None:
        if not value:
            return None
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None


class HistoryAdmission:
    """Fail-closed gate immediately before every live history request."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        quota_reader: QuotaReader | None = None,
        universe_version_id: str | None = None,
        run_id: str | None = None,
        rate_limiter: Any | None = None,
    ) -> None:
        del run_id
        self.service = HistoryCapacityService(session, settings)
        self.quota_reader = quota_reader
        self.universe_version_id = universe_version_id
        self.coordinator = rate_limiter
        self._attempted: set[str] = set()
        self._runtime_new_symbols: set[str] = set()

    def bind_quota_reader(self, reader: QuotaReader) -> None:
        self.quota_reader = reader

    def prepare_symbols(
        self, symbols: Iterable[str], *, now: datetime | None = None
    ) -> dict[str, object]:
        if self.coordinator is not None:
            self.coordinator.acquire_collection_lock()
        normalized = list(
            dict.fromkeys(normalize_symbol(symbol) for symbol in symbols if normalize_symbol(symbol))
        )
        items = [
            {"symbol": symbol, "enabled": True, "collection": {"daily_history": True}}
            for symbol in normalized
        ]
        plan = self.service.build_plan(
            items,
            universe_version_id=self.universe_version_id,
            reader=self.quota_reader,
            now=now,
        )
        self.service.apply_plan(
            plan,
            universe_version_id=self.universe_version_id,
            now=now,
            reconcile_desired=False,
        )
        self.service.session.commit()
        return plan

    def acquire(self, symbol: str, *, now: datetime | None = None) -> dict[str, object]:
        normalized = normalize_symbol(symbol)
        current = as_utc(now or utc_now())
        cached = self._cached_bars(normalized, now=current)
        if cached is not None:
            return {
                "admitted": True,
                "access_state": "acquired",
                "cached": True,
                "bars": cached,
            }
        state = self.service._state(normalized)
        if state is None:
            self.prepare_symbols([normalized], now=current)
            state = self.service._state(normalized)
        if state is None or state.access_state in {"pending_quota", "unavailable", "disabled"}:
            return {
                "admitted": False,
                "access_state": "pending_quota",
                "reason_code": state.reason_code if state else "capacity_snapshot_unavailable",
                "message": state.message if state else "无法确认历史 K 线额度，新增采集已暂停。",
            }
        allowed, reason, message = self._runtime_admission(normalized, current)
        if not allowed:
            state.access_state = "pending_quota"
            state.reason_code = reason
            state.message = message
            state.updated_at = current
            self.service.session.commit()
            return {
                "admitted": False,
                "access_state": "pending_quota",
                "reason_code": reason,
                "message": message,
            }
        self.service.mark_attempt(normalized, now=current)
        self.service.session.commit()
        self._attempted.add(normalized)
        return {"admitted": True, "access_state": "collecting", "cached": False}

    def _runtime_admission(
        self, symbol: str, now: datetime
    ) -> tuple[bool, str | None, str | None]:
        if self.quota_reader is None:
            quota = self.service._from_latest(now)
        else:
            quota = self.service.capture(self.quota_reader, now=now)
            self.service.session.commit()
        if not quota.get("available"):
            return (
                False,
                "capacity_snapshot_unavailable",
                str(quota.get("warning") or "无法复核 OpenD 历史 K 线额度，暂不发起请求。"),
            )
        window = {
            normalize_symbol(item)
            for item in quota.get("window_symbols", [])
            if normalize_symbol(item)
        }
        if symbol in window:
            return True, None, None
        remain = quota.get("remain") if isinstance(quota.get("remain"), int) else None
        total = quota.get("total") if isinstance(quota.get("total"), int) else None
        reserve = self.service._reserve(total)
        locally_spent = len(self._runtime_new_symbols - window)
        if remain is not None and reserve is not None and remain - locally_spent > reserve:
            # Quota detail can lag immediately after request_history_kline.
            # Count newly admitted symbols in memory while the host-wide lock
            # is held so a stale provider response cannot overspend the reserve.
            self._runtime_new_symbols.add(symbol)
            return True, None, None
        return (
            False,
            "history_quota_reserve",
            "OpenD 当前剩余额度已达到安全余量，后续每日采集会自动重试。",
        )

    def mark_failure(self, symbol: str, message: str, *, now: datetime | None = None) -> None:
        self.service.mark_failure(symbol, message, now=now)
        self.service.session.commit()

    def release_unfinished(self, *, now: datetime | None = None) -> None:
        current = as_utc(now or utc_now())
        try:
            for symbol in self._attempted:
                state = self.service._state(symbol)
                if state is not None and state.access_state == "collecting":
                    state.access_state = "retry_wait"
                    state.reason_code = "history_not_persisted"
                    state.message = "本次历史 K 线未写入规范化缓存，后续每日采集会重试。"
                    state.updated_at = current
            self.service.session.commit()
        finally:
            if self.coordinator is not None:
                self.coordinator.close()

    def _cached_bars(self, symbol: str, *, now: datetime) -> list[dict[str, object]] | None:
        state = self.service._state(symbol)
        if state is None:
            return None
        target = self.service._target_date(now)
        rows = DailyEvidenceRepository(self.service.session).bars(
            [symbol], through_date=target, cutoff_time=now
        ).get(symbol, [])
        minimum = max(1, int(state.minimum_bar_count or self.service.minimum_bar_count))
        if len(rows) < minimum or rows[-1].bar_date < target:
            return None
        return [
            {
                "date": row.bar_date.isoformat(),
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
                "turnover": row.turnover,
                "turnover_rate": row.turnover_rate,
            }
            for row in rows
        ]
