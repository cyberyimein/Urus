from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.intraday.events import _STATE_SCHEMA
from app.models.intraday_events import (
    IntradayEventIngestCursorModel,
    IntradayEventModel,
    IntradaySetupStateModel,
)


class IntradayEventRepository:
    """Persistence boundary for intraday state, immutable events, and cursor."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_cursor(self, processor_id: str) -> IntradayEventIngestCursorModel | None:
        return self.session.get(IntradayEventIngestCursorModel, processor_id)

    def get_state(self, processor_id: str, task_name: str) -> dict[str, Any]:
        row = self.session.scalar(
            select(IntradaySetupStateModel).where(
                IntradaySetupStateModel.processor_id == processor_id,
                IntradaySetupStateModel.task_name == task_name,
            )
        )
        if row is None or not isinstance(row.state_json, Mapping):
            return {"schema_version": _STATE_SCHEMA, "setups": [], "history": []}
        return dict(row.state_json)

    def get_state_row(
        self, processor_id: str, task_name: str
    ) -> IntradaySetupStateModel | None:
        return self.session.scalar(
            select(IntradaySetupStateModel).where(
                IntradaySetupStateModel.processor_id == processor_id,
                IntradaySetupStateModel.task_name == task_name,
            )
        )

    def save_slot(
        self,
        *,
        processor_id: str,
        task_name: str,
        state: Mapping[str, Any],
        transitions: Iterable[Mapping[str, Any]],
        cursor_values: Mapping[str, Any],
        now: datetime,
    ) -> int:
        state_row = self.get_state_row(processor_id, task_name)
        if state_row is None:
            state_row = IntradaySetupStateModel(
                processor_id=processor_id,
                task_name=task_name,
                state_json=dict(state),
                updated_at=now,
            )
            self.session.add(state_row)
        else:
            state_row.state_json = dict(state)
            state_row.updated_at = now

        created_count = 0
        for transition in transitions:
            setup_id = str(transition.get("setup_id") or "")
            transition_seq = int(transition.get("transition_seq") or 0)
            if not setup_id or transition_seq <= 0:
                continue
            exists = self.session.scalar(
                select(IntradayEventModel.id).where(
                    IntradayEventModel.processor_id == processor_id,
                    IntradayEventModel.setup_id == setup_id,
                    IntradayEventModel.transition_seq == transition_seq,
                )
            )
            if exists is not None:
                continue
            payload = dict(transition)
            self.session.add(
                IntradayEventModel(
                    processor_id=processor_id,
                    setup_id=setup_id,
                    transition_seq=transition_seq,
                    task_name=str(transition.get("task_name") or task_name),
                    symbol=str(transition.get("symbol") or ""),
                    strategy=str(transition.get("strategy") or ""),
                    setup_type=str(transition.get("setup_type") or ""),
                    transition=str(transition.get("transition") or ""),
                    status=str(transition.get("status") or ""),
                    timeframe=str(transition.get("timeframe") or "5m"),
                    rule_version=str(transition.get("rule_version") or ""),
                    trading_date=str(transition.get("trading_date") or ""),
                    evidence_cutoff=str(transition.get("evidence_cutoff") or ""),
                    created_at=now,
                    payload_json=payload,
                )
            )
            created_count += 1

        self.save_cursor(processor_id, cursor_values, now=now)
        return created_count

    def save_cursor(
        self,
        processor_id: str,
        values: Mapping[str, Any],
        *,
        now: datetime,
    ) -> IntradayEventIngestCursorModel:
        row = self.get_cursor(processor_id)
        if row is None:
            row = IntradayEventIngestCursorModel(
                processor_id=processor_id,
                source_path_identity=str(values["source_path_identity"]),
                updated_at=now,
            )
            self.session.add(row)
        for field in (
            "source_path_identity",
            "last_scheduled_at",
            "last_task_name",
            "last_capture_id",
            "last_content_sha256",
            "seen_source_slots_json",
            "backlog_remaining",
            "last_status",
            "last_error",
            "capabilities_summary_json",
        ):
            if field in values:
                setattr(row, field, values[field])
        row.updated_at = now
        return row

    def list_events(
        self,
        *,
        processor_id: str,
        after_id: int | None,
        limit: int,
        symbol: str | None = None,
        strategy: str | None = None,
        status: str | None = None,
    ) -> list[IntradayEventModel]:
        statement = select(IntradayEventModel).where(
            IntradayEventModel.processor_id == processor_id
        )
        if after_id is not None:
            statement = statement.where(IntradayEventModel.id > after_id)
        if symbol:
            statement = statement.where(IntradayEventModel.symbol == symbol.upper())
        if strategy:
            statement = statement.where(IntradayEventModel.strategy == strategy.upper())
        if status:
            statement = statement.where(IntradayEventModel.status == status.lower())
        statement = statement.order_by(IntradayEventModel.id.asc()).limit(limit)
        return list(self.session.scalars(statement))

    def event_tail(
        self,
        *,
        processor_id: str,
        limit: int,
        symbol: str | None = None,
        strategy: str | None = None,
        status: str | None = None,
    ) -> list[IntradayEventModel]:
        statement = select(IntradayEventModel).where(
            IntradayEventModel.processor_id == processor_id
        )
        if symbol:
            statement = statement.where(IntradayEventModel.symbol == symbol.upper())
        if strategy:
            statement = statement.where(IntradayEventModel.strategy == strategy.upper())
        if status:
            statement = statement.where(IntradayEventModel.status == status.lower())
        rows = list(
            self.session.scalars(
                statement.order_by(IntradayEventModel.id.desc()).limit(limit)
            )
        )
        return list(reversed(rows))

    def event_statuses(
        self, processor_id: str, setup_ids: Iterable[str]
    ) -> dict[str, str]:
        requested_ids = {str(setup_id) for setup_id in setup_ids if str(setup_id)}
        if not requested_ids:
            return {}
        statuses: dict[str, str] = {}
        rows = self.session.execute(
            select(IntradayEventModel.setup_id, IntradayEventModel.status).where(
                IntradayEventModel.processor_id == processor_id,
                IntradayEventModel.setup_id.in_(requested_ids),
            )
            .order_by(IntradayEventModel.id.desc())
        )
        for setup_id, status in rows:
            statuses.setdefault(str(setup_id), str(status or "unknown"))
        return statuses

    def current_health(self, processor_id: str) -> IntradayEventIngestCursorModel | None:
        return self.get_cursor(processor_id)


__all__ = ["IntradayEventRepository"]
