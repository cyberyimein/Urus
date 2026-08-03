"""persist explicit full scheduled-event initialization batches

Revision ID: 0005_event_schedule_initialization
Revises: 0004_event_lifecycle
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_event_schedule_initialization"
down_revision = "0004_event_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_schedule_initializations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("requested_categories", sa.JSON(), nullable=False),
        sa.Column("requested_definitions", sa.JSON(), nullable=False),
        sa.Column("requested_targets", sa.JSON(), nullable=False),
        sa.Column("discovered_count", sa.Integer(), nullable=False),
        sa.Column("missing_count", sa.Integer(), nullable=False),
        sa.Column("api_call_count", sa.Integer(), nullable=False),
        sa.Column("metadata_payload", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_event_schedule_initializations_status",
        "event_schedule_initializations",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_event_schedule_initializations_status",
        table_name="event_schedule_initializations",
    )
    op.drop_table("event_schedule_initializations")
