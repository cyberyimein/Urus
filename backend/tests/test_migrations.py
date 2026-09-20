from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

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
        "focus_symbols",
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


def test_orm_created_observation_tables_can_resume_from_0019(tmp_path: Path) -> None:
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


def test_legacy_instrument_bars_are_backfilled_into_canonical_daily_bars(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'legacy-bars.db'}"
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0024_remote_decision_workflows")

    captured_at = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO runs
                    (id, run_type, status, started_at, completed_at, cutoff_time, snapshot_id, error_message)
                VALUES (:id, :run_type, :status, :started_at, :completed_at, :cutoff_time, :snapshot_id, :error_message)
                """
            ),
            {
                "id": "legacy-run",
                "run_type": "post_close",
                "status": "completed",
                "started_at": captured_at,
                "completed_at": captured_at,
                "cutoff_time": captured_at,
                "snapshot_id": None,
                "error_message": None,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO snapshots
                    (id, run_id, schema_version, cutoff_time, created_at, quality_status, payload)
                VALUES (:id, :run_id, :schema_version, :cutoff_time, :created_at, :quality_status, :payload)
                """
            ),
            {
                "id": "legacy-snapshot",
                "run_id": "legacy-run",
                "schema_version": "legacy-v1",
                "cutoff_time": captured_at,
                "created_at": captured_at,
                "quality_status": "ok",
                "payload": "{}",
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO instrument_analysis_batches
                    (id, run_id, snapshot_id, provider, source_mode, captured_at, persisted_at,
                     feature_version, requested_symbols, quota_audit)
                VALUES (:id, :run_id, :snapshot_id, :provider, :source_mode, :captured_at, :persisted_at,
                        :feature_version, :requested_symbols, :quota_audit)
                """
            ),
            {
                "id": "legacy-batch",
                "run_id": "legacy-run",
                "snapshot_id": "legacy-snapshot",
                "provider": "moomoo",
                "source_mode": "live",
                "captured_at": captured_at,
                "persisted_at": captured_at,
                "feature_version": "legacy-v1",
                "requested_symbols": '["INTC"]',
                "quota_audit": "{}",
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO instrument_snapshots
                    (id, batch_id, symbol, asset_type, captured_at, quote_time, spot, previous_close,
                     quality_status, quote_payload, history_metadata, feature_payload, relative_payload)
                VALUES (:id, :batch_id, :symbol, :asset_type, :captured_at, :quote_time, :spot, :previous_close,
                        :quality_status, :quote_payload, :history_metadata, :feature_payload, :relative_payload)
                """
            ),
            {
                "id": "legacy-instrument-snapshot",
                "batch_id": "legacy-batch",
                "symbol": "INTC",
                "asset_type": "equity",
                "captured_at": captured_at,
                "quote_time": None,
                "spot": 20.5,
                "previous_close": 20.0,
                "quality_status": "ok",
                "quote_payload": "{}",
                "history_metadata": "{}",
                "feature_payload": "{}",
                "relative_payload": "{}",
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO instrument_daily_bars
                    (instrument_snapshot_id, bar_date, adjustment, open, high, low, close, volume,
                     turnover, turnover_rate)
                VALUES (:instrument_snapshot_id, :bar_date, :adjustment, :open, :high, :low, :close, :volume,
                        :turnover, :turnover_rate)
                """
            ),
            {
                "instrument_snapshot_id": "legacy-instrument-snapshot",
                "bar_date": "2026-08-21",
                "adjustment": "QFQ",
                "open": 20.0,
                "high": 21.0,
                "low": 19.5,
                "close": 20.5,
                "volume": 1000.0,
                "turnover": 20000.0,
                "turnover_rate": 0.1,
            },
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT symbol, bar_date, close, source, source_revision, content_sha256
                FROM daily_bars
                WHERE symbol = 'INTC'
                """
            )
        ).mappings().one()

    assert row["symbol"] == "INTC"
    assert str(row["bar_date"]) == "2026-08-21"
    assert row["close"] == 20.5
    assert row["source"] == "legacy_instrument_snapshot"
    assert row["source_revision"] == row["content_sha256"]
