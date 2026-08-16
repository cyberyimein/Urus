from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.database import Base, create_database
from app.models import (
    AIDecisionRunModel,
    AIDecisionSessionModel,
    AIModelTurnModel,
    AITraceNodeModel,
    AIToolCallModel,
    ForecastExperienceModel,
    RunModel,
)
from app.repositories.agent import AIDecisionRepository, ReportDeletionConflict


def _seed_report(session, *, with_child: bool = False) -> None:
    now = datetime(2026, 8, 13, 6, tzinfo=UTC)
    session.add(
        RunModel(
            id="workflow-1",
            run_type="manual_analysis",
            status="succeeded",
            cutoff_time=now,
        )
    )
    session.add(
        AIDecisionSessionModel(
            id="report-1",
            workflow_run_id="workflow-1",
            dataset_key="manual-analysis:test",
            cutoff_time=now,
            decision_phase="current_state",
            trading_date="2026-08-13",
            parent_session_id=None,
            status="succeeded",
            policy_json={},
            technical_report_schema_version="urus.technical_report.v1",
            technical_report_json={"schema_version": "urus.technical_report.v1"},
            decision_report_schema_version="urus.ai_decision_report.v5",
            decision_report_json={"status": "succeeded"},
            started_at=now,
            completed_at=now,
            created_at=now,
        )
    )
    if with_child:
        session.add(
            AIDecisionSessionModel(
                id="report-child",
                workflow_run_id="workflow-1",
                dataset_key="manual-analysis:test-child",
                cutoff_time=now,
                decision_phase="post_close_review",
                trading_date="2026-08-13",
                parent_session_id="report-1",
                status="succeeded",
                policy_json={},
                technical_report_schema_version="urus.technical_report.v1",
                technical_report_json={},
                decision_report_schema_version="urus.ai_decision_report.v5",
                decision_report_json={"status": "succeeded"},
                started_at=now,
                completed_at=now,
                created_at=now,
            )
        )
    session.add(
        AIDecisionRunModel(
            id="decision-run-1",
            decision_session_id="report-1",
            workflow_run_id="workflow-1",
            stage="synthesis",
            sequence=1,
            task_type="equity_ranking",
            status="succeeded",
            dataset_key="manual-analysis:test",
            source_run_ids=["workflow-1"],
            source_snapshot_ids=[],
            cutoff_time=now,
            target_symbol=None,
            requested_symbols=["QQQ"],
            skill_name="urus-equity-decision",
            skill_hash="hash",
            provider="test",
            model="test-model",
            temperature=0.0,
            input_schema_version="urus.stage4b_decision_packet.v1",
            input_hash="input",
            output_schema_version="urus.equity_decision.v3",
            raw_output_text="{}",
            parsed_output={},
            started_at=now,
            completed_at=now,
            created_at=now,
        )
    )
    session.add(
        AIToolCallModel(
            id="tool-call-1",
            decision_run_id="decision-run-1",
            sequence=1,
            tool_name="get_market_snapshot",
            arguments={},
            result={"ok": True},
            ok=True,
            started_at=now,
            completed_at=now,
        )
    )
    session.add(
        AIModelTurnModel(
            id="model-turn-1",
            decision_run_id="decision-run-1",
            trace_node_id="trace-1",
            sequence=1,
            response_message={},
            raw_provider_response={},
            raw_response_bytes=2,
            raw_response_truncated=False,
            created_at=now,
        )
    )
    session.add(
        AITraceNodeModel(
            id="trace-1",
            decision_session_id="report-1",
            decision_run_id="decision-run-1",
            parent_node_id=None,
            depends_on_node_ids=[],
            sequence=1,
            lane="equity",
            node_type="synthesis",
            label="Test report",
            status="succeeded",
            input_summary={},
            output_summary={},
            evidence_refs=[],
            metrics={},
            started_at=now,
            completed_at=now,
        )
    )
    session.add(
        ForecastExperienceModel(
            id="experience-1",
            pattern_key="test.durable_experience",
            category="risk_rule",
            statement="Keep this aggregate lesson after deleting its latest report.",
            applicability_tags=[],
            evidence_refs=[],
            status="recurring",
            confidence=0.7,
            occurrence_count=2,
            support_count=2,
            contradiction_count=0,
            source_report_id="report-1",
            source_pre_market_report_id="report-1",
            first_seen_trading_date="2026-08-12",
            last_seen_trading_date="2026-08-13",
            first_seen_at=now,
            last_seen_at=now,
        )
    )
    session.commit()


