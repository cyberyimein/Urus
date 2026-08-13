"""persist Urus Agent decision and tool-call audit records

Revision ID: 0007_urus_agent_decision_audit
Revises: 0006_strategy_research_datasets
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_urus_agent_decision_audit"
down_revision = "0006_strategy_research_datasets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_decision_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("dataset_key", sa.String(length=128), nullable=False),
        sa.Column("source_run_ids", sa.JSON(), nullable=False),
        sa.Column("source_snapshot_ids", sa.JSON(), nullable=False),
        sa.Column("cutoff_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_symbol", sa.String(length=16), nullable=True),
        sa.Column("requested_symbols", sa.JSON(), nullable=False),
        sa.Column("skill_name", sa.String(length=128), nullable=False),
        sa.Column("skill_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("input_schema_version", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("output_schema_version", sa.String(length=64), nullable=False),
        sa.Column("raw_output_text", sa.Text(), nullable=True),
        sa.Column("parsed_output", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost", sa.Float(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_decision_runs_dataset_task",
        "ai_decision_runs",
        ["dataset_key", "task_type", "created_at"],
    )
    op.create_index(
        "ix_ai_decision_runs_target_created",
        "ai_decision_runs",
        ["target_symbol", "created_at"],
    )
    op.create_index(
        "ix_ai_decision_runs_status_created",
        "ai_decision_runs",
        ["status", "created_at"],
    )
    op.create_table(
        "ai_tool_calls",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("decision_run_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("tool_call_id", sa.String(length=128), nullable=True),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("result_bytes", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["decision_run_id"], ["ai_decision_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("decision_run_id", "sequence", name="uq_ai_tool_calls_run_sequence"),
    )
    op.create_index(
        "ix_ai_tool_calls_run_sequence",
        "ai_tool_calls",
        ["decision_run_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_tool_calls_run_sequence", table_name="ai_tool_calls")
    op.drop_table("ai_tool_calls")
    op.drop_index("ix_ai_decision_runs_status_created", table_name="ai_decision_runs")
    op.drop_index("ix_ai_decision_runs_target_created", table_name="ai_decision_runs")
    op.drop_index("ix_ai_decision_runs_dataset_task", table_name="ai_decision_runs")
    op.drop_table("ai_decision_runs")
