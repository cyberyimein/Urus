from app.integrations.anomalo import MockAnomaloAdapter
from app.integrations.decision import MockDecisionAdapter
from app.integrations.moomoo import DisabledMoomooAdapter


def test_framework_integrations_are_offline_mock_adapters() -> None:
    assert DisabledMoomooAdapter().market_card("QQQ")["is_mock"] is True
    offline = DisabledMoomooAdapter().instrument_cards(["SPY", "INTC", "NVDA"])
    assert offline["is_mock"] is True
    assert offline["data_state"] == "unavailable"
    assert offline["unavailable_symbols"] == ["SPY", "INTC", "NVDA"]
    assert MockAnomaloAdapter().summarize.__qualname__.startswith("MockAnomaloAdapter")
    assert MockDecisionAdapter().decide.__qualname__.startswith("MockDecisionAdapter")
