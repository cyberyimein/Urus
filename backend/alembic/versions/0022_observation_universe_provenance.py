"""Persist Phase C Universe provenance and automatic-group ownership.

Revision ID: 0022_observation_universe_provenance
Revises: 0021_group_snapshot_calculation_version
"""

from alembic import op
import sqlalchemy as sa


revision = "0022_observation_universe_provenance"
down_revision = "0021_group_snapshot_calculation_version"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "observation_universe_revisions" not in tables:
        op.create_table(
            "observation_universe_revisions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("source_url", sa.String(512), nullable=False),
            sa.Column("upstream_version_id", sa.String(128), nullable=True),
            sa.Column("upstream_revision", sa.Integer(), nullable=True),
            sa.Column("local_universe_version_id", sa.String(36), nullable=False),
            sa.Column("content_sha256", sa.String(64), nullable=False),
            sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["local_universe_version_id"],
                ["instrument_universe_versions.id"],
                ondelete="RESTRICT",
            ),
            sa.UniqueConstraint(
                "source_url",
                "content_sha256",
                name="uq_observation_universe_source_content",
            ),
        )
        op.create_index(
            "ix_observation_universe_revisions_source_fetched",
            "observation_universe_revisions",
            ["source_url", "fetched_at"],
        )

    group_columns = _columns("observation_group_versions")
    with op.batch_alter_table("observation_group_versions") as batch:
        if "source" not in group_columns:
            batch.add_column(
                sa.Column("source", sa.String(24), nullable=False, server_default="manual")
            )
        if "universe_revision_id" not in group_columns:
            batch.add_column(
                sa.Column("universe_revision_id", sa.String(36), nullable=True)
            )
            batch.create_foreign_key(
                "fk_observation_group_universe_revision",
                "observation_universe_revisions",
                ["universe_revision_id"],
                ["id"],
                ondelete="SET NULL",
            )

    run_columns = _columns("observation_runs")
    with op.batch_alter_table("observation_runs") as batch:
        if "universe_revision_id" not in run_columns:
            batch.add_column(sa.Column("universe_revision_id", sa.String(36), nullable=True))
            batch.create_foreign_key(
                "fk_observation_run_universe_revision",
                "observation_universe_revisions",
                ["universe_revision_id"],
                ["id"],
                ondelete="SET NULL",
            )
        if "universe_freshness" not in run_columns:
            batch.add_column(
                sa.Column(
                    "universe_freshness",
                    sa.String(16),
                    nullable=False,
                    server_default="unknown",
                )
            )
        if "universe_source_url" not in run_columns:
            batch.add_column(sa.Column("universe_source_url", sa.String(512), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("observation_runs") as batch:
        batch.drop_constraint("fk_observation_run_universe_revision", type_="foreignkey")
        batch.drop_column("universe_source_url")
        batch.drop_column("universe_freshness")
        batch.drop_column("universe_revision_id")
    with op.batch_alter_table("observation_group_versions") as batch:
        batch.drop_constraint("fk_observation_group_universe_revision", type_="foreignkey")
        batch.drop_column("universe_revision_id")
        batch.drop_column("source")
    op.drop_index(
        "ix_observation_universe_revisions_source_fetched",
        table_name="observation_universe_revisions",
    )
    op.drop_table("observation_universe_revisions")
