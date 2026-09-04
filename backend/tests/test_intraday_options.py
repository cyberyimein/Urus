from __future__ import annotations

from datetime import datetime
import gzip
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import inspect, select
import pandas as pd

from app.integrations.moomoo_intraday_options import MoomooIntradayOptionsAdapter
from app.intraday_options.collector import IntradayOptionCaptureService
from app.intraday_options.maintenance import create_daily_backup
from app.intraday_options.models import (
    IntradayOptionCaptureAttemptModel,
    IntradayOptionCaptureRunModel,
    IntradayOptionContractSnapshotModel,
    IntradayOptionOutcomeModel,
    IntradayUnderlyingMinuteBarModel,
    IntradayUnderlyingMinuteBarRevisionModel,
)
from app.intraday_options.storage import IntradayOptionStore


NEW_YORK = ZoneInfo("America/New_York")


def capture_payload() -> dict:
    return {
        "schema_version": "moomoo-intraday-options-v1",
        "provider": "moomoo_openapi",
        "captured_at": "2026-09-04T14:00:12+00:00",
        "capture_tier": "structure",
        "request_band_percent": 10.0,
        "requested_symbols": ["SPY", "QQQ"],
        "warnings": ["OI is daily"],
        "underlying_bars": [
            {
                "symbol": "SPY",
                "bar_time": "2026-09-04 10:00:00",
                "open": 100.0,
                "high": 101.0,
                "low": 99.5,
                "close": 100.5,
                "volume": 1000,
                "turnover": 100500,
                "vwap": 100.5,
                "provider": "moomoo_openapi",
                "quality_state": "ready",
            }
        ],
        "symbols": [
            {
                "symbol": symbol,
                "spot": 100.5,
                "expirations": [
                    {
                        "expiration": "2026-09-04",
                        "contracts": [
                            {
                                "contract_code": f"US.{symbol}260904C100000",
                                "option_type": "CALL",
                                "strike": 100.0,
                                "contract_multiplier": 100.0,
                                "exercise_type": "AMERICAN",
                                "bid": 1.0,
                                "ask": 1.2,
                                "bid_size": 10,
                                "ask_size": 12,
                                "last": 1.1,
                                "volume": 20,
                                "open_interest": 30,
                                "implied_volatility": 25.0,
                                "delta": 0.5,
                                "gamma": 0.02,
                                "vega": 0.1,
                                "theta": -0.2,
                                "rho": 0.01,
                                "quote_time": "2026-09-04 10:00:05",
                                "warnings": [],
                                "provider_metadata": {},
                            }
                        ],
                    }
                ],
            }
            for symbol in ("SPY", "QQQ")
        ],
    }


