from scripts.build_stage4b_decision_packet import (
    build_decision_packet,
    project_decision_packet,
)
from app.urus_agent.packet import build_online_decision_packet
from app.urus_agent.evidence import EvidenceStore
from datetime import UTC, datetime


def _observation(run_type: str, price: float, max_pain: float) -> dict:
    return {
        "run": {"id": f"run-{run_type}", "run_type": run_type, "status": "succeeded"},
        "snapshot": {
            "id": f"snapshot-{run_type}",
            "schema_version": "test",
            "quality_status": "ok",
            "payload": {
                "data_mode": "live",
                "is_mock": False,
                "market": {
                    "symbol": "QQQ",
                    "last_price": price,
                    "regular_price": price,
                    "history": {"available": True, "technical_indicators": {}},
                    "market_snapshot": {"quotes": [], "quality_status": "ok"},
                },
                "instrument_cards": [
                    {
                        "symbol": "INTC",
                        "regular_price": price,
                        "last_price": price,
                        "volume": 100,
                        "history": {"available": True, "technical_indicators": {}},
                        "relative_strength": {"available": True},
                    }
                ],
                "options": {
                    "status": "succeeded",
                    "available": True,
                    "symbols": [
                        {
                            "symbol": "QQQ",
                            "spot": price,
                            "overview": {"iv": 0.2},
                            "expirations": [
                                {
                                    "expiration": "2026-08-07",
                                    "days_to_expiry": 3,
                                    "max_pain": max_pain,
                                    "expected_move": {"amount": 5.0, "percent": 0.7},
                                    "exposure": {
                                        "totals": {"net_dex": 10, "modeled_net_gex": 20},
                                        "walls": {"call_gamma": {"strike": 710}},
                                        "by_strike": [{"strike": 700, "modeled_net_gex": 999}],
                                    },
                                    "spot_gamma_profile": {
                                        "primary_gamma_flip": 700,
                                        "current_spot_net_gex": 20,
                                        "points": [{"spot": 700, "net_gex": 20}],
                                    },
                                }
                            ],
                        }
                    ],
                },
                "systematic_flows": {
                    "schema_version": "urus.systematic_flows.v1",
                    "model_state": "intraday_estimate" if run_type == "pre_close" else "pre_market_context",
                    "assets": [{
                        "symbol": "QQQ",
                        "target_exposure": 0.8 if run_type == "pre_close" else 0.6,
                        "pressure_index": 40 if run_type == "pre_close" else 20,
                        "mechanical_action": "add_long",
                    }],
                    "portfolio": {
                        "unweighted_net_exposure": 0.8 if run_type == "pre_close" else 0.6,
                        "unweighted_gross_exposure": 0.8 if run_type == "pre_close" else 0.6,
                    },
                },
                "data_quality": {"status": "ok"},
            },
        },
        "steps": {"1A": {"payload": {"large": "duplicate"}}},
    }


def test_build_decision_packet_compacts_and_pairs_observations() -> None:
    pair = {
        "backup_schema": "urus.stage4b_strategy_pair.v1",
        "dataset_key": "test-pair",
        "content_sha256": "source-hash",
        "pair": {
            "observations": {
                "pre_market": _observation("pre_market", 690.0, 685.0),
                "pre_close": _observation("pre_close", 700.0, 690.0),
            }
        },
        "events": {"records": []},
    }

    packet = build_decision_packet(pair)

    assert packet["schema_version"] == "urus.stage4b_decision_packet.v1"
    assert packet["paired_changes"]["market"]["regular_price"]["absolute"] == 10.0
    assert "technical_confirmation" in packet["paired_changes"]["instruments"][0]
    expiration = packet["observations"]["pre_close"]["options"]["symbols"][0][
        "expirations"
    ][0]
    assert "by_strike" not in expiration
    assert "regular_price" in packet["observations"]["pre_close"]["instruments"][0]
    assert "points" not in expiration["spot_gamma_profile"]
    assert "steps" not in packet["observations"]["pre_close"]
    change = packet["paired_changes"]["options"][0]["expirations"][0]
    assert change["max_pain"]["absolute"] == 5.0
    assert packet["observations"]["pre_close"]["systematic_flows"]["assets"][0]["symbol"] == "QQQ"
    assert packet["paired_changes"]["systematic_flows"]["assets"][0]["target_exposure"]["absolute"] == 0.2
    assert packet["execution_ready"] is False


