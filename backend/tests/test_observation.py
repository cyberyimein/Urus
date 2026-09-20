from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select

from app.decision_harness.discovery import DiscoveryProjectionService
from app.decision_harness.group_observation import build_group_snapshot
from app.decision_harness.market_evidence import DailyMarketEvidenceService
from app.decision_harness.observation_report import build_observation_report, rank_observation_groups
from app.models.observation import (
    GroupDailySnapshotModel,
    ObservationGroupVersionModel,
    ObservationRunModel,
)
from app.repositories.daily_evidence import DailyEvidenceRepository
from app.repositories.observation import ObservationUniverseRevisionRepository
from app.repositories.universe import InstrumentUniverseRepository
from app.schemas.observation import ObservationRunCreateRequest
from app.schemas.universe import InstrumentConfig
from app.services.capital_flow import completed_session_dates
from app.services.observation import ObservationRunService


def _bars(symbol: str, dates, start: float, slope: float = 0.25):
    return [
        {
            "symbol": symbol,
            "date": trading_date.isoformat() if hasattr(trading_date, "isoformat") else str(trading_date),
            "open": start + index * slope - 0.2,
            "high": start + index * slope + 0.6,
            "low": start + index * slope - 0.6,
            "close": start + index * slope,
            "volume": 2_000_000.0 + index * 1000,
        }
        for index, trading_date in enumerate(dates)
    ]


def test_group_snapshot_exposes_breadth_rotation_and_heatmap() -> None:
    dates = [f"2026-01-{index + 1:02d}" for index in range(80)]
    bars_a = _bars("INTC", dates, 20.0)
    bars_b = _bars("AMD", dates, 40.0, slope=0.05)
    benchmark = _bars("QQQ", dates, 300.0, slope=0.2)
    evidence = {
        "dataset": {
            "dataset_id": "dataset-group-1",
            "feature_version": "technical_v5",
            "trading_date": dates[-1],
            "indicator_snapshot_ids": ["indicator-intc", "indicator-amd", "indicator-qqq"],
        },
        "chart": {
            "instruments": {
                symbol: {
                    "price": {"bars": bars},
                    "quality": {"status": "ok", "warnings": []},
                }
                for symbol, bars in (("INTC", bars_a), ("AMD", bars_b), ("QQQ", benchmark))
            }
        },
        "strategy_decisions": [],
        "deterministic_synthesis": {},
    }
    snapshot = build_group_snapshot(
        {
            "group_id": "semiconductors",
            "version_id": "group-version-1",
            "version": 1,
            "display_name": "半导体",
            "symbols": ["INTC", "AMD"],
            "benchmark_symbols": ["QQQ"],
        },
        evidence,
    )

    assert snapshot["schema_version"] == "urus.group_daily_snapshot.v4"
    assert snapshot["feature_version"] == "technical_v5"
    assert snapshot["features"]["valid_symbol_count"] == 2
    assert snapshot["features"]["breadth"]["above_ma20"] == 1.0
    assert snapshot["group_strategy_decisions"]
    assert snapshot["changes"]["previous_trading_date"] is None
    assert snapshot["charts"]["relative_strength"]["series"]
    assert snapshot["charts"]["breadth"]["series"]["above_ma20"]
    assert len(snapshot["charts"]["rotation"]) == 2
    assert len(snapshot["charts"]["heatmap"]) == 2
    assert len(snapshot["charts"]["small_multiples"]) == 2
    assert snapshot["content_sha256"]


def test_group_snapshot_excludes_partial_and_stale_symbols_from_decision() -> None:
    dates = [f"2026-01-{index + 1:02d}" for index in range(80)]
    bars_a = _bars("INTC", dates, 20.0)
    bars_b = _bars("AMD", dates, 40.0, slope=0.05)
    evidence = {
        "dataset": {
            "dataset_id": "dataset-group-quality",
            "feature_version": "technical_v5",
            "trading_date": dates[-1],
            "indicator_snapshot_ids": [],
        },
        "chart": {
            "instruments": {
                "INTC": {
                    "price": {"bars": bars_a},
                    "quality": {"status": "ok", "warnings": []},
                },
                "AMD": {
                    "price": {"bars": bars_b},
                    "quality": {"status": "partial", "warnings": ["history incomplete"]},
                },
            }
        },
        "strategy_decisions": [],
        "deterministic_synthesis": {},
    }

    snapshot = build_group_snapshot(
        {
            "group_id": "semiconductors",
            "version_id": "group-version-quality",
            "version": 1,
            "display_name": "半导体",
            "symbols": ["INTC", "AMD"],
            "benchmark_symbols": [],
        },
        evidence,
    )

    assert snapshot["quality"]["valid_symbol_count"] == 1
    assert snapshot["quality"]["status"] == "partial"
    assert snapshot["group_decision"]["state"] == "insufficient_data"
    assert snapshot["features"]["valid_symbol_count"] == 1


def _report_decision(
    *,
    decision_id: str,
    symbol: str,
    stance: str,
    stage: str = "confirmed",
    score: float = 50,
) -> dict[str, object]:
    return {
        "decision_id": decision_id,
        "scope": {"symbol": symbol},
        "strategy": {"name": "fixture", "version": "1.0"},
        "stance": stance,
        "action": "prioritize" if stance == "bullish" else "avoid",
        "score": score,
        "setup_progress": {
            "stage": stage,
            "confirmation_distance_atr": 0.5,
        },
        "status": "ok",
    }


def _report_snapshot(
    *,
    group_id: str,
    group_name: str,
    decisions: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "status": "succeeded",
        "snapshot_id": f"snapshot-{group_id}",
        "dataset_id": "dataset-report",
        "group_id": group_id,
        "payload": {
            "group": {
                "group_id": group_id,
                "display_name": group_name,
            },
            "features": {
                "returns_percent": {"20d": {"median": 1.0}},
                "relative_strength": {"median_excess_20d": 0.5},
                "breadth": {"above_ma20": 0.5},
            },
            "changes": {},
            "quality": {},
            "group_decision": {"state": "mixed", "stance": "neutral", "action": "watch"},
            "symbols": [],
            "strategy_decisions": decisions,
        },
    }


def test_observation_report_opportunity_lanes_exclude_bearish_decisions() -> None:
    report = build_observation_report(
        run_id="run-opportunities",
        trading_date="2026-08-21",
        snapshots=[
            _report_snapshot(
                group_id="software",
                group_name="软件",
                decisions=[
                    _report_decision(
                        decision_id="bullish-confirmed",
                        symbol="MSFT",
                        stance="bullish",
                    ),
                    _report_decision(
                        decision_id="bearish-confirmed",
                        symbol="NVDA",
                        stance="bearish",
                    ),
                    _report_decision(
                        decision_id="bearish-forming",
                        symbol="AMD",
                        stance="bearish",
                        stage="forming",
                    ),
                ],
            )
        ],
    )

    assert all(
        item["stance"] != "bearish"
        for lane in report["opportunity_lanes"].values()
        for item in lane
    )
    assert {item["decision_id"] for item in report["risk_lanes"]["bearish"]} == {
        "bearish-confirmed",
        "bearish-forming",
    }


def test_observation_report_deduplicates_overlapping_decisions_and_keeps_membership() -> None:
    shared = _report_decision(
        decision_id="shared-decision",
        symbol="INTC",
        stance="bullish",
    )
    report = build_observation_report(
        run_id="run-overlap",
        trading_date="2026-08-21",
        snapshots=[
            _report_snapshot(group_id="chips", group_name="芯片", decisions=[shared]),
            _report_snapshot(
                group_id="turnarounds",
                group_name="反转",
                decisions=[dict(shared)],
            ),
        ],
    )

    confirmed = report["opportunity_lanes"]["confirmed"]
    assert len([item for item in confirmed if item["decision_id"] == "shared-decision"]) == 1
    item = next(item for item in confirmed if item["decision_id"] == "shared-decision")
    assert set(item["group_ids"]) == {"chips", "turnarounds"}


