from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.analytics.cta import aggregate_cta_proxy_signals, calculate_cta_proxy_signal
from app.models import StepStatus
from app.schemas.read_model import EventSummary
from app.workflows.context import RunContext
from app.workflows.cta import (
    InstrumentCTAProxyStep,
    MarketCTAProxyStep,
    build_systematic_flows,
)


def _bars(*, count: int = 300, daily_return: float = 0.001) -> list[dict[str, object]]:
    current = 100.0
    start = date(2025, 1, 1)
    result: list[dict[str, object]] = []
    for index in range(count):
        # Alternating noise avoids a zero-volatility synthetic series while
        # retaining a clear trend for deterministic assertions.
        noise = 0.0005 if index % 2 == 0 else -0.0005
        current *= 1.0 + daily_return + noise
        result.append(
            {
                "date": (start + timedelta(days=index)).isoformat(),
                "open": current * 0.999,
                "high": current * 1.002,
                "low": current * 0.998,
                "close": current,
                "volume": 1_000_000 + index,
            }
        )
    return result


def test_cta_proxy_signal_is_multi_horizon_and_auditable() -> None:
    signal = calculate_cta_proxy_signal(
        _bars(),
        symbol="QQQ",
        source="fixture",
        proxy_for="NQ equity-index futures",
    )

    assert signal["schema_version"] == "urus.cta_proxy_signal.v1"
    assert signal["available"] is True
    assert signal["quality_status"] == "ok"
    assert signal["direction"] == "long"
    assert signal["target_exposure"] > 0
    assert set(signal["components"]) == {"momentum", "ema", "donchian"}
    assert set(signal["components"]["momentum"]["horizons"]) == {
        "20d",
        "60d",
        "120d",
        "252d",
    }


def test_cta_proxy_signal_declares_insufficient_history() -> None:
    signal = calculate_cta_proxy_signal(
        _bars(count=40),
        symbol="SPY",
        source="fixture",
        proxy_for="ES equity-index futures",
    )

    assert signal["available"] is False
    assert signal["quality_status"] == "unavailable"
    assert signal["warnings"]


def test_cta_workflow_steps_use_frozen_inputs() -> None:
    qqq = {"symbol": "QQQ", "history": {"bars": _bars()}}
    spy = {"symbol": "SPY", "history": {"bars": _bars(daily_return=-0.0008)}}
    context = RunContext(
        run_id="cta-run",
        run_type="pre_close",
        cutoff_time=datetime.now(UTC),
        symbols=["QQQ"],
        workflow_research_variant="cta",
        cta_proxy_symbols=["QQQ", "SPY"],
        cta_market_input={"provider": "fixture", "symbols": [qqq]},
        instrument_persistence_input={"provider": "fixture", "symbols": [qqq, spy]},
    )

    market = MarketCTAProxyStep().execute(context)
    instrument = InstrumentCTAProxyStep().execute(context)

    assert market.status == StepStatus.SUCCEEDED
    assert market.payload["variant"] == "cta"
    assert market.payload["aggregate"]["signal_count"] == 1
    assert instrument.status == StepStatus.SUCCEEDED
    assert instrument.payload["aggregate"]["signal_count"] == 2
    assert {item["symbol"] for item in instrument.payload["signals"]} == {"QQQ", "SPY"}
    parsed = EventSummary.model_validate({**instrument.payload, "data_state": "derived"})
    assert parsed.variant == "cta"
    assert len(parsed.signals) == 2


def test_cta_signal_aggregation_does_not_invent_missing_values() -> None:
    aggregate = aggregate_cta_proxy_signals(
        [{"available": False, "symbol": "QQQ"}]
    )
    assert aggregate == {
        "available": False,
        "signal_count": 0,
        "average_target_exposure": None,
        "average_pressure_index": None,
        "classification": "unavailable",
    }


def test_systematic_flows_preserve_position_and_mechanical_action_by_asset_class() -> None:
    qqq = {"symbol": "QQQ", "history": {"bars": _bars()}}
    tlt = {"symbol": "TLT", "history": {"bars": _bars(daily_return=-0.0008)}}
    context = RunContext(
        run_id="cta-run",
        run_type="post_close_review",
        cutoff_time=datetime.now(UTC),
        symbols=["QQQ"],
        workflow_research_variant="cta",
        cta_proxy_symbols=["QQQ", "TLT"],
        cta_market_input={"provider": "fixture", "symbols": [qqq]},
        instrument_persistence_input={"provider": "fixture", "symbols": [qqq, tlt]},
    )
    market = MarketCTAProxyStep().execute(context)
    instrument = InstrumentCTAProxyStep().execute(context)

    flows = build_systematic_flows(
        market.payload, instrument.payload, run_type="post_close_review"
    )

    assert flows["schema_version"] == "urus.systematic_flows.v1"
    assert flows["model_state"] == "official_close_model"
    assert flows["provisional"] is False
    assert {item["asset_class"] for item in flows["assets"]} == {"equity", "duration"}
    assert all("mechanical_action" in item for item in flows["assets"])
    assert set(flows["portfolio"]["asset_classes"]) == {"equity", "duration"}
