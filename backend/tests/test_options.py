from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pandas as pd

from app.analytics.options import (
    OptionContract,
    build_post_close_option_alignment,
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
from app.services.options_collection import OptionsCollectionService
from app.urus_agent.evidence import EvidenceStore
from app.urus_agent.reports import build_technical_report
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


def test_post_close_alignment_marks_max_pain_and_dex_wall_candidates() -> None:
    result = build_post_close_option_alignment(
        {
            "symbols": [
                {
                    "symbol": "QQQ",
                    "spot": 100.0,
                    "expirations": [
                        {
                            "expiration": "2026-08-21",
                            "max_pain": 100.0,
                            "exposure": {
                                "walls": {
                                    "net_dex": {"strike": 100.5, "exposure": 500.0},
                                    "call_dex": {"strike": 105.0, "exposure": 300.0},
                                    "put_dex": {"strike": 95.0, "exposure": -250.0},
                                }
                            },
                        }
                    ],
                }
            ]
        },
        {"QQQ": {"price": 100.2, "price_kind": "regular_price"}},
    )

    expiration = result["symbols"][0]["expirations"][0]
    assert result["status"] == "flagged"
    assert result["flagged_symbols"] == ["QQQ"]
    assert result["flag_count"] == 1
    assert expiration["near_max_pain"] is True
    assert expiration["near_dex_wall"] is True
    assert expiration["dex_influence_candidate"] is True
    assert expiration["flags"] == ["near_max_pain", "near_dex_wall"]


def test_post_close_alignment_does_not_use_last_price_fallback_for_flags() -> None:
    result = build_post_close_option_alignment(
        {
            "symbols": [
                {
                    "symbol": "QQQ",
                    "spot": 100.0,
                    "expirations": [
                        {
                            "expiration": "2026-08-21",
                            "max_pain": 100.0,
                            "exposure": {
                                "walls": {
                                    "net_dex": {"strike": 100.5, "exposure": 500.0},
                                }
                            },
                        }
                    ],
                }
            ]
        },
        {"QQQ": {"price": 100.2, "price_kind": "last_price_fallback"}},
    )

    symbol = result["symbols"][0]
    expiration = symbol["expirations"][0]
    assert result["status"] == "unavailable"
    assert result["flagged_symbols"] == []
    assert result["flag_count"] == 0
    assert symbol["close_price"] is None
    assert symbol["status"] == "unavailable"
    assert expiration["near_max_pain"] is False
    assert expiration["near_dex_wall"] is False
    assert expiration["flags"] == []


def test_technical_report_persists_post_close_option_alignment_from_compact_packet() -> None:
    packet = {
        "schema_version": "urus.stage4b_decision_packet.v1",
        "source": {"dataset_key": "run:post-close-options"},
        "decision_context": {
            "current_observation": "post_close_review",
            "decision_phase": "post_close_review",
            "trading_date": "2026-08-21",
        },
        "observations": {
            "post_close_review": {
                "run": {"id": "run-1", "cutoff_time": "2026-08-21T08:00:00Z"},
                "market": {
                    "primary": {"symbol": "SPY", "regular_price": 500.0},
                    "cross_asset_quotes": [
                        {
                            "symbol": "QQQ",
                            "regular_price": 100.2,
                            "last_price": 101.4,
                            "quote_time": "2026-08-21T08:00:00Z",
                        }
                    ],
                },
                "instruments": [],
                "options": {
                    "symbols": [
                        {
                            "symbol": "QQQ",
                            "spot": 100.0,
                            "expirations": [
                                {
                                    "expiration": "2026-08-21",
                                    "max_pain": 100.0,
                                    "exposure_totals": {"net_dex": 500.0},
                                    "walls": {
                                        "net_dex": {"strike": 100.5, "exposure": 500.0}
                                    },
                                }
                            ],
                        }
                    ]
                },
            }
        },
        "quality": {},
    }

    report = build_technical_report(EvidenceStore(packet))
    alignment = report["options"]["post_close_alignment"]

    assert alignment["status"] == "flagged"
    assert alignment["symbols"][0]["close_price"] == pytest.approx(100.2)
    assert alignment["symbols"][0]["price_kind"] == "regular_price"


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


def test_options_collection_marks_a_live_provider_without_data_unavailable() -> None:
    class UnavailableOptionsAdapter:
        def options_snapshot(self, symbols: list[str] | None = None) -> dict[str, object]:
            return {
                "is_mock": False,
                "status": "unavailable",
                "available": False,
                "provider": "test",
                "symbols": [],
                "unavailable_symbols": symbols or [],
            }

    result = OptionsCollectionService().collect(
        UnavailableOptionsAdapter(),
        ["QQQ"],
    )

    assert result.status == StepStatus.UNAVAILABLE
    assert result.data_state == "unavailable"
    assert result.payload["status"] == "unavailable"


class SnapshotContext:
    def get_market_snapshot(self, codes: list[str]):
        return 0, pd.DataFrame({"code": codes})

    def close(self) -> None:
        return None


class RecordingRateLimiter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, float]] = []

    def acquire_rate_slot(self, rate_class: str, interval_seconds: float, **kwargs) -> None:
        del kwargs
        self.calls.append((rate_class, interval_seconds))


class OptionMetadataContext:
    def query_subscription(self):
        return 0, {}

    def get_market_snapshot(self, codes: list[str]):
        return 0, pd.DataFrame(
            {
                "code": codes,
                "last_price": [100.0 for _ in codes],
            }
        )

    def get_option_underlying_overview(self, codes: list[str]):
        return 0, pd.DataFrame({"code": codes})

    def get_option_expiration_date(self, code: str):
        del code
        return 0, pd.DataFrame(
            {
                "strike_time": ["2026-08-21"],
                "option_expiry_date_distance": [0],
            }
        )

    def close(self) -> None:
        return None


def test_option_metadata_requests_use_the_shared_rate_class() -> None:
    limiter = RecordingRateLimiter()
    adapter = MoomooOptionsAdapter(
        host="test",
        port=11111,
        symbols=["SPY"],
        target_dtes=[0],
        max_dte=7,
        strike_range_percent=20,
        batch_size=400,
        quote_context_factory=lambda **_: OptionMetadataContext(),
        option_metadata_interval_seconds=0.55,
        rate_limiter=limiter,
    )
    # Keep this test focused on the metadata endpoints; the chain response is
    # intentionally empty after the two metadata calls.
    adapter._option_chain = lambda underlying, start, end: pd.DataFrame()

    payload = adapter.options_snapshot()

    metadata_calls = [
        interval for rate_class, interval in limiter.calls if rate_class == "moomoo_option_metadata"
    ]
    assert metadata_calls == [pytest.approx(0.55), pytest.approx(0.55)]
    assert payload["status"] == "unavailable"


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
