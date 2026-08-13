"""add daily decision phase and report lineage

Revision ID: 0009_daily_decision_cycle
Revises: 0008_stage4b_sessions_trace
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_daily_decision_cycle"
down_revision = "0008_stage4b_sessions_trace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ai_decision_sessions") as batch:
        batch.add_column(
            sa.Column("decision_phase", sa.String(length=32), nullable=False, server_default="pre_close")
        )
        batch.add_column(
            sa.Column("trading_date", sa.String(length=10), nullable=False, server_default="")
        )
        batch.add_column(sa.Column("parent_session_id", sa.String(length=36), nullable=True))
        batch.create_index("ix_ai_decision_sessions_parent_session_id", ["parent_session_id"])
        batch.create_index(
            "ix_ai_decision_sessions_trading_phase", ["trading_date", "decision_phase"]
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_decision_sessions") as batch:
        batch.drop_index("ix_ai_decision_sessions_trading_phase")
        batch.drop_index("ix_ai_decision_sessions_parent_session_id")
        batch.drop_column("parent_session_id")
        batch.drop_column("trading_date")
        batch.drop_column("decision_phase")
