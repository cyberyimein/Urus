"""Persist Phase D remote decision Workflow bindings, runs and artifacts.

Revision ID: 0024_remote_decision_workflows
Revises: 0023_market_data_capacity_admission
"""

from alembic import op
import sqlalchemy as sa


revision = "0024_remote_decision_workflows"
down_revision = "0023_market_data_capacity_admission"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "decision_workflow_bindings" not in existing:
        op.create_table(
            "decision_workflow_bindings",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("intent_type", sa.String(64), nullable=False),
            sa.Column("workflow_ref", sa.String(256), nullable=False),
            sa.Column("status", sa.String(24), nullable=False, server_default="disabled"),
            sa.Column("definition_hash", sa.String(64), nullable=False),
            sa.Column("compiled_hash", sa.String(64), nullable=False),
            sa.Column("capability_manifest_hash", sa.String(64), nullable=False),
            sa.Column("input_schema_version", sa.String(128), nullable=False),
            sa.Column("output_schema_version", sa.String(128), nullable=False),
            sa.Column("definition_json", sa.JSON(), nullable=False),
            sa.Column("manifest_json", sa.JSON(), nullable=False),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("intent_type", "workflow_ref", name="uq_decision_binding_intent_ref"),
        )
        op.create_index(
            "ix_decision_binding_intent_status",
            "decision_workflow_bindings",
            ["intent_type", "status"],
        )

    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "remote_decision_runs" not in existing:
        op.create_table(
            "remote_decision_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("anomalo_run_id", sa.String(128), nullable=True),
            sa.Column("intent_type", sa.String(64), nullable=False),
            sa.Column("request_intent_id", sa.String(128), nullable=False),
            sa.Column("idempotency_key", sa.String(64), nullable=False),
            sa.Column("scope_type", sa.String(32), nullable=False),
            sa.Column("scope_id", sa.String(128), nullable=False),
            sa.Column("scope_version", sa.String(128), nullable=True),
            sa.Column("dataset_id", sa.String(36), nullable=True),
            sa.Column("lens_type", sa.String(32), nullable=True),
            sa.Column("lens_id", sa.String(128), nullable=True),
            sa.Column("lens_version", sa.String(128), nullable=True),
            sa.Column("source_locator_json", sa.JSON(), nullable=False),
            sa.Column("source_dataset_id", sa.String(36), nullable=True),
            sa.Column("source_snapshot_id", sa.String(36), nullable=True),
            sa.Column("source_observation_run_id", sa.String(36), nullable=True),
            sa.Column("workflow_ref", sa.String(256), nullable=False),
            sa.Column("definition_hash", sa.String(64), nullable=False),
            sa.Column("compiled_hash", sa.String(64), nullable=False),
            sa.Column("input_schema_version", sa.String(128), nullable=False),
            sa.Column("input_sha256", sa.String(64), nullable=False),
            sa.Column("input_json", sa.JSON(), nullable=False),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("trigger_mode", sa.String(24), nullable=False, server_default="manual"),
            sa.Column("trigger_source", sa.String(64), nullable=False),
            sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
            sa.Column("remote_status", sa.String(24), nullable=True),
            sa.Column("latest_event_sequence", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("validation_status", sa.String(24), nullable=False, server_default="pending"),
            sa.Column("preflight_fingerprint", sa.String(64), nullable=False),
            sa.Column("result_json", sa.JSON(), nullable=True),
            sa.Column("error_code", sa.String(96), nullable=True),
            sa.Column("safe_error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["source_dataset_id"], ["daily_decision_datasets.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["source_snapshot_id"], ["group_daily_snapshots.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["source_observation_run_id"], ["observation_runs.id"], ondelete="RESTRICT"),
            sa.UniqueConstraint("request_intent_id", name="uq_remote_decision_request_intent"),
            sa.UniqueConstraint("idempotency_key", name="uq_remote_decision_idempotency"),
        )
        op.create_index("ix_remote_decision_runs_scope", "remote_decision_runs", ["scope_type", "scope_id"])
        op.create_index("ix_remote_decision_runs_status", "remote_decision_runs", ["status"])
        op.create_index("ix_remote_decision_runs_created", "remote_decision_runs", ["created_at"])
        op.create_index("ix_remote_decision_runs_anomalo_run_id", "remote_decision_runs", ["anomalo_run_id"])

    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "remote_decision_events" not in existing:
        op.create_table(
            "remote_decision_events",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("local_run_id", sa.String(36), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(96), nullable=False),
            sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=True),
            sa.Column("node_id", sa.String(128), nullable=True),
            sa.Column("attempt", sa.Integer(), nullable=True),
            sa.Column("child_run_id", sa.String(128), nullable=True),
            sa.Column("safe_data_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["local_run_id"], ["remote_decision_runs.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("local_run_id", "sequence", name="uq_remote_decision_event_run_sequence"),
        )
        op.create_index(
            "ix_remote_decision_events_run_created",
            "remote_decision_events",
            ["local_run_id", "created_at"],
        )

    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "remote_decision_artifacts" not in existing:
        op.create_table(
            "remote_decision_artifacts",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("local_run_id", sa.String(36), nullable=False),
            sa.Column("output_schema_version", sa.String(128), nullable=False),
            sa.Column("completeness", sa.String(32), nullable=False),
            sa.Column("artifact_json", sa.JSON(), nullable=False),
            sa.Column("artifact_sha256", sa.String(64), nullable=False),
            sa.Column("evidence_refs_json", sa.JSON(), nullable=False),
            sa.Column("usage_json", sa.JSON(), nullable=False),
            sa.Column("trace_ref", sa.String(256), nullable=True),
            sa.Column("validation_status", sa.String(24), nullable=False, server_default="accepted"),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["local_run_id"], ["remote_decision_runs.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("local_run_id", name="uq_remote_decision_artifact_run"),
        )


def downgrade() -> None:
    # This migration is only additive.  Keep downgrade explicit but safe for
    # environments where Base.metadata.create_all pre-created the tables.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "remote_decision_artifacts" in existing:
        op.drop_table("remote_decision_artifacts")
    if "remote_decision_events" in existing:
        op.drop_index("ix_remote_decision_events_run_created", table_name="remote_decision_events")
        op.drop_table("remote_decision_events")
    if "remote_decision_runs" in existing:
        op.drop_index("ix_remote_decision_runs_anomalo_run_id", table_name="remote_decision_runs")
        op.drop_index("ix_remote_decision_runs_created", table_name="remote_decision_runs")
        op.drop_index("ix_remote_decision_runs_status", table_name="remote_decision_runs")
        op.drop_index("ix_remote_decision_runs_scope", table_name="remote_decision_runs")
        op.drop_table("remote_decision_runs")
    if "decision_workflow_bindings" in existing:
        op.drop_index("ix_decision_binding_intent_status", table_name="decision_workflow_bindings")
        op.drop_table("decision_workflow_bindings")
