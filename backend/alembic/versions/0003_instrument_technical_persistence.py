"""persist stage 3A instrument snapshots and technical inputs

Revision ID: 0003_instrument_technical_persistence
Revises: 0002_option_persistence
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_instrument_technical_persistence"
down_revision = "0002_option_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instrument_analysis_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("source_mode", sa.String(length=32), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("persisted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("feature_version", sa.String(length=32), nullable=False),
        sa.Column("requested_symbols", sa.JSON(), nullable=False),
        sa.Column("quota_audit", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["snapshots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_instrument_batches_run_id"),
        sa.UniqueConstraint("snapshot_id", name="uq_instrument_batches_snapshot_id"),
    )
    op.create_table(
        "instrument_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("asset_type", sa.String(length=16), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quote_time", sa.String(length=64), nullable=True),
        sa.Column("spot", sa.Float(), nullable=True),
        sa.Column("previous_close", sa.Float(), nullable=True),
        sa.Column("quality_status", sa.String(length=24), nullable=False),
        sa.Column("quote_payload", sa.JSON(), nullable=False),
        sa.Column("history_metadata", sa.JSON(), nullable=False),
        sa.Column("feature_payload", sa.JSON(), nullable=False),
        sa.Column("relative_payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["instrument_analysis_batches.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "symbol", name="uq_instrument_snapshots_batch_symbol"),
    )
    op.create_index(
        "ix_instrument_snapshots_symbol_captured",
        "instrument_snapshots",
        ["symbol", "captured_at"],
    )
    op.create_table(
        "instrument_daily_bars",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instrument_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("bar_date", sa.Date(), nullable=False),
        sa.Column("adjustment", sa.String(length=16), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("turnover", sa.Float(), nullable=True),
        sa.Column("turnover_rate", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["instrument_snapshot_id"], ["instrument_snapshots.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "instrument_snapshot_id",
            "bar_date",
            "adjustment",
            name="uq_instrument_bars_snapshot_date_adjustment",
        ),
    )
    op.create_index(
        "ix_instrument_bars_symbol_date",
        "instrument_daily_bars",
        ["instrument_snapshot_id", "bar_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_instrument_bars_symbol_date", table_name="instrument_daily_bars")
    op.drop_table("instrument_daily_bars")
    op.drop_index(
        "ix_instrument_snapshots_symbol_captured", table_name="instrument_snapshots"
    )
    op.drop_table("instrument_snapshots")
    op.drop_table("instrument_analysis_batches")