class FakeProvider:
    def __init__(self, payload: dict | None = None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls = 0

    def capture_intraday(self, **_kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.payload

    def close(self) -> None:
        pass


def build_store(tmp_path: Path) -> IntradayOptionStore:
    return IntradayOptionStore(
        f"sqlite:///{tmp_path / 'intraday.db'}", tmp_path / "raw"
    )


def test_intraday_store_is_a_dedicated_schema_and_persists_raw_capture(tmp_path: Path) -> None:
    store = build_store(tmp_path)
    service = IntradayOptionCaptureService(store, NEW_YORK)
    scheduled = datetime(2026, 9, 4, 10, 0, tzinfo=NEW_YORK)

    result = service.collect(
        FakeProvider(capture_payload()),
        scheduled_at=scheduled,
        capture_tier="structure",
        symbols=["SPY", "QQQ"],
        band_percent=10,
        now=datetime(2026, 9, 4, 10, 0, 50, tzinfo=NEW_YORK),
    )

    assert result.status == "succeeded"
    assert result.skipped is False
    assert "observation_runs" not in inspect(store.engine).get_table_names()
    with store.session_factory() as session:
        run = session.scalar(select(IntradayOptionCaptureRunModel))
        attempt = session.scalar(select(IntradayOptionCaptureAttemptModel))
        assert run is not None and run.is_late_capture is True
        assert run.symbols_completed == ["QQQ", "SPY"]
        assert run.content_sha256 == result.content_sha256
        assert session.query(IntradayOptionContractSnapshotModel).count() == 2
        assert session.query(IntradayUnderlyingMinuteBarModel).count() == 1
        raw_path = Path(attempt.raw_payload_path)
    with gzip.open(raw_path, "rt", encoding="utf-8") as handle:
        assert json.load(handle)["capture_tier"] == "structure"
    store.close()


def test_same_logical_capture_is_idempotent(tmp_path: Path) -> None:
    store = build_store(tmp_path)
    provider = FakeProvider(capture_payload())
    service = IntradayOptionCaptureService(store, NEW_YORK)
    scheduled = datetime(2026, 9, 4, 10, 0, tzinfo=NEW_YORK)
    kwargs = {
        "scheduled_at": scheduled,
        "capture_tier": "structure",
        "symbols": ["SPY", "QQQ"],
        "band_percent": 10,
        "now": scheduled,
    }

    first = service.collect(provider, **kwargs)
    second = service.collect(provider, **kwargs)

    assert first.run_id == second.run_id
    assert second.skipped is True
    assert provider.calls == 1
    with store.session_factory() as session:
        assert session.query(IntradayOptionCaptureRunModel).count() == 1
        assert session.query(IntradayOptionCaptureAttemptModel).count() == 1
    store.close()


def test_failed_capture_is_audited_and_can_retry(tmp_path: Path) -> None:
    store = build_store(tmp_path)
    service = IntradayOptionCaptureService(store, NEW_YORK)
    scheduled = datetime(2026, 9, 4, 10, 5, tzinfo=NEW_YORK)
    kwargs = {
        "scheduled_at": scheduled,
        "capture_tier": "fast",
        "symbols": ["SPY", "QQQ"],
        "band_percent": 3,
        "now": scheduled,
    }

    failed = service.collect(FakeProvider(error=RuntimeError("OpenD unavailable")), **kwargs)
    retried = service.collect(FakeProvider(capture_payload()), **kwargs)

    assert failed.status == "failed"
    assert retried.status == "succeeded"
    assert failed.run_id == retried.run_id
    with store.session_factory() as session:
        attempts = session.scalars(
            select(IntradayOptionCaptureAttemptModel).order_by(
                IntradayOptionCaptureAttemptModel.attempt_number
            )
        ).all()
        assert [item.status for item in attempts] == ["failed", "succeeded"]
        assert attempts[0].error_message.endswith("OpenD unavailable")
    store.close()


def test_partial_capture_retries_without_mixing_attempt_snapshots(tmp_path: Path) -> None:
    store = build_store(tmp_path)
    service = IntradayOptionCaptureService(store, NEW_YORK)
    scheduled = datetime(2026, 9, 4, 10, 5, tzinfo=NEW_YORK)
    partial_payload = capture_payload()
    partial_payload["symbols"] = partial_payload["symbols"][:1]
    kwargs = {
        "scheduled_at": scheduled,
        "capture_tier": "fast",
        "symbols": ["SPY", "QQQ"],
        "band_percent": 3,
        "now": scheduled,
    }

    partial = service.collect(FakeProvider(partial_payload), **kwargs)
    complete = service.collect(FakeProvider(capture_payload()), **kwargs)

    assert partial.status == "partial"
    assert complete.status == "succeeded"
    assert partial.run_id == complete.run_id
    with store.session_factory() as session:
        attempts = session.scalars(select(IntradayOptionCaptureAttemptModel)).all()
        snapshots = session.scalars(select(IntradayOptionContractSnapshotModel)).all()
        run = session.get(IntradayOptionCaptureRunModel, complete.run_id)
        assert [item.status for item in attempts] == ["partial", "succeeded"]
        assert len(snapshots) == 3
        assert run is not None and run.selected_attempt_id == complete.attempt_id
        assert {item.capture_attempt_id for item in snapshots} == {
            item.id for item in attempts
        }
    store.close()


def test_minute_bar_revisions_replace_canonical_value(tmp_path: Path) -> None:
    store = build_store(tmp_path)
    service = IntradayOptionCaptureService(store, NEW_YORK)
    first = capture_payload()
    second = capture_payload()
    second["captured_at"] = "2026-09-04T14:05:12+00:00"
    second["underlying_bars"][0]["close"] = 101.25
    for minute, payload in ((0, first), (5, second)):
        service.collect(
            FakeProvider(payload),
            scheduled_at=datetime(2026, 9, 4, 10, minute, tzinfo=NEW_YORK),
            capture_tier="fast",
            symbols=["SPY", "QQQ"],
            band_percent=3,
        )
    with store.session_factory() as session:
        canonical = session.scalar(select(IntradayUnderlyingMinuteBarModel))
        assert canonical is not None and canonical.close == 101.25
        assert session.query(IntradayUnderlyingMinuteBarRevisionModel).count() == 2
    store.close()


def test_close_labels_and_app_consistent_backup(tmp_path: Path) -> None:
    store = build_store(tmp_path)
    scheduled = datetime(2026, 9, 4, 10, 0, tzinfo=NEW_YORK)
    result = IntradayOptionCaptureService(store, NEW_YORK).collect(
        FakeProvider(capture_payload()),
        scheduled_at=scheduled,
        capture_tier="structure",
        symbols=["SPY", "QQQ"],
        band_percent=10,
        now=scheduled,
    )
    assert result.status == "succeeded"
    close_payload = {
        "captured_at": "2026-09-04T20:05:00+00:00",
        "official_closes": {"SPY": 103.0, "QQQ": 97.0},
        "underlying_bars": [
            {
                "symbol": symbol,
                "bar_time": "2026-09-04 15:59:00",
                "open": close,
                "high": close + 0.1,
                "low": close - 0.1,
                "close": close,
                "volume": 100,
                "turnover": 10000,
                "vwap": close,
                "provider": "moomoo_openapi",
                "quality_state": "ready",
            }
            for symbol, close in (("SPY", 103.0), ("QQQ", 97.0))
        ],
    }
    assert store.finalize_trading_day(
        scheduled.date(), close_payload, market_timezone=NEW_YORK
    ) == 2
    manifest = create_daily_backup(
        str(store.engine.url), tmp_path / "raw", tmp_path / "backups", scheduled.date()
    )
    assert manifest.is_file()
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert set(manifest_payload["files"]) == {"intraday_options.db", "raw.tar"}
    assert manifest_payload["database_integrity_check"] == "ok"
    assert manifest_payload["table_counts"]["intraday_option_outcomes"] == 2
    with store.session_factory() as session:
        assert session.query(IntradayOptionOutcomeModel).count() == 2
    store.close()


def test_moomoo_intraday_adapter_uses_two_nearest_expiries_and_snapshot_batches() -> None:
    class FakeContext:
        def __init__(self) -> None:
            self.snapshot_calls: list[list[str]] = []
            self.history_calls: list[dict] = []

        def get_market_snapshot(self, codes):
            self.snapshot_calls.append(list(codes))
            if codes == ["US.SPY", "US.QQQ", "US.IWM"]:
                return 0, pd.DataFrame(
                    [
                        {"code": code, "last_price": 100.0, "update_time": "2026-09-04 10:00:00"}
                        for code in codes
                    ]
                )
            return 0, pd.DataFrame(
                [
                    {
                        "code": code,
                        "bid_price": 1.0,
                        "ask_price": 1.2,
                        "bid_vol": 10,
                        "ask_vol": 12,
                        "last_price": 1.1,
                        "volume": 20,
                        "option_open_interest": 30,
                        "option_implied_volatility": 25.0,
                        "option_delta": 0.5,
                        "option_gamma": 0.02,
                        "option_vega": 0.1,
                        "option_theta": -0.2,
                        "option_rho": 0.01,
                        "option_contract_multiplier": 100,
                        "option_area_type": "AMERICAN",
                        "update_time": "2026-09-04 10:00:01",
                    }
                    for code in codes
                ]
            )

        def request_history_kline(self, **kwargs):
            self.history_calls.append(kwargs)
            return 0, pd.DataFrame(
                [
                    {
                        "time_key": "2026-09-04 09:59:00",
                        "open": 99.8,
                        "high": 100.1,
                        "low": 99.7,
                        "close": 100.0,
                        "volume": 1000,
                        "turnover": 100000,
                    }
                ]
            ), None

        def get_history_kl_quota(self, *, get_detail):
            assert get_detail is True
            return 0, (2, 98, [{"code": "US.SPY"}, {"code": "US.QQQ"}])

        def get_option_expiration_date(self, _code):
            return 0, pd.DataFrame(
                [
                    {"strike_time": "2026-09-04", "option_expiry_date_distance": 0},
                    {"strike_time": "2026-09-05", "option_expiry_date_distance": 1},
                    {"strike_time": "2026-09-11", "option_expiry_date_distance": 7},
                ]
            )

        def get_option_chain(self, code, *, start, end):
            rows = []
            for expiration in ("2026-09-04", "2026-09-05", "2026-09-11"):
                for strike in (95.0, 100.0, 105.0):
                    for option_type in ("CALL", "PUT"):
                        rows.append(
                            {
                                "code": f"{code}-{expiration}-{strike}-{option_type}",
                                "strike_time": expiration,
                                "strike_price": strike,
                                "option_type": option_type,
                                "lot_size": 100,
                            }
                        )
            return 0, pd.DataFrame(rows)

        def close(self):
            pass

    context = FakeContext()
    adapter = MoomooIntradayOptionsAdapter(
        host="test",
        port=11111,
        symbols=["SPY", "QQQ", "IWM"],
        target_dtes=[0, 1],
        max_dte=7,
        strike_range_percent=10,
        batch_size=2,
        quote_context_factory=lambda **_kwargs: context,
        sleeper=lambda _seconds: None,
    )

    payload = adapter.capture_intraday(
        symbols=["SPY", "QQQ", "IWM"], capture_tier="fast", band_percent=3
    )

    assert [item["symbol"] for item in payload["symbols"]] == ["SPY", "QQQ", "IWM"]
    assert all(len(item["expirations"]) == 2 for item in payload["symbols"])
    assert all(
        {contract["strike"] for expiry in item["expirations"] for contract in expiry["contracts"]}
        == {100.0}
        for item in payload["symbols"]
    )
    assert len(payload["underlying_bars"]) == 3
    assert payload["minute_bar_symbols"] == ["IWM", "QQQ", "SPY"]
    assert payload["minute_bar_unavailable_symbols"] == []
    assert payload["quality_summary"]["complete"] is True
    assert payload["provider_raw"]["option_chain_batches"]
    assert payload["provider_raw"]["option_snapshot_batches"]
    first_batch = payload["provider_raw"]["option_snapshot_batches"][0]
    assert first_batch["request_started_at"] <= first_batch["response_received_at"]
    assert all(call["ktype"] == "K_1M" and call["autype"] == "NONE" for call in context.history_calls)
    assert all(len(codes) <= 2 for codes in context.snapshot_calls[1:])


def test_intraday_symbols_take_history_capacity_before_urus_reserve() -> None:
    class QuotaOnlyContext:
        def get_history_kl_quota(self, *, get_detail):
            assert get_detail is True
            return 0, (97, 3, [])

        def close(self):
            pass

    adapter = MoomooIntradayOptionsAdapter(
        host="test",
        port=11111,
        symbols=["SPY", "QQQ", "IWM"],
        target_dtes=[0, 1],
        max_dte=7,
        strike_range_percent=10,
        batch_size=400,
        quote_context_factory=lambda **_kwargs: QuotaOnlyContext(),
        sleeper=lambda _seconds: None,
    )

    admission = adapter._history_admission(["US.SPY", "US.QQQ", "US.IWM"])

    assert admission["priority"] == "intraday_options_first"
    assert admission["admitted_codes"] == ["US.SPY", "US.QQQ", "US.IWM"]
    assert admission["deferred_codes"] == []


def test_intraday_history_admission_still_fails_closed_at_zero_capacity() -> None:
    class QuotaOnlyContext:
        def get_history_kl_quota(self, *, get_detail):
            assert get_detail is True
            return 0, (100, 0, [{"code": "US.SPY"}, {"code": "US.QQQ"}])

        def close(self):
            pass

    adapter = MoomooIntradayOptionsAdapter(
        host="test",
        port=11111,
        symbols=["SPY", "QQQ", "IWM"],
        target_dtes=[0, 1],
        max_dte=7,
        strike_range_percent=10,
        batch_size=400,
        quote_context_factory=lambda **_kwargs: QuotaOnlyContext(),
        sleeper=lambda _seconds: None,
    )

    admission = adapter._history_admission(["US.SPY", "US.QQQ", "US.IWM"])

    assert admission["admitted_codes"] == ["US.SPY", "US.QQQ"]
    assert admission["deferred_codes"] == ["US.IWM"]
