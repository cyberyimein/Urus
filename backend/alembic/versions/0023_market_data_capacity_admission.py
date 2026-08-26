"""Persist the latest Moomoo quota and per-symbol collection state.

Revision ID: 0023_market_data_capacity_admission
Revises: 0022_observation_universe_provenance
"""

from alembic import op
import sqlalchemy as sa


revision = "0023_market_data_capacity_admission"
down_revision = "0022_observation_universe_provenance"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "moomoo_history_quota_snapshots" not in tables:
        op.create_table(
            "moomoo_history_quota_snapshots",
            sa.Column("id", sa.String(128), primary_key=True),
            sa.Column("provider", sa.String(64), nullable=False),
            sa.Column(
                "quota_kind",
                sa.String(64),
                nullable=False,
                server_default="history_candlestick",
            ),
            sa.Column("available", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("used_quota", sa.Integer(), nullable=True),
            sa.Column("remain_quota", sa.Integer(), nullable=True),
            sa.Column("total_quota", sa.Integer(), nullable=True),
            sa.Column("detail_json", sa.JSON(), nullable=False),
            sa.Column(
                "quality_status", sa.String(24), nullable=False, server_default="unavailable"
            ),
            sa.Column("warning", sa.Text(), nullable=True),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint(
                "provider", "quota_kind", name="uq_history_quota_provider_kind"
            ),
        )

    tables = _tables()
    if "history_collection_states" not in tables:
        op.create_table(
            "history_collection_states",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("provider", sa.String(64), nullable=False),
            sa.Column("symbol", sa.String(32), nullable=False),
            sa.Column("exchange", sa.String(16), nullable=False, server_default="XNYS"),
            sa.Column("adjustment", sa.String(16), nullable=False, server_default="QFQ"),
            sa.Column(
                "access_state", sa.String(32), nullable=False, server_default="not_requested"
            ),
            sa.Column(
                "quality_state", sa.String(24), nullable=False, server_default="unknown"
            ),
            sa.Column("reason_code", sa.String(64), nullable=True),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column("desired_history", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("universe_version_id", sa.String(36), nullable=True),
            sa.Column("capacity_snapshot_id", sa.String(128), nullable=True),
            sa.Column("bar_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("latest_bar_date", sa.Date(), nullable=True),
            sa.Column("required_through_date", sa.Date(), nullable=True),
            sa.Column("minimum_bar_count", sa.Integer(), nullable=False, server_default="260"),
            sa.Column("quota_cost", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("first_deferred_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["universe_version_id"], ["instrument_universe_versions.id"], ondelete="SET NULL"
            ),
            sa.ForeignKeyConstraint(
                ["capacity_snapshot_id"],
                ["moomoo_history_quota_snapshots.id"],
                ondelete="SET NULL",
            ),
            sa.UniqueConstraint(
                "provider", "symbol", name="uq_history_collection_state_provider_symbol"
            ),
        )
        op.create_index(
            "ix_history_collection_states_access_state",
            "history_collection_states",
            ["access_state"],
        )
        op.create_index(
            "ix_history_collection_states_updated",
            "history_collection_states",
            ["updated_at"],
        )


def downgrade() -> None:
    tables = _tables()
    if "history_collection_states" in tables:
        op.drop_index("ix_history_collection_states_updated", table_name="history_collection_states")
        op.drop_index(
            "ix_history_collection_states_access_state", table_name="history_collection_states"
        )
        op.drop_table("history_collection_states")
    if "moomoo_history_quota_snapshots" in tables:
        op.drop_table("moomoo_history_quota_snapshots")
