"""Persist multiple cross-cutting themes for each universe item.

Revision ID: 0014_universe_item_themes
Revises: 0013_report_display_projection
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "0014_universe_item_themes"
down_revision = "0013_report_display_projection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "instrument_universe_items" not in tables:
        return

    columns = {column["name"] for column in inspector.get_columns("instrument_universe_items")}
    if "themes" in columns:
        return

    # A JSON default keeps the ALTER TABLE valid for existing SQLite files.
    # Rows are immediately backfilled from the legacy primary theme below.
    op.add_column(
        "instrument_universe_items",
        sa.Column("themes", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    rows = bind.execute(sa.text("SELECT id, theme FROM instrument_universe_items")).mappings()
    for row in rows:
        theme = str(row["theme"] or "").strip()
        bind.execute(
            sa.text("UPDATE instrument_universe_items SET themes = :themes WHERE id = :id"),
            {"id": row["id"], "themes": json.dumps([theme] if theme else [], ensure_ascii=False)},
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "instrument_universe_items" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("instrument_universe_items")}
        if "themes" in columns:
            op.drop_column("instrument_universe_items", "themes")
