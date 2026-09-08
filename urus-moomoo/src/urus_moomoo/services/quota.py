from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
import math
from typing import Any, Callable, Protocol
from uuid import uuid4


HISTORY_PROVIDER = "moomoo_openapi"
HISTORY_QUOTA_KIND = "history_candlestick"


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
        value = normalize_symbol(symbol)
        if value and enabled and daily_history and value not in result:
            result.append(value)
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
    assert isinstance(history, dict)
    used = _integer(history.get("used_quota", history.get("usedQuota", history.get("used"))))
    remain = _integer(history.get("remain_quota", history.get("remainQuota", history.get("remain"))))
    details = history.get("detail_list", history.get("detailList", []))
    explicit = history.get("window_symbols", raw.get("window_symbols", []))
    explicit_symbols = (
        {normalize_symbol(item) for item in explicit if normalize_symbol(item)}
        if isinstance(explicit, (list, tuple, set))
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
    }


@dataclass
class HistoryState:
    symbol: str
    desired: bool = True
    access_state: str = "not_requested"
    quality_state: str = "unknown"
    reason_code: str | None = None
    error_message: str | None = None
    attempts: int = 0
    updated_at: str | None = None


@dataclass
class HistoryCapacityService:
    """Quota snapshot, reserve planning and explainable per-symbol state."""

    settings: Any | None = None
    minimum_bar_count: int = 260
    reserve_ratio: float = 0.20
    reserve_absolute: int = 10
    snapshot_ttl_seconds: float = 300.0
    store: Any | None = None
    states: dict[str, HistoryState] = field(default_factory=dict)
    latest_quota: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if self.settings is not None:
            self.minimum_bar_count = max(1, int(getattr(self.settings, "daily_min_history_bars", self.minimum_bar_count)))
            self.reserve_ratio = max(0.0, float(getattr(self.settings, "moomoo_history_quota_reserve_ratio", self.reserve_ratio)))
            self.reserve_absolute = max(0, int(getattr(self.settings, "moomoo_history_quota_reserve_absolute", self.reserve_absolute)))
            self.snapshot_ttl_seconds = max(30.0, float(getattr(self.settings, "moomoo_capacity_snapshot_ttl_seconds", self.snapshot_ttl_seconds)))

    def capture(self, reader: QuotaReader | None, *, now: datetime | None = None) -> dict[str, object]:
        observed = (now or datetime.now(UTC)).astimezone(UTC)
        if reader is None:
            payload = normalize_quota_payload({"available": False, "warning": "quota reader not configured"})
        else:
            try:
                payload = normalize_quota_payload(reader())
            except Exception as exc:
                payload = normalize_quota_payload({"available": False, "warning": f"quota read failed: {exc}"})
        payload["captured_at"] = observed.isoformat()
        payload["expires_at"] = (observed + timedelta(seconds=self.snapshot_ttl_seconds)).isoformat()
        payload["snapshot_id"] = f"{HISTORY_PROVIDER}:{uuid4()}"
        self.latest_quota = payload
        if self.store is not None and hasattr(self.store, "write_quota_snapshot"):
            self.store.write_quota_snapshot(str(payload["snapshot_id"]), payload)
        return payload

    def build_plan(
        self,
        items: Iterable[Any],
        *,
        quota: dict[str, object] | None = None,
        reader: QuotaReader | None = None,
        cached_bars: dict[str, list[dict[str, object]]] | None = None,
        target_date: date | None = None,
        universe_content_sha256: str = "",
        now: datetime | None = None,
    ) -> dict[str, object]:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        normalized = normalize_quota_payload(quota) if quota is not None else (
            self.capture(reader, now=current) if reader is not None else self.latest_quota
        )
        normalized = normalized or normalize_quota_payload({"available": False, "warning": "quota snapshot missing"})
        symbols = history_symbols_from_items(items)
        cached_bars = cached_bars or {}
        remain = normalized.get("remain") if isinstance(normalized.get("remain"), int) else None
        total = normalized.get("total") if isinstance(normalized.get("total"), int) else None
        reserve = self._reserve(total)
        slots = max(0, remain - reserve) if remain is not None and reserve is not None else 0
        window = set(normalized.get("window_symbols", []))
        plan_items: list[dict[str, object]] = []
        candidates: list[dict[str, object]] = []
        for position, symbol in enumerate(symbols):
            rows = cached_bars.get(symbol, [])
            latest = rows[-1].get("date") if rows else None
            ready = len(rows) >= self.minimum_bar_count and (target_date is None or str(latest) >= target_date.isoformat())
            if ready:
                decision, cost, reason = "cache_hit", 0, None
            elif symbol in window:
                decision, cost, reason = "admitted", 0, None
            else:
                decision, cost, reason = "pending_quota", 1, "history_quota_reserve"
            entry = {"symbol": symbol, "cache_state": "ready" if ready else "missing" if not rows else "stale", "bar_count": len(rows), "decision": decision, "quota_cost": cost, "reason_code": reason}
            plan_items.append(entry)
            if cost:
                candidates.append(entry)
            self.states.setdefault(symbol, HistoryState(symbol=symbol))
        if normalized.get("available") and reserve is not None:
            for entry in candidates:
                if slots <= 0:
                    break
                entry.update(decision="admitted", reason_code=None)
                slots -= 1
        for entry in plan_items:
            state = self.states[entry["symbol"]]
            if entry["decision"] == "cache_hit":
                state.access_state, state.quality_state = "acquired", "ready"
            elif entry["decision"] == "admitted":
                state.access_state, state.reason_code = "admitted", None
            else:
                state.access_state, state.reason_code = "pending_quota", str(entry["reason_code"])
        warnings = [] if normalized.get("available") else [str(normalized.get("warning") or "quota unavailable; history admission is fail-closed")]
        return {
            "schema_version": "urus.market_data_capacity_plan.v1",
            "plan_id": str(uuid4()),
            "provider": HISTORY_PROVIDER,
            "universe_content_sha256": universe_content_sha256,
            "quota": {"available": bool(normalized.get("available")), "used": normalized.get("used"), "remain": remain, "total": total, "reserve": reserve, "available_to_spend": slots},
            "symbols": plan_items,
            "summary": {"symbol_count": len(plan_items), "pending_quota_count": sum(x["decision"] == "pending_quota" for x in plan_items)},
            "warnings": warnings,
        }

    def mark_attempt(self, symbol: str, *, now: datetime | None = None) -> None:
        state = self.states.setdefault(normalize_symbol(symbol), HistoryState(normalize_symbol(symbol)))
        state.access_state, state.attempts, state.updated_at = "collecting", state.attempts + 1, (now or datetime.now(UTC)).isoformat()

    def mark_success(self, symbol: str, *, quality_state: str = "ready", now: datetime | None = None) -> None:
        state = self.states.setdefault(normalize_symbol(symbol), HistoryState(normalize_symbol(symbol)))
        state.access_state, state.quality_state, state.reason_code, state.error_message = "acquired", quality_state, None, None
        state.updated_at = (now or datetime.now(UTC)).isoformat()

    def mark_failure(self, symbol: str, message: str, *, now: datetime | None = None) -> None:
        state = self.states.setdefault(normalize_symbol(symbol), HistoryState(normalize_symbol(symbol)))
        state.access_state, state.reason_code, state.error_message = "retry_wait", "provider_error", message
        state.updated_at = (now or datetime.now(UTC)).isoformat()

    def projection(self) -> dict[str, object]:
        return {"schema_version": "urus.history_access_projection.v1", "provider": HISTORY_PROVIDER, "states": [vars(state).copy() for state in self.states.values()]}

    def _reserve(self, total: int | None) -> int | None:
        if total is None:
            return None
        return max(self.reserve_absolute, math.ceil(total * self.reserve_ratio))


