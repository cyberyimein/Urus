from __future__ import annotations

import threading
from datetime import UTC, date, timedelta
from time import monotonic

from app.integrations.moomoo import OpenDMarketAdapter


class FakeSdk:
    RET_OK = 0

    class TradeDateMarket:
        US = "US"

    class KLType:
        K_DAY = "K_DAY"

    class AuType:
        QFQ = "QFQ"

    class PeriodType:
        DAY = "DAY"


class FakeQuoteContext:
    def __init__(self) -> None:
        self.closed = False
        self.snapshot_calls: list[list[str]] = []
        self.history_kwargs: dict[str, object] = {}
        self.capital_flow_calls: list[dict[str, object]] = []
        self.trading_days_calls: list[dict[str, object]] = []

    def request_trading_days(self, **kwargs):
        self.trading_days_calls.append(kwargs)
        return 0, [
            {"time": "2026-08-03", "trade_date_type": "WHOLE"},
            {"time": "2026-08-04", "trade_date_type": "WHOLE"},
        ]

    def get_market_snapshot(self, codes: list[str]):
        self.snapshot_calls.append(codes)
        return 0, [
            {
                "code": code,
                "stock_name": f"{code} test quote",
                "prev_close_price": 100.0,
                "last_price": 101.25,
                "pre_price": 101.8,
                "pre_volume": 12345,
                "after_price": 100.75,
                "after_volume": 678,
                "volume": 456789,
                "update_time": "2026-08-03 08:31:00",
            }
            for code in codes
        ]

    def request_history_kline(self, **kwargs):
        self.history_kwargs = kwargs
        today = date(2026, 8, 3)
        rows = []
        for index in range(25):
            close = 90.0 + index * 0.5
            rows.append(
                {
                    "time_key": (today - timedelta(days=25 - index)).isoformat(),
                    "open": close - 0.2,
                    "high": close + 0.4,
                    "low": close - 0.5,
                    "close": close,
                    "volume": 1000 + index,
                }
            )
        return 0, rows

    def get_capital_flow(self, code: str, **kwargs):
        self.capital_flow_calls.append({"code": code, **kwargs})
        return 0, [
            {
                "capital_flow_item_time": "2026-07-29 16:00:00",
                "in_flow": -12.0,
                "main_in_flow": 8.0,
                "super_in_flow": 5.0,
                "big_in_flow": 3.0,
                "mid_in_flow": -9.0,
                "sml_in_flow": -11.0,
            }
        ]

    def close(self) -> None:
        self.closed = True


def test_market_card_preserves_share_class_in_card_and_snapshot():
    context = FakeQuoteContext()
    adapter = OpenDMarketAdapter("test", 11111, sdk=FakeSdk(), quote_context=context)
    try:
        card = adapter.market_card("BRK.A")
    finally:
        adapter.close()
    assert card["symbol"] == "BRK.A"
    assert card["quote_code"] == "US.BRK.A"
    assert context.snapshot_calls[0][0] == "US.BRK.A"
    assert any(row["symbol"] == "BRK.A" for row in card["market_snapshot"]["quotes"])


def test_opend_close_returns_when_sdk_close_blocks() -> None:
    release = threading.Event()

    class BlockingQuoteContext(FakeQuoteContext):
        def close(self) -> None:
            release.wait()
            super().close()

    adapter = OpenDMarketAdapter("test", 11111, sdk=FakeSdk(), quote_context=BlockingQuoteContext())
    adapter._close_timeout_seconds = 0.01

    started = monotonic()
    adapter.close()
    elapsed = monotonic() - started
    release.set()

    assert elapsed < 0.5


def test_opend_adapter_normalises_snapshot_and_daily_summary() -> None:
    quote_context = FakeQuoteContext()
    adapter = OpenDMarketAdapter(
        "opend-host", 11111, history_days=20, sdk=FakeSdk(), quote_context=quote_context
    )

    card = adapter.market_card("QQQ")
    adapter.close()

    assert card["is_mock"] is False
    assert card["data_mode"] == "opend"
    assert card["quote_code"] == "US.QQQ"
    assert card["previous_close"] == 100.0
    assert card["regular_price"] == 101.25
    assert card["premarket_price"] == 101.8
    assert card["afterhours_price"] == 100.75
    assert card["history"]["available"] is True
    assert card["history"]["returned_days"] == 25
    assert card["history"]["technical_indicators"]["quality_status"] == "ok"
    assert card["history"]["technical_indicators"]["atr14"]["sample_count"] == 14
    assert card["history"]["technical_indicators"]["bollinger_20_2"]["source"] == "moomoo_opend_history"
    assert quote_context.snapshot_calls[0][0] == "US.QQQ"
    assert "US.SPY" in quote_context.snapshot_calls[0]
    assert len(quote_context.snapshot_calls) == 1
    assert card["market_snapshot"]["quality_status"] == "ok"
    assert card["market_snapshot"]["vix"]["available"] is False
    assert card["market_snapshot"]["vix"]["status"] == "skipped"
    assert card["market_snapshot"]["vix"]["source"] == "not_requested"
    assert "按策略不请求" in card["market_snapshot"]["vix"]["reason"]
    assert quote_context.history_kwargs["code"] == "US.QQQ"
    assert quote_context.closed is True


