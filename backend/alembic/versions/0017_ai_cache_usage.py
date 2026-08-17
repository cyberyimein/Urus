"""Persist prompt-cache usage for model runs and turns.

Revision ID: 0017_ai_cache_usage
Revises: 0016_capital_flow_daily
"""

from alembic import op
import sqlalchemy as sa


revision = "0017_ai_cache_usage"
down_revision = "0016_capital_flow_daily"
branch_labels = None
depends_on = None


def _add_if_missing(table: str, column: sa.Column) -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {item["name"] for item in inspector.get_columns(table)}
    if column.name not in existing:
        op.add_column(table, column)


def upgrade() -> None:
    _add_if_missing(
        "ai_decision_runs",
        sa.Column("cached_prompt_tokens", sa.Integer(), nullable=True),
    )
    _add_if_missing(
        "ai_decision_runs",
        sa.Column("cache_write_tokens", sa.Integer(), nullable=True),
    )
    _add_if_missing(
        "ai_model_turns",
        sa.Column("cached_prompt_tokens", sa.Integer(), nullable=True),
    )
    _add_if_missing(
        "ai_model_turns",
        sa.Column("cache_write_tokens", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_model_turns", "cache_write_tokens")
    op.drop_column("ai_model_turns", "cached_prompt_tokens")
    op.drop_column("ai_decision_runs", "cache_write_tokens")
    op.drop_column("ai_decision_runs", "cached_prompt_tokens")
