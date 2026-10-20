"""Non-finite optional quote fields must not discard an otherwise valid capture."""

import json
import math
from datetime import timedelta

import pytest

from test_equity_history_refs import _bar, _OPEN, _TRADE_DATE
from test_intraday_equity_reference_runtime import (
    _CUTOFF, _Provider, _runtime, _shard_task,
)
from urus_moomoo.storage.sqlite import CollectorStore


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_optional_nonfinite_quote_values_are_null_and_retry_is_idempotent(tmp_path, value):
    quote = {
        "symbol": "AAA", "quote_code": "US.AAA", "last_price": 101.5,
        "pe_ratio": value, "details": {"levels": [12.0, value]},
    }
    store = CollectorStore(tmp_path / "collector.sqlite")
    args = dict(
        task_name="equity-intraday", scheduled_at=_CUTOFF,
        trading_date=_TRADE_DATE, capture_id="g1", captured_at=_CUTOFF,
        quotes=[quote], requested_symbols=["AAA"],
    )
    try:
        first = store.equity_history.write_quote_batch(**args)
        retry = store.equity_history.write_quote_batch(
            **{**args, "captured_at": _CUTOFF + timedelta(seconds=5)},
        )
        resolved = store.equity_history.resolve_ref(first)
        assert resolved["available"] is True
        row = resolved["rows"][0]
        assert row["last_price"] == 101.5
        assert row["pe_ratio"] is None
        assert row["details"]["levels"] == [12.0, None]
        assert set(row["nonfinite_fields"]) == {"pe_ratio", "details.levels[1]"}
        json.dumps(row, allow_nan=False)
        assert first == retry
        assert store.connection.execute("SELECT count(*) FROM equity_quote_batches").fetchone()[0] == 1
        assert not math.isfinite(quote["pe_ratio"])
        assert not math.isfinite(quote["details"]["levels"][1])
    finally:
        store.close()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0.0])
def test_invalid_last_price_only_excludes_that_quote_and_preserves_minutes(tmp_path, value):
    class Provider(_Provider):
        def capture_intraday_equities(self, **kwargs):
            payload = super().capture_intraday_equities(**kwargs)
            if kwargs.get("capture_quotes"):
                payload["quotes"][0]["last_price"] = value
            return payload

    runtime = _runtime(tmp_path, Provider())
    try:
        result = runtime.collect_task(_shard_task("G1"), _CUTOFF + timedelta(seconds=60))
        data = result["contract"]["data"]
        refs = data["equity_data_refs"]
        bars = [ref for ref in refs if ref["kind"] == "bars"]
        assert len(bars) == 2
        assert all(runtime.store.equity_history.resolve_ref(ref)["available"] for ref in bars)
        quotes = next(ref for ref in refs if ref["kind"] == "quotes")
        rows = runtime.store.equity_history.resolve_ref(quotes)["rows"]
        assert {row["symbol"] for row in rows} == {"SMH", "AAA"}
        assert data["quality_summary"]["complete"] is False
        assert data["slot_shard_status"] == "partial"
        assert data["quality_summary"]["unavailable_quote_symbols"] == ["SPY"]
        assert data["quality_summary"]["invalid_quote_rows"] == 1
    finally:
        runtime.close()


def test_nonfinite_bar_close_is_still_rejected(tmp_path):
    store = CollectorStore(tmp_path / "collector.sqlite")
    bar = {**_bar(0), "close": float("nan")}
    try:
        with pytest.raises(ValueError, match="finite"):
            store.equity_history.upsert_bars(
                [bar], timeframe="1m", trading_date=_TRADE_DATE,
                source_capture_id="invalid", observed_at=_OPEN,
            )
        assert store.connection.execute("SELECT count(*) FROM equity_bar_revisions").fetchone()[0] == 0
    finally:
        store.close()
