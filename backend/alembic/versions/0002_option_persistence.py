"""persist option contracts and gamma profiles

Revision ID: 0002_option_persistence
Revises: 0001_initial
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_option_persistence"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "option_analysis_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("source_mode", sa.String(length=32), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("persisted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_version", sa.String(length=32), nullable=False),
        sa.Column("risk_free_rate_percent", sa.Float(), nullable=False),
        sa.Column("dividend_yield_percent", sa.Float(), nullable=False),
        sa.Column("gamma_profile_range_percent", sa.Float(), nullable=False),
        sa.Column("gamma_profile_points", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["snapshots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_option_batches_run_id"),
        sa.UniqueConstraint("snapshot_id", name="uq_option_batches_snapshot_id"),
    )
    op.create_table(
        "option_symbol_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("spot", sa.Float(), nullable=False),
        sa.Column("spot_time", sa.String(length=64), nullable=True),
        sa.Column("overview", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["option_analysis_batches.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "symbol", name="uq_option_symbols_batch_symbol"),
    )
    op.create_index("ix_option_symbol_snapshots_symbol", "option_symbol_snapshots", ["symbol"])
    op.create_index(
        "ix_option_symbols_symbol_batch", "option_symbol_snapshots", ["symbol", "batch_id"]
    )
    op.create_table(
        "option_expiration_analyses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("symbol_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("expiration", sa.Date(), nullable=False),
        sa.Column("days_to_expiry", sa.Integer(), nullable=False),
        sa.Column("contract_count", sa.Integer(), nullable=False),
        sa.Column("max_pain", sa.Float(), nullable=True),
        sa.Column("expected_move_amount", sa.Float(), nullable=True),
        sa.Column("expected_move_percent", sa.Float(), nullable=True),
        sa.Column("expected_move_atm_strike", sa.Float(), nullable=True),
        sa.Column("exposure_totals", sa.JSON(), nullable=False),
        sa.Column("exposure_walls", sa.JSON(), nullable=False),
        sa.Column("profile_available", sa.Boolean(), nullable=False),
        sa.Column("primary_gamma_flip", sa.Float(), nullable=True),
        sa.Column("current_spot_net_gex", sa.Float(), nullable=True),
        sa.Column("usable_iv_contracts", sa.Integer(), nullable=False),
        sa.Column("profile_metadata", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["symbol_snapshot_id"], ["option_symbol_snapshots.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "symbol_snapshot_id", "expiration", name="uq_option_expirations_symbol_date"
        ),
    )
    op.create_index("ix_option_expirations_date", "option_expiration_analyses", ["expiration"])
    op.create_table(
        "option_contract_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("expiration_analysis_id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=96), nullable=False),
        sa.Column("option_type", sa.String(length=8), nullable=False),
        sa.Column("strike", sa.Float(), nullable=False),
        sa.Column("spot", sa.Float(), nullable=False),
        sa.Column("multiplier", sa.Float(), nullable=False),
        sa.Column("bid", sa.Float(), nullable=True),
        sa.Column("ask", sa.Float(), nullable=True),
        sa.Column("last", sa.Float(), nullable=True),
        sa.Column("volume", sa.Integer(), nullable=False),
        sa.Column("open_interest", sa.Integer(), nullable=False),
        sa.Column("implied_volatility", sa.Float(), nullable=True),
        sa.Column("delta", sa.Float(), nullable=True),
        sa.Column("gamma", sa.Float(), nullable=True),
        sa.Column("quote_time", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["expiration_analysis_id"], ["option_expiration_analyses.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "expiration_analysis_id", "code", name="uq_option_contracts_expiration_code"
        ),
    )
    op.create_index(
        "ix_option_contracts_expiration_strike_type",
        "option_contract_snapshots",
        ["expiration_analysis_id", "strike", "option_type"],
    )
    op.create_table(
        "option_gamma_profile_points",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("expiration_analysis_id", sa.String(length=36), nullable=False),
        sa.Column("point_index", sa.Integer(), nullable=False),
        sa.Column("hypothetical_spot", sa.Float(), nullable=False),
        sa.Column("call_gex", sa.Float(), nullable=False),
        sa.Column("put_gex", sa.Float(), nullable=False),
        sa.Column("net_gex", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["expiration_analysis_id"], ["option_expiration_analyses.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "expiration_analysis_id", "point_index", name="uq_option_profile_expiration_point"
        ),
    )
    op.create_table(
        "option_gamma_flips",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("expiration_analysis_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("level", sa.Float(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["expiration_analysis_id"], ["option_expiration_analyses.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "expiration_analysis_id", "position", name="uq_option_flips_expiration_position"
        ),
    )


def downgrade() -> None:
    op.drop_table("option_gamma_flips")
    op.drop_table("option_gamma_profile_points")
    op.drop_index(
        "ix_option_contracts_expiration_strike_type", table_name="option_contract_snapshots"
    )
    op.drop_table("option_contract_snapshots")
    op.drop_index("ix_option_expirations_date", table_name="option_expiration_analyses")
    op.drop_table("option_expiration_analyses")
    op.drop_index("ix_option_symbols_symbol_batch", table_name="option_symbol_snapshots")
    op.drop_index("ix_option_symbol_snapshots_symbol", table_name="option_symbol_snapshots")
    op.drop_table("option_symbol_snapshots")
    op.drop_table("option_analysis_batches")
