"""Independent runtime acceptance checks for malformed G1 quote payloads.

These tests exercise the real ``CollectorRuntime`` and ``CollectorStore`` with
the existing P2-shaped fake provider.  Quote quality is allowed to degrade the
slot, but it must not erase valid minute-bar references or prevent the later
G2/G3 window and local finalizer from running.
"""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
from typing import Any

from test_intraday_equity_reference_runtime import (
    _CUTOFF,
    _Provider,
    _finalizer_task,
    _runtime,
    _shard_task,
)


class _NonFinitePoolQuoteProvider(_Provider):
    """Return valid minute bars with non-finite values across the quote pool."""

    def capture_intraday_equities(self, **kwargs: Any) -> dict[str, object]:
        payload = super().capture_intraday_equities(**kwargs)
        if kwargs.get("capture_quotes"):
            for quote in payload["quotes"]:
                # Keep the whole pool in the request while exercising both a
                # required quote value and an optional nested provider field.
                quote["last_price"] = float("nan")
                quote["market_depth"] = {
                    "bid": float("inf"),
                    "ask": -float("inf"),
                }
        return payload


class _FinitePriceNestedNonFiniteProvider(_Provider):
    """Keep quote identity/price valid while corrupting optional nested fields."""

    def capture_intraday_equities(self, **kwargs: Any) -> dict[str, object]:
        payload = super().capture_intraday_equities(**kwargs)
        if kwargs.get("capture_quotes"):
            for quote in payload["quotes"]:
                quote["market_depth"] = {
                    "bid": float("nan"),
                    "ask": float("inf"),
                    "levels": [-float("inf")],
                }
        return payload


def _bar_refs(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        reference
        for reference in data.get("equity_data_refs", [])
        if isinstance(reference, dict) and reference.get("kind") == "bars"
    ]


def test_full_pool_nonfinite_quotes_do_not_drop_g1_minute_refs(tmp_path: Path) -> None:
    """A malformed full-pool snapshot leaves valid G1 bars resolvable."""

    provider = _NonFinitePoolQuoteProvider()
    runtime = _runtime(tmp_path, provider)
    try:
        result = runtime.collect_task(
            _shard_task("G1"),
            _CUTOFF + timedelta(seconds=60),
        )
        data = result["contract"]["data"]
        references = _bar_refs(data)
        resolved = [runtime.store.equity_history.resolve_ref(reference) for reference in references]
    finally:
        runtime.close()

    assert references, "invalid G1 quotes must not erase valid minute-bar refs"
    assert all(item["available"] for item in resolved)
    assert data["quality_summary"]["complete"] is False
    assert data["quality_summary"]["status"] == "partial"
    assert data["slot_shard_status"] == "partial"
    assert set(data["unavailable_quote_symbols"]) == {"SPY", "SMH", "AAA"}


def test_nested_nonfinite_optional_quotes_keep_complete_refs_via_runtime_and_resolver(
    tmp_path: Path,
) -> None:
    """Finite prices plus dirty optional fields still form a complete slot."""

    provider = _FinitePriceNestedNonFiniteProvider()
    runtime = _runtime(tmp_path, provider)
    try:
        for shard_id, offset in (("G1", 60), ("G2", 120), ("G3", 180)):
            runtime.collect_task(
                _shard_task(shard_id),
                _CUTOFF + timedelta(seconds=offset),
            )
        final = runtime.collect_task(
            _finalizer_task(),
            _CUTOFF + timedelta(seconds=240),
        )
        data = final["contract"]["data"]
        resolved = [
            runtime.store.equity_history.resolve_ref(reference)
            for reference in data["equity_data_refs"]
        ]
        quote_ref = next(
            reference
            for reference in data["equity_data_refs"]
            if reference.get("kind") == "quotes"
        )
        quote_result = runtime.store.equity_history.resolve_ref(quote_ref)

        # Keep this at the backend resolver boundary as well as the direct
        # store boundary: no inline fake payload is used here.
        import sys

        backend_root = Path(__file__).resolve().parents[2] / "backend"
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))
        from app.intraday.references import hydrate_capture_from_path, resolved_capture_data

        hydrated = hydrate_capture_from_path(runtime.store.path, final["contract"])
        hydrated_data = resolved_capture_data(hydrated)
    finally:
        runtime.close()

    assert final["completed"] is True
    assert data["quality_summary"]["complete"] is True
    assert data["unavailable_quote_symbols"] == []
    assert len(data["equity_data_refs"]) == 7
    assert all(item["available"] for item in resolved)
    assert quote_result["available"] is True
    assert len(quote_result["rows"]) == 3
    assert len(hydrated_data["quotes"]) == 3
    assert len(hydrated_data["minute_bars"]) == 15
    for row in quote_result["rows"]:
        json.dumps(row, allow_nan=False)
        assert row["market_depth"] == {
            "bid": None,
            "ask": None,
            "levels": [None],
        }
        assert row["nonfinite_fields"] == [
            "market_depth.ask",
            "market_depth.bid",
            "market_depth.levels[0]",
        ]


