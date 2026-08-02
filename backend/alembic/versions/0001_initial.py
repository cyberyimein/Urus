"""create initial workflow tables

Revision ID: 0001_initial
Revises:
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cutoff_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_id", sa.String(length=36), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runs_status", "runs", ["status"], unique=False)
    op.create_index("ix_runs_snapshot_id", "runs", ["snapshot_id"], unique=False)

    op.create_table(
        "step_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("step_code", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "step_code", name="uq_step_runs_run_step"),
    )
    op.create_index("ix_step_runs_run_position", "step_runs", ["run_id", "position"], unique=False)
    op.create_index("ix_step_runs_status", "step_runs", ["status"], unique=False)

    op.create_table(
        "snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("cutoff_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quality_status", sa.String(length=24), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_snapshots_run_id"),
    )


def downgrade() -> None:
    op.drop_table("snapshots")
    op.drop_index("ix_step_runs_status", table_name="step_runs")
    op.drop_index("ix_step_runs_run_position", table_name="step_runs")
    op.drop_table("step_runs")
    op.drop_index("ix_runs_snapshot_id", table_name="runs")
    op.drop_index("ix_runs_status", table_name="runs")
    op.drop_table("runs")

