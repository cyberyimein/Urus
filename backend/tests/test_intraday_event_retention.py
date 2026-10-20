from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.intraday.event_retention import prune_event_history
from app.models.intraday_events import IntradayEventModel, IntradaySetupStateModel


def _event(setup, day, *, processor="test-equities", status="expired"):
    return IntradayEventModel(
        processor_id=processor, setup_id=setup, transition_seq=1, task_name="equities",
        symbol="SPY", strategy="S2", setup_type="ma5_reclaim", transition="setup_expired",
        status=status, timeframe="5m", rule_version="test-v1", trading_date=day,
        evidence_cutoff=day + "T14:00:00+00:00", created_at=datetime(2026, 10, 16, tzinfo=UTC),
        payload_json={},
    )


def test_event_retention_dry_run_then_apply_only_owned_terminal_history():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            _event("old", "2026-10-14"), _event("retained", "2026-10-15"),
            _event("active", "2026-10-14", status="confirmed"),
            _event("other", "2026-10-14", processor="other-processor"),
        ])
        session.add(IntradaySetupStateModel(
            processor_id="test-equities", task_name="equities",
            state_json={"setups": [{"setup_id": "active", "status": "confirmed"}]},
            updated_at=datetime(2026, 10, 16, tzinfo=UTC),
        ))
        session.commit()
        args = dict(processor_id="test-equities", now=datetime(2026, 10, 16, 15, tzinfo=UTC))
        plan = prune_event_history(session, **args, dry_run=True)
        assert plan["oldest_retained_date"] == "2026-10-15"
        assert plan["eligible_events"] == 1 and plan["deleted_events"] == 0
        assert len(session.scalars(select(IntradayEventModel)).all()) == 4
        applied = prune_event_history(session, **args, dry_run=False)
        session.commit()
        assert applied["deleted_events"] == 1
        assert {event.setup_id for event in session.scalars(select(IntradayEventModel))} == {"retained", "active", "other"}
    engine.dispose()


def test_weekend_does_not_count_as_retention_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        result = prune_event_history(
            session, processor_id="test-equities", now=datetime(2026, 10, 18, 15, tzinfo=UTC), dry_run=True,
        )
        assert result["oldest_retained_date"] == "2026-10-15"
    engine.dispose()