def test_quote_storage_failure_keeps_g1_bars_and_finalizer_partial(
    tmp_path: Path,
) -> None:
    """A quote write exception is local to quotes and cannot claim a full slot."""

    provider = _Provider()
    runtime = _runtime(tmp_path, provider)

    def fail_quote_storage(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("synthetic quote storage failure")

    runtime.store.equity_history.write_quote_batch = fail_quote_storage  # type: ignore[method-assign]
    try:
        g1 = runtime.collect_task(
            _shard_task("G1"),
            _CUTOFF + timedelta(seconds=60),
        )
        g1_data = g1["contract"]["data"]
        g1_refs = _bar_refs(g1_data)
        g1_resolved = [
            runtime.store.equity_history.resolve_ref(reference)
            for reference in g1_refs
        ]

        g2 = runtime.collect_task(
            _shard_task("G2"),
            _CUTOFF + timedelta(seconds=120),
        )
        g3 = runtime.collect_task(
            _shard_task("G3"),
            _CUTOFF + timedelta(seconds=180),
        )
        provider_call_count_before_finalizer = len(provider.calls)
        final = runtime.collect_task(
            _finalizer_task(),
            _CUTOFF + timedelta(seconds=240),
        )
        final_data = final["contract"]["data"]
        final_refs = _bar_refs(final_data)
        final_resolved = [
            runtime.store.equity_history.resolve_ref(reference)
            for reference in final_refs
        ]
    finally:
        runtime.close()

    assert g1_refs, "quote persistence failure must preserve G1 bar refs"
    assert all(item["available"] for item in g1_resolved)
    assert g1_data["quality_summary"]["status"] == "partial"
    assert g1_data["slot_shard_status"] == "partial"
    assert g2["contract"]["data"]["equity_data_refs"]
    assert g3["contract"]["data"]["equity_data_refs"]
    assert final["completed"] is False
    assert final_data["quality_summary"]["complete"] is False
    assert set(final_data["unavailable_quote_symbols"]) == {"SPY", "SMH", "AAA"}
    assert any(
        reference.get("kind") == "bars" and "SPY" in reference.get("symbols", [])
        for reference in final_refs
    ), "finalizer must retain G1 bar refs even when its quote is unavailable"
    assert all(item["available"] for item in final_resolved)
    assert [call.get("shard_id") for call in provider.calls] == ["G1", "G2", "G3"]
    assert len(provider.calls) == provider_call_count_before_finalizer


def test_nonfinite_g1_quotes_do_not_block_g2_g3_or_slot_finalizer(
    tmp_path: Path,
) -> None:
    """A malformed G1 snapshot still permits later shards and the slot merge."""

    provider = _NonFinitePoolQuoteProvider()
    runtime = _runtime(tmp_path, provider)
    try:
        runtime.collect_task(_shard_task("G1"), _CUTOFF + timedelta(seconds=60))
        runtime.collect_task(_shard_task("G2"), _CUTOFF + timedelta(seconds=120))
        runtime.collect_task(_shard_task("G3"), _CUTOFF + timedelta(seconds=180))
        calls_before_eof = len(provider.calls)
        final = runtime.collect_task(
            _finalizer_task(),
            _CUTOFF + timedelta(seconds=240),
        )
        data = final["contract"]["data"]
    finally:
        runtime.close()

    assert [call.get("shard_id") for call in provider.calls] == ["G1", "G2", "G3"]
    assert len(provider.calls) == calls_before_eof
    assert final["terminal"] is True
    assert final["completed"] is False
    assert data["quality_summary"]["status"] == "partial"
    assert data["slot_coverage"]["completed_shards"] == ["G2", "G3"]
    assert data["slot_coverage"]["partial_shards"] == ["G1"]
