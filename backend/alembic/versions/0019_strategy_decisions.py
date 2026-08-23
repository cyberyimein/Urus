"""Persist Phase B deterministic strategy decisions and synthesis.

Revision ID: 0019_strategy_decisions
Revises: 0018_daily_market_evidence
"""

from alembic import op
import sqlalchemy as sa


revision = "0019_strategy_decisions"
down_revision = "0018_daily_market_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "dataset_id",
            sa.String(36),
            sa.ForeignKey("daily_decision_datasets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope_type", sa.String(24), nullable=False),
        sa.Column("scope_id", sa.String(128), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("strategy_set_sha256", sa.String(64), nullable=False),
        sa.Column("strategy_name", sa.String(64), nullable=False),
        sa.Column("strategy_version", sa.String(32), nullable=False),
        sa.Column("implementation_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "dataset_id",
            "strategy_set_sha256",
            "symbol",
            "strategy_name",
            "strategy_version",
            "implementation_sha256",
            name="uq_strategy_decisions_dataset_strategy_symbol_version",
        ),
    )
    op.create_index(
        "ix_strategy_decisions_dataset_symbol",
        "strategy_decisions",
        ["dataset_id", "symbol"],
    )
    op.create_index(
        "ix_strategy_decisions_strategy_created",
        "strategy_decisions",
        ["strategy_name", "created_at"],
    )

    op.create_table(
        "deterministic_syntheses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "dataset_id",
            sa.String(36),
            sa.ForeignKey("daily_decision_datasets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope_type", sa.String(24), nullable=False),
        sa.Column("scope_id", sa.String(128), nullable=False),
        sa.Column("strategy_set_sha256", sa.String(64), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "dataset_id",
            "strategy_set_sha256",
            name="uq_deterministic_syntheses_dataset_strategy_set",
        ),
    )
    op.create_index(
        "ix_deterministic_syntheses_dataset_created",
        "deterministic_syntheses",
        ["dataset_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_deterministic_syntheses_dataset_created",
        table_name="deterministic_syntheses",
    )
    op.drop_table("deterministic_syntheses")
    op.drop_index("ix_strategy_decisions_strategy_created", table_name="strategy_decisions")
    op.drop_index("ix_strategy_decisions_dataset_symbol", table_name="strategy_decisions")
    op.drop_table("strategy_decisions")
