from app.integrations.anomalo import MockAnomaloAdapter
from app.integrations.decision import MockDecisionAdapter
from app.integrations.moomoo import DisabledMoomooAdapter


def test_framework_integrations_are_offline_mock_adapters() -> None:
    assert DisabledMoomooAdapter().market_card("QQQ")["is_mock"] is True
    assert MockAnomaloAdapter().summarize.__qualname__.startswith("MockAnomaloAdapter")
    assert MockDecisionAdapter().decide.__qualname__.startswith("MockDecisionAdapter")

