from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session
from sqlalchemy import delete, select, update

from app.core.time import utc_now
from app.models import (
    AIDecisionRunModel,
    AIDecisionSessionModel,
    AIModelTurnModel,
    AITraceNodeModel,
    AIToolCallModel,
    ForecastExperienceModel,
    ReportDisplayProjectionModel,
)
from app.urus_agent.contracts import AgentTask, DecisionResult
from app.urus_agent.trace import TraceNodeRecord


class ReportDeletionConflict(RuntimeError):
    """Raised when a report is still being produced and cannot be removed."""


def _is_official_scoreable_session(model: AIDecisionSessionModel) -> bool:
    policy = model.policy_json if isinstance(model.policy_json, dict) else {}
    report = model.decision_report_json if isinstance(model.decision_report_json, dict) else {}
    return (
        policy.get("official_cycle", True) is not False
        and policy.get("eligible_for_scoring", True) is not False
        and policy.get("trigger_type", "scheduled") != "manual"
        and policy.get("analysis_mode", "official_cycle") == "official_cycle"
        and report.get("official_cycle", True) is not False
        and report.get("eligible_for_scoring", True) is not False
        and report.get("trigger_type", "scheduled") != "manual"
        and report.get("analysis_mode", "official_cycle") == "official_cycle"
        and report.get("status", model.status) == "succeeded"
    )


