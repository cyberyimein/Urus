from datetime import UTC, datetime, timedelta
from typing import Any

from urus_moomoo.integrations.moomoo import OpenDMarketAdapter
from urus_moomoo.services.quota import HistoryAdmission, HistoryCapacityService


class _NoopRateLimiter:
    def acquire_rate_slot(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _Sdk:
    RET_OK = 0

    class KLType:
        K_1M = "K_1M"

    class AuType:
        NONE = "None"


class _QuoteContext:
    def __init__(self) -> None:
        self._query_timeout = 12
        self._sync_query_connect_timeout = None
        self.snapshot_timeout_values: list[float] = []
        self.history_timeout_values: list[float] = []
        self.snapshot_requests: list[list[str]] = []
        self.history_requests: list[dict[str, Any]] = []

    def get_market_snapshot(self, codes: list[str]) -> tuple[int, list[dict[str, Any]]]:
        self.snapshot_timeout_values.append(self._query_timeout)
        self.snapshot_requests.append(list(codes))
        return 0, [
            {
                "code": code,
                "name": code,
                "last_price": 100,
                "prev_close_price": 99,
                "update_time": "2026-10-15 09:35:01",
            }
            for code in codes
        ]

    def request_history_kline(self, **kwargs: Any) -> tuple[int, list[dict[str, Any]], None]:
        self.history_timeout_values.append(self._query_timeout)
        self.history_requests.append(dict(kwargs))
        return 0, [
            {
                "time_key": "2026-10-15 09:35:00",
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100.5,
                "volume": 10,
                "turnover": 1005,
            }
        ], None


def _adapter(context: _QuoteContext) -> OpenDMarketAdapter:
    admission = HistoryAdmission(
        HistoryCapacityService(reserve_absolute=0, reserve_ratio=0),
        quota_reader=lambda: {
            "available": True,
            "used": 0,
            "remain": 100,
            "total": 100,
        },
    )
    admission.prepare_symbols(["AAA"])
    return OpenDMarketAdapter(
        host="loopback-fixture",
        port=0,
        market_symbols=["AAA"],
        sdk=_Sdk(),
        quote_context=context,
        history_admission=admission,
        rate_limiter=_NoopRateLimiter(),
    )


def test_shard_timeout_is_applied_to_requests_then_restored() -> None:
    context = _QuoteContext()
    adapter = _adapter(context)
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    try:
        payload = adapter.capture_intraday_equities(
            symbols=["AAA"],
            quote_symbols=["AAA"],
            minute_symbols=["AAA"],
            trading_date="2026-10-15",
            scheduled_at=datetime.fromisoformat("2026-10-15T09:35:00-04:00"),
            evidence_cutoff=datetime.fromisoformat("2026-10-15T09:35:00-04:00"),
            capture_quotes=True,
            request_budget_deadline_at=deadline,
            request_timeout_seconds=2,
            minute_timestamp_role="end",
        )
    finally:
        adapter.close()

    assert context.snapshot_timeout_values == [2]
    assert context.history_timeout_values == [2]
    assert context._query_timeout == 12
    assert payload["minute_timestamp_role"] == "end"
    assert payload["quality_summary"]["complete"] is True


def test_non_quote_shard_uses_cached_snapshot_only_and_makes_no_quote_request() -> None:
    context = _QuoteContext()
    adapter = _adapter(context)
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    try:
        payload = adapter.capture_intraday_equities(
            symbols=["AAA"],
            quote_symbols=["AAA"],
            minute_symbols=["AAA"],
            trading_date="2026-10-15",
            scheduled_at=datetime.fromisoformat("2026-10-15T09:35:00-04:00"),
            evidence_cutoff=datetime.fromisoformat("2026-10-15T09:35:00-04:00"),
            capture_quotes=False,
            shard_id="G2",
            request_budget_deadline_at=deadline,
            request_timeout_seconds=2,
            minute_timestamp_role="end",
        )
    finally:
        adapter.close()

    assert context.snapshot_requests == []
    assert context.history_requests
    assert payload["shard_id"] == "G2"
    assert payload["quote_symbols"] == ["AAA"]
    assert payload["quotes"] == []
    assert payload["quality_summary"]["complete"] is True


def test_expired_budget_records_skipped_symbols_without_provider_requests() -> None:
    context = _QuoteContext()
    adapter = _adapter(context)
    deadline = datetime.now(UTC) - timedelta(seconds=1)
    try:
        payload = adapter.capture_intraday_equities(
            symbols=["AAA"],
            quote_symbols=["AAA"],
            minute_symbols=["AAA"],
            trading_date="2026-10-15",
            scheduled_at=datetime.fromisoformat("2026-10-15T09:35:00-04:00"),
            evidence_cutoff=datetime.fromisoformat("2026-10-15T09:35:00-04:00"),
            capture_quotes=True,
            shard_id="G1",
            shard_symbols=["AAA"],
            request_budget_deadline_at=deadline,
            request_timeout_seconds=2,
            minute_timestamp_role="end",
        )
    finally:
        adapter.close()

    assert context.snapshot_requests == []
    assert context.history_requests == []
    assert payload["slot_shard_status"] == "skipped_budget"
    assert payload["quality_summary"]["skipped_budget_symbols"] == ["AAA"]
