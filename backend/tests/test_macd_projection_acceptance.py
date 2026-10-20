"""Acceptance tests for MACD momentum fields at observation boundaries.

The tests use public group-snapshot, indicator-catalog, and HTTP projection
interfaces.  The legacy projection fixture deliberately omits the new fields
to model a frozen snapshot created before the MACD momentum extension.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.analytics.technical import calculate_technical_indicators
from app.core.config import Settings
from app.core.database import Base
from app.decision_harness.contracts import (
    FEATURE_VERSION,
    INDICATOR_SNAPSHOT_FEATURE_VERSION,
    MACD_MOMENTUM_VERSION,
    hash_bars,
)
from app.decision_harness.cross_section import CrossSectionService
from app.decision_harness.group_observation import build_group_snapshot
from app.decision_harness.market_evidence import DailyMarketEvidenceService
from app.models.daily_evidence import DailyDecisionDatasetModel, DailyIndicatorSnapshotModel
from app.models.observation import GroupDailySnapshotModel, ObservationRunModel
from app.repositories.daily_evidence import DailyEvidenceRepository


def _bars(symbol: str, count: int, start: float, *, slope: float = 0.25) -> list[dict[str, object]]:
    first_day = date(2026, 1, 1)
    return [
        {
            "symbol": symbol,
            "date": (first_day + timedelta(days=index)).isoformat(),
            "open": start + index * slope - 0.2,
            "high": start + index * slope + 0.6,
            "low": start + index * slope - 0.6,
            "close": start + index * slope,
            "volume": 2_000_000.0 + index * 1_000.0,
        }
        for index in range(count)
    ]


def _evidence(*instruments: tuple[str, list[dict[str, object]]]) -> dict[str, object]:
    latest_date = max(str(bars[-1]["date"]) for _, bars in instruments)
    return {
        "dataset": {
            "dataset_id": "dataset-macd-acceptance",
            "feature_version": FEATURE_VERSION,
            "trading_date": latest_date,
            "indicator_snapshot_ids": [],
        },
        "chart": {
            "instruments": {
                symbol: {
                    "price": {"bars": bars},
                    "quality": {"status": "ok", "warnings": []},
                }
                for symbol, bars in instruments
            }
        },
        "strategy_decisions": [],
        "deterministic_synthesis": {},
    }


def test_group_snapshot_propagates_member_macd_momentum_and_preserves_missing_values() -> None:
    full_bars = _bars("AAA", 80, 100.0)
    short_bars = _bars("BBB", 34, 80.0)
    benchmark_bars = _bars("QQQ", 80, 300.0, slope=0.1)
    snapshot = build_group_snapshot(
        {
            "group_id": "macd-group",
            "version_id": "macd-group-v1",
            "version": 1,
            "display_name": "MACD acceptance",
            "symbols": ["AAA", "BBB"],
            "benchmark_symbols": ["QQQ"],
        },
        _evidence(("AAA", full_bars), ("BBB", short_bars), ("QQQ", benchmark_bars)),
    )

    rows = {str(row["symbol"]): row for row in snapshot["symbols"]}
    expected_macd = calculate_technical_indicators(full_bars, source="acceptance_test")[
        "macd_12_26_9"
    ]
    assert rows["AAA"]["macd_histogram"] == expected_macd["histogram"]
    assert rows["AAA"]["macd_histogram_delta"] == expected_macd["histogram_delta"]
    assert rows["AAA"]["macd_histogram_delta2"] == expected_macd["histogram_delta2"]

    # 34 bars are enough for H, but not for ΔH or Δ²H.  Missing derived data
    # must remain null for the member instead of becoming numeric zero.
    assert rows["BBB"]["macd_histogram"] is not None
    assert rows["BBB"]["macd_histogram_delta"] is None
    assert rows["BBB"]["macd_histogram_delta2"] is None


def test_indicator_catalog_exposes_selectable_macd_momentum_lenses(client) -> None:
    response = client.get("/api/observation/indicator-catalog")
    assert response.status_code == 200, response.text

    catalog = {str(item["id"]): item for item in response.json()}
    assert "macd_histogram_delta" in catalog
    assert "macd_histogram_delta2" in catalog
    assert catalog["macd_histogram_delta"]["source_path"] == "symbols[].macd_histogram_delta"
    assert catalog["macd_histogram_delta2"]["source_path"] == "symbols[].macd_histogram_delta2"


def test_legacy_snapshot_remains_readable_and_new_lens_degrades_missing_fields_to_null(client, app) -> None:
    snapshot_id = "legacy-macd-snapshot"
    run_id = "legacy-macd-run"
    trading_date = date(2026, 1, 30)
    created_at = datetime(2026, 1, 30, 23, tzinfo=UTC)
    legacy_snapshot = GroupDailySnapshotModel(
        id=snapshot_id,
        group_id="legacy-group",
        group_version_id="legacy-group-v1",
        group_version=1,
        dataset_id="legacy-dataset",
        trading_date=trading_date,
        snapshot_schema_version="urus.group_daily_snapshot.v4",
        content_sha256="legacy-snapshot-hash",
        created_at=created_at,
        payload_json={
            "schema_version": "urus.group_daily_snapshot.v4",
            # Pin this fixture to the pre-extension schema.  The current
            # implementation may advance FEATURE_VERSION; a moving value
            # would make this compatibility test stop exercising old data.
            "feature_version": "technical_v5",
            "dataset_id": "legacy-dataset",
            "trading_date": trading_date.isoformat(),
            "group": {
                "group_id": "legacy-group",
                "version_id": "legacy-group-v1",
                "version": 1,
                "display_name": "Legacy group",
                "benchmark_symbols": ["QQQ"],
            },
            "quality": {"status": "ok", "warnings": []},
            "features": {},
            # This is the old frozen shape: no macd_histogram_delta fields.
            "symbols": [
                {
                    "symbol": "AAA",
                    "valid": True,
                    "quality_status": "ok",
                    "macd_histogram": 0.25,
                }
            ],
        },
    )
    run = ObservationRunModel(
        id=run_id,
        idempotency_key=run_id,
        status="succeeded",
        trigger_mode="scheduled",
        trading_date=trading_date,
        cutoff_time=created_at,
        group_ids=["legacy-group"],
        group_version_ids=["legacy-group-v1"],
        content_sha256="legacy-run-hash",
        payload_json={
            "group_snapshots": [
                {
                    "status": "succeeded",
                    "snapshot_id": snapshot_id,
                    "group_id": "legacy-group",
                    "group_version_id": "legacy-group-v1",
                    "dataset_id": "legacy-dataset",
                }
            ]
        },
        created_at=created_at,
    )
    with app.state.session_factory() as session:
        session.add_all([legacy_snapshot, run])
        session.commit()

    old_projection = client.get(f"/api/observation/runs/{run_id}/indicators/macd_histogram")
    assert old_projection.status_code == 200, old_projection.text
    assert old_projection.json()["rows"][0]["value"] == 0.25

    delta_projection = client.get(
        f"/api/observation/runs/{run_id}/indicators/macd_histogram_delta"
    )
    assert delta_projection.status_code == 200, delta_projection.text
    body = delta_projection.json()
    row = body["rows"][0]
    assert row["symbol"] == "AAA"
    assert row["value"] is None
    assert row["valid"] is False
    assert row["status"] == "missing"
    assert body["quality"]["status"] == "partial"


def test_indicator_catalog_matches_service_registry() -> None:
    catalog_ids = {str(item["id"]) for item in CrossSectionService.indicator_catalog()}
    assert {"macd_histogram_delta", "macd_histogram_delta2"} <= catalog_ids


def test_freeze_uses_macd_extension_snapshot_without_reusing_legacy_cache() -> None:
    """A pre-extension snapshot must remain immutable while the new freeze is idempotent."""

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    target_date = date(2026, 3, 20)
    cutoff = datetime(2026, 3, 21, 12, 0, tzinfo=UTC)
    bars = _bars("AAA", 79, 100.0)
    old_payload = {"macd_12_26_9": {"available": True, "histogram": 0.25}}
    old_v1_payload = {
        "macd_12_26_9": {
            "available": True,
            "histogram": 0.30,
            "histogram_delta": 0.10,
            "histogram_delta2": 0.01,
        }
    }

    with Session(engine) as session:
        repository = DailyEvidenceRepository(session)
        repository.upsert_bars(bars, source="fixture", collected_at=cutoff)
        legacy_snapshot = DailyIndicatorSnapshotModel(
            id="legacy-technical-v5",
            symbol="AAA",
            exchange="XNYS",
            bar_date=target_date,
            adjustment="QFQ",
            feature_version="technical_v5",
            input_bar_hash=hash_bars(bars),
            payload_json=old_payload,
            quality_json={"status": "ok", "sample_count": len(bars)},
            calculated_at=cutoff,
        )
        legacy_v1_snapshot = DailyIndicatorSnapshotModel(
            id="legacy-macd-momentum-v1",
            symbol="AAA",
            exchange="XNYS",
            bar_date=target_date,
            adjustment="QFQ",
            feature_version="technical_v5:macd_histogram_momentum_v1",
            input_bar_hash=hash_bars(bars),
            payload_json=old_v1_payload,
            quality_json={"status": "ok", "sample_count": len(bars)},
            calculated_at=cutoff,
        )
        session.add_all([legacy_snapshot, legacy_v1_snapshot])
        session.commit()

        service = DailyMarketEvidenceService(
            session,
            Settings(
                app_env="test",
                market_calendar="XNYS",
                market_timezone="America/New_York",
                daily_min_history_bars=20,
            ),
        )
        first = service.freeze(
            scope_type="instrument",
            scope_id="AAA",
            symbols=["AAA"],
            trading_date=target_date,
            cutoff_time=cutoff,
        )

        first_ids = list(first["dataset"]["indicator_snapshot_ids"])
        assert first["dataset"]["macd_momentum_version"] == MACD_MOMENTUM_VERSION
        assert MACD_MOMENTUM_VERSION == "macd_histogram_momentum_v2"
        assert len(first_ids) == 1
        assert first_ids[0] not in {legacy_snapshot.id, legacy_v1_snapshot.id}
        extension = session.get(DailyIndicatorSnapshotModel, first_ids[0])
        assert extension is not None
        assert extension.feature_version == INDICATOR_SNAPSHOT_FEATURE_VERSION
        assert extension.feature_version == "technical_v5:macd_histogram_momentum_v2"
        assert "histogram_delta" in extension.payload_json["macd_12_26_9"]
        assert session.get(DailyIndicatorSnapshotModel, legacy_snapshot.id).payload_json == old_payload
        assert session.get(DailyIndicatorSnapshotModel, legacy_v1_snapshot.id).payload_json == old_v1_payload

        second = service.freeze(
            scope_type="instrument",
            scope_id="AAA",
            symbols=["AAA"],
            trading_date=target_date,
            cutoff_time=cutoff,
        )

        assert second["dataset"]["dataset_id"] == first["dataset"]["dataset_id"]
        assert second["dataset"]["indicator_snapshot_ids"] == first_ids
        assert session.scalar(select(func.count()).select_from(DailyIndicatorSnapshotModel)) == 3
        assert session.scalar(select(func.count()).select_from(DailyDecisionDatasetModel)) == 1


@pytest.mark.parametrize("indicator_id", ["macd_histogram_delta", "macd_histogram_delta2"])
def test_raw_macd_momentum_lens_does_not_rank_price_scale_or_publish_group_median(
    indicator_id: str, client, app
) -> None:
    """Raw H derivatives stay per-symbol facts, not cross-symbol magnitudes."""

    snapshot_id = f"raw-macd-{indicator_id}-snapshot"
    run_id = f"raw-macd-{indicator_id}-run"
    dataset_id = f"raw-macd-{indicator_id}-dataset"
    trading_date = date(2026, 2, 6)
    created_at = datetime(2026, 2, 6, 23, tzinfo=UTC)
    values = {
        "AAA": {"macd_histogram": 500.0, "macd_histogram_delta": 0.000123, "macd_histogram_delta2": -0.000045},
        "BBB": {"macd_histogram": 5.0, "macd_histogram_delta": -0.20, "macd_histogram_delta2": -0.020},
        "CCC": {"macd_histogram": 50.0, "macd_histogram_delta": 0.03, "macd_histogram_delta2": 0.003},
        "DDD": {"macd_histogram": 0.5, "macd_histogram_delta": -0.04, "macd_histogram_delta2": -0.004},
    }
    symbols = [
        {
            "symbol": symbol,
            "valid": True,
            "quality_status": "ok",
            **macd_values,
        }
        for symbol, macd_values in values.items()
    ]
    payload = {
        "schema_version": "urus.group_daily_snapshot.v4",
        "feature_version": FEATURE_VERSION,
        "dataset_id": dataset_id,
        "trading_date": trading_date.isoformat(),
        "group": {
            "group_id": "raw-macd-group",
            "version_id": "raw-macd-group-v1",
            "version": 1,
            "display_name": "Raw MACD acceptance",
            "benchmark_symbols": ["QQQ"],
        },
        "quality": {"status": "ok", "warnings": []},
        "features": {},
        "symbols": symbols,
    }
    snapshot = GroupDailySnapshotModel(
        id=snapshot_id,
        group_id="raw-macd-group",
        group_version_id="raw-macd-group-v1",
        group_version=1,
        dataset_id=dataset_id,
        trading_date=trading_date,
        snapshot_schema_version="urus.group_daily_snapshot.v4",
        content_sha256=f"{snapshot_id}-hash",
        created_at=created_at,
        payload_json=payload,
    )
    run = ObservationRunModel(
        id=run_id,
        idempotency_key=run_id,
        status="succeeded",
        trigger_mode="manual",
        trading_date=trading_date,
        cutoff_time=created_at,
        group_ids=["raw-macd-group"],
        group_version_ids=["raw-macd-group-v1"],
        content_sha256=f"{run_id}-hash",
        payload_json={
            "group_snapshots": [
                {
                    "status": "succeeded",
                    "snapshot_id": snapshot_id,
                    "group_id": "raw-macd-group",
                    "group_version_id": "raw-macd-group-v1",
                    "dataset_id": dataset_id,
                }
            ]
        },
        created_at=created_at,
    )
    with app.state.session_factory() as session:
        session.add_all([snapshot, run])
        session.commit()

    response = client.get(f"/api/observation/runs/{run_id}/indicators/{indicator_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    valid_rows = [row for row in body["rows"] if row["valid"]]
    assert len(valid_rows) == 4
    assert len({row["value"] for row in valid_rows}) == 4
    rows_by_symbol = {str(row["symbol"]): row for row in valid_rows}
    expected_display = {
        "macd_histogram_delta": "+0.000123",
        "macd_histogram_delta2": "-0.000045",
    }[indicator_id]
    assert rows_by_symbol["AAA"]["display_value"] == expected_display

    # Every raw derivative is on the instrument's own price scale.  The
    # projection may expose the attention object for a stable response shape,
    # but it must not publish a percentile that compares unlike price scales.
    percentile_fields = {
        "global_percentile",
        "within_group_percentile",
        "change_percentile",
        "absolute_change_percentile",
        "threshold_distance_percentile",
    }
    for row in valid_rows:
        attention = row.get("attention_features") or {}
        assert all(attention.get(field) is None for field in percentile_fields)

    # Preserve the group summary shape for clients while withholding a
    # misleading cross-symbol median/min/max for the raw lens.
    distribution = body["groups"][0]["distribution"]
    assert distribution["count"] == 4
    assert all(distribution.get(field) is None for field in ("median", "q1", "q3", "min", "max"))
    assert body["groups"][0]["distribution_median_change"] is None