class AIDecisionRepository:
    """Persist immutable Urus Agent results and their ordered tool trace."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        task: AgentTask,
        result: DecisionResult,
        *,
        started_at: datetime | None = None,
        decision_session_id: str | None = None,
        workflow_run_id: str | None = None,
        parent_decision_run_id: str | None = None,
        stage: str | None = None,
        sequence: int | None = None,
        trace_nodes: list[TraceNodeRecord] | None = None,
    ) -> AIDecisionRunModel:
        model = self.begin_run(
            task,
            started_at=started_at,
            decision_session_id=decision_session_id,
            workflow_run_id=workflow_run_id,
            parent_decision_run_id=parent_decision_run_id,
            stage=stage,
            sequence=sequence,
        )
        return self.complete_run(model.id, task, result, trace_nodes=trace_nodes)

    def begin_run(
        self,
        task: AgentTask,
        *,
        started_at: datetime | None = None,
        decision_session_id: str | None = None,
        workflow_run_id: str | None = None,
        parent_decision_run_id: str | None = None,
        stage: str | None = None,
        sequence: int | None = None,
    ) -> AIDecisionRunModel:
        now = utc_now()
        model = AIDecisionRunModel(
            id=str(uuid4()),
            decision_session_id=decision_session_id or task.decision_session_id,
            workflow_run_id=workflow_run_id or task.workflow_run_id,
            parent_decision_run_id=parent_decision_run_id or task.parent_decision_run_id,
            stage=stage or task.stage,
            sequence=sequence or task.sequence,
            task_type=task.task_type,
            status="running",
            dataset_key=task.dataset_key,
            source_run_ids=task.source_run_ids,
            source_snapshot_ids=task.source_snapshot_ids,
            cutoff_time=task.cutoff_time,
            target_symbol=task.target_symbol,
            requested_symbols=task.symbols,
            skill_name=task.requested_skill,
            skill_hash="pending",
            provider="pending",
            input_schema_version="urus.stage4b_decision_packet.v1",
            input_hash="pending",
            output_schema_version="unknown",
            parsed_output={},
            started_at=started_at or now,
            created_at=now,
        )
        self.session.add(model)
        self.session.commit()
        return model

    def complete_run(
        self,
        decision_id: str,
        task: AgentTask,
        result: DecisionResult,
        *,
        trace_nodes: list[TraceNodeRecord] | None = None,
    ) -> AIDecisionRunModel:
        now = utc_now()
        model = self.session.get(AIDecisionRunModel, decision_id)
        if model is None:
            raise KeyError(f"ai_decision_not_found:{decision_id}")
        output = result.output or {}
        model.status = result.status
        model.skill_name = result.skill_name or task.requested_skill
        model.skill_hash = result.skill_hash or "unknown"
        model.provider = result.provider
        model.model = result.model
        model.temperature = result.temperature
        model.input_hash = result.input_hash or "unknown"
        model.output_schema_version = str(output.get("schema_version") or "unknown")
        model.raw_output_text = result.raw_output
        model.parsed_output = output
        model.error_code = result.error_code
        model.error_message = result.error_message
        model.prompt_tokens = result.prompt_tokens
        model.completion_tokens = result.completion_tokens
        model.estimated_cost = result.estimated_cost
        model.completed_at = now
        self.session.add(model)
        self.session.flush()
        for sequence, call in enumerate(result.tool_calls, start=1):
            result_payload = call.get("result") or {}
            self.session.add(
                AIToolCallModel(
                    id=str(uuid4()),
                    decision_run_id=model.id,
                    sequence=sequence,
                    tool_call_id=call.get("tool_call_id"),
                    tool_name=str(call.get("name") or "unknown"),
                    arguments=call.get("arguments") or {},
                    result=result_payload,
                    ok=bool(result_payload.get("ok", False)),
                    error_code=(result_payload.get("error") or {}).get("code") if isinstance(result_payload.get("error"), dict) else None,
                    duration_ms=call.get("duration_ms"),
                    result_bytes=len(json.dumps(result_payload, ensure_ascii=False, default=str).encode("utf-8")),
                    prefetched=bool(call.get("prefetched", False)),
                    started_at=model.started_at,
                    completed_at=now,
                )
            )
        for turn in result.model_turns:
            self.session.add(
                AIModelTurnModel(
                    id=str(uuid4()),
                    decision_run_id=model.id,
                    trace_node_id=turn.get("trace_node_id"),
                    sequence=int(turn.get("sequence") or 0),
                    response_message=dict(turn.get("response_message") or {}),
                    raw_provider_response=dict(turn.get("raw_provider_response") or {}),
                    raw_response_bytes=int(turn.get("raw_response_bytes") or 0),
                    raw_response_truncated=bool(turn.get("raw_response_truncated", False)),
                    prompt_tokens=turn.get("prompt_tokens"),
                    completion_tokens=turn.get("completion_tokens"),
                    created_at=now,
                )
            )
        if trace_nodes:
            self._add_trace_nodes(model, trace_nodes)
        self.session.commit()
        return model

    def _add_trace_nodes(
        self,
        decision_run: AIDecisionRunModel,
        trace_nodes: list[TraceNodeRecord],
    ) -> None:
        session_id = decision_run.decision_session_id
        if not session_id:
            return
        existing_ids = {
            item[0]
            for item in self.session.execute(
                select(AITraceNodeModel.id).where(AITraceNodeModel.id.in_([node.id for node in trace_nodes]))
            )
        }
        for node in trace_nodes:
            if node.id in existing_ids:
                continue
            self.session.add(
                AITraceNodeModel(
                    id=node.id,
                    decision_session_id=session_id,
                    decision_run_id=node.decision_run_id or decision_run.id,
                    parent_node_id=node.parent_node_id,
                    depends_on_node_ids=node.depends_on_node_ids,
                    sequence=node.sequence,
                    lane=node.lane,
                    node_type=node.node_type,
                    label=node.label,
                    status=node.status,
                    input_summary=node.input_summary,
                    output_summary=node.output_summary,
                    evidence_refs=node.evidence_refs,
                    metrics=node.metrics,
                    error_code=node.error_code,
                    error_message=node.error_message,
                    started_at=node.started_at,
                    completed_at=node.completed_at,
                )
            )

    def save_trace_nodes(
        self,
        session_id: str,
        trace_nodes: list[TraceNodeRecord],
    ) -> None:
        if not trace_nodes:
            return
        # Avoid a fake run row: insert the trace rows directly when a session
        # node is not tied to an invocation.
        existing_ids = {
            item[0]
            for item in self.session.execute(
                select(AITraceNodeModel.id).where(AITraceNodeModel.id.in_([node.id for node in trace_nodes]))
            )
        }
        for node in trace_nodes:
            if node.id in existing_ids:
                continue
            self.session.add(
                AITraceNodeModel(
                    id=node.id,
                    decision_session_id=session_id,
                    decision_run_id=node.decision_run_id,
                    parent_node_id=node.parent_node_id,
                    depends_on_node_ids=node.depends_on_node_ids,
                    sequence=node.sequence,
                    lane=node.lane,
                    node_type=node.node_type,
                    label=node.label,
                    status=node.status,
                    input_summary=node.input_summary,
                    output_summary=node.output_summary,
                    evidence_refs=node.evidence_refs,
                    metrics=node.metrics,
                    error_code=node.error_code,
                    error_message=node.error_message,
                    started_at=node.started_at,
                    completed_at=node.completed_at,
                )
            )
        self.session.commit()

    def create_session(
        self,
        *,
        workflow_run_id: str,
        dataset_key: str,
        cutoff_time: datetime,
        policy: dict[str, object],
        technical_report: dict[str, object],
        decision_phase: str = "pre_close",
        trading_date: str = "",
        parent_session_id: str | None = None,
    ) -> AIDecisionSessionModel:
        now = utc_now()
        model = AIDecisionSessionModel(
            id=str(uuid4()),
            workflow_run_id=workflow_run_id,
            dataset_key=dataset_key,
            cutoff_time=cutoff_time,
            decision_phase=decision_phase,
            trading_date=trading_date,
            parent_session_id=parent_session_id,
            status="running",
            policy_json=policy,
            technical_report_schema_version=str(technical_report.get("schema_version") or "unknown"),
            technical_report_json=technical_report,
            started_at=now,
            created_at=now,
        )
        self.session.add(model)
        self.session.commit()
        return model

    def update_session(self, session_id: str, **values: object) -> AIDecisionSessionModel:
        model = self.session.get(AIDecisionSessionModel, session_id)
        if model is None:
            raise KeyError(f"decision_session_not_found:{session_id}")
        for field, value in values.items():
            setattr(model, field, value)
        self.session.add(model)
        self.session.commit()
        return model

    def get_session(self, session_id: str) -> AIDecisionSessionModel | None:
        return self.session.get(AIDecisionSessionModel, session_id)

    def delete_session(self, session_id: str) -> bool:
        """Delete one report and its Agent audit trail, preserving source data.

        A report owns its decision runs, model turns, tool calls and trace
        nodes. The workflow run and frozen snapshots remain available through
        the operations and dataset views.
        """

        model = self.get_session(session_id)
        if model is None:
            return False
        if model.status == "running":
            raise ReportDeletionConflict("研究报告仍在生成中，完成后才能删除。")

        child_sessions = list(
            self.session.scalars(
                select(AIDecisionSessionModel).where(
                    AIDecisionSessionModel.parent_session_id == session_id
                )
            )
        )
        if child_sessions:
            # The report JSON and review evidence are immutable. Re-parenting
            # only this foreign-key-like field would leave those persisted
            # references pointing at a deleted report, so keep the lineage
            # intact and require the dependent reports to be handled first.
            raise ReportDeletionConflict(
                "该报告仍被后续研究报告引用，请先处理后续报告后再删除。"
            )

        decision_runs = self.runs_for_session(session_id)
        decision_run_ids = [run.id for run in decision_runs]
        # Experiences are durable aggregate knowledge, not report-owned rows.
        # Clear both provenance pointers explicitly because SQLite deployments
        # do not universally enable foreign-key actions.
        self.session.execute(
            update(ForecastExperienceModel)
            .where(ForecastExperienceModel.source_report_id == session_id)
            .values(source_report_id=None)
        )
        self.session.execute(
            update(ForecastExperienceModel)
            .where(ForecastExperienceModel.source_pre_market_report_id == session_id)
            .values(source_pre_market_report_id=None)
        )
        # The chart projection is report-owned, but source runs and snapshots
        # remain available for operations/history inspection.
        self.session.execute(
            delete(ReportDisplayProjectionModel).where(
                ReportDisplayProjectionModel.report_id == session_id
            )
        )
        self.session.execute(
            delete(AITraceNodeModel).where(AITraceNodeModel.decision_session_id == session_id)
        )
        if decision_run_ids:
            self.session.execute(
                delete(AIToolCallModel).where(AIToolCallModel.decision_run_id.in_(decision_run_ids))
            )
            self.session.execute(
                delete(AIModelTurnModel).where(AIModelTurnModel.decision_run_id.in_(decision_run_ids))
            )
            self.session.execute(
                delete(AIDecisionRunModel).where(AIDecisionRunModel.id.in_(decision_run_ids))
            )
        self.session.delete(model)
        self.session.commit()
        return True

    def sessions_for_workflow(self, workflow_run_id: str) -> list[AIDecisionSessionModel]:
        statement = (
            select(AIDecisionSessionModel)
            .where(AIDecisionSessionModel.workflow_run_id == workflow_run_id)
            .order_by(AIDecisionSessionModel.created_at.desc())
        )
        return list(self.session.scalars(statement))

    def session_for_trading_phase(
        self, trading_date: str, decision_phase: str, *, before: datetime | None = None
    ) -> AIDecisionSessionModel | None:
        conditions = [
            AIDecisionSessionModel.trading_date == trading_date,
            AIDecisionSessionModel.decision_phase == decision_phase,
            AIDecisionSessionModel.decision_report_json.is_not(None),
            AIDecisionSessionModel.status == "succeeded",
        ]
        if before is not None:
            conditions.append(AIDecisionSessionModel.cutoff_time < before)
        statement = (
            select(AIDecisionSessionModel)
            .where(*conditions)
            .order_by(AIDecisionSessionModel.created_at.desc())
            .limit(20)
        )
        return next(
            (
                model
                for model in self.session.scalars(statement)
                if _is_official_scoreable_session(model)
            ),
            None,
        )

    def latest_session_before(
        self, trading_date: str, decision_phase: str
    ) -> AIDecisionSessionModel | None:
        statement = (
            select(AIDecisionSessionModel)
            .where(
                AIDecisionSessionModel.trading_date < trading_date,
                AIDecisionSessionModel.decision_phase == decision_phase,
                AIDecisionSessionModel.decision_report_json.is_not(None),
                AIDecisionSessionModel.status == "succeeded",
            )
            .order_by(
                AIDecisionSessionModel.trading_date.desc(),
                AIDecisionSessionModel.created_at.desc(),
            )
            .limit(20)
        )
        return next(
            (
                model
                for model in self.session.scalars(statement)
                if _is_official_scoreable_session(model)
            ),
            None,
        )

    def upsert_experience_candidates(
        self,
        *,
        source_report_id: str,
        source_pre_market_report_id: str | None,
        trading_date: str,
        candidates: list[dict[str, object]],
    ) -> None:
        now = utc_now()
        candidates_by_key = {
            str(candidate.get("pattern_key") or "").strip().lower(): candidate
            for candidate in candidates
            if str(candidate.get("pattern_key") or "").strip()
        }
        for pattern_key, candidate in candidates_by_key.items():
            existing = self.session.scalar(
                select(ForecastExperienceModel).where(
                    ForecastExperienceModel.pattern_key == pattern_key
                )
            )
            if existing is None:
                self.session.add(
                    ForecastExperienceModel(
                        id=str(uuid4()),
                        pattern_key=pattern_key,
                        category=str(candidate.get("category") or "forecast_error"),
                        statement=str(candidate.get("statement") or ""),
                        applicability_tags=list(candidate.get("applicability_tags") or []),
                        evidence_refs=list(candidate.get("evidence") or []),
                        status="candidate",
                        confidence=float(candidate.get("confidence") or 0.0),
                        occurrence_count=1,
                        support_count=1,
                        contradiction_count=0,
                        source_report_id=source_report_id,
                        source_pre_market_report_id=source_pre_market_report_id,
                        first_seen_trading_date=trading_date,
                        last_seen_trading_date=trading_date,
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                )
                continue
            same_observation = existing.last_seen_trading_date == trading_date
            existing.statement = str(candidate.get("statement") or existing.statement)
            existing.category = str(candidate.get("category") or existing.category)
            existing.applicability_tags = list(
                dict.fromkeys(
                    [
                        *list(existing.applicability_tags or []),
                        *list(candidate.get("applicability_tags") or []),
                    ]
                )
            )[:12]
            existing.evidence_refs = list(candidate.get("evidence") or existing.evidence_refs)
            existing.confidence = max(
                float(existing.confidence or 0.0),
                float(candidate.get("confidence") or 0.0),
            )
            existing.source_report_id = source_report_id
            existing.source_pre_market_report_id = source_pre_market_report_id
            existing.last_seen_at = now
            # Same-day retries may enrich evidence, tags and confidence, but
            # must not promote a lesson. Recurrence means another trading day
            # observed the falsifiable pattern again.
            if same_observation:
                continue
            existing.occurrence_count += 1
            existing.support_count += 1
            existing.status = "recurring" if existing.occurrence_count >= 2 else existing.status
            existing.last_seen_trading_date = trading_date
        self.session.commit()

    def active_experiences(self, limit: int = 8) -> list[dict[str, object]]:
        statement = (
            select(ForecastExperienceModel)
            .where(ForecastExperienceModel.status.in_(("candidate", "recurring", "validated")))
            .order_by(
                ForecastExperienceModel.status.desc(),
                ForecastExperienceModel.last_seen_at.desc(),
            )
            .limit(max(1, min(limit, 20)))
        )
        return [
            {
                "pattern_key": model.pattern_key,
                "category": model.category,
                "statement": model.statement,
                "applicability_tags": list(model.applicability_tags or []),
                "status": model.status,
                "confidence": model.confidence,
                "occurrence_count": model.occurrence_count,
                "support_count": model.support_count,
                "contradiction_count": model.contradiction_count,
                "last_seen_trading_date": model.last_seen_trading_date,
            }
            for model in self.session.scalars(statement)
        ]

    def list_sessions(self, limit: int = 50) -> list[AIDecisionSessionModel]:
        statement = (
            select(AIDecisionSessionModel)
            .order_by(AIDecisionSessionModel.created_at.desc())
            .limit(max(1, min(limit, 100)))
        )
        return list(self.session.scalars(statement))

    def get(self, decision_id: str) -> AIDecisionRunModel | None:
        return self.session.get(AIDecisionRunModel, decision_id)

    def list(self, limit: int = 50) -> list[AIDecisionRunModel]:
        statement = select(AIDecisionRunModel).order_by(AIDecisionRunModel.created_at.desc()).limit(max(1, min(limit, 100)))
        return list(self.session.scalars(statement))

    def runs_for_session(self, session_id: str) -> list[AIDecisionRunModel]:
        statement = (
            select(AIDecisionRunModel)
            .where(AIDecisionRunModel.decision_session_id == session_id)
            .order_by(AIDecisionRunModel.sequence)
        )
        return list(self.session.scalars(statement))

    def tool_calls(self, decision_id: str) -> list[AIToolCallModel]:
        statement = select(AIToolCallModel).where(AIToolCallModel.decision_run_id == decision_id).order_by(AIToolCallModel.sequence)
        return list(self.session.scalars(statement))

    def trace_nodes(self, session_id: str) -> list[AITraceNodeModel]:
        statement = (
            select(AITraceNodeModel)
            .where(AITraceNodeModel.decision_session_id == session_id)
            .order_by(AITraceNodeModel.sequence)
        )
        return list(self.session.scalars(statement))

    def model_turns(self, decision_id: str) -> list[AIModelTurnModel]:
        statement = (
            select(AIModelTurnModel)
            .where(AIModelTurnModel.decision_run_id == decision_id)
            .order_by(AIModelTurnModel.sequence)
        )
        return list(self.session.scalars(statement))

    def session_summary(self, session_id: str) -> dict[str, object]:
        """Return bounded run/tool/token metadata for the report header."""

        runs = self.runs_for_session(session_id)
        durations = [
            int((run.completed_at - run.started_at).total_seconds() * 1000)
            for run in runs
            if run.completed_at is not None and run.started_at is not None
        ]
        tool_count = 0
        prefetched_tool_count = 0
        for run in runs:
            calls = self.tool_calls(run.id)
            tool_count += len(calls)
            prefetched_tool_count += sum(bool(call.prefetched) for call in calls)
        providers = sorted({str(run.provider) for run in runs if run.provider and run.provider != "pending"})
        models = sorted({str(run.model) for run in runs if run.model})
        skill_hashes = sorted({str(run.skill_hash) for run in runs if run.skill_hash and run.skill_hash != "pending"})
        return {
            "run_count": len(runs),
            "tool_call_count": tool_count,
            "prefetched_tool_count": prefetched_tool_count,
            "model_requested_tool_count": tool_count - prefetched_tool_count,
            "prompt_tokens": sum(int(run.prompt_tokens or 0) for run in runs),
            "completion_tokens": sum(int(run.completion_tokens or 0) for run in runs),
            "estimated_cost": round(sum(float(run.estimated_cost or 0) for run in runs), 8) or None,
            "duration_ms": sum(durations),
            "providers": providers,
            "models": models,
            "skill_hashes": skill_hashes,
            "statuses": [str(run.status) for run in runs],
        }
