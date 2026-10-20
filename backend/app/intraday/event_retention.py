"""Two-session retention for owned equity events, preserving active setups."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.intraday_events import IntradayEventModel, IntradaySetupStateModel


def prune_event_history(
    session: Session,
    *,
    processor_id: str,
    now: datetime,
    dry_run: bool = True,
    task_names: Sequence[str] | None = None,
    market_calendar: str = "XNYS",
) -> dict[str, object]:
    import exchange_calendars as xcals

    if not processor_id.strip() or now.tzinfo is None:
        raise ValueError("event retention requires processor identity and aware time")
    day = now.astimezone(ZoneInfo("America/New_York")).date()
    calendar = xcals.get_calendar(market_calendar)
    retained_sessions = calendar.sessions_in_range(day - timedelta(days=30), day)[-2:]
    if len(retained_sessions) != 2:
        raise ValueError("cannot determine event retention sessions")
    oldest = retained_sessions[0].date().isoformat()
    scope = [IntradayEventModel.processor_id == processor_id]
    if task_names is not None:
        scope.append(IntradayEventModel.task_name.in_(list(task_names)))
    latest_ids = select(func.max(IntradayEventModel.id)).where(*scope).group_by(IntradayEventModel.setup_id)
    protected = set(session.scalars(
        select(IntradayEventModel.setup_id).where(
            IntradayEventModel.id.in_(latest_ids), IntradayEventModel.status.in_(("candidate", "confirmed")),
        )
    ))
    state_query = select(IntradaySetupStateModel).where(IntradaySetupStateModel.processor_id == processor_id)
    if task_names is not None:
        state_query = state_query.where(IntradaySetupStateModel.task_name.in_(list(task_names)))
    for state in session.scalars(state_query):
        for setup in (state.state_json or {}).get("setups", []):
            if setup.get("status") in {"candidate", "confirmed"}:
                protected.add(str(setup.get("setup_id") or ""))
                parent = setup.get("parent_setup_id")
                if parent:
                    protected.add(str(parent))
    eligible = [
        *scope, IntradayEventModel.trading_date < oldest,
        func.length(IntradayEventModel.trading_date) == 10,
        IntradayEventModel.setup_id.not_in(protected),
    ]
    count = session.scalar(select(func.count()).select_from(IntradayEventModel).where(*eligible)) or 0
    deleted = 0
    if not dry_run and count:
        deleted = session.execute(delete(IntradayEventModel).where(*eligible)).rowcount
    return {
        "oldest_retained_date": oldest, "eligible_events": count,
        "deleted_events": deleted, "protected_setups": len(protected), "dry_run": dry_run,
    }
