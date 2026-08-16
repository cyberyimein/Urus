from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_empty_database_can_migrate(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert {
        "runs",
        "step_runs",
        "snapshots",
        "alembic_version",
        "forecast_experiences",
        "capital_flow_daily",
    }.issubset(
        set(inspector.get_table_names())
    )
    assert "prefetched" in {
        column["name"] for column in inspector.get_columns("ai_tool_calls")
    }
    experience_columns = {
        column["name"]: column
        for column in inspector.get_columns("forecast_experiences")
    }
    assert experience_columns["source_report_id"]["nullable"] is True