class HistoryAdmission:
    """Request-time fail-closed gate; planning alone never authorizes a call."""

    def __init__(self, service: HistoryCapacityService, *, quota_reader: QuotaReader | None = None) -> None:
        self.service = service
        self.quota_reader = quota_reader
        self.scope: set[str] = set()
        self._admitted: set[str] = set()
        self._runtime_new_symbols: set[str] = set()

    def reset_run(self) -> None:
        """Clear request-time state before starting the next collection task."""

        self._admitted.clear()

    def prepare_symbols(self, symbols: Iterable[str]) -> None:
        self.scope = {normalize_symbol(item) for item in symbols if normalize_symbol(item)}

    def acquire(self, symbol: str, *, now: datetime | None = None) -> dict[str, object]:
        normalized = normalize_symbol(symbol)
        if normalized not in self.scope:
            return {"admitted": False, "access_state": "pending_quota", "reason_code": "outside_scope", "symbol": normalized}
        if normalized in self._admitted:
            return {"admitted": False, "access_state": "pending_quota", "reason_code": "duplicate_request", "symbol": normalized}
        if self.quota_reader is None:
            return {"admitted": False, "access_state": "pending_quota", "reason_code": "quota_unavailable", "symbol": normalized}
        snapshot = self.service.capture(self.quota_reader, now=now)
        if not snapshot.get("available"):
            return {"admitted": False, "access_state": "pending_quota", "reason_code": "quota_unavailable", "symbol": normalized}
        remain = snapshot.get("remain")
        total = snapshot.get("total")
        reserve = self.service._reserve(total if isinstance(total, int) else None)
        window = set(snapshot.get("window_symbols", []))
        locally_spent = len(self._runtime_new_symbols - window)
        consumes_new_quota = normalized not in window and normalized not in self._runtime_new_symbols
        if consumes_new_quota and (
            not isinstance(remain, int)
            or reserve is None
            or remain - locally_spent <= reserve
        ):
            return {"admitted": False, "access_state": "pending_quota", "reason_code": "history_quota_reserve", "symbol": normalized}
        if consumes_new_quota:
            # Quota detail can lag immediately after a history request. Keep
            # new-symbol admission locally reserved until OpenD reports it.
            self._runtime_new_symbols.add(normalized)
        self._admitted.add(normalized)
        self.service.mark_attempt(normalized, now=now)
        return {"admitted": True, "access_state": "admitted", "reason_code": None, "symbol": normalized}

    def mark_failure(self, symbol: str, message: str, *, now: datetime | None = None) -> None:
        self.service.mark_failure(symbol, message, now=now)
