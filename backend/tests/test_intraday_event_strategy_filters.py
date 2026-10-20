"""Event filters accept every implemented strategy shown by the intraday UI."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import Base
from app.intraday.event_service import PROCESSOR_ID
from app.main import create_app
from test_intraday_event_retention import _event


@pytest.mark.parametrize("strategy", ["S4", "S5", "S6", "S7", "S8", "s8"])
def test_extended_strategy_filter_returns_only_its_own_events(tmp_path, strategy):
    database = tmp_path / "events.sqlite"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(engine)
    expected = _event("extended", "2026-10-16", processor=PROCESSOR_ID)
    expected.strategy = strategy.upper()
    expected.payload_json = {"strategy": strategy.upper(), "setup_id": "extended"}
    other = _event("other", "2026-10-16", processor=PROCESSOR_ID)
    with Session(engine) as session:
        session.add_all([expected, other])
        session.commit()
    engine.dispose()
    settings = Settings(
        app_env="test", database_url=f"sqlite:///{database}",
        intraday_events_enabled=True, moomoo_enabled=False,
        fred_enabled=False, yahoo_enabled=False, anomalo_workflow_enabled=False,
        observation_universe_source_url="",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/intraday/events", params={"strategy": strategy})
        assert response.status_code == 200, response.text
        events = response.json()["events"]
        assert len(events) == 1
        assert events[0]["strategy"] == strategy.upper()
        assert events[0]["setup_id"] == "extended"
        assert client.get("/api/intraday/events?strategy=S9").status_code == 400