def test_group_snapshot_scopes_global_synthesis_to_group_symbols() -> None:
    dates = [f"2026-01-{index + 1:02d}" for index in range(80)]
    evidence = {
        "dataset": {
            "dataset_id": "dataset-group-synthesis-scope",
            "feature_version": "technical_v5",
            "trading_date": dates[-1],
            "indicator_snapshot_ids": [],
            "scope": {"scope_type": "observation_run", "scope_id": "run-1", "symbols": ["AAA", "BBB"]},
        },
        "chart": {
            "instruments": {
                symbol: {
                    "price": {"bars": _bars(symbol, dates, start)},
                    "quality": {"status": "ok", "warnings": []},
                }
                for symbol, start in (("AAA", 20.0), ("BBB", 40.0))
            }
        },
        "strategy_decisions": [
            _report_decision(
                decision_id="aaa-bullish",
                symbol="AAA",
                stance="bullish",
                score=70,
            ),
            _report_decision(
                decision_id="bbb-bearish",
                symbol="BBB",
                stance="bearish",
                score=-70,
            ),
        ],
        "deterministic_synthesis": {
            "schema_version": "urus.deterministic_synthesis.v1",
            "dataset_id": "dataset-group-synthesis-scope",
            "scope": {"scope_type": "observation_run", "scope_id": "run-1", "symbols": ["AAA", "BBB"]},
            "consensus_state": "conflicted",
            "suggested_action": "watch",
            "bullish_count": 1,
            "bearish_count": 1,
            "symbol_summaries": {
                "AAA": {"symbol": "AAA", "consensus_state": "aligned", "suggested_action": "prioritize", "bullish_count": 2, "bearish_count": 0},
                "BBB": {"symbol": "BBB", "consensus_state": "aligned", "suggested_action": "avoid", "bullish_count": 0, "bearish_count": 2},
            },
        },
    }

    snapshot = build_group_snapshot(
        {
            "group_id": "aaa-only",
            "version_id": "aaa-only-v1",
            "version": 1,
            "display_name": "AAA only",
            "symbols": ["AAA"],
            "benchmark_symbols": [],
        },
        evidence,
    )

    synthesis = snapshot["deterministic_synthesis"]
    assert set(synthesis["symbol_summaries"]) == {"AAA"}
    assert synthesis["consensus_state"] == "aligned"
    assert synthesis["suggested_action"] == "prioritize"
    assert synthesis["bullish_count"] == 2
    assert synthesis["bearish_count"] == 0


def test_group_snapshot_preserves_mixed_symbol_synthesis_action() -> None:
    dates = [f"2026-01-{index + 1:02d}" for index in range(80)]
    snapshot = build_group_snapshot(
        {
            "group_id": "mixed-group",
            "version_id": "mixed-group-v1",
            "version": 1,
            "display_name": "Mixed group",
            "symbols": ["AAA"],
            "benchmark_symbols": [],
        },
        {
            "dataset": {
                "dataset_id": "dataset-mixed-symbol",
                "feature_version": "technical_v5",
                "trading_date": dates[-1],
                "indicator_snapshot_ids": [],
            },
            "chart": {
                "instruments": {
                    "AAA": {
                        "price": {"bars": _bars("AAA", dates, 20.0)},
                        "quality": {"status": "ok", "warnings": []},
                    }
                }
            },
            "strategy_decisions": [],
            "deterministic_synthesis": {
                "schema_version": "urus.deterministic_synthesis.v1",
                "dataset_id": "dataset-mixed-symbol",
                "scope": {"scope_type": "observation_run", "scope_id": "run-1", "symbols": ["AAA"]},
                "consensus_state": "mixed",
                "suggested_action": "watch",
                "bullish_count": 1,
                "bearish_count": 0,
                "neutral_count": 4,
                "symbol_summaries": {
                    "AAA": {
                        "symbol": "AAA",
                        "consensus_state": "mixed",
                        "suggested_action": "watch",
                        "bullish_count": 1,
                        "bearish_count": 0,
                        "neutral_count": 4,
                    }
                },
            },
        },
    )

    synthesis = snapshot["deterministic_synthesis"]
    assert synthesis["consensus_state"] == "mixed"
    assert synthesis["suggested_action"] == "watch"


def test_group_decision_marks_broad_strength_deterioration_as_watch() -> None:
    dates = [f"2026-02-{index + 1:02d}" for index in range(28)]
    evidence = {
        "dataset": {
            "dataset_id": "dataset-group-impulse",
            "feature_version": "technical_v5",
            "trading_date": dates[-1],
            "indicator_snapshot_ids": [],
        },
        "chart": {
            "instruments": {
                symbol: {
                    "price": {"bars": _bars(symbol, dates, start, slope=0.25)},
                    "quality": {"status": "ok", "warnings": []},
                }
                for symbol, start in (("AAA", 20.0), ("BBB", 40.0))
            }
            | {
                "QQQ": {
                    "price": {"bars": _bars("QQQ", dates, 300.0, slope=0.01)},
                    "quality": {"status": "ok", "warnings": []},
                }
            }
        },
        "strategy_decisions": [],
        "deterministic_synthesis": {},
    }
    previous_snapshot = {
        "trading_date": "2026-01-30",
        "features": {
            "returns_percent": {"20d": {"median": 99.0}},
            "relative_strength": {"median_excess_20d": 99.0},
            "breadth": {"above_ma20": 1.0},
            "leaders": [{"symbol": "AAA"}],
        },
        "group_decision": {
            "state": "broad_strength",
            "stance": "bullish",
            "action": "prioritize",
        },
    }

    snapshot = build_group_snapshot(
        {
            "group_id": "impulse-group",
            "version_id": "impulse-group-v1",
            "version": 1,
            "display_name": "Impulse group",
            "symbols": ["AAA", "BBB"],
            "benchmark_symbols": ["QQQ"],
        },
        evidence,
        previous_snapshot=previous_snapshot,
    )

    decision = snapshot["group_decision"]
    assert decision["state"] == "broad_strength"
    assert decision["impulse"] == "deteriorating"
    assert decision["phase"] == "broad_strength_deteriorating"
    assert decision["action"] == "watch"


@pytest.mark.parametrize("symbol_count", [1, 2, 3])
def test_group_phase_marks_singleton_and_small_groups_low_evidence(
    symbol_count: int,
) -> None:
    dates = [f"2026-03-{index + 1:02d}" for index in range(80)]
    symbols = ["AAA", "BBB", "CCC"][:symbol_count]
    snapshot = build_group_snapshot(
        {
            "group_id": f"small-{symbol_count}",
            "version_id": f"small-{symbol_count}-v1",
            "version": 1,
            "display_name": "Small group",
            "symbols": symbols,
            "benchmark_symbols": [],
        },
        {
            "dataset": {
                "dataset_id": f"dataset-small-{symbol_count}",
                "feature_version": "technical_v5",
                "trading_date": dates[-1],
                "indicator_snapshot_ids": [],
            },
            "chart": {
                "instruments": {
                    symbol: {
                        "price": {"bars": _bars(symbol, dates, 20.0 + index * 10)},
                        "quality": {"status": "ok", "warnings": []},
                    }
                    for index, symbol in enumerate(symbols)
                }
            },
            "strategy_decisions": [],
            "deterministic_synthesis": {},
        },
    )

    assert snapshot["schema_version"] == "urus.group_daily_snapshot.v4"
    decision = snapshot["group_decision"]
    assert snapshot["quality"]["valid_symbol_count"] == symbol_count
    assert decision["evidence_strength"] == "low"
    assert decision["rank_eligible"] is False
    assert decision["action"] != "prioritize"
    assert decision["category"] != "leader"


@pytest.mark.parametrize("has_setup", [False, True])
def test_weak_improving_group_needs_unique_symbol_setup_for_reversal_watch(
    has_setup: bool,
) -> None:
    dates = [f"2026-04-{index + 1:02d}" for index in range(80)]
    strategy_decisions = []
    symbol_summaries = {
        "AAA": {"symbol": "AAA", "consensus_state": "mixed", "suggested_action": "watch"},
        "BBB": {"symbol": "BBB", "consensus_state": "aligned", "suggested_action": "avoid"},
    }
    if has_setup:
        setup = {
            "stage": "near_confirmation",
            "direction": "bullish",
            "confirmation_price": 18.0,
            "confirmation_operator": ">=",
            "observation_price": 17.0,
            "remaining_price": 1.0,
            "remaining_percent": 5.9,
            "remaining_atr": 0.4,
            "invalidation_price": 14.0,
            "invalidation_distance_price": 3.0,
            "invalidation_distance_percent": 17.6,
            "invalidation_distance_atr": 1.2,
        }
        symbol_summaries["AAA"]["primary_setup"] = setup
        strategy_decisions = [
            {
                "decision_id": f"aaa-setup-{index}",
                "scope": {"symbol": "AAA"},
                "status": "ok",
                "stance": "bullish",
                "action": "watch",
                "setup_progress": {
                    "stage": "near_confirmation",
                    "confirmation_distance_atr": 0.4,
                },
                "setup_plan": setup,
            }
            for index in range(3)
        ]

    snapshot = build_group_snapshot(
        {
            "group_id": "weak-improving",
            "version_id": "weak-improving-v1",
            "version": 1,
            "display_name": "Weak improving group",
            "symbols": ["AAA", "BBB"],
            "benchmark_symbols": ["QQQ"],
        },
        {
            "dataset": {
                "dataset_id": "dataset-weak-improving",
                "feature_version": "technical_v5",
                "trading_date": dates[-1],
                "indicator_snapshot_ids": [],
            },
            "chart": {
                "instruments": {
                    "AAA": {
                        "price": {"bars": _bars("AAA", dates, 20.0, slope=-0.25)},
                        "quality": {"status": "ok", "warnings": []},
                    },
                    "BBB": {
                        "price": {"bars": _bars("BBB", dates, 30.0, slope=-0.2)},
                        "quality": {"status": "ok", "warnings": []},
                    },
                    "QQQ": {
                        "price": {"bars": _bars("QQQ", dates, 300.0, slope=0.1)},
                        "quality": {"status": "ok", "warnings": []},
                    },
                }
            },
            "strategy_decisions": strategy_decisions,
            "deterministic_synthesis": {
                "schema_version": "urus.deterministic_synthesis.v1",
                "symbol_summaries": symbol_summaries,
            },
        },
        previous_snapshot={
            "trading_date": "2026-03-31",
            "features": {
                "returns_percent": {"20d": {"median": -99.0}},
                "relative_strength": {"median_excess_20d": -99.0},
                "breadth": {"above_ma20": 0.0},
                "leaders": [],
            },
            "group_decision": {
                "state": "broad_weakness",
                "stance": "bearish",
                "action": "avoid",
            },
        },
    )

    decision = snapshot["group_decision"]
    assert snapshot["schema_version"] == "urus.group_daily_snapshot.v4"
    assert decision["state"] == "broad_weakness"
    assert decision["impulse"] == "improving"
    assert decision["phase"] == "broad_weakness_improving"
    assert decision["setup_participation"] == (0.5 if has_setup else 0.0)
    assert decision["category"] == ("reversal_watch" if has_setup else "weak")
    assert decision["action"] == "watch" if has_setup else decision["action"] != "prioritize"


