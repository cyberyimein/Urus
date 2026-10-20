from urus_moomoo.services.quota import (
    HistoryAdmission,
    HistoryCapacityService,
    detail_symbols,
    normalize_quota_payload,
)


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
