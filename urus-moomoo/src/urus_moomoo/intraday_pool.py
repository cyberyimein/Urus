from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import date
from typing import Any
from urllib.parse import urlencode

from urus_moomoo.config import CollectionTask
from urus_moomoo.publisher import _get_json
from urus_moomoo.storage.sqlite import CollectorStore


_BENCHMARKS = ("SPY", "QQQ", "IWM")
_ROLE_ORDER = ("A", "B", "C", "D")
_SHARD_ORDER = ("G1", "G2", "G3")
_PLAN_OPERATIONS = {
    "capture_intraday_equities",
    "maintain_intraday_equities",
    "finalize_intraday_equities",
}


class IntradayPoolPlanLoader:
    """Fetch and freeze one API-supplied collection plan per market date."""

    def __init__(
        self,
        *,
        api_url: str,
        timeout_seconds: float,
        store: CollectorStore,
        get: Callable[[str, float], tuple[int, Mapping[str, Any]]] | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.store = store
        self.get = get or _get_json

    def plan_for(self, trading_date: str) -> dict[str, Any]:
        frozen = self.store.get_intraday_collection_plan(trading_date)
        if frozen is not None:
            return frozen
        if not self.api_url:
            raise RuntimeError("intraday collection plan unavailable: missing Urus API URL")

        query = urlencode({"trading_date": trading_date})
        url = f"{self.api_url}/api/intraday/collection-plan?{query}"
        try:
            status, payload = self.get(url, self.timeout_seconds)
        except Exception as exc:
            raise RuntimeError(
                f"intraday collection plan unavailable for {trading_date}: {exc}"
            ) from exc
        if status != 200 or payload.get("status") != "configured":
            reason = str(payload.get("message") or payload.get("status") or "invalid response")
            raise RuntimeError(
                f"intraday collection plan unavailable for {trading_date}: {reason}"
            )
        try:
            return self.store.freeze_intraday_collection_plan(trading_date, dict(payload))
        except (TypeError, ValueError, RuntimeError) as exc:
            raise RuntimeError(
                f"intraday collection plan unavailable for {trading_date}: {exc}"
            ) from exc

    def prepare_task(self, task: CollectionTask, trading_date: date) -> CollectionTask:
        if task.operation.strip() not in _PLAN_OPERATIONS:
            return task
        plan = self.plan_for(trading_date.isoformat())
        members = _symbols(plan, "member_symbols")
        params = dict(task.params or {})
        params["pool_plan_id"] = str(plan["plan_id"])
        params["pool_revision"] = int(plan["pool_revision"])
        params["display_group_by_symbol"] = dict(plan["display_group_by_symbol"])

        is_capture = task.operation.strip() in {
            "capture_intraday_equities",
            "finalize_intraday_equities",
        }
        if is_capture:
            params["groups"] = _assign_internal_roles(params.get("groups"), members)
            has_shards = bool(
                params.get("equity_shards_enabled")
                or params.get("windowed_equity_shard")
                or params.get("equity_shard_finalizer")
                or "equity_shard_symbols" in params
            )
            if has_shards:
                params["equity_shard_symbols"] = _assign_shards(
                    params.get("equity_shard_symbols"), members
                )
            if params.get("windowed_equity_shard"):
                shard_id = str(params.get("shard_id") or "").upper()
                if shard_id not in _SHARD_ORDER:
                    raise RuntimeError("windowed intraday task has an invalid shard id")
                params["shard_symbols"] = list(params["equity_shard_symbols"][shard_id])
        return replace(task, symbols=tuple(members), params=params)


def _symbols(plan: Mapping[str, Any], key: str) -> list[str]:
    raw = plan.get(key)
    if not isinstance(raw, list):
        raise RuntimeError(f"intraday collection plan {key} must be a list")
    symbols = [str(value).strip().upper() for value in raw if str(value).strip()]
    if len(symbols) != len(raw) or len(symbols) != len(set(symbols)):
        raise RuntimeError(f"intraday collection plan {key} contains invalid symbols")
    if not symbols:
        raise RuntimeError(f"intraday collection plan {key} must not be empty")
    return symbols


def _assign_internal_roles(
    configured_groups: object, members: Sequence[str]
) -> dict[str, list[str]]:
    allowed = set(members)
    configured = configured_groups if isinstance(configured_groups, Mapping) else {}
    result: dict[str, list[str]] = {role: [] for role in _ROLE_ORDER}
    assigned: set[str] = set()
    for role in _ROLE_ORDER:
        raw_members = configured.get(role, ())
        if isinstance(raw_members, (str, bytes)) or not isinstance(raw_members, Sequence):
            continue
        for raw_symbol in raw_members:
            symbol = str(raw_symbol).strip().upper()
            if symbol in allowed and symbol not in assigned:
                result[role].append(symbol)
                assigned.add(symbol)
    for symbol in members:
        if symbol not in assigned:
            # New members use the existing rotating, lower-intensity lane until
            # a later deliberate role assignment changes their sampling budget.
            result["D"].append(symbol)
    return result


def _assign_shards(
    configured_shards: object, members: Sequence[str]
) -> dict[str, list[str]]:
    configured = configured_shards if isinstance(configured_shards, Mapping) else {}
    allowed = set(members)
    result: dict[str, list[str]] = {shard: [] for shard in _SHARD_ORDER}
    assigned: set[str] = set()
    for shard in _SHARD_ORDER:
        raw_members = configured.get(shard, ())
        if isinstance(raw_members, (str, bytes)) or not isinstance(raw_members, Sequence):
            continue
        for raw_symbol in raw_members:
            symbol = str(raw_symbol).strip().upper()
            if symbol in allowed and symbol not in assigned:
                result[shard].append(symbol)
                assigned.add(symbol)
    for symbol in members:
        if symbol in assigned:
            continue
        smallest_shard = min(_SHARD_ORDER, key=lambda shard: (len(result[shard]), _SHARD_ORDER.index(shard)))
        result[smallest_shard].append(symbol)
        assigned.add(symbol)
    for shard in _SHARD_ORDER:
        if result[shard]:
            continue
        donor = max(_SHARD_ORDER, key=lambda item: (len(result[item]), -_SHARD_ORDER.index(item)))
        if len(result[donor]) <= 1:
            raise RuntimeError("intraday pool must contain at least three sharded members")
        result[shard].append(result[donor].pop())
    return result
