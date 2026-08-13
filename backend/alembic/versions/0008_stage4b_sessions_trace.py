"""persist Stage 4B sessions, trace nodes, and raw model turns

Revision ID: 0008_stage4b_sessions_trace
Revises: 0007_urus_agent_decision_audit
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_stage4b_sessions_trace"
down_revision = "0007_urus_agent_decision_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_decision_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_run_id", sa.String(length=36), nullable=False),
        sa.Column("dataset_key", sa.String(length=128), nullable=False),
        sa.Column("cutoff_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("policy_json", sa.JSON(), nullable=False),
        sa.Column("technical_report_schema_version", sa.String(length=64), nullable=False),
        sa.Column("technical_report_json", sa.JSON(), nullable=False),
        sa.Column("decision_report_schema_version", sa.String(length=64), nullable=True),
        sa.Column("decision_report_json", sa.JSON(), nullable=True),
        sa.Column("equity_decision_run_id", sa.String(length=36), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_decision_sessions_workflow_created",
        "ai_decision_sessions",
        ["workflow_run_id", "created_at"],
    )
    op.create_index(
        "ix_ai_decision_sessions_status_created",
        "ai_decision_sessions",
        ["status", "created_at"],
    )

    with op.batch_alter_table("ai_decision_runs") as batch:
        batch.add_column(sa.Column("decision_session_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("workflow_run_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("parent_decision_run_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("stage", sa.String(length=24), nullable=False, server_default="equity"))
        batch.add_column(sa.Column("sequence", sa.Integer(), nullable=False, server_default="1"))
        batch.create_index("ix_ai_decision_runs_decision_session_id", ["decision_session_id"])
        batch.create_index("ix_ai_decision_runs_workflow_run_id", ["workflow_run_id"])
        batch.create_index("ix_ai_decision_runs_parent_decision_run_id", ["parent_decision_run_id"])

    op.create_table(
        "ai_trace_nodes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("decision_session_id", sa.String(length=36), nullable=False),
        sa.Column("decision_run_id", sa.String(length=36), nullable=True),
        sa.Column("parent_node_id", sa.String(length=36), nullable=True),
        sa.Column("depends_on_node_ids", sa.JSON(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("lane", sa.String(length=32), nullable=False),
        sa.Column("node_type", sa.String(length=24), nullable=False),
        sa.Column("label", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("input_summary", sa.JSON(), nullable=False),
        sa.Column("output_summary", sa.JSON(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["decision_session_id"], ["ai_decision_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_trace_nodes_session_sequence",
        "ai_trace_nodes",
        ["decision_session_id", "sequence"],
    )
    op.create_index("ix_ai_trace_nodes_run_sequence", "ai_trace_nodes", ["decision_run_id", "sequence"])
    op.create_index("ix_ai_trace_nodes_parent_node_id", "ai_trace_nodes", ["parent_node_id"])

    op.create_table(
        "ai_model_turns",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("decision_run_id", sa.String(length=36), nullable=False),
        sa.Column("trace_node_id", sa.String(length=36), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("response_message", sa.JSON(), nullable=False),
        sa.Column("raw_provider_response", sa.JSON(), nullable=False),
        sa.Column("raw_response_bytes", sa.Integer(), nullable=False),
        sa.Column("raw_response_truncated", sa.Boolean(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["decision_run_id"], ["ai_decision_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("decision_run_id", "sequence", name="uq_ai_model_turns_run_sequence"),
    )
    op.create_index("ix_ai_model_turns_node_sequence", "ai_model_turns", ["trace_node_id", "sequence"])
    op.create_index("ix_ai_model_turns_trace_node_id", "ai_model_turns", ["trace_node_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_model_turns_trace_node_id", table_name="ai_model_turns")
    op.drop_index("ix_ai_model_turns_node_sequence", table_name="ai_model_turns")
    op.drop_table("ai_model_turns")
    op.drop_index("ix_ai_trace_nodes_parent_node_id", table_name="ai_trace_nodes")
    op.drop_index("ix_ai_trace_nodes_run_sequence", table_name="ai_trace_nodes")
    op.drop_index("ix_ai_trace_nodes_session_sequence", table_name="ai_trace_nodes")
    op.drop_table("ai_trace_nodes")
    with op.batch_alter_table("ai_decision_runs") as batch:
        batch.drop_index("ix_ai_decision_runs_parent_decision_run_id")
        batch.drop_index("ix_ai_decision_runs_workflow_run_id")
        batch.drop_index("ix_ai_decision_runs_decision_session_id")
        batch.drop_column("sequence")
        batch.drop_column("stage")
        batch.drop_column("parent_decision_run_id")
        batch.drop_column("workflow_run_id")
        batch.drop_column("decision_session_id")
    op.drop_index("ix_ai_decision_sessions_status_created", table_name="ai_decision_sessions")
    op.drop_index("ix_ai_decision_sessions_workflow_created", table_name="ai_decision_sessions")
    op.drop_table("ai_decision_sessions")
