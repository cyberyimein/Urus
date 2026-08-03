from __future__ import annotations

from datetime import datetime

import pytest
import pandas as pd

from app.analytics.options import (
    OptionContract,
    calculate_expected_move,
    calculate_exposure,
    calculate_max_pain,
)
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
    assert totals["put_gex"] == pytest.approx(6_000)
    assert totals["modeled_net_gex"] == pytest.approx(-4_000)
    assert result["walls"]["net_dex"]["strike"] == 100


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
    assert [item["level"] for item in result["gamma_flip_levels"]] == [95.0, 105.0]


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
