"""add persisted runtime scheduler and model settings

Revision ID: 0010_runtime_settings
Revises: 0009_daily_decision_cycle
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_runtime_settings"
down_revision = "0009_daily_decision_cycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ``create_app`` intentionally calls ``Base.metadata.create_all`` so a
    # fresh local checkout can boot without a migration step. If that boot
    # happened before Alembic was run, the table already exists while
    # ``alembic_version`` still points at 0009. Treat the table as migrated
    # instead of crashing on a duplicate CREATE TABLE.
    bind = op.get_bind()
    if "runtime_settings" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "runtime_settings",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "runtime_settings" in sa.inspect(bind).get_table_names():
        op.drop_table("runtime_settings")
