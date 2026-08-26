"""Version immutable group snapshots by calculation schema.

Revision ID: 0021_group_snapshot_calculation_version
Revises: 0020_observation_groups
"""

from alembic import op
import sqlalchemy as sa


revision = "0021_group_snapshot_calculation_version"
down_revision = "0020_observation_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"]: item for item in inspector.get_columns("group_daily_snapshots")}
    constraints = {
        item.get("name")
        for item in inspector.get_unique_constraints("group_daily_snapshots")
    }
    has_column = "snapshot_schema_version" in columns
    has_old_constraint = "uq_group_daily_snapshot_version_dataset" in constraints
    has_new_constraint = "uq_group_daily_snapshot_version_dataset_schema" in constraints
    if has_column and has_new_constraint and not has_old_constraint:
        return
    with op.batch_alter_table("group_daily_snapshots") as batch:
        if not has_column:
            batch.add_column(
                sa.Column(
                    "snapshot_schema_version",
                    sa.String(length=64),
                    nullable=False,
                    server_default="urus.group_daily_snapshot.v1",
                )
            )
        if has_old_constraint:
            batch.drop_constraint("uq_group_daily_snapshot_version_dataset", type_="unique")
        if not has_new_constraint:
            batch.create_unique_constraint(
                "uq_group_daily_snapshot_version_dataset_schema",
                ["group_version_id", "dataset_id", "snapshot_schema_version"],
            )
        batch.alter_column("snapshot_schema_version", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("group_daily_snapshots") as batch:
        batch.drop_constraint("uq_group_daily_snapshot_version_dataset_schema", type_="unique")
        batch.create_unique_constraint(
            "uq_group_daily_snapshot_version_dataset",
            ["group_version_id", "dataset_id"],
        )
        batch.drop_column("snapshot_schema_version")
