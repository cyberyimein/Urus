"""Add the personal focus symbol list.

Revision ID: 0026_focus_symbols
Revises: 0025_backfill_canonical_daily_bars
"""

from alembic import op
import sqlalchemy as sa


revision = "0026_focus_symbols"
down_revision = "0025_backfill_canonical_daily_bars"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "focus_symbols" not in set(sa.inspect(op.get_bind()).get_table_names()):
        op.create_table(
            "focus_symbols",
            sa.Column("symbol", sa.String(16), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    if "focus_symbols" in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table("focus_symbols")