def test_observation_report_deduplicates_overlapping_strategy_conflicts() -> None:
    decisions = [
        _report_decision(
            decision_id="shared-bullish",
            symbol="INTC",
            stance="bullish",
            score=65,
        ),
        _report_decision(
            decision_id="shared-bearish",
            symbol="INTC",
            stance="bearish",
            score=-65,
        ),
    ]
    report = build_observation_report(
        run_id="run-conflict-overlap",
        trading_date="2026-08-21",
        snapshots=[
            _report_snapshot(group_id="chips", group_name="芯片", decisions=decisions),
            _report_snapshot(
                group_id="turnarounds",
                group_name="反转",
                decisions=[dict(item) for item in decisions],
            ),
        ],
    )

    assert len(report["strategy_conflicts"]) == 1
    conflict = report["strategy_conflicts"][0]
    assert set(conflict["decision_ids"]) == {"shared-bullish", "shared-bearish"}
    assert set(conflict["group_ids"]) == {"chips", "turnarounds"}


def test_observation_group_version_and_run_are_idempotent(client) -> None:
    cutoff = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    session_dates = completed_session_dates(cutoff, "XNYS", count=260)
    with client.app.state.session_factory() as session:
        repository = DailyEvidenceRepository(session)
        for symbol, start in (("INTC", 20.0), ("AMD", 80.0), ("QQQ", 300.0)):
            repository.upsert_bars(
                _bars(symbol, session_dates, start),
                source="fixture",
                collected_at=cutoff,
            )
        session.commit()

    group = client.post(
        "/api/observation/groups",
        json={
            "group_id": "semiconductors",
            "display_name": "半导体",
            "description": "核心芯片观察组",
            "symbols": ["INTC", "AMD"],
            "benchmark_symbols": ["QQQ"],
            "tags": ["sector"],
        },
    )
    assert group.status_code == 201
    group_body = group.json()
    assert group_body["version"] == 1

    payload = {
        "group_ids": ["semiconductors"],
        "trading_date": session_dates[-1].isoformat(),
        "cutoff_time": cutoff.isoformat(),
        "request_intent_id": "user-intent-1",
    }
    first = client.post("/api/observation/runs", json=payload)
    assert first.status_code == 201, first.text
    first_body = first.json()
    assert first_body["status"] == "succeeded"
    assert first_body["group_count"] == 1
    assert first_body["group_snapshots"][0]["group_id"] == "semiconductors"
    report = first_body["report"]
    assert report["schema_version"] == "urus.observation_report.v2"
    assert report["mode"] == "deterministic-only"
    assert report["group_rankings"][0]["group_id"] == "semiconductors"
    assert {item["symbol"] for item in report["anomalies"]["leaders"]} <= {"INTC", "AMD"}
    assert {item["symbol"] for item in report["anomalies"]["laggards"]} <= {"INTC", "AMD"}
    assert set(report["opportunity_lanes"]) == {"confirmed", "near_confirmation", "forming"}
    assert set(report["risk_lanes"]) == {"invalidated", "bearish"}
    assert report["content_sha256"]

    listed = client.get("/api/observation/runs?limit=1")
    assert listed.status_code == 200, listed.text
    assert listed.json()[0]["run_id"] == first_body["run_id"]
    listed_detail = client.get(f"/api/observation/runs/{listed.json()[0]['run_id']}")
    assert listed_detail.status_code == 200, listed_detail.text
    assert listed_detail.json()["run_id"] == first_body["run_id"]

    indicator_catalog = client.get("/api/observation/indicator-catalog")
    assert indicator_catalog.status_code == 200
    assert {item["id"] for item in indicator_catalog.json()} >= {
        "rsi14",
        "macd_histogram",
        "above_ma20",
    }
    indicator_projection = client.get(
        f"/api/observation/runs/{first_body['run_id']}/indicators/rsi14"
    )
    assert indicator_projection.status_code == 200, indicator_projection.text
    indicator_body = indicator_projection.json()
    assert indicator_body["lens"]["id"] == "rsi14"
    assert {row["symbol"] for row in indicator_body["rows"]} == {"INTC", "AMD"}
    assert indicator_body["groups"][0]["group_name"] == "半导体"
    assert indicator_body["quality"]["snapshot_ids"]
    assert indicator_body["ai"]["status"] == "disabled"
    assert indicator_body["content_sha256"]

    strategy_catalog = client.get("/api/observation/strategy-catalog")
    assert strategy_catalog.status_code == 200
    assert any(item["id"] == "trend_momentum_v1" for item in strategy_catalog.json())
    strategy_projection = client.get(
        f"/api/observation/runs/{first_body['run_id']}/strategies/trend_momentum_v1"
    )
    assert strategy_projection.status_code == 200, strategy_projection.text
    strategy_body = strategy_projection.json()
    assert strategy_body["lens"]["id"] == "trend_momentum_v1"
    assert len(strategy_body["rows"]) == 2
    assert {row["symbol"] for row in strategy_body["rows"]} == {"INTC", "AMD"}
    assert strategy_body["groups"][0]["group_name"] == "半导体"
    assert strategy_body["ai"]["available"] is False

    second = client.post("/api/observation/runs", json=payload)
    assert second.status_code == 201
    assert second.json()["run_id"] == first_body["run_id"]

    replay = client.post(
        "/api/observation/runs",
        json={**payload, "request_intent_id": "user-intent-replay"},
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["run_id"] != first_body["run_id"]
    assert replay.json()["report"]["content_sha256"] == report["content_sha256"]

    scheduled_payload = {
        "group_ids": ["semiconductors"],
        "trading_date": session_dates[-1].isoformat(),
        "cutoff_time": cutoff.isoformat(),
        "trigger_mode": "scheduled",
    }
    scheduled_first = client.post("/api/observation/runs", json=scheduled_payload)
    assert scheduled_first.status_code == 201, scheduled_first.text
    scheduled_second = client.post("/api/observation/runs", json=scheduled_payload)
    assert scheduled_second.status_code == 201
    assert scheduled_second.json()["run_id"] == scheduled_first.json()["run_id"]


def test_observation_run_persists_options_and_close_alignment(client, monkeypatch) -> None:
    cutoff = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    session_dates = completed_session_dates(cutoff, "XNYS", count=260)
    with client.app.state.session_factory() as session:
        DailyEvidenceRepository(session).upsert_bars(
            _bars("QQQ", session_dates, 20.0),
            source="fixture",
            collected_at=cutoff,
        )
        session.commit()

    created = client.post(
        "/api/observation/groups",
        json={
            "group_id": "options-observation-group",
            "display_name": "期权观察测试组",
            "symbols": ["QQQ"],
        },
    )
    assert created.status_code == 201, created.text
    close = 20.0 + 259 * 0.25

    class OptionsAdapter:
        def options_snapshot(self, symbols: list[str] | None = None) -> dict[str, object]:
            return {
                "is_mock": False,
                "status": "available",
                "available": True,
                "provider": "test-options",
                "source_mode": "snapshot",
                "captured_at": cutoff.isoformat(),
                "requested_symbols": symbols or [],
                "unavailable_symbols": [],
                "symbols": [
                    {
                        "symbol": "QQQ",
                        "spot": close,
                        "spot_time": cutoff.isoformat(),
                        "overview": {},
                        "expirations": [
                            {
                                "expiration": "2026-08-21",
                                "days_to_expiry": 0,
                                "contract_count": 1,
                                "max_pain": close,
                                "expected_move": {
                                    "amount": 1.0,
                                    "percent": 1.0,
                                    "atm_strike": close,
                                },
                                "exposure": {
                                    "totals": {},
                                    "walls": {
                                        "net_dex": {"strike": close, "exposure": 100.0}
                                    },
                                    "by_strike": [],
                                    "gamma_zones": [],
                                    "gamma_noise_threshold": 0.0,
                                    "usable_delta_contracts": 1,
                                    "usable_gamma_contracts": 1,
                                },
                            }
                        ],
                    }
                ],
                "subscription_quota": {},
                "model_assumptions": [],
                "warnings": [],
                "note": "fixture",
            }

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "app.api.observation.OptionsCollectionService.build_adapter",
        lambda settings, **kwargs: OptionsAdapter(),
    )

    response = client.post(
        "/api/observation/runs",
        json={
            "group_ids": ["options-observation-group"],
            "trading_date": session_dates[-1].isoformat(),
            "cutoff_time": cutoff.isoformat(),
            "request_intent_id": "options-observation-1",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["options"]["symbols"][0]["symbol"] == "QQQ"
    assert body["options_alignment"]["source_phase"] == "post_close_observation"
    assert body["options_alignment"]["status"] == "flagged"
    assert body["options_alignment"]["flagged_symbols"] == ["QQQ"]

    selected = client.get(
        "/api/observation/options",
        params={"trading_date": session_dates[-1].isoformat(), "symbol": "QQQ"},
    )
    assert selected.status_code == 200, selected.text
    selected_body = selected.json()
    assert selected_body["run_id"] == body["run_id"]
    assert selected_body["available"] is True
    assert selected_body["options"]["requested_symbols"] == ["QQQ"]
    assert selected_body["alignment"]["flagged_symbols"] == ["QQQ"]


def test_observation_run_keeps_option_symbols_out_of_history_quota(client) -> None:
    cutoff = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    session_dates = completed_session_dates(cutoff, "XNYS", count=260)
    with client.app.state.session_factory() as session:
        DailyEvidenceRepository(session).upsert_bars(
            _bars("INTC", session_dates, 20.0),
            source="fixture",
            collected_at=cutoff,
        )
        session.commit()

    created = client.post(
        "/api/observation/groups",
        json={
            "group_id": "option-quota-isolation",
            "display_name": "期权额度隔离测试组",
            "symbols": ["INTC"],
        },
    )
    assert created.status_code == 201, created.text

    settings = client.app.state.settings
    settings.options_target_symbols = "OPTION_ONLY"
    settings.options_watchlist_symbols = ""
    settings.enabled_symbols = ""

    class RecordingHistoryAdmission:
        def __init__(self) -> None:
            self.prepared: list[list[str]] = []

        def bind_quota_reader(self, reader) -> None:
            self.quota_reader = reader

        def prepare_symbols(self, symbols, *, now=None):
            del now
            self.prepared.append(list(symbols))

    class RecordingBarSource:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def quota_snapshot(self) -> dict[str, object]:
            return {"available": True, "used": 0, "remain": 100, "total": 100}

        def instrument_cards(self, symbols: list[str]) -> dict[str, object]:
            self.calls.append(list(symbols))
            return {"history_snapshots": []}

    admission = RecordingHistoryAdmission()
    bar_source = RecordingBarSource()
    with client.app.state.session_factory() as session:
        result = ObservationRunService(session, settings).create_run(
            ObservationRunCreateRequest(
                group_ids=["option-quota-isolation"],
                trading_date=session_dates[-1],
                cutoff_time=cutoff,
                request_intent_id="option-quota-isolation-1",
            ),
            bar_source=bar_source,
            history_admission=admission,
        )

    assert admission.prepared == [["INTC"]]
    assert bar_source.calls == []
    assert result["options_alignment"]["status"] == "unavailable"


def test_observation_options_reports_missing_trading_date_without_falling_back(client) -> None:
    response = client.get(
        "/api/observation/options",
        params={"trading_date": "2026-08-21", "symbol": "QQQ"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"] is None
    assert body["available"] is False
    assert "2026-08-21" in body["message"]


def test_scheduled_observation_requires_source_captures_when_enabled(client) -> None:
    client.app.state.settings.observation_scheduled_require_source_captures = True

    response = client.post(
        "/api/observation/runs",
        json={"trigger_mode": "scheduled", "trading_date": "2026-09-08"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "observation_source_captures_required"


def test_source_capture_failure_never_builds_live_adapters(client, monkeypatch, tmp_path) -> None:
    client.app.state.settings.sidecar_capture_database_path = str(
        tmp_path / "missing-collector.sqlite"
    )
    monkeypatch.setattr(
        "app.api.observation._market_adapter",
        lambda *args, **kwargs: pytest.fail("live market adapter must not be constructed"),
    )
    monkeypatch.setattr(
        "app.api.observation.OptionsCollectionService.build_adapter",
        lambda *args, **kwargs: pytest.fail("live options adapter must not be constructed"),
    )

    response = client.post(
        "/api/observation/runs",
        json={
            "trigger_mode": "scheduled",
            "trading_date": "2026-09-08",
            "group_ids": ["core-watchlist"],
            "group_version_ids": ["missing-version"],
            "collection_plan_id": "0" * 64,
            "source_captures": {
                "market_capture_id": "market-1",
                "market_content_sha256": "0" * 64,
                "options_capture_id": "options-1",
                "options_content_sha256": "0" * 64,
            },
        },
    )

    assert response.status_code == 422
    assert "Sidecar capture database is missing" in response.text


def test_observation_group_updates_require_the_current_version(client) -> None:
    created = client.post(
        "/api/observation/groups",
        json={
            "group_id": "case-sensitive-group",
            "display_name": "初始组",
            "symbols": ["INTC"],
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["group_id"] == "case-sensitive-group"

    updated = client.put(
        "/api/observation/groups/case-sensitive-group",
        json={
            "group_id": "case-sensitive-group",
            "display_name": "新版本",
            "symbols": ["INTC"],
            "base_version_id": body["version_id"],
        },
    )
    assert updated.status_code == 200
    stale = client.put(
        "/api/observation/groups/case-sensitive-group",
        json={
            "group_id": "case-sensitive-group",
            "display_name": "过期版本",
            "symbols": ["AMD"],
            "base_version_id": body["version_id"],
        },
    )
    assert stale.status_code == 409


def test_cross_section_exposes_previous_trading_session_comparison(client) -> None:
    cutoff = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    session_dates = completed_session_dates(cutoff, "XNYS", count=261)
    with client.app.state.session_factory() as session:
        repository = DailyEvidenceRepository(session)
        for symbol, start in (("INTC", 20.0), ("AMD", 80.0), ("QQQ", 300.0)):
            repository.upsert_bars(
                _bars(symbol, session_dates, start),
                source="fixture",
                collected_at=cutoff,
            )
        session.commit()

    created = client.post(
        "/api/observation/groups",
        json={
            "group_id": "comparison-group",
            "display_name": "对比测试组",
            "symbols": ["INTC", "AMD"],
            "benchmark_symbols": ["QQQ"],
        },
    )
    assert created.status_code == 201, created.text

    previous_run = client.post(
        "/api/observation/runs",
        json={
            "group_ids": ["comparison-group"],
            "trading_date": session_dates[-2].isoformat(),
            "cutoff_time": cutoff.isoformat(),
            "request_intent_id": "comparison-previous",
        },
    )
    assert previous_run.status_code == 201, previous_run.text

    current_run = client.post(
        "/api/observation/runs",
        json={
            "group_ids": ["comparison-group"],
            "trading_date": session_dates[-1].isoformat(),
            "cutoff_time": cutoff.isoformat(),
            "request_intent_id": "comparison-current",
        },
    )
    assert current_run.status_code == 201, current_run.text
    run_id = current_run.json()["run_id"]

    indicator = client.get(f"/api/observation/runs/{run_id}/indicators/rsi14")
    assert indicator.status_code == 200, indicator.text
    indicator_body = indicator.json()
    assert indicator_body["comparison"]["status"] == "ok"
    assert indicator_body["comparison"]["current_trading_date"] == session_dates[-1].isoformat()
    assert indicator_body["comparison"]["previous_trading_date"] == session_dates[-2].isoformat()
    indicator_row = next(row for row in indicator_body["rows"] if row["symbol"] == "INTC")
    assert indicator_row["previous_trading_date"] == session_dates[-2].isoformat()
    assert indicator_row["previous_snapshot_id"]
    assert indicator_row["previous_value"] is not None
    assert indicator_row["previous_state"] != "missing"
    assert indicator_body["groups"][0]["previous_distribution"]["count"] == 2

    strategy = client.get(f"/api/observation/runs/{run_id}/strategies/trend_momentum_v1")
    assert strategy.status_code == 200, strategy.text
    strategy_row = next(row for row in strategy.json()["rows"] if row["symbol"] == "INTC")
    assert strategy_row["previous_trading_date"] == session_dates[-2].isoformat()
    assert strategy_row["previous_value"] is not None
    assert "previous_state" in strategy_row
    assert "previous_action" in strategy_row


def test_cross_section_starts_new_strategy_comparison_chain_after_implementation_change(client) -> None:
    cutoff = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    session_dates = completed_session_dates(cutoff, "XNYS", count=261)
    with client.app.state.session_factory() as session:
        repository = DailyEvidenceRepository(session)
        for symbol, start in (("INTC", 20.0), ("AMD", 80.0), ("QQQ", 300.0)):
            repository.upsert_bars(
                _bars(symbol, session_dates, start),
                source="fixture",
                collected_at=cutoff,
            )
        session.commit()

    created = client.post(
        "/api/observation/groups",
        json={
            "group_id": "strategy-version-group",
            "display_name": "策略版本测试组",
            "symbols": ["INTC", "AMD"],
            "benchmark_symbols": ["QQQ"],
        },
    )
    assert created.status_code == 201, created.text

    previous_run = client.post(
        "/api/observation/runs",
        json={
            "group_ids": ["strategy-version-group"],
            "trading_date": session_dates[-2].isoformat(),
            "cutoff_time": cutoff.isoformat(),
            "request_intent_id": "strategy-version-previous",
        },
    )
    assert previous_run.status_code == 201, previous_run.text

    current_run = client.post(
        "/api/observation/runs",
        json={
            "group_ids": ["strategy-version-group"],
            "trading_date": session_dates[-1].isoformat(),
            "cutoff_time": cutoff.isoformat(),
            "request_intent_id": "strategy-version-current",
        },
    )
    assert current_run.status_code == 201, current_run.text

    previous_snapshot_id = previous_run.json()["group_snapshots"][0]["snapshot_id"]
    with client.app.state.session_factory() as session:
        snapshot = session.get(GroupDailySnapshotModel, previous_snapshot_id)
        assert snapshot is not None
        payload = dict(snapshot.payload_json)
        decisions = []
        for item in payload.get("strategy_decisions", []):
            updated = dict(item)
            strategy = dict(updated.get("strategy") or {})
            if strategy.get("name") == "trend_momentum_v1":
                strategy["version"] = "mismatched-version"
                strategy["implementation_sha256"] = "mismatched-hash"
                updated["strategy"] = strategy
            decisions.append(updated)
        assert decisions
        payload["strategy_decisions"] = decisions
        snapshot.payload_json = payload
        session.commit()

    projection = client.get(
        f"/api/observation/runs/{current_run.json()['run_id']}/strategies/trend_momentum_v1"
    )
    assert projection.status_code == 200, projection.text
    projection_body = projection.json()
    assert projection_body["comparison"]["status"] == "unavailable"
    assert projection_body["comparison"]["previous_trading_date"] is None
    assert projection_body["comparison"]["available_group_count"] == 0
    assert all(row["previous_trading_date"] is None for row in projection_body["rows"])

    current_snapshot_id = current_run.json()["group_snapshots"][0]["snapshot_id"]
    with client.app.state.session_factory() as session:
        snapshot = session.get(GroupDailySnapshotModel, current_snapshot_id)
        assert snapshot is not None
        payload = dict(snapshot.payload_json)
        decisions = []
        for item in payload.get("strategy_decisions", []):
            updated = dict(item)
            strategy = dict(updated.get("strategy") or {})
            if strategy.get("name") == "trend_momentum_v1":
                strategy["implementation_sha256"] = "mismatched-current-hash"
                updated["strategy"] = strategy
            decisions.append(updated)
        assert decisions
        payload["strategy_decisions"] = decisions
        snapshot.payload_json = payload
        session.commit()

    current_projection = client.get(
        f"/api/observation/runs/{current_run.json()['run_id']}/strategies/trend_momentum_v1"
    )
    assert current_projection.status_code == 409, current_projection.text
    assert current_projection.json()["error"]["code"] == "cross_section_strategy_version_conflict"


def test_refresh_missing_bars_does_not_backdate_collection_time(client) -> None:
    cutoff = datetime.now(UTC) - timedelta(minutes=1)
    session_dates = completed_session_dates(cutoff, "XNYS", count=260)

    class HistoricalAdapter:
        def instrument_cards(self, symbols: list[str]) -> dict[str, object]:
            return {
                "history_snapshots": [
                    {
                        "symbol": symbol,
                        "history": {"bars": _bars(symbol, session_dates, 20.0)},
                    }
                    for symbol in symbols
                ]
            }

    with client.app.state.session_factory() as session:
        service = DailyMarketEvidenceService(session, client.app.state.settings)
        result = service.refresh_missing_bars(
            ["INTC"],
            through_date=session_dates[-1],
            cutoff_time=cutoff,
            source_adapter=HistoricalAdapter(),
        )
        visible = DailyEvidenceRepository(session).bars(
            ["INTC"], through_date=session_dates[-1], cutoff_time=cutoff
        )

    assert result["status"] == "partial"
    assert result["fetched_symbols"] == []
    assert visible["INTC"] == []


def test_observation_run_rejects_groups_from_another_universe_revision(client) -> None:
    synced = client.post("/api/observation/groups/sync")
    assert synced.status_code == 200, synced.text

    with client.app.state.session_factory() as session:
        universe = InstrumentUniverseRepository(session).active()
        assert universe is not None
        revision = ObservationUniverseRevisionRepository(session).save(
            source_url="https://mismatch.example/api",
            upstream_version_id="mismatch-version",
            upstream_revision=99,
            local_universe_version_id=universe.id,
            content_sha256="f" * 64,
        )

    response = client.post(
        "/api/observation/runs",
        json={
            "trigger_mode": "scheduled",
            "universe_revision_id": revision.id,
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "observation_run_invalid"


def test_observation_groups_sync_from_active_universe(client) -> None:
    response = client.post("/api/observation/groups/sync")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "local"
    assert body["symbol_count"] == 2
    assert body["group_count"] >= 2

    groups = client.get("/api/observation/groups").json()
    core = next(item for item in groups if item["group_id"] == "core-watchlist")
    assert core["display_name"] == "指标推荐"
    assert "indicator-recommendation" in core["tags"]
    assert core["symbols"] == ["SMH", "INTC"]
    assert core["benchmark_symbols"] == ["QQQ"]
    assert any(item["display_name"] == "半导体" and item["symbols"] == ["SMH"] for item in groups)
    assert any(item["display_name"] == "个股观察" and item["symbols"] == ["INTC"] for item in groups)


def test_observation_theme_groups_honor_daily_members_outside_indicator_watchlist(client) -> None:
    deployed = client.get("/api/settings/universe").json()
    qqq = next(item for item in deployed["items"] if item["symbol"] == "QQQ")
    qqq["theme"] = "半导体"
    qqq["themes"] = ["半导体"]

    updated = client.put(
        "/api/settings/universe",
        json={"base_version_id": deployed["version_id"], "items": deployed["items"]},
    )
    assert updated.status_code == 200, updated.text

    synced = client.post("/api/observation/groups/sync")
    assert synced.status_code == 200, synced.text
    groups = synced.json()["groups"]
    core = next(item for item in groups if item["group_id"] == "core-watchlist")
    semiconductor = next(item for item in groups if item["display_name"] == "半导体")

    assert "QQQ" not in core["symbols"]
    assert set(semiconductor["symbols"]) == {"SMH", "QQQ"}


def test_persisted_self_selected_group_is_hidden_from_active_catalog(client) -> None:
    created = client.post(
        "/api/observation/groups",
        json={
            "group_id": "persisted-self-selected-group",
            "display_name": "核心观察组",
            "description": "历史手工自选组",
            "symbols": ["INTC"],
            "benchmark_symbols": ["QQQ"],
            "tags": ["watchlist", "user-qualified"],
        },
    )
    assert created.status_code == 201, created.text

    visible = client.get("/api/observation/groups")
    assert visible.status_code == 200, visible.text
    assert all(item["group_id"] != "persisted-self-selected-group" for item in visible.json())

    historical = client.get("/api/observation/groups/persisted-self-selected-group")
    assert historical.status_code == 200, historical.text
    assert historical.json()["group"]["display_name"] == "自选组"


def test_observation_groups_sync_from_deployed_universe(client, app, monkeypatch) -> None:
    deployed = client.get("/api/settings/universe").json()
    wolf = next(item.copy() for item in deployed["items"] if item["symbol"] == "INTC")
    wolf.update({"symbol": "WOLF", "display_name": "WOLF", "themes": ["半导体"], "theme": "半导体"})
    deployed["items"].append(wolf)
    spy = next(item for item in deployed["items"] if item["symbol"] == "SPY")
    spy["roles"]["equity_watchlist"] = True
    deployed["content_sha256"] = InstrumentUniverseRepository.content_digest(
        [InstrumentConfig.model_validate(item) for item in deployed["items"]]
    )
    deployed.update({"version_id": "deployed-v5", "revision": 5, "source": "runtime"})

    class DeployedResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return deployed

    app.state.settings.observation_universe_source_url = "https://deployed.example"
    monkeypatch.setattr(
        "app.services.observation.httpx.get",
        lambda url, timeout: DeployedResponse(),
    )

    response = client.post("/api/observation/groups/sync")

    assert response.status_code == 200, response.text
    assert response.json()["source"] == "upstream"
    assert response.json()["symbol_count"] == 4
    first_sync = response.json()
    assert first_sync["universe_freshness"] == "fresh"
    assert first_sync["source_url"] == "https://deployed.example/api"
    assert first_sync["universe_revision_id"]
    assert "WOLF" in client.get("/api/settings/universe").json()["derived"]["instrument_symbols"]
    core = next(item for item in response.json()["groups"] if item["group_id"] == "core-watchlist")
    assert core["display_name"] == "指标推荐"
    assert "SPY" in core["symbols"]
    semiconductor = next(
        item for item in client.get("/api/observation/groups").json()
        if item["display_name"] == "半导体"
    )
    assert semiconductor["symbols"] == ["SMH", "WOLF"]
    assert semiconductor["source"] == "universe"
    assert semiconductor["universe_revision_id"] == first_sync["universe_revision_id"]

    repeated = client.post("/api/observation/groups/sync")
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["universe_revision_id"] == first_sync["universe_revision_id"]
    repeated_semiconductor = next(
        item for item in repeated.json()["groups"] if item["display_name"] == "半导体"
    )
    assert repeated_semiconductor["version_id"] == semiconductor["version_id"]


def test_observation_universe_sync_fails_closed_and_supports_explicit_stale_mode(
    client, app, monkeypatch
) -> None:
    deployed = client.get("/api/settings/universe").json()

    class DeployedResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return deployed

    app.state.settings.observation_universe_source_url = "https://deployed.example?token=redacted"
    monkeypatch.setattr(
        "app.services.observation.httpx.get",
        lambda url, timeout: DeployedResponse(),
    )
    first = client.post("/api/observation/groups/sync")
    assert first.status_code == 200, first.text
    revision_id = first.json()["universe_revision_id"]

    def unavailable(url, timeout):
        raise httpx.ConnectError("secret-token-must-not-leak", request=httpx.Request("GET", url))

    monkeypatch.setattr("app.services.observation.httpx.get", unavailable)
    failed = client.post("/api/observation/groups/sync")
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "observation_universe_sync_failed"
    assert "secret-token" not in failed.text
    assert "token=redacted" not in failed.text

    app.state.settings.observation_allow_stale_universe = True
    stale = client.post("/api/observation/groups/sync")
    assert stale.status_code == 200, stale.text
    assert stale.json()["universe_freshness"] == "stale"
    assert stale.json()["universe_revision_id"] == revision_id
    assert stale.json()["warnings"]


def test_observation_run_reuses_one_frozen_decision_for_overlapping_groups(client) -> None:
    cutoff = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    session_dates = completed_session_dates(cutoff, "XNYS", count=260)
    with client.app.state.session_factory() as session:
        repository = DailyEvidenceRepository(session)
        for symbol, start in (("INTC", 20.0), ("AMD", 80.0), ("QQQ", 300.0)):
            repository.upsert_bars(_bars(symbol, session_dates, start), source="fixture", collected_at=cutoff)
        session.commit()

    for group_id, symbols in (("chips", ["INTC", "AMD"]), ("turnarounds", ["INTC"])):
        response = client.post(
            "/api/observation/groups",
            json={
                "group_id": group_id,
                "display_name": group_id,
                "symbols": symbols,
                "benchmark_symbols": ["QQQ"],
            },
        )
        assert response.status_code == 201, response.text

    run = client.post(
        "/api/observation/runs",
        json={
            "group_ids": ["chips", "turnarounds"],
            "trading_date": session_dates[-1].isoformat(),
            "cutoff_time": cutoff.isoformat(),
            "request_intent_id": "overlap-1",
        },
    )
    assert run.status_code == 201, run.text
    body = run.json()
    assert body["status"] == "succeeded"
    assert len({item["dataset_id"] for item in body["group_snapshots"]}) == 1

    projection = client.get(
        f"/api/observation/runs/{body['run_id']}/strategies/trend_momentum_v1"
    ).json()
    intc_rows = [item for item in projection["rows"] if item["symbol"] == "INTC"]
    assert len(intc_rows) == 2
    assert len({item["decision_id"] for item in intc_rows}) == 1


def test_relative_strength_projection_rejects_mixed_benchmarks(client) -> None:
    cutoff = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    session_dates = completed_session_dates(cutoff, "XNYS", count=260)
    with client.app.state.session_factory() as session:
        repository = DailyEvidenceRepository(session)
        for symbol, start in (("INTC", 20.0), ("AMD", 80.0), ("QQQ", 300.0), ("SPY", 500.0)):
            repository.upsert_bars(_bars(symbol, session_dates, start), source="fixture", collected_at=cutoff)
        session.commit()

    for group_id, symbol, benchmark in (
        ("qqq-relative", "INTC", "QQQ"),
        ("spy-relative", "AMD", "SPY"),
    ):
        created = client.post(
            "/api/observation/groups",
            json={
                "group_id": group_id,
                "display_name": group_id,
                "symbols": [symbol],
                "benchmark_symbols": [benchmark],
            },
        )
        assert created.status_code == 201, created.text

    run = client.post(
        "/api/observation/runs",
        json={
            "group_ids": ["qqq-relative", "spy-relative"],
            "trading_date": session_dates[-1].isoformat(),
            "cutoff_time": cutoff.isoformat(),
            "request_intent_id": "mixed-benchmark",
        },
    ).json()

    projection = client.get(
        f"/api/observation/runs/{run['run_id']}/indicators/relative_strength_20d"
    )
    assert projection.status_code == 409
    assert projection.json()["error"]["code"] == "cross_section_benchmark_conflict"


def _report_rank_row(
    group_id: str,
    relative_20d: float | None,
    expected_tracking_move_20d: float | None,
    *,
    rank_eligible: bool = True,
) -> dict:
    return {
        "group_id": group_id,
        "group_name": group_id,
        "snapshot_id": f"snapshot-{group_id}",
        "decision_ids": [f"decision-{group_id}"],
        "rank_eligible": rank_eligible,
        "relative_20d": relative_20d,
        "expected_tracking_move_20d": expected_tracking_move_20d,
    }


def test_rank_observation_groups_requires_five_eligible_groups_and_is_order_invariant() -> None:
    eligible = [
        _report_rank_row(f"g{index}", float(index), 1.0)
        for index in range(1, 6)
    ]
    rows = eligible + [_report_rank_row("ineligible", 100.0, 1.0, rank_eligible=False)]

    ranked = rank_observation_groups(rows)
    reversed_ranked = rank_observation_groups(list(reversed(rows)))

    assert [item["group_id"] for item in ranked] == ["g1", "g2", "g3", "g4", "g5"]
    assert "ineligible" not in {item["group_id"] for item in ranked}
    normalized = {item["group_id"]: item["rank_normalized"] for item in ranked}
    assert normalized == {"g1": -1.0, "g2": -0.5, "g3": 0.0, "g4": 0.5, "g5": 1.0}
    assert normalized == {
        item["group_id"]: item["rank_normalized"] for item in reversed_ranked
    }
    assert rank_observation_groups(eligible[:4]) == []


def test_rank_observation_groups_uses_bounded_relative_efficiency_and_deduplicates() -> None:
    rows = [
        _report_rank_row("fast", 2.0, 1.0),
        _report_rank_row("floor", 1.0, 0.1),
        _report_rank_row("missing-relative", None, 1.0),
        _report_rank_row("missing-move", 2.0, None),
        _report_rank_row("fifth", 0.5, 1.0),
    ]
    duplicated = rows + [dict(rows[0])]

    baseline = rank_observation_groups(rows)
    with_duplicate = rank_observation_groups(duplicated)
    baseline_by_group = {item["group_id"]: item for item in baseline}
    duplicate_by_group = {item["group_id"]: item for item in with_duplicate}

    assert len(with_duplicate) == len(baseline) == 5
    assert duplicate_by_group.keys() == baseline_by_group.keys()
    assert duplicate_by_group["fast"] == baseline_by_group["fast"]
    assert duplicate_by_group["fast"]["relative_efficiency"] == pytest.approx(2.0)
    assert duplicate_by_group["floor"]["relative_efficiency"] == pytest.approx(2.0)
    assert duplicate_by_group["missing-relative"]["relative_efficiency"] is None
    assert duplicate_by_group["missing-move"]["relative_efficiency"] is None


def _v2_report_snapshot(group_id: str, phase: str, category: str) -> dict:
    return {
        "group_id": group_id,
        "snapshot_id": f"snapshot-{group_id}",
        "dataset_id": "dataset-report-v2",
        "status": "succeeded",
        "payload": {
            "group": {"group_id": group_id, "display_name": group_id.title()},
            "features": {
                "valid_symbol_count": 5,
                "returns_percent": {"20d": {"median": 2.0}},
                "relative_strength": {"median_excess_20d": 1.0},
                "breadth": {"above_ma20": 0.8},
            },
            "changes": {
                "median_20d_delta_percent": 0.2,
                "relative_20d_delta_percent": 0.1,
                "breadth_ma20_delta": 0.1,
            },
            "quality": {"status": "ok", "warnings": []},
            "group_decision": {
                "state": "broad_strength" if "strength" in phase else "broad_weakness",
                "phase": phase,
                "category": category,
                "rank_eligible": True,
                "stance": "constructive",
                "action": "prioritize",
            },
        },
    }


def test_observation_report_v2_buckets_themes_and_keeps_legacy_fields() -> None:
    report = build_observation_report(
        run_id="run-report-v2",
        trading_date="2026-08-28",
        snapshots=[
            _v2_report_snapshot("leaders", "broad_strength_stable", "leader"),
            _v2_report_snapshot("deteriorating", "broad_strength_deteriorating", "deteriorating"),
            _v2_report_snapshot("reversal", "broad_weakness_improving", "reversal_watch"),
        ],
    )

    assert report["schema_version"] == "urus.observation_report.v2"
    assert {item["group_id"] for item in report["leading_themes"]} == {"leaders"}
    assert {item["group_id"] for item in report["deteriorating_themes"]} == {"deteriorating"}
    assert {item["group_id"] for item in report["reversal_themes"]} == {"reversal"}
    assert "group_rankings" in report
    assert "improving_groups" in report
    assert "deteriorating_groups" in report


def _discovery_run(report: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id="run-discovery-current",
        status="succeeded",
        trading_date=datetime(2026, 8, 21).date(),
        cutoff_time=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
        group_ids=["group-a", "group-b"],
        payload_json={"report": report or {}},
    )


def _discovery_row(
    group_id: str,
    group_name: str,
    indicator_id: str,
    *,
    benchmark: str = "QQQ",
) -> dict:
    current_state, previous_state = (
        ("overbought", "balanced") if indicator_id == "rsi14" else ("leading", "inline")
    )
    current_value, previous_value = (75.0, 50.0) if indicator_id == "rsi14" else (1.0, 0.0)
    return {
        "group_id": group_id,
        "group_name": group_name,
        "symbol": "AAA",
        "snapshot_id": f"snapshot-{group_id}-current",
        "dataset_id": "dataset-current",
        "previous_snapshot_id": f"snapshot-{group_id}-previous",
        "previous_dataset_id": "dataset-previous",
        "previous_trading_date": "2026-08-20",
        "transition_date": "2026-08-21",
        "value": current_value,
        "previous_value": previous_value,
        "display_value": str(current_value),
        "previous_display_value": str(previous_value),
        "state": current_state,
        "previous_state": previous_state,
        "state_label": current_state,
        "previous_state_label": previous_state,
        "change": current_value - previous_value,
        "benchmark_symbols": [benchmark],
        "transition": {
            "type": "state_changed",
            "from": previous_state,
            "to": current_state,
        },
    }


class _DiscoveryCrossSectionFake:
    def __init__(self, rows_by_indicator: dict[str, list[dict]]) -> None:
        self.rows_by_indicator = rows_by_indicator
        self.reverse = False

    @staticmethod
    def indicator_catalog() -> list[dict[str, str]]:
        return [
            {"id": "rsi14", "name": "RSI 14", "feature_version": "technical_v5"},
            {
                "id": "relative_strength_20d",
                "name": "Relative Strength 20D",
                "feature_version": "technical_v5",
            },
        ]

    def indicator_projection(self, run_id: str, indicator_id: str) -> dict:
        rows = list(self.rows_by_indicator.get(indicator_id, []))
        if self.reverse:
            rows.reverse()
        return {
            "comparison": {
                "status": "ok",
                "previous_trading_date": "2026-08-20",
            },
            "rows": rows,
            "quality": {"status": "ok", "warnings": []},
        }


class _DiscoverySessionFake:
    def __init__(self, group_versions: dict[str, SimpleNamespace] | None = None) -> None:
        self.group_versions = group_versions or {}

    def get(self, model, object_id: str):
        assert model is ObservationGroupVersionModel
        return self.group_versions.get(str(object_id))


class _DiscoveryRepositoryFake:
    def __init__(
        self,
        run: SimpleNamespace,
        *,
        snapshots: dict[str, SimpleNamespace] | None = None,
        previous: dict[str, SimpleNamespace] | None = None,
        group_versions: dict[str, SimpleNamespace] | None = None,
    ) -> None:
        self.run = run
        self.snapshots = snapshots or {}
        self.previous = previous or {}
        self.session = _DiscoverySessionFake(group_versions)

    def get_run(self, run_id: str) -> SimpleNamespace | None:
        return self.run if run_id == self.run.id else None

    def get_snapshot(self, snapshot_id: str) -> SimpleNamespace | None:
        return self.snapshots.get(str(snapshot_id))

    def previous_snapshot(self, *, group_id: str, group_version_id: str, trading_date):
        return self.previous.get(str(group_id))


def _discovery_group_case(
    group_id: str,
    *,
    tags: list[str] | None = None,
    symbols: list[str] | None = None,
    valid_count: int = 4,
    coverage: float = 1.0,
    previous_available: bool = True,
    current_state: str = "broad_strength",
    previous_state: str = "mixed",
    impulse: str = "improving",
    changed: bool = True,
) -> tuple[SimpleNamespace, SimpleNamespace | None, SimpleNamespace]:
    version_id = f"version-{group_id}"
    requested_count = len(symbols or ["AAA", "BBB", "CCC", "DDD"])
    current_date = datetime(2026, 8, 21, tzinfo=UTC).date()
    previous_date = datetime(2026, 8, 20, tzinfo=UTC).date()
    current_payload = {
        "features": {
            "breadth": {"above_ma20": 0.75},
            "relative_strength": {"median_excess_20d": 1.25},
            "leader_concentration": 0.4,
        },
        "group_decision": {
            "state": current_state,
            "impulse": impulse,
            "participation": 0.75,
            "rank_eligible": True,
            "sample_support": {
                "valid_symbol_count": valid_count,
                "requested_symbol_count": requested_count,
                "coverage": coverage,
            },
        },
        "changes": {
            "previous_trading_date": previous_date.isoformat(),
            "group_state": {
                "from": previous_state,
                "to": current_state,
                "changed": changed,
            },
            "breadth_ma20_delta": 0.125,
            "relative_20d_delta_percent": 0.5,
            "leaders_added": ["AAA"],
            "leaders_removed": ["ZZZ"],
        },
    }
    previous_payload = {
        "group_decision": {"state": previous_state},
        "trading_date": previous_date.isoformat(),
    }
    current = SimpleNamespace(
        id=f"snapshot-{group_id}-current",
        group_id=group_id,
        group_version_id=version_id,
        dataset_id=f"dataset-{group_id}-current",
        trading_date=current_date,
        payload_json=current_payload,
    )
    previous = (
        SimpleNamespace(
            id=f"snapshot-{group_id}-previous",
            group_id=group_id,
            group_version_id=version_id,
            dataset_id=f"dataset-{group_id}-previous",
            trading_date=previous_date,
            payload_json=previous_payload,
        )
        if previous_available
        else None
    )
    group_version = SimpleNamespace(
        id=version_id,
        group_id=group_id,
        display_name=f"{group_id} theme",
        tags=tags or ["theme"],
        symbols=symbols or ["AAA", "BBB", "CCC", "DDD"],
    )
    return current, previous, group_version


def _discovery_group_service(cases: list[tuple[SimpleNamespace, SimpleNamespace | None, SimpleNamespace]]):
    run = _discovery_run()
    run.payload_json = {
        "report": {},
        "group_snapshots": [
            {
                "group_id": current.group_id,
                "group_version_id": current.group_version_id,
                "snapshot_id": current.id,
                "dataset_id": current.dataset_id,
                "status": "succeeded",
            }
            for current, _, _ in cases
        ],
    }
    snapshots = {current.id: current for current, previous, _ in cases}
    previous = {
        current.group_id: prior
        for current, prior, _ in cases
        if prior is not None
    }
    group_versions = {current.group_version_id: version for current, _, version in cases}
    service = DiscoveryProjectionService(None)
    service.repository = _DiscoveryRepositoryFake(
        run,
        snapshots=snapshots,
        previous=previous,
        group_versions=group_versions,
    )
    service.cross_section = _DiscoveryCrossSectionFake({})
    return service, run


def test_discovery_group_phase_events_filter_to_qualified_theme_groups() -> None:
    cases = [
        _discovery_group_case("qualified"),
        _discovery_group_case("indicator", tags=["theme", "indicator-recommendation"]),
        _discovery_group_case("single", symbols=["AAA"]),
        _discovery_group_case("low-sample", valid_count=3),
        _discovery_group_case("no-previous", previous_available=False),
        _discovery_group_case("universe-core-watchlist", tags=["theme"]),
    ]
    service, run = _discovery_group_service(cases)

    result = service.build(run.id)

    assert [item["group"]["id"] for item in result["events"]] == ["qualified"]
    event = result["events"][0]
    assert event["kind"] == "group_phase_change"
    assert event["stage"] == {"id": "broad_strength_start", "label": "参与扩散至广泛强势"}
    assert event["metrics"] == {
        "participation": 0.75,
        "breadth_ma20": 0.75,
        "breadth_ma20_delta": 0.125,
        "relative_20d": 1.25,
        "relative_20d_delta": 0.5,
        "leader_concentration": 0.4,
        "valid_symbol_count": 4,
        "requested_symbol_count": 4,
        "coverage": 1.0,
        "leaders_added": ["AAA"],
        "leaders_removed": ["ZZZ"],
    }
    assert event["duration"] == {"status": "unavailable", "label": "持续时长未知"}
    assert event["current"] == {
        "run_id": run.id,
        "trading_date": "2026-08-21",
        "snapshot_ids": ["snapshot-qualified-current"],
        "dataset_ids": ["dataset-qualified-current"],
    }
    assert event["compare"] == {
        "run_id": None,
        "trading_date": "2026-08-20",
        "snapshot_ids": ["snapshot-qualified-previous"],
        "dataset_ids": ["dataset-qualified-previous"],
    }
    assert event["route"] == {
        "name": "group-observation",
        "params": {"groupId": "qualified"},
        "query": {"run": run.id, "discoveryEvent": event["event_id"]},
    }
    first_digest = result["content_sha256"]
    replay = service.build(run.id)
    assert replay["content_sha256"] == first_digest
    assert replay["events"][0]["event_id"] == event["event_id"]


@pytest.mark.parametrize(
    ("current_state", "previous_state", "impulse", "expected"),
    [
        ("narrow_leadership", "mixed", "stable", "少数龙头"),
        ("narrow_leadership", "narrow_leadership", "deteriorating", "少数龙头"),
        ("broad_strength", "broad_strength", "deteriorating", "开始恶化"),
        ("broad_strength", "mixed", "stable", "参与扩散至广泛强势"),
        ("broad_strength", "broad_strength", "improving", "广泛强势继续扩散"),
        ("mixed", "mixed", "improving", "边际改善"),
    ],
)
def test_discovery_group_phase_stage_mapping(
    current_state: str,
    previous_state: str,
    impulse: str,
    expected: str,
) -> None:
    service, run = _discovery_group_service(
        [
            _discovery_group_case(
                "stage-case",
                current_state=current_state,
                previous_state=previous_state,
                impulse=impulse,
            )
        ]
    )

    result = service.build(run.id)

    assert len(result["events"]) == 1
    assert result["events"][0]["stage"]["label"] == expected


def test_discovery_group_phase_events_sort_before_indicator_events() -> None:
    cases = [
        _discovery_group_case(
            "group-late",
            current_state="narrow_leadership",
            previous_state="mixed",
            impulse="stable",
        ),
        _discovery_group_case(
            "group-early",
            current_state="broad_strength",
            previous_state="broad_strength",
            impulse="deteriorating",
        ),
        _discovery_group_case(
            "group-mid",
            current_state="broad_strength",
            previous_state="mixed",
            impulse="stable",
        ),
    ]
    service, run = _discovery_group_service(cases)
    service.cross_section = _DiscoveryCrossSectionFake(
        {"rsi14": [_discovery_row("indicator-group", "Indicator", "rsi14")]}
    )

    result = service.build(run.id)

    assert [item["stage"]["label"] for item in result["events"][:3]] == [
        "开始恶化",
        "参与扩散至广泛强势",
        "少数龙头",
    ]
    assert result["events"][3]["kind"] == "indicator_transition"


def test_discovery_indicator_transitions_deduplicate_groups_and_benchmarks() -> None:
    run = _discovery_run()
    fake = _DiscoveryCrossSectionFake(
        {
            "rsi14": [
                _discovery_row("group-a", "组 A", "rsi14"),
                _discovery_row("group-b", "组 B", "rsi14"),
            ],
            "relative_strength_20d": [
                _discovery_row("group-a", "组 A", "relative_strength_20d", benchmark="QQQ"),
                _discovery_row("group-b", "组 B", "relative_strength_20d", benchmark="SPY"),
            ],
        }
    )
    service = DiscoveryProjectionService(None)
    service.repository = _DiscoveryRepositoryFake(run)
    service.cross_section = fake

    result = service.build(run.id)
    assert len(result["events"]) == 3
    rsi_events = [item for item in result["events"] if item["lens"]["id"] == "rsi14"]
    relative_events = [
        item for item in result["events"] if item["lens"]["id"] == "relative_strength_20d"
    ]
    assert len(rsi_events) == 1
    assert {item["id"] for item in rsi_events[0]["groups"]} == {"group-a", "group-b"}
    assert len(relative_events) == 2
    assert {item["id"] for item in relative_events[0]["groups"]} != {
        item["id"] for item in relative_events[1]["groups"]
    }
    route = rsi_events[0]["route"]
    assert route["name"] == "instrument-decision"
    assert route["params"] == {"symbol": "AAA"}
    assert route["query"] == {
        "dataset": "dataset-current",
        "run": run.id,
        "discoveryEvent": rsi_events[0]["event_id"],
        "lens": "rsi14",
    }
    assert set(result["events"][0]) == {
        "event_id",
        "kind",
        "symbol",
        "lens",
        "groups",
        "current",
        "compare",
        "before",
        "after",
        "transition",
        "priority_reason",
        "route",
    }

    first_digest = result["content_sha256"]
    first_ids = [item["event_id"] for item in result["events"]]
    fake.reverse = True
    replay = service.build(run.id)
    assert replay["content_sha256"] == first_digest
    assert [item["event_id"] for item in replay["events"]] == first_ids


def test_discovery_without_previous_evidence_is_explicitly_empty() -> None:
    run = _discovery_run()

    class NoPrevious(_DiscoveryCrossSectionFake):
        def indicator_projection(self, run_id: str, indicator_id: str) -> dict:
            return {
                "comparison": {"status": "unavailable", "previous_trading_date": None},
                "rows": [],
                "quality": {"status": "ok", "warnings": []},
            }

    service = DiscoveryProjectionService(None)
    service.repository = _DiscoveryRepositoryFake(run)
    service.cross_section = NoPrevious({})

    result = service.build(run.id)
    assert result["events"] == []
    assert result["comparison"]["status"] == "unavailable"
    assert result["quality"]["capabilities"]["strategy_transition"] == "deferred"
    assert any("没有可比" in item for item in result["quality"]["warnings"])


def test_discovery_group_event_supplies_comparison_when_indicators_are_unavailable() -> None:
    service, run = _discovery_group_service([_discovery_group_case("group-only")])

    class NoIndicatorHistory(_DiscoveryCrossSectionFake):
        def indicator_projection(self, run_id: str, indicator_id: str) -> dict:
            return {
                "comparison": {"status": "unavailable", "previous_trading_date": None},
                "rows": [],
                "quality": {"status": "ok", "warnings": []},
            }

    service.cross_section = NoIndicatorHistory({})

    result = service.build(run.id)

    assert result["events"][0]["kind"] == "group_phase_change"
    assert result["comparison"] == {
        "mode": "previous_trading_session",
        "status": "partial",
        "current_trading_date": "2026-08-21",
        "previous_trading_date": "2026-08-20",
        "previous_trading_dates": ["2026-08-20"],
        "compare_run_id": None,
    }
    assert result["quality"]["status"] == "partial"
    assert not any("未生成跨日变化" in item for item in result["quality"]["warnings"])


def test_discovery_endpoint_is_read_only_and_marks_only_strategy_deferred(client) -> None:
    cutoff = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    session_dates = completed_session_dates(cutoff, "XNYS", count=260)
    with client.app.state.session_factory() as session:
        repository = DailyEvidenceRepository(session)
        for symbol, start in (("INTC", 20.0), ("AMD", 80.0), ("QQQ", 300.0)):
            repository.upsert_bars(
                _bars(symbol, session_dates, start),
                source="fixture",
                collected_at=cutoff,
            )
        session.commit()

    group = client.post(
        "/api/observation/groups",
        json={
            "group_id": "discovery-read-only",
            "display_name": "Discovery read only",
            "symbols": ["INTC", "AMD"],
            "benchmark_symbols": ["QQQ"],
        },
    )
    assert group.status_code == 201, group.text
    created = client.post(
        "/api/observation/runs",
        json={
            "group_ids": ["discovery-read-only"],
            "trading_date": session_dates[-1].isoformat(),
            "cutoff_time": cutoff.isoformat(),
            "request_intent_id": "discovery-read-only-run",
        },
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["run_id"]

    with client.app.state.session_factory() as session:
        before = (
            session.scalar(select(func.count()).select_from(ObservationRunModel)),
            session.scalar(select(func.count()).select_from(GroupDailySnapshotModel)),
        )
    response = client.get(f"/api/observation/runs/{run_id}/discovery")
    with client.app.state.session_factory() as session:
        after = (
            session.scalar(select(func.count()).select_from(ObservationRunModel)),
            session.scalar(select(func.count()).select_from(GroupDailySnapshotModel)),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == "urus.discovery_projection.v1"
    assert body["quality"]["capabilities"] == {
        "indicator_transition": "available",
        "strategy_transition": "deferred",
        "group_phase_change": "available",
    }
    assert before == after
