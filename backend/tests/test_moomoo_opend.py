from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.integrations.moomoo import OpenDMarketAdapter
from app.models import StepStatus
from app.workflows.context import RunContext
from app.workflows.market import MarketCollectorStep


class FakeSdk:
    RET_OK = 0

    class KLType:
        K_DAY = "K_DAY"

    class AuType:
        QFQ = "QFQ"


class FakeQuoteContext:
    def __init__(self) -> None:
        self.closed = False
        self.snapshot_calls: list[list[str]] = []
        self.history_kwargs: dict[str, object] = {}

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

    def close(self) -> None:
        self.closed = True


def test_opend_adapter_normalises_snapshot_and_daily_summary() -> None:
    quote_context = FakeQuoteContext()
    adapter = OpenDMarketAdapter(
        "opend-host",
        11111,
        history_days=20,
        sdk=FakeSdk(),
        quote_context=quote_context,
    )

    card = adapter.market_card("QQQ")
    adapter.close()

    assert card["is_mock"] is False
    assert card["data_mode"] == "opend"
    assert card["quote_code"] == "US.QQQ"
    assert card["previous_close"] == 100.0
    assert card["premarket_price"] == 101.8
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


def test_market_step_preserves_live_marker() -> None:
    adapter = OpenDMarketAdapter(
        "test",
        11111,
        history_days=20,
        sdk=FakeSdk(),
        quote_context=FakeQuoteContext(),
    )
    context = RunContext(
        run_id="run-test",
        run_type="pre_market",
        cutoff_time=datetime.now(UTC),
        symbols=["QQQ", "INTC"],
        market_adapter=adapter,
    )

    result = MarketCollectorStep().execute(context)
    adapter.close()

    assert result.status == StepStatus.SUCCEEDED
    assert result.payload["is_mock"] is False
    assert "OpenD" in result.summary


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
