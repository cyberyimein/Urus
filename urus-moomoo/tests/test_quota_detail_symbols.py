import pytest

from urus_moomoo.integrations.moomoo import _display_symbol, _normalise_quote_code
from urus_moomoo.services.quota import (
    HistoryAdmission,
    HistoryCapacityService,
    detail_symbols,
    normalize_quota_payload,
    normalize_symbol,
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


@pytest.mark.parametrize("remaining,expected_admitted", [(0, [False, True]), (2, [True, True])])
def test_share_class_admission_does_not_borrow_plain_ticker_quota(remaining, expected_admitted):
    admission = HistoryAdmission(
        HistoryCapacityService(reserve_absolute=0, reserve_ratio=0),
        quota_reader=lambda: {
            "available": True, "used": 1, "remain": remaining,
            "detail_list": [{"code": "US.A"}],
        },
    )
    admission.prepare_symbols(["BRK.A", "A"])
    decisions = admission.acquire_many(["US.BRK.A", "US.A"])
    assert [item["symbol"] for item in decisions] == ["BRK.A", "A"]
    assert [item["admitted"] for item in decisions] == expected_admitted


def test_detail_symbols_uses_code_fields_not_ticker_like_names() -> None:
    details = [
        {
            "code": "US.COHR",
            "name": "NET",
            "company": "Cloudflare",
        },
        {
            "security_code": "US.MSFT",
            "name": "Coherent",
            "company": "AAPL Systems",
        },
    ]

    assert detail_symbols(details) == {"COHR", "MSFT"}


def test_detail_symbols_finds_code_fields_in_nested_detail_objects() -> None:
    details = {
        "details": [
            {
                "detail_list": [
                    {
                        "stock_code": "US.AAPL",
                        "name": "NVDA",
                    }
                ]
            }
        ]
    }

    assert detail_symbols(details) == {"AAPL"}


def test_detail_symbols_ignores_code_like_fields_in_unknown_containers() -> None:
    details = {
        "metadata": {
            "detail_list": [
                {"code": "US.NVDA", "name": "NET"},
            ]
        }
    }

    assert detail_symbols(details) == set()


def test_detail_symbols_keeps_explicit_string_list_compatibility() -> None:
    assert detail_symbols(["US.AAPL", "MSFT"]) == {"AAPL", "MSFT"}
    assert detail_symbols({"symbols": ["US.AAPL", "MSFT"]}) == {"AAPL", "MSFT"}


def test_normalized_quota_window_excludes_unrelated_name_fields() -> None:
    quota = normalize_quota_payload(
        {
            "used_quota": 46,
            "remain_quota": 54,
            "detail_list": [
                {"code": "US.COHR", "name": "NET", "company": "Cloudflare"}
            ],
            "window_symbols": ["US.SPY"],
        }
    )

    assert quota["window_symbols"] == ["COHR", "SPY"]


def test_batch_admission_uses_one_quota_snapshot_and_preserves_reserve() -> None:
    calls = 0

    def quota_reader():
        nonlocal calls
        calls += 1
        return {
            "available": True,
            "used": 40,
            "remain": 3,
            "total": 43,
            "detail_list": [{"code": "US.A"}],
        }

    admission = HistoryAdmission(
        HistoryCapacityService(reserve_absolute=1, reserve_ratio=0),
        quota_reader=quota_reader,
    )
    admission.prepare_symbols(["A", "B", "C", "D"])

    decisions = admission.acquire_many(["A", "B", "C", "D"])

    assert calls == 1
    assert [item["admitted"] for item in decisions] == [True, True, True, False]
    assert decisions[-1]["reason_code"] == "history_quota_reserve"
