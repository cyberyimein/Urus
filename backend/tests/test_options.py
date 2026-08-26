from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pandas as pd

from app.analytics.options import (
    OptionContract,
    calculate_expected_move,
    calculate_exposure,
    calculate_max_pain,
    calculate_spot_gamma_profile,
    trim_exposure_display,
)
from app.analytics.options_volatility import enrich_option_overview
from app.core.config import Settings
from app.integrations.moomoo_options import MoomooOptionsAdapter
from app.models import StepStatus
from app.workflows.context import RunContext
from app.workflows.options import OptionsCollectorStep


def contract(
    option_type: str,
    strike: float,
    *,
    open_interest: int,
    delta: float,
    gamma: float,
    bid: float = 1.0,
    ask: float = 1.2,
) -> OptionContract:
    return OptionContract(
        code=f"TEST-{option_type}-{strike}",
        option_type=option_type,
        expiration="2026-08-21",
        strike=strike,
        spot=100.0,
        multiplier=100.0,
        bid=bid,
        ask=ask,
        last=1.1,
        volume=10,
        open_interest=open_interest,
        implied_volatility=25.0,
        delta=delta,
        gamma=gamma,
    )


def test_max_pain_is_calculated_per_expiration_from_open_interest() -> None:
    contracts = [
        contract("CALL", 90, open_interest=10, delta=0.8, gamma=0.01),
        contract("PUT", 90, open_interest=2, delta=-0.2, gamma=0.01),
        contract("CALL", 100, open_interest=5, delta=0.5, gamma=0.03),
        contract("PUT", 100, open_interest=20, delta=-0.5, gamma=0.03),
        contract("CALL", 110, open_interest=2, delta=0.2, gamma=0.01),
        contract("PUT", 110, open_interest=10, delta=-0.8, gamma=0.01),
    ]

    assert calculate_max_pain(contracts) == 100


def test_dex_uses_delta_sign_and_gex_keeps_model_assumption_explicit() -> None:
    contracts = [
        contract("CALL", 100, open_interest=10, delta=0.5, gamma=0.02),
        contract("PUT", 100, open_interest=20, delta=-0.4, gamma=0.03),
    ]

    result = calculate_exposure(contracts)
    totals = result["totals"]
    assert totals["call_dex"] == pytest.approx(50_000)
    assert totals["put_dex"] == pytest.approx(-80_000)
    assert totals["net_dex"] == pytest.approx(-30_000)
    assert totals["call_gex"] == pytest.approx(2_000)
    assert totals["put_gex"] == pytest.approx(-6_000)
    assert totals["modeled_net_gex"] == pytest.approx(-4_000)
    assert result["walls"]["net_dex"]["strike"] == 100
    assert result["walls"]["put_gamma"] == {"strike": 100.0, "exposure": -6_000.0}


def test_gamma_zones_group_significant_contiguous_strikes_and_mark_flips() -> None:
    contracts = [
        contract("CALL", 90, open_interest=10, delta=0.4, gamma=0.02),
        contract("PUT", 100, open_interest=20, delta=-0.5, gamma=0.02),
        contract("CALL", 110, open_interest=30, delta=0.6, gamma=0.02),
    ]

    result = calculate_exposure(contracts)

    assert [row["gamma_regime"] for row in result["by_strike"]] == [
        "positive",
        "negative",
        "positive",
    ]
    assert [(zone["sign"], zone["start_strike"], zone["end_strike"]) for zone in result["gamma_zones"]] == [
        ("positive", 90.0, 90.0),
        ("negative", 100.0, 100.0),
        ("positive", 110.0, 110.0),
    ]
    assert [item["level"] for item in result["strike_gex_sign_changes"]] == [95.0, 105.0]


def test_display_trim_keeps_full_chain_totals_and_walls() -> None:
    contracts = [
        contract("CALL", 50, open_interest=100, delta=1.0, gamma=0.001),
        contract("PUT", 100, open_interest=10, delta=-0.5, gamma=0.02),
        contract("CALL", 110, open_interest=20, delta=0.4, gamma=0.02),
        contract("CALL", 150, open_interest=1, delta=0.01, gamma=0.001),
    ]
    exposure = calculate_exposure(contracts)
    totals_before = exposure["totals"].copy()
    walls_before = exposure["walls"].copy()

    trim_exposure_display(exposure, spot=100, strike_range_percent=20)

    assert exposure["totals"] == totals_before
    assert exposure["walls"] == walls_before
    assert exposure["calculation_strike_count"] == 4
    assert exposure["display_strike_count"] == 2
    assert [row["strike"] for row in exposure["by_strike"]] == [100, 110]
    assert "gamma_flip_levels" not in exposure


def test_spot_gamma_profile_reprices_contracts_and_finds_nearest_zero_crossing() -> None:
    contracts = [
        contract("PUT", 90, open_interest=100, delta=-0.4, gamma=0.02),
        contract("CALL", 110, open_interest=100, delta=0.4, gamma=0.02),
    ]

    profile = calculate_spot_gamma_profile(
        contracts,
        days_to_expiry=30,
        range_percent=30,
        point_count=121,
        risk_free_rate_percent=4,
        dividend_yield_percent=0,
    )

    assert profile["available"] is True
    assert profile["point_count"] == 121
    assert profile["usable_iv_contracts"] == 2
    assert profile["points"][60]["spot"] == 100
    assert 98 < profile["primary_gamma_flip"] < 100
    assert profile["points"][0]["net_gex"] < 0
    assert profile["points"][-1]["net_gex"] > 0