def test_decision_packet_exposes_rsi_context_as_structured_ai_evidence() -> None:
    observation = _observation("pre_close", 700.0, 690.0)
    instrument = observation["snapshot"]["payload"]["instrument_cards"][0]
    instrument["history"]["technical_indicators"]["rsi_context"] = {
        "available": True,
        "zone": "overbought",
        "classification": "breakout_confirmed",
        "continuation_direction": "up",
        "continuation_score": 6,
        "reversal_score": 1,
        "score_scale": 8,
        "signals": {"breakout_20d": True},
        "interpretation": "高动量突破得到量价确认。",
    }
    pair = {
        "backup_schema": "urus.stage4b_strategy_pair.v1",
        "dataset_key": "rsi-context-pair",
        "pair": {
            "observations": {
                "pre_market": _observation("pre_market", 690.0, 685.0),
                "pre_close": observation,
            }
        },
        "events": {"records": []},
    }

    packet = build_decision_packet(pair)

    context = packet["observations"]["pre_close"]["instruments"][0]["technical"]["rsi_context"]
    assert context["classification"] == "breakout_confirmed"
    assert context["continuation_score"] == 6


def test_build_decision_packet_rejects_incomplete_pair() -> None:
    pair = {
        "backup_schema": "urus.stage4b_strategy_pair.v1",
        "pair": {"observations": {"pre_market": _observation("pre_market", 690, 685)}},
    }

    try:
        build_decision_packet(pair)
    except ValueError as error:
        assert "pre_market and pre_close" in str(error)
    else:
        raise AssertionError("Incomplete pair should be rejected")


def test_project_decision_packet_separates_equity_and_option_context() -> None:
    pair = {
        "backup_schema": "urus.stage4b_strategy_pair.v1",
        "dataset_key": "test-pair",
        "pair": {
            "observations": {
                "pre_market": _observation("pre_market", 690.0, 685.0),
                "pre_close": _observation("pre_close", 700.0, 690.0),
            }
        },
        "events": {"records": []},
    }
    packet = build_decision_packet(pair)

    equity = project_decision_packet(packet, mode="equity")
    options = project_decision_packet(packet, mode="options", symbols={"qqq"})

    assert equity["observations"]["pre_close"]["options"]["symbols"] == []
    assert options["projection"] == {"mode": "options", "symbols": ["QQQ"]}
    assert [
        item["symbol"]
        for item in options["observations"]["pre_close"]["options"]["symbols"]
    ] == ["QQQ"]
    assert options["content_sha256"] != packet["content_sha256"]


def test_online_packet_uses_distinct_observations_and_calculates_changes() -> None:
    packet = build_online_decision_packet(
        dataset_key="pair-live",
        label="live pair",
        captured_at=datetime(2026, 8, 3, 20, tzinfo=UTC),
        pre_market_observation=_observation("pre_market", 690.0, 685.0),
        pre_close_observation=_observation("pre_close", 700.0, 690.0),
        events=[],
    )

    assert packet["source"]["dataset_key"] == "pair-live"
    assert (
        packet["observations"]["pre_market"]["run"]["id"]
        != packet["observations"]["pre_close"]["run"]["id"]
    )
    assert packet["paired_changes"]["market"]["regular_price"] == {
        "before": 690.0,
        "after": 700.0,
        "absolute": 10.0,
        "percent": 1.449275,
    }


def test_evidence_paths_can_select_option_expiration_by_date() -> None:
    packet = build_online_decision_packet(
        dataset_key="option-paths",
        label="option paths",
        captured_at=datetime(2026, 8, 3, 20, tzinfo=UTC),
        pre_market_observation=_observation("pre_market", 690.0, 685.0),
        pre_close_observation=_observation("pre_close", 700.0, 690.0),
        events=[],
    )

    store = EvidenceStore(packet)

    assert store.has_path(
        "observations.pre_close.options.symbols[QQQ].expirations[2026-08-07].max_pain"
    )