def test_opend_adapter_accepts_a_configured_primary_proxy() -> None:
    adapter = OpenDMarketAdapter("unreachable", 11111, sdk=FakeSdk(), quote_context=FakeQuoteContext())

    assert adapter._quote_ctx is not None
    card = adapter.market_card("INTC")
    adapter.close()
    assert card["symbol"] == "INTC"
    assert card["quote_code"] == "US.INTC"


def test_opend_adapter_normalises_one_daily_capital_flow_row() -> None:
    quote_context = FakeQuoteContext()
    adapter = OpenDMarketAdapter("test", 11111, sdk=FakeSdk(), quote_context=quote_context)

    result = adapter.capital_flow_day("SOXX", date(2026, 7, 29))
    adapter.close()

    assert result["main_in_flow"] == 8.0
    assert result["mid_in_flow"] == -9.0
    assert result["quality_status"] == "ok"
    assert quote_context.capital_flow_calls == [
        {"code": "US.SOXX", "period_type": "DAY", "start": "2026-07-29", "end": "2026-07-29"}
    ]


def test_opend_adapter_reads_us_trading_days() -> None:
    quote_context = FakeQuoteContext()
    adapter = OpenDMarketAdapter(
        "test",
        11111,
        sdk=FakeSdk(),
        quote_context=quote_context,
        trading_calendar_request_interval_seconds=0,
    )

    result = adapter.trading_days(date(2026, 8, 3), date(2026, 8, 4))
    adapter.close()

    assert result == [
        {"time": "2026-08-03", "trade_date_type": "WHOLE"},
        {"time": "2026-08-04", "trade_date_type": "WHOLE"},
    ]
    assert quote_context.trading_days_calls == [
        {"market": "US", "start": "2026-08-03", "end": "2026-08-04"}
    ]


def test_opend_adapter_collects_history_snapshots_with_instrument_cards() -> None:
    quote_context = FakeQuoteContext()
    adapter = OpenDMarketAdapter(
        "test", 11111, history_days=20, sdk=FakeSdk(), quote_context=quote_context
    )

    payload = adapter.instrument_cards(["INTC", "SMH"])
    adapter.close()

    assert payload["is_mock"] is False
    assert payload["requested_symbols"] == ["QQQ", "INTC", "SMH"]
    assert [item["symbol"] for item in payload["instruments"]] == ["QQQ", "INTC", "SMH"]
    assert [item["symbol"] for item in payload["history_snapshots"]] == ["QQQ", "INTC", "SMH"]
    assert payload["instruments"][1]["relative_strength"]["available"] is True
    assert payload["instruments"][2]["relative_strength"]["benchmark"] == "QQQ"
    assert payload["instruments"][1]["regular_price"] == 101.25
    assert payload["instruments"][1]["afterhours_price"] == 100.75
    assert payload["provider"] == "moomoo_openapi"
    assert payload["source_mode"] == "snapshot"
    assert payload["captured_at"]
    assert payload["instruments"][1]["provider"] == payload["provider"]
    assert payload["instruments"][1]["source_mode"] == payload["source_mode"]
    assert payload["instruments"][1]["captured_at"] == payload["captured_at"]
    assert payload["instruments"][1]["theme"] == "半导体"
    assert payload["instruments"][1]["themes"] == ["半导体"]
    assert payload["instruments"][1]["history"]["technical_indicators"]["bollinger_20_2"]["upper"] > 0
    assert payload["quota_audit"]["subscription_unchanged"] is True
    assert quote_context.snapshot_calls[-1] == ["US.QQQ", "US.INTC", "US.SMH"]


def test_opend_single_instrument_card_does_not_return_qqq_benchmark() -> None:
    quote_context = FakeQuoteContext()
    adapter = OpenDMarketAdapter("test", 11111, history_days=20, sdk=FakeSdk(), quote_context=quote_context)

    card = adapter.instrument_card("INTC")
    adapter.close()

    assert card["symbol"] == "INTC"
    assert card["quote_code"] == "US.INTC"


def test_opend_adapter_separates_saas_from_big_tech() -> None:
    adapter = OpenDMarketAdapter("test", 11111, history_days=20, sdk=FakeSdk(), quote_context=FakeQuoteContext())

    payload = adapter.instrument_cards(["NOW", "ORCL", "MSFT"])
    adapter.close()

    themes = {item["symbol"]: item["themes"] for item in payload["instruments"]}
    assert themes["NOW"] == ["SaaS"]
    assert themes["ORCL"] == ["SaaS"]
    assert themes["MSFT"] == ["大科技"]


def test_opend_adapter_reports_unreachable_endpoint_without_sdk_retry(monkeypatch) -> None:
    class UnusedSdk:
        RET_OK = 0

        @staticmethod
        def OpenQuoteContext(**kwargs):
            raise AssertionError("the SDK context should not be created after probe failure")

    def refused(*args, **kwargs):
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr("app.integrations.moomoo.socket.create_connection", refused)
    adapter = OpenDMarketAdapter("opend-host", 11111, sdk=UnusedSdk())

    try:
        adapter.market_card("QQQ")
    except RuntimeError as exc:
        assert "无法连接 Moomoo OpenD" in str(exc)
    else:
        raise AssertionError("unreachable OpenD endpoint did not fail")
