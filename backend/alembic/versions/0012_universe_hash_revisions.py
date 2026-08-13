"""Allow restoring identical universe content as a later revision.

Revision ID: 0012_universe_hash_revisions
Revises: 0011_instrument_universe
"""

from alembic import op
import sqlalchemy as sa

revision = "0012_universe_hash_revisions"
down_revision = "0011_instrument_universe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    unique_hash = next(
        (
            constraint
            for constraint in inspector.get_unique_constraints("instrument_universe_versions")
            if constraint.get("column_names") == ["content_sha256"]
        ),
        None,
    )
    if unique_hash is None:
        return
    if bind.dialect.name == "sqlite":
        # SQLite reports inline UNIQUE constraints without a droppable name.
        # Rebuild only the small version header table; item foreign keys keep
        # referencing the same final table name.
        op.execute("PRAGMA foreign_keys=OFF")
        op.execute(
            """
            CREATE TABLE instrument_universe_versions_new (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                revision INTEGER NOT NULL UNIQUE,
                content_sha256 VARCHAR(64) NOT NULL,
                source VARCHAR(24) NOT NULL,
                created_at DATETIME NOT NULL
            )
            """
        )
        op.execute(
            """
            INSERT INTO instrument_universe_versions_new
                (id, revision, content_sha256, source, created_at)
            SELECT id, revision, content_sha256, source, created_at
            FROM instrument_universe_versions
            """
        )
        op.drop_index("ix_instrument_universe_versions_created", table_name="instrument_universe_versions")
        op.drop_table("instrument_universe_versions")
        op.rename_table("instrument_universe_versions_new", "instrument_universe_versions")
        op.create_index("ix_instrument_universe_versions_created", "instrument_universe_versions", ["created_at"])
        op.execute("PRAGMA foreign_keys=ON")
    else:
        name = unique_hash.get("name")
        if name:
            op.drop_constraint(name, "instrument_universe_versions", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint(
        "uq_instrument_universe_versions_content_sha256",
        "instrument_universe_versions",
        ["content_sha256"],
    )
