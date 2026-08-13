"""Persist report-only option chart projections.

Revision ID: 0013_report_display_projection
Revises: 0012_universe_hash_revisions
"""

from alembic import op
import sqlalchemy as sa


revision = "0013_report_display_projection"
down_revision = "0012_universe_hash_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "report_display_projections" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "report_display_projections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "report_id",
            sa.String(36),
            sa.ForeignKey("ai_decision_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("source_snapshot_ids", sa.JSON(), nullable=False),
        sa.Column("source_run_ids", sa.JSON(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("report_id", name="uq_report_display_report"),
    )


def downgrade() -> None:
    op.drop_table("report_display_projections")
