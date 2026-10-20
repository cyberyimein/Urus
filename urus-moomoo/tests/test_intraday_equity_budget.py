from datetime import UTC, datetime, timedelta
import hashlib

import pytest

from urus_moomoo.services.intraday_equities import build_minute_timestamp_policy
from urus_moomoo.services.pacing import CollectionCoordinator


def test_minute_timestamp_policy_requires_matching_capture_evidence() -> None:
    capture_hash = hashlib.sha256(b"timestamp boundary test fixture").hexdigest()
    verification = {
        "provider": "moomoo_openapi",
        "market": "US",
        "interval": "K_1M",
        "role": "end",
        "evidence_trading_date": "2026-10-15",
        "capture_check_sha256": capture_hash,
    }

    policy = build_minute_timestamp_policy(
        "end",
        verification,
        market_timezone="America/New_York",
    )

    assert policy["provider_boundary_verified"] is True
    assert policy["provider_boundary_evidence"] == verification


@pytest.mark.parametrize(
    "changed_field",
    [
        "provider",
        "market",
        "interval",
        "role",
        "evidence_trading_date",
        "capture_check_sha256",
    ],
)
def test_minute_timestamp_policy_fails_closed_on_mismatched_evidence(
    changed_field: str,
) -> None:
    verification = {
        "provider": "moomoo_openapi",
        "market": "US",
        "interval": "K_1M",
        "role": "end",
        "evidence_trading_date": "2026-10-15",
        "capture_check_sha256": hashlib.sha256(
            b"timestamp boundary test fixture"
        ).hexdigest(),
    }
    verification[changed_field] = "mismatch"

    policy = build_minute_timestamp_policy(
        "end",
        verification,
        market_timezone="America/New_York",
    )

    assert policy["provider_boundary_verified"] is False
    assert policy["provider_boundary_evidence"] is None
    assert policy["enablement_note"]


def test_collection_lock_timeout_does_not_wait_past_the_shard_budget(tmp_path) -> None:
    path = tmp_path / "collector.lock"
    holder = CollectionCoordinator(path)
    waiter = CollectionCoordinator(path)
    holder.acquire()
    try:
        assert waiter.acquire_with_timeout(0) is False
        assert waiter.locked is False
    finally:
        waiter.close()
        holder.close()


def test_shared_rate_wait_beyond_latest_start_is_rejected_without_sleep(tmp_path) -> None:
    now = datetime(2026, 10, 15, 13, 35, tzinfo=UTC)
    sleeps: list[float] = []
    coordinator = CollectionCoordinator(tmp_path / "collector.lock", sleeper=sleeps.append)
    try:
        waited = coordinator.acquire_rate_slot(
            "moomoo_quote_history",
            0.55,
            now=now,
            latest_start_at=now - timedelta(milliseconds=1),
        )
    finally:
        coordinator.close()

    assert waited == float("inf")
    assert sleeps == []
