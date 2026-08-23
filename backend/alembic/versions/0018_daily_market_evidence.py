"""Add canonical daily bars, indicator cache, daily datasets and chart projections.

Revision ID: 0018_daily_market_evidence
Revises: 0017_ai_cache_usage
"""

from alembic import op
import sqlalchemy as sa


revision = "0018_daily_market_evidence"
down_revision = "0017_ai_cache_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "daily_bars",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("exchange", sa.String(16), nullable=False),
        sa.Column("asset_type", sa.String(16), nullable=False),
        sa.Column("bar_date", sa.Date(), nullable=False),
        sa.Column("market_timezone", sa.String(64), nullable=False),
        sa.Column("adjustment", sa.String(16), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("turnover", sa.Float(), nullable=True),
        sa.Column("turnover_rate", sa.Float(), nullable=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_revision", sa.String(64), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("corrected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quality_status", sa.String(24), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "symbol", "exchange", "bar_date", "adjustment", "source", "source_revision",
            name="uq_daily_bars_symbol_exchange_date_adjustment_source",
        ),
    )
    op.create_index("ix_daily_bars_symbol_date", "daily_bars", ["symbol", "bar_date"])
    op.create_index("ix_daily_bars_date_source", "daily_bars", ["bar_date", "source"])

    op.create_table(
        "daily_indicator_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("exchange", sa.String(16), nullable=False),
        sa.Column("bar_date", sa.Date(), nullable=False),
        sa.Column("adjustment", sa.String(16), nullable=False),
        sa.Column("feature_version", sa.String(64), nullable=False),
        sa.Column("input_bar_hash", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("quality_json", sa.JSON(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "symbol", "exchange", "bar_date", "adjustment", "feature_version", "input_bar_hash",
            name="uq_daily_indicators_symbol_date_feature_input",
        ),
    )
    op.create_index("ix_daily_indicators_symbol_date", "daily_indicator_snapshots", ["symbol", "bar_date"])

    op.create_table(
        "daily_decision_datasets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("scope_type", sa.String(24), nullable=False),
        sa.Column("scope_id", sa.String(128), nullable=False),
        sa.Column("scope_version", sa.Integer(), nullable=True),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("cutoff_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("market_timezone", sa.String(64), nullable=False),
        sa.Column("bar_completion_policy", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("scope_json", sa.JSON(), nullable=False),
        sa.Column("bar_manifest_json", sa.JSON(), nullable=False),
        sa.Column("indicator_snapshot_ids", sa.JSON(), nullable=False),
        sa.Column("group_snapshot_ids", sa.JSON(), nullable=False),
        sa.Column("quality_json", sa.JSON(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("content_sha256", name="uq_daily_decision_datasets_content_hash"),
    )
    op.create_index(
        "ix_daily_decision_datasets_scope_date",
        "daily_decision_datasets",
        ["scope_type", "scope_id", "trading_date"],
    )
    op.create_index(
        "ix_daily_decision_datasets_status_created",
        "daily_decision_datasets",
        ["status", "created_at"],
    )

    op.create_table(
        "decision_chart_projections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "dataset_id",
            sa.String(36),
            sa.ForeignKey("daily_decision_datasets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("scope_type", sa.String(24), nullable=False),
        sa.Column("scope_id", sa.String(128), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("dataset_id", name="uq_decision_chart_projections_dataset"),
    )
    op.create_index(
        "ix_decision_chart_projections_scope",
        "decision_chart_projections",
        ["scope_type", "scope_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_decision_chart_projections_scope", table_name="decision_chart_projections")
    op.drop_table("decision_chart_projections")
    op.drop_index("ix_daily_decision_datasets_status_created", table_name="daily_decision_datasets")
    op.drop_index("ix_daily_decision_datasets_scope_date", table_name="daily_decision_datasets")
    op.drop_table("daily_decision_datasets")
    op.drop_index("ix_daily_indicators_symbol_date", table_name="daily_indicator_snapshots")
    op.drop_table("daily_indicator_snapshots")
    op.drop_index("ix_daily_bars_date_source", table_name="daily_bars")
    op.drop_index("ix_daily_bars_symbol_date", table_name="daily_bars")
    op.drop_table("daily_bars")
