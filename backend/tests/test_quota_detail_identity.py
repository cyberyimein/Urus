from app.services.history_quota import detail_symbols, normalize_quota_payload


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
