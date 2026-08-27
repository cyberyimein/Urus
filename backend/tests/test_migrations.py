from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.core.database import Base


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
        "daily_bars",
        "daily_indicator_snapshots",
        "daily_decision_datasets",
        "decision_chart_projections",
        "strategy_decisions",
        "deterministic_syntheses",
        "observation_group_versions",
        "group_daily_snapshots",
        "observation_runs",
            "observation_universe_revisions",
            "moomoo_history_quota_snapshots",
        "history_collection_states",
            "decision_workflow_bindings",
            "remote_decision_runs",
            "remote_decision_events",
            "remote_decision_artifacts",
        }.issubset(
        set(inspector.get_table_names())
    )
    assert "prefetched" in {
        column["name"] for column in inspector.get_columns("ai_tool_calls")
    }
    assert {"cached_prompt_tokens", "cache_write_tokens"}.issubset(
        {column["name"] for column in inspector.get_columns("ai_decision_runs")}
    )
    assert {"cached_prompt_tokens", "cache_write_tokens"}.issubset(
        {column["name"] for column in inspector.get_columns("ai_model_turns")}
    )
    assert "bar_completion_policy" in {
        column["name"] for column in inspector.get_columns("daily_decision_datasets")
    }
    experience_columns = {
        column["name"]: column
        for column in inspector.get_columns("forecast_experiences")
    }
    assert experience_columns["source_report_id"]["nullable"] is True
    assert "snapshot_schema_version" in {
        column["name"] for column in inspector.get_columns("group_daily_snapshots")
    }
    assert {"source", "universe_revision_id"}.issubset(
        {column["name"] for column in inspector.get_columns("observation_group_versions")}
    )
    assert {"universe_revision_id", "universe_freshness", "universe_source_url"}.issubset(
        {column["name"] for column in inspector.get_columns("observation_runs")}
    )


def test_orm_created_phase_c_tables_can_resume_from_0019(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'orm-first.db'}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.stamp(config, "0019_strategy_decisions")

    command.upgrade(config, "head")

    inspector = inspect(engine)
    constraints = {
        item["name"] for item in inspector.get_unique_constraints("group_daily_snapshots")
    }
    assert "uq_group_daily_snapshot_version_dataset_schema" in constraints
    assert "uq_group_daily_snapshot_version_dataset" not in constraints
