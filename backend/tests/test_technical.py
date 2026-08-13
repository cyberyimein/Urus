import pytest

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


def _descending_bars(count: int = 80) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(count):
        close = 200.0 - index
        rows.append(
            {
                "date": f"2026-06-{index + 1:02d}",
                "open": close + 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 1000.0,
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
    assert result["rsi14"]["available"] is True
    assert result["rsi14"]["value"] == 100.0
    assert result["rsi14"]["state"] == "overbought"


def test_rsi_context_distinguishes_confirmed_breakout_from_automatic_sell_signal() -> None:
    bars = [{**bar, "volume": 1000.0} for bar in _bars(80)]
    previous_close = float(bars[-2]["close"])
    bars[-1] = {
        **bars[-1],
        "open": previous_close + 0.5,
        "high": previous_close + 4.2,
        "low": previous_close + 0.2,
        "close": previous_close + 4.0,
        "volume": 1800.0,
    }

    result = calculate_technical_indicators(bars, source="test_history")
    context = result["rsi_context"]

    assert result["rsi14"]["value"] >= 70
    assert context["zone"] == "overbought"
    assert context["classification"] == "breakout_confirmed"
    assert context["continuation_direction"] == "up"
    assert context["continuation_score"] >= 5
    assert context["signals"]["breakout_20d"] is True
    assert context["signals"]["high_volume_close_high"] is True
    assert "sell" not in context["interpretation"].lower()


def test_rsi_context_distinguishes_oversold_breakdown_from_reversal_candidate() -> None:
    bars = _descending_bars()
    previous_close = float(bars[-2]["close"])
    bars[-1] = {
        **bars[-1],
        "open": previous_close - 0.5,
        "high": previous_close - 0.2,
        "low": previous_close - 5.0,
        "close": previous_close - 4.8,
        "volume": 2200.0,
    }

    result = calculate_technical_indicators(bars, source="test_history")
    context = result["rsi_context"]

    assert result["rsi14"]["value"] <= 30
    assert context["zone"] == "oversold"
    assert context["classification"] == "breakdown_confirmed"
    assert context["continuation_direction"] == "down"
    assert context["continuation_score"] >= 5
    assert context["signals"]["breakdown_20d"] is True
    assert context["signals"]["high_volume_close_low"] is True
    assert context["reversal_score"] <= 2


def test_rsi_context_confirms_recovery_only_after_rsi_leaves_oversold_zone() -> None:
    closes = [200.0 - index for index in range(60)] + [142.0, 145.0, 149.0]
    bars = [
        {
            "date": f"2026-recovery-{index:03d}",
            "open": closes[index - 1] if index else close,
            "high": max(close, closes[index - 1] if index else close) + 0.5,
            "low": min(close, closes[index - 1] if index else close) - 0.5,
            "close": close,
            "volume": 1000.0,
        }
        for index, close in enumerate(closes)
    ]

    result = calculate_technical_indicators(bars, source="test_history")
    context = result["rsi_context"]

    assert result["rsi14"]["previous_value"] <= 30
    assert result["rsi14"]["value"] > 30
    assert context["signals"]["crossed_above_30"] is True
    assert context["classification"] == "reversal_confirmed"
    assert context["reversal_score"] >= 5


def test_rsi_context_confirms_exit_risk_only_after_rsi_leaves_overbought_zone() -> None:
    closes = [100.0 + index for index in range(60)] + [158.0, 155.0, 151.0]
    bars = [
        {
            "date": f"2026-exit-{index:03d}",
            "open": closes[index - 1] if index else close,
            "high": max(close, closes[index - 1] if index else close) + 0.5,
            "low": min(close, closes[index - 1] if index else close) - 0.5,
            "close": close,
            "volume": 1000.0,
        }
        for index, close in enumerate(closes)
    ]

    result = calculate_technical_indicators(bars, source="test_history")
    context = result["rsi_context"]

    assert result["rsi14"]["previous_value"] >= 70
    assert result["rsi14"]["value"] < 70
    assert context["signals"]["crossed_below_70"] is True
    assert context["classification"] == "exit_confirmed"
    assert context["reversal_score"] >= 4


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
    assert 0 <= result["rsi14"]["value"] <= 100
    assert result["volume_effort_result"]["available"] is True
    assert result["volume_effort_result"]["volume_ratio_20d"] == 3.0
    assert result["volume_effort_result"]["combination"] == "high_down"
    assert result["volume_effort_result"]["signal"] == "volume_down_distribution"


@pytest.mark.parametrize(
    ("volume", "move", "expected_combination"),
    [
        (2000.0, 0.01, "high_up"),
        (2000.0, -0.01, "high_down"),
        (2000.0, 0.001, "high_flat"),
        (1000.0, 0.01, "normal_up"),
        (1000.0, -0.01, "normal_down"),
        (1000.0, 0.001, "normal_flat"),
        (700.0, 0.01, "low_up"),
        (700.0, -0.01, "low_down"),
        (700.0, 0.001, "low_flat"),
    ],
)
def test_effort_result_preserves_every_volume_price_combination(
    volume: float,
    move: float,
    expected_combination: str,
) -> None:
    bars = [{**bar, "volume": 1000.0} for bar in _bars(60)]
    previous_close = float(bars[-2]["close"])
    close = previous_close * (1 + move)
    bars[-1] = {
        **bars[-1],
        "high": max(previous_close, close) * 1.01,
        "low": min(previous_close, close) * 0.99,
        "close": close,
        "volume": volume,
    }

    result = calculate_technical_indicators(bars, source="test_history")

    assert result["volume_effort_result"]["combination"] == expected_combination


def test_daily_technical_indicators_report_insufficient_samples() -> None:
    result = calculate_technical_indicators(_bars(10), source="test_history")

    assert result["available"] is False
    assert result["quality_status"] == "partial"
    assert result["warnings"]
    assert result["rsi14"]["available"] is False
    assert result["rsi14"]["value"] is None
    assert result["rsi_context"]["available"] is False


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
