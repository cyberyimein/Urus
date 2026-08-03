from app.analytics.technical import calculate_relative_strength, calculate_technical_indicators


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


def test_daily_technical_indicators_include_multiband_macd_and_effort_result() -> None:
    bars = [{**bar, "volume": 1000.0} for bar in _bars(60)]
    previous_close = float(bars[-2]["close"])
    bars[-1] = {
        **bars[-1],
        "high": previous_close * 1.01,
        "low": previous_close * 0.93,
        "close": previous_close * 0.94,
        "volume": 3000.0,
    }

    result = calculate_technical_indicators(bars, source="test_history")

    assert result["available"] is True
    assert result["bollinger_20_1"]["standard_deviations"] == 1
    assert result["bollinger_20_2"]["standard_deviations"] == 2
    assert result["bollinger_20_3"]["standard_deviations"] == 3
    assert result["bollinger_bandwidth_20"]["value"] > 0
    assert result["macd_12_26_9"]["available"] is True
    assert result["macd_12_26_9"]["histogram"] is not None
    assert result["volume_effort_result"]["available"] is True
    assert result["volume_effort_result"]["volume_ratio_20d"] == 3.0
    assert result["volume_effort_result"]["signal"] == "volume_down_distribution"


def test_daily_technical_indicators_report_insufficient_samples() -> None:
    result = calculate_technical_indicators(_bars(10), source="test_history")

    assert result["available"] is False
    assert result["quality_status"] == "partial"
    assert result["warnings"]


def test_daily_technical_indicators_include_extended_windows() -> None:
    result = calculate_technical_indicators(_bars(260), source="test_history")

    assert result["returns_percent"]["252d"] is not None
    assert result["moving_average"]["200d"] is not None
    assert result["realized_volatility_10d"]["sample_count"] == 10
    assert result["realized_volatility_60d"]["sample_count"] == 60


def test_relative_strength_aligns_returns_and_calculates_beta() -> None:
    instrument = _bars(80)
    benchmark = [
        {
            **bar,
            "close": 100.0 + index * 0.5,
        }
        for index, bar in enumerate(_bars(80))
    ]

    result = calculate_relative_strength(
        instrument,
        benchmark,
        benchmark="QQQ",
        source="test_history",
    )

    assert result["available"] is True
    assert result["excess_returns_percent"]["20d"] is not None
    assert result["beta"]["20d"] is not None
    assert result["correlation"]["20d"] is not None
