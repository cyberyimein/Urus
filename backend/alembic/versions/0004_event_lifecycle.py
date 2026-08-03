"""persist scheduled event lifecycle, agent evidence and market reactions

Revision ID: 0004_event_lifecycle
Revises: 0003_instrument_technical_persistence
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_event_lifecycle"
down_revision = "0003_instrument_technical_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_definitions",
        sa.Column("key", sa.String(length=96), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("subject_type", sa.String(length=16), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("discovery_mode", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("result_schema_name", sa.String(length=96), nullable=False),
        sa.Column("metadata_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index("ix_event_definitions_category", "event_definitions", ["category"])

    op.create_table(
        "events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_key", sa.String(length=192), nullable=False),
        sa.Column("definition_key", sa.String(length=96), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("subject_type", sa.String(length=16), nullable=False),
        sa.Column("subject", sa.String(length=96), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("period", sa.String(length=64), nullable=True),
        sa.Column("discovery_mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("time_precision", sa.String(length=24), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("is_estimated", sa.Boolean(), nullable=False),
        sa.Column("announced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_expected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("latest_result_version", sa.Integer(), nullable=False),
        sa.Column("metadata_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["definition_key"], ["event_definitions.key"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_key"),
    )
    op.create_index(
        "ix_events_category_status_next_check",
        "events",
        ["category", "status", "next_check_at"],
    )
    op.create_index("ix_events_subject_scheduled", "events", ["subject", "scheduled_at"])
    op.create_index("ix_events_status", "events", ["status"])

    op.create_table(
        "event_sources",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("publisher", sa.String(length=160), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("evidence_payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "url", name="uq_event_sources_event_url"),
    )
    op.create_index(
        "ix_event_sources_event_published", "event_sources", ["event_id", "published_at"]
    )

    op.create_table(
        "event_results",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("result_status", sa.String(length=24), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("guidance", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("needs_follow_up", sa.Boolean(), nullable=False),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "version", name="uq_event_results_event_version"),
    )
    op.create_index("ix_event_results_event_captured", "event_results", ["event_id", "captured_at"])

    op.create_table(
        "event_agent_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=True),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("agent", sa.String(length=128), nullable=False),
        sa.Column("session_id", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("response_payload", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_event_agent_runs_event_started", "event_agent_runs", ["event_id", "started_at"]
    )
    op.create_index(
        "ix_event_agent_runs_operation_status", "event_agent_runs", ["operation", "status"]
    )

    op.create_table(
        "event_market_reactions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("window", sa.String(length=24), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_id", "run_id", "window", name="uq_event_reactions_event_run_window"
        ),
    )
    op.create_index("ix_event_reactions_event_window", "event_market_reactions", ["event_id", "window"])


def downgrade() -> None:
    op.drop_index("ix_event_reactions_event_window", table_name="event_market_reactions")
    op.drop_table("event_market_reactions")
    op.drop_index("ix_event_agent_runs_operation_status", table_name="event_agent_runs")
    op.drop_index("ix_event_agent_runs_event_started", table_name="event_agent_runs")
    op.drop_table("event_agent_runs")
    op.drop_index("ix_event_results_event_captured", table_name="event_results")
    op.drop_table("event_results")
    op.drop_index("ix_event_sources_event_published", table_name="event_sources")
    op.drop_table("event_sources")
    op.drop_index("ix_events_status", table_name="events")
    op.drop_index("ix_events_subject_scheduled", table_name="events")
    op.drop_index("ix_events_category_status_next_check", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_event_definitions_category", table_name="event_definitions")
    op.drop_table("event_definitions")
