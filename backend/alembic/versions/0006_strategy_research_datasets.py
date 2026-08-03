"""persist Step 4 strategy research evidence bundles

Revision ID: 0006_strategy_research_datasets
Revises: 0005_event_schedule_initialization
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_strategy_research_datasets"
down_revision = "0005_event_schedule_initialization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_research_datasets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("dataset_key", sa.String(length=128), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_run_id", sa.String(length=36), nullable=False),
        sa.Column("source_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("source_run_type", sa.String(length=32), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_collection_status", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("metadata_payload", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["source_run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_snapshot_id"], ["snapshots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_key", name="uq_strategy_dataset_key"),
        sa.UniqueConstraint("source_run_id", name="uq_strategy_dataset_source_run"),
    )
    op.create_index(
        "ix_strategy_datasets_status_created",
        "strategy_research_datasets",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_strategy_research_datasets_status",
        "strategy_research_datasets",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_strategy_research_datasets_status", table_name="strategy_research_datasets")
    op.drop_index(
        "ix_strategy_datasets_status_created",
        table_name="strategy_research_datasets",
    )
    op.drop_table("strategy_research_datasets")
