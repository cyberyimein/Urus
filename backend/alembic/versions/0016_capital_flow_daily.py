"""Persist daily order-size capital-flow observations.

Revision ID: 0016_capital_flow_daily
Revises: 0015_forecast_experience
"""

from alembic import op
import sqlalchemy as sa


revision = "0016_capital_flow_daily"
down_revision = "0015_forecast_experience"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "capital_flow_daily",
        sa.Column("provider", sa.String(64), primary_key=True),
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("trading_date", sa.Date(), primary_key=True),
        sa.Column("period_type", sa.String(16), primary_key=True),
        sa.Column("in_flow", sa.Float(), nullable=True),
        sa.Column("main_in_flow", sa.Float(), nullable=True),
        sa.Column("super_in_flow", sa.Float(), nullable=True),
        sa.Column("big_in_flow", sa.Float(), nullable=True),
        sa.Column("mid_in_flow", sa.Float(), nullable=True),
        sa.Column("sml_in_flow", sa.Float(), nullable=True),
        sa.Column("source_time", sa.String(64), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quality_status", sa.String(24), nullable=False),
        sa.Column("quality_warnings", sa.JSON(), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "provider",
            "symbol",
            "trading_date",
            "period_type",
            name="uq_capital_flow_daily_provider_symbol_date_period",
        ),
    )
    op.create_index(
        "ix_capital_flow_daily_symbol_date",
        "capital_flow_daily",
        ["symbol", "trading_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_capital_flow_daily_symbol_date", table_name="capital_flow_daily")
    op.drop_table("capital_flow_daily")
