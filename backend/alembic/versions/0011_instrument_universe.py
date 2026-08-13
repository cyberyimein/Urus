"""Add immutable instrument universe versions and freeze them on runs.

Revision ID: 0011_instrument_universe
Revises: 0010_runtime_settings
"""

from alembic import op
import sqlalchemy as sa

revision = "0011_instrument_universe"
down_revision = "0010_runtime_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Local development calls metadata.create_all so new tables can exist
    # before Alembic advances. Keep this migration safe for that supported path.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "instrument_universe_versions" not in tables:
        op.create_table(
            "instrument_universe_versions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("revision", sa.Integer(), nullable=False, unique=True),
            sa.Column("content_sha256", sa.String(64), nullable=False),
            sa.Column("source", sa.String(24), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_instrument_universe_versions_created", "instrument_universe_versions", ["created_at"])
    if "instrument_universe_items" not in tables:
        op.create_table(
            "instrument_universe_items",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("version_id", sa.String(36), sa.ForeignKey("instrument_universe_versions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("symbol", sa.String(16), nullable=False),
            sa.Column("display_name", sa.String(128), nullable=False),
            sa.Column("asset_type", sa.String(16), nullable=False),
            sa.Column("theme", sa.String(64), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("roles", sa.JSON(), nullable=False),
            sa.Column("benchmarks", sa.JSON(), nullable=False),
            sa.Column("collection", sa.JSON(), nullable=False),
            sa.Column("notes", sa.Text(), nullable=False),
            sa.UniqueConstraint("version_id", "symbol", name="uq_universe_version_symbol"),
        )
        op.create_index("ix_universe_items_version_position", "instrument_universe_items", ["version_id", "position"])
    run_columns = {column["name"] for column in sa.inspect(bind).get_columns("runs")}
    if "universe_version_id" not in run_columns:
        op.add_column("runs", sa.Column("universe_version_id", sa.String(36), nullable=True))
    if "universe_content_sha256" not in run_columns:
        op.add_column("runs", sa.Column("universe_content_sha256", sa.String(64), nullable=True))
    run_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("runs")}
    if "ix_runs_universe_version_id" not in run_indexes:
        op.create_index("ix_runs_universe_version_id", "runs", ["universe_version_id"])


def downgrade() -> None:
    op.drop_index("ix_runs_universe_version_id", table_name="runs")
    op.drop_column("runs", "universe_content_sha256")
    op.drop_column("runs", "universe_version_id")
    op.drop_index("ix_universe_items_version_position", table_name="instrument_universe_items")
    op.drop_table("instrument_universe_items")
    op.drop_index("ix_instrument_universe_versions_created", table_name="instrument_universe_versions")
    op.drop_table("instrument_universe_versions")
