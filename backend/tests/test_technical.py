from app.analytics.technical import calculate_technical_indicators


def _bars(count: int = 30) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(count):
        close = 100.0 + index
        rows.append(
            {
                "date": f"2026-07-{index + 1:02d}",
                "open": close - 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
            }
        )
    return rows


def test_daily_technical_indicators_include_required_metadata() -> None:
    result = calculate_technical_indicators(_bars(), source="test_history")

    assert result["available"] is True
    assert result["quality_status"] == "ok"
    assert result["as_of"] == "2026-07-30"
    assert result["realized_volatility_20d"]["sample_count"] == 20
    assert result["realized_volatility_20d"]["source"] == "test_history"
    assert result["atr14"]["value"] == 2.0
    assert result["atr14_percent"]["unit"] == "percent"
    assert result["bollinger_20_2"]["sample_count"] == 20
    assert result["bollinger_20_2"]["current_price"] == 129.0


def test_daily_technical_indicators_report_insufficient_samples() -> None:
    result = calculate_technical_indicators(_bars(10), source="test_history")

    assert result["available"] is False
    assert result["quality_status"] == "partial"
    assert result["warnings"]
