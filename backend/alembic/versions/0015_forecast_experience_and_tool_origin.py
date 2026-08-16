"""Persist forecast experience and distinguish deterministic evidence reads.

Revision ID: 0015_forecast_experience
Revises: 0014_universe_item_themes
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_forecast_experience"
down_revision = "0014_universe_item_themes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tool_columns = {
        column["name"] for column in inspector.get_columns("ai_tool_calls")
    }
    if "prefetched" not in tool_columns:
        op.add_column(
            "ai_tool_calls",
            sa.Column("prefetched", sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    if "forecast_experiences" not in inspector.get_table_names():
        op.create_table(
            "forecast_experiences",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("pattern_key", sa.String(96), nullable=False),
            sa.Column("category", sa.String(32), nullable=False),
            sa.Column("statement", sa.Text(), nullable=False),
            sa.Column("applicability_tags", sa.JSON(), nullable=False),
            sa.Column("evidence_refs", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("occurrence_count", sa.Integer(), nullable=False),
            sa.Column("support_count", sa.Integer(), nullable=False),
            sa.Column("contradiction_count", sa.Integer(), nullable=False),
            sa.Column(
                "source_report_id",
                sa.String(36),
                sa.ForeignKey("ai_decision_sessions.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("source_pre_market_report_id", sa.String(36), nullable=True),
            sa.Column("first_seen_trading_date", sa.String(10), nullable=False),
            sa.Column("last_seen_trading_date", sa.String(10), nullable=False),
            sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "pattern_key", name="uq_forecast_experiences_pattern_key"
            ),
        )
        op.create_index(
            "ix_forecast_experiences_status_last_seen",
            "forecast_experiences",
            ["status", "last_seen_at"],
        )
        op.create_index(
            "ix_forecast_experiences_source_report_id",
            "forecast_experiences",
            ["source_report_id"],
        )


def downgrade() -> None:
    op.drop_table("forecast_experiences")
    op.drop_column("ai_tool_calls", "prefetched")