def test_delete_report_removes_agent_audit_but_preserves_workflow_run(tmp_path) -> None:
    engine, factory = create_database(f"sqlite:///{tmp_path / 'reports.db'}")
    Base.metadata.create_all(bind=engine)
    with factory() as session:
        _seed_report(session)
        repository = AIDecisionRepository(session)

        assert repository.delete_session("report-1") is True

        assert session.get(AIDecisionSessionModel, "report-1") is None
        assert session.get(AIDecisionRunModel, "decision-run-1") is None
        assert session.get(AIToolCallModel, "tool-call-1") is None
        assert session.get(AIModelTurnModel, "model-turn-1") is None
        assert session.get(AITraceNodeModel, "trace-1") is None
        experience = session.get(ForecastExperienceModel, "experience-1")
        assert experience is not None
        assert experience.source_report_id is None
        assert experience.source_pre_market_report_id is None
        assert session.get(RunModel, "workflow-1") is not None
        assert repository.delete_session("missing-report") is False
    engine.dispose()


def test_delete_report_rejects_running_session(tmp_path) -> None:
    engine, factory = create_database(f"sqlite:///{tmp_path / 'running-report.db'}")
    Base.metadata.create_all(bind=engine)
    with factory() as session:
        _seed_report(session)
        report = session.get(AIDecisionSessionModel, "report-1")
        assert report is not None
        report.status = "running"
        session.commit()

        with pytest.raises(ReportDeletionConflict):
            AIDecisionRepository(session).delete_session("report-1")
        assert session.get(AIDecisionSessionModel, "report-1") is not None
    engine.dispose()


def test_delete_report_rejects_report_with_children(tmp_path) -> None:
    engine, factory = create_database(f"sqlite:///{tmp_path / 'dependent-report.db'}")
    Base.metadata.create_all(bind=engine)
    with factory() as session:
        _seed_report(session, with_child=True)

        with pytest.raises(ReportDeletionConflict, match="后续研究报告引用"):
            AIDecisionRepository(session).delete_session("report-1")

        assert session.get(AIDecisionSessionModel, "report-1") is not None
        child = session.get(AIDecisionSessionModel, "report-child")
        assert child is not None and child.parent_session_id == "report-1"
    engine.dispose()


def test_delete_report_endpoint_returns_not_found_after_removal(client) -> None:
    now = datetime(2026, 8, 13, 6, tzinfo=UTC)
    with client.app.state.session_factory() as session:
        session.add(
            RunModel(
                id="workflow-api-1",
                run_type="manual_analysis",
                status="succeeded",
                cutoff_time=now,
            )
        )
        session.add(
            AIDecisionSessionModel(
                id="report-api-1",
                workflow_run_id="workflow-api-1",
                dataset_key="manual-analysis:api",
                cutoff_time=now,
                decision_phase="current_state",
                trading_date="2026-08-13",
                status="succeeded",
                policy_json={},
                technical_report_schema_version="urus.technical_report.v1",
                technical_report_json={},
                started_at=now,
                created_at=now,
            )
        )
        session.commit()

    response = client.delete("/api/research-reports/report-api-1")
    assert response.status_code == 200
    assert response.json() == {"report_id": "report-api-1", "deleted": True}
    missing = client.delete("/api/research-reports/report-api-1")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "research_report_not_found"
