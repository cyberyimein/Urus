"""Persist deterministic intraday setup state and event transitions.

Revision ID: 0027_intraday_events
Revises: 0026_focus_symbols
"""

from alembic import op
import sqlalchemy as sa


revision = "0027_intraday_events"
down_revision = "0026_focus_symbols"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "intraday_setup_states" not in tables:
        op.create_table(
            "intraday_setup_states",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("processor_id", sa.String(length=96), nullable=False),
            sa.Column("task_name", sa.String(length=128), nullable=False),
            sa.Column("state_json", sa.JSON(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "processor_id", "task_name", name="uq_intraday_state_processor_task"
            ),
        )
        op.create_index(
            "ix_intraday_setup_states_task_updated",
            "intraday_setup_states",
            ["task_name", "updated_at"],
        )

    if "intraday_events" not in tables:
        op.create_table(
            "intraday_events",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("processor_id", sa.String(length=96), nullable=False),
            sa.Column("setup_id", sa.String(length=256), nullable=False),
            sa.Column("transition_seq", sa.Integer(), nullable=False),
            sa.Column("task_name", sa.String(length=128), nullable=False),
            sa.Column("symbol", sa.String(length=32), nullable=False),
            sa.Column("strategy", sa.String(length=8), nullable=False),
            sa.Column("setup_type", sa.String(length=64), nullable=False),
            sa.Column("transition", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("timeframe", sa.String(length=16), nullable=False),
            sa.Column("rule_version", sa.String(length=128), nullable=False),
            sa.Column("trading_date", sa.String(length=10), nullable=False),
            sa.Column("evidence_cutoff", sa.String(length=40), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "processor_id",
                "setup_id",
                "transition_seq",
                name="uq_intraday_event_setup_sequence",
            ),
            sqlite_autoincrement=True,
        )
        op.create_index(
            "ix_intraday_events_symbol_strategy_status",
            "intraday_events",
            ["symbol", "strategy", "status", "id"],
        )
        op.create_index(
            "ix_intraday_events_task_cutoff",
            "intraday_events",
            ["task_name", "evidence_cutoff", "id"],
        )

    if "intraday_event_ingest_cursors" not in tables:
        op.create_table(
            "intraday_event_ingest_cursors",
            sa.Column("processor_id", sa.String(length=96), nullable=False),
            sa.Column("source_path_identity", sa.String(length=1024), nullable=False),
            sa.Column("last_scheduled_at", sa.String(length=40), nullable=True),
            sa.Column("last_task_name", sa.String(length=128), nullable=True),
            sa.Column("last_capture_id", sa.String(length=128), nullable=True),
            sa.Column("last_content_sha256", sa.String(length=64), nullable=True),
            sa.Column("seen_source_slots_json", sa.JSON(), nullable=False),
            sa.Column("backlog_remaining", sa.Boolean(), nullable=False),
            sa.Column("last_status", sa.String(length=24), nullable=False),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("capabilities_summary_json", sa.JSON(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("processor_id"),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "intraday_event_ingest_cursors" in tables:
        op.drop_table("intraday_event_ingest_cursors")
    if "intraday_events" in tables:
        op.drop_index(
            "ix_intraday_events_task_cutoff", table_name="intraday_events"
        )
        op.drop_index(
            "ix_intraday_events_symbol_strategy_status", table_name="intraday_events"
        )
        op.drop_table("intraday_events")
    if "intraday_setup_states" in tables:
        op.drop_index(
            "ix_intraday_setup_states_task_updated", table_name="intraday_setup_states"
        )
        op.drop_table("intraday_setup_states")
