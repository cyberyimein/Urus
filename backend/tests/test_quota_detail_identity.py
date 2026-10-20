import pytest

from app.integrations.moomoo import _display_symbol, _normalise_quote_code
from app.services.history_quota import (
    detail_symbols, history_symbols_from_items, normalize_quota_payload, normalize_symbol,
)


@pytest.mark.parametrize("value,ticker,quote_code", [
    ("BRK.A", "BRK.A", "US.BRK.A"),
    (" us.brk.b ", "BRK.B", "US.BRK.B"),
    ("A", "A", "US.A"),
    ("US.A", "A", "US.A"),
    ("HK.00700", "00700", "HK.00700"),
    ("SH.600519", "600519", "SH.600519"),
])
def test_share_class_identity_is_preserved_through_quota_and_quotes(value, ticker, quote_code):
    assert normalize_symbol(value) == ticker
    assert _display_symbol(value) == ticker
    assert _normalise_quote_code(value) == quote_code


def test_quota_scope_and_window_keep_share_class_and_plain_ticker_distinct():
    assert history_symbols_from_items([
        {"symbol": "US.BRK.A"}, {"symbol": "A"}, {"symbol": "BRK.A"},
    ]) == ["BRK.A", "A"]
    quota = normalize_quota_payload({
        "used": 2, "remain": 98,
        "detail_list": [{"code": "US.BRK.A"}, {"code": "US.A"}],
    })
    assert quota["window_symbols"] == ["A", "BRK.A"]


def test_quota_detail_company_name_does_not_grant_another_symbol() -> None:
    details = [
        {"code": "US.SPY", "name": "NET", "request_time": "2026-10-16 04:30:00"},
        {"code": "US.QQQ", "name": "Cloudflare"},
    ]
    assert detail_symbols(details) == {"SPY", "QQQ"}


def test_quota_detail_only_reads_identity_fields_and_known_containers() -> None:
    details = {
        "detail_list": [
            {"security_code": "US.SPY", "metadata": {"name": "META"}},
            {"stock_code": ["US.QQQ", "US.IWM"]},
        ],
        "warnings": ["NET", "US.AAPL"],
    }
    assert detail_symbols(details) == {"SPY", "QQQ", "IWM"}


def test_explicit_quota_code_list_remains_supported() -> None:
    assert detail_symbols(["US.SPY", "QQQ"]) == {"SPY", "QQQ"}
    payload = normalize_quota_payload({
        "used": 1, "remain": 99,
        "detail_list": [{"code": "US.SPY", "name": "NET"}],
        "window_symbols": ["QQQ"],
    })
    assert payload["window_symbols"] == ["QQQ", "SPY"]