def test_spot_gamma_profile_is_unavailable_without_usable_iv() -> None:
    invalid = contract("CALL", 100, open_interest=10, delta=0.5, gamma=0.02)
    invalid = OptionContract(**{**invalid.__dict__, "implied_volatility": None})

    profile = calculate_spot_gamma_profile([invalid], days_to_expiry=30)

    assert profile == {"available": False, "points": [], "gamma_flip_levels": []}


def test_expected_move_uses_atm_call_and_put_midpoints() -> None:
    contracts = [
        contract("CALL", 95, open_interest=1, delta=0.7, gamma=0.01),
        contract("PUT", 95, open_interest=1, delta=-0.3, gamma=0.01),
        contract("CALL", 100, open_interest=1, delta=0.5, gamma=0.02, bid=2.0, ask=2.4),
        contract("PUT", 100, open_interest=1, delta=-0.5, gamma=0.02, bid=1.8, ask=2.2),
    ]

    expected = calculate_expected_move(contracts)
    assert expected == {"amount": 4.2, "percent": 4.2, "atm_strike": 100}


def test_crossed_market_does_not_create_expected_move() -> None:
    contracts = [
        contract("CALL", 100, open_interest=1, delta=0.5, gamma=0.02, bid=2.5, ask=2.0),
        contract("PUT", 100, open_interest=1, delta=-0.5, gamma=0.02),
    ]

    assert calculate_expected_move(contracts)["amount"] is None


def test_iv_hv_features_are_deterministic_and_explicitly_proxy_quality() -> None:
    overview = enrich_option_overview(
        {"iv": 35.429, "hv_30d": 58.328, "iv_rank": 35.58}
    )

    assert overview["iv_hv_spread"] == pytest.approx(-22.899)
    assert overview["iv_hv_ratio"] == pytest.approx(0.607410, abs=1e-6)
    assert overview["iv_hv_regime"] == "deep_discount"
    assert overview["term_match_method"] == "provider_composite_proxy"
    assert overview["model_fidelity"] == "proxy"


def test_iv_hv_features_do_not_divide_by_zero() -> None:
    overview = enrich_option_overview({"iv": 25.0, "hv_30d": 0.0})

    assert overview["iv_hv_ratio"] is None
    assert overview["iv_hv_regime"] == "unknown"
    assert "hv30_unavailable_or_non_positive" in overview["iv_hv_warnings"]


class FakeOptionsAdapter:
    def options_snapshot(self) -> dict[str, object]:
        return {
            "is_mock": False,
            "status": "available",
            "available": True,
            "provider": "test",
            "symbols": [],
        }

    def close(self) -> None:
        return None


def test_options_workflow_uses_the_dedicated_snapshot_adapter() -> None:
    context = RunContext(
        run_id="test",
        run_type="pre_market",
        cutoff_time=datetime.now(),
        symbols=["QQQ", "INTC"],
        options_adapter=FakeOptionsAdapter(),
    )

    result = OptionsCollectorStep().execute(context)

    assert result.status == StepStatus.SUCCEEDED
    assert result.payload["provider"] == "test"
    assert "DEX/GEX" in result.summary


def test_option_universe_merges_core_etfs_with_watchlist() -> None:
    settings = Settings(
        options_target_symbols="SPY,QQQ,SMH,IGV",
        options_watchlist_symbols="INTC,NVDA",
        enabled_symbols="QQQ,INTC,NVDA",
    )

    assert settings.options_collection_symbol_list == [
        "SPY",
        "QQQ",
        "SMH",
        "IGV",
        "INTC",
        "NVDA",
    ]


def test_options_collection_forwards_the_configured_serial_scope() -> None:
    calls: list[list[str] | None] = []

    class RecordingOptionsAdapter:
        def options_snapshot(self, symbols: list[str] | None = None) -> dict[str, object]:
            calls.append(symbols)
            return {
                "is_mock": False,
                "status": "available",
                "available": True,
                "provider": "test",
                "symbols": [{"symbol": symbol} for symbol in symbols or []],
            }

    context = RunContext(
        run_id="options-run",
        run_type="pre_market",
        cutoff_time=datetime(2026, 8, 4, tzinfo=UTC),
        symbols=["QQQ"],
        option_symbols=["QQQ", "INTC"],
        options_adapter=RecordingOptionsAdapter(),
    )
    result = OptionsCollectorStep().execute(context)

    assert result.status == StepStatus.SUCCEEDED
    assert calls == [["QQQ", "INTC"]]


class SnapshotContext:
    def get_market_snapshot(self, codes: list[str]):
        return 0, pd.DataFrame({"code": codes})

    def close(self) -> None:
        return None


def test_snapshot_requests_are_spaced_below_moomoo_rate_limit() -> None:
    clock = [0.0]
    sleeps: list[float] = []

    def monotonic_clock() -> float:
        return clock[0]

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    adapter = MoomooOptionsAdapter(
        host="test",
        port=11111,
        symbols=["SPY"],
        target_dtes=[0],
        max_dte=7,
        strike_range_percent=20,
        batch_size=400,
        quote_context_factory=lambda **_: SnapshotContext(),
        snapshot_interval_seconds=0.51,
        monotonic_clock=monotonic_clock,
        sleeper=sleeper,
    )

    adapter._market_snapshot(["US.SPY"], "first")
    adapter._market_snapshot(["US.SPY"], "second")

    assert sleeps == [pytest.approx(0.51)]


def test_expirations_are_grouped_into_thirty_day_chain_windows() -> None:
    expirations = [
        ("2026-08-03", 1),
        ("2026-08-10", 8),
        ("2026-08-31", 29),
        ("2026-09-30", 59),
        ("2026-10-30", 89),
    ]

    assert MoomooOptionsAdapter._expiration_groups(expirations) == [
        expirations[:3],
        expirations[3:],
    ]
