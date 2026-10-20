"""Acceptance contract for MACD sign cards and historical sign markers.

The fixture prices are deliberately simple: a flat warm-up gives exact zero
MACD values, then a short tail creates adjacent sign changes.  Assertions use
the public technical indicator and series functions only.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

import pytest

from app.analytics.technical import calculate_technical_indicators, calculate_technical_series


SERIES_IDS = (
    "macd_histogram",
    "macd_histogram_delta",
    "macd_histogram_delta2",
)


def _bars(closes: Sequence[float]) -> list[dict[str, object]]:
    start = date(2026, 1, 1)
    return [
        {
            "date": (start + timedelta(days=index)).isoformat(),
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
        }
        for index, close in enumerate(closes)
    ]


def _macd(closes: Sequence[float]) -> dict[str, object]:
    return calculate_technical_indicators(_bars(closes), source="signal_acceptance")[
        "macd_12_26_9"
    ]  # type: ignore[return-value]


def _series_by_id(closes: Sequence[float]) -> dict[str, dict[str, object]]:
    result = calculate_technical_series(_bars(closes), source="signal_acceptance")
    return {str(item["series_id"]): item for item in result["series"]}  # type: ignore[return-value]


@pytest.mark.parametrize(
    ("tail", "expected_transition"),
    [
        ([99.0, 101.0], "negative_to_positive"),
        ([101.0, 99.0], "positive_to_negative"),
        ([100.0, 101.0], "zero_to_positive"),
        ([100.0, 99.0], "zero_to_negative"),
    ],
)
def test_macd_signal_cards_and_series_mark_adjacent_sign_changes(
    tail: Sequence[float], expected_transition: str
) -> None:
    closes = [100.0] * 80 + list(tail)
    macd = _macd(closes)
    signals = macd["histogram_signals"]  # type: ignore[index]
    series = _series_by_id(closes)

    assert set(signals) == {"histogram", "histogram_delta", "histogram_delta2"}
    expected_sign = {
        "negative_to_positive": "positive",
        "positive_to_negative": "negative",
        "zero_to_positive": "positive",
        "zero_to_negative": "negative",
    }[expected_transition]
    label_token = {"positive": "正", "negative": "负"}[expected_sign]
    for signal_name, series_id in zip(
        ("histogram", "histogram_delta", "histogram_delta2"),
        SERIES_IDS,
        strict=True,
    ):
        signal = signals[signal_name]
        points = series[series_id]["points"]
        latest = points[-1]
        marker = [
            item
            for item in series[series_id]["markers"]
            if item["time"] == latest["time"]
        ]

        assert signal["sign"] == expected_sign
        assert signal["transition"] == expected_transition
        assert label_token in signal["label"]
        assert marker, f"missing {series_id} marker at the latest bar"
        assert len(marker) == 1
        assert marker[0]["type"] == expected_transition
        assert marker[0]["from_sign"] in {"positive", "negative", "zero"}
        assert marker[0]["to_sign"] in {"positive", "negative", "zero"}
        assert marker[0]["to_sign"] == signal["sign"]
        assert marker[0]["value"] == latest["value"]
        assert marker[0]["label"]


def test_macd_signs_use_six_decimal_values_and_do_not_mark_first_ready_point() -> None:
    closes = [100.0] * 80 + [101.0, 102.0]
    macd = _macd(closes)
    signals = macd["histogram_signals"]  # type: ignore[index]
    series = _series_by_id(closes)

    for signal_name, series_id in zip(
        ("histogram", "histogram_delta", "histogram_delta2"),
        SERIES_IDS,
        strict=True,
    ):
        signal = signals[signal_name]
        assert signal["sign"] == "positive"
        assert signal["transition"] == "none"
        points = series[series_id]["points"]
        markers = series[series_id]["markers"]
        assert all(item["value"] == round(item["value"], 6) for item in markers)
        first_ready = next(point for point in points if point["value"] is not None)
        assert all(item["time"] != first_ready["time"] for item in markers)


def test_macd_to_zero_transition_is_explicit_after_six_decimal_rounding() -> None:
    # The final H rounds to -0.0, while the previous H is positive.  Sign
    # classification must follow the returned six-decimal value, so this is a
    # positive-to-zero transition rather than a hidden negative value.
    closes = [100.0] * 80 + [101.0, 99.42792]
    macd = _macd(closes)
    signal = macd["histogram_signals"]["histogram"]  # type: ignore[index]
    histogram = _series_by_id(closes)["macd_histogram"]

    assert macd["histogram"] == 0.0
    assert signal["sign"] == "zero"
    assert signal["transition"] == "to_zero"
    latest = histogram["points"][-1]
    marker = histogram["markers"][-1]
    assert marker["type"] == "to_zero"
    assert marker["from_sign"] == "positive"
    assert marker["to_sign"] == "zero"
    assert marker["value"] == latest["value"] == 0.0


def test_macd_unavailable_and_warmup_points_have_no_false_crossing_markers() -> None:
    short_closes = [100.0] * 33
    macd = _macd(short_closes)
    series = _series_by_id(short_closes)
    signals = macd["histogram_signals"]  # type: ignore[index]

    for signal_name, series_id in zip(
        ("histogram", "histogram_delta", "histogram_delta2"),
        SERIES_IDS,
        strict=True,
    ):
        signal = signals[signal_name]
        assert signal["sign"] == "unavailable"
        assert signal["transition"] == "unavailable"
        assert signal["label"]
        assert series[series_id]["markers"] == []

    ready_closes = [100.0] * 80 + [101.0]
    ready_series = _series_by_id(ready_closes)
    for series_id in SERIES_IDS:
        points = ready_series[series_id]["points"]
        first_ready = next(point for point in points if point["value"] is not None)
        assert all(
            item["time"] != first_ready["time"]
            for item in ready_series[series_id]["markers"]
        )


def test_macd_momentum_context_explains_negative_h_negative_delta_positive_delta2() -> None:
    # Downside momentum is still worsening (H<0 and ΔH<0), but the worsening
    # is slowing (Δ²H>0).  This must not be labelled as a bullish reversal.
    warmup = [100.0 + 0.03 * index + 0.5 * ((index * 7) % 11) for index in range(80)]
    macd = _macd([*warmup, 100.0, 100.0, 100.0])
    context = macd["momentum_context"]  # type: ignore[index]

    assert macd["histogram"] < 0
    assert macd["histogram_delta"] < 0
    assert macd["histogram_delta2"] > 0
    assert context["label"] == "下行动量仍增强，但恶化减速"
