"""Add immutable observation groups, group snapshots and observation runs.

Revision ID: 0020_observation_groups
Revises: 0019_strategy_decisions
"""

from alembic import op
import sqlalchemy as sa


revision = "0020_observation_groups"
down_revision = "0019_strategy_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    phase_c_tables = {
        "observation_group_versions",
        "group_daily_snapshots",
        "observation_runs",
    }
    if phase_c_tables.issubset(existing):
        # Older application startup code called Base.metadata.create_all before
        # Alembic. Preserve those tables and let 0021 reconcile the snapshot
        # uniqueness contract instead of attempting destructive recreation.
        return
    partial = phase_c_tables & existing
    if partial:
        raise RuntimeError(
            "Phase C migration found an incomplete pre-created schema: "
            + ", ".join(sorted(partial))
        )
    op.create_table(
        "observation_group_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("symbols", sa.JSON(), nullable=False),
        sa.Column("benchmark_symbols", sa.JSON(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("group_id", "version", name="uq_observation_group_id_version"),
    )
    op.create_index(
        "ix_observation_group_status_order",
        "observation_group_versions",
        ["status", "display_order"],
    )
    op.create_table(
        "group_daily_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_version_id", sa.String(36), nullable=False),
        sa.Column("dataset_id", sa.String(36), nullable=False),
        sa.Column("group_id", sa.String(64), nullable=False),
        sa.Column("group_version", sa.Integer(), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["group_version_id"], ["observation_group_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id"], ["daily_decision_datasets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("group_version_id", "dataset_id", name="uq_group_daily_snapshot_version_dataset"),
    )
    op.create_index(
        "ix_group_daily_snapshots_group_date",
        "group_daily_snapshots",
        ["group_id", "trading_date"],
    )
    op.create_table(
        "observation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("trigger_mode", sa.String(16), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("cutoff_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("group_ids", sa.JSON(), nullable=False),
        sa.Column("group_version_ids", sa.JSON(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_observation_runs_date_status", "observation_runs", ["trading_date", "status"])
    op.create_index("ix_observation_runs_created", "observation_runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_observation_runs_created", table_name="observation_runs")
    op.drop_index("ix_observation_runs_date_status", table_name="observation_runs")
    op.drop_table("observation_runs")
    op.drop_index("ix_group_daily_snapshots_group_date", table_name="group_daily_snapshots")
    op.drop_table("group_daily_snapshots")
    op.drop_index("ix_observation_group_status_order", table_name="observation_group_versions")
    op.drop_table("observation_group_versions")
