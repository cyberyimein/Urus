"""Acceptance tests for the P0/P1 MACD histogram momentum contract.

These tests intentionally use only the public technical-analysis entry points.
The reference implementation below is kept independent from the application
code so that rounding or EMA changes cannot make the test reproduce the same
bug as the production implementation.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

import pytest

from app.analytics.technical import (
    calculate_technical_indicators,
    calculate_technical_series,
)


def _bars(closes: Sequence[float]) -> list[dict[str, object]]:
    start = date(2026, 1, 1)
    return [
        {
            "date": (start + timedelta(days=index - 1)).isoformat(),
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
        }
        for index, close in enumerate(closes, start=1)
    ]


def _reference_ema(values: Sequence[float], window: int) -> list[float | None]:
    """Independent SMA-seeded EMA used only as a behavioral oracle."""
    output: list[float | None] = [None] * len(values)
    if len(values) < window:
        return output

    current = sum(values[:window]) / window
    output[window - 1] = current
    alpha = 2.0 / (window + 1)
    for index in range(window, len(values)):
        current = current + alpha * (values[index] - current)
        output[index] = current
    return output


def _reference_macd(closes: Sequence[float]) -> dict[str, list[float | None]]:
    """Return aligned DIF, DEA, H, ΔH and Δ²H reference series."""
    fast = _reference_ema(closes, 12)
    slow = _reference_ema(closes, 26)
    dif: list[float | None] = [
        fast[index] - slow[index]
        if fast[index] is not None and slow[index] is not None
        else None
        for index in range(len(closes))
    ]

    dea: list[float | None] = [None] * len(closes)
    dif_start = next((index for index, value in enumerate(dif) if value is not None), None)
    if dif_start is not None:
        valid_dif = [value for value in dif[dif_start:] if value is not None]
        if len(valid_dif) >= 9:
            current = sum(valid_dif[:9]) / 9
            dea[dif_start + 8] = current
            alpha = 2.0 / 10
            for offset, value in enumerate(valid_dif[9:], start=9):
                current = current + alpha * (value - current)
                dea[dif_start + offset] = current

    histogram: list[float | None] = [
        dif[index] - dea[index]
        if dif[index] is not None and dea[index] is not None
        else None
        for index in range(len(closes))
    ]
    delta: list[float | None] = [None] * len(closes)
    delta2: list[float | None] = [None] * len(closes)
    for index in range(1, len(closes)):
        if histogram[index] is not None and histogram[index - 1] is not None:
            delta[index] = histogram[index] - histogram[index - 1]
    for index in range(2, len(closes)):
        if delta[index] is not None and delta[index - 1] is not None:
            delta2[index] = delta[index] - delta[index - 1]

    return {
        "dif": dif,
        "dea": dea,
        "histogram": histogram,
        "histogram_delta": delta,
        "histogram_delta2": delta2,
    }


def _macd(closes: Sequence[float]) -> dict[str, object]:
    result = calculate_technical_indicators(_bars(closes), source="acceptance_test")
    return result["macd_12_26_9"]  # type: ignore[return-value]


def _quadrant_closes(tail: Sequence[float]) -> list[float]:
    """Use a fixed warm-up plus a short tail for H/ΔH sign combinations."""
    warmup = [100.0 + 0.03 * index + 0.5 * ((index * 7) % 11) for index in range(80)]
    return [*warmup, *tail]


def test_macd_delta_uses_unrounded_histogram_values() -> None:
    closes = [100.0 + ((index * index * 7 + index * 3) % 29) / 10 for index in range(80)]
    reference = _reference_macd(closes)
    histogram = reference["histogram"]
    delta = reference["histogram_delta"]
    delta2 = reference["histogram_delta2"]
    assert histogram[-3:] == pytest.approx(
        [-0.06147516554741936, 0.005180337112234427, 0.046599719290814884]
    )
    assert delta[-1] is not None
    assert delta2[-1] is not None

    rounded_histogram_delta = round(histogram[-1], 6) - round(histogram[-2], 6)  # type: ignore[arg-type]
    rounded_histogram_delta2 = (
        round(histogram[-1], 6)
        - 2 * round(histogram[-2], 6)
        + round(histogram[-3], 6)  # type: ignore[arg-type]
    )
    expected_delta = round(delta[-1], 6)  # type: ignore[arg-type]
    expected_delta2 = round(delta2[-1], 6)  # type: ignore[arg-type]
    assert expected_delta != rounded_histogram_delta
    assert expected_delta2 != rounded_histogram_delta2

    actual = _macd(closes)
    assert actual["histogram_delta"] == expected_delta
    assert actual["histogram_delta2"] == expected_delta2


@pytest.mark.parametrize(
    ("sample_count", "histogram_available", "delta_available", "delta2_available"),
    [
        (33, False, False, False),
        (34, True, False, False),
        (35, True, True, False),
        (36, True, True, True),
    ],
)
def test_macd_histogram_momentum_has_explicit_34_35_36_sample_boundaries(
    sample_count: int,
    histogram_available: bool,
    delta_available: bool,
    delta2_available: bool,
) -> None:
    closes = [100.0 + 0.2 * index for index in range(sample_count)]
    macd = _macd(closes)

    assert (macd["histogram"] is not None) is histogram_available
    assert (macd["histogram_delta"] is not None) is delta_available
    assert (macd["histogram_delta2"] is not None) is delta2_available


@pytest.mark.parametrize(
    ("tail", "expected_h_positive", "expected_delta_positive"),
    [
        ([100.0, 100.0, 100.0], False, False),  # negative H, negative ΔH
        ([100.0, 100.0, 102.0], False, True),  # negative H, positive ΔH
        ([100.0, 109.0, 104.0], True, False),  # positive H, negative ΔH
        ([100.0, 100.0, 109.0], True, True),  # positive H, positive ΔH
    ],
)
def test_macd_histogram_and_delta_cover_the_four_momentum_quadrants(
    tail: Sequence[float],
    expected_h_positive: bool,
    expected_delta_positive: bool,
) -> None:
    macd = _macd(_quadrant_closes(tail))

    assert macd["histogram"] is not None
    assert macd["histogram_delta"] is not None
    assert (macd["histogram"] > 0) is expected_h_positive
    assert (macd["histogram_delta"] > 0) is expected_delta_positive


def test_macd_delta2_distinguishes_deterioration_slowing_from_improvement() -> None:
    # H remains negative and ΔH remains negative, but Δ²H is positive:
    # the downside is still worsening while the worsening is decelerating.
    closes = _quadrant_closes([100.0, 100.0, 100.0])
    reference = _reference_macd(closes)
    expected = [reference[key][-1] for key in ("histogram", "histogram_delta", "histogram_delta2")]
    assert expected[0] is not None and expected[1] is not None and expected[2] is not None
    assert expected[0] < 0
    assert expected[1] < 0
    assert expected[2] > 0

    macd = _macd(closes)
    assert macd["histogram"] == round(expected[0], 6)
    assert macd["histogram_delta"] == round(expected[1], 6)
    assert macd["histogram_delta2"] == round(expected[2], 6)


def test_macd_zero_is_available_and_is_not_confused_with_missing_history() -> None:
    zero_macd = _macd([100.0] * 36)
    assert zero_macd["histogram"] == 0.0
    assert zero_macd["histogram_delta"] == 0.0
    assert zero_macd["histogram_delta2"] == 0.0

    missing_macd = _macd([100.0] * 33)
    assert missing_macd["histogram"] is None
    assert missing_macd["histogram_delta"] is None
    assert missing_macd["histogram_delta2"] is None


def test_macd_keeps_legacy_fields_and_adds_aligned_histogram_delta_series() -> None:
    closes = [100.0 + ((index * index * 7 + index * 3) % 29) / 10 for index in range(80)]
    reference = _reference_macd(closes)
    indicators = calculate_technical_indicators(_bars(closes), source="acceptance_test")
    macd = indicators["macd_12_26_9"]

    for field in (
        "available",
        "quality_status",
        "dif",
        "dea",
        "histogram",
        "previous_dif",
        "previous_dea",
        "previous_histogram",
        "crossover",
        "zero_axis",
        "momentum",
    ):
        assert field in macd
    assert macd["histogram"] == round(reference["histogram"][-1], 6)  # type: ignore[arg-type]
    assert macd["previous_histogram"] == round(reference["histogram"][-2], 6)  # type: ignore[arg-type]

    series_result = calculate_technical_series(_bars(closes), source="acceptance_test")
    by_id = {item["series_id"]: item for item in series_result["series"]}
    for series_id in ("macd_histogram", "macd_histogram_delta", "macd_histogram_delta2"):
        assert series_id in by_id
        assert len(by_id[series_id]["points"]) == len(closes)

    histogram_points = by_id["macd_histogram"]["points"]
    delta_points = by_id["macd_histogram_delta"]["points"]
    delta2_points = by_id["macd_histogram_delta2"]["points"]
    assert histogram_points[32]["value"] is None
    assert histogram_points[33]["value"] == round(reference["histogram"][33], 6)  # type: ignore[arg-type]
    assert delta_points[33]["value"] is None
    assert delta_points[34]["value"] == round(reference["histogram_delta"][34], 6)  # type: ignore[arg-type]
    assert delta2_points[34]["value"] is None
    assert delta2_points[35]["value"] == round(reference["histogram_delta2"][35], 6)  # type: ignore[arg-type]


def test_macd_series_preserves_zero_points_while_leaving_warmup_points_null() -> None:
    result = calculate_technical_series(_bars([100.0] * 36), source="acceptance_test")
    by_id = {item["series_id"]: item for item in result["series"]}

    first_valid_index = {
        "macd_histogram": 33,
        "macd_histogram_delta": 34,
        "macd_histogram_delta2": 35,
    }
    for series_id, first_index in first_valid_index.items():
        points = by_id[series_id]["points"]
        assert all(point["value"] is None for point in points[:first_index])
        assert points[first_index]["value"] == 0.0
